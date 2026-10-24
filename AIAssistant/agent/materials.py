# Material property database (materials_db.json) and requirement-based selection.
#
# Every property value carries its source: published data sheets (Ultimaker via TU Dresden, Ultimaker TDS),
# the Prusa material guide for process windows, Stereotech material profiles, or "estimate" where no open
# data sheet exists. Levels for resistances and wear: 0 — poor, 1 — moderate, 2 — good.

import json
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "materials_db.json")

LEVEL = {0: "низкая", 1: "средняя", 2: "высокая"}
SOURCE_LABEL = {"um_tud_2024": "Ultimaker / TU Dresden", "um_pc_tds": "Ultimaker TDS", "prusa_guide": "Prusa",
                "stereotech_profile": "профиль Stereotech", "estimate": "оценка"}
STEREOTECH_PREFIXES = ("enduse_", "fiberpart_", "sealant_", "proto_")

_DEFAULTS = {"abrasive": False, "conductive": False, "flexible": False, "semi_flexible": False, "food_safe": False,
             "low_voc": True, "marks_surfaces": False, "esd": False, "role": "part"}


def _impact_from_izod(izod):
    return 0 if izod < 6 else (1 if izod <= 12 else 2)


def _load():
    with open(DB_PATH, encoding="utf-8") as f:
        db = json.load(f)
    materials = {}
    for material_id, entry in db["materials"].items():
        props, src = {}, {}

        def put(key, value, source):
            props[key] = value
            src[key] = source

        ref_id = entry.get("ref")
        if ref_id:
            ref = db["references"][ref_id]
            for key, value in ref.items():
                if key not in ("name", "source", "extra") and not isinstance(value, dict):
                    put(key, value, ref["source"])
            for key, (value, source) in db["levels"].get(ref_id, {}).items():
                put(key, value, source)
            if "izod_kj_m2" in props and "impact" not in props:
                put("impact", _impact_from_izod(props["izod_kj_m2"]), src["izod_kj_m2"])
            props["reference"] = ref["name"] + (" (аналог)" if entry.get("analog") else "")
        template = entry.get("family_template")
        if template:
            for key, value in db["templates"][template].items():
                if key != "source":
                    put(key, value, "estimate")
            props["reference"] = "типовые значения семейства %s" % template
        for key, value in (entry.get("override") or {}).items():
            put(key, value, "estimate")
        if "family" not in props:
            props["family"] = entry.get("process", "?")
        for key, value in _DEFAULTS.items():
            if key not in props:
                props[key] = entry.get(key, value)
        props["process"] = entry.get("process")
        props["_src"] = src
        materials[material_id] = props
    return db, materials


DB, MATERIALS = _load()

# Constraint schema exposed to the LLM (all optional). Hard constraints exclude, "prefer" only ranks.
CONSTRAINT_SCHEMA = {
    "service_temp_c": {"type": "number", "description": "Длительная рабочая температура детали, °C"},
    "short_term_temp_c": {"type": "number", "description": "Кратковременный контакт с горячим, °C"},
    "min_tg_c": {"type": "number", "description": "Только если ТЗ прямо требует температуру стеклования"},
    "max_density": {"type": "number", "description": "г/см³"},
    "max_water_absorption_pct": {"type": "number", "description": "Водопоглощение за 24 ч, %"},
    "min_modulus_gpa": {"type": "number", "description": "Модуль упругости при растяжении, ГПа (4500 МПа = 4.5)"},
    "min_elongation_pct": {"type": "number", "description": "Удлинение при разрыве, % (нехрупкость)"},
    "uv": {"type": "integer", "description": "Требуемая стойкость к УФ: 1 средняя, 2 высокая"},
    "ozone": {"type": "integer", "description": "Стойкость к озону: 1/2"},
    "oil_fuel": {"type": "integer", "description": "Масло-, бензостойкость: 1/2"},
    "alkali": {"type": "integer", "description": "Стойкость к щелочам: 1/2"},
    "acid": {"type": "integer", "description": "Стойкость к кислотам/солям: 1/2"},
    "alcohol": {"type": "integer", "description": "Стойкость к спиртам (IPA): 1/2"},
    "wear": {"type": "integer", "description": "Износостойкость: 1/2"},
    "impact": {"type": "integer", "description": "Ударная вязкость/нехрупкость: 1/2"},
    "flexible": {"type": "boolean", "description": "true — нужна эластичная деталь (чехлы, уплотнения). "
                                                   "По умолчанию деталь жёсткая и эластомеры исключаются"},
    "friction": {"type": "string", "enum": ["low", "high"], "description": "Нужно низкое (скольжение) или высокое (сцепление) трение"},
    "non_abrasive": {"type": "boolean", "description": "Без стекла/углерода: не изнашивать ответные детали"},
    "dielectric": {"type": "boolean", "description": "Материал не должен проводить ток"},
    "no_marks": {"type": "boolean", "description": "Не оставлять следов на контактирующих поверхностях"},
    "food_safe": {"type": "boolean"},
    "low_voc": {"type": "boolean", "description": "Без летучих выделений (стирол и т.п.)"},
    "prefer": {"type": "array", "items": {"type": "string",
                                          "enum": ["esd", "light", "stiff", "tough", "low_friction", "high_friction",
                                                   "heat", "chemical", "cheap"]},
               "description": "Пожелания для ранжирования, не исключают"},
}


