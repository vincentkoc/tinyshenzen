"""Tests for --polish-gaps (gaps.py): find holes in the city, fill them for free."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

import gaps as G
import slice_frontend as SF
import tone as TN

BEIGE = (214, 208, 196)


def city(h: int, w: int, seed: int = 0) -> np.ndarray:
    """A synthetic original: gray ground with many sharp-edged coloured blocks."""
    rng = np.random.default_rng(seed)
    a = np.empty((h, w, 3), np.uint8)
    a[:] = (128, 128, 124)
    for _ in range(h * w // 60):
        y, x = rng.integers(0, h), rng.integers(0, w)
        bh, bw = rng.integers(6, 28, 2)
        a[y : y + bh, x : x + bw] = rng.integers(30, 230, 3)
    return a


def restyle(orig: np.ndarray, seed: int = 1) -> np.ndarray:
    """A 'restyle' of it: same geometry, a different palette, a little noise."""
    lab = TN.rgb_to_lab(orig)
    lab[..., 0] = 0.85 * lab[..., 0] + 10.0
    lab[..., 1] *= 1.15
    lab += np.random.default_rng(seed).normal(0, 0.6, lab.shape).astype(np.float32)
    return TN.lab_to_rgb(lab)


def fields(cur: np.ndarray, orig: np.ndarray) -> tuple[dict, np.ndarray, np.ndarray]:
    st = G.cell_structure(cur, orig)
    lab, det = G.cell_detail(cur)
    return st, lab, det


class LabelTest(unittest.TestCase):
    def test_components_are_four_connected(self):
        m = np.zeros((8, 10), bool)
        m[1:3, 1:3] = True  # blob 1
        m[5:7, 6:9] = True  # blob 2
        m[2, 3:6] = True  # joins blob 1 sideways
        m[3:6, 5] = True  # ... and down into blob 2: one L-shaped region
        m[0, 9] = True  # a lone cell
        lab, n = G.label(m)
        self.assertEqual(n, 2)
        self.assertEqual(lab[1, 1], lab[6, 8])
        self.assertNotEqual(lab[0, 9], lab[1, 1])
        self.assertTrue(((lab > 0) == m).all())

    def test_u_shape_merges(self):
        m = np.zeros((5, 5), bool)
        m[0:4, 0] = m[0:4, 4] = m[3, :] = True
        _, n = G.label(m)
        self.assertEqual(n, 1)


class ScoreTest(unittest.TestCase):
    def test_void_and_veil_score_high_and_good_restyle_low(self):
        orig = city(512, 512)
        cur = restyle(orig)
        cur[64:224, 64:224] = BEIGE  # a beige void
        veil = cur[288:448, 288:448].astype(np.float32)
        cur[288:448, 288:448] = (0.25 * veil + 0.75 * np.array(BEIGE)).astype(np.uint8)  # a ghostly veil
        st, lab, det = fields(cur, orig)
        score, void = G.gap_score(lab, det, np.ones(det.shape, np.float32), st)
        c = G.GAP_CELL
        self.assertGreater(float(score[96 // c : 192 // c, 96 // c : 192 // c].mean()), 0.8)
        self.assertGreater(float(void[96 // c : 192 // c, 96 // c : 192 // c].mean()), 0.5)
        self.assertGreater(float(score[320 // c : 416 // c, 320 // c : 416 // c].mean()), 0.6)
        self.assertLess(float(score[16:, :8].mean()), 0.1)  # bottom-left, untouched

    def test_smooth_restyle_water_over_dithered_original_is_not_a_gap(self):
        rng = np.random.default_rng(3)
        orig = np.empty((256, 256, 3), np.uint8)
        orig[:] = (36, 88, 74)
        orig[rng.random((256, 256)) < 0.3] = (10, 40, 34)  # dithered procedural water
        cur = np.empty_like(orig)
        cur[:] = (47, 109, 88)  # smooth restyle water
        st, lab, det = fields(cur, orig)
        score, _ = G.gap_score(lab, det, np.ones(det.shape, np.float32), st)
        self.assertLess(float(score.max()), 0.05)

    def test_dark_dithered_edge_fog_in_the_original_is_not_city(self):
        yy, xx = np.mgrid[0:256, 0:256]
        orig = np.where(((xx + yy) % 2 == 0)[..., None], np.array([0, 0, 0], np.uint8), np.array([200, 194, 182], np.uint8))
        orig = orig.astype(np.uint8)
        cur = np.empty_like(orig)
        cur[:] = SF.VIEW_FOG
        st, lab, det = fields(cur, orig)
        score, _ = G.gap_score(lab, det, np.ones(det.shape, np.float32), st)
        self.assertLess(float(score.max()), 0.05)


class FindGapsTest(unittest.TestCase):
    def test_regions_are_classed_and_ranked(self):
        h, w = 64, 64
        score = np.zeros((h, w), np.float32)
        void = np.zeros_like(score)
        score[4:20, 4:20] = 1.0
        void[4:20, 4:20] = 1.0  # a beige void
        score[26:50, 20:32] = 1.0  # right side sits on the tile line x=512 (cell 32)
        score[40:46, 50:56] = 0.9  # a small soft patch
        score[2, 60] = 1.0  # noise
        gaps, lab = G.find_gaps(score, void, np.array([0, 512, 1024]), np.array([0, 512, 1024]))
        kinds = {g.kind for g in gaps}
        self.assertEqual(len(gaps), 3)
        self.assertIn("void", kinds)
        self.assertIn("cutoff", kinds)
        self.assertEqual(gaps[0].kind, "cutoff")  # largest first
        self.assertTrue(all(gaps[i].rank >= gaps[i + 1].rank for i in range(len(gaps) - 1)))
        self.assertEqual(int(lab[2, 60]), 0)
        self.assertIn((0, 1), gaps[0].tiles)

    def test_blurry_tile_is_flagged_against_its_neighbours(self):
        per = 512 // G.GAP_CELL
        eo = np.full((3 * per, 3 * per), 0.3, np.float32)
        ec = eo.copy()
        ec[per : 2 * per, per : 2 * per] = 0.05
        out = G.blurry_tiles(ec, eo, np.ones_like(eo))
        self.assertEqual([d["tile"] for d in out], [[1, 1]])


class FillTest(unittest.TestCase):
    def setUp(self):
        self.orig = city(512, 512, seed=5)
        self.good = restyle(self.orig, seed=6)
        lab_o = TN.rgb_to_lab(self.orig).reshape(-1, 3)
        lab_r = TN.rgb_to_lab(self.good).reshape(-1, 3)
        self.style = TN.fit_house_style(lab_o, lab_r, samples=50_000)
        self.cur = self.good.copy()
        self.cur[160:352, 160:352] = BEIGE
        c = G.GAP_CELL
        self.region = np.zeros((512 // c, 512 // c), bool)
        self.region[160 // c : 352 // c, 160 // c : 352 // c] = True
        self.score = self.region.astype(np.float32)
        self.good_cells = ~TN.smoothstep(G.soft(self.region, 2.0), 0.01, 0.02).astype(bool)

    def test_house_fill_restores_detail_and_leaves_the_rest_exactly(self):
        out, alpha, used = G.fill_region(self.cur, self.orig, self.style, self.region, self.score, self.good_cells, [])
        self.assertEqual(used[0]["source"], "house")
        inner = np.s_[200:312, 200:312]
        err = np.abs(out[inner].astype(int) - self.good[inner].astype(int)).mean()
        self.assertLess(err, 6.0)
        far = alpha < 1e-3
        self.assertTrue(far[:64].all())
        self.assertTrue((out[far] == self.cur[far]).all())

    def test_an_intact_window_wins_over_the_house_fill(self):
        win = self.good.copy()
        win[:, :] = np.roll(win, 0, axis=0)
        cov = np.ones((512, 512), bool)
        broken = self.cur.copy()  # a window that is itself washed is not used
        out, _, used = G.fill_region(self.cur, self.orig, self.style, self.region, self.score, self.good_cells,
                                     [(broken, cov), (win, cov)])
        self.assertEqual([u["source"] for u in used], ["window"])
        self.assertEqual(used[0]["index"], 1)
        inner = np.s_[200:312, 200:312]
        self.assertLess(np.abs(out[inner].astype(int) - self.good[inner].astype(int)).mean(), 3.0)

    def test_water_pixels_are_not_refilled_from_the_original(self):
        cur = self.cur.copy()
        cur[200:260, 200:260] = (47, 109, 88)  # restyle water inside the hole
        out, _, _ = G.fill_region(cur, self.orig, self.style, self.region, self.score, self.good_cells, [])
        d = np.abs(out[214:246, 214:246].astype(int) - cur[214:246, 214:246].astype(int)).max()
        self.assertLessEqual(int(d), 2)

    def test_pure_fill_blends_back_to_the_composite(self):
        out, alpha, _ = G.fill_region(self.cur, self.orig, self.style, self.region, self.score, self.good_cells, [])
        fill, alpha2, _ = G.fill_region(self.cur, self.orig, self.style, self.region, self.score, self.good_cells, [], pure=True)
        self.assertTrue(np.allclose(alpha, alpha2))
        a = alpha[..., None]
        blended = self.cur * (1 - a) + fill * a
        self.assertLess(np.abs(blended - out.astype(np.float32)).mean(), 1.0)

    def test_verify_drops_a_fill_that_does_not_help(self):
        city = np.ones(self.region.shape, np.float32)
        out, _, _ = G.fill_region(self.cur, self.orig, self.style, self.region, self.score, self.good_cells, [])
        ok, before, after = G.verify_fill(self.cur, out, self.orig, city, self.region)
        self.assertTrue(ok)
        self.assertLess(after, before)
        same, _, _ = G.verify_fill(self.cur, self.cur.copy(), self.orig, city, self.region)
        self.assertFalse(same)

    def test_pick_windows_is_a_greedy_cover(self):
        region = np.zeros((4, 4), bool)
        region[:, :] = True
        a = np.zeros_like(region)
        a[:, :2] = True
        b = np.zeros_like(region)
        b[:, 1:] = True
        tiny = np.zeros_like(region)
        tiny[0, 0] = True
        picks = G.pick_windows(region, [tiny, a, b])
        self.assertEqual([i for i, _ in picks], [2, 1])
        self.assertEqual(int(sum(c.sum() for _, c in picks)), 16)


def _save(arr: np.ndarray, path: Path, lossless: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr, "RGB").save(path, "WEBP", lossless=lossless)


class PolishGapsTest(unittest.TestCase):
    """End to end on a 4x2 world: stage mode leaves the map alone."""

    def _world(self, td: Path):
        out = td / "world"
        rdir, pdir = out / "tiles-restyled", out / "tiles"
        big = city(1024, 2048, seed=9)
        good = restyle(big, seed=10)
        cur = good.copy()
        cur[300:700, 900:1300] = BEIGE  # a hole across four tiles
        for ty in range(2):
            for tx in range(4):
                s = np.s_[ty * 512 : (ty + 1) * 512, tx * 512 : (tx + 1) * 512]
                _save(big[s], pdir / "0" / f"{tx}_{ty}.webp")
                _save(cur[s], rdir / "0" / f"{tx}_{ty}.webp")
        grid = {"ox": 0, "oy": 0, "cols": 4, "rows": 2}
        present = {(x, y) for x in range(4) for y in range(2)}
        SF._pyramid_from_z0(rdir, grid, {0: set(present)}, pad_rgb=SF.VIEW_FOG)
        return out, rdir, pdir, grid, good

    def test_stage_fills_the_hole_and_parents_match_a_full_rebuild(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            out, rdir, pdir, grid, good = self._world(td)
            live = {p: p.read_bytes() for p in rdir.rglob("*.webp")}
            stage, report = td / "stage", td / "report"
            present, changed = SF.polish_gaps(rdir, grid, proc_dest=pdir, stage=stage, report=report, workers=2)
            self.assertEqual({p: p.read_bytes() for p in rdir.rglob("*.webp")}, live)  # live untouched
            self.assertTrue({(1, 0), (2, 0), (1, 1), (2, 1)} <= changed, changed)
            self.assertNotIn((3, 1), changed)
            a = np.asarray(Image.open(stage / "tiles-restyled" / "0" / "2_0.webp").convert("RGB")).astype(int)
            hole = np.s_[350:500, 0:200]  # grid px (350..500, 1024..1224) in tile 2_0
            self.assertLess(np.abs(a[hole] - good[350:500, 1024:1224]).mean(), 8.0)
            rep = json.loads((report / "gaps.json").read_text())
            self.assertGreaterEqual(len(rep["gaps"]), 1)
            self.assertEqual(rep["gaps"][0]["kind"], "void")
            self.assertTrue((report / "sheet.jpg").exists())
            n = SF.rebuild_parents(rdir, stage / "tiles-restyled", changed, grid, present)
            self.assertGreater(n, 0)
            # Same as a full rebuild over the merged z0.
            merged = td / "merged"
            for p in (rdir / "0").glob("*.webp"):
                src = stage / "tiles-restyled" / "0" / p.name
                _save(np.asarray(Image.open(src if src.exists() else p).convert("RGB")), merged / "0" / p.name)
            SF._pyramid_from_z0(merged, grid, {0: set(present)}, pad_rgb=SF.VIEW_FOG)
            for z in (1, 2):
                for p in (merged / str(z)).glob("*.webp"):
                    got = np.asarray(Image.open(stage / "tiles-restyled" / str(z) / p.name).convert("RGB")).astype(int)
                    want = np.asarray(Image.open(p).convert("RGB")).astype(int)
                    self.assertLessEqual(int(np.abs(got - want).max()), 2, (z, p.name))

    def test_in_place_backs_up_and_a_re_run_gives_the_same_tiles(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            out, rdir, pdir, grid, good = self._world(td)
            before = {p.name: p.read_bytes() for p in (rdir / "0").glob("*.webp")}
            backup = td / "backup"
            _, changed = SF.polish_gaps(rdir, grid, proc_dest=pdir, backup=backup, workers=2)
            self.assertTrue(changed)
            self.assertEqual({p.name for p in (backup / "0").glob("*.webp")}, {f"{x}_{y}.webp" for x, y in changed})
            for p in (backup / "0").glob("*.webp"):
                self.assertEqual(p.read_bytes(), before[p.name])
            first = {p.name: np.asarray(Image.open(p).convert("RGB")) for p in (rdir / "0").glob("*.webp")}
            _, again = SF.polish_gaps(rdir, grid, proc_dest=pdir, backup=backup, workers=2)
            self.assertEqual(again, set())  # starts from the backup, lands on the same tiles
            for p in (rdir / "0").glob("*.webp"):
                self.assertTrue((np.asarray(Image.open(p).convert("RGB")) == first[p.name]).all(), p.name)


if __name__ == "__main__":
    unittest.main()
