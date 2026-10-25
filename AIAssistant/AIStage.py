# "AI Ассистент" tab in the slicer's top bar: the chat panel takes the place of the settings sidebar.

import os

from UM.Application import Application
from UM.Logger import Logger

from steslicer.Stages.SteSlicerStage import SteSlicerStage

from .agent.bridge import ChatBridge

PANEL_QML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qml", "AIChatPanel.qml")


class AIStage(SteSlicerStage):
    def __init__(self, core, parent=None):
        super().__init__(parent)
        self._bridge = ChatBridge(core)
        Application.getInstance().engineCreatedSignal.connect(self._engineCreated)

    def _engineCreated(self):
        try:
            engine = Application.getInstance()._qml_engine
            engine.rootContext().setContextProperty("AIChat", self._bridge)
        except Exception:
            Logger.logException("e", "AI Assistant: cannot expose the chat bridge to QML")
        self.addDisplayComponent("sidebar", PANEL_QML)
        # The slicer opens on the assistant tab: a beginner starts from the chat.
        Application.getInstance().callLater(self._activate)

    def _activate(self):
        try:
            Application.getInstance().getController().setActiveStage(self.getPluginId())
        except Exception:
            Logger.logException("w", "AI Assistant: cannot open the assistant tab at start")