def _source(m, key):
    return SOURCE_LABEL.get(m["_src"].get(key, "estimate"), "оценка")


def _check(m, c):
    reasons = []

    def need(ok, text, key=None):
        if not ok:
            reasons.append(text + (" [%s]" % _source(m, key) if key else ""))

    def value(key, default=None):
        return m.get(key, default)

    if c.get("service_temp_c") is not None:
        need(value("service_temp_c", 0) >= c["service_temp_c"],
             "рабочая температура %s °C < %s °C" % (value("service_temp_c", "?"), c["service_temp_c"]), "service_temp_c")
    if c.get("short_term_temp_c") is not None:
        need(value("short_term_temp_c", 0) >= c["short_term_temp_c"],
             "кратковременно выдерживает %s °C < %s °C" % (value("short_term_temp_c", "?"), c["short_term_temp_c"]),
             "short_term_temp_c")
    if c.get("min_tg_c") is not None:
        need(value("tg_c", -999) >= c["min_tg_c"], "Tg %s °C < %s °C" % (value("tg_c", "?"), c["min_tg_c"]), "tg_c")
    if c.get("max_density") is not None:
        need(value("density", 99) <= c["max_density"],
             "плотность %s > %s г/см³" % (value("density", "?"), c["max_density"]), "density")
    if c.get("max_water_absorption_pct") is not None:
        need(value("water_pct", 99) <= c["max_water_absorption_pct"],
             "водопоглощение %s %% > %s %%" % (value("water_pct", "?"), c["max_water_absorption_pct"]), "water_pct")
    if c.get("min_modulus_gpa") is not None:
        need(value("modulus_gpa", 0) >= c["min_modulus_gpa"],
             "модуль %s ГПа < %s ГПа" % (value("modulus_gpa", "?"), c["min_modulus_gpa"]), "modulus_gpa")
    if c.get("min_elongation_pct") is not None:
        need(value("elongation_pct", 0) >= c["min_elongation_pct"],
             "удлинение %s %% < %s %% (хрупкий)" % (value("elongation_pct", "?"), c["min_elongation_pct"]),
             "elongation_pct")
    for key, label in (("uv", "УФ"), ("ozone", "озону"), ("oil", "маслам/топливу"), ("alkali", "щелочам"),
                       ("acid", "кислотам"), ("alcohol", "спиртам"), ("wear", "износу"), ("impact", "удару")):
        ckey = "oil_fuel" if key == "oil" else key
        if c.get(ckey):
            level = value(key, 0)
            need(level >= c[ckey], "стойкость к %s %s" % (label, LEVEL.get(level, "?")), key)
    if c.get("flexible") is True:
        need(m["flexible"], "не эластичный")
    else:  # rigid part unless elasticity is asked for
        need(not m["flexible"], "эластомер, не держит форму", "flexible")
    if c.get("friction") == "low":
        need(value("friction") in ("low", "medium"), "высокое трение", "friction")
    if c.get("friction") == "high":
        need(value("friction") == "high", "низкое трение (будет проскальзывать)", "friction")
    if c.get("non_abrasive"):
        need(not m["abrasive"], "наполнитель абразивен для ответных деталей")
    if c.get("dielectric"):
        need(not m["conductive"], "проводит ток")
    if c.get("no_marks"):
        need(not m["marks_surfaces"], "может оставлять тёмные следы (углерод)")
    if c.get("food_safe"):
        need(m["food_safe"], "нет данных о пищевом допуске")
    if c.get("low_voc"):
        need(m["low_voc"], "выделяет летучие вещества (стирол)")
    return reasons


