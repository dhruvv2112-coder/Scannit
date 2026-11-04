"""Content checksums and perceptual hashes."""

from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import numpy as np

_CHUNK = 1 << 20


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def dhash(gray: np.ndarray) -> int:
    """64-bit difference hash of a grayscale uint8 image (9x8 horizontal gradient)."""
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    value = 0
    for b in bits:
        value = (value << 1) | int(b)
    return value


_POPCOUNT8 = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def min_hamming(value: int, others: np.ndarray) -> tuple[int, int]:
    """Smallest Hamming distance between `value` and an array of uint64 hashes.

    Returns (distance, index). `others` must be non-empty.
    """
    x = np.bitwise_xor(others, np.uint64(value))
    counts = _POPCOUNT8[x.view(np.uint8).reshape(-1, 8)].sum(axis=1)
    i = int(np.argmin(counts))
    return int(counts[i]), i
