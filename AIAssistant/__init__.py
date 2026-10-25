from . import AIAssistant
from . import AIStage


def getMetaData():
    return {
        "stage": {
            "name": "AI Ассистент",
            "weight": 5
        }
    }


def register(app):
    core = AIAssistant.AIAssistant()
    return {"extension": core, "stage": AIStage.AIStage(core)}