def _score(material_id, m, c):
    score = 0.0
    # A safety margin is good, but an over-specified material is not better: margins are capped.
    if c.get("service_temp_c") is not None:
        score += 0.5 * min(1.0, (m.get("service_temp_c", 0) - c["service_temp_c"]) / 30.0)
    if c.get("min_modulus_gpa") is not None:
        score += 0.5 * min(1.0, (m.get("modulus_gpa", 0) - c["min_modulus_gpa"]) / 2.0)
    # Fillers wear nozzles and mating parts and shed dust: use them only when stiffness is asked for.
    if m["abrasive"] and "stiff" not in (c.get("prefer") or []) and c.get("min_modulus_gpa") is None:
        score -= 0.5
    for want in c.get("prefer") or []:
        if want == "esd" and m.get("esd"):
            score += 1.0
        elif want == "light":
            score += max(0.0, 1.3 - m.get("density", 1.3))
        elif want == "stiff":
            score += min(1.0, m.get("modulus_gpa", 0) / 5.0)
        elif want == "tough":
            score += m.get("impact", 0) / 2.0
        elif want == "low_friction" and m.get("friction") == "low":
            score += 1.0
        elif want == "high_friction" and m.get("friction") == "high":
            score += 1.0
        elif want == "heat":
            score += min(1.0, m.get("service_temp_c", 0) / 150.0)
        elif want == "chemical":
            score += sum(m.get(k, 0) for k in ("oil", "alkali", "acid", "alcohol")) / 8.0
        elif want == "cheap" and not m["abrasive"]:
            score += 0.5
    # Materials of the printer's manufacturer have tuned profiles for this machine.
    if material_id.startswith(STEREOTECH_PREFIXES):
        score += 0.3
    # Data from published sheets is preferred over estimates.
    measured = sum(1 for s in m["_src"].values() if s != "estimate")
    score += 0.05 * min(measured, 10)
    return round(score, 2)


def select_materials(constraints, available_ids=None, names=None):
    """Return ranked suitable materials and the excluded ones with reasons (with sources)."""
    names = names or {}
    suitable, excluded, unknown = [], [], []
    ids = available_ids if available_ids is not None else list(MATERIALS.keys())
    for material_id in ids:
        m = MATERIALS.get(material_id)
        name = names.get(material_id) or material_id
        if m is None:
            unknown.append(material_id)
            continue
        if m["role"] != "part":
            excluded.append({"id": material_id, "name": name,
                             "reasons": [{"support": "материал поддержек", "metal": "металлонаполненный для спекания",
                                          "fiber": "армирующее волокно, не самостоятельный материал"}[m["role"]]]})
            continue
        reasons = _check(m, constraints)
        if reasons:
            excluded.append({"id": material_id, "name": name, "reasons": reasons})
        else:
            suitable.append({"id": material_id, "name": name, "score": _score(material_id, m, constraints),
                             "data_from": m.get("reference"), "properties": describe(m)})
    suitable.sort(key=lambda s: -s["score"])
    # Full properties only for the top candidates: the result goes into the LLM context on every step.
    for candidate in suitable[3:]:
        candidate.pop("properties", None)
    closest = []
    if not suitable:
        # Nothing meets every requirement: show the materials that fail the fewest, with what exactly fails,
        # so the compromise is explicit instead of silently dropping the main functional requirement.
        ranked = sorted((e for e in excluded if MATERIALS.get(e["id"], {}).get("role") == "part"),
                        key=lambda e: (len(e["reasons"]), -_score(e["id"], MATERIALS[e["id"]], constraints)))
        closest = [{"id": e["id"], "name": e["name"], "fails": e["reasons"]} for e in ranked[:5]]
    return {"recommended": suitable[0]["id"] if suitable else None,
            "closest_if_none": closest,
            "suitable": suitable[:8], "excluded": excluded, "not_in_database": unknown,
            "note": "В квадратных скобках — источник значения. «оценка» — нет открытого паспорта, нужна проверка. "
                    "Полный список источников — material_info."}


def describe(m):
    def v(key, fmt="%s"):
        if key not in m:
            return None
        value = m[key]
        if key in ("uv", "oil", "alkali", "acid", "alcohol", "wear", "impact", "ozone"):
            value = LEVEL.get(value, value)
        return (fmt % value) + " [%s]" % _source(m, key)
    data = {"семейство": m.get("family"), "данные": m.get("reference"),
            "Tg": v("tg_c", "%s °C"), "рабочая_t": v("service_temp_c", "%s °C"),
            "плотность": v("density", "%s г/см³"), "водопоглощение": v("water_pct", "%s %%"),
            "модуль": v("modulus_gpa", "%s ГПа"), "удлинение": v("elongation_pct", "%s %%"),
            "УФ": v("uv"), "масла": v("oil"), "щёлочи": v("alkali"), "кислоты": v("acid"), "спирты": v("alcohol"),
            "износ": v("wear"), "удар": v("impact"), "трение": v("friction"),
            "абразивный": m["abrasive"], "проводящий": m["conductive"]}
    return dict((k, val) for k, val in data.items() if val is not None)


def process_window(material_id):
    """Recommended process window of the material family (Prusa guide) or None."""
    m = MATERIALS.get(material_id)
    if m is None or not m.get("process"):
        return None
    return DB["process"].get(m["process"])


def material_info(material_id):
    m = MATERIALS.get(material_id)
    if m is None:
        raise ValueError("Материала %s нет в базе" % material_id)
    return {"id": material_id, "properties": describe(m), "process_window": process_window(material_id),
            "sources": sources_used()}


def sources_used():
    return dict((key, "%s — %s%s" % (s.get("publisher", ""), s.get("title", ""),
                                     (" (" + s["url"] + ")") if s.get("url") else ""))
                for key, s in DB["sources"].items())
