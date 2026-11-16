"""Tests for coverage.py: fading the pyramids out along a coverage outline."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

import coverage as CV

FOG = (0xE9, 0xE6, 0xDF)
TILE = 64


def no_noise(tx, ty, *, amplitude, period, tile=TILE):
    return np.zeros((tile, tile), dtype=np.float32)


def almost_empty(arr, fog):
    return float(np.abs(arr.astype(np.int16) - np.array(fog, dtype=np.int16)).mean()) < 1.5


def save(im, path):
    im.save(path, "WEBP", lossless=True)


class OutlineTest(unittest.TestCase):
    def test_signed_distance(self):
        square = np.array([[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]], dtype=float)
        o = CV.Outline([square])
        d = o.signed(np.array([50.0, 150.0, 50.0]), np.array([50.0, 50.0, 10.0]))
        np.testing.assert_allclose(d, [-50.0, 50.0, -10.0], atol=1e-6)

    def test_projection_matches_viewer(self):
        geo = {"lon0": 114.0683, "lat0": 22.5455, "betaDeg": 0, "k": 2}
        [ring] = CV.project_rings([np.array([[114.0683, 22.5455]])], geo, (-10.0, -20.0))
        np.testing.assert_allclose(ring[0], [10.0, 20.0], atol=1e-9)
        # one metre north moves k px right and k/2 px up
        [north] = CV.project_rings([np.array([[114.0683, 22.5455 + 1 / 111195.08]])], geo, (0.0, 0.0))
        np.testing.assert_allclose(north[0], [2.0, -1.0], atol=1e-3)

    def test_load_rings(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.geojson"
            p.write_text(json.dumps({"type": "Feature", "geometry": {
                "type": "MultiPolygon", "coordinates": [[[[0, 0], [1, 0], [1, 1], [0, 0]]]]}}))
            [ring] = CV.load_rings(p)
            self.assertEqual(ring.shape, (4, 2))


class PolishTest(unittest.TestCase):
    def run_pass(self, root: Path):
        # the outline keeps x < 96 px: tile 0 inside, tile 1 crossing, tiles 2 and 3 outside
        outline = CV.Outline([np.array([[-1000, -1000], [96, -1000], [96, 1000], [-1000, 1000], [-1000, -1000]], float)])
        return CV.polish_coverage(
            root / "tiles", {"cols": 4, "rows": 1}, outline, backup=root / "backup", fog=FOG,
            noise=no_noise, almost_empty=almost_empty, save=save, tile=TILE, inset=4, fade=20, noise_amp=0,
            log=lambda m: None,
        )

    def test_fades_drops_and_reruns_from_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tiles" / "0").mkdir(parents=True)
            rng = np.random.default_rng(1)
            originals = {}
            for x in range(4):
                arr = rng.integers(0, 120, (TILE, TILE, 3), dtype=np.uint8)
                originals[x] = arr
                save(Image.fromarray(arr), root / "tiles" / "0" / f"{x}_0.webp")
            present, changed, dropped = self.run_pass(root)
            self.assertEqual(present, {(0, 0), (1, 0)})
            self.assertEqual(changed, {(1, 0)})
            self.assertEqual(dropped, {(2, 0), (3, 0)})
            faded = np.asarray(Image.open(root / "tiles" / "0" / "1_0.webp").convert("RGB"))
            np.testing.assert_array_equal(faded[:, :20], originals[1][:, :20])  # inside the inset
            self.assertTrue(np.all(faded[:, 56:] == np.array(FOG, dtype=np.uint8)))
            for x in (1, 2, 3):
                self.assertTrue((root / "backup" / "0" / f"{x}_0.webp").exists())
            self.assertFalse((root / "backup" / "0" / "0_0.webp").exists())
            # a re-run starts from the backup and lands on the same tiles
            again = self.run_pass(root)
            self.assertEqual(again[0], present)
            self.assertEqual(again[2], dropped)
            np.testing.assert_array_equal(
                np.asarray(Image.open(root / "tiles" / "0" / "1_0.webp").convert("RGB")), faded)


if __name__ == "__main__":
    unittest.main()
