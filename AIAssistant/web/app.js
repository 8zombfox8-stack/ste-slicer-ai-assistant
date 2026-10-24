// Web chat for the STE Slicer AI Assistant. Talks to the plugin's local server.

const $ = (id) => document.getElementById(id);
const log = $("log");
let lastEventId = -1;
let busy = false;
let pending = [];          // attachments waiting to be sent: {name, data}
let thinkingEl = null;
const toolEls = {};        // call_id -> <details>

const TOOL_LABELS = {
  get_slicer_status: "Состояние слайсера",
  get_settings: "Чтение настроек",
  find_settings: "Поиск настроек",
  set_settings: "Изменение настроек",
  set_printing_mode: "Режим печати 3D/5D",
  list_materials: "Список материалов",
  select_material: "Подбор материала по ТЗ",
  set_material: "Выбор материала",
  list_quality_profiles: "Список профилей",
  set_quality_profile: "Выбор профиля",
  save_custom_profile: "Сохранение профиля",
  clear_plate: "Очистка стола",
  reset_settings: "Сброс настроек к профилю",
  load_model: "Загрузка модели",
  list_objects: "Объекты на столе",
  analyze_model: "Анализ геометрии",
  rotate_object: "Поворот",
  move_object: "Перемещение",
  scale_object: "Масштаб",
  lay_on_plate: "Положить на стол",
  setup_5d: "Подготовка к 5D",
  arrange_all: "Авторасстановка",
  delete_object: "Удаление объекта",
  slice: "Слайсинг",
  get_slice_status: "Статус слайсинга",
  get_print_info: "Время и материал",
  export_gcode: "Экспорт G-code",
  save_project: "Сохранение проекта 3MF",
  save_results: "Сохранение результата",
  preflight_check: "Предполётная проверка",
  compare_variants: "Сравнение вариантов",
  make_tech_card: "Техкарта",
  save_recipe: "Сохранение рецепта",
  list_recipes: "Список рецептов",
  apply_recipe: "Применение рецепта",
  check_process: "Проверка параметров процесса",
  material_info: "Свойства материала",
};
let progressEl = null;

// Plain-language explanation of what each tool does, shown under its badge.
const TOOL_HINTS = {
  get_slicer_status: "Смотрю, какой принтер, материал и режим сейчас выбраны",
  get_settings: "Читаю текущие значения параметров печати",
  find_settings: "Ищу нужный параметр среди сотен настроек слайсера",
  set_settings: "Меняю параметры печати — причины будут в итоге этапа",
  set_printing_mode: "Переключаю способ печати: обычный послойный (3D) или спиралью вокруг оси (5D)",
  list_materials: "Смотрю, какими материалами умеет печатать этот принтер",
  select_material: "Проверяю каждый материал по требованиям задачи и отсеиваю неподходящие",
  set_material: "Выбираю материал в слайсере — вместе с ним подтягиваются его температуры",
  list_quality_profiles: "Смотрю доступные профили качества (толщину слоя)",
  set_quality_profile: "Выбираю профиль качества",
  clear_plate: "Убираю со стола старые модели",
  reset_settings: "Возвращаю настройки к заводскому профилю, чтобы не унаследовать чужие изменения",
  load_model: "Загружаю модель детали на стол",
  analyze_model: "Изучаю форму детали: размеры, нависания, есть ли ось вращения и отверстие",
  rotate_object: "Поворачиваю деталь на столе",
  move_object: "Перемещаю деталь на столе",
  setup_5d: "Ставлю деталь осью вверх по центру и настраиваю основу для печати спиралью",
  check_process: "Сверяю температуры, обдув, скорость и слой с рекомендациями для материала",
  preflight_check: "Проверяю деталь перед печатью: поместится ли, не отвалится ли от стола, нужны ли опоры",
  slice: "Превращаю модель в программу для принтера (G-code)",
  save_results: "Сохраняю проект и программу печати в папку результатов",
  make_tech_card: "Оформляю технологическую карту — паспорт изготовления детали",
  compare_variants: "Нарезаю несколько вариантов и сравниваю время, расход и цену",
  material_info: "Смотрю свойства материала в базе и источники данных",
};

// ---------- rendering ----------

function hideEmpty() { const e = $("empty"); if (e) e.remove(); }

