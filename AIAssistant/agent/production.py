# Production helpers: part cost estimate, technological card (HTML) and reusable recipes.

import datetime
import html
import json
import os
import re

# Approximate filament prices, rubles per kg (Russian market, 2026). Checked by id prefix, then by type.
PRICE_BY_ID_PREFIX = [
    ("contifiber", 12000), ("fiberpart_", 6000), ("enduse_", 3500), ("sealant_", 4000),
    ("metalcast_", 9000), ("proto_", 1800),
]
PRICE_BY_TYPE = {
    "PLA": 1600, "PETG": 1800, "CPE": 2000, "ABS": 1700, "HIPS": 1700, "PC": 3500, "NYLON": 4000, "PA": 4000,
    "PP": 3200, "TPU": 3500, "TPE": 3500, "PVA": 6000,
}
DEFAULT_PRICE = 3000


def material_price_per_kg(material_id, material_type):
    material_id = (material_id or "").lower()
    for prefix, price in PRICE_BY_ID_PREFIX:
        if material_id.startswith(prefix):
            return price
    material_type = (material_type or "").upper()
    for key, price in PRICE_BY_TYPE.items():
        if key in material_type:
            return price
    return DEFAULT_PRICE


def estimate_cost(grams, seconds, price_per_kg, machine_rate_per_hour):
    material = grams / 1000.0 * price_per_kg
    machine = seconds / 3600.0 * machine_rate_per_hour
    return {"material_rub": round(material), "machine_time_rub": round(machine),
            "total_rub": round(material + machine),
            "assumptions": "материал %d ₽/кг, машинное время %d ₽/ч (оценка)" % (price_per_kg, machine_rate_per_hour)}


def safe_name(name):
    return re.sub(r'[<>:"/\\|?*]', "_", str(name)).strip() or "model"


# ---------------- recipes ----------------

