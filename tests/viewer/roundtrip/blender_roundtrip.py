"""XR-007 (early): GLB round-trip through Blender, compared with an independent GLB parser.

Usage (Blender's Python module, e.g. `uv venv -p 3.11 && uv pip install bpy`):
    python tests/viewer/roundtrip/blender_roundtrip.py <out_dir> <in1.glb> [<in2.glb> ...]

For each input: factory-reset Blender, import the GLB, export it again as GLB (+Y up), then compare
original vs exported in glTF space: node names, primitive modes, triangle/point counts, world-space
bounds per named node and overall, PBR factors per material, asset.extras. Writes <out_dir>/report.json
and the exported files. Exit code 1 if any check fails. Nothing here judges reconstruction accuracy.
"""
from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path

TOL_M = 1e-5


# ---------------------------------------------------------------- independent GLB reader (no bpy)
def read_glb(path: Path):
    data = path.read_bytes()
    magic, version, length = struct.unpack_from("<III", data, 0)
    assert magic == 0x46546C67 and version == 2 and length == len(data), "not a GLB 2.0"
    jlen, _ = struct.unpack_from("<II", data, 12)
    gltf = json.loads(data[20:20 + jlen])
    if len(data) < 28 + jlen:  # JSON-only GLB (e.g. every mesh was dropped on export)
        return gltf, b""
    blen, _ = struct.unpack_from("<II", data, 20 + jlen)
    return gltf, data[28 + jlen:28 + jlen + blen]


def accessor(gltf, binc, idx):
    a = gltf["accessors"][idx]
    v = gltf["bufferViews"][a["bufferView"]]
    comps = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
    fmt = {5126: "f", 5125: "I", 5123: "H", 5121: "B"}[a["componentType"]]
    size = struct.calcsize(fmt)
    stride = v.get("byteStride") or size * comps
    base = v.get("byteOffset", 0) + a.get("byteOffset", 0)
    return [struct.unpack_from("<" + fmt * comps, binc, base + i * stride) for i in range(a["count"])]


def qmul(q, r):
    x1, y1, z1, w1 = q; x2, y2, z2, w2 = r
    return (w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2, w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2, w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2)


def qrot(q, p):
    x, y, z, w = qmul(qmul(q, (p[0], p[1], p[2], 0.0)), (-q[0], -q[1], -q[2], q[3]))
    return (x, y, z)


def node_mat(n):
    if "matrix" in n:
        m = n["matrix"]  # column-major
        return lambda p: tuple(m[i] * p[0] + m[4 + i] * p[1] + m[8 + i] * p[2] + m[12 + i] for i in range(3))
    t = n.get("translation", [0, 0, 0]); r = n.get("rotation", [0, 0, 0, 1]); s = n.get("scale", [1, 1, 1])
    return lambda p: tuple(a + b for a, b in zip(qrot(r, (p[0] * s[0], p[1] * s[1], p[2] * s[2])), t))


def summarise(path: Path):
    gltf, binc = read_glb(path)
    nodes = gltf.get("nodes", [])
    parent = {c: i for i, n in enumerate(nodes) for c in n.get("children", [])}

    def world(i, p):
        while i is not None:
            p = node_mat(nodes[i])(p); i = parent.get(i)
        return p

    per_node, all_lo, all_hi = {}, [math.inf] * 3, [-math.inf] * 3
    tris = pts = 0
    modes, attrs = set(), set()
    for i, n in enumerate(nodes):
        if "mesh" not in n:
            continue
        lo, hi = [math.inf] * 3, [-math.inf] * 3
        for prim in gltf["meshes"][n["mesh"]]["primitives"]:
            mode = prim.get("mode", 4); modes.add(mode); attrs.update(prim["attributes"])
            pos = accessor(gltf, binc, prim["attributes"]["POSITION"])
            if mode == 0:
                pts += len(pos)
            elif mode == 4:
                tris += (gltf["accessors"][prim["indices"]]["count"] if "indices" in prim else len(pos)) // 3
            for p in pos:
                w = world(i, p)
                for k in range(3):
                    lo[k] = min(lo[k], w[k]); hi[k] = max(hi[k], w[k])
        per_node[n.get("name", f"node{i}")] = {"min": lo, "max": hi, "size": [hi[k] - lo[k] for k in range(3)]}
        for k in range(3):
            all_lo[k] = min(all_lo[k], lo[k]); all_hi[k] = max(all_hi[k], hi[k])
    mats = {}
    for m in gltf.get("materials", []):
        pbr = m.get("pbrMetallicRoughness", {})
        mats[m.get("name", "?")] = {"baseColorFactor": pbr.get("baseColorFactor", [1, 1, 1, 1]), "metallicFactor": pbr.get("metallicFactor", 1.0),
                                    "roughnessFactor": pbr.get("roughnessFactor", 1.0), "textures": sorted(k for k in pbr if k.endswith("Texture"))}
    return {"nodes": per_node, "bounds": {"min": all_lo, "max": all_hi}, "triangles": tris, "points": pts, "modes": sorted(modes), "attributes": sorted(attrs),
            "materials": mats, "extras": gltf.get("asset", {}).get("extras"), "generator": gltf.get("asset", {}).get("generator")}


