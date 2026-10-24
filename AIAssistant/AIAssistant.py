# AI Assistant for STE Slicer.
# Python 3.5 compatible: no f-strings, no variable annotations.

import os
import sys

from UM.Extension import Extension
from UM.Logger import Logger

from steslicer.SteSlicerApplication import SteSlicerApplication

from .agent.settings import PluginSettings
from .agent.tools import ToolRegistry
from .agent.agent import Agent
from .agent.server import start_server
from .agent.workspace import Workspace
from .agent import appwindow

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(PLUGIN_DIR, "web")


class AIAssistant(Extension):
    def __init__(self):
        super().__init__()
        self.setMenuName("AI Assistant")
        self.addMenuItem("Открыть чат", self.showChat)
        self._server = None
        self._settings = None
        self._agent = None
        # The chat server starts together with the slicer, so the "AI Assistant" launcher can find it.
        SteSlicerApplication.getInstance().callLater(self._startSafely)

    def _startSafely(self):
        try:
            self._ensureStarted()
        except Exception:
            Logger.logException("e", "AI Assistant: failed to start the chat server")

    def _ensureStarted(self):
        if self._server is not None:
            return
        app = SteSlicerApplication.getInstance()
        self._settings = PluginSettings(app.getPreferences())
        workspace = Workspace(self._settings)
        tools = ToolRegistry(app, self._settings)
        self._agent = Agent(self._settings, tools, workspace)
        tools.setEventSink(self._agent.emit)
        self._server = start_server(WEB_DIR, self._agent, self._settings, tools, workspace)
        appwindow.write_port_file(self._server.server_port, os.path.abspath(sys.executable))
        Logger.log("i", "AI Assistant: server started on port %d", self._server.server_port)

    def showChat(self):
        try:
            self._ensureStarted()
            how = appwindow.open_window("http://127.0.0.1:%d/" % self._server.server_port)
            Logger.log("i", "AI Assistant: chat window opened with %s", how)
        except Exception:
            Logger.logException("e", "AI Assistant: failed to open chat")
