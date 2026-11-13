#!/usr/bin/env python3
"""CPU tests for tiling, feathering, and dataset prep. No GPU, no API calls."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
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




class DistrictOriginTest(unittest.TestCase):
    def test_same_geo_is_a_pure_translation_of_zero(self):
        geo = {"lon0": 114.0683, "lat0": 22.5455, "betaDeg": 0, "k": 2}
        dmeta = {"origin": {"x": -11950, "y": -6000}, "geo": geo}
        wx, wy, dx, dy = SF.district_world_origin(dmeta, geo)
        self.assertEqual((wx, wy), (-11950, -6000))
        self.assertEqual((dx, dy), (0.0, 0.0))

    def test_nanshan_houhai_origin_moves_into_civic_center_space(self):
        world = {"lon0": 114.0683, "lat0": 22.5455, "betaDeg": 0, "k": 2}
        dmeta = {
            "origin": {"x": -23850, "y": -11975},
            "geo": {"lon0": 113.94167, "lat0": 22.51694, "betaDeg": 0, "k": 2},
        }
        wx, wy, dx, dy = SF.district_world_origin(dmeta, world)
        self.assertEqual((wx, wy), (-56211, -21804))
        self.assertAlmostEqual(dx, -32360.51, places=1)
        self.assertAlmostEqual(dy, -9828.79, places=1)
        # Shekou (113.916, 22.485) in world space sits inside this canvas,
        # not at the untranslated local origin the first slicer used.
        sx, sy = SF.to_map(world, 113.916, 22.485)
        self.assertTrue(-56211 < sx < -56211 + 60950, sx)
        self.assertTrue(-21804 < sy < -21804 + 30600, sy)
        # The untranslated placement would put Shekou around (-12377, 915),
        # many kilometres east of its real world position.
        self.assertLess(sx, -30000)

    def test_luohu_caiwuwei_origin_moves_east_of_civic_center(self):
        world = {"lon0": 114.0683, "lat0": 22.5455, "betaDeg": 0, "k": 2}
        dmeta = {
            "origin": {"x": -8650, "y": -5175},
            "geo": {"lon0": 114.10194, "lat0": 22.54667, "betaDeg": 0, "k": 2},
        }
        wx, wy, dx, dy = SF.district_world_origin(dmeta, world)
        self.assertEqual((wx, wy), (-1480, -1850))
        self.assertAlmostEqual(dx, 7169.65, places=1)
        self.assertAlmostEqual(dy, 3324.63, places=1)

    def test_pack_round_trips(self):
        present = {(0, 0), (3, 0), (1, 2), (7, 3)}
        b64 = SF.pack_bits(present, 8, 4)
        self.assertEqual(SF.unpack_bits(b64, 8, 4), present)


class BeigeCompositeTest(unittest.TestCase):
    PAD = (0x1A, 0x1E, 0x1C)

    def test_empty_mask_catches_viewer_fog_and_dark_fog(self):
        beige = Image.new("RGB", (8, 8), SF.VIEW_FOG)
        self.assertTrue(SF.empty_mask(np.asarray(beige), self.PAD).all())
        dark = Image.new("RGB", (8, 8), (10, 12, 14))
        self.assertTrue(SF.empty_mask(np.asarray(dark), self.PAD).all())
        city = Image.new("RGB", (8, 8), (40, 120, 90))
        self.assertFalse(SF.empty_mask(np.asarray(city), self.PAD).any())

    def test_later_districts_beige_edge_does_not_erase_a_neighbour(self):
        # Futian-like city, then a Luohu-like window that is viewer-fog beige
        # on the overlapping half — the civic-center bug.
        city = Image.new("RGB", (32, 32), (40, 110, 90))
        for y in range(32):
            for x in range(32):
                city.putpixel((x, y), (40 + x, 110, 90 + y // 2))
        incoming = Image.new("RGB", (32, 32), SF.VIEW_FOG)
        for y in range(16, 32):
            for x in range(32):
                incoming.putpixel((x, y), (180, 90, 70))  # real Luohu content
        out = SF.composite_district(city, incoming, self.PAD)
        pix = out.load()
        # top half stays Futian, not beige
        self.assertNotEqual(pix[8, 4], SF.VIEW_FOG)
        self.assertEqual(pix[8, 4], city.getpixel((8, 4)))
        # bottom half takes Luohu's real content
        self.assertEqual(pix[8, 20], (180, 90, 70))

    def test_almost_empty_rejects_a_uniform_beige_tile(self):
        beige = np.asarray(Image.new("RGB", (16, 16), SF.VIEW_FOG))
        self.assertTrue(SF._almost_empty(beige, self.PAD))
        city = np.zeros((16, 16, 3), dtype=np.uint8)
        city[:] = SF.VIEW_FOG
        city[4:12, 4:12] = (40, 120, 90)
        self.assertFalse(SF._almost_empty(city, self.PAD))

    def test_pyramid_ignores_leftover_children_not_in_present(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "tiles"
            (dest / "0").mkdir(parents=True)
            # a real city tile at 0,0 and a stale leftover at 1,0
            Image.new("RGB", (512, 512), (40, 110, 90)).save(dest / "0" / "0_0.webp", "WEBP")
            Image.new("RGB", (512, 512), (255, 0, 0)).save(dest / "0" / "1_0.webp", "WEBP")
            grid = {"ox": 0, "oy": 0, "cols": 4, "rows": 4}
            present = {0: {(0, 0)}}
            levels = SF._pyramid_from_z0(dest, grid, present, self.PAD)
            self.assertIn((0, 0), SF.unpack_bits(levels[0]["tiles"], 4, 4))
            self.assertNotIn((1, 0), SF.unpack_bits(levels[0]["tiles"], 4, 4))
            # z1 parent 0,0 is built only from 0,0 — leftover 1,0 is ignored
            self.assertTrue((dest / "1" / "0_0.webp").exists())
            parent = Image.open(dest / "1" / "0_0.webp")
            arr = np.asarray(parent.convert("RGB"))
            # the right half would be red if the leftover child had been used
            self.assertFalse((np.abs(arr[:, 256:].astype(int) - (255, 0, 0)) < 10).all())

    def test_fill_gaps_copies_procedural_into_missing_restyle_cells(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            restyle = root / "restyle"
            proc = root / "proc"
            (restyle / "0").mkdir(parents=True)
            (proc / "0").mkdir(parents=True)
            Image.new("RGB", (512, 512), (40, 110, 90)).save(restyle / "0" / "1_1.webp", "WEBP")
            Image.new("RGB", (512, 512), (10, 20, 15)).save(proc / "0" / "1_1.webp", "WEBP")
            Image.new("RGB", (512, 512), (12, 22, 18)).save(proc / "0" / "2_3.webp", "WEBP")
            # a procedural tile outside the (smaller) grid must not be copied
            Image.new("RGB", (512, 512), (1, 2, 3)).save(proc / "0" / "20_20.webp", "WEBP")
            # viewer-fog beige is empty page, not city and not dark edge fog
            Image.new("RGB", (512, 512), SF.VIEW_FOG).save(proc / "0" / "3_3.webp", "WEBP")
            grid = {"ox": 0, "oy": 0, "cols": 8, "rows": 8}
            present = SF.fill_gaps_from_procedural(restyle, proc, grid, {(1, 1)}, self.PAD)
            self.assertEqual(present, {(1, 1), (2, 3)})
            self.assertTrue((restyle / "0" / "2_3.webp").exists())
            self.assertFalse((restyle / "0" / "20_20.webp").exists())
            self.assertFalse((restyle / "0" / "3_3.webp").exists())
            # existing restyle is not overwritten by the procedural copy
            arr = np.asarray(Image.open(restyle / "0" / "1_1.webp").convert("RGB"))
            proc = np.asarray(Image.open(proc / "0" / "1_1.webp").convert("RGB"))
            self.assertFalse(np.allclose(arr, proc, atol=5))
            self.assertGreater(int(arr[0, 0, 1]), 80)



class EdgeFogPolishTest(unittest.TestCase):
    PAD = (0x1A, 0x1E, 0x1C)

    def test_dark_fog_mask_keeps_water_and_city(self):
        dark = np.full((16, 16, 3), (10, 12, 14), dtype=np.uint8)
        self.assertTrue(SF.dark_fog_mask(dark, self.PAD).all())
        pad = np.full((16, 16, 3), self.PAD, dtype=np.uint8)
        self.assertTrue(SF.dark_fog_mask(pad, self.PAD).all())
        black = np.zeros((16, 16, 3), dtype=np.uint8)
        self.assertTrue(SF.dark_fog_mask(black, self.PAD).all())
        water = np.full((16, 16, 3), SF.RESTYLE_WATER, dtype=np.uint8)
        self.assertFalse(SF.dark_fog_mask(water, self.PAD).any())
        city = np.full((16, 16, 3), (40, 120, 90), dtype=np.uint8)
        self.assertFalse(SF.dark_fog_mask(city, self.PAD).any())
        park = np.full((16, 16, 3), (30, 90, 40), dtype=np.uint8)
        self.assertFalse(SF.dark_fog_mask(park, self.PAD).any())

    def test_smooth_edge_fog_turns_a_dark_frame_beige_and_keeps_the_city(self):
        arr = np.full((64, 64, 3), (8, 10, 9), dtype=np.uint8)
        arr[16:48, 16:48] = (40, 120, 90)
        out, status = SF.smooth_edge_fog(arr, self.PAD, feather=8)
        self.assertEqual(status, "changed")
        self.assertIsNotNone(out)
        # city core stays green, not beige
        core = out[28:36, 28:36]
        self.assertGreater(int(core[:, :, 1].mean()), 80)
        self.assertFalse(np.all(np.abs(core.astype(int) - SF.VIEW_FOG) <= 4))
        # far into the original dark frame is viewer fog
        corner = out[2:6, 2:6]
        self.assertTrue(np.all(np.abs(corner.astype(int) - SF.VIEW_FOG) <= 6))

    def test_smooth_edge_fog_drops_a_uniform_dark_tile(self):
        arr = np.full((32, 32, 3), (6, 7, 6), dtype=np.uint8)
        out, status = SF.smooth_edge_fog(arr, self.PAD, feather=4)
        self.assertEqual(status, "empty")
        self.assertIsNone(out)

    def test_smooth_edge_fog_leaves_a_city_tile_alone(self):
        arr = np.full((32, 32, 3), (50, 110, 80), dtype=np.uint8)
        out, status = SF.smooth_edge_fog(arr, self.PAD, feather=4)
        self.assertEqual(status, "unchanged")
        self.assertTrue(np.array_equal(out, arr))

    def test_pyramid_pad_is_viewer_fog_so_missing_children_are_not_dark(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "tiles"
            (dest / "0").mkdir(parents=True)
            city = Image.new("RGB", (512, 512), (40, 110, 90))
            city.save(dest / "0" / "0_0.webp", "WEBP")
            grid = {"ox": 0, "oy": 0, "cols": 4, "rows": 4}
            present = {0: {(0, 0)}}
            levels = SF._pyramid_from_z0(dest, grid, present, SF.VIEW_FOG)
            self.assertTrue((dest / "1" / "0_0.webp").exists())
            parent = np.asarray(Image.open(dest / "1" / "0_0.webp").convert("RGB"))
            # right and bottom halves had no child: they must be beige, not dark pad
            right = parent[:, 256:]
            self.assertGreater(int(right.mean()), 180)
            self.assertTrue(np.all(np.abs(right.astype(int) - SF.VIEW_FOG).mean(axis=2) < 25))
            # leftover dark would have been a stair-step; leftover file is ignored
            self.assertIn((0, 0), SF.unpack_bits(levels[0]["tiles"], 4, 4))

    def test_pyramid_cleans_stale_parents_and_ignores_leftover_children(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "tiles"
            (dest / "0").mkdir(parents=True)
            (dest / "1").mkdir(parents=True)
            Image.new("RGB", (512, 512), (40, 110, 90)).save(dest / "0" / "0_0.webp", "WEBP")
            Image.new("RGB", (512, 512), (255, 0, 0)).save(dest / "0" / "1_0.webp", "WEBP")
            Image.new("RGB", (512, 512), (0, 0, 255)).save(dest / "1" / "3_3.webp", "WEBP")
            grid = {"ox": 0, "oy": 0, "cols": 4, "rows": 4}
            present = {0: {(0, 0)}}
            levels = SF._pyramid_from_z0(dest, grid, present, SF.VIEW_FOG)
            self.assertNotIn((1, 0), SF.unpack_bits(levels[0]["tiles"], 4, 4))
            parent = np.asarray(Image.open(dest / "1" / "0_0.webp").convert("RGB"))
            self.assertFalse((np.abs(parent[:, 256:].astype(int) - (255, 0, 0)) < 10).all())
            # stale parent from a previous larger set is gone
            self.assertFalse((dest / "1" / "3_3.webp").exists())


class BayWaterFillTest(unittest.TestCase):
    PAD = (0x1A, 0x1E, 0x1C)

    def test_bad_water_catches_black_and_flat_blocks_and_keeps_textured_water(self):
        rest = np.zeros((64, 64, 3), dtype=np.uint8)
        rest[:32] = (0, 0, 0)  # black hole
        rest[32:] = SF.RESTYLE_WATER  # flat teal
        # procedural is textured teal throughout
        rng = np.random.default_rng(0)
        proc = np.clip(
            np.array(SF.RESTYLE_WATER, dtype=np.int16)
            + rng.integers(-18, 19, size=(64, 64, 3)),
            0, 255,
        ).astype(np.uint8)
        # bump procedural std above the flat threshold
        proc[::2, ::2] = (70, 140, 120)
        water = np.ones((64, 64), dtype=bool)
        bad = SF.bad_water_mask(rest, water, proc=proc, pad_rgb=self.PAD)
        self.assertGreater(bad[:32].mean(), 0.9, "black hole should be bad")
        self.assertGreater(bad[32:].mean(), 0.9, "flat sgWater against textured proc should be bad")
        # a textured restyle of the same water is not bad
        good_rest = proc.copy()
        good_bad = SF.bad_water_mask(good_rest, water, proc=proc, pad_rgb=self.PAD)
        self.assertLess(good_bad.mean(), 0.1)

    def test_fill_bad_water_restores_black_bay_to_the_restyle_tone(self):
        rest = np.zeros((64, 64, 3), dtype=np.uint8)
        rest[0:8, 0:8] = (80, 90, 70)  # a little land, left alone
        land = np.zeros((64, 64), dtype=bool)
        land[0:8, 0:8] = True
        water = ~land
        rng = np.random.default_rng(1)
        proc = np.clip(
            np.array(SF.RESTYLE_WATER, dtype=np.int16)
            + rng.integers(-12, 13, size=(64, 64, 3)),
            0, 255,
        ).astype(np.uint8)
        proc[::3, ::3] = (60, 130, 110)
        out, n = SF.fill_bad_water(rest, water, proc=proc, water_tone=SF.RESTYLE_WATER, origin=(100, 200), pad_rgb=self.PAD)
        self.assertGreater(n, 64 * 64 * 0.7)
        # land is untouched
        self.assertTrue(np.array_equal(out[0:8, 0:8], rest[0:8, 0:8]))
        # bay is no longer black and sits near sgWater
        bay = out[16:, 16:]
        self.assertGreater(int(bay[:, :, 1].mean()), 70)
        self.assertGreater(int(bay[:, :, 1].mean() - bay[:, :, 0].mean()), 15)
        self.assertGreater(int(bay.min()), 5)

    def test_fill_without_procedural_uses_a_grained_tone(self):
        rest = np.zeros((48, 48, 3), dtype=np.uint8)
        water = np.ones((48, 48), dtype=bool)
        out, n = SF.fill_bad_water(rest, water, proc=None, water_tone=SF.RESTYLE_WATER, origin=(0, 0), pad_rgb=self.PAD)
        self.assertGreater(n, 40 * 40)
        self.assertGreater(int(out[:, :, 1].mean()), 80)
        # grain means it is not a single colour
        self.assertGreater(len(np.unique(out.reshape(-1, 3), axis=0)), 8)

    def test_water_mask_roi_translates_a_district_into_world_space(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # 32×16 mask, origin at local (-10, -4). Water is a 4×4 block at
            # local mask pixel (8, 4) — i.e. local coord (-10+8, -4+4) = (-2, 0).
            raw = root / "water_mask.raw"
            width, height = 32, 16
            mm = np.memmap(raw, dtype=np.uint8, mode="w+", shape=(height, width))
            mm[:] = 0
            mm[4:8, 8:12] = 255
            mm.flush()
            del mm
            (root / "water_mask.json").write_text(
                '{"width": 32, "height": 16, "origin": {"x": -10, "y": -4}, "encoding": "uint8"}\n'
            )
            # district local origin equals mask origin; world origin is (+100, +50)
            # so world coord of the water block's top-left:
            # local = (-2, 0), world = local - local_origin + world_origin
            #        = (-2 - (-10) + 100, 0 - (-4) + 50) = (108, 54)
            d = {
                "chunks": root,
                "origin": (100, 50),
                "meta": {"origin": {"x": -10, "y": -4}},
            }
            SF.attach_water_masks([d])
            self.assertIn("water_mask", d)
            # crop a 8×8 window at the water block
            roi = SF.water_mask_roi([d], 108, 54, 8, 8)
            self.assertEqual(roi.shape, (8, 8))
            self.assertTrue((roi[:4, :4] == 255).all(), roi[:4, :4])
            self.assertTrue((roi[4:, :] == 0).all())
            # a window far away is empty
            empty = SF.water_mask_roi([d], 0, 0, 8, 8)
            self.assertEqual(int(empty.max()), 0)

    def test_polish_z0_deletes_dark_edge_tiles_and_fills_a_black_bay(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            restyle = root / "restyle"
            proc = root / "proc"
            (restyle / "0").mkdir(parents=True)
            (proc / "0").mkdir(parents=True)
            # (0,0) is a dark edge-fog tile — should be deleted
            Image.new("RGB", (512, 512), (4, 5, 4)).save(restyle / "0" / "0_0.webp", "WEBP")
            # (1,0) is a black bay; procedural has textured water
            Image.new("RGB", (512, 512), (0, 0, 0)).save(restyle / "0" / "1_0.webp", "WEBP")
            rng = np.random.default_rng(2)
            proc_arr = np.clip(
                np.array(SF.RESTYLE_WATER, dtype=np.int16)
                + rng.integers(-14, 15, size=(512, 512, 3)),
                0, 255,
            ).astype(np.uint8)
            proc_arr[::4, ::4] = (65, 135, 115)
            Image.fromarray(proc_arr, "RGB").save(proc / "0" / "1_0.webp", "WEBP")
            # also a procedural-only water cell at (2,0) with no restyle
            Image.fromarray(proc_arr, "RGB").save(proc / "0" / "2_0.webp", "WEBP")
            # a real city tile, left alone
            Image.new("RGB", (512, 512), (80, 130, 100)).save(restyle / "0" / "0_1.webp", "WEBP")
            grid = {"ox": 0, "oy": 0, "cols": 8, "rows": 8}
            present = SF.polish_z0_tiles(
                restyle, grid, districts=[], proc_dest=proc, pad_rgb=self.PAD, feather=8,
                progress_path=root / "progress.jsonl",
            )
            self.assertNotIn((0, 0), present)
            self.assertFalse((restyle / "0" / "0_0.webp").exists())
            self.assertIn((0, 1), present)
            city = np.asarray(Image.open(restyle / "0" / "0_1.webp").convert("RGB"))
            self.assertGreater(int(city[10, 10, 1]), 100)
            # bay tile is filled with teal, not black
            self.assertIn((1, 0), present)
            bay = np.asarray(Image.open(restyle / "0" / "1_0.webp").convert("RGB"))
            self.assertGreater(int(bay[:, :, 1].mean()), 70)
            self.assertGreater(int(bay[:, :, 1].mean() - bay[:, :, 0].mean()), 10)
            # a missing restyle cell over procedural water is created
            self.assertIn((2, 0), present)
            self.assertTrue((restyle / "0" / "2_0.webp").exists())
            created = np.asarray(Image.open(restyle / "0" / "2_0.webp").convert("RGB"))
            self.assertGreater(int(created[:, :, 1].mean()), 70)

    def test_real_water_needs_the_mask_and_procedural_agreement(self):
        mask = np.zeros((16, 16), dtype=np.uint8)
        mask[:, :8] = 255  # left half "kind = water", including unpainted default
        proc = np.zeros((16, 16, 3), dtype=np.uint8)
        proc[:, :8] = self.PAD  # left is dark fog, not real water
        proc[:, 8:] = SF.RESTYLE_WATER  # right looks like water but mask says land
        # no real water: mask-water is fog, mask-land is ignored
        water = SF.real_water_mask(mask, proc)
        self.assertFalse(water.any())
        # when procedural left is actually teal, it is real water
        proc[:, :8] = SF.RESTYLE_WATER
        water = SF.real_water_mask(mask, proc)
        self.assertTrue(water[:, :8].all())
        self.assertFalse(water[:, 8:].any())




class SmoothEdgePolishTest(unittest.TestCase):
    PAD = (0x1A, 0x1E, 0x1C)

    def test_core_content_rejects_beige_fog_and_the_washed_halo(self):
        city = np.full((16, 16, 3), (40, 120, 90), dtype=np.uint8)
        self.assertTrue(SF.core_content_mask(city, self.PAD).all())
        water = np.full((16, 16, 3), SF.RESTYLE_WATER, dtype=np.uint8)
        self.assertTrue(SF.core_content_mask(water, self.PAD).all())
        beige = np.full((16, 16, 3), SF.VIEW_FOG, dtype=np.uint8)
        self.assertFalse(SF.core_content_mask(beige, self.PAD).any())
        dark = np.full((16, 16, 3), (8, 10, 9), dtype=np.uint8)
        self.assertFalse(SF.core_content_mask(dark, self.PAD).any())
        # washed gray-green halo: a 50/50 blend of forest and viewer fog,
        # pulled close to beige with low chroma
        wash = np.full((16, 16, 3), (210, 208, 200), dtype=np.uint8)
        self.assertFalse(SF.core_content_mask(wash, self.PAD).any())

    def test_chamfer_and_signed_distance_signs(self):
        seed = np.zeros((32, 32), dtype=bool)
        seed[12:20, 12:20] = True
        d = SF.chamfer_distance(seed, 10)
        self.assertEqual(d[16, 16], 0)
        self.assertAlmostEqual(float(d[12, 16]), 0.0, places=3)
        self.assertGreater(float(d[10, 16]), 1.0)
        self.assertLess(float(d[10, 16]), 3.0)
        signed = SF.signed_distance_field(seed, 10)
        self.assertLess(float(signed[16, 16]), 0)
        self.assertGreater(float(signed[0, 0]), 0)
        # magnitude at the far corner is capped, not zero
        self.assertGreater(float(signed[0, 0]), 8)

    def test_fade_alpha_is_zero_inside_and_one_past_the_radius(self):
        dist = np.array([-40, -16, 0, 64, 128, 200], dtype=np.float32)
        a = SF.fade_alpha(dist, fade=128, inset=16)
        self.assertLess(float(a[0]), 0.02)
        self.assertGreater(float(a[2]), 0.0)
        self.assertLess(float(a[2]), 0.15)
        self.assertAlmostEqual(float(a[4]), 1.0, places=3)
        self.assertEqual(float(a[5]), 1.0)
        # smoothstep is monotonic
        self.assertTrue(np.all(np.diff(a) >= -1e-6))

    def test_coverage_fade_turns_the_outside_beige_and_keeps_the_core(self):
        arr = np.full((64, 64, 3), (40, 120, 90), dtype=np.uint8)
        # signed dist: negative in a 32×32 centre, positive outside
        yy, xx = np.ogrid[:64, :64]
        signed = np.sqrt((xx - 32.0) ** 2 + (yy - 32.0) ** 2) - 16.0
        out, status = SF.apply_coverage_fade(arr, signed, fade=16, inset=4, pad_rgb=self.PAD)
        self.assertEqual(status, "changed")
        core = out[30:34, 30:34]
        self.assertGreater(int(core[:, :, 1].mean()), 80)
        corner = out[0:4, 0:4]
        self.assertTrue(np.all(np.abs(corner.astype(int) - SF.VIEW_FOG) <= 4))

    def test_global_fade_is_continuous_across_a_tile_seam(self):
        """Two neighbouring 512-px tiles sharing one coverage field meet at the seam."""
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "tiles"
            (dest / "0").mkdir(parents=True)
            # A circle of city centred on the vertical seam at x=512, y=256.
            # Split into tile (0,0) and (1,0).
            for tx in (0, 1):
                img = np.full((512, 512, 3), SF.VIEW_FOG, dtype=np.uint8)
                # also sprinkle a per-tile washed halo that would seam if faded locally
                img[:] = (210, 208, 200)
                yy, xx = np.ogrid[:512, :512]
                world_x = xx + tx * 512
                r = np.sqrt((world_x - 512.0) ** 2 + (yy - 256.0) ** 2)
                img[r <= 180] = (40, 120, 90)
                Image.fromarray(img, "RGB").save(dest / "0" / f"{tx}_0.webp", "WEBP")
            grid = {"ox": 0, "oy": 0, "cols": 4, "rows": 2}
            field = SF.build_signed_distance(
                dest, grid, scale=8, fade=64, inset=8, pad_rgb=self.PAD, erode=0,
            )
            d0 = SF.upsample_distance(field, 0, 0, scale=8)
            d1 = SF.upsample_distance(field, 1, 0, scale=8)
            # Distance itself is continuous: adjacent pixel centres differ by ~1
            seam = np.abs(d0[:, -1] - d1[:, 0])
            self.assertLess(float(np.median(seam)), 2.5)
            self.assertLess(float(seam.max()), 8.0)
            left = np.asarray(Image.open(dest / "0" / "0_0.webp").convert("RGB"))
            right = np.asarray(Image.open(dest / "0" / "1_0.webp").convert("RGB"))
            out_l, _ = SF.apply_coverage_fade(left, d0, fade=64, inset=8, pad_rgb=self.PAD)
            out_r, _ = SF.apply_coverage_fade(right, d1, fade=64, inset=8, pad_rgb=self.PAD)
            self.assertIsNotNone(out_l)
            self.assertIsNotNone(out_r)
            # RGB at the shared edge is nearly identical — no tile-aligned jump
            jump = np.abs(out_l[:, -1].astype(int) - out_r[:, 0].astype(int)).mean()
            self.assertLess(float(jump), 8.0, jump)
            # The washed halo outside the circle became viewer fog, not gray blocks
            # A far corner of the left tile (no circle) is beige
            self.assertTrue(
                np.all(np.abs(out_l[10:20, 10:20].astype(int) - SF.VIEW_FOG) <= 8)
            )

    def test_restore_fills_beige_holes_with_toned_procedural_land(self):
        rest = np.full((64, 64, 3), (40, 110, 80), dtype=np.uint8)
        rest[16:48, 16:48] = SF.VIEW_FOG  # a washed hole
        proc = np.full((64, 64, 3), (50, 130, 70), dtype=np.uint8)
        # procedural fog around the outside must not be copied
        proc[0:4, :] = (6, 7, 6)
        out, n = SF.restore_from_procedural(rest, proc, pad_rgb=self.PAD, feather=4)
        self.assertGreater(n, 30 * 30)
        # hole is now land, green, not beige
        hole = out[24:40, 24:40]
        self.assertGreater(int(hole[:, :, 1].mean()), 80)
        self.assertFalse(np.all(np.abs(hole.astype(int) - SF.VIEW_FOG) <= 6))
        # good restyle around the hole is still the original city colour
        rim = out[2:8, 2:8]
        self.assertTrue(np.allclose(rim, (40, 110, 80), atol=2))
        # procedural dark fog was not painted into the restyle
        self.assertTrue(np.allclose(out[0:4, 8:16], (40, 110, 80), atol=2))

    def test_restore_does_not_copy_procedural_fog_into_an_empty_tile(self):
        rest = np.full((32, 32, 3), SF.VIEW_FOG, dtype=np.uint8)
        proc = np.full((32, 32, 3), (8, 10, 9), dtype=np.uint8)  # all dark fog
        out, n = SF.restore_from_procedural(rest, proc, pad_rgb=self.PAD, feather=2)
        self.assertEqual(n, 0)
        self.assertTrue(np.allclose(out, rest))

    def test_restore_land_tiles_creates_a_missing_tile_from_procedural(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            restyle = root / "restyle"
            proc = root / "proc"
            (restyle / "0").mkdir(parents=True)
            (proc / "0").mkdir(parents=True)
            # missing restyle cell, procedural has a green hill
            Image.new("RGB", (512, 512), (45, 120, 60)).save(proc / "0" / "2_1.webp", "WEBP")
            # a good city tile is left alone
            Image.new("RGB", (512, 512), (80, 130, 100)).save(restyle / "0" / "0_0.webp", "WEBP")
            Image.new("RGB", (512, 512), (80, 130, 100)).save(proc / "0" / "0_0.webp", "WEBP")
            grid = {"ox": 0, "oy": 0, "cols": 8, "rows": 8}
            present = SF.restore_land_tiles(
                restyle, grid, proc, pad_rgb=self.PAD,
                progress_path=root / "land.jsonl",
            )
            self.assertIn((2, 1), present)
            self.assertTrue((restyle / "0" / "2_1.webp").exists())
            hill = np.asarray(Image.open(restyle / "0" / "2_1.webp").convert("RGB"))
            self.assertGreater(int(hill[:, :, 1].mean()), 80)
            city = np.asarray(Image.open(restyle / "0" / "0_0.webp").convert("RGB"))
            self.assertGreater(int(city[10, 10, 1]), 100)

    def test_polish_smooth_edge_restores_then_fades_without_a_seam(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            restyle = root / "restyle"
            proc = root / "proc"
            (restyle / "0").mkdir(parents=True)
            (proc / "0").mkdir(parents=True)
            # 2×1 tiles. Procedural is a green half-plane (y > 200) across both.
            # Restyle has the green on tile 0 only; tile 1 is missing (washed out).
            green = (40, 120, 70)
            for tx in (0, 1):
                pimg = np.full((512, 512, 3), (8, 10, 9), dtype=np.uint8)
                pimg[200:, :] = green
                Image.fromarray(pimg, "RGB").save(proc / "0" / f"{tx}_0.webp", "WEBP")
            r0 = np.full((512, 512, 3), SF.VIEW_FOG, dtype=np.uint8)
            r0[200:, :] = green
            Image.fromarray(r0, "RGB").save(restyle / "0" / "0_0.webp", "WEBP")
            grid = {"ox": 0, "oy": 0, "cols": 4, "rows": 2}
            present = SF.polish_smooth_edge(
                restyle, grid, proc_dest=proc, pad_rgb=self.PAD, fade=48, inset=8,
            )
            self.assertIn((0, 0), present)
            self.assertIn((1, 0), present)
            left = np.asarray(Image.open(restyle / "0" / "0_0.webp").convert("RGB"))
            right = np.asarray(Image.open(restyle / "0" / "1_0.webp").convert("RGB"))
            # restored land on the right is green, not beige, in the solid interior
            self.assertGreater(int(right[400, 256, 1]), 70)
            # seam in the solid interior (well inside the half-plane)
            jump = np.abs(left[400, -1].astype(int) - right[400, 0].astype(int)).mean()
            self.assertLess(float(jump), 10.0, jump)
            # far above the half-plane (procedural fog) is viewer beige, not dark
            self.assertTrue(np.all(np.abs(left[20, 20].astype(int) - SF.VIEW_FOG) <= 8))




class OverviewFromZ0Test(unittest.TestCase):
    """write_overview_from_z0 must not leave a pad-coloured grid."""

    PAD = SF.VIEW_FOG
    CITY = (50, 120, 90)

    def _write_grid(self, root: Path, cols: int, rows: int, colour=None):
        colour = colour or self.CITY
        dest = root / "tiles"
        (dest / "0").mkdir(parents=True)
        for ty in range(rows):
            for tx in range(cols):
                Image.new("RGB", (512, 512), colour).save(
                    dest / "0" / f"{tx}_{ty}.webp", "WEBP"
                )
        return dest

    def test_abutting_tiles_leave_no_pad_grid(self):
        # 8×4 tiles, max_width=100 → TILE*scale = 12.5, the case that
        # used to open a 1-px beige gap on every other column.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dest = self._write_grid(root, 8, 4)
            info = SF.write_overview_from_z0(
                dest, {"cols": 8, "rows": 4}, root, max_width=100, pad_rgb=self.PAD,
            )
            self.assertEqual(info["width"], 100)
            ov = np.asarray(Image.open(root / "overview.webp").convert("RGB"))
            self.assertEqual(ov.shape[1], 100)
            fog = np.all(np.abs(ov.astype(int) - self.PAD) <= 2, axis=2)
            # the whole canvas is covered by tiles, so no pad should remain
            self.assertEqual(int(fog.sum()), 0, f"pad pixels={int(fog.sum())}")
            # city colour is present (not blown out to beige)
            self.assertGreater(int(ov[:, :, 1].mean()), 80)

    def test_tinysz_core_scale_has_no_column_gaps(self):
        # same rounding as the real core overview: 168 cols, width 2048
        # (TILE*scale ≈ 12.190). Use a short 168×2 strip.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dest = self._write_grid(root, 168, 2)
            info = SF.write_overview_from_z0(
                dest, {"cols": 168, "rows": 2}, root, max_width=2048, pad_rgb=self.PAD,
            )
            self.assertEqual(info["width"], 2048)
            ov = np.asarray(Image.open(root / "overview.webp").convert("RGB"))
            fog = np.all(np.abs(ov.astype(int) - self.PAD) <= 2, axis=2)
            self.assertEqual(int(fog.sum()), 0, f"pad pixels={int(fog.sum())}")
            # no 1-px bright columns of a different colour
            col_mean = ov.astype(np.float32).mean(axis=(0, 2))
            jumps = np.abs(np.diff(col_mean))
            self.assertLess(float(jumps.max()), 8.0, jumps.max())




if __name__ == "__main__":
    unittest.main()