function scrollDown() { log.scrollTop = log.scrollHeight; }

function addMessage(kind, text, files) {
  hideEmpty();
  const el = document.createElement("div");
  el.className = "msg " + kind;
  if (kind === "assistant") el.innerHTML = renderMarkdown(text);
  else el.textContent = text;
  if (files && files.length) {
    const f = document.createElement("div");
    f.className = "files";
    f.textContent = "📎 " + files.join(", ");
    el.appendChild(f);
  }
  log.appendChild(el);
  scrollDown();
}

function setThinking(on) {
  if (on && !thinkingEl) {
    thinkingEl = document.createElement("div");
    thinkingEl.className = "thinking";
    thinkingEl.textContent = "Ассистент думает";
    log.appendChild(thinkingEl);
    scrollDown();
  } else if (!on && thinkingEl) {
    thinkingEl.remove();
    thinkingEl = null;
  }
}

function addToolCall(ev) {
  hideEmpty();
  const d = document.createElement("details");
  d.className = "tool";
  const label = TOOL_LABELS[ev.name] || ev.name;
  const hint = TOOL_HINTS[ev.name];
  d.innerHTML = "<summary>🔧 " + escapeHtml(label) + " · <b>" + escapeHtml(ev.name) +
    "</b><span class='state'>⏳</span>" + (hint ? "<div class='hint'>" + escapeHtml(hint) + "</div>" : "") +
    "</summary><pre></pre>";
  d.querySelector("pre").textContent = "Аргументы:\n" + JSON.stringify(ev.arguments, null, 2);
  log.appendChild(d);
  toolEls[ev.call_id] = d;
  scrollDown();
}

function addToolResult(ev) {
  const d = toolEls[ev.call_id];
  if (!d) return;
  d.querySelector(".state").textContent = ev.ok ? "✅" : "❌";
  if (!ev.ok) d.classList.add("failed");
  d.querySelector("pre").textContent += "\n\nРезультат:\n" + JSON.stringify(ev.result, null, 2);
}

// Small safe subset of Markdown: text is escaped first, then formatting is applied.
function renderInline(s) {
  return s
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((\/files\/[^)\s]+|https?:\/\/[^)\s]+)\)/g, "<a href='$2' target='_blank'>$1</a>");
}

function renderTable(lines) {
  const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => renderInline(c.trim()));
  const head = cells(lines[0]);
  const body = lines.slice(2).map(cells);
  return "<table class='md'><tr>" + head.map((c) => "<th>" + c + "</th>").join("") + "</tr>" +
    body.map((r) => "<tr>" + r.map((c) => "<td>" + c + "</td>").join("") + "</tr>").join("") + "</table>";
}

