#!/usr/bin/env python3
"""CPU tests for tiling, feathering, and dataset prep. No GPU, no API calls."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import blend
import restyle_map
import prepare_dataset
import water_constrain as W
import slice_frontend as SF
import tiles as T
from tiles import Tile


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

class StitchTest(unittest.TestCase):
    def test_overlapping_solids_blend_in_the_hem(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # two 64x64 tiles overlapping by 16 on a 112x64 canvas (step 48)
            a = Image.new("RGB", (64, 64), (255, 0, 0))
            b = Image.new("RGB", (64, 64), (0, 0, 255))
            pa, pb = root / "a.png", root / "b.png"
            a.save(pa); b.save(pb)
            tiles = [Tile(0, 0, 64, 64), Tile(48, 0, 64, 64)]
            paths = {(0, 0): pa, (48, 0): pb}
            im = blend.stitch_bands(tiles, paths, 112, 64, overlap=16, out_path=None)
            self.assertEqual(im.size, (112, 64))
            pix = im.load()
            # far left is red, far right is blue
            self.assertEqual(pix[8, 32], (255, 0, 0))
            self.assertEqual(pix[104, 32], (0, 0, 255))
            # in the hem, a mix — not a hard seam
            mid = pix[56, 32]
            self.assertTrue(mid[0] > 20 and mid[2] > 20, mid)
            self.assertFalse(mid in ((255, 0, 0), (0, 0, 255), (0, 0, 0)), mid)





class FogSkipTest(unittest.TestCase):
    def test_smooth_dark_image_is_fog_and_a_sharp_city_is_not(self):
        dark = Image.new("RGB", (1024, 1024), (8, 8, 10))
        self.assertTrue(restyle_map.is_mostly_fog(dark))
        pale = Image.new("RGB", (1024, 1024), (0xE9, 0xE6, 0xDF))
        self.assertTrue(restyle_map.is_mostly_fog(pale))
        # coarse high-contrast blocks (a 4 px checkerboard would vanish at the 128 px metric)
        city = Image.new("RGB", (1024, 1024), (200, 200, 190))
        px = city.load()
        for y in range(1024):
            for x in range(1024):
                if ((x // 32) + (y // 32)) % 2 == 0:
                    px[x, y] = (40, 90, 160)
        self.assertFalse(restyle_map.is_mostly_fog(city))
        self.assertGreater(restyle_map.edge_energy(city), 8.0)
        self.assertLess(restyle_map.edge_energy(dark), 1.0)

    def test_composite_dark_fog_keeps_black_and_leaves_bright_restyle(self):
        proc = Image.new("RGB", (64, 64), (8, 8, 8))
        # right half is a bright "city" in the procedural image
        for y in range(64):
            for x in range(32, 64):
                proc.putpixel((x, y), (160, 160, 150))
        rest = Image.new("RGB", (64, 64), (0, 255, 0))  # invented green everywhere
        out = restyle_map.composite_dark_fog(proc, rest, lum_max=48, feather=1)
        # left (dark fog) stays near black, not green
        l = out.getpixel((8, 32))
        self.assertLess(l[1], 80, l)
        # right (bright) takes the restyle
        r = out.getpixel((56, 32))
        self.assertGreater(r[1], 200, r)



class WaterConstrainTest(unittest.TestCase):
    def _rgb(self, w, h, color):
        return Image.new("RGB", (w, h), color)

    def test_false_water_on_land_is_replaced_by_procedural(self):
        # beige land in the renderer, invented teal in the restyle
        proc = self._rgb(64, 64, (0xE0, 0xD8, 0xC8))
        rest = self._rgb(64, 64, (0x2F, 0x6D, 0x58))
        mask = Image.new("L", (64, 64), 0)  # land
        out, report = W.constrain(proc, rest, mask, feather=1)
        self.assertGreater(report["false_water"], 64 * 64 * 0.9)
        self.assertEqual(report["missing_water"], 0)
        px = out.getpixel((32, 32))
        self.assertGreater(px[0], 200, px)  # back to beige (0xE0, 0xD8, 0xC8), not teal
        self.assertGreater(px[0], px[1] - 10, px)
        self.assertGreater(abs(px[0] - 0xE0) < 16 or px[0] >= 200, 0)
        self.assertEqual(px, (0xE0, 0xD8, 0xC8))

    def test_missing_water_is_restored_from_procedural(self):
        proc = self._rgb(64, 64, (0x2F, 0x6D, 0x58))
        rest = self._rgb(64, 64, (0xE0, 0xD8, 0xC8))  # paved over
        mask = Image.new("L", (64, 64), 255)  # water kind
        out, report = W.constrain(proc, rest, mask, feather=1)
        self.assertGreater(report["missing_water"], 64 * 64 * 0.9)
        px = out.getpixel((32, 32))
        self.assertLess(px[0], 80, px)
        self.assertGreater(px[1], 80, px)

    def test_matching_water_and_land_are_left_alone(self):
        water = self._rgb(32, 32, (0x2F, 0x6D, 0x58))
        land = self._rgb(32, 32, (0xE0, 0xD8, 0xC8))
        mask_w = Image.new("L", (32, 32), 255)
        mask_l = Image.new("L", (32, 32), 0)
        out_w, r_w = W.constrain(water, water, mask_w, feather=1)
        out_l, r_l = W.constrain(land, land, mask_l, feather=1)
        self.assertEqual(r_w["fixed"], 0)
        self.assertEqual(r_l["fixed"], 0)
        self.assertEqual(out_w.getpixel((16, 16)), (0x2F, 0x6D, 0x58))
        self.assertEqual(out_l.getpixel((16, 16)), (0xE0, 0xD8, 0xC8))

    def test_default_water_under_fog_is_not_forced(self):
        # unpainted kind is water, but the procedural image is dark fog
        proc = self._rgb(64, 64, (8, 8, 10))
        rest = self._rgb(64, 64, (8, 8, 10))
        mask = Image.new("L", (64, 64), 255)
        _out, report = W.constrain(proc, rest, mask, feather=1)
        self.assertEqual(report["gt_water"], 0)
        self.assertEqual(report["fixed"], 0)

    def test_window_grid_glass_is_not_treated_as_water(self):
        # Glass is teal in BOTH the procedural image and the restyle (as on the
        # real map). The constrain step must not replace it with "land".
        def glass():
            im = Image.new("RGB", (64, 64), (0x3A, 0x7A, 0x6C))
            px = im.load()
            for y in range(64):
                for x in range(64):
                    if x % 4 == 0 or y % 4 == 0:
                        px[x, y] = (0xD0, 0xE0, 0xE8)
            return im
        proc, rest = glass(), glass()
        mask = Image.new("L", (64, 64), 0)
        out, report = W.constrain(proc, rest, mask, feather=1)
        self.assertEqual(report["false_water"], 0, report)
        cell = out.getpixel((6, 6))
        self.assertEqual(cell, (0x3A, 0x7A, 0x6C))

    def test_textured_invented_water_on_land_is_replaced(self):
        # Restyle water often has a pixel-art wash (high local edge). It must
        # still come off where the procedural image is beige land.
        proc = self._rgb(80, 80, (0xE0, 0xD8, 0xC8))
        rest = Image.new("RGB", (80, 80), (0x2F, 0x6D, 0x58))
        px = rest.load()
        for y in range(80):
            for x in range(80):
                if (x + y) % 3 == 0:
                    px[x, y] = (0x3D, 0x8A, 0x70)
                elif (x + 2 * y) % 5 == 0:
                    px[x, y] = (0x24, 0x58, 0x4A)
        mask = Image.new("L", (80, 80), 0)
        out, report = W.constrain(proc, rest, mask, feather=1)
        self.assertGreater(report["false_water"], 80 * 80 * 0.7, report)
        pxo = out.getpixel((40, 40))
        self.assertEqual(pxo, (0xE0, 0xD8, 0xC8))

    def test_feather_blends_at_the_edge(self):
        proc = self._rgb(80, 80, (0xE0, 0xD8, 0xC8))
        rest = self._rgb(80, 80, (0xC0, 0xB0, 0x90))  # restyle land, slightly different
        # invented water only on the left half
        for y in range(80):
            for x in range(40):
                rest.putpixel((x, y), (0x2F, 0x6D, 0x58))
        mask = Image.new("L", (80, 80), 0)
        out, report = W.constrain(proc, rest, mask, feather=12)
        self.assertGreater(report["false_water"], 30 * 80)
        left = out.getpixel((8, 40))
        right = out.getpixel((70, 40))
        edge = out.getpixel((40, 40))
        self.assertGreater(left[0], 150, left)
        self.assertEqual(right, (0xC0, 0xB0, 0x90))
        # the seam is a mix, not a hard cut
        self.assertNotEqual(edge, left)
        self.assertNotEqual(edge, right)



    def test_harmonize_shifts_flat_beige_toward_restyle_land(self):
        proc = self._rgb(96, 96, (0xE0, 0xD8, 0xC8))
        rest = Image.new("RGB", (96, 96), (0xC8, 0xBE, 0xA8))  # restyle pale ground
        # left half invented water, right half already restyle land
        for y in range(96):
            for x in range(48):
                rest.putpixel((x, y), (0x2F, 0x6D, 0x58))
        mask = Image.new("L", (96, 96), 0)
        out, report = W.constrain(proc, rest, mask, feather=1, origin=(0, 0), strip_rooftops=False)
        self.assertGreater(report["false_water"], 40 * 96)
        left = out.getpixel((16, 48))
        right = out.getpixel((80, 48))
        self.assertEqual(right, (0xC8, 0xBE, 0xA8))
        # patched land is closer to the restyle ground than to raw procedural beige
        def dist(a, b):
            return sum((a[i] - b[i]) ** 2 for i in range(3))
        self.assertLess(dist(left, (0xC8, 0xBE, 0xA8)), dist(left, (0xE0, 0xD8, 0xC8)))

    def test_invented_rooftop_garden_is_restored_to_procedural_roof(self):
        proc = self._rgb(80, 80, (0xB0, 0xAE, 0xA8))  # concrete roof
        rest = self._rgb(80, 80, (0xB0, 0xAE, 0xA8))
        for y in range(20, 60):
            for x in range(20, 60):
                rest.putpixel((x, y), (0x3A, 0x8A, 0x48))  # invented garden
        mask = Image.new("L", (80, 80), 0)
        out, report = W.constrain(proc, rest, mask, feather=1, harmonize=False)
        self.assertGreater(report["rooftops"], 30 * 30)
        mid = out.getpixel((40, 40))
        self.assertGreater(mid[0], 140, mid)
        self.assertLess(abs(mid[0] - mid[1]), 20, mid)



class SliceFrontendTest(unittest.TestCase):
    def test_futian_grid_matches_pipeline(self):
        # minX -11950, minY -6000, maxX 11550, maxY 5175
        g = SF.make_grid(-11950, -6000, 11550, 5175)
        self.assertEqual(g["ox"], -12288)
        self.assertEqual(g["oy"], -6144)
        self.assertEqual(g["cols"], 48)
        self.assertEqual(g["rows"], 24)
        self.assertEqual(g["ccols"], 12)
        self.assertEqual(g["crows"], 6)
        self.assertEqual(g["chunk"], 2048)

    def test_pack_bits_is_lsb_first_row_major(self):
        # 2×2, all present: bits 0..3 set → 0x0F → "Dw=="
        self.assertEqual(SF.pack_bits({(0, 0), (1, 0), (0, 1), (1, 1)}, 2, 2), "Dw==")
        # only (0, 0): bit 0 → 0x01 → "AQ=="
        self.assertEqual(SF.pack_bits({(0, 0)}, 2, 2), "AQ==")

    def test_slice_writes_a_pyramid(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # 600×400 image, origin at (0,0) → grid origin 0,0, 1×1 chunk = 4×4 tiles of 512
            # max 600,400 → ccols=1, crows=1, 4×4 tiles. Only (0,0) has content.
            im = Image.new("RGB", (600, 400), (20, 30, 40))
            for y in range(400):
                for x in range(600):
                    im.putpixel((x, y), (x % 250, y % 250, 80))
            g = SF.make_grid(0, 0, 600, 400)
            self.assertEqual((g["cols"], g["rows"]), (4, 4))
            dest = root / "tiles"
            levels = SF.slice_image(im, g, (0, 0), dest, pad_rgb=(0, 0, 0))
            self.assertGreaterEqual(len(levels), 2)
            self.assertEqual(levels[0]["z"], 0)
            self.assertTrue((dest / "0" / "0_0.webp").exists())
            # the far pad tiles are empty and skipped
            self.assertFalse((dest / "0" / "3_3.webp").exists())
            # a parent exists
            self.assertTrue(any((dest / str(l["z"])).exists() for l in levels if l["z"] > 0))


class RestyleMapShardTest(unittest.TestCase):
    def test_parse_shard_is_zero_based_and_rejects_bad_specs(self):
        self.assertEqual(restyle_map.parse_shard("0/8"), (0, 8))
        self.assertEqual(restyle_map.parse_shard("7/8"), (7, 8))
        with self.assertRaises(ValueError):
            restyle_map.parse_shard("8/8")
        with self.assertRaises(ValueError):
            restyle_map.parse_shard("1/0")
        with self.assertRaises(ValueError):
            restyle_map.parse_shard("2")
        with self.assertRaises(ValueError):
            restyle_map.parse_shard("-1/4")

    def test_shards_are_disjoint_and_cover_every_window(self):
        ts = T.tiles_for(3000, 2000, 1024, 256)
        n = 5
        parts = [restyle_map.select_tiles(ts, shard=(i, n)) for i in range(n)]
        ids = [[(t.x, t.y) for t in p] for p in parts]
        flat = [xy for part in ids for xy in part]
        self.assertEqual(len(flat), len(ts))
        self.assertEqual(len(set(flat)), len(ts))
        self.assertEqual(set(flat), {(t.x, t.y) for t in ts})
        # no two shards share a window
        for i in range(n):
            for j in range(i + 1, n):
                self.assertFalse(set(ids[i]) & set(ids[j]))

    def test_explicit_windows_select_only_those_origins(self):
        ts = T.tiles_for(3000, 2000, 1024, 256)
        want = [(ts[0].x, ts[0].y), (ts[3].x, ts[3].y)]
        got = restyle_map.select_tiles(ts, windows=want)
        self.assertEqual([(t.x, t.y) for t in got], want)
        with self.assertRaises(ValueError):
            restyle_map.select_tiles(ts, windows=[(1, 2)])

    def test_parse_windows_accepts_semicolons_and_spaces(self):
        self.assertEqual(restyle_map.parse_windows("0,0;768,0"), [(0, 0), (768, 0)])
        self.assertEqual(restyle_map.parse_windows("0,0 768,1024"), [(0, 0), (768, 1024)])

    def test_resume_skips_an_existing_tile_without_calling_the_api(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "already.png"
            Image.new("RGB", (64, 64), (10, 20, 30)).save(dest)
            dest.write_bytes(dest.read_bytes() + b"\0" * 2000)
            self.assertGreater(dest.stat().st_size, 1000)
            src = Path(td) / "src.png"
            Image.new("RGB", (64, 64), (1, 2, 3)).save(src)
            result = restyle_map.restyle_one(src, dest, Path(td) / "x.log", "pixelsg", quality="medium")
            self.assertTrue(result["skipped"])
            self.assertEqual(result["calls"], 0)
            self.assertEqual(result["usd"], 0.0)




if __name__ == "__main__":
    unittest.main()
