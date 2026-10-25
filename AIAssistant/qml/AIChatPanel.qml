// Chat panel of the AI Assistant inside STE Slicer ("AI Ассистент" tab).
// Talks to the agent through the AIChat bridge (agent/bridge.py); values travel as JSON strings.
import QtQuick 2.10
import QtQuick.Controls 2.3
import QtQuick.Layouts 1.3
import QtQuick.Dialogs 1.3

import UM 1.2 as UM

Rectangle {
    id: panel
    color: "#ffffff"

    property int fs: Math.max(12, UM.Theme.getFont("default").pixelSize)
    property int gap: 8

    property string currentChat: ""
    property int lastEventId: -1
    property bool busy: false
    property bool waiting: false
    property bool resumable: false
    property bool busyElsewhere: false
    property bool thinking: false
    property bool interactive: true
    property var treeNodes: []
    property var pendingFiles: []          // file:/// urls waiting to be sent
    property bool showDetails: false       // beginner view by default: no printer status, steps collapsed
    property bool confirmStages: false     // stage cards are hidden from beginners
    property var toolIndex: ({})           // call_id -> feed index
    property int progressIndex: -1

    readonly property var toolLabels: ({
        get_slicer_status: "Состояние слайсера", get_settings: "Чтение настроек", find_settings: "Поиск настроек",
        set_settings: "Изменение настроек", set_printing_mode: "Режим печати 3D/5D", list_materials: "Список материалов",
        select_material: "Подбор материала по ТЗ", set_material: "Выбор материала", list_quality_profiles: "Список профилей",
        set_quality_profile: "Выбор профиля", save_custom_profile: "Сохранение профиля", clear_plate: "Очистка стола",
        reset_settings: "Сброс настроек к профилю", load_model: "Загрузка модели", list_objects: "Объекты на столе",
        analyze_model: "Анализ геометрии", rotate_object: "Поворот", move_object: "Перемещение", scale_object: "Масштаб",
        lay_on_plate: "Положить на стол", setup_5d: "Подготовка к 5D", arrange_all: "Авторасстановка",
        delete_object: "Удаление объекта", slice: "Слайсинг", get_slice_status: "Статус слайсинга",
        get_print_info: "Время и материал", export_gcode: "Экспорт G-code", save_project: "Сохранение проекта 3MF",
        save_results: "Сохранение результата", preflight_check: "Предполётная проверка",
        compare_variants: "Сравнение вариантов", make_tech_card: "Техкарта", save_recipe: "Сохранение рецепта",
        list_recipes: "Список рецептов", apply_recipe: "Применение рецепта",
        check_process: "Проверка параметров процесса", material_info: "Свойства материала"
    })
    readonly property var toolHints: ({
        select_material: "проверяю материалы по требованиям задачи",
        analyze_model: "размеры, нависания, ось вращения, отверстие",
        setup_5d: "ставлю деталь осью вверх и настраиваю основу для спирали",
        check_process: "сверяю температуры, обдув, скорость и слой с нормами материала",
        preflight_check: "поместится ли, удержится ли на столе, нужны ли опоры",
        slice: "превращаю модель в программу для принтера",
        save_results: "сохраняю проект 3MF и G-code",
        make_tech_card: "паспорт изготовления детали",
        compare_variants: "сравниваю время, расход и цену вариантов"
    })
    // What a beginner sees in the activity line.
    readonly property var beginnerLabels: ({
        get_slicer_status: "Смотрю, что выбрано в слайсере", get_settings: "Смотрю настройки",
        find_settings: "Ищу нужную настройку", set_settings: "Настраиваю печать", set_printing_mode: "Выбираю способ печати",
        list_materials: "Смотрю доступные пластики", select_material: "Подбираю пластик", set_material: "Выбираю пластик",
        material_info: "Проверяю свойства пластика", list_quality_profiles: "Выбираю качество печати",
        set_quality_profile: "Выбираю качество печати", clear_plate: "Освобождаю стол", reset_settings: "Сбрасываю старые настройки",
        load_model: "Загружаю модель", analyze_model: "Изучаю форму детали", rotate_object: "Поворачиваю деталь",
        move_object: "Ставлю деталь на место", lay_on_plate: "Кладу деталь на стол", setup_5d: "Готовлю печать по кругу (5D)",
        check_process: "Проверяю режим печати", preflight_check: "Проверяю деталь перед печатью",
        slice: "Готовлю программу для принтера", save_results: "Сохраняю файлы", export_gcode: "Сохраняю программу для принтера",
        save_project: "Сохраняю проект", make_tech_card: "Оформляю карточку детали"
    })
    readonly property var modeNames: ({ classic: "3D", cylindrical: "5D Spiral", cylindrical_full: "5D Spiral Full" })
    readonly property var systemDecides: /пусть\s+(решит|выберет)|решит(е)?\s+(сам|систем)|на\s+(ваше|твоё|твое)\s+усмотрение|выберите\s+сами|не\s+знаю/i

    // The chat needs more room than the settings sidebar; the original width is restored on leaving the tab.
    Component.onCompleted: {
        if (parent && parent.initialWidth !== undefined)
            parent.initialWidth = Math.round(UM.Theme.getSize("sidebar").width * 1.45)
        loadTree()
        refreshStatus()
        var s = JSON.parse(AIChat.settings())
        interactive = s.interactive !== false
        confirmStages = s.confirm_stages === true
        if (!s.base_url || !s.api_key_set || !s.model) openSettings()
    }
    Component.onDestruction: {
        if (parent && parent.initialWidth !== undefined)
            parent.initialWidth = UM.Theme.getSize("sidebar").width
    }

    // ---------- helpers ----------

    function esc(s) {
        return String(s === undefined || s === null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
            .replace(/>/g, "&gt;").replace(/"/g, "&quot;")
    }

    function inline(s) {
        return s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
            .replace(/`([^`]+)`/g, "<code>$1</code>")
            .replace(/\[([^\]]+)\]\((\/files\/[^)\s]+|https?:\/\/[^)\s]+)\)/g, "<a href='$2'>$1</a>")
    }

    function htmlTable(head, rows) {
        var h = "<table border='1' cellspacing='0' cellpadding='4' width='100%' style='border-color:#d5dde3; border-collapse:collapse'>"
        h += "<tr>" + head.map(function (c) { return "<th align='left' style='background:#eef3f7'>" + c + "</th>" }).join("") + "</tr>"
        rows.forEach(function (r) { h += "<tr>" + r.map(function (c) { return "<td>" + c + "</td>" }).join("") + "</tr>" })
        return h + "</table>"
    }

    // Small safe subset of Markdown: the text is escaped first, then formatting is applied.
    function markdown(text) {
        var lines = esc(String(text).trim()).split("\n")
        var out = []
        var isRow = function (l) { return l !== undefined && /^\s*\|.*\|\s*$/.test(l) }
        var cells = function (l) { return l.trim().replace(/^\||\|$/g, "").split("|").map(function (c) { return inline(c.trim()) }) }
        for (var i = 0; i < lines.length; i++) {
            if (isRow(lines[i]) && lines[i + 1] !== undefined && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
                var block = []
                while (isRow(lines[i])) block.push(lines[i++])
                i--
                out.push(htmlTable(cells(block[0]), block.slice(2).map(cells)))
                continue
            }
            out.push(inline(lines[i].replace(/^#{1,4} (.+)$/, "<b>$1</b>").replace(/^\s*[-*] /, "• ")))
        }
        return out.join("<br>").replace(/(<br>){3,}/g, "<br><br>").replace(/(<br>)?(<table[\s\S]*?<\/table>)(<br>)?/g, "$2")
    }

    function add(kind, text, extra) {
        var item = { kind: kind, text: text || "", payload: "", state: "", callId: "", reqId: "", extra: "" }
        for (var k in extra) item[k] = extra[k]
        feed.append(item)
        return feed.count - 1
    }

    function findReq(reqId) {
        for (var i = feed.count - 1; i >= 0; i--)
            if (feed.get(i).reqId === reqId) return i
        return -1
    }

    function toast(text) { toastText.text = text; toastBox.visible = true; toastTimer.restart() }

    // ---------- events from the agent ----------

    function handleEvent(ev) {
        switch (ev.kind) {
        case "user":
            add("user", ev.text, { extra: (ev.attachments || []).join(", ") }); break
        case "assistant":
            thinking = false
            // Remarks between steps and the closing summary of a full preparation are kept but shown only with
            // technical details on (callId "details"); answers to ordinary questions are always shown.
            // Older chats have no summary flag: their closing summary starts with «Готово».
            var summary = ev.summary || /^\s*Готово\b/.test(ev.text || "")
            if (ev.text) add("assistant", markdown(ev.text), { callId: (ev.interim || summary) ? "details" : "" })
            break
        case "thinking": thinking = true; break
        case "idle": thinking = false; break
        case "error": thinking = false; add("error", ev.text); break
        case "resumed": add("note", "▶ " + ev.text); break
        case "tool_call":
            // Consecutive steps are grouped into one "activity" line; the list opens on click.
            thinking = false
            var last = feed.count - 1
            // Hidden stage reports and hidden remarks do not split the activity line.
            while (last >= 0 && ((!confirmStages && feed.get(last).kind === "stage" && feed.get(last).callId === "auto") ||
                                 (!showDetails && feed.get(last).kind === "assistant" && feed.get(last).callId === "details"))) last--
            var group = (last >= 0 && feed.get(last).kind === "steps") ? JSON.parse(feed.get(last).payload) : null
            if (!group) last = feed.count - 1
            if (!group) { group = { items: [] }; last = add("steps", "", { payload: "{}" }) }
            group.items.push({ label: beginnerLabels[ev.name] || toolLabels[ev.name] || ev.name, state: "run" })
            feed.setProperty(last, "payload", JSON.stringify(group))
            var t = {}
            for (var k in toolIndex) t[k] = toolIndex[k]
            t[ev.call_id] = [last, group.items.length - 1]
            toolIndex = t
            break
        case "tool_result":
            var at = toolIndex[ev.call_id]
            if (at !== undefined && at[0] < feed.count) {
                var g = JSON.parse(feed.get(at[0]).payload)
                if (g.items && g.items[at[1]]) {
                    g.items[at[1]].state = ev.ok ? "ok" : "fail"
                    feed.setProperty(at[0], "payload", JSON.stringify(g))
                }
            }
            refreshStatus()
            break
        case "progress":
            var p = JSON.stringify({ percent: ev.percent, failed: !!ev.failed })
            if (progressIndex < 0 || progressIndex >= feed.count) progressIndex = add("progress", ev.label, { payload: p })
            else { feed.setProperty(progressIndex, "text", ev.label); feed.setProperty(progressIndex, "payload", p) }
            if (ev.done) progressIndex = -1
            break
        case "file": add("file", ev.label, { extra: ev.url }); break
        case "questions":
            if (!ev.questions || !ev.questions.length) break
            thinking = false
            add("questions", ev.intro || "", { payload: JSON.stringify(ev), reqId: ev.request_id, state: ev.auto ? "done" : "" })
            break
        case "stage":
            thinking = false
            // callId "auto": a report made while stages are hidden; shown only with "Этапы работы: показывать".
            add("stage", "", { payload: JSON.stringify(ev), reqId: ev.request_id || "", state: ev.auto ? "done" : "",
                               callId: ev.auto ? "auto" : "" })
            break
        case "response":
            var r = findReq(ev.request_id)
            if (r >= 0) { feed.setProperty(r, "state", "done"); feed.setProperty(r, "extra", JSON.stringify(ev.response || {})) }
            break
        case "stopped":
            thinking = false
            for (var j = 0; j < feed.count; j++)
                if ((feed.get(j).kind === "questions" || feed.get(j).kind === "stage") && feed.get(j).state === "")
                    feed.setProperty(j, "state", "done")
            add("error", ev.text)
            break
        case "reset": clearFeed(); break
        }
    }

    function poll() {
        if (!currentChat) { loadTree(); return }
        var data = JSON.parse(AIChat.events(currentChat, lastEventId))
        if (data.missing_chat) { currentChat = ""; loadTree(); return }
        for (var i = 0; i < data.events.length; i++) {
            lastEventId = data.events[i].id
            try { handleEvent(data.events[i]) } catch (e) { console.log("AI chat event failed: " + e) }
        }
        busy = !!data.busy
        waiting = !!data.waiting
        resumable = !data.busy && !!data.resumable
        busyElsewhere = !!data.running_chat && data.running_chat !== currentChat
        // When the assistant starts working in another chat, show that chat.
        var running = data.running_chat || ""
        if (running && running !== lastRunningChat && running !== currentChat) { lastRunningChat = running; loadTree(); openChat(running) }
        lastRunningChat = running
    }
    property string lastRunningChat: ""

    function refreshStatus() {
        var s = JSON.parse(AIChat.status())
        if (s.error || !s.printer) { statusLine.text = s.error || s.message || "Принтер не выбран"; return }
        statusLine.text = "<b>" + esc(s.printer) + "</b> · " + (modeNames[s.printing_mode] || esc(s.printing_mode)) +
            " · " + esc(s.material || "—") + " · " + esc(s.quality || "—") + " · объектов: " + s.objects_on_plate
    }

    // ---------- chats ----------

    function nodeById(id) {
        for (var i = 0; i < treeNodes.length; i++) if (treeNodes[i].id === id) return treeNodes[i]
        return null
    }

    function pathOf(node) {
        var parts = []
        for (var n = node; n; n = n.parent ? nodeById(n.parent) : null) parts.unshift(n.name)
        return parts.join(" › ")
    }

    function loadTree() {
        var data = JSON.parse(AIChat.tree())
        treeNodes = data.nodes || []
        var chats = treeNodes.filter(function (n) { return n.type === "chat" })
        chats.sort(function (a, b) { return pathOf(a).localeCompare(pathOf(b)) })
        var items = chats.map(function (n) { return { id: n.id, label: (n.id === data.running_chat ? "⏳ " : "") + pathOf(n) } })
        if (!currentChat || !nodeById(currentChat)) {
            var saved = AIChat.rememberedChat()
            var latest = chats.slice().sort(function (a, b) { return b.updated - a.updated })[0]
            var pick = saved && nodeById(saved) ? saved : (latest ? latest.id : "")
            chatItems = items
            if (pick) openChat(pick)
        } else {
            chatItems = items
        }
        syncCombo()
    }
    property var chatItems: []

    function syncCombo() {
        for (var i = 0; i < chatItems.length; i++)
            if (chatItems[i].id === currentChat) { chatBox.currentIndex = i; return }
    }

    function clearFeed() {
        feed.clear()
        toolIndex = ({})
        progressIndex = -1
        thinking = false
    }

    function openChat(id) {
        if (!id || id === currentChat) return
        currentChat = id
        AIChat.rememberChat(id)
        lastEventId = -1
        clearFeed()
        syncCombo()
        poll()
    }

    function newChat() {
        if (busy) { toast("Идёт обработка запроса. Чтобы начать новый чат, сначала нажмите «Стоп»."); return }
        var chat = nodeById(currentChat)
        var res = JSON.parse(AIChat.create("chat", "", chat && chat.parent ? chat.parent : ""))
        if (res.error) { toast(res.error); return }
        currentChat = ""
        AIChat.rememberChat(res.node.id)
        loadTree()
    }

    // ---------- sending ----------

    function send() {
        var text = input.text
        if (busy || (!text.trim() && !pendingFiles.length)) return
        var err = AIChat.send(currentChat, text, pendingFiles)
        if (err) { toast(err); return }
        input.text = ""
        pendingFiles = []
        busy = true
        poll()
    }

    function addFiles(urls) {
        var list = pendingFiles.slice()
        for (var i = 0; i < urls.length; i++) {
            var u = urls[i].toString()
            if (list.indexOf(u) < 0) list.push(u)
        }
        pendingFiles = list
    }

    function fileName(url) { return decodeURIComponent(url.replace(/^.*[\/\\]/, "")) }

    function respond(index, response) {
        feed.setProperty(index, "state", "done")
        feed.setProperty(index, "extra", JSON.stringify(response))
        AIChat.respond(feed.get(index).reqId, JSON.stringify(response))
    }

    function openSettings() {
        var s = JSON.parse(AIChat.settings())
        fBase.text = s.base_url || ""
        fModel.text = s.model || ""
        fOut.text = s.output_dir || ""
        fRate.text = s.machine_rate_rub || ""
        fKey.text = ""
        fKey.placeholderText = s.api_key_set ? "сохранён (" + s.api_key_hint + ")" : "вставьте ключ"
        settingsPopup.open()
    }

    Timer { interval: 600; running: true; repeat: true; onTriggered: panel.poll() }
    Timer { interval: 5000; running: true; repeat: true; onTriggered: panel.loadTree() }

    ListModel { id: feed }

    // ---------- layout ----------

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: panel.gap
        spacing: panel.gap

        // Header: chat selector and controls
        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            Text {
                text: "AI Ассистент"
                font.pixelSize: panel.fs + 3
                font.bold: true
                color: "#1f2427"
            }
            ComboBox {
                id: chatBox
                Layout.fillWidth: true
                implicitHeight: 30
                font.pixelSize: panel.fs
                model: panel.chatItems
                textRole: "label"
                onActivated: panel.openChat(panel.chatItems[index].id)
            }
            AIButton {
                text: "Новый чат"
                hoverText: panel.busy ? "Идёт обработка…" : ""
                dangerOnHover: panel.busy
                fontSize: panel.fs
                onClicked: panel.newChat()
            }
            AIButton {
                text: "⋯"
                fontSize: panel.fs + 2
                onClicked: moreMenu.open()
                Menu {
                    id: moreMenu
                    y: parent.height
                    x: parent.width - width
                    width: 330
                    font.pixelSize: panel.fs
                    MenuItem { text: "Удалить этот чат"; enabled: panel.currentChat !== ""; onTriggered: deleteDialog.open() }
                    MenuItem { text: "Папка с готовыми файлами"; onTriggered: AIChat.openResultsFolder() }
                    MenuSeparator { }
                    MenuItem {
                        text: "Этапы работы: " + (panel.confirmStages ? "показывать" : "скрыты")
                        onTriggered: {
                            panel.confirmStages = !panel.confirmStages
                            AIChat.saveSettings(JSON.stringify({ confirm_stages: panel.confirmStages }))
                            panel.toast(panel.confirmStages ? "После каждого этапа ассистент покажет итог и подождёт «Продолжить»"
                                                            : "Ассистент задаст вопросы и сразу доведёт деталь до результата")
                        }
                    }
                    MenuItem {
                        text: "Технические подробности: " + (panel.showDetails ? "показывать" : "скрыты")
                        onTriggered: panel.showDetails = !panel.showDetails
                    }
                    MenuSeparator { }
                    MenuItem { text: "Настройки подключения к ИИ"; onTriggered: panel.openSettings() }
                    MenuItem { text: "Проекты и папки (отдельное окно)"; onTriggered: AIChat.openWindow() }
                }
            }
        }

        // Printer, mode and material: only for those who asked for technical details.
        Text {
            id: statusLine
            Layout.fillWidth: true
            visible: panel.showDetails
            textFormat: Text.StyledText
            wrapMode: Text.WordWrap
            font.pixelSize: panel.fs - 1
            color: "#55616b"
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: "#e3e8ec" }

        // Feed
        ListView {
            id: feedView
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 0  // the gap is part of each row, so hidden rows leave no gap
            model: feed
            ScrollBar.vertical: ScrollBar { }
            // positionViewAtEnd() scrolls into emptiness when the last rows are hidden (zero height),
            // so the view is moved by its content height instead.
            function scrollToEnd() {
                forceLayout()
                contentY = Math.max(originY, originY + contentHeight - height)
            }
            onCountChanged: Qt.callLater(scrollToEnd)

            delegate: Item {
                id: row
                width: feedView.width - 12
                property int rowIndex: index
                // Reports made while stages are hidden appear when "Этапы работы: показывать" is switched on.
                property bool shown: !(model.kind === "stage" && model.callId === "auto" && !panel.confirmStages) &&
                                     !(model.kind === "assistant" && model.callId === "details" && !panel.showDetails)
                height: shown && rowLoader.item ? rowLoader.item.height + 6 : 0
                visible: height > 0
                // Rows near the end (the last ones may be hidden) keep the newest content in view.
                onHeightChanged: if (rowIndex >= feed.count - 4) Qt.callLater(feedView.scrollToEnd)

              Loader {
                id: rowLoader
                width: parent.width
                active: row.shown
                sourceComponent: {
                    switch (model.kind) {
                    case "user": return userItem
                    case "assistant": return assistantItem
                    case "tool": return toolItem
                    case "steps": return stepsItem
                    case "progress": return progressItem
                    case "file": return fileItem
                    case "questions": return questionsItem
                    case "stage": return stageItem
                    default: return noteItem
                    }
                }

                // Item templates live inside the delegate so they can read its model roles.
    // ---------- feed items ----------

    Component {
        id: userItem
        Rectangle {
            radius: 8
            color: "#0277bd"
            height: userCol.implicitHeight + 14
            Column {
                id: userCol
                x: 10; y: 7
                width: parent.width - 20
                spacing: 3
                Text { width: parent.width; text: model.text; wrapMode: Text.Wrap; color: "white"; font.pixelSize: panel.fs }
                Text { width: parent.width; visible: model.extra !== ""; text: "Файлы: " + model.extra; wrapMode: Text.Wrap; color: "#d6ebf8"; font.pixelSize: panel.fs - 2 }
            }
        }
    }

    Component {
        id: assistantItem
        Rectangle {
            radius: 8
            color: "#f3f6f8"
            border.color: "#e1e7eb"
            height: aText.implicitHeight + 16
            Text {
                id: aText
                x: 10; y: 8
                width: parent.width - 20
                text: model.text
                textFormat: Text.RichText
                wrapMode: Text.Wrap
                font.pixelSize: panel.fs
                color: "#1f2427"
                onLinkActivated: AIChat.openLink(link)
                MouseArea { anchors.fill: parent; acceptedButtons: Qt.NoButton; cursorShape: parent.hoveredLink ? Qt.PointingHandCursor : Qt.ArrowCursor }
            }
        }
    }

    Component {
        id: noteItem
        Rectangle {
            radius: 6
            color: model.kind === "error" ? "#fdecea" : "#f1f8e9"
            border.color: model.kind === "error" ? "#f5c2bd" : "#dcedc8"
            height: nText.implicitHeight + 12
            Text {
                id: nText
                x: 8; y: 6
                width: parent.width - 16
                text: model.text
                wrapMode: Text.Wrap
                font.pixelSize: panel.fs - 1
                color: model.kind === "error" ? "#b71c1c" : "#33691e"
            }
        }
    }

    // Activity line: "⟳ Изучаю форму детали…" while working, "✓ Выполнено шагов: 5" afterwards.
    Component {
        id: stepsItem
        Column {
            id: steps
            property var g: JSON.parse(model.payload || "{}")
            property var items: g.items || []
            property bool open: panel.showDetails
            property var current: {
                for (var i = items.length - 1; i >= 0; i--) if (items[i].state === "run") return items[i]
                return null
            }
            property int failed: items.filter(function (s) { return s.state === "fail" }).length
            spacing: 2
            Text {
                width: parent.width
                wrapMode: Text.Wrap
                font.pixelSize: panel.fs - 1
                color: steps.current ? "#0277bd" : "#6b7780"
                text: (steps.current ? "⟳ " + steps.current.label + "…"
                                     : "✓ Выполнено шагов: " + steps.items.length + (steps.failed ? " (повторено: " + steps.failed + ")" : "")) +
                      "   " + (steps.open ? "▴ скрыть" : "▾ подробнее")
                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: steps.open = !steps.open }
            }
            Repeater {
                model: steps.open ? steps.items : []
                delegate: Text {
                    x: 14
                    width: steps.width - 14
                    wrapMode: Text.Wrap
                    font.pixelSize: panel.fs - 2
                    color: modelData.state === "fail" ? "#c62828" : "#8a959e"
                    text: (modelData.state === "ok" ? "✓ " : modelData.state === "fail" ? "✗ " : "• ") + modelData.label
                }
            }
        }
    }

    Component {
        id: toolItem
        Row {
            spacing: 6
            height: Math.max(tIcon.implicitHeight, tCol.implicitHeight)
            Text {
                id: tIcon
                width: 16
                text: model.state === "ok" ? "✓" : model.state === "fail" ? "✗" : "•"
                color: model.state === "ok" ? "#2e7d32" : model.state === "fail" ? "#c62828" : "#0277bd"
                font.pixelSize: panel.fs
                font.bold: true
            }
            Column {
                id: tCol
                width: parent.width - 22
                Text { width: parent.width; text: model.text; color: "#37474f"; font.pixelSize: panel.fs - 1; wrapMode: Text.Wrap }
                Text { width: parent.width; visible: model.extra !== ""; text: model.extra; color: "#8a959e"; font.pixelSize: panel.fs - 2; wrapMode: Text.Wrap }
            }
        }
    }

    Component {
        id: progressItem
        Column {
            spacing: 3
            property var p: JSON.parse(model.payload || "{}")
            Text { text: model.text + " — " + (p.failed ? "ошибка" : (p.percent || 0) + "%"); font.pixelSize: panel.fs - 1; color: p.failed ? "#c62828" : "#37474f" }
            Rectangle {
                width: parent.width; height: 6; radius: 3; color: "#e3e8ec"
                Rectangle { width: parent.width * Math.min(1, (parent.parent.p.percent || 0) / 100); height: parent.height; radius: 3; color: parent.parent.p.failed ? "#c62828" : "#0277bd" }
            }
        }
    }

    Component {
        id: fileItem
        Text {
            text: "<a href='" + panel.esc(model.extra) + "'>📄 " + panel.esc(model.text) + " — открыть</a>"
            textFormat: Text.RichText
            font.pixelSize: panel.fs
            onLinkActivated: AIChat.openLink(link)
            MouseArea { anchors.fill: parent; acceptedButtons: Qt.NoButton; cursorShape: Qt.PointingHandCursor }
        }
    }

    // Clarifying questions with answer options
    Component {
        id: questionsItem
        Rectangle {
            id: qCard
            property var d: JSON.parse(model.payload)
            property bool done: model.state === "done"
            property var given: model.extra ? JSON.parse(model.extra) : null
            property var sel: ({})
            property var custom: ({})
            property int cardIndex: row.rowIndex
            radius: 8
            color: "#fffdf5"
            border.color: done ? "#e6e1cc" : "#f0c36d"
            height: qCol.implicitHeight + 20

            function isOn(qid, option) {
                var src = (done && given && given.answers) ? (given.answers[qid] || {}).selected : sel[qid]
                return (src || []).indexOf(option) >= 0
            }
            function toggle(qid, option, multiple) {
                var copy = {}
                for (var k in sel) copy[k] = sel[k]
                var cur = (copy[qid] || []).slice()
                var at = cur.indexOf(option)
                if (at >= 0) cur.splice(at, 1)
                else cur = multiple ? cur.concat([option]) : [option]
                copy[qid] = cur
                sel = copy
            }
            function answers(skipAll) {
                var out = {}
                d.questions.forEach(function (q) { out[q.id] = skipAll ? {} : { selected: sel[q.id] || [], custom: custom[q.id] || "" } })
                return { answers: out }
            }

            Column {
                id: qCol
                x: 10; y: 10
                width: parent.width - 20
                spacing: 8
                // The intro phrase is the card's heading ("Несколько вопросов, чтобы подобрать материал…").
                Text { width: parent.width; visible: model.text !== ""; text: model.text; wrapMode: Text.Wrap; font.pixelSize: panel.fs; font.bold: true; color: "#5d4a00" }
                Repeater {
                    model: qCard.d.questions
                    delegate: Column {
                        property var q: modelData
                        width: qCol.width
                        spacing: 4
                        Text { width: parent.width; text: q.question; wrapMode: Text.Wrap; font.pixelSize: panel.fs; color: "#1f2427" }
                        Text {
                            width: parent.width
                            visible: !!q.why || (!!q.multiple && !qCard.done)
                            text: (q.why || "") + (q.multiple && !qCard.done ? (q.why ? " · " : "") + "можно выбрать несколько" : "")
                            wrapMode: Text.Wrap; font.pixelSize: panel.fs - 2; color: "#8a959e"
                        }
                        Flow {
                            width: parent.width
                            spacing: 4
                            Repeater {
                                model: (q.options || []).filter(function (o) { return !panel.systemDecides.test(o) })
                                delegate: AIButton {
                                    text: modelData
                                    fontSize: panel.fs - 1
                                    kind: qCard.isOn(q.id, modelData) ? "primary" : "normal"
                                    enabled: !qCard.done
                                    onClicked: qCard.toggle(q.id, modelData, !!q.multiple)
                                }
                            }
                        }
                        TextField {
                            width: parent.width
                            visible: !qCard.done  // after the answer the card shows only the chosen buttons
                            enabled: !qCard.done
                            font.pixelSize: panel.fs - 1
                            placeholderText: (q.options || []).length ? "Свой вариант" : "Ваш ответ"
                            text: (qCard.done && qCard.given && qCard.given.answers) ? ((qCard.given.answers[q.id] || {}).custom || "") : ""
                            onTextEdited: { var c = qCard.custom; c[q.id] = text; qCard.custom = c }
                        }
                    }
                }
                Text {
                    visible: !!qCard.d.auto
                    width: parent.width; wrapMode: Text.Wrap
                    text: "Автоматический режим: ассистент ответит на вопросы сам и назовёт допущения."
                    font.pixelSize: panel.fs - 2; color: "#8a959e"
                }
                Row {
                    visible: !qCard.done
                    spacing: 10
                    AIButton { text: "Готово"; kind: "primary"; fontSize: panel.fs; onClicked: panel.respond(qCard.cardIndex, qCard.answers(false)) }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "На пустые вопросы ассистент ответит сам"
                        font.pixelSize: panel.fs - 2; color: "#8a959e"
                    }
                }
                Text {
                    visible: qCard.done && !qCard.d.auto
                    text: "✓ Ответы отправлены"
                    font.pixelSize: panel.fs - 2; color: "#2e7d32"
                }
            }
        }
    }

    // Stage checkpoint: decisions with reasons, checks, one field for changes
    Component {
        id: stageItem
        Rectangle {
            id: sCard
            property var d: JSON.parse(model.payload)
            property bool done: model.state === "done"
            property var given: model.extra ? JSON.parse(model.extra) : null
            property int cardIndex: row.rowIndex
            radius: 8
            color: "#f7fbff"
            border.color: done ? "#dbe5ec" : "#90caf9"
            height: sCol.implicitHeight + 20

            function bodyHtml() {
                // Short lines for a beginner: "Материал: PLA — почему"; only warnings and problems are listed.
                var lines = []
                var decisions = Array.isArray(d.decisions) ? d.decisions : []
                decisions.forEach(function (x) {
                    var why = (x.why || "").trim()
                    lines.push("<b>" + panel.esc(x.what) + ":</b> " + panel.esc(x.value) +
                               (why ? " <font color='#6b7780'>— " + panel.esc(why) + "</font>" : ""))
                })
                var checks = Array.isArray(d.checks) ? d.checks : []
                var icon = { warning: "<font color='#ef6c00'>⚠</font>", problem: "<font color='#c62828'>✗</font>" }
                var issues = checks.filter(function (c) { return c.status !== "ok" })  // no status: show as a warning
                issues.forEach(function (c) {
                    lines.push((icon[c.status] || icon.warning) + " <b>" + panel.esc(c.name) + "</b>" + (c.detail ? " — " + panel.esc(c.detail) : ""))
                })
                if (checks.length && !issues.length)
                    lines.push("<font color='#2e7d32'>✓ Проверки пройдены</font>")
                return lines.join("<br>")
            }

            Column {
                id: sCol
                x: 10; y: 10
                width: parent.width - 20
                spacing: 8
                Text {
                    width: parent.width; wrapMode: Text.Wrap
                    text: (sCard.d.stage ? "Этап " + sCard.d.stage + " из 7 · " : "") + (sCard.d.title || "")
                    font.bold: true; font.pixelSize: panel.fs; color: "#01579b"
                }
                Text { width: parent.width; visible: !!sCard.d.plain_summary; text: sCard.d.plain_summary || ""; wrapMode: Text.Wrap; font.pixelSize: panel.fs - 1; color: "#37474f" }
                Text {
                    width: parent.width
                    property string body: sCard.bodyHtml()
                    visible: body !== ""
                    text: body
                    textFormat: Text.RichText
                    wrapMode: Text.Wrap
                    font.pixelSize: panel.fs - 1
                }
                RowLayout {
                    visible: !sCard.done
                    width: parent.width
                    spacing: 6
                    TextField {
                        id: change
                        Layout.fillWidth: true
                        font.pixelSize: panel.fs - 1
                        placeholderText: "Что изменить? Например: «возьми PETG». Если всё устраивает — «Продолжить»."
                        onAccepted: go.clicked()
                    }
                    AIButton {
                        id: go
                        kind: "primary"
                        fontSize: panel.fs
                        text: change.text.trim() ? "Отправить изменения" : "Продолжить"
                        onClicked: {
                            var comment = change.text.trim()
                            panel.respond(sCard.cardIndex, comment ? { action: "change", comment: comment } : { action: "continue" })
                        }
                    }
                }
                Text {
                    visible: sCard.done && !sCard.d.auto
                    width: parent.width; wrapMode: Text.Wrap
                    text: sCard.given && sCard.given.action === "change" ? "Попросили изменить: " + (sCard.given.comment || "") : "✓ Продолжаем"
                    font.pixelSize: panel.fs - 2; color: "#2e7d32"
                }
            }
        }
    }


              }  // rowLoader
            }  // row

            footer: Text {
                width: feedView.width
                height: panel.thinking || (panel.busy && !panel.waiting) ? implicitHeight + 10 : 0
                visible: height > 0
                topPadding: 8
                text: panel.busyElsewhere ? "Ассистент работает в другом чате…" : "Ассистент думает" + dots.text
                color: "#7a858e"
                font.pixelSize: panel.fs - 1
                font.italic: true
                Text { id: dots; visible: false; property int n: 0; text: ["", ".", "..", "..."][n % 4] }
                Timer { interval: 400; running: parent.visible; repeat: true; onTriggered: dots.n++ }
            }

            Text {
                anchors.centerIn: parent
                width: parent.width - 40
                visible: feed.count === 0
                horizontalAlignment: Text.AlignLeft
                wrapMode: Text.WordWrap
                color: "#5f6b74"
                font.pixelSize: panel.fs
                text: "Как напечатать деталь:\n\n1. Нажмите «+ Модель» и выберите файл детали (STL).\n    Можно добавить и фото.\n\n2. Напишите пару слов: что это и где будет стоять.\n    Например: «Держатель для телефона на стол».\n\n3. Ответьте на 2–3 вопроса — остальное ассистент сделает сам\n    и сохранит файл для принтера."
            }
        }

        // Attachments waiting to be sent
        Flow {
            Layout.fillWidth: true
            spacing: 4
            visible: panel.pendingFiles.length > 0
            Repeater {
                model: panel.pendingFiles
                delegate: Rectangle {
                    height: chipText.implicitHeight + 8
                    width: chipText.implicitWidth + 28
                    radius: 10
                    color: "#e7f1f8"
                    Text { id: chipText; x: 8; anchors.verticalCenter: parent.verticalCenter; text: panel.fileName(modelData); font.pixelSize: panel.fs - 1 }
                    Text {
                        anchors.right: parent.right; anchors.rightMargin: 7; anchors.verticalCenter: parent.verticalCenter
                        text: "✕"; color: "#7a858e"; font.pixelSize: panel.fs - 1
                        MouseArea {
                            anchors.fill: parent; anchors.margins: -4; cursorShape: Qt.PointingHandCursor
                            onClicked: { var l = panel.pendingFiles.slice(); l.splice(index, 1); panel.pendingFiles = l }
                        }
                    }
                }
            }
        }

        // Composer
        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            AIButton { text: "+ Модель"; fontSize: panel.fs; Layout.alignment: Qt.AlignBottom; onClicked: fileDialog.open() }
            ScrollView {
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(Math.max(input.implicitHeight, 34), 140)
                TextArea {
                    id: input
                    wrapMode: TextEdit.Wrap
                    font.pixelSize: panel.fs
                    placeholderText: "Что это за деталь и где она будет стоять?"
                    selectByMouse: true
                    background: Rectangle { border.color: input.activeFocus ? "#0277bd" : "#c9d1d7"; radius: 4 }
                    Keys.onReturnPressed: {
                        if (event.modifiers & Qt.ShiftModifier) { event.accepted = false; return }
                        event.accepted = true
                        panel.send()
                    }
                    Keys.onEnterPressed: { event.accepted = true; panel.send() }
                }
            }
            AIButton {
                Layout.alignment: Qt.AlignBottom
                kind: "primary"
                fontSize: panel.fs
                enabled: !panel.busy
                visible: !panel.busy && !panel.resumable
                text: "Отправить"
                onClicked: panel.send()
            }
            // While working: "Стоп"; after a stop: "Продолжить" (from the same place).
            AIButton {
                Layout.alignment: Qt.AlignBottom
                visible: panel.busy || panel.resumable
                text: panel.busy ? "Стоп" : "Продолжить"
                kind: panel.busy ? "danger" : "success"
                fontSize: panel.fs
                onClicked: {
                    if (panel.busy) { AIChat.stop(); panel.thinking = false; panel.busy = false; panel.resumable = true }
                    else { AIChat.resume(panel.currentChat); panel.busy = true; panel.resumable = false }
                }
            }
        }
    }

    DropArea {
        anchors.fill: parent
        onDropped: { if (drop.hasUrls) { panel.addFiles(drop.urls); drop.accept() } }
    }

    FileDialog {
        id: fileDialog
        title: "Файлы для ассистента"
        selectMultiple: true
        nameFilters: ["Модели и фото (*.stl *.obj *.3mf *.png *.jpg *.jpeg *.docx)", "Все файлы (*)"]
        onAccepted: panel.addFiles(fileUrls)
    }

    // ---------- deleting a chat ----------

    function deleteCurrentChat() {
        var res = JSON.parse(AIChat.remove(currentChat))
        if (res.error) { toast(res.error); return }
        currentChat = ""
        AIChat.rememberChat("")
        clearFeed()
        loadTree()
        toast("Чат удалён")
    }

    Popup {
        id: deleteDialog
        modal: true
        focus: true
        x: panel.gap
        y: 60
        width: panel.width - 2 * panel.gap
        padding: 14
        background: Rectangle { color: "white"; border.color: "#c9d1d7"; radius: 6 }
        Column {
            width: parent.width
            spacing: 10
            Text {
                width: parent.width
                wrapMode: Text.Wrap
                font.pixelSize: panel.fs
                text: "Удалить чат «" + (panel.nodeById(panel.currentChat) || { name: "" }).name + "»? Восстановить его будет нельзя."
            }
            Row {
                spacing: 6
                AIButton { text: "Удалить"; kind: "danger"; fontSize: panel.fs; onClicked: { deleteDialog.close(); panel.deleteCurrentChat() } }
                AIButton { text: "Отмена"; fontSize: panel.fs; onClicked: deleteDialog.close() }
            }
        }
    }

    // ---------- settings ----------

    Popup {
        id: settingsPopup
        modal: true
        focus: true
        x: panel.gap
        y: 60
        width: panel.width - 2 * panel.gap
        padding: 14
        background: Rectangle { color: "white"; border.color: "#c9d1d7"; radius: 6 }
        ColumnLayout {
            anchors.fill: parent
            spacing: 6
            Text { text: "Настройки LLM"; font.bold: true; font.pixelSize: panel.fs + 1 }
            Text { text: "Base URL (OpenAI-совместимый API)"; font.pixelSize: panel.fs - 1; color: "#55616b" }
            TextField { id: fBase; Layout.fillWidth: true; font.pixelSize: panel.fs; selectByMouse: true }
            Text { text: "API key (хранится в настройках слайсера)"; font.pixelSize: panel.fs - 1; color: "#55616b" }
            TextField { id: fKey; Layout.fillWidth: true; font.pixelSize: panel.fs; echoMode: TextInput.Password; selectByMouse: true }
            Text { text: "Модель"; font.pixelSize: panel.fs - 1; color: "#55616b" }
            TextField { id: fModel; Layout.fillWidth: true; font.pixelSize: panel.fs; selectByMouse: true }
            Text { text: "Папка результатов"; font.pixelSize: panel.fs - 1; color: "#55616b" }
            TextField { id: fOut; Layout.fillWidth: true; font.pixelSize: panel.fs; selectByMouse: true }
            Text { text: "Стоимость машино-часа, ₽"; font.pixelSize: panel.fs - 1; color: "#55616b" }
            TextField { id: fRate; Layout.fillWidth: true; font.pixelSize: panel.fs; selectByMouse: true }
            Row {
                spacing: 6
                AIButton {
                    text: "Сохранить"; kind: "primary"; fontSize: panel.fs
                    onClicked: {
                        AIChat.saveSettings(JSON.stringify({ base_url: fBase.text, api_key: fKey.text, model: fModel.text,
                                                             output_dir: fOut.text, machine_rate_rub: fRate.text }))
                        settingsPopup.close()
                        panel.toast("Настройки сохранены")
                    }
                }
                AIButton { text: "Отмена"; fontSize: panel.fs; onClicked: settingsPopup.close() }
            }
        }
    }

    Rectangle {
        id: toastBox
        visible: false
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 70
        width: Math.min(parent.width - 30, toastText.implicitWidth + 24)
        height: toastText.implicitHeight + 14
        radius: 6
        color: "#323a40"
        Text { id: toastText; anchors.centerIn: parent; width: parent.width - 24; wrapMode: Text.Wrap; color: "white"; font.pixelSize: panel.fs - 1 }
        Timer { id: toastTimer; interval: 3500; onTriggered: toastBox.visible = false }
    }
}
