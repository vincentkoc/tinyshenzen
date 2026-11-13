"""Feather-blend restyled tiles into an output, in horizontal bands so a 23k-wide
Futian canvas does not need a multi-gigabyte float accumulator.

The hem matches `edgeWeight` in tinyworlds-pipeline/src/restyle.ts."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from tiles import DEFAULT_OVERLAP, Tile

Image.MAX_IMAGE_PIXELS = None


def tile_weights(tile: Tile, overlap: int) -> np.ndarray:
    """(h, w) smooth-step hem, 1 in the interior, 0 at the outer edge."""
    yy = np.arange(tile.h) + 0.5
    xx = np.arange(tile.w) + 0.5
    d = np.minimum(
        np.minimum(yy[:, None], tile.h - yy[:, None]),
        np.minimum(xx[None, :], tile.w - xx[None, :]),
    )
    wt = np.ones((tile.h, tile.w), dtype=np.float64)
    if overlap <= 0:
        return wt
    inside = d >= overlap
    outside = d <= 0
    mid = ~inside & ~outside
    t = np.empty_like(d)
    t[mid] = d[mid] / overlap
    wt[mid] = t[mid] * t[mid] * (3 - 2 * t[mid])
    wt[outside] = 0.0
    return wt


def stitch_bands(
    tiles: list[Tile],
    tile_paths: dict[tuple[int, int], Path],
    width: int,
    height: int,
    overlap: int = DEFAULT_OVERLAP,
    out_path: Path | None = None,
    preview_path: Path | None = None,
    preview_max_width: int = 4096,
    missing: str = "skip",
) -> Image.Image:
    """Blend every tile into a full RGB image. Writes PNG/JPEG when paths are given."""
    step = max(1, (tiles[0].h - overlap) if tiles else height)
    raw_path = (out_path.with_suffix(".rgb") if out_path else Path("/tmp/stitch.rgb"))
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    mm = np.memmap(raw_path, dtype=np.uint8, mode="w+", shape=(height, width, 3))
    mm[:] = 0

    for y0 in range(0, height, step):
        y1 = min(height, y0 + step)
        band_h = y1 - y0
        acc = np.zeros((band_h, width, 3), dtype=np.float64)
        wgt = np.zeros((band_h, width), dtype=np.float64)
        for t in tiles:
            if t.y + t.h <= y0 or t.y >= y1:
                continue
            path = tile_paths.get((t.x, t.y))
            if path is None or not Path(path).exists():
                if missing == "skip":
                    continue
                rgb = np.zeros((t.h, t.w, 3), dtype=np.uint8)
            else:
                with Image.open(path) as im:
                    rgb = np.asarray(im.convert("RGB").resize((t.w, t.h), Image.Resampling.NEAREST))
            wt = tile_weights(t, overlap if len(tiles) > 1 else 0)
            iy0 = max(t.y, y0)
            iy1 = min(t.y + t.h, y1)
            src_y0, src_y1 = iy0 - t.y, iy1 - t.y
            dst_y0, dst_y1 = iy0 - y0, iy1 - y0
            sl = wt[src_y0:src_y1, :, None]
            acc[dst_y0:dst_y1, t.x : t.x + t.w, :] += rgb[src_y0:src_y1] * sl
            wgt[dst_y0:dst_y1, t.x : t.x + t.w] += wt[src_y0:src_y1]
        denom = np.maximum(wgt, 1e-8)[:, :, None]
        mm[y0:y1] = np.clip(acc / denom, 0, 255).astype(np.uint8)
        print(f"  stitch rows {y0}–{y1} / {height}", flush=True)

    mm.flush()
    im = Image.fromarray(np.asarray(mm), "RGB")
    if out_path is not None:
        im.save(out_path, "PNG", compress_level=4)
        print(f"  wrote {out_path} {width}×{height}", flush=True)
    if preview_path is not None:
        pw = min(preview_max_width, width)
        ph = max(1, round(height * (pw / width)))
        prev = im.resize((pw, ph), Image.Resampling.LANCZOS)
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        prev.convert("RGB").save(preview_path, "JPEG", quality=90, optimize=True)
        print(f"  preview {preview_path} {pw}×{ph}", flush=True)
    del mm
    try:
        raw_path.unlink(missing_ok=True)
    except OSError:
        pass
    return im