def recipes_dir(output_dir):
    path = os.path.join(output_dir, "recipes")
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def save_recipe(output_dir, recipe):
    path = os.path.join(recipes_dir(output_dir), safe_name(recipe["name"]) + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(recipe, f, ensure_ascii=False, indent=2, default=str)
    return path


def list_recipes(output_dir):
    folder = recipes_dir(output_dir)
    result = []
    for file_name in sorted(os.listdir(folder)):
        if file_name.endswith(".json"):
            try:
                with open(os.path.join(folder, file_name), encoding="utf-8") as f:
                    recipe = json.load(f)
                result.append({"name": recipe.get("name"), "description": recipe.get("description"),
                               "printing_mode": recipe.get("printing_mode"), "material_id": recipe.get("material_id"),
                               "quality_type": recipe.get("quality_type"), "created": recipe.get("created")})
            except (ValueError, OSError):
                continue
    return result


def load_recipe(output_dir, name):
    path = os.path.join(recipes_dir(output_dir), safe_name(name) + ".json")
    if not os.path.isfile(path):
        raise ValueError("Рецепт «%s» не найден, вызовите list_recipes" % name)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------- technological card ----------------

_CARD_CSS = """
body{font-family:"Segoe UI",Arial,sans-serif;color:#13232c;margin:0;background:#eef3f5}
.page{max-width:900px;margin:24px auto;background:#fff;padding:32px 40px;border-radius:10px;
box-shadow:0 2px 12px rgba(0,0,0,.08)}
h1{margin:0 0 4px;font-size:24px}h2{font-size:16px;margin:26px 0 8px;color:#1b5f7c;
border-bottom:2px solid #a4c81c;padding-bottom:4px}
.meta{color:#5b7380;font-size:13px}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{border:1px solid #d5e1e6;padding:6px 10px;text-align:left;vertical-align:top}
th{background:#f3f8fa;width:34%}
.grid td:first-child{font-family:Consolas,monospace;color:#1b5f7c}
.kpi{display:flex;gap:12px;flex-wrap:wrap}.kpi div{background:#f3f8fa;border-radius:8px;padding:10px 16px}
.kpi b{display:block;font-size:20px}
ul{margin:6px 0;padding-left:20px}
@media print{body{background:#fff}.page{box-shadow:none;margin:0}}
"""


def _rows(pairs):
    return "".join("<tr><th>%s</th><td>%s</td></tr>" % (html.escape(str(k)), html.escape(str(v))) for k, v in pairs)


def build_tech_card(data):
    e = html.escape
    status = data["status"]
    info = data.get("print_info") or {}
    cost = data.get("cost") or {}
    parts = ["<!doctype html><html lang='ru'><head><meta charset='utf-8'>",
             "<title>Техкарта — %s</title><style>%s</style></head><body><div class='page'>" % (e(data["model_name"]), _CARD_CSS),
             "<h1>Технологическая карта: %s</h1>" % e(data["model_name"]),
             "<div class='meta'>Аддитивное производство (FDM) · STE Slicer · AI Assistant · %s</div>" % e(data["date"])]
    if data.get("summary"):
        parts.append("<h2>Назначение и условия работы</h2><p>%s</p>" % e(data["summary"]))
    parts.append("<h2>Оборудование и материал</h2><table>%s</table>" % _rows([
        ("Принтер", status.get("printer")),
        ("Режим печати", status.get("printing_mode_description") or status.get("printing_mode")),
        ("Материал", "%s (%s)" % (status.get("material"), status.get("material_id"))),
        ("Профиль качества", status.get("quality")),
    ]))
    requirements = data.get("requirements") or []
    if requirements:
        parts.append("<h2>Требования ТЗ → технологические решения</h2><table><tr><th>Требование</th><th>Решение</th></tr>")
        for item in requirements:
            parts.append("<tr><td>%s</td><td>%s</td></tr>" % (e(str(item.get("requirement", ""))), e(str(item.get("decision", "")))))
        parts.append("</table>")
    changes = data.get("changes") or {}
    rows = [(k, v) for k, v in sorted((changes.get("global") or {}).items())]
    for position, values in sorted((changes.get("extruders") or {}).items()):
        rows += [("%s (экструдер %s)" % (k, position), v) for k, v in sorted(values.items())]
    if rows:
        parts.append("<h2>Изменения относительно профиля</h2><table class='grid'><tr><th>Настройка</th><th>Значение</th></tr>")
        parts.append("".join("<tr><td>%s</td><td>%s</td></tr>" % (e(str(k)), e(str(v))) for k, v in rows))
        parts.append("</table>")
    objects = data.get("objects") or []
    if objects:
        obj = objects[0]
        parts.append("<h2>Ориентация и размещение</h2><table>%s</table>" % _rows([
            ("Габариты X × Y × Z, мм", "%s × %s × %s" % (obj["size_mm"]["x"], obj["size_mm"]["y"], obj["size_mm"]["z"])),
            ("Центр детали на столе, мм", "X %s, Y %s" % (obj["center_mm"]["x"], obj["center_mm"]["y"])),
            ("Обоснование", data.get("orientation_note") or "—"),
        ]))
    if info:
        parts.append("<h2>Результат слайсинга</h2><div class='kpi'>")
        parts.append("<div>Время печати<b>%s</b></div>" % e(str(info.get("print_time", "—"))))
        parts.append("<div>Материал<b>%s г</b></div>" % e(str(info.get("material_total_g", "—"))))
        if cost:
            parts.append("<div>Себестоимость<b>%s ₽</b></div>" % e(str(cost.get("total_rub"))))
        parts.append("</div>")
        if cost:
            parts.append("<p class='meta'>%s</p>" % e(cost.get("assumptions", "")))
    risks = data.get("risks") or []
    if risks:
        parts.append("<h2>Риски и контроль качества</h2><ul>%s</ul>" % "".join("<li>%s</li>" % e(str(r)) for r in risks))
    if data.get("files"):
        parts.append("<h2>Файлы</h2><ul>%s</ul>" % "".join("<li>%s</li>" % e(f) for f in data["files"]))
    parts.append("</div></body></html>")
    return "".join(parts)


def today():
    return datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
