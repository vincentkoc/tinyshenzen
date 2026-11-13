#!/usr/bin/env python3
"""CPU tests for tiling, feathering, and dataset prep. No GPU, no API calls."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import prepare_dataset
import tiles as T


class TilesForTest(unittest.TestCase):
    def test_small_image_is_one_tile(self):
        self.assertEqual(T.tiles_for(800, 600, 1024, 256), [T.Tile(0, 0, 800, 600)])

    def test_futian_counts(self):
        ts = T.tiles_for(23500, 11175, 1024, 256)
        self.assertEqual(len(ts), 465)
        self.assertEqual(ts[0], T.Tile(0, 0, 1024, 1024))
        self.assertEqual(ts[-1], T.Tile(22476, 10151, 1024, 1024))
        xs = {t.x for t in ts}
        ys = {t.y for t in ts}
        self.assertEqual(len(xs), 31)
        self.assertEqual(len(ys), 15)

    def test_feather_is_one_in_the_middle_and_zero_at_the_edge(self):
        t = T.Tile(0, 0, 1024, 1024)
        self.assertAlmostEqual(T.edge_weight(512, 512, t, 256), 1.0)
        self.assertAlmostEqual(T.edge_weight(0.0, 512, t, 256), 0.0)
        mid_hem = T.edge_weight(128, 512, t, 256)
        self.assertTrue(0.4 < mid_hem < 0.6, mid_hem)


class PrepareDatasetTest(unittest.TestCase):
    def test_pairs_become_musubi_edit_layout(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            teacher = root / "teacher"
            (teacher / "procedural").mkdir(parents=True)
            (teacher / "restyled").mkdir()
            Image.new("RGB", (1024, 1024), (40, 80, 60)).save(teacher / "procedural" / "01-civic-center.png")
            Image.new("RGB", (1024, 1024), (50, 90, 80)).save(teacher / "restyled" / "01-civic-center.png")
            manifest = {
                "look": "pixelsg",
                "windows": [{"id": "01-civic-center", "lon": 114.06, "lat": 22.54, "tags": ["civic"]}],
            }
            man_path = root / "teacher-manifest.json"
            man_path.write_text(json.dumps(manifest))
            dest = root / "dataset"
            n = prepare_dataset.prepare(teacher, dest, man_path)
            self.assertEqual(n, 1)
            self.assertTrue((dest / "images" / "01-civic-center.png").exists())
            self.assertTrue((dest / "controls" / "01-civic-center.png").exists())
            caption = (dest / "images" / "01-civic-center.txt").read_text()
            self.assertIn("isometric pixel art", caption.lower())
            self.assertNotRegex(caption.lower(), r"yadong|style reference image|upload this image")
            toml = (dest / "dataset.toml").read_text()
            self.assertIn("control_directory", toml)
            self.assertIn("control_resolution = [1024, 1024]", toml)
            self.assertIn(str((dest / "images").resolve()), toml)
            self.assertIn(str((dest / "controls").resolve()), toml)

if __name__ == "__main__":
    unittest.main()