def compare(a, b):
    checks = []
    def chk(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
    dmax = max(abs(x - y) for x, y in zip(a["bounds"]["min"] + a["bounds"]["max"], b["bounds"]["min"] + b["bounds"]["max"]))
    chk("overall bounds equal (glTF Y-up, meters)", dmax <= TOL_M, f"max |Δ| = {dmax:.2e} m")
    chk("node names preserved", set(a["nodes"]) == set(b["nodes"]), f"missing {sorted(set(a['nodes']) - set(b['nodes']))}, added {sorted(set(b['nodes']) - set(a['nodes']))}")
    worst = 0.0
    for name, na in a["nodes"].items():
        nb = b["nodes"].get(name)
        if nb:
            worst = max(worst, *(abs(x - y) for x, y in zip(na["min"] + na["max"], nb["min"] + nb["max"])))
    chk("per-node world bounds equal (position, size, up axis)", worst <= TOL_M, f"max |Δ| = {worst:.2e} m")
    chk("primitive modes preserved", a["modes"] == b["modes"], f"{a['modes']} -> {b['modes']}")
    chk("vertex attributes preserved (e.g. NORMAL, COLOR_0)", set(a["attributes"]) <= set(b["attributes"]), f"{a['attributes']} -> {b['attributes']}")
    chk("triangle count preserved", a["triangles"] == b["triangles"], f"{a['triangles']} -> {b['triangles']}")
    chk("point count preserved", a["points"] == b["points"], f"{a['points']} -> {b['points']}")
    chk("material names preserved", set(a["materials"]) == set(b["materials"]), f"{sorted(a['materials'])} -> {sorted(b['materials'])}")
    pbr_worst, pbr_detail = 0.0, []
    for name, ma in a["materials"].items():
        mb = b["materials"].get(name)
        if not mb:
            continue
        for key in ("metallicFactor", "roughnessFactor"):
            pbr_worst = max(pbr_worst, abs(ma[key] - mb[key]))
        pbr_worst = max(pbr_worst, *(abs(x - y) for x, y in zip(ma["baseColorFactor"], mb["baseColorFactor"])))
        if ma["textures"] != mb["textures"]:
            pbr_detail.append(f"{name}: textures {ma['textures']} -> {mb['textures']}")
    chk("PBR factors preserved (baseColor, metallic, roughness)", pbr_worst <= 1e-6, f"max |Δ| = {pbr_worst:.2e}; " + "; ".join(pbr_detail))
    chk("asset.extras (units/up-axis/provenance) preserved", a["extras"] == b["extras"], f"{a['extras']} -> {b['extras']}")
    return checks


# ---------------------------------------------------------------- Blender import -> export
def blender_roundtrip(src: Path, dst: Path, loose_points: bool):
    import bpy  # noqa: PLC0415
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(src))
    imported = {o.name: (o.type, tuple(round(d, 6) for d in o.dimensions)) for o in bpy.context.scene.objects}
    bpy.ops.export_scene.gltf(filepath=str(dst), export_format="GLB", export_yup=True, export_apply=False, export_extras=True,
                              export_materials="EXPORT", use_mesh_vertices=loose_points)
    return {"blender": bpy.app.version_string, "objects_in_blender": imported, "use_mesh_vertices": loose_points}


def main(argv):
    out = Path(argv[1]); out.mkdir(parents=True, exist_ok=True)
    report, failed = {"tolerance_m": TOL_M, "results": []}, False
    runs = [(Path(p), lp) for p in argv[2:] for lp in ((False, True) if summarise(Path(p))["points"] else (False,))]
    for src, lp in runs:
        dst = out / f"{src.stem}{'.loose-points' if lp else ''}.blender.glb"
        meta = blender_roundtrip(src, dst, lp)
        a, b = summarise(src), summarise(dst)
        checks = compare(a, b)
        failed |= not all(c["ok"] for c in checks)
        report["results"].append({"input": src.name, "output": dst.name, **meta, "checks": checks,
                                  "original": {k: a[k] for k in ("bounds", "triangles", "points", "modes", "generator")},
                                  "exported": {k: b[k] for k in ("bounds", "triangles", "points", "modes", "generator")}})
        report["blender"] = meta["blender"]
    def finite(o):  # JSON has no Infinity/NaN (an empty export has no bounds)
        if isinstance(o, float):
            return o if math.isfinite(o) else None
        if isinstance(o, dict):
            return {k: finite(v) for k, v in o.items()}
        if isinstance(o, list):
            return [finite(v) for v in o]
        return o
    (out / "report.json").write_text(json.dumps(finite(report), indent=2, allow_nan=False) + "\n")
    for r in report["results"]:
        print(f"== {r['input']} -> {r['output']} (Blender {r['blender']}, use_mesh_vertices={r['use_mesh_vertices']})")
        for c in r["checks"]:
            print(f"  {'PASS' if c['ok'] else 'FAIL'}  {c['check']}  [{c['detail']}]")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
