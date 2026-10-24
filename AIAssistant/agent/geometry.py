# Geometry analysis of a scene node for orientation and 3D/5D decisions.
# Works in printer coordinates (Z up, mm). Main thread only (reads scene data).

import math

import numpy

OVERHANG_COS = math.cos(math.radians(45))  # faces steeper than 45° from vertical need support
ANGLE_BINS = 72

# Which rotation puts a face that points in direction d onto the build plate (d becomes -Z).
_DOWN_ROTATIONS = [
    ("-Z (как сейчас)", numpy.array([0, 0, -1.0]), {}),
    ("+Z", numpy.array([0, 0, 1.0]), {"x": 180}),
    ("+X", numpy.array([1.0, 0, 0]), {"y": 90}),
    ("-X", numpy.array([-1.0, 0, 0]), {"y": -90}),
    ("+Y", numpy.array([0, 1.0, 0]), {"x": -90}),
    ("-Y", numpy.array([0, -1.0, 0]), {"x": 90}),
]


def describe_rotation(rotation):
    """{'x': 90} -> 'поворот на 90° вокруг оси X' (human-readable, no braces)."""
    if not rotation:
        return "без поворота"
    parts = ["%+g° вокруг оси %s" % (angle, axis.upper()) for axis, angle in sorted(rotation.items()) if angle]
    return "поворот на " + " и ".join(p.lstrip("+") for p in parts) if parts else "без поворота"


def _triangles(node):
    mesh = node.getMeshData().getTransformed(node.getWorldTransformation())
    verts = mesh.getVertices()
    if mesh.hasIndices():
        tris = verts[mesh.getIndices()]
    else:
        tris = verts[:len(verts) - len(verts) % 3].reshape(-1, 3, 3)
    # scene (x, y_up, z) -> printer (x, -z, y)
    tris = tris[:, :, [0, 2, 1]].astype(numpy.float64)
    tris[:, :, 1] *= -1
    return tris


def _face_data(tris):
    cross = numpy.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    double_area = numpy.linalg.norm(cross, axis=1)
    valid = double_area > 1e-12
    normals = numpy.zeros_like(cross)
    normals[valid] = cross[valid] / double_area[valid][:, None]
    return normals, double_area / 2.0


def _overhang_area(tris, normals, areas, down):
    # Faces facing "down" (towards the plate) more than 45°, excluding the ones lying on the plate.
    heights = -(tris.mean(axis=1).dot(down))           # height above the plate along -down
    floor = heights.min()
    facing = normals.dot(down) > OVERHANG_COS
    on_plate = heights < floor + 0.3
    over = facing & ~on_plate
    contact = facing & on_plate
    return float(areas[over].sum()), float(areas[contact].sum())


def _axis_symmetry(points, axis_index):
    # Circularity of the outline around an axis through the bounding-box centre,
    # and the radius of a central bore (no material near the axis).
    others = [i for i in range(3) if i != axis_index]
    center = (points.min(axis=0) + points.max(axis=0)) / 2.0
    a = points[:, others[0]] - center[others[0]]
    b = points[:, others[1]] - center[others[1]]
    radius = numpy.hypot(a, b)
    angle_bin = ((numpy.arctan2(b, a) + math.pi) / (2 * math.pi) * ANGLE_BINS).astype(int) % ANGLE_BINS
    max_r = numpy.zeros(ANGLE_BINS)
    min_r = numpy.full(ANGLE_BINS, numpy.inf)
    numpy.maximum.at(max_r, angle_bin, radius)
    numpy.minimum.at(min_r, angle_bin, radius)
    filled = max_r > 0
    if filled.sum() < ANGLE_BINS * 0.8:
        return 0.0, 0.0, float(radius.max())
    outer = max_r[filled]
    circularity = 1.0 - float(outer.std() / outer.mean()) if outer.mean() > 0 else 0.0
    bore = float(numpy.median(min_r[filled]))
    return circularity, bore, float(outer.mean())


def analyze_node(node):
    tris = _triangles(node)
    normals, areas = _face_data(tris)
    points = tris.reshape(-1, 3)
    size = points.max(axis=0) - points.min(axis=0)

    # Signed volume (divergence theorem)
    volume = float(numpy.einsum("ij,ij->i", tris[:, 0], numpy.cross(tris[:, 1], tris[:, 2])).sum() / 6.0)

    orientations = []
    for label, down, rotation in _DOWN_ROTATIONS:
        over, contact = _overhang_area(tris, normals, areas, down)
        orientations.append({"face_on_plate": label, "rotate": rotation, "rotate_text": describe_rotation(rotation),
                             "overhang_area_mm2": round(over, 1), "contact_area_mm2": round(contact, 1)})
    best = min(orientations, key=lambda o: (o["overhang_area_mm2"], -o["contact_area_mm2"]))

    axes = []
    for index, name in enumerate(["X", "Y", "Z"]):
        circularity, bore, outer = _axis_symmetry(points, index)
        axes.append({"axis": name, "circularity": round(circularity, 3),
                     "outer_diameter_mm": round(outer * 2, 2),
                     "central_bore_diameter_mm": round(bore * 2, 2) if bore > 0.5 else 0.0,
                     "length_along_axis_mm": round(float(size[index]), 2)})
    rot_axis = max(axes, key=lambda a: a["circularity"])
    is_rotational = rot_axis["circularity"] >= 0.93

    to_vertical = {"X": {"y": 90}, "Y": {"x": 90}, "Z": {}}[rot_axis["axis"]]
    hints = []
    if is_rotational:
        hints.append("Деталь близка к телу вращения вокруг оси %s (круглость %.2f). Для 5D ось вращения детали "
                     "должна быть вертикальной (поворот %s) и проходить через центр стола (move_object x=0, y=0)."
                     % (rot_axis["axis"], rot_axis["circularity"],
                        describe_rotation(to_vertical) if to_vertical else "не нужен"))
        if rot_axis["central_bore_diameter_mm"] > 0:
            hints.append("Есть центральное отверстие ~%.1f мм вдоль оси вращения."
                         % rot_axis["central_bore_diameter_mm"])
    else:
        hints.append("Деталь не является телом вращения — естественный выбор режим 3D (classic).")
    hints.append("Минимум нависаний в 3D: на стол гранью %s (%s), нависания %.0f мм², опора %.0f мм²."
                 % (best["face_on_plate"], describe_rotation(best["rotate"]),
                    best["overhang_area_mm2"], best["contact_area_mm2"]))

    return {
        "name": node.getName(),
        "size_mm": {"x": round(float(size[0]), 2), "y": round(float(size[1]), 2), "z": round(float(size[2]), 2)},
        "volume_cm3": round(abs(volume) / 1000.0, 2),
        "surface_area_cm2": round(float(areas.sum()) / 100.0, 2),
        "triangles": int(len(tris)),
        "rotational_symmetry": {"likely": is_rotational, "best_axis": rot_axis, "all_axes": axes,
                                "rotate_to_make_axis_vertical": to_vertical,
                                "rotate_to_make_axis_vertical_text": describe_rotation(to_vertical)},
        "orientations_3d": orientations,
        "best_orientation_3d": best,
        "hints": hints,
    }
