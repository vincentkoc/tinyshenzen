"""Tests for cover_mask.py: the viewer's cells of the edge tiles inside the outline."""

from __future__ import annotations

import base64
import unittest

import numpy as np

import cover_mask as CM
from coverage import Outline


def manifest(cols: int, rows: int, present: list[tuple[int, int]]) -> dict:
    bits = bytearray((cols * rows + 7) // 8)
    for tx, ty in present:
        i = tx + ty * cols
        bits[i >> 3] |= 1 << (i & 7)
    return {
        "tileSize": 512,
        "levels": [{"z": 0, "cols": cols, "rows": rows, "tiles": base64.b64encode(bytes(bits)).decode()}],
    }


def unpack(b64: str, cells: int) -> np.ndarray:
    raw = base64.b64decode(b64)
    flat = [(raw[i >> 3] >> (i & 7)) & 1 for i in range(cells * cells)]
    return np.array(flat, dtype=bool).reshape(cells, cells)


class CoverMaskTest(unittest.TestCase):
    def test_pack_is_row_major_lsb_first(self):
        inside = np.zeros((4, 4), dtype=bool)
        inside[0, 0] = True  # bit 0
        inside[1, 1] = True  # bit 5
        self.assertEqual(base64.b64decode(CM.pack_cells(inside)), bytes([0b00100001, 0]))

    def test_only_tiles_the_outline_crosses_are_listed(self):
        # outline: x from 0 to 1280 (two and a half tiles), the full height of a 4×1 grid
        ring = np.array([[0, -100], [1280, -100], [1280, 612], [0, 612], [0, -100]], dtype=float)
        mask = CM.cover_mask(Outline([ring]), manifest(4, 1, [(0, 0), (1, 0), (2, 0), (3, 0)]), cells=8)
        self.assertNotIn("0_0", mask)  # well inside: whole
        self.assertIn("2_0", mask)  # crossed in its middle
        cells = unpack(mask["2_0"], 8)
        self.assertTrue(cells[:, :3].all())  # the left of 1280 - 1024 = 256 px, half a cell in
        self.assertFalse(cells[:, 4:].any())
        # the last tile lies wholly outside: listed with nothing inside
        self.assertFalse(unpack(mask["3_0"], 8).any())

    def test_missing_tiles_are_left_out(self):
        ring = np.array([[0, -100], [700, -100], [700, 612], [0, 612], [0, -100]], dtype=float)
        mask = CM.cover_mask(Outline([ring]), manifest(3, 1, [(0, 0)]), cells=8)
        self.assertEqual(set(mask), set())


if __name__ == "__main__":
    unittest.main()
