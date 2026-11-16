"""Tests for web_tiles.py: the compressed web pyramid next to the originals."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

import web_tiles as WT


def fake_encode(_cwebp, src: Path, out: Path) -> dict:
    Image.open(src).convert("RGB").save(out, "WEBP", quality=50)
    return {"tile": src.name, "mode": "fake", "orig": src.stat().st_size, "bytes": out.stat().st_size, "psnr": 0}


def world(root: Path) -> Path:
    w = root / "w"
    rng = np.random.default_rng(0)
    for z, names in {0: ["0_0", "1_0", "0_1"], 1: ["0_0"]}.items():
        d = w / "tiles-restyled" / str(z)
        d.mkdir(parents=True)
        for n in names:
            a = rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)
            Image.fromarray(a).save(d / f"{n}.webp", "WEBP", lossless=True)
    (w / "manifest.json").write_text(json.dumps({"name": "w", "build": "abc", "restyle": {"dir": "tiles-restyled"}}))
    return w


class WebTilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.w = world(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def snapshot(self, d: Path) -> dict:
        return {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob("*.webp"))}

    def test_builds_sibling_pyramid_and_keeps_originals(self):
        before = self.snapshot(self.w / "tiles-restyled")
        s = WT.build_web_tiles(self.w, encode=fake_encode, workers=1)
        self.assertEqual(self.snapshot(self.w / "tiles-restyled"), before)
        web = self.w / "tiles-web"
        self.assertEqual(sorted(self.snapshot(web)), sorted(before))
        self.assertEqual(s["encoded"], {"fake": 3})
        self.assertEqual((web / "1" / "0_0.webp").read_bytes(), before["1/0_0.webp"])

    def test_manifest_points_at_web_dir_with_encoding_and_new_build(self):
        WT.build_web_tiles(self.w, encode=fake_encode, workers=1)
        m = json.loads((self.w / "manifest.json").read_text())
        self.assertEqual(m["restyle"]["dir"], "tiles-web")
        enc = m["restyle"]["encoding"]
        self.assertEqual((enc["quality"], enc["method"], enc["sharp_yuv"], enc["source"]), (WT.QUALITY, WT.METHOD, True, "tiles-restyled"))
        self.assertNotEqual(m["build"], "abc")

    def test_rerun_is_a_no_op(self):
        WT.build_web_tiles(self.w, encode=fake_encode, workers=1)
        b1 = json.loads((self.w / "manifest.json").read_text())["build"]
        time.sleep(0.01)
        s = WT.build_web_tiles(self.w, encode=fake_encode, workers=1)
        self.assertEqual(s["encoded"], {})
        self.assertEqual(json.loads((self.w / "manifest.json").read_text())["build"], b1)

    def test_rerun_redoes_only_changed_and_drops_removed(self):
        WT.build_web_tiles(self.w, encode=fake_encode, workers=1)
        src = self.w / "tiles-restyled" / "0"
        future = time.time() + 5
        os.utime(src / "1_0.webp", (future, future))
        (src / "0_1.webp").unlink()
        s = WT.build_web_tiles(self.w, encode=fake_encode, workers=1)
        self.assertEqual(s["encoded"], {"fake": 1})
        self.assertFalse((self.w / "tiles-web" / "0" / "0_1.webp").exists())

    def test_param_change_reencodes_everything(self):
        WT.build_web_tiles(self.w, encode=fake_encode, workers=1)
        old = WT.QUALITY
        try:
            WT.QUALITY = old - 5
            s = WT.build_web_tiles(self.w, encode=fake_encode, workers=1)
        finally:
            WT.QUALITY = old
        self.assertEqual(s["encoded"], {"fake": 3})

    def test_refuses_to_overwrite_source(self):
        with self.assertRaises(SystemExit):
            WT.build_web_tiles(self.w, web_dir="tiles-restyled", encode=fake_encode, workers=1)

    @unittest.skipUnless(os.environ.get("CWEBP") or shutil.which("cwebp"), "cwebp not installed")
    def test_real_cwebp_is_smaller_or_keeps_original(self):
        d = self.w / "tiles-restyled" / "0"
        a = np.zeros((128, 128, 3), np.uint8)
        a[:, :64] = (40, 90, 76)
        a[:, 64:] = (210, 205, 190)
        Image.fromarray(a).save(d / "0_0.webp", "WEBP", lossless=True)
        WT.build_web_tiles(self.w, workers=1)
        for n in ("0_0", "1_0", "0_1"):
            self.assertLessEqual((self.w / "tiles-web" / "0" / f"{n}.webp").stat().st_size, (d / f"{n}.webp").stat().st_size)
        log = [json.loads(l) for l in (self.w / "tiles-web" / ".encode-log.jsonl").read_text().splitlines()]
        self.assertEqual({r["tile"] for r in log}, {"0_0.webp", "1_0.webp", "0_1.webp"})


if __name__ == "__main__":
    unittest.main()
