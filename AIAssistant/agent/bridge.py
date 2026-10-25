# Bridge between the chat panel inside the slicer (qml/AIChatPanel.qml) and the agent.
# The panel calls these slots on the Qt main thread; complex values travel as JSON strings.

import json
import os

from PyQt5.QtCore import QObject, QUrl, pyqtSlot
from PyQt5.QtGui import QDesktopServices

from UM.Logger import Logger

from .server import attachment_from_file


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)


class ChatBridge(QObject):
    # `core` is the AIAssistant extension; the agent is created by it on first use.
    def __init__(self, core, parent=None):
        super().__init__(parent)
        self._core = core

    @property
    def _agent(self):
        return self._core.started().agent

    @property
    def _settings(self):
        return self._core.started().settings

    @property
    def _tools(self):
        return self._core.started().tools

    @property
    def _workspace(self):
        return self._core.started().workspace

    # ---- feed ----

    @pyqtSlot(str, int, result=str)
    def events(self, chat_id, after):
        try:
            return _dump(self._agent.eventsAfter(chat_id, after))
        except ValueError as e:
            return _dump({"error": str(e), "missing_chat": True})

    @pyqtSlot(result=str)
    def status(self):
        try:
            return _dump(self._tools.call("get_slicer_status", {}))
        except Exception as e:
            return _dump({"error": str(e)})

    # ---- requests ----

    @pyqtSlot(str, str, "QVariantList", result=str)
    def send(self, chat_id, text, file_urls):
        try:
            attachments = []
            for url in file_urls or []:
                path = QUrl(str(url)).toLocalFile() or str(url)
                attachments.append(attachment_from_file(path))
            if not self._agent.submit(chat_id, text, attachments):
                return "Ассистент ещё занят предыдущим запросом."
            return ""
        except Exception as e:
            Logger.logException("e", "AI Assistant: sending from the panel failed")
            return "Не удалось отправить: %s" % e

    @pyqtSlot(str, str, result=bool)
    def respond(self, request_id, response_json):
        return bool(self._agent.interaction.respond(request_id, json.loads(response_json or "{}")))

    @pyqtSlot(result=bool)
    def stop(self):
        return bool(self._agent.stop())

    @pyqtSlot(str, result=bool)
    def resume(self, chat_id):
        return bool(self._agent.resume(chat_id))

    # ---- projects: folders and chats ----

    @pyqtSlot(result=str)
    def tree(self):
        return _dump({"nodes": self._workspace.tree(), "running_chat": self._agent.runningChat()})

    @pyqtSlot(str, str, str, result=str)
    def create(self, node_type, name, parent):
        try:
            return _dump({"node": self._workspace.create(node_type, name or None, parent or None)})
        except Exception as e:
            return _dump({"error": str(e)})

    @pyqtSlot(str, str, result=str)
    def rename(self, node_id, name):
        try:
            return _dump({"node": self._workspace.rename(node_id, name)})
        except Exception as e:
            return _dump({"error": str(e)})

    @pyqtSlot(str, result=str)
    def remove(self, node_id):
        try:
            running = self._agent.runningChat()
            if running and running in self._workspace.descendants(node_id):
                return _dump({"error": "Нельзя удалить: в этом чате сейчас работает ассистент. Сначала нажмите «Стоп»."})
            removed = self._workspace.delete(node_id)
            self._agent.forget(removed)
            return _dump({"removed": removed})
        except Exception as e:
            return _dump({"error": str(e)})

    # ---- settings ----

    @pyqtSlot(result=str)
    def settings(self):
        return _dump(self._settings.publicView())

    @pyqtSlot(str, result=str)
    def saveSettings(self, data_json):
        data = json.loads(data_json or "{}")
        for key in ("base_url", "api_key", "model", "output_dir", "machine_rate_rub"):
            if data.get(key):
                self._settings.set(key, data[key])
        for flag in ("interactive", "confirm_stages"):
            if flag in data:
                self._settings.set(flag, "1" if data[flag] else "0")
        return _dump(self._settings.publicView())

    # ---- links to results ----

    @pyqtSlot(str)
    def openLink(self, link):
        if link.startswith("/files/"):
            base = os.path.normpath(self._settings.get("output_dir"))
            path = os.path.normpath(os.path.join(base, QUrl.fromPercentEncoding(link[len("/files/"):].encode("utf-8"))))
            if not path.startswith(base + os.sep) or not os.path.exists(path):
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        elif link.startswith("http://") or link.startswith("https://"):
            QDesktopServices.openUrl(QUrl(link))

    # The panel is recreated each time the tab is opened; the open chat is remembered here.
    _remembered_chat = ""

    @pyqtSlot(str)
    def rememberChat(self, chat_id):
        ChatBridge._remembered_chat = chat_id

    @pyqtSlot(result=str)
    def rememberedChat(self):
        return ChatBridge._remembered_chat

    @pyqtSlot()
    def openWindow(self):
        self._core.showChat()

    @pyqtSlot()
    def openResultsFolder(self):
        folder = self._settings.get("output_dir")
        if os.path.isdir(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