function renderMarkdown(text) {
  const lines = escapeHtml(text).split("\n");
  const out = [];
  for (let i = 0; i < lines.length; i++) {
    const isRow = (l) => l !== undefined && /^\s*\|.*\|\s*$/.test(l);
    if (isRow(lines[i]) && lines[i + 1] !== undefined && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const block = [];
      while (isRow(lines[i])) block.push(lines[i++]);
      i--;
      out.push(renderTable(block));
      continue;
    }
    out.push(renderInline(lines[i]
      .replace(/^#{1,4} (.+)$/, "<b class='h'>$1</b>")
      .replace(/^\s*[-*] /, "• ")));
  }
  return out.join("\n").replace(/\n{3,}/g, "\n\n").replace(/\n?(<table[\s\S]*?<\/table>)\n?/g, "$1");
}

function addFileLink(ev) {
  hideEmpty();
  const a = document.createElement("a");
  a.className = "file-link";
  a.href = ev.url;
  a.target = "_blank";
  a.textContent = "📄 " + ev.label + " — открыть";
  log.appendChild(a);
  scrollDown();
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function showProgress(ev) {
  if (!progressEl) {
    progressEl = document.createElement("div");
    progressEl.className = "progress";
    progressEl.innerHTML = "<span class='label'></span><div class='bar'><i></i></div><span class='pct'></span>";
    log.appendChild(progressEl);
  }
  progressEl.querySelector(".label").textContent = ev.label;
  progressEl.querySelector("i").style.width = ev.percent + "%";
  progressEl.querySelector(".pct").textContent = ev.failed ? "ошибка" : ev.percent + "%";
  if (ev.failed) progressEl.classList.add("failed");
  scrollDown();
  if (ev.done) progressEl = null;
}

// ---------- interactive cards: questions and stage checkpoints ----------

const cards = {};  // request_id -> card element
const SYSTEM_DECIDES = /пусть\s+(решит|выберет)|решит(е)?\s+(сам|систем)|на\s+(ваше|твоё|твое)\s+усмотрение|выберите\s+сами|не\s+знаю/i;

async function respond(requestId, response, card) {
  card.classList.add("done");
  card.querySelectorAll("button, input, textarea").forEach((el) => (el.disabled = true));
  await fetch("/api/respond", { method: "POST", body: JSON.stringify({ request_id: requestId, response }) });
}

function addQuestionsCard(ev) {
  if (!Array.isArray(ev.questions) || !ev.questions.length) return;
  hideEmpty();
  setThinking(false);
  const card = document.createElement("div");
  card.className = "card questions";
  let html = "<div class='card-title'>❓ Уточняющие вопросы</div>";
  if (ev.intro) html += "<div class='card-text'>" + escapeHtml(ev.intro) + "</div>";
  ev.questions.forEach((q, qi) => {
    // "Let the system decide" is a card-level button; drop duplicates the model may add as options.
    const options = (q.options || []).filter((o) => !SYSTEM_DECIDES.test(o));
    html += "<div class='q' data-id='" + escapeHtml(q.id) + "' data-multiple='" + (q.multiple ? 1 : 0) + "'>" +
      "<div class='q-text'>" + (qi + 1) + ". " + escapeHtml(q.question) + "</div>" +
      (q.why ? "<div class='q-why'>" + escapeHtml(q.why) + "</div>" : "") +
      "<div class='opts'>" + options.map((o) =>
        "<button type='button' class='opt'>" + escapeHtml(o) + "</button>").join("") + "</div>" +
      "<input class='custom' placeholder='Свой вариант (необязательно)'></div>";
  });
  if (ev.auto) {
    html += "<div class='card-note'>Автоматический режим: ассистент ответит на вопросы сам и назовёт допущения.</div>";
  } else {
    html += "<div class='card-actions'><button type='button' class='primary send'>Ответить</button>" +
      "<button type='button' class='ghost skip'>Пусть решит система</button></div>";
  }
  card.innerHTML = html;
  card.querySelectorAll(".q").forEach((qEl) => {
    qEl.querySelectorAll(".opt").forEach((b) => b.addEventListener("click", () => {
      if (qEl.dataset.multiple !== "1") qEl.querySelectorAll(".opt").forEach((x) => x !== b && x.classList.remove("on"));
      b.classList.toggle("on");
    }));
  });
  if (!ev.auto) {
    const collect = (skipAll) => {
      const answers = {};
      card.querySelectorAll(".q").forEach((qEl) => {
        answers[qEl.dataset.id] = skipAll ? {} : {
          selected: [...qEl.querySelectorAll(".opt.on")].map((b) => b.textContent),
          custom: qEl.querySelector(".custom").value,
        };
      });
      return { answers };
    };
    card.querySelector(".send").onclick = () => respond(ev.request_id, collect(false), card);
    card.querySelector(".skip").onclick = () => respond(ev.request_id, collect(true), card);
    cards[ev.request_id] = card;
  } else {
    card.classList.add("done");
  }
  log.appendChild(card);
  scrollDown();
}

const STATUS_ICON = { ok: "✅", warning: "⚠️", problem: "❌" };

function addStageCard(ev) {
  hideEmpty();
  setThinking(false);
  const card = document.createElement("div");
  card.className = "card stage";
  let html = "<div class='card-title'>Этап " + escapeHtml(ev.stage) + " из 7 · " + escapeHtml(ev.title || "") + "</div>" +
    "<div class='card-text'>" + escapeHtml(ev.plain_summary || "") + "</div>";
  if (!Array.isArray(ev.decisions)) ev.decisions = [];
  if (!Array.isArray(ev.checks)) ev.checks = [];
  if (ev.decisions.length) {
    // The "Почему" column is shown only when there is something to put in it.
    const withWhy = ev.decisions.some((d) => (d.why || "").trim());
    html += "<table class='md'><tr><th>Решение</th><th>Значение</th>" + (withWhy ? "<th>Почему</th>" : "") + "</tr>" +
      ev.decisions.map((d) => "<tr><td>" + escapeHtml(d.what) + "</td><td><b>" + escapeHtml(d.value) + "</b></td>" +
        (withWhy ? "<td>" + escapeHtml((d.why || "").trim() || "—") + "</td>" : "") + "</tr>").join("") + "</table>";
  }
  if (ev.checks && ev.checks.length) {
    html += "<ul class='checks'>" + ev.checks.map((c) => "<li>" + (STATUS_ICON[c.status] || "•") + " <b>" +
      escapeHtml(c.name) + "</b>" + (c.detail ? " — " + escapeHtml(c.detail) : "") + "</li>").join("") + "</ul>";
  }
  if (ev.auto) {
    html += "<div class='card-note'>Автоматический режим: продолжаю без подтверждения.</div>";
  } else {
    // One field and one button: empty field — continue, text in the field — send it as a change request.
    html += "<div class='change-box'><textarea placeholder='Что изменить? Например: «возьми PETG» или «уменьши заполнение». " +
      "Если всё устраивает — просто нажмите «Продолжить».'></textarea>" +
      "<button type='button' class='primary go'>Продолжить</button></div>";
  }
  card.innerHTML = html;
  if (!ev.auto) {
    const area = card.querySelector("textarea");
    const go = card.querySelector(".go");
    area.addEventListener("input", () => {
      go.textContent = area.value.trim() ? "Отправить изменения" : "Продолжить";
    });
    area.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); go.click(); }
    });
    go.onclick = () => {
      const comment = area.value.trim();
      respond(ev.request_id, comment ? { action: "change", comment } : { action: "continue" }, card);
    };
    cards[ev.request_id] = card;
  } else {
    card.classList.add("done");
  }
  log.appendChild(card);
  scrollDown();
}

