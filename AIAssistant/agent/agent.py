# Dialogue loop: user message -> LLM -> tool calls -> LLM -> ... -> final answer.
# Everything the agent does is published as events that the web chat polls.
#
# Each request runs in its own thread with a run id. "Stop" bumps the run id: the chat is released at once,
# and the old thread (which may still be waiting for the LLM) drops its results instead of touching the dialogue.

import json
import re
import threading
import time
import traceback

from UM.Logger import Logger

from .interaction import AGENT_TOOLS, AGENT_TOOL_NAMES, Interaction
from .llm import chat_completion, LLMError
from .prompt import SYSTEM_PROMPT

MAX_TOOL_ROUNDS = 60
# Tool results are shown in full in the chat, but the LLM gets a compact copy: long contexts make every
# round slower and costlier.
MAX_TOOL_RESULT_CHARS = 6000
CJK_RE = re.compile(u"[぀-ヿ㐀-䶿一-鿿가-힯＀-￯]")


def has_cjk(text):
    return bool(text) and bool(CJK_RE.search(text))


class _Stopped(Exception):
    pass


class _Session:
    """One chat: LLM history and the event feed shown in the web chat."""

    def __init__(self, chat_id, messages, events):
        self.chat_id = chat_id
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}] + \
            [m for m in messages if m.get("role") != "system"]  # always the current rules
        self.events = events
        self.resumable = False


