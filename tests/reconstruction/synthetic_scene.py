"""Render a synthetic room corner with known metric geometry and camera poses.

Three textured planes (floor + two walls) meeting at the world origin, Z-up, meters.
Cameras orbit the corner looking at it. Ground truth (intrinsics, poses, plane geometry)
is written to ground_truth.json so later tickets (REC-004 scale/checkpoints) can measure
errors against known distances.

Usage: python tests/reconstruction/synthetic_scene.py OUT_DIR [N_VIEWS]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

SEED = 20261103
W, H = 800, 600
FX = FY = 640.0
CX, CY = W / 2, H / 2
K = np.array([[FX, 0, CX], [0, FY, CY], [0, 0, 1]], dtype=np.float64)
TEX_PX = 1200

# name, origin, u axis, v axis, (size_u_m, size_v_m)
PLANES = [
    ("floor", (0, 0, 0), (1, 0, 0), (0, 1, 0), (2.4, 2.4)),
    ("wall_x0", (0, 0, 0), (0, 1, 0), (0, 0, 1), (2.4, 2.0)),
    ("wall_y0", (0, 0, 0), (1, 0, 0), (0, 0, 1), (2.4, 2.0)),
]


def _texture(rng: np.random.Generator) -> np.ndarray:
    img = np.zeros((TEX_PX, TEX_PX, 3), np.float32)
    for cell in (3, 9, 27, 81):
        small = rng.random((TEX_PX // cell + 1, TEX_PX // cell + 1, 3), dtype=np.float32)
        img += cv2.resize(small, (TEX_PX + cell, TEX_PX + cell), interpolation=cv2.INTER_NEAREST)[:TEX_PX, :TEX_PX]
    return (img / 4 * 255).clip(0, 255).astype(np.uint8)


def look_at(center: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Return R (cam_from_world) for an OpenCV camera (x right, y down, z forward)."""
    z = target - center
    z /= np.linalg.norm(z)
    up = np.array([0.0, 0.0, 1.0])
    x = np.cross(z, up)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    return np.stack([x, y, z])


def camera_poses(n: int) -> list[tuple[np.ndarray, np.ndarray]]:
    poses = []
    for i, th in enumerate(np.linspace(np.radians(22), np.radians(68), n)):
        c = np.array([3.6 * np.cos(th), 3.6 * np.sin(th), 1.5 + 0.15 * np.sin(i)])
        R = look_at(c, np.array([0.5, 0.5, 0.7]))
        poses.append((R, c))
    return poses


def render(out: Path, n_views: int = 12) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    img_dir = out / "images"
    img_dir.mkdir(exist_ok=True)
    rng = np.random.default_rng(SEED)
    textures = [_texture(rng) for _ in PLANES]
    gt_cams = []
    for idx, (R, c) in enumerate(camera_poses(n_views)):
        t = -R @ c
        canvas = np.full((H, W, 3), 40, np.uint8)
        for (name, o, u, v, (su, sv)), tex in zip(PLANES, textures, strict=True):
            o, u, v = (np.array(a, np.float64) for a in (o, u, v))
            corners = [o, o + su * u, o + su * u + sv * v, o + sv * v]
            if any((R @ p + t)[2] <= 0.1 for p in corners):
                raise ValueError(f"plane {name} crosses behind camera {idx}")
            # texture pixel (s, t) -> world o + s*(su/N)*u + t*(sv/N)*v -> image
            A = np.column_stack([(su / TEX_PX) * u, (sv / TEX_PX) * v, o])
            Hm = K @ np.column_stack([R @ A[:, 0], R @ A[:, 1], R @ A[:, 2] + t])
            warped = cv2.warpPerspective(tex, Hm, (W, H), flags=cv2.INTER_LINEAR)
            mask = cv2.warpPerspective(np.full(tex.shape[:2], 255, np.uint8), Hm, (W, H), flags=cv2.INTER_NEAREST)
            canvas[mask > 0] = warped[mask > 0]
        name = f"view_{idx:03d}.png"
        cv2.imwrite(str(img_dir / name), canvas)
        gt_cams.append({"image": name, "R_cam_from_world": R.tolist(), "center_m": c.tolist()})
    gt = {
        "units": "m",
        "frame": "Z-up world, room corner at origin",
        "intrinsics": {"model": "PINHOLE", "width_px": W, "height_px": H, "params": [FX, FY, CX, CY]},
        "planes": [{"name": n, "origin": o, "u": u, "v": v, "size_m": s} for n, o, u, v, s in PLANES],
        "cameras": gt_cams,
    }
    (out / "ground_truth.json").write_text(json.dumps(gt, indent=2) + "\n")
    return img_dir


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    print(render(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) == 3 else 12))
