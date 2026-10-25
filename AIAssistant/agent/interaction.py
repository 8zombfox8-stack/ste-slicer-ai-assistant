# Interactive steps of the dialogue: clarifying questions with answer options and stage checkpoints.
# The agent thread blocks until the user answers in the web chat (or immediately in automatic mode).

import json
import re
import threading

WAIT_TIMEOUT_S = 1800
CJK_RE = re.compile(u"[぀-ヿ㐀-䶿一-鿿가-힯＀-￯]")

ASK_USER_TOOL = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": (
            "Задать пользователю 1–8 уточняющих вопросов с вариантами ответа (кнопками) на одном экране; можно "
            "вызывать и позже по ходу работы, если появился вопрос, меняющий решение. Под каждым "
            "вопросом есть поле «Свой вариант». Пользователь может оставить вопрос пустым — тогда решай сам, "
            "без отдельного сообщения о допущениях. Не добавляй варианты «Не знаю», «Пусть решит система», «Другое». "
            "Вариант, который ты советуешь, помечай « (рекомендую)»."),
        "parameters": {
            "type": "object",
            "properties": {
                "intro": {"type": "string", "description": "Одна фраза простым языком: зачем эти вопросы"},
                "questions": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "question": {"type": "string"},
                        "why": {"type": "string", "description": "Простыми словами: на что повлияет ответ"},
                        "options": {"type": "array", "items": {"type": "string"}},
                        "multiple": {"type": "boolean", "description": "Можно выбрать несколько вариантов"},
                    },
                    "required": ["id", "question", "options"]}},
            },
            "required": ["questions"],
        },
    },
}

STAGE_TOOL = {
    "type": "function",
    "function": {
        "name": "stage_report",
        "description": (
            "Завершить этап подготовки детали: показать пользователю итог этапа простым языком, принятые решения "
            "с причинами и результаты проверок, и спросить, продолжать ли. Вызывай после каждого этапа. "
            "Если пользователь просит изменить — внеси изменения, проверь и снова вызови stage_report для этого этапа. "
            "Карточку читает новичок: коротко, без названий настроек и чисел, которые ему ничего не скажут."),
        "parameters": {
            "type": "object",
            "properties": {
                "stage": {"type": "integer", "description": "Номер этапа 1–7"},
                "title": {"type": "string", "description": "2–4 слова"},
                "plain_summary": {"type": "string",
                                  "description": "Одно предложение до 20 слов, обычными словами"},
                "decisions": {"type": "array", "description": "Не больше 3 главных решений", "items": {
                    "type": "object",
                    "properties": {"what": {"type": "string", "description": "1–2 слова: «Материал», «Опоры»"},
                                   "value": {"type": "string", "description": "До 5 слов, без ключей настроек"},
                                   "why": {"type": "string",
                                           "description": "Обязательно, до 10 слов, обычными словами"}},
                    "required": ["what", "value", "why"]}},
                "checks": {"type": "array", "description": "Только warning и problem; удачные проверки не перечисляй",
                           "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string", "description": "1–3 слова"},
                                   "status": {"type": "string", "enum": ["ok", "warning", "problem"]},
                                   "detail": {"type": "string", "description": "До 12 слов: что не так и что делать"}},
                    "required": ["name", "status"]}},
            },
            "required": ["stage", "title", "plain_summary"],
        },
    },
}