class Agent:
    """Runs one request at a time (there is one slicer); every chat keeps its own history on disk."""

    def __init__(self, settings, tools, workspace):
        self._settings = settings
        self._tools = tools
        self._workspace = workspace
        self._lock = threading.RLock()
        self._sessions = {}
        self._session = None  # chat of the current (or last) request: events and history go there
        self._busy = False
        self._run_id = 0
        self.interaction = Interaction(self._emit, settings)

    # ---- sessions ----

    def session(self, chat_id):
        with self._lock:
            if chat_id not in self._sessions:
                if self._workspace.get(chat_id) is None:
                    raise ValueError("Чат не найден")
                messages, events = self._workspace.loadChat(chat_id)
                self._sessions[chat_id] = _Session(chat_id, messages, events)
            return self._sessions[chat_id]

    def runningChat(self):
        return self._session.chat_id if (self._busy and self._session) else None

    def forget(self, chat_ids):
        with self._lock:
            for chat_id in chat_ids:
                self._sessions.pop(chat_id, None)

    def _persist(self, session=None):
        session = session or self._session
        if session is not None:
            try:
                self._workspace.saveChat(session.chat_id, session.messages[1:], session.events)
            except Exception:
                Logger.logException("w", "AI Assistant: cannot save chat")

    @property
    def _messages(self):
        return self._session.messages

    @_messages.setter
    def _messages(self, value):
        self._session.messages = value

    # ---- events for the web chat ----

    def emit(self, kind, **data):
        self._emit(kind, **data)

    def _emit(self, kind, **data):
        with self._lock:
            if self._session is None:
                return
            events = self._session.events
            event = {"id": len(events), "kind": kind, "time": time.time()}
            event.update(data)
            events.append(event)

    def eventsAfter(self, chat_id, after):
        session = self.session(chat_id)
        with self._lock:
            running = self.runningChat()
            return {"events": session.events[after + 1:], "busy": self._busy, "running_chat": running,
                    "waiting": running == chat_id and self.interaction.waiting(),
                    "resumable": session.resumable and not self._busy}

    def stop(self):
        with self._lock:
            if not self._busy:
                return False
            self._run_id += 1  # the running thread becomes stale
            self._busy = False
            self._session.resumable = True
        self.interaction.stop()
        self._tools.abort()
        self._emit("stopped", text="Остановлено. Нажмите «Продолжить», чтобы продолжить с этого места, "
                                   "или отправьте новый запрос.")
        self._emit("idle")
        self._persist()
        return True

    def resume(self, chat_id):
        # Continue the stopped request: everything done before the stop is in the dialogue history,
        # the interrupted step (if any) got an "остановлено" result and is simply repeated.
        if not self.session(chat_id).resumable:
            return False
        return self.submit(chat_id, "Продолжай работу с того места, где тебя остановили. Не повторяй уже "
                                    "сделанные шаги; прерванный шаг выполни заново.", [], resumed=True)

    # ---- dialogue ----

    def submit(self, chat_id, text, attachments, resumed=False):
        session = self.session(chat_id)
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            self._run_id += 1
            run_id = self._run_id
            self._session = session
            session.resumable = False
        self.interaction.resetStop()
        self._tools.resetAbort()
        self._repairHistory()
        if resumed:
            self._emit("resumed", text="Продолжаю с места остановки")
        else:
            self._emit("user", text=text, attachments=[a.get("name") for a in attachments])
            self._autoTitle(chat_id, text)
        self._persist()
        threading.Thread(target=self._run, args=(run_id, text, attachments), daemon=True).start()
        return True

    def _autoTitle(self, chat_id, text):
        node = self._workspace.get(chat_id)
        if node and node["name"] == "Новый чат" and (text or "").strip():
            title = " ".join(text.split())[:48]
            self._workspace.rename(chat_id, title + ("…" if len(text.strip()) > 48 else ""))
        else:
            self._workspace.touch(chat_id)

    def _alive(self, run_id):
        if run_id != self._run_id:
            raise _Stopped()

    def _repairHistory(self):
        # After a stop the dialogue may end with tool calls that never got results; the API rejects that.
        answered = set(m.get("tool_call_id") for m in self._messages if m.get("role") == "tool")
        repaired = []
        for message in self._messages:
            repaired.append(message)
            for call in message.get("tool_calls") or []:
                if call.get("id") not in answered:
                    repaired.append({"role": "tool", "tool_call_id": call.get("id"),
                                     "content": json.dumps({"error": "остановлено пользователем"}, ensure_ascii=False)})
        self._messages = repaired

    def _userContent(self, text, attachments):
        parts = [{"type": "text", "text": text or "(без текста)"}]
        for a in attachments:
            if a.get("kind") == "image":
                parts.append({"type": "image_url", "image_url": {"url": a["data_url"]}})
            elif a.get("kind") == "document":
                parts.append({"type": "text",
                              "text": "Пользователь приложил документ «%s». Его текст:\n\n%s" % (a.get("name"), a.get("text"))})
            else:
                parts.append({"type": "text",
                              "text": "Пользователь приложил файл «%s», путь на диске: %s" % (a.get("name"), a.get("path"))})
        return parts

    def _complete(self, run_id, tools=None):
        message = chat_completion(self._settings.get("base_url"), self._settings.get("api_key"),
                                  self._settings.get("model"), self._messages, tools)
        self._alive(run_id)
        return message

    def _run(self, run_id, text, attachments):
        try:
            if not self._settings.isConfigured():
                self._emit("error", text="Не заданы адрес, ключ или модель LLM. Откройте «Настройки» в чате.")
                return
            self._messages.append({"role": "user", "content": self._userContent(text, attachments)})
            tools = AGENT_TOOLS + self._tools.openaiList()
            for _ in range(MAX_TOOL_ROUNDS):
                self._alive(run_id)
                self._emit("thinking")
                message = self._complete(run_id, tools)
                tool_calls = message.get("tool_calls") or []
                content = message.get("content") or ""
                if not tool_calls:
                    content = self._russianOnly(run_id, content)
                    self._messages.append({"role": "assistant", "content": content})
                    self._emit("assistant", text=content)
                    return
                self._messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
                if content.strip():
                    self._emit("assistant", text=CJK_RE.sub("", content))
                for call in tool_calls:
                    self._runTool(run_id, call)
            # Step limit reached: ask for a summary without tools instead of dropping the work silently.
            self._emit("thinking")
            self._messages.append({"role": "user", "content":
                                   "Лимит шагов исчерпан. Без вызова инструментов кратко опиши, что уже сделано "
                                   "и что осталось сделать; спроси, продолжать ли."})
            message = self._complete(run_id)
            content = self._russianOnly(run_id, message.get("content") or "")
            self._messages.append({"role": "assistant", "content": content})
            self._emit("assistant", text=content)
        except _Stopped:
            Logger.log("i", "AI Assistant: stale request %d finished after stop", run_id)
        except LLMError as e:
            if run_id == self._run_id:
                self._emit("error", text=str(e))
        except Exception as e:
            Logger.logException("e", "AI Assistant agent failed")
            if run_id == self._run_id:
                self._emit("error", text="Внутренняя ошибка: %s" % e, detail=traceback.format_exc()[-1500:])
        finally:
            with self._lock:
                current = run_id == self._run_id
                if current:
                    self._busy = False
            if current:
                self._emit("idle")
                self._persist()

    def _russianOnly(self, run_id, content):
        # Some models slip into Chinese mid-answer. Ask once for a clean Russian rewrite.
        if not has_cjk(content):
            return content
        self._emit("thinking")
        rewrite = chat_completion(self._settings.get("base_url"), self._settings.get("api_key"),
                                  self._settings.get("model"),
                                  [{"role": "system", "content": "Ты редактор. Перепиши текст полностью на русском "
                                    "языке, сохранив смысл, цифры, таблицы и Markdown. Никаких китайских или "
                                    "японских символов. Верни только текст."},
                                   {"role": "user", "content": content}])
        self._alive(run_id)
        fixed = rewrite.get("content") or content
        return CJK_RE.sub("", fixed)

    def _runTool(self, run_id, call):
        name = call["function"]["name"]
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
        except ValueError:
            args = {}
        if name in AGENT_TOOL_NAMES:
            # Interactive steps are rendered as cards in the chat, not as tool badges.
            try:
                result = self.interaction.call(name, args)
            except Exception as e:
                result = {"error": str(e)}
            self._alive(run_id)
            self._messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": _compact(result)})
            return
        self._emit("tool_call", name=name, arguments=args, call_id=call.get("id"))
        try:
            result = self._tools.call(name, args)
            ok = True
        except Exception as e:
            result = {"error": str(e)}
            ok = False
        self._alive(run_id)
        self._emit("tool_result", name=name, ok=ok, result=result, call_id=call.get("id"))
        self._messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": _compact(result)})
        self._persist()  # long runs: keep the chat on disk after every step


def _compact(result):
    text = json.dumps(result, ensure_ascii=False, default=str, separators=(",", ":"))
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return text
    return text[:MAX_TOOL_RESULT_CHARS] + "…[результат сокращён; полная версия показана пользователю]"
