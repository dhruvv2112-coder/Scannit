"""Minimal, dependency-free GLB writer/reader for point clouds and triangle meshes.

Coordinates: input is the scene frame (right-handed, Z-up, meters). glTF is Y-up, so export applies
ZUP_TO_YUP: (x, y, z) -> (x, z, -y). The 4x4 is recorded in the artifact manifest and is invertible
(YUP_TO_ZUP = its transpose).
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np

ZUP_TO_YUP = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], dtype=np.float64)
YUP_TO_ZUP = ZUP_TO_YUP.T.copy()

_FLOAT, _UBYTE, _UINT = 5126, 5121, 5125
_ARRAY_BUFFER, _ELEMENT_ARRAY_BUFFER = 34962, 34963


def apply(T: np.ndarray, xyz: np.ndarray) -> np.ndarray:
    xyz = np.asarray(xyz, dtype=np.float64).reshape(-1, 3)
    return xyz @ T[:3, :3].T + T[:3, 3]


def _pad(b: bytes, fill: bytes) -> bytes:
    return b + fill * ((4 - len(b) % 4) % 4)


def write_glb(path: Path, xyz_zup, rgb=None, faces=None, extras: dict | None = None) -> dict:
    """Write points (faces=None, glTF mode 0) or triangles (mode 4). Returns Y-up bounds."""
    pos = apply(ZUP_TO_YUP, xyz_zup).astype(np.float32)
    if len(pos) == 0:
        raise ValueError("no vertices to export")
    blobs, views, accessors = [], [], []

    def add(data: bytes, target: int, accessor: dict):
        offset = sum(len(b) for b in blobs)
        blobs.append(_pad(data, b"\x00"))
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data), "target": target})
        accessors.append({**accessor, "bufferView": len(views) - 1})
        return len(accessors) - 1

    attrs = {"POSITION": add(pos.tobytes(), _ARRAY_BUFFER, {
        "componentType": _FLOAT, "count": len(pos), "type": "VEC3",
        "min": pos.min(axis=0).tolist(), "max": pos.max(axis=0).tolist()})}
    if rgb is not None:
        col = np.asarray(rgb, dtype=np.uint8).reshape(-1, 3)
        col4 = np.concatenate([col, np.full((len(col), 1), 255, np.uint8)], axis=1)  # VEC4 keeps 4-byte stride
        attrs["COLOR_0"] = add(col4.tobytes(), _ARRAY_BUFFER, {
            "componentType": _UBYTE, "normalized": True, "count": len(col4), "type": "VEC4"})
    prim = {"attributes": attrs, "mode": 0}
    if faces is not None:
        idx = np.asarray(faces, dtype=np.uint32).reshape(-1)
        prim["indices"] = add(idx.tobytes(), _ELEMENT_ARRAY_BUFFER, {
            "componentType": _UINT, "count": len(idx), "type": "SCALAR"})
        prim["mode"] = 4
    gltf = {
        "asset": {"version": "2.0", "generator": "scannit-recon", "extras": extras or {}},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "reconstruction"}],
        "meshes": [{"primitives": [prim]}],
        "buffers": [{"byteLength": sum(len(b) for b in blobs)}],
        "bufferViews": views, "accessors": accessors,
    }
    js = _pad(json.dumps(gltf, separators=(",", ":"), sort_keys=True).encode(), b" ")
    binc = b"".join(blobs)
    total = 12 + 8 + len(js) + 8 + len(binc)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
        f.write(struct.pack("<II", len(binc), 0x004E4942) + binc)
    return {"min": pos.min(axis=0).tolist(), "max": pos.max(axis=0).tolist()}


def read_glb(path: Path) -> tuple[dict, np.ndarray]:
    """Return (gltf json, POSITION array in glTF Y-up). Raises ValueError on a malformed file."""
    data = Path(path).read_bytes()
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67 or version != 2 or length != len(data):
        raise ValueError("not a valid GLB 2.0 file")
    jlen, jtype = struct.unpack_from("<II", data, 12)
    if jtype != 0x4E4F534A:
        raise ValueError("first GLB chunk is not JSON")
    gltf = json.loads(data[20:20 + jlen])
    blen, btype = struct.unpack_from("<II", data, 20 + jlen)
    if btype != 0x004E4942:
        raise ValueError("second GLB chunk is not BIN")
    binc = data[28 + jlen:28 + jlen + blen]
    acc = gltf["accessors"][gltf["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
    view = gltf["bufferViews"][acc["bufferView"]]
    pos = np.frombuffer(binc, np.float32, acc["count"] * 3, view["byteOffset"]).reshape(-1, 3)
    return gltf, pos