def _as_list(value):
    """Arrays sometimes arrive as JSON text; accept both, anything else becomes an empty list."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except ValueError:
            return []
    return []


AGENT_TOOLS = [ASK_USER_TOOL, STAGE_TOOL]
AGENT_TOOL_NAMES = set(t["function"]["name"] for t in AGENT_TOOLS)


class Interaction:
    """Pending user interactions. One at a time: the agent waits for each answer."""

    def __init__(self, emit, settings):
        self._emit = emit
        self._settings = settings
        self._lock = threading.Lock()
        self._counter = 0
        self._pending = None  # (request_id, Event, holder)
        self._stopped = False

    def interactive(self):
        return self._settings.get("interactive") != "0"

    def confirmStages(self):
        return self.interactive() and self._settings.get("confirm_stages") == "1"

    # ---- called by the agent thread ----

    def waiting(self):
        return self._pending is not None

    def call(self, name, args):
        if CJK_RE.search(json.dumps(args, ensure_ascii=False)):
            # Some models slip into Chinese: do not show such a card, let the model rewrite it.
            return {"error": "В тексте есть китайские или японские символы. Перепиши всё только на русском языке "
                             "и вызови инструмент снова."}
        if name == "ask_user":
            return self._ask(args)
        if name == "stage_report":
            return self._stage(args)
        raise ValueError("Неизвестный инструмент: %s" % name)

    def _ask(self, args):
        questions = _as_list(args.get("questions"))
        questions = [q for q in questions if isinstance(q, dict) and q.get("question")][:8]
        if not questions:
            # Models sometimes send the array as broken text: don't show an empty card, ask for a retry.
            return {"error": "Параметр questions должен быть массивом объектов вида "
                             "{\"id\": \"...\", \"question\": \"...\", \"options\": [\"...\", \"...\"]}. "
                             "Вызови ask_user снова с корректным массивом."}
        for i, q in enumerate(questions):
            q["id"] = str(q.get("id") or "q%d" % (i + 1))
            q["options"] = [str(o) for o in _as_list(q.get("options")) if str(o).strip()][:8]
        if not self.interactive():
            self._emit("questions", request_id=None, intro=args.get("intro"), questions=questions, auto=True)
            return {"auto_mode": True,
                    "instruction": "Автоматический режим: пользователь не отвечает. Прими решения сам и явно "
                                   "перечисли допущения в итоговом ответе."}
        response = self._wait("questions", intro=args.get("intro"), questions=questions)
        if response is None:
            return {"timeout": True, "instruction": "Ответа нет: решай сам и явно назови допущения."}
        answers = []
        given = response.get("answers") or {}
        for q in questions:
            a = given.get(q.get("id")) or {}
            selected = [s for s in (a.get("selected") or []) if s]
            custom = (a.get("custom") or "").strip()
            answers.append({"id": q.get("id"), "question": q.get("question"),
                            "selected": selected, "custom": custom or None,
                            "status": "answered" if (selected or custom) else "skipped"})
        return {"answers": answers,
                "instruction": "Для пропущенных вопросов (status=skipped) реши сам, без отдельного сообщения о "
                               "допущениях. Свой вариант (custom) пользователя важнее кнопок."}

    def _stage(self, args):
        args = dict(args)
        args["decisions"] = [d for d in _as_list(args.get("decisions")) if isinstance(d, dict)]
        args["checks"] = [c for c in _as_list(args.get("checks")) if isinstance(c, dict)]
        missing = [d.get("what") for d in args["decisions"] if not (str(d.get("why") or "")).strip()]
        if missing:
            # The card has a "Почему" column; an empty one looks broken, so ask the model to fill it.
            return {"error": "Заполни поле why у решений: %s. Для фактов напиши, что это значит для печати "
                             "(например «отверстие больше оправки → нужен режим Spiral Full»). Вызови stage_report "
                             "снова." % ", ".join(str(m) for m in missing)}
        payload = {"stage": args.get("stage"), "title": args.get("title"),
                   "plain_summary": args.get("plain_summary"),
                   "decisions": args.get("decisions") or [], "checks": args.get("checks") or []}
        if not self.confirmStages():
            # The report is kept in the chat but hidden in the panel until "Этапы работы: показывать".
            self._emit("stage", request_id=None, auto=True, **payload)
            return {"action": "continue", "auto_mode": True}
        response = self._wait("stage", **payload)
        if response is None:
            return {"action": "continue", "timeout": True}
        if response.get("action") == "change":
            return {"action": "change", "comment": response.get("comment") or "",
                    "instruction": "Пользователь просит изменить этап. Внеси изменения, проверь и снова вызови "
                                   "stage_report для этого этапа."}
        return {"action": "continue"}

    def _wait(self, kind, **payload):
        with self._lock:
            if self._stopped:
                return None
            self._counter += 1
            request_id = "r%d" % self._counter
            event = threading.Event()
            holder = {}
            self._pending = (request_id, event, holder)
        self._emit(kind, request_id=request_id, **payload)
        event.wait(WAIT_TIMEOUT_S)
        with self._lock:
            self._pending = None
        return holder.get("response")

    # ---- called by the HTTP server ----

    def respond(self, request_id, response):
        with self._lock:
            if self._pending is None or self._pending[0] != request_id:
                return False
            _, event, holder = self._pending
            holder["response"] = response
        self._emit("response", request_id=request_id, response=response)
        event.set()
        return True

    def stop(self):
        with self._lock:
            self._stopped = True
            pending = self._pending
        if pending is not None:
            pending[1].set()

    def resetStop(self):
        with self._lock:
            self._stopped = False

    def stopped(self):
        return self._stopped
