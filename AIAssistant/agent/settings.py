# LLM connection settings, stored in the slicer's own preferences file
# (never in the source code).

import os

from PyQt5.QtCore import QStandardPaths

_PREFIX = "ai_assistant/"
_DEFAULTS = {
    "base_url": "",
    "api_key": "",
    "model": "",
    "output_dir": os.path.join(QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation), "STE_AI_results"),
    "machine_rate_rub": "300",
    "interactive": "1",  # "1": questions and stage confirmations, "0": automatic mode
}


class PluginSettings:
    def __init__(self, preferences):
        self._prefs = preferences
        for key, value in _DEFAULTS.items():
            self._prefs.addPreference(_PREFIX + key, value)

    def get(self, key):
        value = self._prefs.getValue(_PREFIX + key)
        return value if value is not None else _DEFAULTS.get(key, "")

    def set(self, key, value):
        if key in _DEFAULTS:
            self._prefs.setValue(_PREFIX + key, str(value).strip())

    def isConfigured(self):
        return bool(self.get("base_url") and self.get("api_key") and self.get("model"))

    def publicView(self):
        # The key is never sent back to the browser in full.
        key = self.get("api_key")
        return {
            "base_url": self.get("base_url"),
            "model": self.get("model"),
            "output_dir": self.get("output_dir"),
            "machine_rate_rub": self.get("machine_rate_rub"),
            "interactive": self.get("interactive") != "0",
            "api_key_set": bool(key),
            "api_key_hint": ("..." + key[-4:]) if len(key) > 4 else "",
        }