function markResponded(ev) {
  const card = cards[ev.request_id];
  if (!card) return;
  card.classList.add("done");
  card.querySelectorAll("button, input, textarea").forEach((el) => (el.disabled = true));
  const r = ev.response || {};
  if (r.answers) {
    card.querySelectorAll(".q").forEach((qEl) => {
      const a = r.answers[qEl.dataset.id] || {};
      qEl.querySelectorAll(".opt").forEach((b) => b.classList.toggle("on", (a.selected || []).includes(b.textContent)));
      if (a.custom) qEl.querySelector(".custom").value = a.custom;
    });
  }
  const note = document.createElement("div");
  note.className = "card-note";
  note.textContent = r.answers ? "Ответы отправлены" : r.action === "change" ? "Попросили изменить: " + (r.comment || "") : "Продолжаем";
  card.appendChild(note);
}

function handleEvent(ev) {
  switch (ev.kind) {
    case "questions": addQuestionsCard(ev); break;
    case "stage": addStageCard(ev); break;
    case "response": markResponded(ev); break;
    case "resumed": addMessage("note", "▶ " + ev.text); break;
    case "stopped":
      setThinking(false);
      Object.values(cards).forEach((c) => {
        if (!c.classList.contains("done")) {
          c.classList.add("done");
          c.querySelectorAll("button, input, textarea").forEach((el) => (el.disabled = true));
        }
      });
      addMessage("error", ev.text);
      break;
    case "progress": showProgress(ev); break;
    case "file": addFileLink(ev); break;
    case "user": addMessage("user", ev.text, ev.attachments); break;
    case "thinking": setThinking(true); break;
    case "assistant": setThinking(false); if (ev.text) addMessage("assistant", ev.text); break;
    case "tool_call": setThinking(false); addToolCall(ev); break;
    case "tool_result": addToolResult(ev); refreshStatus(); break;
    case "error": setThinking(false); addMessage("error", ev.text); break;
    case "idle": setThinking(false); break;
    case "reset": log.innerHTML = ""; break;
  }
}

// ---------- server communication ----------

