# Slicer tools. The same registry is exposed to the LLM (as OpenAI "tools")
# and through the MCP endpoint (tools/list, tools/call).
#
# Coordinates: tools speak printer coordinates (X right, Y depth, Z up, mm).
# The Uranium scene is Y-up, so: printer X = scene X, printer Y = -scene Z, printer Z = scene Y.
#
# Handlers marked main=True run entirely in the Qt main thread.
# Long handlers (main=False) run in the caller's thread and touch the slicer only via run_on_main.

import math
import os
import re
import time

from PyQt5.QtCore import QUrl

from UM.Logger import Logger
from UM.Math.Quaternion import Quaternion
from UM.Math.Vector import Vector
from UM.Mesh.MeshWriter import MeshWriter
from UM.Operations.GroupedOperation import GroupedOperation
from UM.Operations.RemoveSceneNodeOperation import RemoveSceneNodeOperation
from UM.Operations.RotateOperation import RotateOperation
from UM.Operations.TranslateOperation import TranslateOperation
from UM.Scene.Iterator.DepthFirstIterator import DepthFirstIterator

from steslicer.Settings.ExtruderManager import ExtruderManager

from .mainthread import run_on_main
from .geometry import analyze_node, describe_rotation
from . import materials
from . import production

PRINTING_MODES = {
    "classic": "3D (классическая послойная печать)",
    "cylindrical": "5D Spiral (печать спиралью вокруг оси вращения)",
    "cylindrical_full": "5D Spiral Full",
}

# Backend states from UM.Backend.Backend.BackendState
_BACKEND_STATES = {1: "not_started", 2: "processing", 3: "done", 4: "error", 5: "disabled"}

# Settings shown in get_slicer_status, so the agent rarely needs to read them one by one.
KEY_SETTINGS = ["layer_height", "layer_height_0", "line_width", "wall_line_count", "wall_thickness",
                "top_bottom_thickness", "infill_sparse_density", "infill_pattern", "support_enable",
                "support_type", "adhesion_type", "speed_print", "cool_fan_enabled", "cool_fan_speed",
                "material_print_temperature", "material_bed_temperature", "retraction_enable", "xy_offset",
                "z_seam_type", "ironing_enabled"]
KEY_SETTINGS_5D = ["cylindrical_mode_base_diameter", "cylindrical_mode_overlap", "cylindrical_layer_height",
                   "cylindrical_layer_height_0", "reinforcement_enabled"]
EXTRUDER_KEYS = {"material_print_temperature", "cool_fan_enabled", "cool_fan_speed", "retraction_enable"}

SLICE_SETTLE_S = 0.7          # quiet time after the settings check before starting a slice
SLICE_SETTLE_MAX_S = 20.0     # never wait longer than this for the settings check
# Safety net for 3D only: 5D preparation reports "not started" for minutes while it legitimately works, and a
# restart there would start a second parallel preparation. In 3D a cancelled slice is restarted once.
SLICE_RESTART_AFTER_S = 20.0
SLICE_MAX_RESTARTS = 1

OBJECT_ARG = {"type": "integer", "description": "id объекта из list_objects. Если не указан и объект один — берётся он."}


