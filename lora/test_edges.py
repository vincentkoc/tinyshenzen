"""Tests for --polish-edges (edges.py): find and fix the restyle's broken edges for free."""

from __future__ import annotations

import unittest

import numpy as np

import edges as E
import tone as TN
from test_gaps import city, restyle

WATER = (36, 88, 74)  # the original's water
RWATER = (47, 109, 88)  # the restyle's water


def identity_style() -> dict:
    """A house style that maps the original's Lab onto itself."""
    rng = np.random.default_rng(0)
    lab = TN.rgb_to_lab(rng.integers(0, 256, (4000, 1, 3)).astype(np.uint8)).reshape(-1, 3)
    return TN.fit_house_style(lab, lab)


def washed(rgb: np.ndarray, a: float) -> np.ndarray:
    return np.clip(np.rint(rgb * (1 - a) + np.array(E.FOG) * a), 0, 255).astype(np.uint8)


class ClassesTest(unittest.TestCase):
    def test_palette_classes(self):
        o = np.zeros((4, 4, 3), np.uint8)
        o[0] = E.GROUND
        o[1] = WATER
        o[2] = (80, 78, 80)  # asphalt
        o[3] = (0, 0, 0)  # fog dither
        c = E.orig_classes(o)
        self.assertTrue(c["ground"][0].all() and c["water"][1].all() and c["road"][2].all() and c["black"][3].all())
        self.assertTrue((c["content"] == c["road"]).all())  # roads are content; blank, water, fog are not

    def test_undither_removes_black_dots_only(self):
        o = np.zeros((32, 32, 3), np.uint8)
        o[:] = (93, 122, 60)  # forest
        o[::4, ::4] = 0  # dither
        u = E.undither(o)
        self.assertTrue((u == (93, 122, 60)).all())
        solid = np.zeros((32, 32, 3), np.uint8)
        self.assertTrue((E.undither(solid) == 0).all())  # solid fog: nothing to recover


class DetectTest(unittest.TestCase):
    def setUp(self):
        self.style = identity_style()

    def test_washed_content_scores_as_veil(self):
        o = city(256, 256)
        good = E.scores(E.cell_fields(o, o), self.style)
        bad = E.scores(E.cell_fields(washed(o, 0.75), o), self.style)
        self.assertLess(float(good["veil"].mean()), 0.15)
        self.assertGreater(float(bad["veil"].mean()), 0.5)

    def test_cut_road_scores_as_road(self):
        o = np.empty((256, 256, 3), np.uint8)
        o[:] = (93, 122, 60)
        o[112:144] = (80, 78, 80)  # a motorway across
        r = o.copy()
        r[:, 128:] = (93, 122, 60)  # the restyle stops it half way
        s = E.scores(E.cell_fields(r, o), self.style)["road"]
        self.assertLess(float(s[7:9, :7].max()), 0.2)
        self.assertGreater(float(s[7:9, 9:].mean()), 0.5)

    def test_bridge_over_water_is_phantom_but_a_pier_is_not(self):
        o = np.empty((256, 256, 3), np.uint8)
        o[:] = WATER
        o[40:56, :] = E.GROUND  # a pier the original draws
        r = np.empty_like(o)
        r[:] = RWATER
        r[40:56, :] = (110, 110, 112)  # the restyle draws the pier grey: fine
        r[160:176, :] = (110, 110, 112)  # a bridge over open water: phantom
        F = E.cell_fields(r, o)
        s = E.scores(F, self.style)["phantom"]
        self.assertLess(float(s[2:4].max()), 0.1)
        self.assertGreater(float(s[10:11].mean()), 0.5)

    def test_band_covers_edge_and_district_rims(self):
        field = np.full((64, 128), -864.0, np.float32)
        field[:, 112:] = 100.0  # outside the coverage on the right
        audit, outer = E.band_weight(field, (64, 128), [(0, 0, 320, 1024)])
        self.assertTrue(audit[:, 120].all())  # edge band
        self.assertTrue(audit[10, 20])  # district rim at x=320 px (cell 20)
        self.assertFalse(audit[10, 70])  # deep inside, away from both
        self.assertTrue(outer[:, 120].all() and not outer[:, 10].any())


class FixTest(unittest.TestCase):
    def setUp(self):
        self.style = identity_style()

    def test_veil_fill_restores_content_and_verifies(self):
        o = city(384, 384, seed=3)
        cur = restyle(o)
        cur[128:256, 128:256] = washed(cur[128:256, 128:256], 0.8)
        region = np.zeros((24, 24), bool)
        region[8:16, 8:16] = True
        score = region.astype(np.float32)
        good = ~region
        fill, alpha, used = E.fix_region("veil", cur, o, self.style, region, score, good, [])
        out = E.blend(cur, fill, alpha)
        ok, before, after = E.verify("veil", cur, out, o, self.style, region)
        self.assertTrue(ok, (before, after))
        self.assertEqual(used[0]["source"], "house")

    def test_phantom_inpaint_leaves_water(self):
        o = np.empty((256, 256, 3), np.uint8)
        o[:] = WATER
        cur = np.empty_like(o)
        cur[:] = RWATER
        cur[120:136, 32:224] = (110, 110, 112)
        region = np.zeros((16, 16), bool)
        region[6:10, 1:15] = True
        fill, alpha, _ = E.fix_region("phantom", cur, o, self.style, region, region.astype(np.float32), ~region, [])
        out = E.blend(cur, fill, alpha)
        self.assertLess(int(np.abs(out[120:136, 40:216].astype(int) - RWATER).max()), 6)
        ok, before, after = E.verify("phantom", cur, out, o, self.style, region)
        self.assertTrue(ok, (before, after))

    def test_fix_that_does_not_help_is_dropped(self):
        o = city(256, 256, seed=5)
        cur = restyle(o)  # nothing wrong
        region = np.zeros((16, 16), bool)
        region[4:12, 4:12] = True
        ok, _, _ = E.verify("veil", cur, cur, o, self.style, region)
        self.assertFalse(ok)

    def test_inpaint_only_touches_mask(self):
        rng = np.random.default_rng(1)
        rgb = rng.integers(0, 255, (64, 64, 3)).astype(np.uint8)
        m = np.zeros((64, 64), bool)
        m[20:30, 20:30] = True
        out = E.inpaint(rgb, m)
        self.assertTrue((out[~m] == rgb[~m]).all())


if __name__ == "__main__":
    unittest.main()