async function poll() {
  try {
    if (!currentChat) { await loadTree(); setTimeout(poll, 600); return; }
    const chat = currentChat;
    const r = await fetch("/api/events?chat=" + encodeURIComponent(chat) + "&after=" + lastEventId);
    const data = await r.json();
    if (chat !== currentChat) { setTimeout(poll, 50); return; }  // switched chats while waiting
    if (data.missing_chat) { currentChat = null; await loadTree(); setTimeout(poll, 300); return; }
    for (const ev of data.events) {
      lastEventId = ev.id;
      try {
        handleEvent(ev);
      } catch (err) {
        // One malformed event must not freeze the whole feed.
        console.error("event", ev, err);
      }
    }
    if ((data.running_chat || null) !== runningChat) { runningChat = data.running_chat || null; renderTree(); }
    setBusy(data.busy, data.waiting, data.resumable, data.running_chat && data.running_chat !== chat);
    if (!statusOk) refreshStatus();
  } catch (e) {
    statusOk = false;
    $("status").textContent = "Нет связи с плагином — слайсер закрыт?";
  }
  setTimeout(poll, 600);
}
let statusOk = true;

async function refreshStatus() {
  try {
    const s = await (await fetch("/api/status")).json();
    if (s.error || !s.printer) {
      $("status").textContent = s.error || s.message || "Принтер не выбран";
      return;
    }
    statusOk = true;
    const modes = { classic: "3D", cylindrical: "5D Spiral", cylindrical_full: "5D Spiral Full" };
    $("status").innerHTML =
      "<b>" + escapeHtml(s.printer) + "</b> · режим <b>" + escapeHtml(modes[s.printing_mode] || s.printing_mode) +
      "</b> · " + escapeHtml(s.material || "—") + " · " + escapeHtml(s.quality || "—") +
      " · объектов: " + s.objects_on_plate;
  } catch (e) { /* poll() reports connection problems */ }
}

let resumable = false;

function setBusy(b, waiting, canResume, elsewhere) {
  busy = b;
  resumable = !b && !!canResume;
  $("send").disabled = b;
  $("send").textContent = !b ? "Отправить" : elsewhere ? "Занят в другом чате" : waiting ? "Ждёт вашего ответа ↑" : "Работаю…";
  // One button: «Стоп» while working, «Продолжить» after a stop.
  $("btn-stop").hidden = !b && !resumable;
  $("btn-stop").textContent = b ? "Стоп" : "Продолжить";
  $("btn-stop").classList.toggle("resume", resumable);
  $("btn-stop").title = b ? "Остановить ассистента" : "Продолжить с места остановки";
  $("btn-reset").classList.toggle("blocked", b);
  $("btn-reset").title = b ? "Идёт обработка запроса — сначала нажмите «Стоп»" : "Новый чат в текущей папке";
  if (!b) $("btn-reset").textContent = "Новый чат";
}

function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 3500);
}

function showMode(interactive) {
  $("mode-interactive").checked = interactive;
  $("mode-label").textContent = interactive ? "Интерактивно" : "Автоматически";
}

async function send(text) {
  if (busy) return;
  if (!text.trim() && !pending.length) return;
  const body = { chat: currentChat, text, attachments: pending };
  pending = [];
  renderChips();
  setBusy(true);
  const r = await fetch("/api/message", { method: "POST", body: JSON.stringify(body) });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    addMessage("error", err.error || "Ассистент ещё занят предыдущим запросом.");
    setBusy(false);
  }
}

// ---------- projects: folders of any depth and chats ----------

let currentChat = null;
let treeNodes = [];
let runningChat = null;
let collapsed = {};
try { collapsed = JSON.parse(localStorage.getItem("ai.collapsed") || "{}"); } catch (e) { collapsed = {}; }

function nodeById(id) { return treeNodes.find((n) => n.id === id); }
function childrenOf(parent) { return treeNodes.filter((n) => (n.parent || null) === parent); }

async function treeAction(action, data) {
  const r = await fetch("/api/tree/" + action, { method: "POST", body: JSON.stringify(data) });
  const res = await r.json().catch(() => ({}));
  if (!r.ok || res.error) toast(res.error || "Не удалось выполнить действие");
  await loadTree();
  return res;
}

async function loadTree() {
  try {
    const data = await (await fetch("/api/tree")).json();
    treeNodes = data.nodes || [];
    runningChat = data.running_chat || null;
  } catch (e) { return; }
  if (!currentChat || !nodeById(currentChat)) {
    let saved = null;
    try { saved = localStorage.getItem("ai.chat"); } catch (e) { saved = null; }
    const first = treeNodes.filter((n) => n.type === "chat").sort((a, b) => b.updated - a.updated)[0];
    openChat(saved && nodeById(saved) ? saved : first && first.id);
    return;
  }
  renderTree();
}