class ToolRegistry:
    def __init__(self, app, settings):
        self._app = app
        self._settings = settings
        self._tools = {}
        self._event_sink = None
        self._slice_state = "not_started"
        self._slice_progress = 0.0
        self._backend_connected = False
        self._aborted = False
        self._last_results_name = None
        self._registerAll()

    def setEventSink(self, sink):
        # sink(kind, **data) — used to stream slicing progress to the chat.
        self._event_sink = sink

    def abort(self):
        # Called by "Stop": long tools (slicing) check this flag and end early.
        self._aborted = True

    def resetAbort(self):
        self._aborted = False

    # ================= registry =================

    def _register(self, name, description, properties, handler, required=None, main=True):
        schema = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        self._tools[name] = {"name": name, "description": description, "inputSchema": schema,
                             "handler": handler, "main": main}

    def mcpList(self):
        return [{"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
                for t in self._tools.values()]

    def openaiList(self):
        return [{"type": "function",
                 "function": {"name": t["name"], "description": t["description"],
                              "parameters": t["inputSchema"]}}
                for t in self._tools.values()]

    def call(self, name, arguments):
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError("Неизвестный инструмент: %s" % name)
        Logger.log("d", "AI Assistant tool call: %s %s", name, arguments)
        if tool["main"]:
            return run_on_main(tool["handler"], arguments or {})
        return tool["handler"](arguments or {})

    def _registerAll(self):
        # ---- state, settings, profiles ----
        self._register("get_slicer_status",
                       "Текущее состояние: принтер, режим печати, материал, профиль качества, объекты на столе, состояние слайсинга.",
                       {}, self._getSlicerStatus)
        self._register("get_settings",
                       "Прочитать сразу несколько настроек печати по ключам одним вызовом. "
                       "Если не уверен в ключе — сначала find_settings.",
                       {"keys": {"type": "array", "items": {"type": "string"},
                                 "description": "Например [\"layer_height\", \"wall_line_count\", \"cool_fan_speed\"]"},
                        "scope": {"type": "string", "enum": ["auto", "global", "extruder"],
                                  "description": "auto (по умолчанию): там, откуда значение берёт слайсер"}},
                       self._getSettings, ["keys"])
        self._register("find_settings",
                       "Найти настройки по слову в ключе, названии или описании (англ.: fan, shrink, offset, seam, "
                       "retraction, brim...). Возвращает ключи, названия и текущие значения.",
                       {"query": {"type": "string"}},
                       self._findSettings, ["query"])
        self._register("set_settings",
                       "Изменить одну или несколько настроек печати. Возвращает старые и новые значения. "
                       "Куда записать (общие настройки или экструдер) определяется автоматически.",
                       {"settings": {"type": "object", "description": "Словарь ключ → значение, например {\"layer_height\": 0.2}"},
                        "scope": {"type": "string", "enum": ["auto", "global", "extruder"]}},
                       self._setSettings, ["settings"])
        self._register("set_printing_mode",
                       "Переключить режим печати: classic (3D), cylindrical (5D Spiral), cylindrical_full (5D Spiral Full).",
                       {"mode": {"type": "string", "enum": list(PRINTING_MODES.keys())}},
                       self._setPrintingMode, ["mode"])
        self._register("list_materials",
                       "Список материалов, доступных для активного принтера и экструдера: id, название, тип, производитель.",
                       {"extruder": {"type": "integer", "description": "Номер экструдера, по умолчанию 0"}},
                       self._listMaterials)
        self._register("select_material",
                       "Подобрать материал по требованиям ТЗ: проверка всех доступных материалов по базе свойств. "
                       "Возвращает рекомендованный, подходящие (по убыванию оценки) и исключённые с причинами. "
                       "Указывай только то, что следует из ТЗ.",
                       materials.CONSTRAINT_SCHEMA, self._selectMaterial)
        self._register("material_info",
                       "Свойства материала из базы с источниками и рекомендуемое технологическое окно (температуры "
                       "сопла и стола, обдув, закрытая камера, сушка, закалённое сопло).",
                       {"material_id": {"type": "string", "description": "По умолчанию — текущий материал"}},
                       self._materialInfo)
        self._register("set_material",
                       "Выбрать материал по его id из list_materials.",
                       {"material_id": {"type": "string"},
                        "extruder": {"type": "integer", "description": "Номер экструдера, по умолчанию 0"}},
                       self._setMaterial, ["material_id"])
        self._register("list_quality_profiles",
                       "Список профилей качества для текущего принтера и материала: quality_type, название, доступность, высота слоя.",
                       {}, self._listQualityProfiles)
        self._register("set_quality_profile",
                       "Выбрать профиль качества по quality_type из list_quality_profiles.",
                       {"quality_type": {"type": "string"}},
                       self._setQualityProfile, ["quality_type"])
        self._register("save_custom_profile",
                       "Сохранить текущие изменённые настройки как пользовательский профиль с заданным именем.",
                       {"name": {"type": "string"}},
                       self._saveCustomProfile, ["name"])

        # ---- models and scene ----
        self._register("reset_settings",
                       "Сбросить все изменённые настройки к профилю (материал и профиль качества сохраняются). "
                       "Вызывать в начале работы над новой деталью, чтобы не унаследовать чужие изменения.",
                       {}, self._resetSettings)
        self._register("clear_plate", "Удалить все модели со стола.", {}, self._clearPlate)
        self._register("load_model",
                       "Загрузить 3D-модель (STL/OBJ/3MF) с диска на стол. Возвращает список объектов после загрузки.",
                       {"path": {"type": "string", "description": "Полный путь к файлу"}},
                       self._loadModel, ["path"], main=False)
        self._register("list_objects",
                       "Объекты на столе: id, имя, положение центра и габариты (мм, координаты принтера, Z вверх).",
                       {}, self._listObjects)
        self._register("analyze_model",
                       "Анализ геометрии объекта: габариты, объём, площадь нависаний, площадь опоры на стол, "
                       "оценка оси вращательной симметрии и центрального отверстия, рекомендации по ориентации.",
                       {"object_id": OBJECT_ARG}, self._analyzeModel)
        self._register("rotate_object",
                       "Повернуть объект на углы (градусы) вокруг осей принтера X, Y, Z (Z — вертикаль). "
                       "После поворота объект ставится на стол.",
                       {"object_id": OBJECT_ARG,
                        "x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}},
                       self._rotateObject)
        self._register("move_object",
                       "Переместить центр объекта в точку (мм, координаты принтера; 0,0 — центр стола и ось вращения 5D). "
                       "Высота не меняется: объект остаётся на столе.",
                       {"object_id": OBJECT_ARG, "x": {"type": "number"}, "y": {"type": "number"}},
                       self._moveObject, ["x", "y"])
        self._register("scale_object",
                       "Масштабировать объект равномерно (1.0 — без изменений).",
                       {"object_id": OBJECT_ARG, "factor": {"type": "number"}},
                       self._scaleObject, ["factor"])
        self._register("setup_5d",
                       "Подготовить деталь-тело вращения к 5D одним вызовом: ось вращения вертикально, центр на оси "
                       "5D (0,0), режим и диаметр базы под центральное отверстие (без зазора между базой и деталью). "
                       "Вызывать вместо ручных rotate/move/set_printing_mode для 5D.",
                       {"object_id": OBJECT_ARG}, self._setup5d, main=False)
        self._register("lay_on_plate", "Опустить объект так, чтобы он стоял на столе.",
                       {"object_id": OBJECT_ARG}, self._layOnPlate)
        self._register("arrange_all", "Автоматически расставить все объекты на столе.", {}, self._arrangeAll)
        self._register("delete_object", "Удалить объект со стола.", {"object_id": OBJECT_ARG},
                       self._deleteObject)

        # ---- slicing and export ----
        self._register("slice",
                       "Запустить слайсинг и дождаться результата. Возвращает время печати и расход материала.",
                       {"timeout_s": {"type": "integer", "description": "Максимум ожидания, по умолчанию 900"}},
                       self._slice, main=False)
        self._register("get_slice_status", "Состояние и прогресс слайсинга.", {}, self._getSliceStatus)
        self._register("get_print_info", "Результат последнего слайсинга: время печати, длина и масса материала.",
                       {}, self._getPrintInfo)
        self._register("export_gcode", "Сохранить G-code последнего слайсинга в файл.",
                       {"path": {"type": "string"}}, self._exportGcode, ["path"], main=False)
        self._register("save_project", "Сохранить проект STE Slicer (3MF со всеми настройками) в файл.",
                       {"path": {"type": "string"}}, self._saveProject, ["path"], main=False)
        self._register("save_results",
                       "Сохранить итог для сдачи: <папка результатов>/results/<model_name>/project.3mf и result.gcode. "
                       "Вызывать после успешного slice.",
                       {"model_name": {"type": "string", "description": "Имя детали, например «Шестерня»"}},
                       self._saveResults, ["model_name"], main=False)

        # ---- production features ----
        self._register("check_process",
                       "Проверка технологических параметров процесса для текущего материала: температура сопла и "
                       "стола, обдув, скорость, ретракт, высота слоя и ширина линии относительно сопла, первый слой, "
                       "закрытая камера, сушка, закалённое сопло. Сравнивает с профилем производителя (Stereotech) и "
                       "технологическим окном семейства (Prusa). Возвращает проверки и рекомендуемые значения.",
                       {}, self._checkProcess)
        self._register("preflight_check",
                       "Предполётная проверка перед печатью: помещается ли деталь, нужны ли поддержки, риск отрыва "
                       "от стола и коробления, мелкие элементы, корректность размещения для 5D. Возвращает список "
                       "проблем с уровнем (ошибка/предупреждение/инфо) и советом.",
                       {"object_id": OBJECT_ARG}, self._preflightCheck)
        self._register("compare_variants",
                       "Нарезать несколько вариантов настроек и сравнить время, массу и стоимость. Каждый вариант — "
                       "название и словарь изменений настроек поверх текущих. После сравнения восстанавливается "
                       "исходное состояние.",
                       {"variants": {"type": "array", "items": {
                           "type": "object",
                           "properties": {"name": {"type": "string"},
                                          "settings": {"type": "object"}},
                           "required": ["name"]}}},
                       self._compareVariants, ["variants"], main=False)
        self._register("make_tech_card",
                       "Создать технологическую карту детали (HTML) в папке результата: оборудование, материал, "
                       "требования ТЗ → решения, изменённые настройки, ориентация, время, масса, стоимость, риски.",
                       {"model_name": {"type": "string"},
                        "summary": {"type": "string", "description": "Назначение детали и условия работы, 1–3 предложения"},
                        "requirements": {"type": "array", "items": {
                            "type": "object",
                            "properties": {"requirement": {"type": "string"}, "decision": {"type": "string"}}}},
                        "orientation_note": {"type": "string"},
                        "risks": {"type": "array", "items": {"type": "string"}}},
                       self._makeTechCard, ["model_name"], main=False)
        self._register("save_recipe",
                       "Сохранить текущую настройку (режим, материал, профиль, изменённые настройки) как рецепт.",
                       {"name": {"type": "string"}, "description": {"type": "string"}},
                       self._saveRecipe, ["name"])
        self._register("list_recipes", "Список сохранённых рецептов.", {}, self._listRecipes)
        self._register("apply_recipe",
                       "Применить сохранённый рецепт. Можно заменить материал (material_id) — тогда настройки рецепта "
                       "применяются поверх профиля нового материала.",
                       {"name": {"type": "string"}, "material_id": {"type": "string"}},
                       self._applyRecipe, ["name"])

    # ================= helpers (main thread only) =================

    def _stack(self, scope="global"):
        if scope == "extruder":
            stack = ExtruderManager.getInstance().getActiveExtruderStack()
        else:
            stack = self._app.getGlobalContainerStack()
        if stack is None:
            raise RuntimeError("Принтер не выбран")
        return stack

    def _sliceableNodes(self):
        root = self._app.getController().getScene().getRoot()
        nodes = []
        for node in DepthFirstIterator(root):
            if node.callDecoration("isSliceable") and node.getMeshData() is not None:
                parent = node.getParent()
                if parent is not None and parent.callDecoration("isGroup"):
                    continue
                nodes.append(node)
        return nodes

    def _node(self, args):
        nodes = self._sliceableNodes()
        object_id = args.get("object_id")
        if object_id is None:
            if len(nodes) == 1:
                return nodes[0]
            raise ValueError("Укажите object_id: на столе %d объектов" % len(nodes))
        for node in nodes:
            if id(node) == int(object_id):
                return node
        raise ValueError("Нет объекта с id %s, вызовите list_objects" % object_id)

    def _describeNode(self, node):
        box = node.getBoundingBox()
        center = box.center
        return {
            "id": id(node),
            "name": node.getName(),
            "center_mm": {"x": round(center.x, 2), "y": round(-center.z, 2), "z": round(center.y, 2)},
            "size_mm": {"x": round(box.width, 2), "y": round(box.depth, 2), "z": round(box.height, 2)},
            "bottom_z_mm": round(box.bottom, 3),
        }

    def _dropToPlate(self, node):
        box = node.getBoundingBox()
        if box is not None and abs(box.bottom) > 1e-4:
            TranslateOperation(node, Vector(0, -box.bottom, 0)).push()

    def _emit(self, kind, **data):
        if self._event_sink is not None:
            self._event_sink(kind, **data)

    # ================= state, settings, profiles =================

    def _getSlicerStatus(self, args):
        stack = self._app.getGlobalContainerStack()
        if stack is None:
            return {"printer": None, "message": "Принтер не добавлен"}
        self._connectBackend()
        extruder = self._mainExtruder()
        mode = stack.getProperty("printing_mode", "value")
        return {
            "printer": stack.getName(),
            "printer_definition": stack.definition.getId(),
            "printing_mode": mode,
            "printing_mode_description": PRINTING_MODES.get(mode, mode),
            "material": extruder.material.getName() if extruder is not None else None,
            "material_id": extruder.material.getMetaDataEntry("base_file") if extruder is not None else None,
            "quality": stack.quality.getName(),
            "quality_type": stack.quality.getMetaDataEntry("quality_type"),
            "key_settings": self._keySettings(stack, extruder, mode),
            "changed_from_profile": self._userChanges(),
            "objects_on_plate": len(self._sliceableNodes()),
            "slice_state": self._slice_state,
        }

    def _keySettings(self, stack, extruder, mode):
        keys = list(KEY_SETTINGS)
        if mode in ("cylindrical", "cylindrical_full"):
            keys += KEY_SETTINGS_5D
        values = {}
        for key in keys:
            source = self._targetStack(key, "auto")
            if source.hasProperty(key, "value"):
                values[key] = source.getProperty(key, "value")
        return values

    def _mainExtruder(self):
        stack = self._stack()
        return stack.extruders.get("0") or ExtruderManager.getInstance().getActiveExtruderStack()

    def _targetStack(self, key, scope="auto"):
        # Like the slicer UI: per-extruder settings live in the extruder stack (the engine reads them there),
        # machine-wide settings in the global stack.
        stack = self._stack()
        if scope == "global":
            return stack
        if scope == "extruder" or (stack.hasProperty(key, "value")
                                   and stack.getProperty(key, "settable_per_extruder")):
            return self._mainExtruder() or stack
        return stack

    def _allStacks(self):
        stack = self._stack()
        stacks = [("global", stack)]
        for position, extruder in sorted(stack.extruders.items()):
            stacks.append((position, extruder))
        return stacks

    def _userChanges(self):
        # Settings changed on top of the profiles: {"global": {...}, "extruders": {"0": {...}}}
        result = {"global": {}, "extruders": {}}
        for name, stack in self._allStacks():
            values = {}
            for key in stack.userChanges.getAllKeys():
                value = stack.userChanges.getProperty(key, "value")
                if isinstance(value, (bool, int, float, str)):
                    values[key] = value
            if name == "global":
                result["global"] = values
            elif values:
                result["extruders"][name] = values
        return result

    def _snapshotUserChanges(self):
        return [(stack, dict((key, stack.userChanges.getProperty(key, "value"))
                             for key in stack.userChanges.getAllKeys()))
                for _, stack in self._allStacks()]

    def _restoreUserChanges(self, snapshot):
        for stack, values in snapshot:
            for key in list(stack.userChanges.getAllKeys()):
                if key not in values:
                    stack.userChanges.removeInstance(key)
            for key, value in values.items():
                stack.userChanges.setProperty(key, "value", value)

    def _settingInfo(self, stack, key):
        info = {"key": key,
                "label": stack.getProperty(key, "label"),
                "value": stack.getProperty(key, "value"),
                "unit": stack.getProperty(key, "unit") or None,
                "enabled": stack.getProperty(key, "enabled")}
        options = stack.getProperty(key, "options")
        if options:
            info["options"] = list(options.keys()) if isinstance(options, dict) else options
        return info

    def _getSettings(self, args):
        scope = args.get("scope", "auto")
        keys = args.get("keys") or []
        if isinstance(keys, str):
            keys = [keys]
        found, missing = [], []
        for key in keys:
            stack = self._targetStack(key, scope)
            if stack.hasProperty(key, "value"):
                found.append(self._settingInfo(stack, key))
            else:
                missing.append(key)
        result = {"settings": found}
        if missing:
            result["unknown_keys"] = missing
            result["hint"] = "Неизвестные ключи: найдите правильные через find_settings"
        return result

    def _findSettings(self, args):
        stack = self._stack()
        words = [w for w in str(args.get("query", "")).lower().split() if w]
        if not words:
            raise ValueError("Пустой запрос")
        matches = []
        for definition in stack.definition.findDefinitions():
            if definition.type == "category":
                continue
            text = " ".join([definition.key, definition.label or "", definition.description or ""]).lower()
            score = sum(2 if w in definition.key else 1 for w in words if w in text)
            if score:
                matches.append((score, definition.key))
        matches.sort(key=lambda m: (-m[0], m[1]))
        return {"settings": [self._settingInfo(stack, key) for _, key in matches[:25]],
                "total_matches": len(matches)}

    def _setSettings(self, args):
        scope = args.get("scope", "auto")
        changed, errors = [], []
        for key, value in (args.get("settings") or {}).items():
            stack = self._targetStack(key, scope)
            if not stack.hasProperty(key, "value"):
                errors.append("Нет такой настройки: %s" % key)
                continue
            old = stack.getProperty(key, "value")
            try:
                stack.userChanges.setProperty(key, "value", _coerce(value, stack.getProperty(key, "type")))
            except (TypeError, ValueError) as e:
                errors.append("%s: %s" % (key, e))
                continue
            changed.append({"key": key, "label": stack.getProperty(key, "label"),
                            "old": old, "new": stack.getProperty(key, "value"),
                            "where": "extruder" if stack is not self._stack() else "global"})
        return {"changed": changed, "errors": errors}

    def _setPrintingMode(self, args):
        mode = args["mode"]
        if mode not in PRINTING_MODES:
            raise ValueError("Режим должен быть одним из: %s" % ", ".join(PRINTING_MODES))
        stack = self._stack()
        if not stack.getProperty("machine_five_axis", "value") and mode != "classic":
            raise ValueError("Этот принтер не поддерживает 5D-печать")
        old = stack.getProperty("printing_mode", "value")
        stack.userChanges.setProperty("printing_mode", "value", mode)
        return {"old": old, "new": stack.getProperty("printing_mode", "value"),
                "description": PRINTING_MODES[mode],
                "build_height_mm": stack.getProperty("machine_height", "value")}

    def _listMaterials(self, args):
        stack = self._stack()
        position = str(args.get("extruder", 0))
        extruder = stack.extruders.get(position)
        if extruder is None:
            raise ValueError("Нет экструдера %s" % position)
        nodes = self._app.getMaterialManager().getAvailableMaterialsForMachineExtruder(stack, extruder) or {}
        result = []
        for root_id, node in sorted(nodes.items()):
            result.append({"id": root_id,
                           "name": node.getMetaDataEntry("name"),
                           "type": node.getMetaDataEntry("material"),
                           "brand": node.getMetaDataEntry("brand")})
        return {"extruder": position, "active": extruder.material.getMetaDataEntry("base_file"),
                "materials": result}

    def _selectMaterial(self, args):
        stack = self._stack()
        extruder = stack.extruders.get("0")
        available = self._app.getMaterialManager().getAvailableMaterialsForMachineExtruder(stack, extruder) or {}
        names = dict((key, node.getMetaDataEntry("name")) for key, node in available.items())
        result = materials.select_materials(args, sorted(available.keys()), names)
        result["constraints"] = args
        return result

    def _materialInfo(self, args):
        material_id = args.get("material_id") or self._mainExtruder().material.getMetaDataEntry("base_file")
        return materials.material_info(material_id)

    def _setMaterial(self, args):
        stack = self._stack()
        position = str(args.get("extruder", 0))
        extruder = stack.extruders.get(position)
        if extruder is None:
            raise ValueError("Нет экструдера %s" % position)
        material_id = args["material_id"]
        available = self._app.getMaterialManager().getAvailableMaterialsForMachineExtruder(stack, extruder) or {}
        if material_id not in available:
            raise ValueError("Материал %s недоступен на этом принтере, вызовите list_materials" % material_id)
        old = extruder.material.getName()
        self._app.getMachineManager().setMaterial(position, available[material_id])
        return {"extruder": position, "old": old, "new": extruder.material.getName(),
                "quality_after_change": stack.quality.getName()}

    def _listQualityProfiles(self, args):
        stack = self._stack()
        groups = self._app.getQualityManager().getQualityGroups(stack) or {}
        result = []
        for quality_type, group in sorted(groups.items()):
            layer = None
            node = getattr(group, "node_for_global", None)
            if node is not None and node.getContainer() is not None:
                layer = node.getContainer().getProperty("layer_height", "value")
            result.append({"quality_type": quality_type, "name": group.name,
                           "available": bool(group.is_available), "layer_height": layer})
        return {"active": stack.quality.getMetaDataEntry("quality_type"), "profiles": result}

    def _setQualityProfile(self, args):
        stack = self._stack()
        quality_type = args["quality_type"]
        groups = self._app.getQualityManager().getQualityGroups(stack) or {}
        group = groups.get(quality_type)
        if group is None:
            raise ValueError("Нет профиля %s, вызовите list_quality_profiles" % quality_type)
        if not group.is_available:
            raise ValueError("Профиль %s недоступен для текущего материала" % quality_type)
        self._app.getMachineManager().setQualityGroup(group, no_dialog=True)
        return {"quality": stack.quality.getName(), "layer_height": stack.getProperty("layer_height", "value")}

    def _saveCustomProfile(self, args):
        self._app.getQualityManager().createQualityChanges(args["name"])
        return {"saved": args["name"]}

    # ================= models and scene =================

    def _resetSettings(self, args):
        removed = self._userChanges()
        for _, stack in self._allStacks():
            stack.userChanges.clear()
        count = len(removed["global"]) + sum(len(v) for v in removed["extruders"].values())
        return {"removed_count": count, "removed": removed,
                "printing_mode_now": self._stack().getProperty("printing_mode", "value")}

    def _clearPlate(self, args):
        self._last_results_name = None  # a new part starts: its results go to their own folder
        count = len(self._sliceableNodes())
        self._app.deleteAll()
        return {"removed": count}

    def _loadModel(self, args):
        # Runs in a worker thread: readLocalFile is asynchronous, so poll the scene until the model appears.
        path = args["path"]
        if not os.path.isfile(path):
            raise ValueError("Файл не найден: %s" % path)
        name = os.path.basename(path)
        before = run_on_main(lambda: set(id(n) for n in self._sliceableNodes()))
        run_on_main(lambda: self._app.readLocalFile(QUrl.fromLocalFile(path), skip_project_file_check=True))
        deadline = time.time() + 120
        while time.time() < deadline:
            time.sleep(0.5)
            new = run_on_main(lambda: [self._describeNode(n) for n in self._sliceableNodes() if id(n) not in before])
            if new:
                time.sleep(0.5)  # let the convex hull and physics settle
                return {"loaded": name, "new_objects": run_on_main(
                    lambda: [self._describeNode(n) for n in self._sliceableNodes() if id(n) not in before])}
        raise RuntimeError("Модель %s не загрузилась за 120 с" % name)

    def _listObjects(self, args):
        stack = self._stack()
        return {"objects": [self._describeNode(n) for n in self._sliceableNodes()],
                "build_volume_mm": {"x": stack.getProperty("machine_width", "value"),
                                    "y": stack.getProperty("machine_depth", "value"),
                                    "z": stack.getProperty("machine_height", "value")}}

    def _analyzeModel(self, args):
        return analyze_node(self._node(args))

    def _rotateObject(self, args):
        node = self._node(args)
        # printer axis -> scene axis
        steps = [(args.get("x"), Vector.Unit_X), (args.get("y"), Vector(0, 0, -1)), (args.get("z"), Vector.Unit_Y)]
        op = GroupedOperation()
        center = node.getBoundingBox().center
        for angle, axis in steps:
            if angle:
                q = Quaternion.fromAngleAxis(math.radians(float(angle)), axis)
                op.addOperation(RotateOperation(node, q, rotate_around_point=center))
        op.push()
        self._dropToPlate(node)
        return self._describeNode(node)

    def _moveObject(self, args):
        node = self._node(args)
        center = node.getBoundingBox().center
        delta = Vector(float(args["x"]) - center.x, 0, -float(args["y"]) - center.z)
        TranslateOperation(node, delta).push()
        self._dropToPlate(node)
        return self._describeNode(node)

    def _scaleObject(self, args):
        node = self._node(args)
        factor = float(args["factor"])
        if not 0.01 <= factor <= 100:
            raise ValueError("Недопустимый масштаб")
        node.scale(Vector(factor, factor, factor))
        self._dropToPlate(node)
        return self._describeNode(node)

    def _setup5d(self, args):
        # Worker thread: several main-thread steps, re-analysing the geometry after the rotation.
        steps = []
        analysis = run_on_main(self._analyzeModel, args)
        symmetry = analysis["rotational_symmetry"]
        if not symmetry["likely"]:
            steps.append("Внимание: деталь не похожа на тело вращения (круглость %.2f)"
                         % symmetry["best_axis"]["circularity"])
        rotation = symmetry["rotate_to_make_axis_vertical"]
        if rotation:
            run_on_main(self._rotateObject, dict(args, **rotation))
            steps.append("Ось вращения %s повёрнута в вертикаль: %s"
                         % (symmetry["best_axis"]["axis"], describe_rotation(rotation)))
        run_on_main(self._moveObject, dict(args, x=0, y=0))
        steps.append("Деталь поставлена на ось 5D (X 0, Y 0)")

        analysis = run_on_main(self._analyzeModel, args)
        axis = analysis["rotational_symmetry"]["all_axes"][2]  # Z after the rotation
        bore = axis["central_bore_diameter_mm"]

        def configure():
            stack = self._stack()
            mandrel = stack.getProperty("non_printing_base_diameter", "value") or 6.0
            minimum = 4.0
            if bore > mandrel + 0.5:
                # Bore wider than the mandrel: without a base of the bore's diameter the first spiral
                # turns hang in the air. Spiral Full builds the cylindrical raft up to the base diameter.
                mode = "cylindrical_full"
                base = max(minimum, math.floor(bore * 10 + 1e-3) / 10.0)
                reason = ("отверстие Ø%.1f мм больше оправки Ø%.1f мм: база Ø%.1f заполняет зазор, "
                          "режим Spiral Full строит подложку до детали" % (bore, mandrel, base))
            else:
                mode = "cylindrical"
                base = None
                reason = ("отверстие Ø%.1f мм не больше оправки Ø%.1f мм — достаточно 5D Spiral"
                          % (bore, mandrel)) if bore > 0 else "центрального отверстия нет — 5D Spiral"
            stack.userChanges.setProperty("printing_mode", "value", mode)
            if base is not None:
                stack.userChanges.setProperty("cylindrical_mode_base_diameter", "value", base)
            return {"printing_mode": mode,
                    "cylindrical_mode_base_diameter": stack.getProperty("cylindrical_mode_base_diameter", "value"),
                    "mandrel_diameter": mandrel, "reason": reason}

        config = run_on_main(configure)
        steps.append("Режим %s, база Ø%s мм — %s" % (config["printing_mode"],
                                                    config["cylindrical_mode_base_diameter"], config["reason"]))
        return {"steps": steps, "config": config, "bore_diameter_mm": bore,
                "object": run_on_main(lambda: self._describeNode(self._node(args)))}

    def _layOnPlate(self, args):
        node = self._node(args)
        self._dropToPlate(node)
        return self._describeNode(node)

    def _arrangeAll(self, args):
        self._app.arrangeAll()
        return {"objects": [self._describeNode(n) for n in self._sliceableNodes()]}

    def _deleteObject(self, args):
        node = self._node(args)
        name = node.getName()
        RemoveSceneNodeOperation(node).push()
        return {"deleted": name}

    # ================= slicing and export =================

    def _connectBackend(self):
        if self._backend_connected:
            return
        backend = self._app.getBackend()
        if backend is None:
            return
        backend.backendStateChange.connect(self._onBackendState)
        backend.processingProgress.connect(self._onProgress)
        self._backend_connected = True

    def _onBackendState(self, state):
        self._slice_state = _BACKEND_STATES.get(int(state), str(state))

    def _onProgress(self, amount):
        self._slice_progress = float(amount)

    def _slice(self, args):
        timeout = int(args.get("timeout_s") or 900)

        def start():
            self._connectBackend()
            if not self._sliceableNodes():
                raise ValueError("На столе нет моделей")
            self._slice_state = "processing"
            self._slice_progress = 0.0
            self._app.getBackend().forceSlice()

        # After a settings change the slicer runs a background settings check; when it finishes, the backend
        # re-marks the plate as "needs slicing" and cancels a slice started in the meantime (auto-slicing is
        # off, so nothing restarts it). Start only once that check is done.
        self._waitForSettingsCheck()
        classic = run_on_main(lambda: self._stack().getProperty("printing_mode", "value")) == "classic"
        run_on_main(start)
        started = time.time()
        last_reported = -1
        restarts = 0
        idle_since = None
        while time.time() - started < timeout:
            time.sleep(0.5)
            if self._aborted:
                run_on_main(lambda: self._app.getBackend().stopSlicing())
                self._emit("progress", label="Слайсинг", percent=0, done=True, failed=True)
                raise RuntimeError("Слайсинг остановлен пользователем")
            state, progress = self._slice_state, self._slice_progress
            if state == "not_started":
                idle_since = idle_since or time.time()
                if classic and progress <= 0 and time.time() - idle_since > SLICE_RESTART_AFTER_S:
                    if restarts >= SLICE_MAX_RESTARTS:
                        self._emit("progress", label="Слайсинг", percent=0, done=True, failed=True)
                        raise RuntimeError("Слайсер несколько раз отменил нарезку. Проверьте настройки на ошибки "
                                           "(красные поля в слайсере) и повторите.")
                    restarts += 1
                    Logger.log("i", "AI Assistant: slice was cancelled by the backend, restart #%d", restarts)
                    run_on_main(start)
                    idle_since = None
                continue
            idle_since = None
            percent = int(progress * 100)
            if percent != last_reported:
                last_reported = percent
                self._emit("progress", label="Слайсинг", percent=max(0, min(100, percent)))
            if state == "done":
                time.sleep(0.5)  # print information is updated right after the state change
                info = run_on_main(self._getPrintInfo, {})
                info["slice_seconds"] = round(time.time() - started, 1)
                self._emit("progress", label="Слайсинг", percent=100, done=True)
                return info
            if state in ("error", "disabled"):
                self._emit("progress", label="Слайсинг", percent=0, done=True, failed=True)
                raise RuntimeError("Слайсинг завершился ошибкой. Проверьте, что модель помещается в область печати "
                                   "и что в настройках нет ошибок (красные поля в слайсере).")
        self._emit("progress", label="Слайсинг", percent=0, done=True, failed=True)
        raise RuntimeError("Слайсинг не завершился за %d с" % timeout)

    def _waitForSettingsCheck(self):
        def busy():
            checker = self._app.getMachineErrorChecker()
            return bool(checker is not None and checker.needToWaitForResult)

        deadline = time.time() + SLICE_SETTLE_MAX_S
        time.sleep(SLICE_SETTLE_S)  # let queued setting-change signals schedule the check first
        quiet_since = None
        while time.time() < deadline and not self._aborted:
            if run_on_main(busy):
                quiet_since = None
            else:
                quiet_since = quiet_since or time.time()
                if time.time() - quiet_since >= SLICE_SETTLE_S:
                    return
            time.sleep(0.25)

    def _getSliceStatus(self, args):
        self._connectBackend()
        return {"state": self._slice_state, "progress_percent": int(self._slice_progress * 100)}

    def _getPrintInfo(self, args):
        info = self._app.getPrintInformation()
        duration = info.currentPrintTime
        seconds = int(duration) if duration is not None else 0
        weights = [round(w, 1) for w in (info.materialWeights or [])]
        lengths = [round(l, 2) for l in (info.materialLengths or [])]
        total_g = round(sum(weights), 1)
        extruder = self._mainExtruder()
        price = production.material_price_per_kg(extruder.material.getMetaDataEntry("base_file"),
                                                 extruder.material.getMetaDataEntry("material"))
        try:
            rate = float(self._settings.get("machine_rate_rub"))
        except ValueError:
            rate = 300.0
        return {"print_time_seconds": seconds,
                "print_time": "%d ч %02d мин" % (seconds // 3600, (seconds % 3600) // 60),
                "material_weight_g": weights,
                "material_length_m": lengths,
                "material_total_g": total_g,
                "cost": production.estimate_cost(total_g, seconds, price, rate)}

    def _exportGcode(self, args):
        # Same content as GCodeWriter.write, but without the preview thumbnail: rendering it goes through
        # OpenGL on the Qt thread and can freeze the slicer (seen with 5D scenes). The thumbnail is optional.
        path = _prepare_path(args["path"], ".gcode")
        if self._slice_state != "done":
            raise RuntimeError("Нет готового G-code: сначала вызовите slice")

        def collect():
            writer = self._app.getMeshFileHandler().getWriter("GCodeWriter")
            if writer is None:
                raise RuntimeError("GCodeWriter не найден")
            plate = self._app.getMultiBuildPlateModel().activeBuildPlate
            gcode_list = getattr(self._app.getController().getScene(), "gcode_dict", {}).get(plate)
            if not gcode_list:
                raise RuntimeError("Нет готового G-code: сначала вызовите slice")
            header = []
            for name in ("_getSlicerVersion", "_getPrintingMode", "_getSerializedBounding"):
                method = getattr(writer, name, None)
                if method is not None:
                    header.append(method())
            keyword = getattr(writer, "_setting_keyword", ";SETTING_")
            has_settings = any(g.startswith(keyword) for g in gcode_list)
            settings = None if has_settings else writer._serialiseSettings(self._app.getGlobalContainerStack())
            return header, list(gcode_list), settings

        header, gcode_list, settings = run_on_main(collect)
        with open(path, "wt", encoding="utf-8") as stream:
            for chunk in header + gcode_list:
                stream.write(chunk)
            if settings:
                stream.write(settings)
        return {"saved": path, "size_kb": round(os.path.getsize(path) / 1024.0, 1)}

    def _saveProject(self, args):
        # Worker thread only: the 3MF workspace writer is decorated with @call_on_qt_thread, which posts
        # itself to the Qt thread and blocks until done — calling it from the Qt thread would deadlock.
        path = _prepare_path(args["path"], ".3mf")
        writer, nodes = run_on_main(lambda: (self._app.getWorkspaceFileHandler().getWriter("3MFWriter"),
                                             [self._app.getController().getScene().getRoot()]))
        if writer is None:
            raise RuntimeError("Запись проектов 3MF недоступна")
        with open(path, "wb") as stream:
            ok = writer.write(stream, nodes)
        if not ok:
            raise RuntimeError("Не удалось сохранить проект")
        return {"saved": path, "size_kb": round(os.path.getsize(path) / 1024.0, 1)}

    def _saveResults(self, args):
        name = re.sub(r'[<>:"/\\|?*]', "_", args["model_name"]).strip() or "model"
        folder = os.path.join(self._settings.get("output_dir"), "results", name)
        self._last_results_name = name  # the tech card goes into the same folder
        project = self._saveProject({"path": os.path.join(folder, "project.3mf")})
        gcode = self._exportGcode({"path": os.path.join(folder, "result.gcode")})
        return {"folder": folder, "project": project, "gcode": gcode,
                "next_step": "Добавьте в эту папку скриншоты переписки chat_1.png, chat_2.png..."}

    # ================= production features =================

    def _preflightCheck(self, args):
        node = self._node(args)
        stack = self._stack()
        extruder = self._mainExtruder()
        analysis = analyze_node(node)
        mode = stack.getProperty("printing_mode", "value")
        issues = []

        def add(level, message, advice=""):
            issues.append({"level": level, "message": message, "advice": advice})

        size = analysis["size_mm"]
        volume = (stack.getProperty("machine_width", "value"), stack.getProperty("machine_depth", "value"),
                  stack.getProperty("machine_height", "value"))
        if size["x"] > volume[0] or size["y"] > volume[1] or size["z"] > volume[2]:
            add("ошибка", "Деталь %s×%s×%s мм больше области печати %s×%s×%s мм"
                % (size["x"], size["y"], size["z"], volume[0], volume[1], volume[2]),
                "Поверните деталь или уменьшите масштаб")
        elif getattr(node, "isOutsideBuildArea", lambda: False)():
            add("ошибка", "Деталь выходит за границы стола", "move_object в центр или arrange_all")

        current = analysis["orientations_3d"][0]
        best = analysis["best_orientation_3d"]
        line_width = extruder.getProperty("line_width", "value") if extruder is not None else 0.4
        material_type = (extruder.material.getMetaDataEntry("material") or "").upper() if extruder is not None else ""
        adhesion = stack.getProperty("adhesion_type", "value")

        if mode == "classic":
            supports = stack.getProperty("support_enable", "value") or \
                (extruder is not None and extruder.getProperty("support_enable", "value"))
            if current["overhang_area_mm2"] > 50 and not supports:
                add("предупреждение", "Нависания %.0f мм² при выключенных поддержках" % current["overhang_area_mm2"],
                    "Включите support_enable или поверните деталь")
            if best["overhang_area_mm2"] < current["overhang_area_mm2"] * 0.5 and current["overhang_area_mm2"] > 50:
                add("инфо", "Ориентация «на стол гранью %s» уменьшит нависания до %.0f мм²"
                    % (best["face_on_plate"], best["overhang_area_mm2"]), describe_rotation(best["rotate"]))
            footprint = max(size["x"] * size["y"], 1.0)
            contact = current["contact_area_mm2"]
            if contact < 0.1 * footprint or contact < 80:
                add("предупреждение", "Маленькая площадь опоры на стол: %.0f мм² (%.0f%% габарита)"
                    % (contact, 100.0 * contact / footprint),
                    "adhesion_type = brim, либо другая ориентация")
            warp_prone = any(t in material_type for t in ("PC", "ABS", "NYLON", "PA", "PP", "ASA"))
            if warp_prone and adhesion in ("none", "skirt") and max(size["x"], size["y"]) > 40:
                add("предупреждение", "Материал %s склонен к короблению, адгезия «%s»" % (material_type, adhesion),
                    "adhesion_type = brim")
        else:
            symmetry = analysis["rotational_symmetry"]
            if not symmetry["likely"]:
                add("предупреждение", "Деталь не похожа на тело вращения — проверьте, что 5D здесь уместен")
            elif symmetry["best_axis"]["axis"] != "Z":
                add("ошибка", "Ось вращения детали (%s) не вертикальна" % symmetry["best_axis"]["axis"],
                    "setup_5d (" + describe_rotation(symmetry["rotate_to_make_axis_vertical"]) + ")")
            box = node.getBoundingBox().center
            if abs(box.x) > 0.5 or abs(box.z) > 0.5:
                add("ошибка", "Ось детали смещена от оси вращения 5D: X %.1f, Y %.1f мм" % (box.x, -box.z),
                    "move_object x=0, y=0")
            bore = analysis["rotational_symmetry"]["all_axes"][2]["central_bore_diameter_mm"]
            base = stack.getProperty("cylindrical_mode_base_diameter", "value")
            if bore > base + 0.5:
                add("ошибка", "Зазор между базой 5D Ø%.1f мм и отверстием детали Ø%.1f мм: первые витки "
                    "спирали печатаются в воздухе" % (base, bore),
                    "setup_5d (режим cylindrical_full, cylindrical_mode_base_diameter = %.1f)"
                    % (math.floor(bore * 10 + 1e-3) / 10.0))
            elif mode == "cylindrical" and bore > (stack.getProperty("non_printing_base_diameter", "value") or 6) + 0.5:
                add("ошибка", "База Ø%.1f мм больше оправки, но в режиме Spiral подложка не строится" % base,
                    "Режим cylindrical_full")
            else:
                add("инфо", "База 5D Ø%.1f мм, отверстие детали %s — зазора нет"
                    % (base, ("Ø%.1f мм" % bore) if bore > 0 else "не найдено"))

        smallest = min(size["x"], size["y"], size["z"])
        if smallest < 2 * line_width:
            add("предупреждение", "Очень тонкая деталь: %.2f мм при ширине линии %.2f мм" % (smallest, line_width),
                "Проверьте, что тонкие места пропечатаются")
        material_id = (extruder.material.getMetaDataEntry("base_file") or "") if extruder is not None else ""
        if re.match(r"(fiberpart_|contifiber|generic_cff|generic_gff)", material_id):
            add("инфо", "Наполненный материал (%s) абразивен" % material_id,
                "Нужно износостойкое сопло; учитывайте износ ответных деталей")
        if not issues:
            add("инфо", "Проблем не найдено")
        errors = sum(1 for i in issues if i["level"] == "ошибка")
        return {"ready": errors == 0, "errors": errors, "issues": issues, "printing_mode": mode}

    def _checkProcess(self, args):
        stack = self._stack()
        extruder = self._mainExtruder()
        material = extruder.material
        material_id = material.getMetaDataEntry("base_file")
        window = materials.process_window(material_id) or {}
        mode = stack.getProperty("printing_mode", "value")
        checks = []

        def add(name, status, current, recommended, source, advice=""):
            checks.append({"name": name, "status": status, "current": current, "recommended": recommended,
                           "source": source, "advice": advice})

        def current(key):
            return extruder.getProperty(key, "value")

        # Values recommended by the material manufacturer: the material profile stores the XML settings under
        # these keys ("print temperature" -> default_material_print_temperature, etc.).
        maker_keys = {"material_print_temperature": "default_material_print_temperature",
                      "material_bed_temperature": "default_material_bed_temperature",
                      "cool_fan_speed": "cool_fan_speed", "retraction_amount": "retraction_amount"}
        xml_settings = _material_xml_settings(material_id)

        def maker(key):
            if key == "speed_print":
                value = xml_settings.get("speed print")
                return float(value) if value else None
            profile_key = maker_keys.get(key)
            if profile_key and profile_key in xml_settings.get("_mapped", ()) and material.hasProperty(profile_key, "value"):
                return material.getProperty(profile_key, "value")
            return None

        # Temperatures: manufacturer profile first, then the family window (Prusa guide).
        for key, label, window_key in (("material_print_temperature", "Температура сопла", "nozzle_c"),
                                       ("material_bed_temperature", "Температура стола", "bed_c")):
            value = current(key)
            ref = maker(key)
            rng = window.get(window_key)
            if ref is not None and abs(value - ref) > 5:
                add(label, "warning", value, ref, "профиль Stereotech",
                    "Отличается от рекомендации производителя материала более чем на 5 °C")
            elif rng and not (rng[0] - 5 <= value <= rng[1] + 5):
                add(label, "warning", value, "%s–%s" % (rng[0], rng[1]), "Prusa Material Guide",
                    "Вне типичного окна для семейства материала")
            else:
                add(label, "ok", value, ref if ref is not None else ("%s–%s" % tuple(rng) if rng else None),
                    "профиль Stereotech" if ref is not None else "Prusa Material Guide")

        # Cooling
        fan_on = current("cool_fan_enabled")
        fan = current("cool_fan_speed") if fan_on else 0
        fan_ref = maker("cool_fan_speed")
        fan_rng = window.get("fan_pct")
        if fan_ref is not None and abs(fan - fan_ref) > 15:
            add("Обдув", "warning", fan, fan_ref, "профиль Stereotech",
                "Сильный обдув у PC/ABS/PA вызывает коробление и расслоение, слабый у PLA — оплывание")
        elif fan_ref is None and fan_rng and not (fan_rng[0] <= fan <= fan_rng[1]):
            add("Обдув", "warning", fan, "%s–%s" % (fan_rng[0], fan_rng[1]), "оценка", "Вне типичного диапазона")
        else:
            add("Обдув", "ok", fan, fan_ref if fan_ref is not None else fan_rng,
                "профиль Stereotech" if fan_ref is not None else "оценка")

        # Speed and retraction against the manufacturer profile
        for key, label, tolerance in (("speed_print", "Скорость печати", 0.3), ("retraction_amount", "Ретракт", 0.5)):
            ref = maker(key)
            value = current(key)
            if ref:
                ok = abs(value - ref) <= tolerance * ref
                add(label, "ok" if ok else "warning", value, ref, "профиль Stereotech",
                    "" if ok else "Сильно отличается от рекомендации производителя материала")

        # Geometry of the extrusion relative to the nozzle
        nozzle = current("machine_nozzle_size") or 0.4
        layer_key = "cylindrical_layer_height" if mode in ("cylindrical", "cylindrical_full") else "layer_height"
        layer = stack.getProperty(layer_key, "value")
        ok = 0.25 * nozzle <= layer <= 0.8 * nozzle
        add("Высота слоя (%s)" % layer_key, "ok" if ok else "problem", layer,
            "%.2f–%.2f" % (0.25 * nozzle, 0.8 * nozzle), "правило: 25–80 % диаметра сопла",
            "" if ok else "Слой вне допустимого для сопла %.1f мм — плохое спекание слоёв или недоэкструзия" % nozzle)
        width = current("line_width")
        ok = 0.9 * nozzle <= width <= 1.3 * nozzle
        add("Ширина линии", "ok" if ok else "warning", width, "%.2f–%.2f" % (0.9 * nozzle, 1.3 * nozzle),
            "правило: 90–130 % диаметра сопла")
        first = stack.getProperty("layer_height_0", "value")
        add("Первый слой", "ok" if first >= layer else "warning", first, "≥ %s" % layer,
            "правило: первый слой не тоньше основного", "" if first >= layer else "Тонкий первый слой хуже держится")

        # Equipment recommendations
        if window.get("enclosure") in ("рекомендуется",):
            add("Закрытая камера", "warning", None, "рекомендуется", "Prusa Material Guide",
                "Материал склонен к короблению: печатать с закрытой камерой, без сквозняков")
        if window.get("dry") in ("да",):
            add("Сушка материала", "warning", None, "сушить перед печатью", "Prusa Material Guide",
                "Материал гигроскопичен: влажный пруток даёт пузыри и слабые слои")
        if window.get("hardened_nozzle"):
            add("Сопло", "warning", None, "закалённое / износостойкое", "Prusa Material Guide",
                "Наполнитель изнашивает латунное сопло")

        problems = sum(1 for c in checks if c["status"] == "problem")
        warnings = sum(1 for c in checks if c["status"] == "warning")
        return {"material": material.getName(), "material_id": material_id, "printing_mode": mode,
                "problems": problems, "warnings": warnings, "checks": checks,
                "adhesion_info": material.getMetaDataEntry("adhesion_info"),
                "material_description": material.getMetaDataEntry("description")}

    def _compareVariants(self, args):
        variants = (args.get("variants") or [])[:4]
        if not variants:
            raise ValueError("Передайте хотя бы один вариант")
        snapshot = run_on_main(self._snapshotUserChanges)
        results = []
        try:
            for variant in variants:
                name = variant.get("name") or "Вариант"
                run_on_main(self._restoreUserChanges, snapshot)
                if variant.get("settings"):
                    applied = run_on_main(self._setSettings, {"settings": variant["settings"]})
                    if applied["errors"]:
                        results.append({"name": name, "error": "; ".join(applied["errors"])})
                        continue
                self._emit("progress", label="Вариант «%s»" % name, percent=0)
                try:
                    info = self._slice({})
                except RuntimeError as e:
                    results.append({"name": name, "error": str(e)})
                    continue
                results.append({"name": name, "settings": variant.get("settings") or {},
                                "print_time": info["print_time"], "print_time_seconds": info["print_time_seconds"],
                                "material_g": info["material_total_g"], "cost_rub": info.get("cost", {}).get("total_rub")})
        finally:
            run_on_main(self._restoreUserChanges, snapshot)
            self._slice_state = "not_started"
        return {"variants": results,
                "note": "Исходные настройки восстановлены. Чтобы применить вариант — set_settings, затем slice."}

    def _makeTechCard(self, args):
        # The model may pass a longer title than it used for save_results: keep the card next to the
        # saved project and G-code, use the title only for the heading.
        name = getattr(self, "_last_results_name", None) or production.safe_name(args["model_name"])

        def collect():
            data = {"status": self._getSlicerStatus({}), "changes": self._userChanges(),
                    "objects": [self._describeNode(n) for n in self._sliceableNodes()]}
            if self._slice_state == "done":
                data["print_info"] = self._getPrintInfo({})
            return data

        data = run_on_main(collect)
        data.update({"model_name": args["model_name"], "date": production.today(),
                     "summary": args.get("summary"), "requirements": args.get("requirements"),
                     "orientation_note": args.get("orientation_note"), "risks": args.get("risks")})
        data["cost"] = (data.get("print_info") or {}).get("cost")
        folder = os.path.join(self._settings.get("output_dir"), "results", name)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        data["files"] = [f for f in ("project.3mf", "result.gcode") if os.path.isfile(os.path.join(folder, f))]
        path = os.path.join(folder, "techcard.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(production.build_tech_card(data))
        url = "/files/results/%s/techcard.html" % name
        self._emit("file", label="Техкарта: %s" % args["model_name"], url=url)
        return {"saved": path, "url": url}

    def _saveRecipe(self, args):
        stack = self._stack()
        extruder = self._mainExtruder()
        recipe = {"name": args["name"], "description": args.get("description", ""), "created": production.today(),
                  "printer": stack.definition.getId(),
                  "printing_mode": stack.getProperty("printing_mode", "value"),
                  "material_id": extruder.material.getMetaDataEntry("base_file"),
                  "quality_type": stack.quality.getMetaDataEntry("quality_type"),
                  "changes": self._userChanges()}
        path = production.save_recipe(self._settings.get("output_dir"), recipe)
        return {"saved": path, "recipe": recipe}

    def _listRecipes(self, args):
        return {"recipes": production.list_recipes(self._settings.get("output_dir"))}

    def _applyRecipe(self, args):
        recipe = production.load_recipe(self._settings.get("output_dir"), args["name"])
        stack = self._stack()
        notes = []
        if recipe.get("printer") and recipe["printer"] != stack.definition.getId():
            notes.append("Рецепт создан для принтера %s, активен %s" % (recipe["printer"], stack.definition.getId()))
        for _, s in self._allStacks():
            s.userChanges.clear()
        material_id = args.get("material_id") or recipe.get("material_id")
        if material_id:
            try:
                self._setMaterial({"material_id": material_id})
            except ValueError as e:
                notes.append(str(e))
        quality_type = recipe.get("quality_type")
        if quality_type:
            try:
                self._setQualityProfile({"quality_type": quality_type})
            except ValueError as e:
                notes.append(str(e))
        stack.userChanges.setProperty("printing_mode", "value", recipe.get("printing_mode") or "classic")
        changes = recipe.get("changes") or {}
        for key, value in (changes.get("global") or {}).items():
            if stack.hasProperty(key, "value"):
                stack.userChanges.setProperty(key, "value", value)
        for position, values in (changes.get("extruders") or {}).items():
            extruder = stack.extruders.get(position)
            if extruder is None:
                continue
            for key, value in values.items():
                if extruder.hasProperty(key, "value"):
                    extruder.userChanges.setProperty(key, "value", value)
        return {"applied": recipe["name"], "material": material_id, "notes": notes,
                "status": self._getSlicerStatus({})}


_XML_TO_PROFILE_KEY = {"print temperature": "default_material_print_temperature",
                       "heated bed temperature": "default_material_bed_temperature",
                       "print cooling": "cool_fan_speed", "retraction amount": "retraction_amount"}


def _material_xml_settings(material_id):
    """Settings written by the manufacturer in the material's .xml.fdm_material file (empty if not found)."""
    try:
        import sys
        folder = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "resources", "materials")
        path = os.path.join(folder, "%s.xml.fdm_material" % material_id)
        if not os.path.isfile(path):
            return {}
        from xml.etree import ElementTree
        root = ElementTree.parse(path).getroot()
        result = {}
        for element in root.iter():
            if element.tag.endswith("setting") and element.get("key") and element.text and element.text.strip():
                result.setdefault(element.get("key"), element.text.strip())
        result["_mapped"] = [_XML_TO_PROFILE_KEY[k] for k in result if k in _XML_TO_PROFILE_KEY]
        return result
    except Exception as e:
        Logger.log("w", "AI Assistant: cannot read material profile %s: %s", material_id, e)
        return {}


def _prepare_path(path, extension):
    path = os.path.abspath(os.path.expanduser(path))
    if not path.lower().endswith(extension):
        path += extension
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    return path


def _coerce(value, setting_type):
    if setting_type == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "да")
        return bool(value)
    if setting_type == "int":
        return int(float(value))
    if setting_type == "float":
        return float(value)
    return value if isinstance(value, str) else str(value)