function renderTree() {
  const box = $("tree");
  box.innerHTML = "";
  const build = (parent, depth) => {
    childrenOf(parent).forEach((n) => {
      const row = document.createElement("div");
      row.className = "tree-row " + n.type + (n.id === currentChat ? " active" : "");
      row.style.paddingLeft = (8 + depth * 14) + "px";
      row.draggable = true;
      row.dataset.id = n.id;
      const icon = n.type === "folder" ? (collapsed[n.id] ? "▸ 📁" : "▾ 📂") : (n.id === runningChat ? "⏳" : "💬");
      row.innerHTML = "<span class='tree-icon'></span><span class='tree-name'></span>" +
        "<span class='tree-tools'><button class='mini' data-act='rename' title='Переименовать'>✎</button>" +
        "<button class='mini' data-act='delete' title='Удалить'>🗑</button></span>";
      row.querySelector(".tree-icon").textContent = icon;
      row.querySelector(".tree-name").textContent = n.name;
      row.onclick = (e) => {
        const act = e.target.dataset && e.target.dataset.act;
        if (act === "rename") return renameNode(n);
        if (act === "delete") return deleteNode(n);
        if (n.type === "folder") {
          collapsed[n.id] = !collapsed[n.id];
          try { localStorage.setItem("ai.collapsed", JSON.stringify(collapsed)); } catch (err) { /* optional */ }
          selectedFolder = n.id;
          renderTree();
        } else {
          openChat(n.id);
        }
      };
      row.ondblclick = (e) => { e.preventDefault(); renameNode(n); };
      row.ondragstart = (e) => e.dataTransfer.setData("text/node", n.id);
      if (n.type === "folder") {
        row.ondragover = (e) => { e.preventDefault(); row.classList.add("drop"); };
        row.ondragleave = () => row.classList.remove("drop");
        row.ondrop = (e) => {
          e.preventDefault(); e.stopPropagation();
          const id = e.dataTransfer.getData("text/node");
          if (id && id !== n.id) treeAction("move", { id, parent: n.id });
        };
      }
      if (n.type === "folder" && n.id === selectedFolder) row.classList.add("selected");
      box.appendChild(row);
      if (n.type === "folder" && !collapsed[n.id]) build(n.id, depth + 1);
    });
  };
  build(null, 0);
  const node = nodeById(currentChat);
  $("chat-title").textContent = node ? pathOf(node) : "";
}

function pathOf(node) {
  const parts = [];
  for (let n = node; n; n = n.parent ? nodeById(n.parent) : null) parts.unshift(n.name);
  return parts.join("  ›  ");
}

let selectedFolder = null;

function targetFolder() {
  if (selectedFolder && nodeById(selectedFolder)) return selectedFolder;
  const chat = nodeById(currentChat);
  return chat ? chat.parent || null : null;
}

function openChat(id) {
  if (!id) return;
  currentChat = id;
  try { localStorage.setItem("ai.chat", id); } catch (e) { /* optional */ }
  const chat = nodeById(id);
  selectedFolder = chat ? chat.parent || null : null;
  lastEventId = -1;
  log.innerHTML = "";
  Object.keys(cards).forEach((k) => delete cards[k]);
  Object.keys(toolEls).forEach((k) => delete toolEls[k]);
  progressEl = null;
  thinkingEl = null;
  renderTree();
}

async function renameNode(n) {
  const name = prompt(n.type === "folder" ? "Название папки" : "Название чата", n.name);
  if (name && name.trim() && name !== n.name) await treeAction("rename", { id: n.id, name: name.trim() });
}

async function deleteNode(n) {
  const what = n.type === "folder" ? "папку «" + n.name + "» со всеми вложенными папками и чатами" : "чат «" + n.name + "»";
  if (!confirm("Удалить " + what + "? Это действие нельзя отменить.")) return;
  await treeAction("delete", { id: n.id });
}

async function newChat() {
  if (busy && runningChat === currentChat) { toast("Ассистент сейчас работает в этом чате — новый чат можно создать, работа продолжится."); }
  const res = await treeAction("create", { type: "chat", parent: targetFolder() });
  if (res.node) openChat(res.node.id);
}

async function newFolder() {
  const name = prompt("Название папки", "Новая папка");
  if (!name) return;
  const res = await treeAction("create", { type: "folder", name, parent: targetFolder() });
  if (res.node) { selectedFolder = res.node.id; collapsed[res.node.id] = false; renderTree(); }
}

// ---------- attachments ----------

function readAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function addFiles(files) {
  for (const f of files) pending.push({ name: f.name, data: await readAsBase64(f) });
  renderChips();
}

function renderChips() {
  const box = $("chips");
  box.innerHTML = "";
  pending.forEach((p, i) => {
    const c = document.createElement("span");
    c.className = "chip";
    c.textContent = p.name;
    const x = document.createElement("button");
    x.type = "button";
    x.textContent = "✕";
    x.onclick = () => { pending.splice(i, 1); renderChips(); };
    c.appendChild(x);
    box.appendChild(c);
  });
}

// ---------- settings ----------

async function openSettings() {
  const s = await (await fetch("/api/settings")).json();
  const f = $("settings-form");
  f.base_url.value = s.base_url || "";
  f.model.value = s.model || "";
  f.output_dir.value = s.output_dir || "";
  f.machine_rate_rub.value = s.machine_rate_rub || "";
  f.api_key.value = "";
  f.api_key.placeholder = s.api_key_set ? "сохранён (" + s.api_key_hint + ")" : "вставьте ключ";
  $("settings").showModal();
}

$("settings").addEventListener("close", async () => {
  if ($("settings").returnValue !== "save") return;
  const f = $("settings-form");
  await fetch("/api/settings", {
    method: "POST",
    body: JSON.stringify({ base_url: f.base_url.value, api_key: f.api_key.value, model: f.model.value,
                           output_dir: f.output_dir.value, machine_rate_rub: f.machine_rate_rub.value }),
  });
});

// ---------- wiring ----------

$("form").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("input").value;
  $("input").value = "";
  autosize();
  send(text);
});

$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("form").requestSubmit(); }
});

function autosize() {
  const t = $("input");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 200) + "px";
}
$("input").addEventListener("input", autosize);

$("file").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => { e.preventDefault(); addFiles(e.dataTransfer.files); });

$("btn-settings").onclick = openSettings;
$("btn-reset").onclick = () => {
  if (busy) { toast("Идёт обработка запроса. Чтобы начать новый чат, сначала нажмите «Стоп»."); return; }
  newChat();
};
$("btn-reset").addEventListener("mouseenter", () => { if (busy) $("btn-reset").textContent = "Идёт обработка…"; });
$("btn-reset").addEventListener("mouseleave", () => { $("btn-reset").textContent = "Новый чат"; });
$("btn-new-chat").onclick = () => $("btn-reset").onclick();
$("btn-new-folder").onclick = newFolder;
// Dropping a folder or chat on the empty part of the tree moves it to the top level.
$("tree").addEventListener("dragover", (e) => e.preventDefault());
$("tree").addEventListener("drop", (e) => {
  const id = e.dataTransfer.getData("text/node");
  if (id) { e.preventDefault(); e.stopPropagation(); treeAction("move", { id, parent: null }); }
});
$("tree").addEventListener("click", (e) => { if (e.target === $("tree")) { selectedFolder = null; renderTree(); } });
setInterval(loadTree, 5000);  // titles and the running-chat marker
$("btn-stop").onclick = async () => {
  const resume = resumable;
  $("btn-stop").disabled = true;
  await fetch(resume ? "/api/resume" : "/api/stop",
              { method: "POST", body: JSON.stringify({ chat: currentChat }) }).catch(() => {});
  $("btn-stop").disabled = false;
  if (resume) {
    setBusy(true, false, false);
  } else {
    setThinking(false);
    setBusy(false, false, true);
  }
};
$("mode-interactive").addEventListener("change", (e) => {
  showMode(e.target.checked);
  fetch("/api/settings", { method: "POST", body: JSON.stringify({ interactive: e.target.checked }) });
});

refreshStatus();
loadTree().then(poll);
fetch("/api/settings").then((r) => r.json()).then((s) => {
  showMode(s.interactive !== false);
  if (!s.base_url || !s.api_key_set || !s.model) openSettings();
});
