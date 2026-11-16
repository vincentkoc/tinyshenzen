#!/usr/bin/env python3
"""Slice a city render into the 512-px webp pyramid tinyworlds-frontend
expects (same grid as the pipeline's makeGrid).

Default (one image): write the restyled stitch to `tiles-restyled/`, and
optionally a procedural pyramid from the 2048-px chunks to `tiles/`.

Multi-district (`--district chunks:tiles`, repeatable): build one pyramid
for a larger world (tinysz-core) from each district's constrained 1024-px
windows, without assembling a multi-gigabyte canvas. Each district is
reprojected from its own meta.geo origin into the world's geo (Nanshan is
centred on Houhai, Luohu on Caiwuwei, the core on Civic Center).
`--tilewise` also slices the procedural layer from 2048-px chunks the same
way. `--polish` is a free, no-API pass over already-sliced z0 tiles: it
replaces the renderer's dark edge fog with feathered viewer fog so the
zoomed-out map has no stair-stepped dark border, and fills blocky or
missing bay-water from the land/water mask with a tone-matched sample
(procedural water, shifted to the restyle teal). `--polish-edge` then
restores land that the first pass washed out or dropped (tone-matched
from the procedural layer) and fades the coverage edge with a single
global signed-distance field, so the blend to viewer beige has no
per-tile seams — including over open water. `--polish-vignette` is
the preferred free edge pass: it reslices the edge from the source
restyle windows (never from already-faded dest tiles), keeps the bay
water fill and south-shore land restore, builds one coarse coverage
mask with a wide blur and morphological close/open so the boundary
does not follow source-tile edges, upsamples the distance field with
bicubic interpolation, and applies a single vignette (~1–2 tiles) to
viewer beige whose falloff is displaced by low-frequency noise so the
edge looks organic rather than stepped in squares. It is idempotent
from source. `--polish-water` then puts bay water on one teal (fine
wave texture kept, no tone step on tile seams), fills the restyle's fog
holes over open water and fades water along its own smoothed body
field; `--polish-forest` cross-fades the gray-washed land just inside
the edge band to the vignette's clean staging and evens restyle-window
greens near the edge and on forest-dominant ground inland;
`--polish-flats` fades featureless ground near the edge (flat fill,
gray puddles, dithered blotches) and the water beside it into the fog
with a soft cross-tile mask. All are free and idempotent, and run
after `--polish-vignette`, passed together. `--polish-fogline` runs
last, on its own: near the edge it fades fog-dominated pixels (dashed
traces in fogged ground, gray-washed windows, straight cut-offs) to
beige with a feathered, noise-wobbled ramp. After it, `--polish-mint`
turns teal procedural-staging rims toward the forest hue, and
`--polish-spots FILE` applies a short list of one-off fixes (fog
ellipses, water blob reshapes, milky river veils) from a JSON file in
global z0 pixels. `--polish-seams` then feathers a tone jump across a
z0 tile edge, and across a restyle-window hem when those windows are
still on disk, but only where both sides are the same kind of surface,
so a building cut or a river bank is not smeared. `--polish-tone`
supersedes it: from the pre-seam tiles it fits one original->restyle
colour transform for the whole city, cancels consistent tone steps on
tile and window edges, adds a robust low-pass correction so every window
lands on that one house tone, and refills washed-out patches from the
original (see tone.py). Higher zoom levels
are rebuilt with viewer-fog padding so missing children do not become
dark squares. Generated images are not committed.
"""

from __future__ import annotations

import argparse
import json
import math
import pickle
import shutil
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

Image.MAX_IMAGE_PIXELS = None

TILE = 512
CHUNK_TILES = 4
CHUNK = TILE * CHUNK_TILES  # 2048

EARTH_RADIUS = 6371008.8
_RAD = math.pi / 180


def to_map(geo: dict, lon: float, lat: float) -> tuple[float, float]:
    """Ground lon/lat → map px, same formula as tinyworlds-frontend projection.ts."""
    k = float(geo["k"])
    lon0 = float(geo["lon0"])
    lat0 = float(geo["lat0"])
    beta = float(geo.get("betaDeg") or 0) * _RAD
    sinb, cosb = math.sin(beta), math.cos(beta)
    m_per_lon = _RAD * EARTH_RADIUS * math.cos(lat0 * _RAD)
    m_per_lat = _RAD * EARTH_RADIUS
    east = (lon - lon0) * m_per_lon
    north = (lat - lat0) * m_per_lat
    p = east * sinb + north * cosb
    q = east * cosb - north * sinb
    return (p + q) * k, ((q - p) * k) / 2


def district_world_origin(dmeta: dict, world_geo: dict) -> tuple[int, int, float, float]:
    """Translate a district canvas origin into the world's map coordinates.

    District renders use their own city.origin as lon0/lat0 (Houhai, Caiwuwei).
    The core world uses Civic Center. Treating the district meta.origin as
    already in world space puts Nanshan and Luohu many kilometres off.
    The two geos share k and beta, and differ only slightly in cos(lat0), so
    a translation of the district's (lon0, lat0) into world space is enough
    (about 0.1 px of residual over a 1024-px window).
    """
    ox, oy = int(dmeta["origin"]["x"]), int(dmeta["origin"]["y"])
    dgeo = dmeta.get("geo") or {}
    if not dgeo or not world_geo or "lon0" not in dgeo or "lon0" not in world_geo:
        return ox, oy, 0.0, 0.0
    same = (
        abs(float(dgeo["lon0"]) - float(world_geo["lon0"])) < 1e-9
        and abs(float(dgeo["lat0"]) - float(world_geo["lat0"])) < 1e-9
        and abs(float(dgeo.get("k") or 2) - float(world_geo.get("k") or 2)) < 1e-12
        and abs(float(dgeo.get("betaDeg") or 0) - float(world_geo.get("betaDeg") or 0)) < 1e-12
    )
    if same:
        return ox, oy, 0.0, 0.0
    dx, dy = to_map(world_geo, float(dgeo["lon0"]), float(dgeo["lat0"]))
    return ox + round(dx), oy + round(dy), dx, dy




def make_grid(min_x: int, min_y: int, max_x: int, max_y: int) -> dict:
    ox = (min_x // CHUNK) * CHUNK
    oy = (min_y // CHUNK) * CHUNK
    ccols = int(np.ceil((max_x - ox) / CHUNK))
    crows = int(np.ceil((max_y - oy) / CHUNK))
    return {
        "ox": ox,
        "oy": oy,
        "cols": ccols * CHUNK_TILES,
        "rows": crows * CHUNK_TILES,
        "ccols": ccols,
        "crows": crows,
        "chunk": CHUNK,
    }


def pack_bits(present: set[tuple[int, int]], cols: int, rows: int) -> str:
    import base64

    n = cols * rows
    buf = bytearray((n + 7) // 8)
    for ty in range(rows):
        for tx in range(cols):
            if (tx, ty) not in present:
                continue
            bit = tx + ty * cols
            buf[bit >> 3] |= 1 << (bit & 7)
    return base64.b64encode(bytes(buf)).decode("ascii")


def unpack_bits(b64: str, cols: int, rows: int) -> set[tuple[int, int]]:
    import base64

    buf = base64.b64decode(b64)
    present: set[tuple[int, int]] = set()
    for ty in range(rows):
        for tx in range(cols):
            bit = tx + ty * cols
            if bit >> 3 >= len(buf):
                continue
            if buf[bit >> 3] & (1 << (bit & 7)):
                present.add((tx, ty))
    return present


def _save_webp(im: Image.Image, path: Path, *, lossless: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if lossless:
        im.save(path, "WEBP", lossless=True, method=2, exact=True)
    else:
        im.save(path, "WEBP", quality=76, method=2)


def _almost_empty(arr: np.ndarray, pad_rgb: tuple[int, int, int]) -> bool:
    """True when the tile is uniformly pad, transparent, or viewer fog."""
    if arr.ndim == 3 and arr.shape[2] == 4:
        if int(arr[:, :, 3].max()) == 0:
            return True
        rgb = arr[:, :, :3]
    else:
        rgb = arr
    if float(empty_mask(rgb, pad_rgb).mean()) > 0.995:
        return True
    # a few pixels of content are enough to keep the tile
    return bool(np.mean(np.abs(rgb.astype(np.int16) - np.array(pad_rgb, dtype=np.int16))) < 1.5)


def slice_image(
    image: Image.Image,
    grid: dict,
    img_origin: tuple[int, int],
    dest: Path,
    *,
    pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
    lossless_z0: bool = True,
) -> list[dict]:
    """Write every level under `dest/{z}/{tx}_{ty}.webp`. Returns manifest levels."""
    ox, oy = grid["ox"], grid["oy"]
    cols, rows = grid["cols"], grid["rows"]
    min_x, min_y = img_origin
    width, height = image.size
    src = image.convert("RGB")

    present: dict[int, set[tuple[int, int]]] = {0: set()}
    t0 = time.time()
    written = 0
    for ty in range(rows):
        for tx in range(cols):
            left = ox + tx * TILE
            top = oy + ty * TILE
            sx = left - min_x
            sy = top - min_y
            # pad canvas, paste the overlapping slice of the source
            tile = Image.new("RGB", (TILE, TILE), pad_rgb)
            src_box = (
                max(0, sx),
                max(0, sy),
                min(width, sx + TILE),
                min(height, sy + TILE),
            )
            if src_box[2] > src_box[0] and src_box[3] > src_box[1]:
                crop = src.crop(src_box)
                tile.paste(crop, (src_box[0] - sx, src_box[1] - sy))
            arr = np.asarray(tile)
            if _almost_empty(arr, pad_rgb):
                continue
            _save_webp(tile, dest / "0" / f"{tx}_{ty}.webp", lossless=lossless_z0)
            present[0].add((tx, ty))
            written += 1
        if (ty + 1) % 4 == 0 or ty + 1 == rows:
            print(f"  z0 rows {ty + 1}/{rows}, {written} tiles, {time.time() - t0:.1f}s", flush=True)

    levels = [{"z": 0, "cols": cols, "rows": rows, "tiles": pack_bits(present[0], cols, rows)}]
    z = 1
    z_cols, z_rows = cols, rows
    while z_cols > 1 or z_rows > 1:
        p_cols, p_rows = z_cols, z_rows
        z_cols = (z_cols + 1) // 2
        z_rows = (z_rows + 1) // 2
        present[z] = set()
        for ty in range(z_rows):
            for tx in range(z_cols):
                big = Image.new("RGB", (TILE * 2, TILE * 2), pad_rgb)
                any_child = False
                for dy in range(2):
                    for dx in range(2):
                        cx, cy = tx * 2 + dx, ty * 2 + dy
                        if (cx, cy) not in present.get(z - 1, ()):
                            continue
                        child = dest / str(z - 1) / f"{cx}_{cy}.webp"
                        if not child.exists():
                            continue
                        with Image.open(child) as im:
                            big.paste(im.convert("RGB"), (dx * TILE, dy * TILE))
                        any_child = True
                if not any_child:
                    continue
                small = big.resize((TILE, TILE), Image.Resampling.LANCZOS)
                _save_webp(small, dest / str(z) / f"{tx}_{ty}.webp", lossless=False)
                present[z].add((tx, ty))
        levels.append({"z": z, "cols": z_cols, "rows": z_rows, "tiles": pack_bits(present[z], z_cols, z_rows)})
        print(f"  z{z} {z_cols}×{z_rows}, {len(present[z])} tiles", flush=True)
        z += 1
    return levels



# Dark restyle fog (see restyle_map.composite_dark_fog). Later districts
# overwrite earlier ones only where they are not this dark.
FOG_LUM_MAX = 48.0
# The viewer's light fog (`--fog: #e9e6df` in globals.css). Restyle windows
# at a district's empty edge are often filled with this exact colour; if we
# paste it over a neighbour it cuts a beige rectangle out of the city.
VIEW_FOG = (0xE9, 0xE6, 0xDF)
VIEW_FOG_TOL = 2


def _lum(arr: np.ndarray) -> np.ndarray:
    return arr.astype(np.float32).mean(axis=2)


def empty_mask(arr: np.ndarray, pad_rgb: tuple[int, int, int]) -> np.ndarray:
    """True where a restyle pixel is fog, pad, or unpainted — not city."""
    rgb = arr[:, :, :3] if arr.ndim == 3 and arr.shape[2] == 4 else arr
    lum = rgb.astype(np.float32).mean(axis=2)
    pad = np.array(pad_rgb, dtype=np.int16)
    fog_c = np.array(VIEW_FOG, dtype=np.int16)
    is_dark = lum < FOG_LUM_MAX
    is_pad = np.all(np.abs(rgb.astype(np.int16) - pad) < 2, axis=2)
    is_black = np.all(rgb == 0, axis=2)
    is_view_fog = np.all(np.abs(rgb.astype(np.int16) - fog_c) <= VIEW_FOG_TOL, axis=2)
    return is_dark | is_pad | is_black | is_view_fog


def is_viewer_fog_tile(arr: np.ndarray) -> bool:
    """True when a tile is uniformly the viewer's light fog — empty, not city
    and not the renderer's dark edge fog, which we do want to fill."""
    rgb = arr[:, :, :3] if arr.ndim == 3 and arr.shape[2] == 4 else arr
    fog_c = np.array(VIEW_FOG, dtype=np.int16)
    return bool(np.all(np.abs(rgb.astype(np.int16) - fog_c) <= VIEW_FOG_TOL, axis=2).mean() > 0.995)


def extract_district_roi(
    tiles_dir: Path,
    windows: list,
    origin: tuple[int, int],
    width: int,
    height: int,
    world_x: int,
    world_y: int,
    tw: int,
    th: int,
    overlap: int = 256,
) -> Image.Image | None:
    """Feather-blend the overlapping 1024-px windows of one district into a ROI
    in world coordinates. Returns None when the ROI misses the district canvas."""
    from blend import tile_weights

    ox, oy = origin
    lx, ly = world_x - ox, world_y - oy
    if lx + tw <= 0 or ly + th <= 0 or lx >= width or ly >= height:
        return None
    acc = np.zeros((th, tw, 3), dtype=np.float64)
    wgt = np.zeros((th, tw), dtype=np.float64)
    any_tile = False
    for t in windows:
        if t.x + t.w <= lx or t.x >= lx + tw or t.y + t.h <= ly or t.y >= ly + th:
            continue
        path = tiles_dir / f"{t.x}_{t.y}.png"
        if not path.exists():
            continue
        with Image.open(path) as im:
            rgb = np.asarray(im.convert("RGB").resize((t.w, t.h), Image.Resampling.NEAREST))
        wt = tile_weights(t, overlap)
        iy0 = max(t.y, ly)
        iy1 = min(t.y + t.h, ly + th)
        ix0 = max(t.x, lx)
        ix1 = min(t.x + t.w, lx + tw)
        sy0, sy1 = iy0 - t.y, iy1 - t.y
        sx0, sx1 = ix0 - t.x, ix1 - t.x
        dy0, dy1 = iy0 - ly, iy1 - ly
        dx0, dx1 = ix0 - lx, ix1 - lx
        sl = wt[sy0:sy1, sx0:sx1, None]
        acc[dy0:dy1, dx0:dx1, :] += rgb[sy0:sy1, sx0:sx1] * sl
        wgt[dy0:dy1, dx0:dx1] += wt[sy0:sy1, sx0:sx1]
        any_tile = True
    if not any_tile:
        return None
    denom = np.maximum(wgt, 1e-8)[:, :, None]
    out = np.clip(acc / denom, 0, 255).astype(np.uint8)
    # pixels with no coverage stay pad-black; mark them later
    missing = wgt < 1e-8
    if missing.any():
        out[missing] = (0, 0, 0)
    return Image.fromarray(out, "RGB")


def composite_district(base: Image.Image, incoming: Image.Image, pad_rgb: tuple[int, int, int]) -> Image.Image:
    """Paste incoming over base, skipping fog, pad and unpainted beige so a
    later district's empty edge cannot cut a hole in an earlier district's city."""
    b = np.asarray(base.convert("RGB"))
    n = np.asarray(incoming.convert("RGB"))
    use = ~empty_mask(n, pad_rgb)
    if not use.any():
        return base
    out = b.copy()
    out[use] = n[use]
    return Image.fromarray(out, "RGB")


def slice_restyle_tilewise(
    districts: list[dict],
    grid: dict,
    dest: Path,
    *,
    pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
    lossless_z0: bool = True,
) -> list[dict]:
    """Write z0 from district window sets, then the downsampled levels."""
    ox, oy = grid["ox"], grid["oy"]
    cols, rows = grid["cols"], grid["rows"]
    present: dict[int, set[tuple[int, int]]] = {0: set()}
    t0 = time.time()
    written = 0
    dest.mkdir(parents=True, exist_ok=True)
    for ty in range(rows):
        for tx in range(cols):
            left = ox + tx * TILE
            top = oy + ty * TILE
            tile = Image.new("RGB", (TILE, TILE), pad_rgb)
            painted = False
            for d in districts:
                roi = extract_district_roi(
                    d["tiles"],
                    d["windows"],
                    d["origin"],
                    d["width"],
                    d["height"],
                    left,
                    top,
                    TILE,
                    TILE,
                )
                if roi is None:
                    continue
                tile = composite_district(tile, roi, pad_rgb)
                painted = True
            if not painted:
                continue
            arr = np.asarray(tile)
            if _almost_empty(arr, pad_rgb):
                continue
            _save_webp(tile, dest / "0" / f"{tx}_{ty}.webp", lossless=lossless_z0)
            present[0].add((tx, ty))
            written += 1
        if (ty + 1) % 4 == 0 or ty + 1 == rows:
            print(f"  z0 rows {ty + 1}/{rows}, {written} tiles, {time.time() - t0:.1f}s", flush=True)
    removed = cleanup_absent_tiles(dest, present[0], cols, rows)
    if removed:
        print(f"  removed {removed} leftover z0 tiles", flush=True)
    return _pyramid_from_z0(dest, grid, present, pad_rgb)


def _districts_covering(districts: list[dict], left: int, top: int, tw: int = TILE, th: int = TILE) -> list[dict]:
    hits = []
    for d in districts:
        dox, doy = d["origin"]
        if left + tw <= dox or top + th <= doy or left >= dox + d["width"] or top >= doy + d["height"]:
            continue
        hits.append(d)
    return hits


def repair_beige_tiles(
    districts: list[dict],
    grid: dict,
    dest: Path,
    *,
    pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
    lossless_z0: bool = True,
) -> dict[int, set[tuple[int, int]]]:
    """Re-composite z0 tiles that contain viewer-fog beige, in place.

    Used after a run that pasted a later district's empty beige edge over
    a neighbour's city. Tiles without beige are left alone. Uniform-beige
    tiles in a single district are deleted (filled later from procedural);
    those sitting in two districts are re-composited so a neighbour's city
    is not thrown away with the beige overlay.
    """
    ox, oy = grid["ox"], grid["oy"]
    cols, rows = grid["cols"], grid["rows"]
    z0 = dest / "0"
    present: dict[int, set[tuple[int, int]]] = {0: set()}
    if z0.exists():
        for path in z0.glob("*.webp"):
            stem = path.stem
            if "_" not in stem:
                continue
            sx, sy = stem.split("_", 1)
            try:
                tx, ty = int(sx), int(sy)
            except ValueError:
                continue
            if 0 <= tx < cols and 0 <= ty < rows:
                present[0].add((tx, ty))
    t0 = time.time()
    repaired = 0
    removed = 0

    def overlaps_two(tx: int, ty: int) -> bool:
        return len(_districts_covering(districts, ox + tx * TILE, oy + ty * TILE)) >= 2

    to_visit = set(present[0])
    # Recover overlap cells we may already have deleted as uniform beige.
    for ty in range(rows):
        for tx in range(cols):
            if overlaps_two(tx, ty):
                to_visit.add((tx, ty))

    for n, (tx, ty) in enumerate(sorted(to_visit, key=lambda t: (t[1], t[0])), 1):
        path = z0 / f"{tx}_{ty}.webp"
        two = overlaps_two(tx, ty)
        needs = False
        if path.exists():
            with Image.open(path) as im:
                arr = np.asarray(im.convert("RGB"))
            if _almost_empty(arr, pad_rgb):
                if two:
                    needs = True
                else:
                    path.unlink()
                    present[0].discard((tx, ty))
                    removed += 1
                    if removed % 200 == 0:
                        print(f"  removed {removed} uniform-beige tiles, {time.time() - t0:.1f}s", flush=True)
                    continue
            else:
                fog_c = np.array(VIEW_FOG, dtype=np.int16)
                is_beige = np.all(np.abs(arr.astype(np.int16) - fog_c) <= VIEW_FOG_TOL, axis=2)
                # Ignore stray near-beige pixels in real pavement; the civic-center
                # bug is a large uniform fill (tens of percent of the tile).
                needs = bool(is_beige.mean() > 0.01)
        else:
            # Missing: only re-extract if two districts meet here (neighbour city
            # may have been deleted with a beige overlay).
            needs = two
        if not needs:
            continue
        left = ox + tx * TILE
        top = oy + ty * TILE
        tile = Image.new("RGB", (TILE, TILE), pad_rgb)
        painted = False
        for d in districts:
            roi = extract_district_roi(
                d["tiles"], d["windows"], d["origin"], d["width"], d["height"],
                left, top, TILE, TILE,
            )
            if roi is None:
                continue
            tile = composite_district(tile, roi, pad_rgb)
            painted = True
        arr = np.asarray(tile)
        if (not painted) or _almost_empty(arr, pad_rgb):
            if path.exists():
                path.unlink()
                present[0].discard((tx, ty))
                removed += 1
            continue
        _save_webp(tile, path, lossless=lossless_z0)
        present[0].add((tx, ty))
        repaired += 1
        if repaired % 50 == 0:
            print(f"  repaired {repaired} beige tiles, {time.time() - t0:.1f}s", flush=True)
    print(
        f"  repair done: {repaired} rewritten, {removed} removed, "
        f"{len(present[0])} present, {time.time() - t0:.1f}s",
        flush=True,
    )
    return present



def slice_procedural_tilewise(
    chunks: Path,
    meta: dict,
    grid: dict,
    dest: Path,
    *,
    pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
    lossless_z0: bool = True,
) -> list[dict]:
    """Write z0 from 2048-px chunks, never assembling the full canvas."""
    chunk = int(meta.get("chunk") or 2048)
    min_x, min_y = int(meta["origin"]["x"]), int(meta["origin"]["y"])
    width, height = int(meta["width"]), int(meta["height"])
    cdir = chunks / "chunks"
    ox, oy = grid["ox"], grid["oy"]
    cols, rows = grid["cols"], grid["rows"]
    present: dict[int, set[tuple[int, int]]] = {0: set()}
    t0 = time.time()
    written = 0
    dest.mkdir(parents=True, exist_ok=True)
    for ty in range(rows):
        for tx in range(cols):
            left = ox + tx * TILE
            top = oy + ty * TILE
            sx, sy = left - min_x, top - min_y
            tile = Image.new("RGB", (TILE, TILE), pad_rgb)
            painted = False
            c0, r0 = sx // chunk, sy // chunk
            c1, r1 = (sx + TILE - 1) // chunk, (sy + TILE - 1) // chunk
            for r in range(r0, r1 + 1):
                for c in range(c0, c1 + 1):
                    src = cdir / f"{c}_{r}.png"
                    if not src.exists():
                        continue
                    with Image.open(src) as im:
                        im = im.convert("RGB")
                        paste_x = c * chunk - sx
                        paste_y = r * chunk - sy
                        tile.paste(im, (paste_x, paste_y))
                        painted = True
            if not painted:
                continue
            # crop any paste that landed outside? PIL pastes clipped.
            arr = np.asarray(tile)
            if _almost_empty(arr, pad_rgb):
                continue
            _save_webp(tile, dest / "0" / f"{tx}_{ty}.webp", lossless=lossless_z0)
            present[0].add((tx, ty))
            written += 1
        if (ty + 1) % 4 == 0 or ty + 1 == rows:
            print(f"  z0 rows {ty + 1}/{rows}, {written} tiles, {time.time() - t0:.1f}s", flush=True)
    return _pyramid_from_z0(dest, grid, present, pad_rgb)


def _pyramid_from_z0(
    dest: Path,
    grid: dict,
    present: dict[int, set[tuple[int, int]]],
    pad_rgb: tuple[int, int, int],
) -> list[dict]:
    """Build z1+ from z0. Missing children are filled with `pad_rgb`.

    Use the viewer's fog as `pad_rgb` for a restyle pyramid so a missing
    neighbour does not become a dark stair-step at low zoom. Children not
    in `present` are ignored even if a leftover file is on disk. Each
    level is cleaned of stale tiles from a previous larger present set.
    """
    cols, rows = grid["cols"], grid["rows"]
    cleanup_level_tiles(dest, 0, present[0], cols, rows)
    levels = [{"z": 0, "cols": cols, "rows": rows, "tiles": pack_bits(present[0], cols, rows)}]
    z = 1
    z_cols, z_rows = cols, rows
    while z_cols > 1 or z_rows > 1:
        z_cols = (z_cols + 1) // 2
        z_rows = (z_rows + 1) // 2
        present[z] = set()
        prev = present.get(z - 1, set())
        for ty in range(z_rows):
            for tx in range(z_cols):
                big = Image.new("RGB", (TILE * 2, TILE * 2), pad_rgb)
                any_child = False
                for dy in range(2):
                    for dx in range(2):
                        cx, cy = tx * 2 + dx, ty * 2 + dy
                        if (cx, cy) not in prev:
                            continue
                        child = dest / str(z - 1) / f"{cx}_{cy}.webp"
                        if not child.exists():
                            continue
                        with Image.open(child) as im:
                            big.paste(im.convert("RGB"), (dx * TILE, dy * TILE))
                        any_child = True
                if not any_child:
                    continue
                small = big.resize((TILE, TILE), Image.Resampling.LANCZOS)
                if _almost_empty(np.asarray(small), pad_rgb):
                    continue
                _save_webp(small, dest / str(z) / f"{tx}_{ty}.webp", lossless=False)
                present[z].add((tx, ty))
        removed = cleanup_level_tiles(dest, z, present[z], z_cols, z_rows)
        levels.append({"z": z, "cols": z_cols, "rows": z_rows, "tiles": pack_bits(present[z], z_cols, z_rows)})
        extra = f", removed {removed} stale" if removed else ""
        print(f"  z{z} {z_cols}×{z_rows}, {len(present[z])} tiles{extra}", flush=True)
        z += 1
    return levels


def cleanup_absent_tiles(dest: Path, present: set[tuple[int, int]], cols: int, rows: int) -> int:
    """Remove leftover z0 files that are not in this run's present set.

    A reproject onto a smaller grid, or a previous wrong origin, leaves
    stale tiles on disk. Higher zoom levels are rebuilt from present only.
    """
    z0 = dest / "0"
    if not z0.exists():
        return 0
    removed = 0
    for path in list(z0.glob("*.webp")):
        stem = path.stem
        if "_" not in stem:
            path.unlink(missing_ok=True)
            removed += 1
            continue
        sx, sy = stem.split("_", 1)
        try:
            tx, ty = int(sx), int(sy)
        except ValueError:
            path.unlink(missing_ok=True)
            removed += 1
            continue
        if (tx, ty) not in present or tx < 0 or ty < 0 or tx >= cols or ty >= rows:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def fill_gaps_from_procedural(
    restyle_dest: Path,
    proc_dest: Path,
    grid: dict,
    present: set[tuple[int, int]],
    pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
) -> set[tuple[int, int]]:
    """Copy procedural z0 tiles into restyle cells that have no city restyle.

    The viewer uses one bitset (the restyle's) when the restyle layer is on,
    so a missing restyle cell shows the page's beige fog even if a procedural
    tile exists. Filling those cells means restyled or procedural content
    covers every data cell, and only the true map edge is fog.
    """
    cols, rows = grid["cols"], grid["rows"]
    proc0 = proc_dest / "0"
    if not proc0.exists():
        return present
    filled = 0
    for path in proc0.glob("*.webp"):
        stem = path.stem
        if "_" not in stem:
            continue
        sx, sy = stem.split("_", 1)
        try:
            tx, ty = int(sx), int(sy)
        except ValueError:
            continue
        if tx < 0 or ty < 0 or tx >= cols or ty >= rows:
            continue
        if (tx, ty) in present:
            continue
        with Image.open(path) as im:
            arr = np.asarray(im.convert("RGB"))
        if is_viewer_fog_tile(arr):
            continue
        dest = restyle_dest / "0" / f"{tx}_{ty}.webp"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        present.add((tx, ty))
        filled += 1
    print(f"  filled {filled} restyle gaps from procedural", flush=True)
    return present


def present_from_disk(dest: Path, cols: int, rows: int) -> set[tuple[int, int]]:
    """z0 tile coordinates already on disk and inside the grid."""
    present: set[tuple[int, int]] = set()
    z0 = dest / "0"
    if not z0.exists():
        return present
    for path in z0.glob("*.webp"):
        stem = path.stem
        if "_" not in stem:
            continue
        sx, sy = stem.split("_", 1)
        try:
            tx, ty = int(sx), int(sy)
        except ValueError:
            continue
        if 0 <= tx < cols and 0 <= ty < rows:
            present.add((tx, ty))
    return present


def cleanup_level_tiles(dest: Path, z: int, present: set[tuple[int, int]], cols: int, rows: int) -> int:
    """Remove leftover tiles at one zoom level that are not in `present`."""
    folder = dest / str(z)
    if not folder.exists():
        return 0
    removed = 0
    for path in list(folder.glob("*.webp")):
        stem = path.stem
        if "_" not in stem:
            path.unlink(missing_ok=True)
            removed += 1
            continue
        sx, sy = stem.split("_", 1)
        try:
            tx, ty = int(sx), int(sy)
        except ValueError:
            path.unlink(missing_ok=True)
            removed += 1
            continue
        if (tx, ty) not in present or tx < 0 or ty < 0 or tx >= cols or ty >= rows:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


# Pixel-Singapore restyle water (`sgWater` in the pipeline). Used as the
# default tone when a tile has no local good-water sample.
RESTYLE_WATER = (0x2F, 0x6D, 0x58)
EDGE_FEATHER = 48


def dark_fog_mask(arr: np.ndarray, pad_rgb: tuple[int, int, int]) -> np.ndarray:
    """True on the renderer's unpainted dark fog, not on water, trees or city.

    Fog is near the renderer pad, near-black, or dark and nearly neutral.
    Chromatic teal or green (bay water, parks) is kept, even when it's dark.
    """
    rgb = arr[:, :, :3] if arr.ndim == 3 and arr.shape[2] == 4 else arr
    lum = rgb.astype(np.float32).mean(axis=2)
    chroma = rgb.max(axis=2).astype(np.int16) - rgb.min(axis=2).astype(np.int16)
    pad = np.array(pad_rgb, dtype=np.int16)
    near_pad = np.all(np.abs(rgb.astype(np.int16) - pad) <= 10, axis=2)
    near_black = lum < 8.0
    neutral_dark = (lum < FOG_LUM_MAX) & (chroma <= 16)
    fog = near_pad | near_black | neutral_dark
    g = rgb[:, :, 1].astype(np.int16)
    r = rgb[:, :, 0].astype(np.int16)
    b = rgb[:, :, 2].astype(np.int16)
    chromatic_green = (g > r + 10) & (g > 28) & (g >= b - 20)
    return fog & ~chromatic_green


def smooth_edge_fog(
    arr: np.ndarray,
    pad_rgb: tuple[int, int, int],
    feather: int = EDGE_FEATHER,
) -> tuple[np.ndarray | None, str]:
    """Replace dark edge fog with viewer fog, feathered at the city boundary.

    Returns `(image, status)` where status is `'unchanged'`, `'changed'`, or
    `'empty'` (the tile is only fog and should be deleted). The whole dark
    band becomes solid beige; only the last `feather` pixels at the city
    edge are a blend, so a thick stair-stepped border disappears rather
    than turning into a soft dark smear.
    """
    rgb = arr[:, :, :3] if arr.ndim == 3 and arr.shape[2] == 4 else np.asarray(arr)
    fog = dark_fog_mask(rgb, pad_rgb)
    view = np.all(
        np.abs(rgb.astype(np.int16) - np.array(VIEW_FOG, dtype=np.int16)) <= VIEW_FOG_TOL,
        axis=2,
    )
    empty = fog | view
    if not fog.any():
        return rgb.copy(), "unchanged"
    if float(empty.mean()) > 0.995:
        return None, "empty"
    if feather > 0:
        blur = Image.fromarray(fog.astype(np.uint8) * 255, "L").filter(
            ImageFilter.GaussianBlur(radius=max(1.0, feather / 2.5))
        )
        alpha = np.asarray(blur, dtype=np.float32) / 255.0
    else:
        alpha = fog.astype(np.float32)
    out = rgb.astype(np.float32)
    vf = np.array(VIEW_FOG, dtype=np.float32)
    out = out * (1.0 - alpha[:, :, None]) + vf * alpha[:, :, None]
    out = np.clip(out, 0, 255).astype(np.uint8)
    if _almost_empty(out, pad_rgb):
        return None, "empty"
    return out, "changed"


def attach_water_masks(districts: list[dict]) -> int:
    """Attach memmap descriptors for each district's `water_mask.raw`, if present.

    The mask origin is in the district's local coordinates; `d['origin']` is
    already the reprojected world origin of that local origin.
    """
    attached = 0
    for d in districts:
        chunks = Path(d["chunks"])
        jpath = chunks / "water_mask.json"
        raw = chunks / "water_mask.raw"
        if not jpath.exists() or not raw.exists():
            d.pop("water_mask", None)
            continue
        meta = json.loads(jpath.read_text())
        loc = d.get("meta") or {}
        loc_o = loc.get("origin") or meta.get("origin") or {}
        d["water_mask"] = {
            "path": raw,
            "width": int(meta["width"]),
            "height": int(meta["height"]),
            "origin": (int(meta["origin"]["x"]), int(meta["origin"]["y"]),),
            "local_origin": (int(loc_o.get("x", meta["origin"]["x"])), int(loc_o.get("y", meta["origin"]["y"]))),
            "world_origin": d["origin"],
        }
        attached += 1
    return attached


def _crop_raw_mask(
    path: Path,
    height: int,
    width: int,
    x: int,
    y: int,
    w: int,
    h: int,
    mm: np.memmap | None = None,
) -> np.ndarray:
    """Crop a window from a uint8 memmap, padding out-of-bounds with 0."""
    out = np.zeros((h, w), dtype=np.uint8)
    ix0, iy0 = max(0, x), max(0, y)
    ix1, iy1 = min(width, x + w), min(height, y + h)
    if ix1 <= ix0 or iy1 <= iy0:
        return out
    owns = mm is None
    if mm is None:
        mm = np.memmap(path, dtype=np.uint8, mode="r", shape=(height, width))
    ox0, oy0 = ix0 - x, iy0 - y
    out[oy0 : oy0 + (iy1 - iy0), ox0 : ox0 + (ix1 - ix0)] = np.array(mm[iy0:iy1, ix0:ix1])
    if owns:
        del mm
    return out


def water_mask_roi(
    districts: list[dict],
    left: int,
    top: int,
    tw: int = TILE,
    th: int = TILE,
) -> np.ndarray:
    """OR of every district water mask covering this world-space rectangle.

    When a district's mask origin equals its local canvas origin (the usual
    case), mask pixel (0, 0) is world `d['origin']`.
    """
    out = np.zeros((th, tw), dtype=np.uint8)
    for d in districts:
        wm = d.get("water_mask")
        if not wm:
            continue
        wx, wy = d["origin"]
        loc_ox, loc_oy = wm["local_origin"]
        mox, moy = wm["origin"]
        # world → district-local → mask pixel
        mx = (left - wx + loc_ox) - mox
        my = (top - wy + loc_oy) - moy
        if "mm" not in wm:
            wm["mm"] = np.memmap(wm["path"], dtype=np.uint8, mode="r", shape=(wm["height"], wm["width"]))
        crop = _crop_raw_mask(wm["path"], wm["height"], wm["width"], mx, my, tw, th, mm=wm["mm"])
        if crop.max() > 0:
            np.maximum(out, crop, out=out)
    return out


def _block_std(rgb: np.ndarray, k: int = 8) -> np.ndarray:
    """Per-block luminance std, repeated to the original resolution."""
    lum = rgb.astype(np.float32).mean(axis=2)
    h, w = lum.shape
    h8, w8 = max(1, h // k), max(1, w // k)
    cut = lum[: h8 * k, : w8 * k].reshape(h8, k, w8, k)
    std = cut.std(axis=(1, 3))
    out = np.repeat(np.repeat(std, k, axis=0), k, axis=1)
    if out.shape != lum.shape:
        padded = np.zeros_like(lum)
        padded[: out.shape[0], : out.shape[1]] = out
        if out.shape[0] < h:
            padded[out.shape[0] :, : out.shape[1]] = out[-1:]
        if out.shape[1] < w:
            padded[:, out.shape[1] :] = padded[:, out.shape[1] - 1 : out.shape[1]]
        return padded
    return out


def real_water_mask(
    mask: np.ndarray,
    proc: np.ndarray | None,
    rest: np.ndarray | None = None,
) -> np.ndarray:
    """Ground-truth water: the kind mask, confirmed by procedural (or restyle) water.

    Unpainted cells in the raw mask default to water; fog covers that default
    outside the city, so a pixel is real water only when the mask agrees and
    the procedural image itself looks like water. If the procedural tile is
    missing, restyle water (including flat sgWater) is used as confirmation.
    A mask of all zeros with clear procedural water still counts — the bay
    can sit just outside a district mask.
    """
    import water_constrain as W

    kind = mask > 127 if mask is not None else np.zeros((), dtype=bool)
    if proc is not None:
        proc_w = W.looks_like_water(proc, require_smooth=False)
        if kind.any():
            return kind & proc_w
        return proc_w
    if rest is not None:
        rest_w = W.looks_like_water(rest, require_smooth=False)
        if kind.any():
            # also treat mask-water that is black or pad in the restyle as
            # water, so a failed black restyle window still gets filled
            fog = dark_fog_mask(rest, (0x1A, 0x1E, 0x1C))
            lum = rest.astype(np.float32).mean(axis=2)
            return kind & (rest_w | fog | (lum < 18))
        return rest_w
    return kind if kind.shape else np.zeros((TILE, TILE), dtype=bool)


def bad_water_mask(
    rest: np.ndarray,
    water: np.ndarray,
    proc: np.ndarray | None = None,
    pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
) -> np.ndarray:
    """Water-region pixels that should be replaced: black, pad, fog, or flat blocks."""
    if water is None or not np.any(water):
        return np.zeros(rest.shape[:2], dtype=bool)
    fog = dark_fog_mask(rest, pad_rgb)
    view = np.all(
        np.abs(rest.astype(np.int16) - np.array(VIEW_FOG, dtype=np.int16)) <= VIEW_FOG_TOL,
        axis=2,
    )
    lum = rest.astype(np.float32).mean(axis=2)
    too_dark = lum < 18.0
    bad = water & (fog | view | too_dark)
    if proc is not None:
        rest_std = _block_std(rest)
        proc_std = _block_std(proc)
        # Solid sgWater (or a 2-colour black+teal posterization) next to
        # textured procedural water is the blocky bay.
        flat = water & (rest_std < 2.5) & (proc_std > 4.0)
        darker = water & (lum < proc.astype(np.float32).mean(axis=2) - 25) & (lum < 40)
        bad = bad | flat | darker
    return bad


def _local_water_tone(rest: np.ndarray, water: np.ndarray, default: tuple[int, int, int] = RESTYLE_WATER) -> tuple[int, int, int]:
    """Median colour of good (non-bad, chromatic) restyle water, else `default`."""
    import water_constrain as W

    good = water & W.looks_like_water(rest, require_smooth=False) & (_block_std(rest) >= 2.0)
    if int(good.sum()) < 80:
        good = water & W.looks_like_water(rest, require_smooth=False)
    if int(good.sum()) < 80:
        return default
    med = np.median(rest[good].astype(np.float32), axis=0)
    return (int(med[0]), int(med[1]), int(med[2]))


def synthesize_water(
    shape: tuple[int, ...],
    tone: tuple[int, int, int],
    origin: tuple[int, int],
) -> np.ndarray:
    """Flat restyle-water tone plus a small pixel-art grain, in RGB uint8."""
    import water_constrain as W

    h, w = shape[:2]
    grain = W.hash_grain(origin, h, w)
    base = np.array(tone, dtype=np.float32)
    out = base[None, None, :] + np.stack([grain * 6.0, grain * 8.0, grain * 7.0], axis=2)
    return np.clip(out, 0, 255).astype(np.uint8)


def fill_bad_water(
    rest: np.ndarray,
    water: np.ndarray,
    proc: np.ndarray | None = None,
    water_tone: tuple[int, int, int] | None = None,
    origin: tuple[int, int] = (0, 0),
    pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
    feather: int = 16,
) -> tuple[np.ndarray, int]:
    """Replace bad water pixels with tone-matched procedural water (or a grain fill).

    Returns `(image, replaced_pixel_count)`. Land and good restyle water
    are left alone; the seam is feathered.
    """
    import water_constrain as W

    if water is None or not np.any(water):
        return rest, 0
    bad = bad_water_mask(rest, water, proc=proc, pad_rgb=pad_rgb)
    if not bad.any():
        return rest, 0
    tone = water_tone or _local_water_tone(rest, water & ~bad)
    if proc is not None:
        fill = proc.astype(np.float32)
        proc_w = W.looks_like_water(proc, require_smooth=False)
        if proc_w.any():
            mu = fill[proc_w].mean(axis=0)
            fill = fill + (np.array(tone, dtype=np.float32) - mu)
        proc_fog = dark_fog_mask(proc, pad_rgb)
        synth = synthesize_water(rest.shape, tone, origin).astype(np.float32)
        fill = np.where(proc_fog[:, :, None], synth, fill)
        fill = np.clip(fill, 0, 255)
    else:
        fill = synthesize_water(rest.shape, tone, origin).astype(np.float32)
    alpha = W.feather_alpha(bad, radius=feather)
    # Never paint over land: feather lives inside the water, against the coast.
    alpha = np.where(water, alpha, 0.0).astype(np.float32)
    out = rest.astype(np.float32) * (1.0 - alpha[:, :, None]) + fill * alpha[:, :, None]
    return np.clip(out, 0, 255).astype(np.uint8), int(bad.sum())


def _atomic_save_webp(im: Image.Image, path: Path, *, lossless: bool) -> None:
    tmp = path.with_suffix(".webp.tmp")
    _save_webp(im, tmp, lossless=lossless)
    tmp.replace(path)


def polish_z0_tiles(
    dest: Path,
    grid: dict,
    *,
    districts: list[dict] | None = None,
    proc_dest: Path | None = None,
    pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
    feather: int = EDGE_FEATHER,
    water_tone: tuple[int, int, int] | None = None,
    lossless_z0: bool = True,
    progress_path: Path | None = None,
) -> set[tuple[int, int]]:
    """Feather dark edge fog to viewer fog and fill bad bay water, in place.

    Resume-safe: a JSONL progress file records finished `tx,ty` after each
    atomic write. Water fill runs first so dark patches in the bay become
    water, not beige; remaining dark fog is then the true map edge.
    Missing restyle cells that are real water (mask + procedural) are
    created so the low-zoom pyramid has a continuous bay.
    """
    ox, oy = grid["ox"], grid["oy"]
    cols, rows = grid["cols"], grid["rows"]
    present = present_from_disk(dest, cols, rows)
    districts = districts or []
    if districts and any("chunks" in d for d in districts):
        nmask = attach_water_masks(districts)
        print(f"  attached {nmask} district water masks", flush=True)

    done: set[tuple[int, int]] = set()
    progress_path = progress_path or (dest / ".polish-progress.jsonl")
    if progress_path.exists():
        for line in progress_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                tx_s, ty_s = line.split(",", 1)
                done.add((int(tx_s), int(ty_s)))
            except ValueError:
                continue
        print(f"  resuming polish, {len(done)} tiles already done", flush=True)

    # Visit existing restyle tiles and procedural cells that may be missing
    # restyle coverage in the bay.
    to_visit = set(present)
    if proc_dest is not None and (proc_dest / "0").is_dir():
        to_visit |= present_from_disk(proc_dest, cols, rows)

    t0 = time.time()
    changed = 0
    removed = 0
    created = 0
    water_px = 0
    dest0 = dest / "0"
    dest0.mkdir(parents=True, exist_ok=True)
    prog = open(progress_path, "a")

    def load_proc(tx: int, ty: int) -> np.ndarray | None:
        if proc_dest is None:
            return None
        p = proc_dest / "0" / f"{tx}_{ty}.webp"
        if not p.exists():
            return None
        with Image.open(p) as im:
            return np.asarray(im.convert("RGB"))

    try:
        for n, (tx, ty) in enumerate(sorted(to_visit, key=lambda t: (t[1], t[0])), 1):
            if (tx, ty) in done:
                continue
            left = ox + tx * TILE
            top = oy + ty * TILE
            path = dest0 / f"{tx}_{ty}.webp"
            rest: np.ndarray | None = None
            existed = path.exists()
            if existed:
                with Image.open(path) as im:
                    rest = np.asarray(im.convert("RGB"))
            proc = load_proc(tx, ty)
            mask = water_mask_roi(districts, left, top) if districts else np.zeros((TILE, TILE), dtype=np.uint8)
            # If we have no restyle tile, start from viewer fog so non-water
            # stays page-coloured after a water fill.
            if rest is None:
                rest = np.zeros((TILE, TILE, 3), dtype=np.uint8)
                rest[:] = VIEW_FOG

            water = real_water_mask(mask, proc, rest)
            out, n_water = fill_bad_water(
                rest, water, proc=proc, water_tone=water_tone, origin=(left, top), pad_rgb=pad_rgb,
            )
            water_px += n_water
            polished, status = smooth_edge_fog(out, pad_rgb, feather=feather)
            if status == "empty" or (polished is not None and _almost_empty(polished, pad_rgb)):
                if existed:
                    path.unlink(missing_ok=True)
                    present.discard((tx, ty))
                    removed += 1
                # a newly considered procedural cell that is not city stays absent
            elif polished is None:
                pass
            else:
                wrote = n_water > 0 or status == "changed" or not existed
                if wrote:
                    _atomic_save_webp(Image.fromarray(polished, "RGB"), path, lossless=lossless_z0)
                    if not existed:
                        created += 1
                    else:
                        changed += 1
                present.add((tx, ty))
            prog.write(f"{tx},{ty}\n")
            if n % 50 == 0:
                prog.flush()
            if n % 200 == 0 or n == len(to_visit):
                print(
                    f"  polish {n}/{len(to_visit)} changed={changed} created={created} "
                    f"removed={removed} water_px={water_px} {time.time() - t0:.1f}s",
                    flush=True,
                )
    finally:
        prog.flush()
        prog.close()

    leftover = cleanup_absent_tiles(dest, present, cols, rows)
    if leftover:
        print(f"  removed {leftover} leftover z0 tiles after polish", flush=True)
    print(
        f"  polish done: {len(present)} z0 tiles, changed={changed}, created={created}, "
        f"removed={removed}, water_px={water_px}, {time.time() - t0:.1f}s",
        flush=True,
    )
    return present


# ---------------------------------------------------------------------------
# Second polish: restore washed-out land, then a global distance-field fade
# so the coverage edge has no per-tile seams.
# ---------------------------------------------------------------------------

COVERAGE_SCALE = 8
EDGE_FADE = 128
EDGE_INSET = 16


def near_view_fog(arr: np.ndarray, tol: int = VIEW_FOG_TOL) -> np.ndarray:
    rgb = arr[:, :, :3] if arr.ndim == 3 and arr.shape[2] == 4 else arr
    fog_c = np.array(VIEW_FOG, dtype=np.int16)
    return np.all(np.abs(rgb.astype(np.int16) - fog_c) <= tol, axis=2)


def is_halo_residue(arr: np.ndarray) -> np.ndarray:
    """Washed gray-green / near-beige fringe left by the per-tile edge feather.

    The first polish blended dark fog toward viewer beige independently on
    each tile, which left a low-chroma pale wash and a tile-aligned seam.
    Those pixels are not solid city, water or forest.
    """
    rgb = arr[:, :, :3] if arr.ndim == 3 and arr.shape[2] == 4 else arr
    vf = np.array(VIEW_FOG, dtype=np.float32)
    dist = np.linalg.norm(rgb.astype(np.float32) - vf, axis=2)
    chroma = rgb.max(axis=2).astype(np.float32) - rgb.min(axis=2).astype(np.float32)
    lum = rgb.astype(np.float32).mean(axis=2)
    close = dist < 36
    pale_wash = (chroma < 18) & (lum > 170) & (dist < 90)
    return close | pale_wash


def core_content_mask(arr: np.ndarray, pad_rgb: tuple[int, int, int]) -> np.ndarray:
    """Solid city, water or land — not fog and not the washed edge halo."""
    fog = dark_fog_mask(arr, pad_rgb)
    view = near_view_fog(arr, tol=6)
    halo = is_halo_residue(arr)
    return ~fog & ~view & ~halo


def chamfer_distance(seed: np.ndarray, max_r: int) -> np.ndarray:
    """0 on `seed`, Euclidean-ish distance outside, capped at `max_r + 1`.

    Two-neighbour relaxation, vectorised. Accurate up to `max_r` which is
    all the fade band needs; farther pixels sit at the cap.
    """
    cap = np.float32(max_r + 1)
    d = np.where(seed, np.float32(0.0), cap).astype(np.float32)
    tmp = np.empty_like(d)
    s2 = np.float32(1.41421356)
    ones = np.float32(1.0)
    for _ in range(int(max_r) + 1):
        tmp[0, :] = cap
        np.add(d[:-1, :], ones, out=tmp[1:, :])
        np.minimum(d, tmp, out=d)
        tmp[-1, :] = cap
        np.add(d[1:, :], ones, out=tmp[:-1, :])
        np.minimum(d, tmp, out=d)
        tmp[:, 0] = cap
        np.add(d[:, :-1], ones, out=tmp[:, 1:])
        np.minimum(d, tmp, out=d)
        tmp[:, -1] = cap
        np.add(d[:, 1:], ones, out=tmp[:, :-1])
        np.minimum(d, tmp, out=d)
        # diagonals
        tmp[0, :] = cap
        tmp[:, 0] = cap
        np.add(d[:-1, :-1], s2, out=tmp[1:, 1:])
        np.minimum(d, tmp, out=d)
        tmp[0, :] = cap
        tmp[:, -1] = cap
        np.add(d[:-1, 1:], s2, out=tmp[1:, :-1])
        np.minimum(d, tmp, out=d)
        tmp[-1, :] = cap
        tmp[:, 0] = cap
        np.add(d[1:, :-1], s2, out=tmp[:-1, 1:])
        np.minimum(d, tmp, out=d)
        tmp[-1, :] = cap
        tmp[:, -1] = cap
        np.add(d[1:, 1:], s2, out=tmp[:-1, :-1])
        np.minimum(d, tmp, out=d)
    return d


def signed_distance_field(content: np.ndarray, max_r: int) -> np.ndarray:
    """Negative inside `content`, positive outside, 0 on the boundary."""
    d_out = chamfer_distance(content, max_r)
    d_in = chamfer_distance(~content, max_r)
    return d_out - d_in


def smoothstep(x: np.ndarray, a: float, b: float) -> np.ndarray:
    t = np.clip((x - a) / max(b - a, 1e-6), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def fade_alpha(signed_dist: np.ndarray, fade: int = EDGE_FADE, inset: int = EDGE_INSET) -> np.ndarray:
    """0 deep inside content, 1 at `fade` pixels outside the core."""
    return smoothstep(signed_dist, -float(inset), float(fade))


def apply_coverage_fade(
    arr: np.ndarray,
    signed_dist: np.ndarray,
    fade: int = EDGE_FADE,
    inset: int = EDGE_INSET,
    pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
) -> tuple[np.ndarray | None, str]:
    """Blend `arr` toward viewer fog with a per-pixel signed distance (z0 px)."""
    alpha = fade_alpha(signed_dist, fade, inset)
    vf = np.array(VIEW_FOG, dtype=np.float32)
    out = arr.astype(np.float32) * (1.0 - alpha[:, :, None]) + vf * alpha[:, :, None]
    out = np.clip(out, 0, 255).astype(np.uint8)
    if float(alpha.mean()) > 0.995 or _almost_empty(out, pad_rgb):
        return None, "empty"
    if float(alpha.max()) < 0.02:
        return arr.copy(), "unchanged"
    return out, "changed"


def upsample_distance(
    field: np.ndarray,
    tx: int,
    ty: int,
    scale: int = COVERAGE_SCALE,
    tile: int = TILE,
    pad: int = 2,
) -> np.ndarray:
    """Bicubic upsample of the coarse field for one z0 tile, padded for continuity."""
    h8 = tile // scale
    w8 = tile // scale
    ch, cw = field.shape
    y0, x0 = ty * h8 - pad, tx * w8 - pad
    y1, x1 = (ty + 1) * h8 + pad, (tx + 1) * w8 + pad
    ya0, xa0 = max(0, y0), max(0, x0)
    ya1, xa1 = min(ch, y1), min(cw, x1)
    core = field[ya0:ya1, xa0:xa1]
    if core.size == 0:
        return np.full((tile, tile), float(EDGE_FADE + 1), dtype=np.float32)
    top, left = ya0 - y0, xa0 - x0
    bot, right = y1 - ya1, x1 - xa1
    crop = np.pad(core, ((top, bot), (left, right)), mode="edge").astype(np.float32)
    im = Image.fromarray(crop, mode="F")
    up = im.resize(((x1 - x0) * scale, (y1 - y0) * scale), Image.Resampling.BICUBIC)
    arr = np.array(up, dtype=np.float32)
    off = pad * scale
    return arr[off : off + tile, off : off + tile]


def build_signed_distance(
    dest: Path,
    grid: dict,
    *,
    scale: int = COVERAGE_SCALE,
    fade: int = EDGE_FADE,
    inset: int = EDGE_INSET,
    pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
    erode: int = 1,
    threshold: float = 0.4,
) -> np.ndarray:
    """Coarse signed-distance field, in z0-pixel units, from every z0 tile.

    Each coarse cell is `scale` z0 pixels. Content is the fraction of
    `core_content_mask` above `threshold`, then eroded by `erode` coarse
    pixels so a washed halo at the rim does not count as the city edge.
    """
    cols, rows = grid["cols"], grid["rows"]
    h8, w8 = TILE // scale, TILE // scale
    ch, cw = rows * h8, cols * w8
    acc = np.zeros((ch, cw), dtype=np.float32)
    t0 = time.time()
    n = 0
    z0 = dest / "0"
    for path in z0.glob("*.webp"):
        stem = path.stem
        if "_" not in stem:
            continue
        sx, sy = stem.split("_", 1)
        try:
            tx, ty = int(sx), int(sy)
        except ValueError:
            continue
        if tx < 0 or ty < 0 or tx >= cols or ty >= rows:
            continue
        with Image.open(path) as im:
            arr = np.asarray(im.convert("RGB"))
        if arr.shape[0] != TILE or arr.shape[1] != TILE:
            arr = np.array(Image.fromarray(arr).resize((TILE, TILE), Image.Resampling.NEAREST))
        content = core_content_mask(arr, pad_rgb)
        block = content[: h8 * scale, : w8 * scale].reshape(h8, scale, w8, scale).mean(axis=(1, 3))
        acc[ty * h8 : (ty + 1) * h8, tx * w8 : (tx + 1) * w8] = block
        n += 1
        if n % 1000 == 0:
            print(f"  coverage {n} tiles, {time.time() - t0:.1f}s", flush=True)
    mask = acc >= threshold
    if erode > 0:
        d_in = chamfer_distance(~mask, erode + 1)
        mask = mask & (d_in >= erode)
    max_r = int(np.ceil((fade + inset) / scale) + 3)
    print(f"  chamfer on {cw}×{ch} coarse grid, max_r={max_r}…", flush=True)
    signed = signed_distance_field(mask, max_r)
    signed *= np.float32(scale)
    print(
        f"  coverage field from {n} tiles, content frac={float(mask.mean()):.3f}, "
        f"{time.time() - t0:.1f}s",
        flush=True,
    )
    return signed


def procedural_painted(proc: np.ndarray, pad_rgb: tuple[int, int, int]) -> np.ndarray:
    """Pixels the renderer actually painted (land or water), not dark fog."""
    return ~dark_fog_mask(proc, pad_rgb) & ~near_view_fog(proc, tol=4)


def missing_or_washed(rest: np.ndarray, pad_rgb: tuple[int, int, int]) -> np.ndarray:
    """Restyle pixels that should be replaced from the procedural layer."""
    return dark_fog_mask(rest, pad_rgb) | near_view_fog(rest, tol=8) | is_halo_residue(rest)


def tone_match_region(
    proc: np.ndarray,
    rest: np.ndarray,
    need: np.ndarray,
    sample: np.ndarray,
) -> np.ndarray:
    """Mean/std-match procedural pixels on `need` toward restyle `sample`."""
    fill = proc.astype(np.float32)
    if int(need.sum()) < 10:
        return fill
    if int(sample.sum()) >= 80:
        src = proc[need].astype(np.float32)
        dst = rest[sample].astype(np.float32)
        mu_s, mu_d = src.mean(0), dst.mean(0)
        shift = np.clip(mu_d - mu_s, -40.0, 40.0)
        sd_s = np.maximum(src.std(0), 6.0)
        sd_d = np.maximum(dst.std(0), 6.0)
        ratio = np.clip(sd_d / sd_s, 0.5, 1.8)
        adj = (fill - mu_s) * ratio + mu_s + shift
        fill = np.where(need[:, :, None], adj, fill)
    return np.clip(fill, 0, 255)


def restore_from_procedural(
    rest: np.ndarray,
    proc: np.ndarray,
    pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
    feather: int = 16,
) -> tuple[np.ndarray, int]:
    """Fill washed or missing restyle pixels with tone-matched procedural content.

    Land and water are matched separately. Good restyle is left alone; the
    seam is feathered. Procedural dark fog is never copied.
    """
    import water_constrain as W

    painted = procedural_painted(proc, pad_rgb)
    if not painted.any():
        return rest, 0
    need = painted & missing_or_washed(rest, pad_rgb)
    if not need.any():
        return rest, 0
    proc_water = W.looks_like_water(proc, require_smooth=False)
    good = ~missing_or_washed(rest, pad_rgb)
    fill = proc.astype(np.float32)
    need_land = need & ~proc_water
    need_water = need & proc_water
    if need_land.any():
        sample = good & ~W.looks_like_water(rest, require_smooth=False)
        fill = tone_match_region(proc, rest, need_land, sample)
    if need_water.any():
        sample = good & W.looks_like_water(rest, require_smooth=False)
        fill_w = tone_match_region(proc, rest, need_water, sample)
        fill = np.where(need_water[:, :, None], fill_w, fill)
    alpha = W.feather_alpha(need, radius=feather)
    alpha = np.where(painted, alpha, 0.0).astype(np.float32)
    # Keep a firm 1 on the hole itself; the feather only lives at the rim.
    alpha = np.maximum(alpha, need.astype(np.float32))
    # Do not overwrite solid restyle city (good & not in the feather rim).
    # feather_alpha already decays outside `need`; good pixels only get the rim.
    out = rest.astype(np.float32) * (1.0 - alpha[:, :, None]) + fill * alpha[:, :, None]
    return np.clip(out, 0, 255).astype(np.uint8), int(need.sum())


def _load_progress(path: Path) -> set[tuple[int, int]]:
    done: set[tuple[int, int]] = set()
    if not path.exists():
        return done
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or "," not in line:
            continue
        try:
            tx_s, ty_s = line.split(",", 1)
            done.add((int(tx_s), int(ty_s)))
        except ValueError:
            continue
    return done


def restore_land_tiles(
    dest: Path,
    grid: dict,
    proc_dest: Path,
    *,
    pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
    lossless_z0: bool = True,
    progress_path: Path | None = None,
) -> set[tuple[int, int]]:
    """Create or repair z0 tiles whose land (or water) was washed out or dropped."""
    cols, rows = grid["cols"], grid["rows"]
    present = present_from_disk(dest, cols, rows)
    proc_present = present_from_disk(proc_dest, cols, rows) if proc_dest is not None else set()
    to_visit = present | proc_present
    progress_path = progress_path or (dest / ".polish2-land.jsonl")
    done = _load_progress(progress_path)
    if done:
        print(f"  resuming land restore, {len(done)} tiles already done", flush=True)
    dest0 = dest / "0"
    dest0.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    changed = created = skipped = 0
    px = 0
    prog = open(progress_path, "a")
    try:
        for n, (tx, ty) in enumerate(sorted(to_visit, key=lambda t: (t[1], t[0])), 1):
            if (tx, ty) in done:
                continue
            path = dest0 / f"{tx}_{ty}.webp"
            existed = path.exists()
            rest = None
            if existed:
                with Image.open(path) as im:
                    rest = np.asarray(im.convert("RGB"))
            else:
                rest = np.zeros((TILE, TILE, 3), dtype=np.uint8)
                rest[:] = VIEW_FOG
            proc = None
            if proc_dest is not None:
                pp = proc_dest / "0" / f"{tx}_{ty}.webp"
                if pp.exists():
                    with Image.open(pp) as im:
                        proc = np.asarray(im.convert("RGB"))
            if proc is None:
                prog.write(f"{tx},{ty}\n")
                skipped += 1
                if existed:
                    present.add((tx, ty))
                continue
            out, n_px = restore_from_procedural(rest, proc, pad_rgb=pad_rgb)
            px += n_px
            if n_px == 0:
                if existed:
                    present.add((tx, ty))
            else:
                if _almost_empty(out, pad_rgb):
                    if existed:
                        path.unlink(missing_ok=True)
                        present.discard((tx, ty))
                else:
                    _atomic_save_webp(Image.fromarray(out, "RGB"), path, lossless=lossless_z0)
                    present.add((tx, ty))
                    if existed:
                        changed += 1
                    else:
                        created += 1
            prog.write(f"{tx},{ty}\n")
            if n % 50 == 0:
                prog.flush()
            if n % 200 == 0 or n == len(to_visit):
                print(
                    f"  restore {n}/{len(to_visit)} changed={changed} created={created} "
                    f"px={px} {time.time() - t0:.1f}s",
                    flush=True,
                )
    finally:
        prog.flush()
        prog.close()
    print(
        f"  land restore done: present={len(present)} changed={changed} created={created} "
        f"px={px}, {time.time() - t0:.1f}s",
        flush=True,
    )
    return present


def apply_fade_tiles(
    dest: Path,
    grid: dict,
    field: np.ndarray,
    *,
    scale: int = COVERAGE_SCALE,
    fade: int = EDGE_FADE,
    inset: int = EDGE_INSET,
    pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
    lossless_z0: bool = True,
    progress_path: Path | None = None,
) -> set[tuple[int, int]]:
    """Apply the global distance fade to every z0 tile. Resume-safe."""
    cols, rows = grid["cols"], grid["rows"]
    present = present_from_disk(dest, cols, rows)
    progress_path = progress_path or (dest / ".polish2-fade.jsonl")
    done = _load_progress(progress_path)
    if done:
        print(f"  resuming edge fade, {len(done)} tiles already done", flush=True)
    dest0 = dest / "0"
    t0 = time.time()
    changed = removed = 0
    prog = open(progress_path, "a")
    try:
        tiles = sorted(present, key=lambda t: (t[1], t[0]))
        for n, (tx, ty) in enumerate(tiles, 1):
            if (tx, ty) in done:
                continue
            path = dest0 / f"{tx}_{ty}.webp"
            if not path.exists():
                present.discard((tx, ty))
                prog.write(f"{tx},{ty}\n")
                continue
            with Image.open(path) as im:
                arr = np.asarray(im.convert("RGB"))
            dist = upsample_distance(field, tx, ty, scale=scale)
            out, status = apply_coverage_fade(arr, dist, fade=fade, inset=inset, pad_rgb=pad_rgb)
            if status == "empty" or out is None:
                path.unlink(missing_ok=True)
                present.discard((tx, ty))
                removed += 1
            elif status == "changed":
                _atomic_save_webp(Image.fromarray(out, "RGB"), path, lossless=lossless_z0)
                changed += 1
            prog.write(f"{tx},{ty}\n")
            if n % 50 == 0:
                prog.flush()
            if n % 200 == 0 or n == len(tiles):
                print(
                    f"  fade {n}/{len(tiles)} changed={changed} removed={removed} "
                    f"{time.time() - t0:.1f}s",
                    flush=True,
                )
    finally:
        prog.flush()
        prog.close()
    leftover = cleanup_absent_tiles(dest, present, cols, rows)
    if leftover:
        print(f"  removed {leftover} leftover z0 tiles after fade", flush=True)
    print(
        f"  fade done: present={len(present)} changed={changed} removed={removed}, "
        f"{time.time() - t0:.1f}s",
        flush=True,
    )
    return present


def polish_smooth_edge(
    dest: Path,
    grid: dict,
    *,
    proc_dest: Path | None = None,
    pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
    fade: int = EDGE_FADE,
    inset: int = EDGE_INSET,
    scale: int = COVERAGE_SCALE,
    lossless_z0: bool = True,
) -> set[tuple[int, int]]:
    """Restore washed-out land, then fade the coverage edge with a global field.

    Three resume-safe stages: land restore (`.polish2-land.jsonl`), a
    coarse signed-distance field (`.polish2-dist.npz`), and the fade
    (`.polish2-fade.jsonl`). The field is in z0 pixels so neighbouring
    tiles share one continuous alpha.
    """
    dest.mkdir(parents=True, exist_ok=True)
    land_done = dest / ".polish2-land-done"
    if proc_dest is not None and (proc_dest / "0").is_dir():
        if land_done.exists():
            print("  land restore already complete, skipping", flush=True)
            present = present_from_disk(dest, grid["cols"], grid["rows"])
        else:
            print(f"  restoring washed-out land from {proc_dest}", flush=True)
            present = restore_land_tiles(dest, grid, proc_dest, pad_rgb=pad_rgb, lossless_z0=lossless_z0)
            land_done.write_text("ok\n")
    else:
        present = present_from_disk(dest, grid["cols"], grid["rows"])

    dist_path = dest / ".polish2-dist.npz"
    field = None
    if dist_path.exists():
        try:
            data = np.load(dist_path)
            field = data["field"]
            h8, w8 = TILE // scale, TILE // scale
            expect = (grid["rows"] * h8, grid["cols"] * w8)
            if field.shape != expect:
                print(f"  cached field {field.shape} != {expect}, rebuilding", flush=True)
                field = None
            else:
                print(f"  loaded cached coverage field {field.shape}", flush=True)
        except (OSError, ValueError, KeyError) as exc:
            print(f"  could not load cached field ({exc}), rebuilding", flush=True)
            field = None
    if field is None:
        field = build_signed_distance(
            dest, grid, scale=scale, fade=fade, inset=inset, pad_rgb=pad_rgb,
        )
        np.savez_compressed(dist_path, field=field)
        print(f"  wrote {dist_path}", flush=True)

    print(f"  applying global edge fade (fade={fade}px, inset={inset}px)", flush=True)
    present = apply_fade_tiles(
        dest, grid, field, scale=scale, fade=fade, inset=inset, pad_rgb=pad_rgb,
        lossless_z0=lossless_z0,
    )
    return present



# ---------------------------------------------------------------------------
# Third polish: rebuild the edge from source (no per-tile blur), then one
# smooth global vignette.
#
# The first --polish feathered dark fog on each 512-px tile, which left
# rounded content islands; --polish-edge's distance field wrapped those
# islands and produced a quilted fade. This pass never reads those
# already-faded dest tiles. It reslices each cell from the district
# restyle windows (the plain slicer composite), fills bay water and
# restores washed-out land, writes a clean staging copy, builds one
# coarse coverage mask with a wide blur and morphological close/open
# so the boundary does not follow source-tile edges, bicubically
# upsamples the signed-distance field, and fades to viewer beige with
# a low-frequency organic displacement of the falloff (~1–2 tiles).
# Re-running always starts from the source windows and the clean
# staging copies, never from dest. Changing fade or smoothing
# parameters discards only the field and fade progress; clean staging
# is reused.
# ---------------------------------------------------------------------------

VIGNETTE_FADE = 768
VIGNETTE_INSET = 32
VIGNETTE_SCALE = 16
# Coarse-pixel radii. One z0 tile is TILE/SCALE = 32 coarse cells, so
# a blur of 20 and a close of 16 round a full-tile stair instead of
# leaving a square step. DIST_BLUR is a second Gaussian on the signed
# distance itself. NOISE is a low-frequency displacement of the fade
# isoline, in z0 pixels, with period NOISE_PERIOD (also z0 px).
VIGNETTE_BLUR = 20
VIGNETTE_OPEN = 6
VIGNETTE_CLOSE = 16
VIGNETTE_DIST_BLUR = 6
VIGNETTE_NOISE = 36
VIGNETTE_NOISE_PERIOD = 384
VIGNETTE_CLEAN_VERSION = "vignette-clean-1"
VIGNETTE_FADE_VERSION = "vignette-fade-2"


def is_flat_neutral_wash(arr: np.ndarray) -> np.ndarray:
    """Neutral, desaturated, low-detail fringe (renderer or restyle halo).

    Mid-gray flats that are not near viewer beige still count: they are
    the washed pillows left at a restyle window's edge, not pavement
    (pavement has pixel-art grain) and not sand (higher chroma).
    """
    rgb = arr[:, :, :3] if arr.ndim == 3 and arr.shape[2] == 4 else arr
    chroma = rgb.max(axis=2).astype(np.float32) - rgb.min(axis=2).astype(np.float32)
    lum = rgb.astype(np.float32).mean(axis=2)
    std = _block_std(rgb, 8)
    return (chroma < 16) & (std < 7.0) & (lum > 90) & (lum < 210)


def source_content_mask(arr: np.ndarray, pad_rgb: tuple[int, int, int]) -> np.ndarray:
    """Solid source content: not fog, not beige, not a flat gray wash."""
    return core_content_mask(arr, pad_rgb) & ~is_flat_neutral_wash(arr)


def strip_uncovered(arr: np.ndarray, pad_rgb: tuple[int, int, int]) -> np.ndarray:
    """Keep solid content; replace fog, pad and washed halo with viewer beige."""
    content = source_content_mask(arr, pad_rgb)
    out = np.empty_like(arr)
    out[:] = VIEW_FOG
    if content.any():
        out[content] = arr[content]
    return out


def reslice_z0_from_source(
    districts: list[dict],
    grid: dict,
    tx: int,
    ty: int,
    pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
) -> np.ndarray | None:
    """Plain-slicer composite of one z0 cell from district restyle windows.

    The canvas starts as viewer beige. Renderer fog, pad and unpainted
    beige in the windows are skipped (same rule as `composite_district`).
    Returns None when no district paints this cell.
    """
    ox, oy = grid["ox"], grid["oy"]
    left, top = ox + tx * TILE, oy + ty * TILE
    tile = Image.new("RGB", (TILE, TILE), VIEW_FOG)
    painted = False
    for d in districts:
        roi = extract_district_roi(
            d["tiles"], d["windows"], d["origin"], d["width"], d["height"],
            left, top, TILE, TILE,
        )
        if roi is None:
            continue
        tile = composite_district(tile, roi, pad_rgb)
        painted = True
    if not painted:
        return None
    arr = np.asarray(tile)
    if arr.shape[0] != TILE or arr.shape[1] != TILE:
        arr = np.array(Image.fromarray(arr).resize((TILE, TILE), Image.Resampling.NEAREST))
    return strip_uncovered(arr, pad_rgb)


def prepare_clean_tile(
    rest: np.ndarray | None,
    proc: np.ndarray | None,
    mask: np.ndarray | None,
    *,
    pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
    water_tone: tuple[int, int, int] | None = None,
    origin: tuple[int, int] = (0, 0),
) -> tuple[np.ndarray | None, int, int]:
    """Water-fill and land-restore a freshly resliced tile; no per-tile blur.

    Uncovered pixels (fog, halo) become viewer beige so a later fade
    never blends leftover gray wash. Returns `(image, water_px, land_px)`
    or `(None, 0, 0)` when the cell is empty after both fills.
    """
    if rest is None:
        rest = np.empty((TILE, TILE, 3), dtype=np.uint8)
        rest[:] = VIEW_FOG
    else:
        rest = strip_uncovered(rest, pad_rgb)
    if mask is None:
        mask = np.zeros((TILE, TILE), dtype=np.uint8)
    water = real_water_mask(mask, proc, rest)
    out, n_water = fill_bad_water(
        rest, water, proc=proc, water_tone=water_tone, origin=origin, pad_rgb=pad_rgb,
    )
    n_land = 0
    if proc is not None:
        out, n_land = restore_from_procedural(out, proc, pad_rgb=pad_rgb)
    out = strip_uncovered(out, pad_rgb)
    if _almost_empty(out, pad_rgb):
        return None, n_water, n_land
    return out, n_water, n_land


def morph_open(mask: np.ndarray, radius: int) -> np.ndarray:
    """Erode then dilate: drop isolated islands smaller than `radius`."""
    if radius <= 0:
        return mask
    d_in = chamfer_distance(~mask, radius + 1)
    eroded = mask & (d_in >= radius)
    d_out = chamfer_distance(eroded, radius + 1)
    return eroded | (d_out <= radius)


def morph_close(mask: np.ndarray, radius: int) -> np.ndarray:
    """Dilate then erode: fill notches smaller than `radius`."""
    if radius <= 0:
        return mask
    d_out = chamfer_distance(mask, radius + 1)
    dilated = mask | (d_out <= radius)
    d_in = chamfer_distance(~dilated, radius + 1)
    return dilated & (d_in >= radius)


def gaussian_blur_float(field: np.ndarray, radius: float) -> np.ndarray:
    """Separable Gaussian blur for a signed float field (PIL cannot blur mode F)."""
    if radius <= 0:
        return field.astype(np.float32, copy=False)
    sigma = float(radius)
    r = max(1, int(math.ceil(3.0 * sigma)))
    x = np.arange(-r, r + 1, dtype=np.float64)
    kernel = np.exp(-(x * x) / (2.0 * sigma * sigma))
    kernel /= kernel.sum()
    kernel = kernel.astype(np.float32)
    src = np.pad(field.astype(np.float32, copy=False), ((r, r), (r, r)), mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(src, kernel.size, axis=1)
    tmp = np.tensordot(windows, kernel, axes=([2], [0]))
    windows = np.lib.stride_tricks.sliding_window_view(tmp, kernel.size, axis=0)
    return np.tensordot(windows, kernel, axes=([2], [0])).astype(np.float32)


def _hash_lattice(ix: np.ndarray, iy: np.ndarray, seed: int) -> np.ndarray:
    """Deterministic lattice hash on [-1, 1], stable across tiles."""
    n = (
        ix.astype(np.int64) * np.int64(374761393)
        + iy.astype(np.int64) * np.int64(668265263)
        + np.int64(seed) * np.int64(1274126177)
    )
    n = np.bitwise_xor(n, n >> 13) * np.int64(1274126177)
    n = np.bitwise_xor(n, n >> 16)
    return ((n & np.int64(0x7FFFFFFF)).astype(np.float32) / np.float32(0x7FFFFFFF)) * np.float32(2.0) - np.float32(1.0)


def fade_boundary_noise(
    tx: int,
    ty: int,
    *,
    tile: int = TILE,
    amplitude: float = VIGNETTE_NOISE,
    period: float = VIGNETTE_NOISE_PERIOD,
    seed: int = 17,
) -> np.ndarray:
    """Low-frequency value noise in z0 pixels, continuous across tile seams.

    Two octaves of smooth-interpolated lattice noise, hashed from world
    (not tile-local) coordinates so neighbouring tiles agree on the
    shared edge. Amplitude 0 returns zeros. Used to displace the fade
    isoline so a straight coverage edge does not read as a square step.
    """
    if amplitude <= 0 or period <= 0:
        return np.zeros((tile, tile), dtype=np.float32)
    yy, xx = np.mgrid[0:tile, 0:tile]
    wx = (tx * tile + xx).astype(np.float32)
    wy = (ty * tile + yy).astype(np.float32)

    def octave(length: float, amp: float, octave_seed: int) -> np.ndarray:
        gx = wx / np.float32(length)
        gy = wy / np.float32(length)
        x0 = np.floor(gx)
        y0 = np.floor(gy)
        fx = gx - x0
        fy = gy - y0
        ux = fx * fx * (3.0 - 2.0 * fx)
        uy = fy * fy * (3.0 - 2.0 * fy)
        ix0 = x0.astype(np.int64)
        iy0 = y0.astype(np.int64)
        n00 = _hash_lattice(ix0, iy0, octave_seed)
        n10 = _hash_lattice(ix0 + 1, iy0, octave_seed)
        n01 = _hash_lattice(ix0, iy0 + 1, octave_seed)
        n11 = _hash_lattice(ix0 + 1, iy0 + 1, octave_seed)
        nx0 = n00 * (1.0 - ux) + n10 * ux
        nx1 = n01 * (1.0 - ux) + n11 * ux
        return (nx0 * (1.0 - uy) + nx1 * uy) * np.float32(amp)

    out = octave(period, amplitude * 0.7, seed)
    out += octave(period * 0.5, amplitude * 0.3, seed + 101)
    return out.astype(np.float32)


def smooth_coverage_mask(
    acc: np.ndarray,
    *,
    threshold: float = 0.4,
    blur: int = VIGNETTE_BLUR,
    open_r: int = VIGNETTE_OPEN,
    close_r: int = VIGNETTE_CLOSE,
) -> np.ndarray:
    """Threshold a coarse content-fraction field after a blur and open/close.

    The Gaussian blur (in coarse pixels) rounds tile-aligned stairs;
    opening drops isolated islands; closing fills notches up to about
    `close_r` coarse cells (a full z0 tile is 32 cells at the default
    scale). The result is a single organic coverage region, not a
    grid of blobs.
    """
    field = acc.astype(np.float32)
    if blur > 0:
        # PIL GaussianBlur on a 0–255 L image, then back to 0–1.
        raw = np.clip(np.round(field * 255.0), 0, 255).astype(np.uint8)
        sm = Image.fromarray(raw, "L").filter(ImageFilter.GaussianBlur(radius=float(blur)))
        field = np.asarray(sm, dtype=np.float32) / 255.0
    mask = field >= threshold
    mask = morph_open(mask, open_r)
    mask = morph_close(mask, close_r)
    return mask


def fade_stamp(
    fade: int,
    inset: int,
    scale: int,
    blur: int,
    open_r: int,
    close_r: int,
    dist_blur: float = VIGNETTE_DIST_BLUR,
    noise_amp: float = VIGNETTE_NOISE,
    noise_period: float = VIGNETTE_NOISE_PERIOD,
) -> str:
    return (
        f"{VIGNETTE_FADE_VERSION} fade={fade} inset={inset} scale={scale} "
        f"blur={blur} open={open_r} close={close_r} dist_blur={dist_blur} "
        f"noise={noise_amp} period={noise_period}\n"
    )


def _candidate_cells(
    districts: list[dict],
    grid: dict,
    dest: Path,
    proc_dest: Path | None,
    clean_dest: Path,
) -> set[tuple[int, int]]:
    ox, oy = grid["ox"], grid["oy"]
    cols, rows = grid["cols"], grid["rows"]
    cells = present_from_disk(dest, cols, rows)
    if proc_dest is not None:
        cells |= present_from_disk(proc_dest, cols, rows)
    cells |= present_from_disk(clean_dest, cols, rows)
    for d in districts:
        x0, y0 = d["origin"]
        x1, y1 = x0 + d["width"], y0 + d["height"]
        tx0 = max(0, (x0 - ox) // TILE)
        ty0 = max(0, (y0 - oy) // TILE)
        tx1 = min(cols - 1, (x1 - 1 - ox) // TILE)
        ty1 = min(rows - 1, (y1 - 1 - oy) // TILE)
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                cells.add((tx, ty))
    return cells


def reslice_clean_tiles(
    dest: Path,
    grid: dict,
    districts: list[dict],
    *,
    proc_dest: Path | None = None,
    clean_dest: Path | None = None,
    pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
    water_tone: tuple[int, int, int] | None = None,
    lossless_z0: bool = True,
    progress_path: Path | None = None,
) -> Path:
    """Reslice every candidate cell from source into `clean_dest`. Resume-safe.

    Dest is not written. Each finished `tx,ty` is appended to the
    progress file, including cells that produced no clean tile.
    """
    ox, oy = grid["ox"], grid["oy"]
    cols, rows = grid["cols"], grid["rows"]
    clean_dest = clean_dest or (dest / ".vignette-clean")
    (clean_dest / "0").mkdir(parents=True, exist_ok=True)
    if districts and any("chunks" in d for d in districts):
        nmask = attach_water_masks(districts)
        print(f"  attached {nmask} district water masks", flush=True)
    progress_path = progress_path or (clean_dest / ".vignette-reslice.jsonl")
    done = _load_progress(progress_path)
    if done:
        print(f"  resuming clean reslice, {len(done)} cells already done", flush=True)
    to_visit = _candidate_cells(districts, grid, dest, proc_dest, clean_dest)
    t0 = time.time()
    written = skipped = water_px = land_px = 0
    prog = open(progress_path, "a")

    def load_proc(tx: int, ty: int) -> np.ndarray | None:
        if proc_dest is None:
            return None
        p = proc_dest / "0" / f"{tx}_{ty}.webp"
        if not p.exists():
            return None
        with Image.open(p) as im:
            return np.asarray(im.convert("RGB"))

    try:
        cells = sorted(to_visit, key=lambda t: (t[1], t[0]))
        for n, (tx, ty) in enumerate(cells, 1):
            if (tx, ty) in done:
                continue
            left, top = ox + tx * TILE, oy + ty * TILE
            rest = reslice_z0_from_source(districts, grid, tx, ty, pad_rgb=pad_rgb)
            proc = load_proc(tx, ty)
            mask = water_mask_roi(districts, left, top) if districts else None
            if rest is None and proc is None:
                path = clean_dest / "0" / f"{tx}_{ty}.webp"
                if path.exists():
                    path.unlink()
                prog.write(f"{tx},{ty}\n")
                skipped += 1
                if n % 200 == 0 or n == len(cells):
                    print(
                        f"  reslice {n}/{len(cells)} written={written} skipped={skipped} "
                        f"water_px={water_px} land_px={land_px} {time.time() - t0:.1f}s",
                        flush=True,
                    )
                continue
            out, n_water, n_land = prepare_clean_tile(
                rest, proc, mask, pad_rgb=pad_rgb, water_tone=water_tone, origin=(left, top),
            )
            water_px += n_water
            land_px += n_land
            path = clean_dest / "0" / f"{tx}_{ty}.webp"
            if out is None:
                if path.exists():
                    path.unlink()
                skipped += 1
            else:
                _atomic_save_webp(Image.fromarray(out, "RGB"), path, lossless=lossless_z0)
                written += 1
            prog.write(f"{tx},{ty}\n")
            if n % 50 == 0:
                prog.flush()
            if n % 200 == 0 or n == len(cells):
                print(
                    f"  reslice {n}/{len(cells)} written={written} skipped={skipped} "
                    f"water_px={water_px} land_px={land_px} {time.time() - t0:.1f}s",
                    flush=True,
                )
    finally:
        prog.flush()
        prog.close()
    print(
        f"  clean reslice done: written={written} skipped={skipped} "
        f"water_px={water_px} land_px={land_px}, {time.time() - t0:.1f}s",
        flush=True,
    )
    (clean_dest / ".vignette-reslice-done").write_text(VIGNETTE_CLEAN_VERSION + "\n")
    return clean_dest


def build_vignette_field(
    clean_dest: Path,
    grid: dict,
    *,
    scale: int = VIGNETTE_SCALE,
    fade: int = VIGNETTE_FADE,
    inset: int = VIGNETTE_INSET,
    blur: int = VIGNETTE_BLUR,
    open_r: int = VIGNETTE_OPEN,
    close_r: int = VIGNETTE_CLOSE,
    dist_blur: float = VIGNETTE_DIST_BLUR,
    threshold: float = 0.4,
    pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
) -> np.ndarray:
    """Coarse signed-distance field, in z0 pixels, from the clean staging tiles.

    After the morphologically-smoothed mask is turned into a signed
    distance, a second Gaussian (`dist_blur`, in coarse pixels) rounds
    remaining isolines before the field is scaled to z0 units.
    """
    cols, rows = grid["cols"], grid["rows"]
    h8, w8 = TILE // scale, TILE // scale
    ch, cw = rows * h8, cols * w8
    acc = np.zeros((ch, cw), dtype=np.float32)
    t0 = time.time()
    n = 0
    z0 = clean_dest / "0"
    if z0.exists():
        for path in z0.glob("*.webp"):
            stem = path.stem
            if "_" not in stem:
                continue
            sx, sy = stem.split("_", 1)
            try:
                tx, ty = int(sx), int(sy)
            except ValueError:
                continue
            if tx < 0 or ty < 0 or tx >= cols or ty >= rows:
                continue
            with Image.open(path) as im:
                arr = np.asarray(im.convert("RGB"))
            if arr.shape[0] != TILE or arr.shape[1] != TILE:
                arr = np.array(Image.fromarray(arr).resize((TILE, TILE), Image.Resampling.NEAREST))
            content = core_content_mask(arr, pad_rgb)
            block = content[: h8 * scale, : w8 * scale].reshape(h8, scale, w8, scale).mean(axis=(1, 3))
            acc[ty * h8 : (ty + 1) * h8, tx * w8 : (tx + 1) * w8] = block
            n += 1
            if n % 1000 == 0:
                print(f"  coverage {n} clean tiles, {time.time() - t0:.1f}s", flush=True)
    print(f"  smoothing coarse coverage ({cw}×{ch}, blur={blur}, open={open_r}, close={close_r})", flush=True)
    mask = smooth_coverage_mask(acc, threshold=threshold, blur=blur, open_r=open_r, close_r=close_r)
    max_r = int(np.ceil((fade + inset) / scale) + 3)
    print(f"  chamfer on {cw}×{ch} coarse grid, max_r={max_r}, content frac={float(mask.mean()):.3f}…", flush=True)
    signed = signed_distance_field(mask, max_r)
    if dist_blur > 0:
        print(f"  blurring signed field, radius={dist_blur} coarse px", flush=True)
        signed = gaussian_blur_float(signed, dist_blur)
    signed *= np.float32(scale)
    print(f"  vignette field from {n} clean tiles, {time.time() - t0:.1f}s", flush=True)
    return signed


def edge_band_from_field(
    field: np.ndarray,
    grid: dict,
    *,
    fade: int = VIGNETTE_FADE,
    inset: int = VIGNETTE_INSET,
    scale: int = VIGNETTE_SCALE,
    margin: int = 32,
) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    """Tiles the fade touches, and tiles that sit fully past the fade (exterior).

    A tile is in the edge band when its coarse distance range overlaps
    `(-inset - margin, fade + margin)`. Fully-exterior tiles (min dist
    greater than the fade) should be removed from dest if present.
    Deep-interior tiles are left alone.
    """
    cols, rows = grid["cols"], grid["rows"]
    h8, w8 = TILE // scale, TILE // scale
    band: set[tuple[int, int]] = set()
    exterior: set[tuple[int, int]] = set()
    lo, hi = -float(inset + margin), float(fade + margin)
    for ty in range(rows):
        for tx in range(cols):
            block = field[ty * h8 : (ty + 1) * h8, tx * w8 : (tx + 1) * w8]
            if block.size == 0:
                continue
            dmin = float(block.min())
            dmax = float(block.max())
            if dmin > fade:
                exterior.add((tx, ty))
            elif dmax >= lo and dmin <= hi:
                band.add((tx, ty))
    return band, exterior


def apply_vignette_fade(
    dest: Path,
    grid: dict,
    field: np.ndarray,
    clean_dest: Path,
    *,
    scale: int = VIGNETTE_SCALE,
    fade: int = VIGNETTE_FADE,
    inset: int = VIGNETTE_INSET,
    pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
    lossless_z0: bool = True,
    progress_path: Path | None = None,
    edge_list_path: Path | None = None,
    noise_amp: float = VIGNETTE_NOISE,
    noise_period: float = VIGNETTE_NOISE_PERIOD,
) -> set[tuple[int, int]]:
    """Fade clean staging tiles onto dest for the edge band only.

    Reads only from `clean_dest` (and the distance field), never from
    dest's existing pixels, so a re-run cannot stack fades. Resume-safe.
    Tiles that leave the fade band for the deep interior (because a
    wider coverage smooth moved the boundary) are restored from clean
    staging so they do not keep a stale fade.
    """
    cols, rows = grid["cols"], grid["rows"]
    present = present_from_disk(dest, cols, rows)
    dest0 = dest / "0"
    band, exterior = edge_band_from_field(field, grid, fade=fade, inset=inset, scale=scale)
    edge_list_path = edge_list_path or (dest / ".vignette-edge.jsonl")
    old_band = _load_progress(edge_list_path) if edge_list_path.exists() else set()
    with open(edge_list_path, "w") as el:
        for tx, ty in sorted(band, key=lambda t: (t[1], t[0])):
            el.write(f"{tx},{ty}\n")
    print(
        f"  edge band {len(band)} tiles, exterior {len(exterior)}, list {edge_list_path}",
        flush=True,
    )
    # Drop dest tiles that sit fully past the vignette (all beige).
    removed_ext = 0
    for tx, ty in exterior:
        path = dest0 / f"{tx}_{ty}.webp"
        if path.exists():
            path.unlink()
            removed_ext += 1
        present.discard((tx, ty))
    if removed_ext:
        print(f"  removed {removed_ext} fully-exterior dest tiles", flush=True)

    # Previously-faded tiles that the new field places in the deep
    # interior must become unfaded clean content, not keep the old fade.
    released = old_band - band - exterior
    restored = 0
    for tx, ty in released:
        clean_path = clean_dest / "0" / f"{tx}_{ty}.webp"
        dest_path = dest0 / f"{tx}_{ty}.webp"
        if not clean_path.exists():
            continue
        with Image.open(clean_path) as im:
            arr = np.asarray(im.convert("RGB"))
        if _almost_empty(arr, pad_rgb):
            if dest_path.exists():
                dest_path.unlink(missing_ok=True)
            present.discard((tx, ty))
            restored += 1
            continue
        _atomic_save_webp(Image.fromarray(arr, "RGB"), dest_path, lossless=lossless_z0)
        present.add((tx, ty))
        restored += 1
    if restored:
        print(f"  restored {restored} tiles from clean (left the fade band)", flush=True)

    progress_path = progress_path or (dest / ".vignette-fade.jsonl")
    done = _load_progress(progress_path)
    if done:
        print(f"  resuming vignette fade, {len(done)} tiles already done", flush=True)
    t0 = time.time()
    changed = removed = created = 0
    prog = open(progress_path, "a")
    try:
        tiles = sorted(band, key=lambda t: (t[1], t[0]))
        for n, (tx, ty) in enumerate(tiles, 1):
            if (tx, ty) in done:
                # dest may already have the faded tile from a previous run
                if (dest0 / f"{tx}_{ty}.webp").exists():
                    present.add((tx, ty))
                else:
                    present.discard((tx, ty))
                continue
            clean_path = clean_dest / "0" / f"{tx}_{ty}.webp"
            dest_path = dest0 / f"{tx}_{ty}.webp"
            existed = dest_path.exists()
            if clean_path.exists():
                with Image.open(clean_path) as im:
                    arr = np.asarray(im.convert("RGB"))
            else:
                arr = np.empty((TILE, TILE, 3), dtype=np.uint8)
                arr[:] = VIEW_FOG
            dist = upsample_distance(field, tx, ty, scale=scale)
            if noise_amp > 0:
                dist = dist + fade_boundary_noise(
                    tx, ty, amplitude=noise_amp, period=noise_period,
                )
            out, status = apply_coverage_fade(arr, dist, fade=fade, inset=inset, pad_rgb=pad_rgb)
            if status == "empty" or out is None or (out is not None and _almost_empty(out, pad_rgb)):
                if existed:
                    dest_path.unlink(missing_ok=True)
                    removed += 1
                present.discard((tx, ty))
            else:
                _atomic_save_webp(Image.fromarray(out, "RGB"), dest_path, lossless=lossless_z0)
                present.add((tx, ty))
                if existed:
                    changed += 1
                else:
                    created += 1
            prog.write(f"{tx},{ty}\n")
            if n % 50 == 0:
                prog.flush()
            if n % 200 == 0 or n == len(tiles):
                print(
                    f"  vignette {n}/{len(tiles)} changed={changed} created={created} "
                    f"removed={removed} {time.time() - t0:.1f}s",
                    flush=True,
                )
    finally:
        prog.flush()
        prog.close()
    leftover = cleanup_absent_tiles(dest, present, cols, rows)
    if leftover:
        print(f"  removed {leftover} leftover z0 tiles after vignette", flush=True)
    print(
        f"  vignette done: present={len(present)} changed={changed} created={created} "
        f"removed={removed}, {time.time() - t0:.1f}s",
        flush=True,
    )
    return present


def polish_vignette(
    dest: Path,
    grid: dict,
    districts: list[dict],
    *,
    proc_dest: Path | None = None,
    clean_dest: Path | None = None,
    pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
    fade: int = VIGNETTE_FADE,
    inset: int = VIGNETTE_INSET,
    scale: int = VIGNETTE_SCALE,
    blur: int = VIGNETTE_BLUR,
    open_r: int = VIGNETTE_OPEN,
    close_r: int = VIGNETTE_CLOSE,
    dist_blur: float = VIGNETTE_DIST_BLUR,
    noise_amp: float = VIGNETTE_NOISE,
    noise_period: float = VIGNETTE_NOISE_PERIOD,
    lossless_z0: bool = True,
) -> set[tuple[int, int]]:
    """Rebuild the edge from source and apply one global beige vignette.

    Three resume-safe stages: a clean reslice into `.vignette-clean`
    (from district windows + water fill + land restore, never from dest),
    a coarse signed-distance field with a wide blur, morphological
    close/open, and a second blur on the distance itself
    (`.vignette-dist.npz`), and the fade onto dest
    (`.vignette-fade.jsonl`) with bicubic upsampling and a
    low-frequency organic displacement of the isoline. Changing fade
    or smoothing parameters discards only the field and fade progress;
    the clean staging is reused.
    """
    dest.mkdir(parents=True, exist_ok=True)
    clean_dest = clean_dest or (dest / ".vignette-clean")
    done_mark = clean_dest / ".vignette-reslice-done"
    if done_mark.exists() and done_mark.read_text().strip() == VIGNETTE_CLEAN_VERSION:
        print(f"  clean staging already complete at {clean_dest}, skipping reslice", flush=True)
    else:
        print(f"  reslicing clean tiles from source → {clean_dest}", flush=True)
        reslice_clean_tiles(
            dest, grid, districts,
            proc_dest=proc_dest, clean_dest=clean_dest,
            pad_rgb=pad_rgb, lossless_z0=lossless_z0,
        )

    stamp = fade_stamp(
        fade, inset, scale, blur, open_r, close_r,
        dist_blur=dist_blur, noise_amp=noise_amp, noise_period=noise_period,
    )
    stamp_path = dest / ".vignette-fade-stamp"
    dist_path = dest / ".vignette-dist.npz"
    fade_progress = dest / ".vignette-fade.jsonl"
    if stamp_path.exists() and stamp_path.read_text() != stamp:
        print("  fade parameters changed, rebuilding coverage and fade", flush=True)
        dist_path.unlink(missing_ok=True)
        fade_progress.unlink(missing_ok=True)
    field = None
    if dist_path.exists():
        try:
            data = np.load(dist_path)
            field = data["field"]
            h8, w8 = TILE // scale, TILE // scale
            expect = (grid["rows"] * h8, grid["cols"] * w8)
            if field.shape != expect:
                print(f"  cached field {field.shape} != {expect}, rebuilding", flush=True)
                field = None
            else:
                print(f"  loaded cached vignette field {field.shape}", flush=True)
        except (OSError, ValueError, KeyError) as exc:
            print(f"  could not load cached field ({exc}), rebuilding", flush=True)
            field = None
    if field is None:
        field = build_vignette_field(
            clean_dest, grid, scale=scale, fade=fade, inset=inset,
            blur=blur, open_r=open_r, close_r=close_r, dist_blur=dist_blur,
            pad_rgb=pad_rgb,
        )
        np.savez_compressed(dist_path, field=field)
        print(f"  wrote {dist_path}", flush=True)
    stamp_path.write_text(stamp)

    print(f"  applying vignette fade (fade={fade}px, inset={inset}px) from clean staging", flush=True)
    present = apply_vignette_fade(
        dest, grid, field, clean_dest,
        scale=scale, fade=fade, inset=inset, pad_rgb=pad_rgb,
        lossless_z0=lossless_z0, progress_path=fade_progress,
        noise_amp=noise_amp, noise_period=noise_period,
    )
    return present


# ---------------------------------------------------------------------------
# --polish-water: one bay tone and its own fade (free, no API)
#
# The restyle paints bay water in a few slightly different teals (flat
# sgWater in one window, a lighter fill or a textured sea in the next),
# and leaves its own fog over open water. After the vignette both show
# as squares: tone steps between tiles, and beige blotches the fade
# blends into. This pass works on the water body only. It puts every
# teal water pixel on one reference tone and keeps only its fine texture
# (a per-tile high-pass, so a tone step at a tile seam is removed on both
# sides), fills uncovered pixels under the real water mask with that
# tone, and fades water to viewer beige along a smoothed water-body
# field of its own, capped by the land fade.
#
# --polish-forest: interior tiles just inside the vignette band still
# hold an older gray wash (squares along the eastern hills). There the
# land cross-fades to the vignette's clean staging, fully clean near the
# band and back to dest at FOREST_DEPTH, along the same global distance
# field, so no step lands on a tile seam. Restyle-window greens are
# also evened (texture kept) near the edge and on forest-dominant ground.
#
# --polish-flats: featureless ground near the edge (flat fill, gray
# puddles, dithered blotches) and the water beside it fade into the fog
# through a soft cross-tile mask. Nothing is drawn new.
#
# Sources are immutable (clean staging for the edge band, a one-time
# snapshot of dest elsewhere), so both passes are idempotent. Run them
# after --polish-vignette; pass both flags together.
# ---------------------------------------------------------------------------

WATER_REF = (36, 88, 74)
WATER_TONE_SCALE = 16
WATER_DETAIL_RADIUS = 6.0
WATER_MAX_DETAIL = 24.0
WATER_BODY_MIN_FRAC = 0.2
WATER_BODY_MIN_TILES = 20
WATER_BODY_DILATE = 3
WATER_INSET = 256
WATER_SHORE_BLUR = 6
# The water body's own coverage: teal fraction per coarse cell, blurred
# and closed hard so the restyle's fog holes inside the bay vanish.
WATER_FIELD_THRESHOLD = 0.3
WATER_FIELD_BLUR = 24
WATER_FIELD_OPEN = 6
WATER_FIELD_CLOSE = 24
WATER_VERSION = "water-10"
# Clean-staging weight: 1 at FOREST_FULL px inside the edge, 0 at FOREST_DEPTH
# (just above the field's cap, so deep interior tiles are never touched).
FOREST_DEPTH = 850
FOREST_FULL = 560
# Vegetation tone: restyle windows paint the hills in slightly different
# greens, so forest steps on window seams. Each tile's own low-frequency
# green (normalized blur, radius FOREST_LOCAL_RADIUS px, within the tile)
# is swapped for a cross-tile mean (cells of FOREST_TONE_SCALE px,
# Gaussian sigma FOREST_TONE_SIGMA cells), keeping the canopy texture.
FOREST_TONE_SCALE = 64
FOREST_TONE_SIGMA = 16.0
FOREST_LOCAL_RADIUS = 48.0
# How far inside the edge the green match reaches: a Gaussian (sigma in
# FOREST_TONE_SCALE cells) of the band/exterior tiles, full strength where
# it is at least FOREST_PROX_LEVEL. Deep interior keeps its own greens.
FOREST_PROX_SIGMA = 24.0
FOREST_PROX_LEVEL = 0.15
# Inland, the match also covers forest-dominant ground: the blurred
# vegetation fraction ramps the strength from FOREST_DOM_LO to FOREST_DOM_HI,
# so streets and parks in the city keep their own greens.
FOREST_DOM_SIGMA = 8.0
FOREST_DOM_LO = 0.4
FOREST_DOM_HI = 0.7
# --polish-flats: featureless ground near the edge (flat restyle fill, gray
# puddles, white lots, renderer wash) fades into the viewer fog through a
# soft cross-tile mask instead of ending in hard-edged blotches.
FLATS_SIGMA = 1.5
FLATS_LO = 0.25
FLATS_HI = 0.75
FLATS_MAX_STD = 4.0
# Water beside fogged flats fades over a wider reach so the shore is not
# a hard teal edge against the fog; thin lines inside flats are absorbed.
FLATS_WATER_SIGMA = 3.0
FLATS_WATER_LO = 0.05
FLATS_WATER_HI = 0.45
FLATS_GATE_BLUR = 10.0
FLATS_MAX_CHROMA = 28


WATER_MAX_LUM = 115


def is_teal_water(arr: np.ndarray, max_lum: float = WATER_MAX_LUM) -> np.ndarray:
    """Bay teal: green and blue well above red, blue a little under green.

    Stricter than `looks_like_water`, which also accepts dark vegetation.
    Lighter teal (above `max_lum`) is the restyle's own fog blended over
    water, not sea, so it is left out here and filled instead.
    """
    rgb = arr[:, :, :3].astype(np.int16)
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    lum = rgb.mean(axis=2)
    return (g - r >= 30) & (b - r >= 20) & (g - b >= -4) & (g - b <= 34) & (lum < max_lum)


def uncovered_mask(arr: np.ndarray, pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C)) -> np.ndarray:
    """Pixels with no painted content: viewer beige, dark fog, or a flat gray wash."""
    view = np.all(np.abs(arr.astype(np.int16) - np.array(VIEW_FOG, dtype=np.int16)) <= 4, axis=2)
    return view | dark_fog_mask(arr, pad_rgb) | is_flat_neutral_wash(arr)


def water_body_tiles(
    teal_frac: dict[tuple[int, int], float],
    *,
    min_frac: float = WATER_BODY_MIN_FRAC,
    min_tiles: int = WATER_BODY_MIN_TILES,
    dilate: int = WATER_BODY_DILATE,
) -> set[tuple[int, int]]:
    """Tiles of the large water bodies (bay, estuary), dilated by `dilate` tiles.

    Confirmed tiles have at least `min_frac` teal; 4-connected groups
    smaller than `min_tiles` (park lakes, reservoirs) are dropped so the
    raw mask's default-water outside the city is never painted.
    """
    seeds = {t for t, f in teal_frac.items() if f >= min_frac}
    keep: set[tuple[int, int]] = set()
    seen: set[tuple[int, int]] = set()
    for start in seeds:
        if start in seen:
            continue
        comp = []
        stack = [start]
        seen.add(start)
        while stack:
            tx, ty = stack.pop()
            comp.append((tx, ty))
            for nb in ((tx + 1, ty), (tx - 1, ty), (tx, ty + 1), (tx, ty - 1)):
                if nb in seeds and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        if len(comp) >= min_tiles:
            keep.update(comp)
    out = set(keep)
    for tx, ty in keep:
        for dy in range(-dilate, dilate + 1):
            for dx in range(-dilate, dilate + 1):
                out.add((tx + dx, ty + dy))
    return out


def tone_match_water(
    arr: np.ndarray,
    teal: np.ndarray,
    ref: tuple[int, int, int] = WATER_REF,
    radius: float = WATER_DETAIL_RADIUS,
    max_detail: float = WATER_MAX_DETAIL,
) -> np.ndarray:
    """Put teal pixels on one tone, keeping only their fine texture.

    The local mean is a normalized blur over this tile's own teal pixels,
    so a flat tone step at a tile seam is removed outright: each side is
    pulled to `ref` independently. (A field smoothed across tiles stays
    continuous at the seam, which leaves the step there.) Steps inside a
    tile soften to a ramp about `radius` px wide; the residual texture is
    clamped to +/- `max_detail` so that ramp cannot ring.
    """
    out = arr.astype(np.float32)
    if not teal.any():
        return arr.copy()
    m = teal.astype(np.float32)
    wsum = np.maximum(gaussian_blur_float(m, radius), 1e-3)
    detail = np.empty_like(out)
    for ch in range(3):
        local = gaussian_blur_float(out[:, :, ch] * m, radius) / wsum
        detail[:, :, ch] = np.clip(out[:, :, ch] - local, -max_detail, max_detail)
    matched = np.array(ref, dtype=np.float32)[None, None, :] + detail
    out = np.where(teal[:, :, None], matched, out)
    return np.clip(np.round(out), 0, 255).astype(np.uint8)


def fill_uncovered_water(
    arr: np.ndarray,
    water: np.ndarray,
    pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
    ref: tuple[int, int, int] = WATER_REF,
) -> tuple[np.ndarray, int]:
    """Paint uncovered pixels under the real water mask with the reference tone.

    Only pixels that carry no painted content (beige, fog, gray wash, or
    teal washed light by the restyle's fog) are replaced, so sand, piers
    and the coast stay as drawn even where the mask is coarse.
    """
    fogged = is_teal_water(arr, max_lum=230.0) & ~is_teal_water(arr)
    bad = water & (uncovered_mask(arr, pad_rgb) | fogged)
    if not bad.any():
        return arr, 0
    out = arr.copy()
    out[bad] = ref
    return out, int(bad.sum())


def build_water_field(
    counts: np.ndarray,
    *,
    scale: int = WATER_TONE_SCALE,
    fade: int = VIGNETTE_FADE,
    water_inset: int = WATER_INSET,
    threshold: float = WATER_FIELD_THRESHOLD,
    blur: int = WATER_FIELD_BLUR,
    open_r: int = WATER_FIELD_OPEN,
    close_r: int = WATER_FIELD_CLOSE,
    dist_blur: float = VIGNETTE_DIST_BLUR,
) -> np.ndarray:
    """Signed distance (z0 px) to the smoothed water body, from coarse teal counts."""
    frac = counts.astype(np.float32) / float(scale * scale)
    mask = smooth_coverage_mask(frac, threshold=threshold, blur=blur, open_r=open_r, close_r=close_r)
    max_r = int(np.ceil((fade + water_inset) / scale) + 3)
    signed = signed_distance_field(mask, max_r)
    if dist_blur > 0:
        signed = gaussian_blur_float(signed, dist_blur)
    return signed * np.float32(scale)


def water_alpha(
    dist: np.ndarray,
    water_dist: np.ndarray,
    water_frac: np.ndarray,
    *,
    fade: int = VIGNETTE_FADE,
    inset: int = VIGNETTE_INSET,
    water_inset: int = WATER_INSET,
) -> np.ndarray:
    """Per-pixel fade: land as the vignette, water by its own body field.

    Water follows the smoothed water-body distance (a long ramp that
    starts `water_inset` inside the body), capped by the land vignette so
    it never runs past it or fades a lake the land keeps. `water_frac`
    (a blurred water mask) blends the two at the shore, so there is no
    line where land and water fade differently.
    """
    a_land = fade_alpha(dist, fade, inset)
    a_water = np.minimum(fade_alpha(water_dist, fade, water_inset), a_land)
    return (a_land * (1.0 - water_frac) + a_water * water_frac).astype(np.float32)


def forest_weight(dist: np.ndarray, depth: int = FOREST_DEPTH, full: int = FOREST_FULL) -> np.ndarray:
    """Weight of clean staging over dest land: 1 near the edge, 0 deep inside."""
    return smoothstep(dist, -float(depth), -float(full)).astype(np.float32)


def is_vegetation(arr: np.ndarray) -> np.ndarray:
    """Forest and grass greens: green well above red and blue (not bay teal)."""
    rgb = arr[:, :, :3].astype(np.int16)
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    return (g - r >= 15) & (g - b >= 25) & (b - r < 10) & (rgb.mean(axis=2) < 170)


def is_featureless(arr: np.ndarray) -> np.ndarray:
    """Flat, near-neutral ground with no drawn detail (not water, not sand).

    Measured on a 4x box-averaged copy so the procedural ground's
    checker dither counts as flat; buildings, roads and trees do not.
    """
    rgb = arr[:, :, :3].astype(np.float32)
    h, w = rgb.shape[:2]
    k = 4
    small = rgb[: h - h % k, : w - w % k].reshape(h // k, k, w // k, k, 3).mean(axis=(1, 3))
    chroma = small.max(axis=2) - small.min(axis=2)
    lum = small.mean(axis=2)
    flat = _block_std(small, 4) < FLATS_MAX_STD
    teal = is_teal_water(np.clip(small, 0, 255).astype(np.uint8), max_lum=255.0)
    m = flat & (chroma < FLATS_MAX_CHROMA) & (lum > 85) & (lum < 240) & ~teal
    out = np.zeros((h, w), dtype=bool)
    out[: h - h % k, : w - w % k] = np.repeat(np.repeat(m, k, axis=0), k, axis=1)
    return out


def normalized_blur(values: np.ndarray, weights: np.ndarray, radius: float) -> tuple[np.ndarray, np.ndarray]:
    """Weighted Gaussian mean of `values` (H, W, C) and the blurred weight."""
    w = gaussian_blur_float(weights.astype(np.float32), radius)
    out = np.empty(values.shape, dtype=np.float32)
    for ch in range(values.shape[2]):
        out[:, :, ch] = gaussian_blur_float(values[:, :, ch].astype(np.float32) * weights, radius) / np.maximum(w, 1e-3)
    return out, w


def vegetation_tone_match(
    arr: np.ndarray,
    veg: np.ndarray,
    target: np.ndarray,
    strength: np.ndarray | float = 1.0,
    radius: float = FOREST_LOCAL_RADIUS,
) -> np.ndarray:
    """Move vegetation's low-frequency tone to `target`, keeping its texture.

    The local tone is measured within this tile only, so a step between
    two tiles' greens is removed on both sides; `target` is continuous
    across tiles. `strength` (0..1, scalar or per pixel) scales the shift.
    """
    if not veg.any():
        return arr.copy()
    local, _ = normalized_blur(arr.astype(np.float32), veg.astype(np.float32), radius)
    shift = (target - local) * (np.asarray(strength, dtype=np.float32)[..., None] if np.ndim(strength) else float(strength))
    out = np.where(veg[:, :, None], arr.astype(np.float32) + shift, arr.astype(np.float32))
    return np.clip(np.round(out), 0, 255).astype(np.uint8)


def edge_proximity(
    grid: dict,
    edge_tiles: set[tuple[int, int]],
    *,
    scale: int = FOREST_TONE_SCALE,
    sigma: float = FOREST_PROX_SIGMA,
    level: float = FOREST_PROX_LEVEL,
) -> np.ndarray:
    """Smooth 0..1 strength near the edge (coarse cells of `scale` px)."""
    f8 = TILE // scale
    ind = np.zeros((grid["rows"] * f8, grid["cols"] * f8), dtype=np.float32)
    for tx, ty in edge_tiles:
        if 0 <= tx < grid["cols"] and 0 <= ty < grid["rows"]:
            ind[ty * f8 : (ty + 1) * f8, tx * f8 : (tx + 1) * f8] = 1.0
    return np.clip(gaussian_blur_float(ind, sigma) / level, 0.0, 1.0)


def forest_zone(
    field: np.ndarray,
    grid: dict,
    vig_band: set[tuple[int, int]],
    exterior: set[tuple[int, int]],
    *,
    depth: int = FOREST_DEPTH,
    scale: int = VIGNETTE_SCALE,
) -> set[tuple[int, int]]:
    """Interior tiles whose distance field reaches above -depth somewhere."""
    h8 = TILE // scale
    zone: set[tuple[int, int]] = set()
    for ty in range(grid["rows"]):
        for tx in range(grid["cols"]):
            if (tx, ty) in vig_band or (tx, ty) in exterior:
                continue
            block = field[ty * h8 : (ty + 1) * h8, tx * h8 : (tx + 1) * h8]
            if block.size and float(block.max()) > -depth:
                zone.add((tx, ty))
    return zone


def water_stamp(fade: int, inset: int, water_inset: int, radius: float, ref: tuple[int, int, int],
                noise_amp: float, noise_period: float, water: bool = True, forest: bool = False,
                depth: int = FOREST_DEPTH, full: int = FOREST_FULL) -> str:
    return (
        f"{WATER_VERSION} water={int(water)} forest={int(forest)} fade={fade} inset={inset} "
        f"water_inset={water_inset} detail={radius} ref={ref[0]},{ref[1]},{ref[2]} "
        f"noise={noise_amp} period={noise_period} depth={depth} full={full}\n"
    )


def _water_frac(water: np.ndarray, radius: float = WATER_SHORE_BLUR) -> np.ndarray:
    f = water.astype(np.float32)
    return np.clip(gaussian_blur_float(f, radius), 0.0, 1.0) if radius > 0 else f


def polish_water(
    dest: Path,
    grid: dict,
    districts: list[dict],
    *,
    clean_dest: Path | None = None,
    field: np.ndarray | None = None,
    pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
    fade: int = VIGNETTE_FADE,
    inset: int = VIGNETTE_INSET,
    scale: int = VIGNETTE_SCALE,
    water_inset: int = WATER_INSET,
    radius: float = WATER_DETAIL_RADIUS,
    ref: tuple[int, int, int] = WATER_REF,
    noise_amp: float = VIGNETTE_NOISE,
    noise_period: float = VIGNETTE_NOISE_PERIOD,
    min_frac: float = WATER_BODY_MIN_FRAC,
    min_tiles: int = WATER_BODY_MIN_TILES,
    dilate: int = WATER_BODY_DILATE,
    lossless_z0: bool = True,
    water: bool = True,
    forest: bool = False,
    flats: bool = False,
    tile_filter=None,
    depth: int = FOREST_DEPTH,
    full: int = FOREST_FULL,
) -> set[tuple[int, int]]:
    """Bay water tone and fade (`water`), near-edge land refresh (`forest`).

    Both start from immutable sources and write each tile once per run.
    """
    cols, rows = grid["cols"], grid["rows"]
    ox, oy = grid["ox"], grid["oy"]
    clean_dest = clean_dest or (dest / ".vignette-clean")
    dest0 = dest / "0"
    snap0 = dest / ".water-src" / "0"
    snap0.mkdir(parents=True, exist_ok=True)
    if field is None:
        dist_path = dest / ".vignette-dist.npz"
        if not dist_path.exists():
            raise SystemExit("--polish-water/--polish-forest need the vignette field; run --polish-vignette first")
        field = np.load(dist_path)["field"]
    if water and any("chunks" in d for d in districts):
        attach_water_masks(districts)
    present = present_from_disk(dest, cols, rows)
    vig_band, exterior = edge_band_from_field(field, grid, fade=fade, inset=inset, scale=scale)
    fzone = forest_zone(field, grid, vig_band, exterior, depth=depth, scale=scale) if forest else set()
    # Vegetation tone (near the edge and on forest-dominant ground) and flats
    # fog (near the edge) are decided after the stats pass.
    prox = edge_proximity(grid, vig_band | exterior) if (forest or flats) else None
    ftiles: set[tuple[int, int]] = set()

    stamp = water_stamp(fade, inset, water_inset, radius, ref, noise_amp, noise_period,
                        water=water, forest=forest, depth=depth, full=full) + f"flats={int(flats)}\n"
    stamp_path = dest / ".water-stamp"
    progress_path = dest / ".water-progress.jsonl"
    if stamp_path.exists() and stamp_path.read_text() != stamp:
        print("  water/forest parameters changed, redoing the pass", flush=True)
        progress_path.unlink(missing_ok=True)

    def source(tx: int, ty: int) -> np.ndarray | None:
        if (tx, ty) in vig_band:
            p = clean_dest / "0" / f"{tx}_{ty}.webp"
        else:
            p = snap0 / f"{tx}_{ty}.webp"
            if not p.exists():
                p = dest0 / f"{tx}_{ty}.webp"
        if not p.exists():
            return None
        with Image.open(p) as im:
            return np.asarray(im.convert("RGB"))

    def mask_at(tx: int, ty: int) -> np.ndarray:
        if not water:
            return np.zeros((TILE, TILE), dtype=bool)
        m = water_mask_roi(districts, ox + tx * TILE, oy + ty * TILE)
        return m > 127

    def clean_at(tx: int, ty: int) -> np.ndarray | None:
        cp = clean_dest / "0" / f"{tx}_{ty}.webp"
        if not cp.exists():
            return None
        with Image.open(cp) as im:
            return np.asarray(im.convert("RGB"))

    def composed(tx: int, ty: int, water: np.ndarray) -> np.ndarray | None:
        """The pixels this pass starts from (stats and apply must agree).

        Edge band: clean staging. Interior: dest land (snapshot), with the
        water taken from clean staging, since older dest water can carry
        halo blotches. In the forest zone the land also cross-fades to
        clean staging along the global distance field.
        """
        arr = source(tx, ty)
        if (tx, ty) in vig_band:
            return arr
        in_forest = (tx, ty) in fzone
        if not (in_forest or water.any()):
            return arr
        carr = clean_at(tx, ty)
        if carr is None:
            return arr
        if arr is None:
            return carr if in_forest else None
        if in_forest:
            w = forest_weight(upsample_distance(field, tx, ty, scale=scale), depth, full)[:, :, None]
            arr = np.clip(np.round(arr * (1.0 - w) + carr * w), 0, 255).astype(np.uint8)
        return np.where(water[:, :, None], carr, arr) if water.any() else arr

    # 1. stats over every non-exterior tile with mask water
    h8 = TILE // WATER_TONE_SCALE
    rows_iter = range(rows) if water else range(0)
    counts = np.zeros((rows * h8, cols * h8), dtype=np.float32)
    teal_frac: dict[tuple[int, int], float] = {}
    has_water: set[tuple[int, int]] = set()
    t0 = time.time()
    n = 0
    for ty in rows_iter:
        for tx in range(cols):
            if (tx, ty) in exterior:
                continue
            wmask = mask_at(tx, ty)
            if not wmask.any():
                continue
            has_water.add((tx, ty))
            arr = composed(tx, ty, wmask)
            n += 1
            if arr is None:
                continue
            teal = is_teal_water(arr) & wmask
            teal_frac[(tx, ty)] = float(teal.mean())
            if not teal.any():
                continue
            s = WATER_TONE_SCALE
            tf = teal.astype(np.float32)
            blk = tf.reshape(h8, s, h8, s).sum(axis=(1, 3))
            counts[ty * h8 : (ty + 1) * h8, tx * h8 : (tx + 1) * h8] = blk
            if n % 500 == 0:
                print(f"  water stats {n} tiles, {time.time() - t0:.1f}s", flush=True)
    body = water_body_tiles(teal_frac, min_frac=min_frac, min_tiles=min_tiles, dilate=dilate)
    wtodo = (has_water & body) - exterior
    todo = sorted(wtodo | fzone, key=lambda t: (t[1], t[0]))
    print(
        f"  water stats done: {len(has_water)} mask tiles, body {len(body)}, "
        f"forest {len(fzone)}, todo {len(todo)}, "
        f"{time.time() - t0:.1f}s",
        flush=True,
    )
    print("  building the water-body field", flush=True)
    water_field = build_water_field(counts, fade=fade, water_inset=water_inset)
    del counts

    # 1b. vegetation tone field (cross-tile mean green) and flats fraction,
    # over every non-exterior tile
    veg_field = veg_weight = veg_strength = flats_field = flats_wfield = None
    f8 = TILE // FOREST_TONE_SCALE
    sc = FOREST_TONE_SCALE
    if forest or flats:
        vsums = np.zeros((rows * f8, cols * f8, 3), dtype=np.float32)
        vcounts = np.zeros((rows * f8, cols * f8), dtype=np.float32)
        fcounts = np.zeros((rows * f8, cols * f8), dtype=np.float32)
        t0 = time.time()
        k = 0
        for ty in range(rows):
            for tx in range(cols):
                if (tx, ty) in exterior:
                    continue
                arr = composed(tx, ty, np.zeros((TILE, TILE), dtype=bool))
                if arr is None:
                    continue
                k += 1
                blk = (slice(ty * f8, (ty + 1) * f8), slice(tx * f8, (tx + 1) * f8))
                if forest:
                    veg = is_vegetation(arr)
                    if veg.any():
                        vf = veg.astype(np.float32)
                        vcounts[blk] = vf.reshape(f8, sc, f8, sc).sum(axis=(1, 3))
                        for ch in range(3):
                            vsums[blk + (ch,)] = (arr[:, :, ch].astype(np.float32) * vf).reshape(f8, sc, f8, sc).sum(axis=(1, 3))
                if flats and float(prox[blk].max()) > 0.01:
                    feat = is_featureless(arr)
                    if (tx, ty) in wtodo:
                        feat &= ~mask_at(tx, ty)  # restyle fog over the bay is filled, not fogged
                    fcounts[blk] = feat.astype(np.float32).reshape(f8, sc, f8, sc).sum(axis=(1, 3))
                if k % 1000 == 0:
                    print(f"  forest/flats stats {k} tiles, {time.time() - t0:.1f}s", flush=True)
        area = float(sc * sc)
        if forest:
            vmean = vsums / np.maximum(vcounts, 1.0)[:, :, None]
            veg_field, veg_weight = normalized_blur(vmean, vcounts, FOREST_TONE_SIGMA)
            # cells far from any vegetation keep their own tone (no shift)
            veg_weight = veg_weight / area
            dom = smoothstep(gaussian_blur_float(vcounts / area, FOREST_DOM_SIGMA), FOREST_DOM_LO, FOREST_DOM_HI)
            veg_strength = np.maximum(prox, dom).astype(np.float32)
            del vmean
        if flats:
            ff = smoothstep(gaussian_blur_float(fcounts / area, FLATS_SIGMA), FLATS_LO, FLATS_HI)
            flats_field = (ff * prox).astype(np.float32)
            fw_ = smoothstep(gaussian_blur_float(fcounts / area, FLATS_WATER_SIGMA), FLATS_WATER_LO, FLATS_WATER_HI)
            flats_wfield = (fw_ * prox).astype(np.float32)
        del vsums, vcounts, fcounts
        for ty in range(rows):
            for tx in range(cols):
                if (tx, ty) in exterior:
                    continue
                blk = (slice(ty * f8, (ty + 1) * f8), slice(tx * f8, (tx + 1) * f8))
                if (forest and float(veg_strength[blk].max()) > 0.01) or (
                    flats and float(flats_field[blk].max()) > 0.01
                ):
                    ftiles.add((tx, ty))
        todo = sorted(set(todo) | ftiles, key=lambda t: (t[1], t[0]))
        print(f"  forest/flats fields: {len(ftiles)} tiles, todo {len(todo)}, {time.time() - t0:.1f}s", flush=True)
    if tile_filter is not None:
        todo = [t for t in todo if tile_filter(*t)]
    stamp_path.write_text(stamp)

    # 2. apply
    done = _load_progress(progress_path)
    if done:
        print(f"  resuming water pass, {len(done)} tiles already done", flush=True)
    changed = created = filled_px = 0
    t0 = time.time()
    prog = open(progress_path, "a")
    try:
        for i, (tx, ty) in enumerate(todo, 1):
            if (tx, ty) in done:
                if (dest0 / f"{tx}_{ty}.webp").exists():
                    present.add((tx, ty))
                continue
            in_band = (tx, ty) in vig_band
            dest_path = dest0 / f"{tx}_{ty}.webp"
            snap_path = snap0 / f"{tx}_{ty}.webp"
            if not in_band and dest_path.exists() and not snap_path.exists():
                shutil.copy2(dest_path, snap_path)
            is_water = (tx, ty) in wtodo
            wmask = mask_at(tx, ty) if is_water else np.zeros((TILE, TILE), dtype=bool)
            arr = composed(tx, ty, wmask)
            if arr is None:
                if not in_band:
                    prog.write(f"{tx},{ty}\n")
                    continue
                arr = np.empty((TILE, TILE, 3), dtype=np.uint8)
                arr[:] = VIEW_FOG
            out = arr
            dist = upsample_distance(field, tx, ty, scale=scale)
            if forest and (tx, ty) in ftiles:
                veg = is_vegetation(arr)
                if veg.any():
                    target = np.stack(
                        [upsample_distance(veg_field[:, :, ch], tx, ty, scale=FOREST_TONE_SCALE) for ch in range(3)],
                        axis=2,
                    )
                    vw = np.clip(upsample_distance(veg_weight, tx, ty, scale=FOREST_TONE_SCALE) / 0.05, 0.0, 1.0)
                    vs = np.clip(upsample_distance(veg_strength, tx, ty, scale=FOREST_TONE_SCALE), 0.0, 1.0)
                    out = vegetation_tone_match(arr, veg, target, vw * vs)
            if is_water:
                teal = is_teal_water(out) & wmask
                out = tone_match_water(out, teal, ref=ref, radius=radius)
                out, nf = fill_uncovered_water(out, wmask, pad_rgb=pad_rgb, ref=ref)
                filled_px += nf
            if flats and flats_field is not None:
                fw = np.clip(upsample_distance(flats_field, tx, ty, scale=FOREST_TONE_SCALE), 0.0, 1.0)
                fww = np.clip(upsample_distance(flats_wfield, tx, ty, scale=FOREST_TONE_SCALE), 0.0, 1.0)
                if float(max(fw.max(), fww.max())) > 0.01:
                    feat = is_featureless(out)
                    if is_water:
                        feat &= ~wmask
                    # soft gate: flats plus thin lines enclosed by them
                    gate = smoothstep(gaussian_blur_float(feat.astype(np.float32), FLATS_GATE_BLUR), 0.3, 0.65)
                    teal = is_teal_water(out, max_lum=255.0)
                    a = np.where(teal, fww, fw * gate)[:, :, None].astype(np.float32)
                    vf0 = np.array(VIEW_FOG, dtype=np.float32)
                    out = np.clip(np.round(out.astype(np.float32) * (1.0 - a) + vf0 * a), 0, 255).astype(np.uint8)
            if is_water or in_band:
                # Water fades in every tile (the per-tile band test would leave a
                # step where a band tile meets an interior one); land only in the band.
                wdist = upsample_distance(water_field, tx, ty, scale=WATER_TONE_SCALE)
                if noise_amp > 0:
                    nz = fade_boundary_noise(tx, ty, amplitude=noise_amp, period=noise_period)
                    dist = dist + nz
                    wdist = wdist + nz
                wfrac = _water_frac(wmask) if is_water else np.zeros((TILE, TILE), dtype=np.float32)
                if in_band:
                    alpha = water_alpha(dist, wdist, wfrac, fade=fade, inset=inset, water_inset=water_inset)
                else:
                    a_land = fade_alpha(dist, fade, inset)
                    a_water = np.minimum(fade_alpha(wdist, fade, water_inset), a_land)
                    alpha = (a_water * wfrac).astype(np.float32)
                vf = np.array(VIEW_FOG, dtype=np.float32)
                out = out.astype(np.float32) * (1.0 - alpha[:, :, None]) + vf * alpha[:, :, None]
                out = np.clip(np.round(out), 0, 255).astype(np.uint8)
            existed = dest_path.exists()
            if _almost_empty(out, pad_rgb) or float(np.abs(out.astype(np.int16) - np.array(VIEW_FOG)).max()) <= 3:
                if existed:
                    dest_path.unlink()
                present.discard((tx, ty))
            else:
                if existed:
                    with Image.open(dest_path) as im:
                        cur = np.asarray(im.convert("RGB"))
                    if int(np.abs(cur.astype(np.int16) - out.astype(np.int16)).max()) > 1:
                        _atomic_save_webp(Image.fromarray(out, "RGB"), dest_path, lossless=lossless_z0)
                        changed += 1
                else:
                    _atomic_save_webp(Image.fromarray(out, "RGB"), dest_path, lossless=lossless_z0)
                    created += 1
                present.add((tx, ty))
            prog.write(f"{tx},{ty}\n")
            if i % 50 == 0:
                prog.flush()
            if i % 200 == 0 or i == len(todo):
                print(
                    f"  water {i}/{len(todo)} changed={changed} created={created} "
                    f"filled_px={filled_px} {time.time() - t0:.1f}s",
                    flush=True,
                )
    finally:
        prog.flush()
        prog.close()
    print(f"  water done: present={len(present)} changed={changed} created={created}", flush=True)
    return present


# ---------------------------------------------------------------------------
# --polish-fogline: clean the fog boundary (free, no API)
#
# After the other passes, three things still read as squares at the edge:
# thin dashed lines left inside fogged ground, restyle windows that carry
# a gray wash with a few tree dots, and straight forest/water edges where
# a source window stops. Near the edge (the same proximity field as the
# forest pass), pixels whose surroundings are mostly fog-like (low chroma,
# light: beige fog, gray wash, flats) fade to viewer beige. Dominance is
# measured on a cross-tile 1/4-resolution mask, so thin lines enclosed by
# fog vanish, dense content stays, and the boundary is a feathered,
# noise-wobbled ramp rather than a tile edge. Sources are a one-time
# snapshot of dest (.fogline-src), so the pass is idempotent. Run it last.
# ---------------------------------------------------------------------------

FOGLINE_SCALE = 4
FOGLINE_SIGMA = 6.0          # in 1/4-res pixels (~24 z0 px)
FOGLINE_LO = 0.55
FOGLINE_HI = 0.9
FOGLINE_MAX_CHROMA = 22
FOGLINE_MIN_LUM = 95
FOGLINE_NOISE = 0.08
FOGLINE_NOISE_PERIOD = 160.0
FOGLINE_VERSION = "fogline-2"


def fog_like(arr: np.ndarray) -> np.ndarray:
    """Beige fog, gray wash and flat neutral ground (not water, not trees)."""
    rgb = arr[:, :, :3].astype(np.int16)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    lum = rgb.mean(axis=2)
    return (chroma < FOGLINE_MAX_CHROMA) & (lum > FOGLINE_MIN_LUM)


def _small_fog_mask(arr: np.ndarray, k: int = FOGLINE_SCALE) -> np.ndarray:
    """Fog-like test on k x k box means, so the procedural ground's
    checker dither (and thin lines drawn over fog) count as fog."""
    h, w = arr.shape[:2]
    rgb = arr[: h - h % k, : w - w % k, :3].astype(np.float32)
    small = rgb.reshape(h // k, k, w // k, k, 3).mean(axis=(1, 3))
    return fog_like(np.round(small)).astype(np.float32)


def fogline_weight(
    context: np.ndarray,
    *,
    sigma: float = FOGLINE_SIGMA,
    lo: float = FOGLINE_LO,
    hi: float = FOGLINE_HI,
    margin: int,
    k: int = FOGLINE_SCALE,
    noise: np.ndarray | None = None,
    strength: np.ndarray | float = 1.0,
) -> np.ndarray:
    """Fog weight for the centre tile from a padded 1/4-res fog-like context."""
    d = gaussian_blur_float(context.astype(np.float32), sigma)
    n = (context.shape[0] - 2 * margin)
    d = d[margin : margin + n, margin : margin + n]
    up = np.array(Image.fromarray(d, mode="F").resize((n * k, n * k), Image.Resampling.BICUBIC), dtype=np.float32)
    if noise is not None:
        up = up + noise
    return (smoothstep(up, lo, hi) * strength).astype(np.float32)


def fogline_stamp() -> str:
    return (
        f"{FOGLINE_VERSION} sigma={FOGLINE_SIGMA} lo={FOGLINE_LO} hi={FOGLINE_HI} "
        f"chroma={FOGLINE_MAX_CHROMA} lum={FOGLINE_MIN_LUM} noise={FOGLINE_NOISE} "
        f"period={FOGLINE_NOISE_PERIOD}"
        f" mask=boxmean\n"
    )


def polish_fogline(
    dest: Path,
    grid: dict,
    *,
    field: np.ndarray | None = None,
    pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
    fade: int = VIGNETTE_FADE,
    inset: int = VIGNETTE_INSET,
    scale: int = VIGNETTE_SCALE,
    lossless_z0: bool = True,
    tile_filter=None,
) -> set[tuple[int, int]]:
    """Fade fog-dominated pixels near the edge to beige (see section notes)."""
    cols, rows = grid["cols"], grid["rows"]
    dest0 = dest / "0"
    snap0 = dest / ".fogline-src" / "0"
    snap0.mkdir(parents=True, exist_ok=True)
    if field is None:
        dist_path = dest / ".vignette-dist.npz"
        if not dist_path.exists():
            raise SystemExit("--polish-fogline needs the vignette field; run --polish-vignette first")
        field = np.load(dist_path)["field"]
    present = present_from_disk(dest, cols, rows)
    vig_band, exterior = edge_band_from_field(field, grid, fade=fade, inset=inset, scale=scale)
    prox = edge_proximity(grid, vig_band | exterior)
    f8 = TILE // FOREST_TONE_SCALE
    stamp_path = dest / ".fogline-stamp"
    progress_path = dest / ".fogline-progress.jsonl"
    stamp = fogline_stamp()
    if stamp_path.exists() and stamp_path.read_text() != stamp:
        print("  fogline parameters changed, redoing the pass", flush=True)
        progress_path.unlink(missing_ok=True)

    def source(tx: int, ty: int) -> np.ndarray | None:
        p = snap0 / f"{tx}_{ty}.webp"
        if not p.exists():
            p = dest0 / f"{tx}_{ty}.webp"
        if not p.exists():
            return None
        with Image.open(p) as im:
            return np.asarray(im.convert("RGB"))

    zone = set()
    for ty in range(rows):
        for tx in range(cols):
            if (tx, ty) in exterior:
                continue
            if float(prox[ty * f8 : (ty + 1) * f8, tx * f8 : (tx + 1) * f8].max()) > 0.01:
                zone.add((tx, ty))
    # 1. 1/4-res fog-like masks (missing tiles count as fog: the viewer shows beige)
    n = TILE // FOGLINE_SCALE
    small: dict[tuple[int, int], np.ndarray] = {}
    need = set()
    for tx, ty in zone:
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                need.add((tx + dx, ty + dy))
    t0 = time.time()
    for i, (tx, ty) in enumerate(sorted(need), 1):
        arr = source(tx, ty) if 0 <= tx < cols and 0 <= ty < rows else None
        if arr is not None:
            small[(tx, ty)] = _small_fog_mask(arr)
        if i % 1000 == 0:
            print(f"  fogline masks {i}/{len(need)}, {time.time() - t0:.1f}s", flush=True)
    ones = np.ones((n, n), dtype=np.float32)
    stamp_path.write_text(stamp)
    todo = sorted(zone, key=lambda t: (t[1], t[0]))
    if tile_filter is not None:
        todo = [t for t in todo if tile_filter(*t)]
    print(f"  fogline zone {len(zone)} tiles, todo {len(todo)}, {time.time() - t0:.1f}s", flush=True)

    done = _load_progress(progress_path)
    margin = int(math.ceil(3 * FOGLINE_SIGMA)) + 2
    vf = np.array(VIEW_FOG, dtype=np.float32)
    changed = removed = 0
    t0 = time.time()
    prog = open(progress_path, "a")
    try:
        for i, (tx, ty) in enumerate(todo, 1):
            if (tx, ty) in done:
                continue
            arr = source(tx, ty)
            if arr is None:
                prog.write(f"{tx},{ty}\n")
                continue
            big = np.concatenate(
                [np.concatenate([small.get((tx + dx, ty + dy), ones) for dx in (-1, 0, 1)], axis=1)
                 for dy in (-1, 0, 1)],
                axis=0,
            )
            ctx = big[n - margin : 2 * n + margin, n - margin : 2 * n + margin]
            strength = np.clip(upsample_distance(prox, tx, ty, scale=FOREST_TONE_SCALE), 0.0, 1.0)
            noise = fade_boundary_noise(tx, ty, amplitude=FOGLINE_NOISE, period=FOGLINE_NOISE_PERIOD)
            w = fogline_weight(ctx, margin=margin, noise=noise, strength=strength)
            dest_path = dest0 / f"{tx}_{ty}.webp"
            snap_path = snap0 / f"{tx}_{ty}.webp"
            if float(w.max()) <= 0.01:
                out = arr
            else:
                out = arr.astype(np.float32) * (1.0 - w[:, :, None]) + vf * w[:, :, None]
                out = np.clip(np.round(out), 0, 255).astype(np.uint8)
            if dest_path.exists() and not snap_path.exists():
                shutil.copy2(dest_path, snap_path)
            if float(np.abs(out.astype(np.int16) - np.array(VIEW_FOG)).max()) <= 3:
                if dest_path.exists():
                    dest_path.unlink()
                    removed += 1
                present.discard((tx, ty))
            else:
                if dest_path.exists():
                    with Image.open(dest_path) as im:
                        cur = np.asarray(im.convert("RGB"))
                    if int(np.abs(cur.astype(np.int16) - out.astype(np.int16)).max()) > 1:
                        _atomic_save_webp(Image.fromarray(out, "RGB"), dest_path, lossless=lossless_z0)
                        changed += 1
                else:
                    _atomic_save_webp(Image.fromarray(out, "RGB"), dest_path, lossless=lossless_z0)
                present.add((tx, ty))
            prog.write(f"{tx},{ty}\n")
            if i % 50 == 0:
                prog.flush()
            if i % 500 == 0 or i == len(todo):
                print(f"  fogline {i}/{len(todo)} changed={changed} removed={removed} {time.time() - t0:.1f}s", flush=True)
    finally:
        prog.flush()
        prog.close()
    print(f"  fogline done: present={len(present)} changed={changed} removed={removed}", flush=True)
    return present


# ---------------------------------------------------------------------------
# Mint rims (--polish-mint): some forest along the edge is unrestyled
# procedural staging, whose ground dither and trees are teal (hue ~160)
# where the restyled forest is green (hue ~95), so it reads as mint
# patches against the forest. Near the edge, teal-hued pixels are rotated
# about the gray axis toward the forest hue, keeping lightness. Large teal
# areas (real water) and blue hues (glass, pools) are left alone. Sources
# are a one-time .mint-src snapshot, so the pass is idempotent.
# ---------------------------------------------------------------------------

MINT_HUE_LO = (128.0, 140.0)    # ramp in: teal starts
MINT_HUE_HI = (172.0, 184.0)    # ramp out: blue glass and pools
MINT_TARGET_HUE = 95.0
MINT_MIN_CHROMA = (4.0, 10.0)
MINT_CHROMA_SCALE = 0.85
MINT_WATER_SIGMA = 24.0         # px: water is a large teal area, trees are small
MINT_WATER_LO = 0.25
MINT_WATER_HI = 0.5
MINT_WATER_MAX_LUM = 95.0      # sea and rivers are darker than the teal haze
MINT_WATER_MAX_STD = 6.0       # and flat, unlike clumps of teal trees
MINT_TEXTURE_BLOCK = 8
MINT_TEXTURE_SIGMA = 12.0
MINT_TEXTURE = (2.0, 6.0)      # lum std: smooth water fades stay teal
MINT_VERSION = "mint-4"


def rgb_hue(rgb: np.ndarray) -> np.ndarray:
    """Hue in degrees (0..360) from the opponent axes, like HSV hue."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return np.degrees(np.arctan2(np.sqrt(3.0) * (g - b), 2.0 * r - g - b)) % 360.0


def rotate_hue(rgb: np.ndarray, degrees: np.ndarray, chroma_scale: np.ndarray | float = 1.0) -> np.ndarray:
    """Rotate colours about the gray axis by `degrees` (per pixel), scaling chroma."""
    mean = rgb.mean(axis=-1, keepdims=True)
    d = rgb - mean
    t = np.radians(degrees)[..., None]
    k = np.ones(3, np.float32) / np.sqrt(3.0)
    cross = np.cross(np.broadcast_to(k, d.shape), d)
    rot = d * np.cos(t) + cross * np.sin(t)  # d is orthogonal to k
    scale = np.asarray(chroma_scale, np.float32)
    return mean + rot * (scale[..., None] if scale.ndim else scale)


def flat_teal_water(arr: np.ndarray, k: int = 4) -> np.ndarray:
    """Dark teal with little texture: sea and rivers, not clumps of teal trees."""
    h, w = arr.shape[:2]
    rgb = arr[: h - h % k, : w - w % k, :3].astype(np.float32)
    small = rgb.reshape(h // k, k, w // k, k, 3).mean(axis=(1, 3))
    m = is_teal_water(np.round(small).astype(np.uint8), max_lum=MINT_WATER_MAX_LUM)
    m &= _block_std(small, 4) < MINT_WATER_MAX_STD
    out = np.zeros((h, w), dtype=bool)
    out[: h - h % k, : w - w % k] = np.repeat(np.repeat(m, k, axis=0), k, axis=1)
    return out


def mint_weight(arr: np.ndarray) -> np.ndarray:
    """0..1 weight of teal-hued, non-water pixels to turn forest green."""
    rgb = arr[:, :, :3].astype(np.float32)
    hue = rgb_hue(rgb)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    w = smoothstep(hue, *MINT_HUE_LO) * (1.0 - smoothstep(hue, *MINT_HUE_HI))
    w = w * smoothstep(chroma, *MINT_MIN_CHROMA)
    water = gaussian_blur_float(flat_teal_water(arr).astype(np.float32), MINT_WATER_SIGMA)
    w = w * (1.0 - smoothstep(water, MINT_WATER_LO, MINT_WATER_HI))
    # staging is textured (dither, trees); water fades and fog are smooth
    texture = gaussian_blur_float(_block_std(rgb, MINT_TEXTURE_BLOCK), MINT_TEXTURE_SIGMA)
    w = w * smoothstep(texture, *MINT_TEXTURE)
    return w.astype(np.float32)


def demint(arr: np.ndarray, strength: np.ndarray | float = 1.0) -> np.ndarray:
    """Turn teal staging greens toward the restyled forest hue."""
    rgb = arr[:, :, :3].astype(np.float32)
    w = mint_weight(arr) * np.asarray(strength, np.float32)
    if float(w.max()) <= 0.01:
        return arr[:, :, :3].copy()
    hue = rgb_hue(rgb)
    delta = ((MINT_TARGET_HUE - hue + 180.0) % 360.0 - 180.0) * w
    out = rotate_hue(rgb, delta, 1.0 - (1.0 - MINT_CHROMA_SCALE) * w)
    return np.clip(np.round(out), 0, 255).astype(np.uint8)


def mint_stamp() -> str:
    return (
        f"{MINT_VERSION} hue={MINT_HUE_LO},{MINT_HUE_HI}->{MINT_TARGET_HUE} chroma={MINT_MIN_CHROMA}x{MINT_CHROMA_SCALE} "
        f"texture={MINT_TEXTURE_BLOCK},{MINT_TEXTURE_SIGMA},{MINT_TEXTURE} water={MINT_WATER_SIGMA},{MINT_WATER_MAX_LUM},{MINT_WATER_MAX_STD},{MINT_WATER_LO}-{MINT_WATER_HI}\n"
    )


def polish_mint(
    dest: Path,
    grid: dict,
    *,
    field: np.ndarray | None = None,
    fade: int = VIGNETTE_FADE,
    inset: int = VIGNETTE_INSET,
    scale: int = VIGNETTE_SCALE,
    lossless_z0: bool = True,
    tile_filter=None,
) -> set[tuple[int, int]]:
    """Turn teal staging rims near the edge forest green (see section notes)."""
    cols, rows = grid["cols"], grid["rows"]
    dest0 = dest / "0"
    snap0 = dest / ".mint-src" / "0"
    snap0.mkdir(parents=True, exist_ok=True)
    if field is None:
        dist_path = dest / ".vignette-dist.npz"
        if not dist_path.exists():
            raise SystemExit("--polish-mint needs the vignette field; run --polish-vignette first")
        field = np.load(dist_path)["field"]
    present = present_from_disk(dest, cols, rows)
    vig_band, exterior = edge_band_from_field(field, grid, fade=fade, inset=inset, scale=scale)
    prox = edge_proximity(grid, vig_band | exterior)
    f8 = TILE // FOREST_TONE_SCALE
    stamp_path = dest / ".mint-stamp"
    progress_path = dest / ".mint-progress.jsonl"
    stamp = mint_stamp()
    if stamp_path.exists() and stamp_path.read_text() != stamp:
        print("  mint parameters changed, redoing the pass", flush=True)
        progress_path.unlink(missing_ok=True)
    stamp_path.write_text(stamp)
    todo = []
    for ty in range(rows):
        for tx in range(cols):
            if (tx, ty) in exterior or (tx, ty) not in present:
                continue
            if float(prox[ty * f8 : (ty + 1) * f8, tx * f8 : (tx + 1) * f8].max()) > 0.01:
                todo.append((tx, ty))
    if tile_filter is not None:
        todo = [t for t in todo if tile_filter(*t)]
    done = _load_progress(progress_path)
    print(f"  mint todo {len(todo)} tiles", flush=True)
    changed = 0
    t0 = time.time()
    prog = open(progress_path, "a")
    try:
        for i, (tx, ty) in enumerate(todo, 1):
            if (tx, ty) in done:
                continue
            dest_path = dest0 / f"{tx}_{ty}.webp"
            snap_path = snap0 / f"{tx}_{ty}.webp"
            src = snap_path if snap_path.exists() else dest_path
            with Image.open(src) as im:
                arr = np.asarray(im.convert("RGB"))
            strength = np.clip(upsample_distance(prox, tx, ty, scale=FOREST_TONE_SCALE), 0.0, 1.0)
            out = demint(arr, strength)
            if not snap_path.exists():
                shutil.copy2(dest_path, snap_path)
            with Image.open(dest_path) as im:
                cur = np.asarray(im.convert("RGB"))
            if int(np.abs(cur.astype(np.int16) - out.astype(np.int16)).max()) > 1:
                _atomic_save_webp(Image.fromarray(out, "RGB"), dest_path, lossless=lossless_z0)
                changed += 1
            prog.write(f"{tx},{ty}\n")
            if i % 50 == 0:
                prog.flush()
            if i % 500 == 0 or i == len(todo):
                print(f"  mint {i}/{len(todo)} changed={changed} {time.time() - t0:.1f}s", flush=True)
    finally:
        prog.flush()
        prog.close()
    print(f"  mint done: changed={changed}", flush=True)
    return present


# ---------------------------------------------------------------------------
# Listed spots (--polish-spots FILE): a few one-off blemishes no general
# rule catches without hurting elsewhere, listed in a JSON file in global
# z0 pixels (tile * 512 + offset). Kinds:
#   {"kind": "fog", "center": [x, y], "radius": [rx, ry]}
#       fade an ellipse to viewer beige, feathered over its outer half;
#   {"kind": "water", "box": [x0, y0, x1, y1], "sigma": px}
#       redraw an isolated water blob inside the box with a smoothed outline
#       (its water/fog blend, blurred and re-thresholded), so straight
#       tile-edge cuts and notches round off;
#   {"kind": "veil", "box": [x0, y0, x1, y1], "sigma": px}
#       pull a milky wash on water back toward the deep-water tone in the
#       box, keeping wave texture; land and buildings are left alone.
# Sources are a one-time .spots-src snapshot, so the pass is idempotent.
# ---------------------------------------------------------------------------

SPOTS_VERSION = "spots-2"
SPOTS_FEATHER = 0.5


def spot_bbox(spot: dict) -> tuple[int, int, int, int]:
    if spot["kind"] == "fog":
        (cx, cy), (rx, ry) = spot["center"], spot["radius"]
        return int(cx - rx), int(cy - ry), int(math.ceil(cx + rx)), int(math.ceil(cy + ry))
    if spot["kind"] in ("water", "veil"):
        x0, y0, x1, y1 = spot["box"]
        return int(x0), int(y0), int(x1), int(y1)
    raise ValueError(f"unknown spot kind {spot['kind']!r}")


def fog_spot(canvas: np.ndarray, origin: tuple[int, int], spot: dict) -> np.ndarray:
    """Fade an ellipse of `canvas` (global origin x, y) to viewer beige."""
    (cx, cy), (rx, ry) = spot["center"], spot["radius"]
    h, w = canvas.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt(((xx + origin[0] - cx) / rx) ** 2 + ((yy + origin[1] - cy) / ry) ** 2)
    a = 1.0 - smoothstep(d, 1.0 - SPOTS_FEATHER, 1.0)
    fog = np.array(VIEW_FOG, np.float32)
    out = canvas.astype(np.float32) * (1.0 - a[:, :, None]) + fog * a[:, :, None]
    return np.clip(np.round(out), 0, 255).astype(np.uint8)


def water_spot(canvas: np.ndarray, spot: dict) -> np.ndarray:
    """Redraw the water blob in `canvas` with a smoothed outline."""
    rgb = canvas.astype(np.float32)
    fog = np.array(VIEW_FOG, np.float32)
    deep = is_teal_water(canvas)
    if not deep.any():
        return canvas.copy()
    water = np.median(rgb[deep], axis=0)
    # per-pixel blend factor between fog (0) and water (1), along fog -> water
    axis = water - fog
    a = np.clip(((rgb - fog) @ axis) / float(axis @ axis), 0.0, 1.0)
    sigma = float(spot.get("sigma", 60))
    smooth = gaussian_blur_float(a.astype(np.float32), sigma)
    shape = smoothstep(smooth, 0.2, 0.7)
    shape = gaussian_blur_float(shape.astype(np.float32), sigma / 2.0)
    out = fog * (1.0 - shape[:, :, None]) + water * shape[:, :, None]
    return np.clip(np.round(out), 0, 255).astype(np.uint8)



def veil_spot(canvas: np.ndarray, spot: dict) -> np.ndarray:
    """Pull a milky wash on water in `canvas` toward the deep-water tone."""
    rgb = canvas.astype(np.float32)
    h, w, _ = rgb.shape
    deep = is_teal_water(canvas)
    washed = is_washed_water(canvas)
    if deep.any():
        water = np.median(rgb[deep], axis=0)
    elif washed.any():
        # no deep sample: use the darker washed pixels as the target
        lum = rgb.mean(axis=2)
        pick = washed & (lum <= np.median(lum[washed]))
        water = np.median(rgb[pick if pick.any() else washed], axis=0)
    else:
        return canvas.copy()
    fog = np.array(VIEW_FOG, np.float32)
    axis = fog - water
    denom = float(axis @ axis) + 1e-6
    proj = np.clip(((rgb - water) @ axis) / denom, 0.0, 1.0)
    closest = water + proj[:, :, None] * axis
    dist = np.sqrt(((rgb - closest) ** 2).sum(axis=2))
    # leftover green keeps this on water, not on beige sand or a glass wall
    still_waterish = rgb[:, :, 1] > rgb[:, :, 0] + 4.0
    on_line = (dist < 14.0) & still_waterish & (proj > 0.06) & (proj < 0.95)
    amount = on_line.astype(np.float32)
    if not amount.any():
        return canvas.copy()
    sigma = float(spot.get("sigma", 40))
    amount = gaussian_blur_float(amount, max(1.0, sigma / 2.0))
    # fade the fix out at the crop edge so neighbouring tiles do not step
    pad = max(8, int(sigma / 2.0))
    edge = np.ones((h, w), np.float32)
    edge[:pad, :] *= np.linspace(0.0, 1.0, pad, dtype=np.float32)[:, None]
    edge[-pad:, :] *= np.linspace(1.0, 0.0, pad, dtype=np.float32)[:, None]
    edge[:, :pad] *= np.linspace(0.0, 1.0, pad, dtype=np.float32)[None, :]
    edge[:, -pad:] *= np.linspace(1.0, 0.0, pad, dtype=np.float32)[None, :]
    amount *= edge
    # replace the low-frequency wash with the deep-water tone; keep the waves
    low = np.stack([gaussian_blur_float(rgb[:, :, c], 8.0) for c in range(3)], axis=2)
    out = rgb + amount[:, :, None] * (water - low)
    return np.clip(np.round(out), 0, 255).astype(np.uint8)


def load_spots(path: Path) -> list[dict]:
    spots = json.loads(Path(path).read_text())["spots"]
    for spot in spots:
        spot_bbox(spot)  # validates the kind
    return spots


def spots_stamp(spots: list[dict]) -> str:
    return f"{SPOTS_VERSION} feather={SPOTS_FEATHER} {json.dumps(spots, sort_keys=True)}\n"


def polish_spots(dest: Path, grid: dict, spots: list[dict], *, lossless_z0: bool = True) -> set[tuple[int, int]]:
    """Apply listed spot fixes to z0 (see section notes)."""
    cols, rows = grid["cols"], grid["rows"]
    dest0 = dest / "0"
    snap0 = dest / ".spots-src" / "0"
    snap0.mkdir(parents=True, exist_ok=True)
    stamp_path = dest / ".spots-stamp"
    stamp = spots_stamp(spots)
    present = present_from_disk(dest, cols, rows)
    if stamp_path.exists() and stamp_path.read_text() == stamp:
        print("  spots already applied", flush=True)
        return present
    fog_tile = np.broadcast_to(np.array(VIEW_FOG, np.uint8), (TILE, TILE, 3))

    tiles: set[tuple[int, int]] = set()
    for spot in spots:
        x0, y0, x1, y1 = spot_bbox(spot)
        for ty in range(max(0, y0 // TILE), min(rows - 1, y1 // TILE) + 1):
            for tx in range(max(0, x0 // TILE), min(cols - 1, x1 // TILE) + 1):
                tiles.add((tx, ty))
    # 1. start every touched tile from its snapshot (or snapshot it now)
    work: dict[tuple[int, int], np.ndarray] = {}
    for tx, ty in sorted(tiles):
        snap, cur = snap0 / f"{tx}_{ty}.webp", dest0 / f"{tx}_{ty}.webp"
        if not snap.exists() and cur.exists():
            shutil.copy2(cur, snap)
        if snap.exists():
            with Image.open(snap) as im:
                work[(tx, ty)] = np.asarray(im.convert("RGB")).copy()
        else:
            work[(tx, ty)] = fog_tile.copy()
    # 2. apply spots in order on a canvas over their tiles
    for spot in spots:
        x0, y0, x1, y1 = spot_bbox(spot)
        tx0, ty0 = max(0, x0 // TILE), max(0, y0 // TILE)
        tx1, ty1 = min(cols - 1, x1 // TILE), min(rows - 1, y1 // TILE)
        canvas = np.concatenate(
            [np.concatenate([work[(tx, ty)] for tx in range(tx0, tx1 + 1)], axis=1) for ty in range(ty0, ty1 + 1)],
            axis=0,
        )
        ox, oy = tx0 * TILE, ty0 * TILE
        cx0, cy0 = max(0, x0 - ox), max(0, y0 - oy)
        cx1, cy1 = min(canvas.shape[1], x1 - ox), min(canvas.shape[0], y1 - oy)
        crop = canvas[cy0:cy1, cx0:cx1]
        if spot["kind"] == "fog":
            crop = fog_spot(crop, (ox + cx0, oy + cy0), spot)
        elif spot["kind"] == "veil":
            crop = veil_spot(crop, spot)
        else:
            crop = water_spot(crop, spot)
        canvas[cy0:cy1, cx0:cx1] = crop
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                y, x = (ty - ty0) * TILE, (tx - tx0) * TILE
                work[(tx, ty)] = canvas[y : y + TILE, x : x + TILE].copy()
    # 3. write back; all-beige tiles are removed (the viewer shows beige)
    changed = removed = 0
    for (tx, ty), out in sorted(work.items()):
        path = dest0 / f"{tx}_{ty}.webp"
        if float(np.abs(out.astype(np.int16) - np.array(VIEW_FOG)).max()) <= 3:
            if path.exists():
                path.unlink()
                removed += 1
            present.discard((tx, ty))
            continue
        if path.exists():
            with Image.open(path) as im:
                cur = np.asarray(im.convert("RGB"))
            if int(np.abs(cur.astype(np.int16) - out.astype(np.int16)).max()) <= 1:
                present.add((tx, ty))
                continue
        _atomic_save_webp(Image.fromarray(out, "RGB"), path, lossless=lossless_z0)
        present.add((tx, ty))
        changed += 1
    stamp_path.write_text(stamp)
    print(f"  spots done: {len(spots)} spots, {len(tiles)} tiles, changed={changed} removed={removed}", flush=True)
    return present


# ---------------------------------------------------------------------------
# --polish-seams: feather a tone jump across a z0 tile edge, or across a
# restyle-window hem when those windows are still on disk. Only the
# low-frequency colour offset is moved, and only where both sides are the
# same kind of surface (water, paddies, sand) so a building cut, a river
# bank, or a path is not smeared. Idempotent: a stamp mismatch re-snapshots
# the current dest (so a preceding spots pass is picked up) and starts again.
# ---------------------------------------------------------------------------

SEAMS_VERSION = "seams-1"
SEAMS_FEATHER = 16
SEAMS_JUMP = 8.0
SEAMS_SMOOTH = 12.0
SEAMS_CHROMA = 0.07
SEAMS_MASK_SMOOTH = 4.0
SEAMS_WINDOW = 1024
SEAMS_OVERLAP = 256


def gaussian_1d(field: np.ndarray, radius: float) -> np.ndarray:
    """Blur along axis 0. `field` is (N,) or (N, C)."""
    if radius <= 0:
        return field.astype(np.float32, copy=False)
    sigma = float(radius)
    r = max(1, int(math.ceil(3.0 * sigma)))
    x = np.arange(-r, r + 1, dtype=np.float64)
    kernel = np.exp(-(x * x) / (2.0 * sigma * sigma))
    kernel /= kernel.sum()
    kernel = kernel.astype(np.float32)
    if field.ndim == 1:
        src = np.pad(field.astype(np.float32), (r, r), mode="edge")
        windows = np.lib.stride_tricks.sliding_window_view(src, kernel.size)
        return np.tensordot(windows, kernel, axes=([1], [0])).astype(np.float32)
    src = np.pad(field.astype(np.float32), ((r, r), (0, 0)), mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(src, kernel.size, axis=0)
    return np.tensordot(windows, kernel, axes=([2], [0])).astype(np.float32)


def is_washed_water(arr: np.ndarray, min_lum: float = 90.0) -> np.ndarray:
    """Teal chroma at a higher luminance than `is_teal_water` (a fog wash on water)."""
    rgb = arr[:, :, :3].astype(np.int16)
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    lum = rgb.mean(axis=2)
    return (g - r >= 16) & (b - r >= 8) & (g - b >= -12) & (g - b <= 40) & (lum >= min_lum)


def _waterish(mean_rgb: np.ndarray) -> np.ndarray:
    """Per-row (N, 3) means that look like water, including a pale wash."""
    r, g, b = mean_rgb[:, 0], mean_rgb[:, 1], mean_rgb[:, 2]
    lum = mean_rgb.mean(axis=1)
    return (g - r >= 16) & (b - r >= 8) & (g - b >= -12) & (g - b <= 40) & (lum < 200)


def _near_fog(mean_rgb: np.ndarray, tol: float = 10.0) -> np.ndarray:
    fog = np.array(VIEW_FOG, np.float32)
    return np.max(np.abs(mean_rgb - fog), axis=1) <= tol


def _chroma(mean_rgb: np.ndarray) -> np.ndarray:
    s = mean_rgb.sum(axis=1, keepdims=True) + 1.0
    return mean_rgb / s


def _feather_weights(width: int) -> np.ndarray:
    """1 at the seam, 0 at `width`. Smoothstep."""
    if width <= 0:
        return np.zeros((0,), np.float32)
    t = np.linspace(1.0, 0.0, width, endpoint=False, dtype=np.float32)
    return t * t * (3.0 - 2.0 * t)


def blend_seam_pair(
    left: np.ndarray,
    right: np.ndarray,
    *,
    feather: int = SEAMS_FEATHER,
    jump: float = SEAMS_JUMP,
    smooth: float = SEAMS_SMOOTH,
    chroma: float = SEAMS_CHROMA,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Meet two arrays at their shared vertical edge by a low-frequency offset.

    `left` is to the west of the seam (its last columns), `right` to the east.
    Horizontal seams are applied by rotating the pair before the call.
    Returns the new arrays and how many seam rows were blended.
    """
    h, lw, _ = left.shape
    _, rw, _ = right.shape
    width = min(int(feather), lw, rw)
    if width < 2 or h < 2:
        return left, right, 0
    lmean = left[:, -2:].astype(np.float32).mean(axis=1)
    rmean = right[:, :2].astype(np.float32).mean(axis=1)
    off = gaussian_1d(rmean - lmean, smooth)
    mag = np.abs(off).mean(axis=1)
    chroma_d = np.abs(_chroma(lmean) - _chroma(rmean)).max(axis=1)
    same = (chroma_d < chroma) | (_waterish(lmean) & _waterish(rmean))
    ok = (mag >= jump) & same & ~_near_fog(lmean) & ~_near_fog(rmean)
    mask = gaussian_1d(ok.astype(np.float32), SEAMS_MASK_SMOOTH)
    mask = np.clip(mask, 0.0, 1.0)
    if float(mask.max()) < 1e-4:
        return left, right, 0
    weights = _feather_weights(width)
    lout = left.astype(np.float32)
    rout = right.astype(np.float32)
    # half the offset each way, fading out from the seam
    for i, w in enumerate(weights):
        gain = (0.5 * w) * mask[:, None]
        lout[:, lw - 1 - i, :] += gain * off
        rout[:, i, :] -= gain * off
    n = int((mask >= 0.25).sum())
    return (
        np.clip(np.round(lout), 0, 255).astype(np.uint8),
        np.clip(np.round(rout), 0, 255).astype(np.uint8),
        n,
    )


def restyle_hem_lines(
    districts: list[dict] | None,
    grid: dict,
    *,
    window: int = SEAMS_WINDOW,
    overlap: int = SEAMS_OVERLAP,
) -> tuple[list[int], list[int]]:
    """Grid-pixel x and y of restyle-window overlap starts, when those windows exist on disk."""
    xs: set[int] = set()
    ys: set[int] = set()
    if not districts:
        return [], []
    ox, oy = grid["ox"], grid["oy"]
    for d in districts:
        tiles_dir = d.get("tiles")
        if not tiles_dir or not Path(tiles_dir).is_dir():
            continue
        if not any(Path(tiles_dir).glob("*.png")):
            continue
        dox, doy = d["origin"]
        for t in d["windows"]:
            if t.x > 0:
                xs.add(int(dox + t.x - ox))
            if t.y > 0:
                ys.add(int(doy + t.y - oy))
    return sorted(xs), sorted(ys)


def seams_stamp(
    feather: int,
    jump: float,
    smooth: float,
    chroma: float,
    hem_x: list[int],
    hem_y: list[int],
    spots_stamp_text: str = "",
) -> str:
    return (
        f"{SEAMS_VERSION} feather={feather} jump={jump} smooth={smooth} "
        f"chroma={chroma} hem_x={len(hem_x)} hem_y={len(hem_y)} spots={spots_stamp_text!r}\n"
    )


def _seams_load(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB")).copy()


def polish_seams(
    dest: Path,
    grid: dict,
    districts: list[dict] | None = None,
    *,
    lossless_z0: bool = True,
    feather: int = SEAMS_FEATHER,
    jump: float = SEAMS_JUMP,
    smooth: float = SEAMS_SMOOTH,
    chroma: float = SEAMS_CHROMA,
) -> set[tuple[int, int]]:
    """Feather jumping z0 tile edges, and restyle-window hems when they are on disk."""
    cols, rows = grid["cols"], grid["rows"]
    dest0 = dest / "0"
    snap0 = dest / ".seams-src" / "0"
    stamp_path = dest / ".seams-stamp"
    spots_text = (dest / ".spots-stamp").read_text() if (dest / ".spots-stamp").exists() else ""
    hem_x, hem_y = restyle_hem_lines(districts, grid)
    stamp = seams_stamp(feather, jump, smooth, chroma, hem_x, hem_y, spots_text)
    present = present_from_disk(dest, cols, rows)
    if stamp_path.exists() and stamp_path.read_text() == stamp:
        print("  seams already applied", flush=True)
        return present
    # A stamp mismatch (or the first run) snapshots the current dest, so a
    # spots pass that just rewrote these tiles is the source, not an old blend.
    if snap0.exists():
        shutil.rmtree(snap0)
    snap0.mkdir(parents=True, exist_ok=True)
    for tx, ty in present:
        src = dest0 / f"{tx}_{ty}.webp"
        if src.exists():
            shutil.copy2(src, snap0 / f"{tx}_{ty}.webp")

    def load(tx: int, ty: int) -> np.ndarray | None:
        key = (tx, ty)
        if key in work:
            return work[key]
        path = snap0 / f"{tx}_{ty}.webp"
        if not path.exists():
            return None
        work[key] = _seams_load(path)
        return work[key]

    work: dict[tuple[int, int], np.ndarray] = {}
    edge_rows = 0
    # Vertical tile edges, then horizontal (the second pass sees the first).
    for ty in range(rows):
        for tx in range(cols - 1):
            if (tx, ty) not in present or (tx + 1, ty) not in present:
                continue
            left, right = load(tx, ty), load(tx + 1, ty)
            if left is None or right is None:
                continue
            a, b, n = blend_seam_pair(left, right, feather=feather, jump=jump, smooth=smooth, chroma=chroma)
            if n:
                work[(tx, ty)], work[(tx + 1, ty)] = a, b
                edge_rows += n
    for ty in range(rows - 1):
        for tx in range(cols):
            if (tx, ty) not in present or (tx, ty + 1) not in present:
                continue
            top, bot = load(tx, ty), load(tx, ty + 1)
            if top is None or bot is None:
                continue
            # Rotate so the shared edge is vertical, reuse the same blender.
            a, b, n = blend_seam_pair(
                np.rot90(top, 1),
                np.rot90(bot, 1),
                feather=feather,
                jump=jump,
                smooth=smooth,
                chroma=chroma,
            )
            if n:
                work[(tx, ty)] = np.ascontiguousarray(np.rot90(a, -1))
                work[(tx, ty + 1)] = np.ascontiguousarray(np.rot90(b, -1))
                edge_rows += n

    # Interior restyle-window hems: the same blender, split inside one tile.
    hem_rows = 0
    margin = max(feather, 2)
    for hx in hem_x:
        local = hx % TILE
        tx = hx // TILE
        if tx < 0 or tx >= cols or local < margin or local > TILE - margin:
            continue
        for ty in range(rows):
            if (tx, ty) not in present:
                continue
            tile = load(tx, ty)
            if tile is None:
                continue
            a, b, n = blend_seam_pair(
                tile[:, :local].copy(),
                tile[:, local:].copy(),
                feather=feather,
                jump=jump,
                smooth=smooth,
                chroma=chroma,
            )
            if n:
                out = tile.copy()
                out[:, :local], out[:, local:] = a, b
                work[(tx, ty)] = out
                hem_rows += n
    for hy in hem_y:
        local = hy % TILE
        ty = hy // TILE
        if ty < 0 or ty >= rows or local < margin or local > TILE - margin:
            continue
        for tx in range(cols):
            if (tx, ty) not in present:
                continue
            tile = load(tx, ty)
            if tile is None:
                continue
            a, b, n = blend_seam_pair(
                np.rot90(tile[:local, :], 1),
                np.rot90(tile[local:, :], 1),
                feather=feather,
                jump=jump,
                smooth=smooth,
                chroma=chroma,
            )
            if n:
                out = tile.copy()
                out[:local, :] = np.ascontiguousarray(np.rot90(a, -1))
                out[local:, :] = np.ascontiguousarray(np.rot90(b, -1))
                work[(tx, ty)] = out
                hem_rows += n

    changed = 0
    for (tx, ty), out in sorted(work.items()):
        path = dest0 / f"{tx}_{ty}.webp"
        cur = _seams_load(path) if path.exists() else None
        if cur is not None and int(np.abs(cur.astype(np.int16) - out.astype(np.int16)).max()) <= 1:
            continue
        _atomic_save_webp(Image.fromarray(out, "RGB"), path, lossless=lossless_z0)
        changed += 1
    stamp_path.write_text(stamp)
    print(
        f"  seams done: changed={changed} edge_rows={edge_rows} hem_rows={hem_rows} "
        f"hems={len(hem_x)}x/{len(hem_y)}y",
        flush=True,
    )
    return present



# --- one house tone (see tone.py) -----------------------------------------

TONE_CONTEXT = 3  # tiles of context around a --tone-region


def tone_candidate_lines(grid: dict, districts: list[dict] | None) -> tuple[np.ndarray, np.ndarray]:
    """Grid-pixel x and y of every line a hard tone step can sit on.

    z0 tile edges, plus each district's canvas edges and restyle-window edges.
    """
    import tone as TN

    cols, rows = grid["cols"], grid["rows"]
    W, H = cols * TILE, rows * TILE
    xs = set(range(0, W + 1, TILE))
    ys = set(range(0, H + 1, TILE))
    ox, oy = grid["ox"], grid["oy"]
    for d in districts or []:
        dox, doy = d["origin"][0] - ox, d["origin"][1] - oy
        xs.update((dox, dox + int(d["width"])))
        ys.update((doy, doy + int(d["height"])))
        for t in d.get("windows") or []:
            xs.update((dox + t.x, dox + t.x + t.w))
            ys.update((doy + t.y, doy + t.y + t.h))
    xl = [x for x in xs if 0 <= x <= W]
    yl = [y for y in ys if 0 <= y <= H]
    # Tile edges must survive the merge (every rectangle sits inside one tile).
    def merged(vals: list[int], step: int, end: int) -> np.ndarray:
        keep = sorted(set(range(0, end + 1, step)))
        out = list(keep)
        for v in sorted(vals):
            if all(abs(v - k) >= TN.TONE_MERGE for k in out):
                out.append(v)
                out.sort()
        return np.array(sorted(out), np.int64)

    return merged(xl, TILE, W), merged(yl, TILE, H)


_TONE: dict = {}


def _tone_load(path: Path) -> np.ndarray | None:
    if not path.exists():
        return None
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB")).copy()


def _tone_cells(key: tuple[int, int]):
    import tone as TN

    tx, ty = key
    rest = _tone_load(_TONE["src0"] / f"{tx}_{ty}.webp")
    orig = _tone_load(_TONE["orig0"] / f"{tx}_{ty}.webp") if _TONE["orig0"] else None
    r = TN.cell_stats(rest) if rest is not None else None
    o = TN.cell_stats(orig) if orig is not None else None
    return tx, ty, r, o


def _tone_tile_fields(tx: int, ty: int):
    import tone as TN

    gx, gy = (tx - _TONE["bx"]) * TILE, (ty - _TONE["by"]) * TILE
    alpha = TN.sample_cells(_TONE["alpha"], gx, gy, TILE)
    wash = TN.sample_cells(_TONE["wash"], gx, gy, TILE)
    return alpha, wash


def _tone_measure(key: tuple[int, int]):
    import tone as TN

    tx, ty = key
    rest = _tone_load(_TONE["src0"] / f"{tx}_{ty}.webp")
    orig = _tone_load(_TONE["orig0"] / f"{tx}_{ty}.webp") if _TONE["orig0"] else None
    if rest is None or orig is None:
        return tx, ty, None
    alpha, wash = _tone_tile_fields(tx, ty)
    lab = TN.rgb_to_lab(rest)
    res = TN.apply_house_style(_TONE["style"], TN.rgb_to_lab(orig)) - lab
    fog = np.abs(rest.astype(np.int16) - np.array(VIEW_FOG, np.int16)).max(axis=2) <= 6
    valid = (alpha < 0.05) & (wash < 0.2) & ~fog
    return tx, ty, TN.tile_measure(res, lab, valid, tx * TILE, ty * TILE, _TONE["xl"], _TONE["yl"])


def _tone_apply(key: tuple[int, int]):
    import tone as TN

    tx, ty = key
    rest = _tone_load(_TONE["src0"] / f"{tx}_{ty}.webp")
    if rest is None:
        return tx, ty, False
    orig = _tone_load(_TONE["orig0"] / f"{tx}_{ty}.webp") if _TONE["orig0"] else None
    alpha, wash = _tone_tile_fields(tx, ty)
    gx, gy = (tx - _TONE["bx"]) * TILE, (ty - _TONE["by"]) * TILE
    cc = TN.TONE_CELL * 2
    corr = TN.sample_cells(_TONE["corr"], gx, gy, TILE, cell=cc)
    stren = TN.sample_cells(_TONE["stren"], gx, gy, TILE, cell=cc)
    step = TN.step_field(_TONE["segments"], tx * TILE, ty * TILE, TILE, TILE)
    total = step * (1.0 - alpha)[..., None] + corr * stren[..., None]
    fog_lab = TN.rgb_to_lab(np.array(VIEW_FOG, np.uint8))
    out = TN.tone_tile(rest, orig, _TONE["style"], total, np.ones_like(stren),
                       wash if orig is not None else np.zeros_like(wash), fog_lab=fog_lab)
    out_dir: Path = _TONE["out_dir"]
    path = out_dir / f"{tx}_{ty}.webp"
    cur = _tone_load(path)
    if cur is not None and int(np.abs(cur.astype(np.int16) - out.astype(np.int16)).max()) <= 1:
        return tx, ty, False
    _atomic_save_webp(Image.fromarray(out, "RGB"), path, lossless=_TONE["lossless"])
    return tx, ty, True


def tone_stamp(xl: np.ndarray, yl: np.ndarray) -> str:
    import tone as TN

    return (
        f"{TN.TONE_VERSION} cell={TN.TONE_CELL} sigma={TN.TONE_SIGMA} robust={TN.TONE_ROBUST} "
        f"band={TN.TONE_BAND} consist={TN.TONE_STEP_CONSIST} min={TN.TONE_STEP_MIN} gate={TN.TONE_STEP_GATE} "
        f"reach={TN.TONE_STEP_REACH} ramp={TN.TONE_STEP_RAMP} "
        f"wash={TN.TONE_WASH_RATIO}/{TN.TONE_WASH_DETAIL}/{TN.TONE_WASH_L} edge={TN.TONE_EDGE_IN}/{TN.TONE_EDGE_OUT} "
        f"deep={TN.TONE_WASH_DEEP} fogkeep={TN.TONE_FOG_KEEP} "
        f"lines={len(xl)}x{len(yl)}\n"
    )


def polish_tone(
    dest: Path,
    grid: dict,
    *,
    proc_dest: Path | None,
    districts: list[dict] | None = None,
    region: tuple[int, int, int, int] | None = None,
    out_dir: Path | None = None,
    lossless_z0: bool = True,
    workers: int = 8,
) -> set[tuple[int, int]]:
    """Give the whole restyle one house tone (free, idempotent from `.tone-src/`).

    Reads the tiles from before `--polish-seams` (`.seams-src/`, else the
    current z0) once into `.tone-src/`, so re-runs and parameter changes
    always start from the same source. `region` (tx0, ty0, tx1, ty1, end
    exclusive) limits the written tiles, and `out_dir` writes them
    elsewhere, for previews.
    """
    import multiprocessing as mp

    import tone as TN

    cols, rows = grid["cols"], grid["rows"]
    if proc_dest is None or not (proc_dest / "0").is_dir():
        raise SystemExit("--polish-tone needs the procedural tiles (the original the restyle was made from)")
    src = dest / ".tone-src"
    src0 = src / "0"
    if not src0.is_dir():
        seed = dest / ".seams-src" / "0"
        seed = seed if seed.is_dir() else dest / "0"
        print(f"  tone source: snapshot of {seed}", flush=True)
        tmp = src / "0.tmp"
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(seed, tmp)
        tmp.replace(src0)
    present = present_from_disk(src, cols, rows)
    if not present:
        return present
    xl, yl = tone_candidate_lines(grid, districts)
    stamp = tone_stamp(xl, yl)
    stamp_path = dest / ".tone-stamp"
    if region is None and out_dir is None and stamp_path.exists() and stamp_path.read_text() == stamp:
        print("  tone already applied", flush=True)
        return present_from_disk(dest, cols, rows)

    def compute(cache: dict | None) -> dict:
        t0 = time.time()
        bx = min(x for x, _ in present)
        by = min(y for _, y in present)
        bw = max(x for x, _ in present) - bx + 1
        bh = max(y for _, y in present) - by + 1
        C = TILE // TN.TONE_CELL
        R = np.zeros((bh * C, bw * C, 3), np.float32)
        O = np.zeros_like(R)
        ER = np.zeros((bh * C, bw * C), np.float32)
        EO = np.zeros_like(ER)
        okR = np.zeros(ER.shape, bool)
        okO = np.zeros(ER.shape, bool)
        _TONE.clear()
        _TONE.update(src0=src0, orig0=proc_dest / "0", bx=bx, by=by, xl=xl, yl=yl, lossless=lossless_z0)
        ctx = mp.get_context("fork")
        with ctx.Pool(workers) as pool:
            for tx, ty, r, o in pool.imap_unordered(_tone_cells, sorted(present), chunksize=8):
                s = np.s_[(ty - by) * C : (ty - by + 1) * C, (tx - bx) * C : (tx - bx + 1) * C]
                if r is not None:
                    R[s], ER[s], okR[s] = r[0], r[1], True
                if o is not None:
                    O[s], EO[s], okO[s] = o[0], o[1], True
        print(f"  tone cells {R.shape[1]}x{R.shape[0]} in {time.time() - t0:.0f}s", flush=True)

        alpha = np.zeros(ER.shape, np.float32)
        deep = np.ones(ER.shape, np.float32)
        dist_path = dest / ".vignette-dist.npz"
        if dist_path.exists():
            field = np.load(dist_path)["field"]
            rep = VIGNETTE_SCALE // TN.TONE_CELL
            f = np.repeat(np.repeat(field, rep, 0), rep, 1)[by * C : (by + bh) * C, bx * C : (bx + bw) * C]
            # Taper well before the vignette starts: the fogline and flats passes
            # fade ground further in than the vignette field says, and a
            # correction (or a wash refill) on fog-blended pixels draws a dark,
            # blocky rim along the edge.
            alpha[: f.shape[0], : f.shape[1]] = TN.smoothstep(f, -TN.TONE_EDGE_IN, TN.TONE_EDGE_OUT)
            # Fog-coloured holes near the edge are the fogline / flats passes at
            # work; only refill washes deep inside the city.
            deep[: f.shape[0], : f.shape[1]] = 1.0 - TN.smoothstep(f, *TN.TONE_WASH_DEEP)
        fog_lab = TN.rgb_to_lab(np.array(VIEW_FOG, np.uint8))
        pad_lab = TN.rgb_to_lab(np.array((0x1A, 0x1E, 0x1C), np.uint8))
        fogR = np.linalg.norm(R - fog_lab, axis=-1) < 3.0
        padO = np.linalg.norm(O - pad_lab, axis=-1) < 6.0
        valid = okR & okO & (alpha < 0.02) & ~fogR & ~padO
        style = TN.fit_house_style(O[valid], R[valid])
        TO = np.empty_like(O)
        for y in range(0, O.shape[0], 256):
            TO[y : y + 256] = TN.apply_house_style(style, O[y : y + 256])
        wash = TN.wash_weight(R, ER, EO, okR & okO & (alpha < 0.5)) * (1.0 - alpha) * deep
        del deep
        del ER, EO
        weights = valid * (1.0 - np.clip(wash * 4.0, 0.0, 1.0))
        _TONE.update(style=style, alpha=alpha, wash=wash)
        print(f"  tone house style fitted on {int(valid.sum())} cells, wash cells {int((wash > 0.5).sum())}", flush=True)

        if region is not None:
            rx0, ry0, rx1, ry1 = region
            work = {(x, y) for x, y in present if rx0 <= x < rx1 and ry0 <= y < ry1}
            m = TONE_CONTEXT
            measure = {(x, y) for x, y in present if rx0 - m <= x < rx1 + m and ry0 - m <= y < ry1 + m}
            # Crop the cell fields to the context so a preview is quick.
            cx0, cy0 = max(bx, rx0 - m), max(by, ry0 - m)
            cx1, cy1 = min(bx + bw, rx1 + m), min(by + bh, ry1 + m)
            cs = np.s_[(cy0 - by) * C : (cy1 - by) * C, (cx0 - bx) * C : (cx1 - bx) * C]
            R, O, TO = R[cs], O[cs], TO[cs]
            alpha, wash, weights = alpha[cs], wash[cs], weights[cs]
            bx, by = cx0, cy0
            _TONE.update(bx=bx, by=by, alpha=alpha, wash=wash)
        else:
            work = set(present)
            measure = set(present)

        segs: list[tuple] = []
        edges: dict = {}
        measured: dict = {}
        if cache is not None and cache["measure"].exists():
            with open(cache["measure"], "rb") as fh:
                while True:
                    try:
                        key, st = pickle.load(fh)
                    except EOFError:
                        break
                    except Exception:
                        break  # a torn last record from an interrupted run
                    measured[key] = st
            print(f"  tone measure: resuming, {len(measured)} tiles cached", flush=True)
        todo = sorted(k for k in measure if k not in measured)
        fh = open(cache["measure"], "ab") if cache is not None else None
        try:
            with ctx.Pool(workers) as pool:
                for n, (tx, ty, st) in enumerate(pool.imap_unordered(_tone_measure, todo, chunksize=4), 1):
                    measured[(tx, ty)] = st
                    if fh is not None:
                        pickle.dump(((tx, ty), st), fh)
                        if n % 50 == 0:
                            fh.flush()
                    if n % 500 == 0:
                        print(f"  tone measure {n}/{len(todo)}, {time.time() - t0:.0f}s", flush=True)
        finally:
            if fh is not None:
                fh.close()
        for (tx, ty), st in sorted(measured.items()):
                if st is None:
                    continue
                for axis, k, j, step in st["steps"]:
                    lines, other = (xl, yl) if axis == "x" else (yl, xl)
                    if np.any(step):
                        segs.append((0 if axis == "x" else 1, lines[k], other[j], other[j + 1], *step))
                edges[(tx, ty)] = st["edges"]
        # Tile-edge lines: pair the neighbour's band with this tile's band.
        for (tx, ty), e in edges.items():
            for axis, nb in (("x", (tx - 1, ty)), ("y", (tx, ty - 1))):
                other = edges.get(nb)
                if other is None or (axis, "lo") not in e or (axis, "hi") not in other:
                    continue
                (rm, rok), (lm, lok) = e[(axis, "lo")], other[(axis, "hi")]
                c, lines, a0 = (tx * TILE, yl, ty * TILE) if axis == "x" else (ty * TILE, xl, tx * TILE)
                j0, j1 = int(np.searchsorted(lines, a0)), int(np.searchsorted(lines, a0 + TILE))
                for j in range(j0, j1):
                    sa, sb = lines[j] - a0, lines[j + 1] - a0
                    step = TN.step_stat(lm[sa:sb], rm[sa:sb], lok[sa:sb] & rok[sa:sb])
                    if np.any(step):
                        segs.append((0 if axis == "x" else 1, c, lines[j], lines[j + 1], *step))
        segments = np.array(segs, np.float32).reshape(-1, 7)
        print(f"  tone steps: {len(segments)} seam segments to cancel, {time.time() - t0:.0f}s", flush=True)

        # Cell-level step field, then the smooth correction on what is left.
        TO -= R
        del R, O
        band = 64
        for y in range(0, TO.shape[0], band):
            hh = min(band, TO.shape[0] - y)
            sf = TN.step_field(segments, bx * TILE, by * TILE + y * TN.TONE_CELL, TO.shape[1], hh, TN.TONE_CELL)
            TO[y : y + hh] -= sf * (1.0 - alpha[y : y + hh])[..., None]
        # The smooth field is low-pass anyway: solve it on 2x2-pooled cells.
        h2, w2 = TO.shape[0] // 2, TO.shape[1] // 2
        w4 = weights[: h2 * 2, : w2 * 2].reshape(h2, 2, w2, 2)
        wsum = w4.sum((1, 3))
        res2 = (TO[: h2 * 2, : w2 * 2] * weights[: h2 * 2, : w2 * 2, None]).reshape(h2, 2, w2, 2, 3).sum((1, 3))
        res2 /= np.maximum(wsum, 1e-6)[..., None]
        del TO
        corr, stren = TN.correction_field(res2, wsum / 4.0, sigma=TN.TONE_SIGMA / 2.0)
        stren *= 1.0 - alpha[: h2 * 2, : w2 * 2].reshape(h2, 2, w2, 2).mean((1, 3))
        _TONE.update(corr=corr, stren=stren, segments=segments)
        print(f"  tone field done, {time.time() - t0:.0f}s", flush=True)
        return {"style": style, "alpha": alpha, "wash": wash, "corr": corr, "stren": stren,
                "segments": segments, "bx": bx, "by": by, "work": work}

    resumable = region is None and out_dir is None
    cache = None
    if resumable:
        cache = {"stamp": dest / ".tone-cache-stamp", "fields": dest / ".tone-fields.npz",
                 "measure": dest / ".tone-measure.pkl", "progress": dest / ".tone-progress.txt"}
        if not cache["stamp"].exists() or cache["stamp"].read_text() != stamp:
            for k in ("fields", "measure", "progress"):
                if cache[k].exists():
                    cache[k].unlink()
            cache["stamp"].write_text(stamp)
    ctx = mp.get_context("fork")
    t0 = time.time()
    if cache is not None and cache["fields"].exists():
        z = np.load(cache["fields"], allow_pickle=False)
        f = {k: z[k] for k in ("alpha", "wash", "corr", "stren", "segments")}
        f["style"] = {"coef": z["coef"], "lut": z["lut"]}
        f["bx"], f["by"] = int(z["bx"]), int(z["by"])
        f["work"] = set(present)
        print("  tone fields: loaded from cache", flush=True)
    else:
        f = compute(cache)
        if cache is not None:
            tmpf = cache["fields"].with_name(".tone-fields.tmp.npz")
            np.savez(tmpf, alpha=f["alpha"], wash=f["wash"], corr=f["corr"], stren=f["stren"],
                     segments=f["segments"], coef=f["style"]["coef"], lut=f["style"]["lut"],
                     bx=f["bx"], by=f["by"])
            tmpf.replace(cache["fields"])
    _TONE.clear()
    _TONE.update(src0=src0, orig0=proc_dest / "0", bx=f["bx"], by=f["by"], xl=xl, yl=yl,
                 lossless=lossless_z0, style=f["style"], alpha=f["alpha"], wash=f["wash"],
                 corr=f["corr"], stren=f["stren"], segments=f["segments"])
    work = f["work"]

    target = out_dir if out_dir is not None else dest / "0"
    target.mkdir(parents=True, exist_ok=True)
    _TONE["out_dir"] = target
    changed = 0
    done: set[tuple[int, int]] = set()
    if cache is not None and cache["progress"].exists():
        for line in cache["progress"].read_text().split("\n"):
            parts = line.split()
            if len(parts) == 2:
                done.add((int(parts[0]), int(parts[1])))
        print(f"  tone apply: resuming, {len(done & work)} tiles already done", flush=True)
    todo = sorted(work - done)
    prog = open(cache["progress"], "a") if cache is not None else None
    try:
        with ctx.Pool(workers) as pool:
            for n, (tx, ty, did) in enumerate(pool.imap_unordered(_tone_apply, todo, chunksize=1), 1):
                changed += int(did)
                if prog is not None:
                    prog.write(f"{tx} {ty}\n")
                    prog.flush()
                if n % 500 == 0:
                    print(f"  tone {n}/{len(todo)} tiles, {changed} changed, {time.time() - t0:.0f}s", flush=True)
    finally:
        if prog is not None:
            prog.close()
    if cache is not None:
        stamp_path.write_text(stamp)
        for k in ("fields", "measure", "progress", "stamp"):
            if cache[k].exists():
                cache[k].unlink()
    print(f"  tone done: {changed}/{len(work)} tiles changed, {time.time() - t0:.0f}s", flush=True)
    _TONE.clear()
    return present_from_disk(dest, cols, rows)


# --- gaps: find and fill holes in the city (see gaps.py) -------------------

_GAPS: dict = {}


def _gaps_load(path: Path) -> np.ndarray | None:
    if not path.exists():
        return None
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB")).copy()


def _gaps_read_crop(z0: Path | list[Path], box: tuple[int, int, int, int], fill=VIEW_FOG) -> np.ndarray:
    """Grid-pixel box (x0, y0, x1, y1) from z0 tiles; the first dir that has a tile wins."""
    dirs = z0 if isinstance(z0, list) else [z0]
    x0, y0, x1, y1 = box
    out = np.empty((y1 - y0, x1 - x0, 3), np.uint8)
    out[:] = fill
    for ty in range(max(0, y0 // TILE), (y1 - 1) // TILE + 1):
        for tx in range(max(0, x0 // TILE), (x1 - 1) // TILE + 1):
            arr = None
            for d in dirs:
                arr = _gaps_load(d / f"{tx}_{ty}.webp")
                if arr is not None:
                    break
            if arr is None:
                continue
            ix0, iy0 = max(x0, tx * TILE), max(y0, ty * TILE)
            ix1, iy1 = min(x1, (tx + 1) * TILE), min(y1, (ty + 1) * TILE)
            out[iy0 - y0 : iy1 - y0, ix0 - x0 : ix1 - x0] = arr[iy0 - ty * TILE : iy1 - ty * TILE, ix0 - tx * TILE : ix1 - tx * TILE]
    return out


def _gaps_window_crops(box: tuple[int, int, int, int], region_px: np.ndarray) -> list[tuple[np.ndarray, np.ndarray, dict]]:
    """Each restyle window's own pixels over a grid-pixel box, skipping windows that
    are an unrestyled copy of the procedural render (fog windows) or miss the region."""
    import gaps as GP

    grid = _GAPS["grid"]
    x0, y0, x1, y1 = box
    W, H = x1 - x0, y1 - y0
    out = []
    for di, d in enumerate(_GAPS["districts"]):
        lx = grid["ox"] + x0 - d["origin"][0]
        ly = grid["oy"] + y0 - d["origin"][1]
        if lx + W <= 0 or ly + H <= 0 or lx >= d["width"] or ly >= d["height"]:
            continue
        proc = None
        for t in d["windows"]:
            if t.x + t.w <= lx or t.x >= lx + W or t.y + t.h <= ly or t.y >= ly + H:
                continue
            path = d["tiles"] / f"{t.x}_{t.y}.png"
            if not path.exists():
                continue
            iy0, iy1 = max(t.y, ly), min(t.y + t.h, ly + H)
            ix0, ix1 = max(t.x, lx), min(t.x + t.w, lx + W)
            cov = np.zeros((H, W), bool)
            cov[iy0 - ly : iy1 - ly, ix0 - lx : ix1 - lx] = True
            if int((cov & region_px).sum()) < 256 * GP.GAP_MIN_CELLS // 2:
                continue
            with Image.open(path) as im:
                im = im.convert("RGB")
                if im.size != (t.w, t.h):
                    im = im.resize((t.w, t.h), Image.Resampling.NEAREST)
                a = np.asarray(im)
            rgb = np.zeros((H, W, 3), np.uint8)
            rgb[iy0 - ly : iy1 - ly, ix0 - lx : ix1 - lx] = a[iy0 - t.y : iy1 - t.y, ix0 - t.x : ix1 - t.x]
            if proc is None:
                proc = _gaps_district_proc(d, lx, ly, W, H)
            if proc is not None:
                diff = np.abs(rgb.astype(np.int16) - proc.astype(np.int16)).mean(axis=2)[cov]
                if float(diff.mean()) < GP.GAP_WIN_COPY:
                    continue
            out.append((rgb, cov, {"district": Path(d["chunks"]).name, "window": [t.x, t.y]}))
    return out


def _gaps_district_proc(d: dict, lx: int, ly: int, w: int, h: int) -> np.ndarray | None:
    """The district's own procedural render over a local box (from its chunks)."""
    chunk = int(d["meta"].get("chunk") or 2048)
    cdir = d["chunks"] / "chunks"
    if not cdir.is_dir():
        return None
    out = np.empty((h, w, 3), np.uint8)
    out[:] = VIEW_FOG
    for r in range(max(0, ly // chunk), (ly + h - 1) // chunk + 1):
        for c in range(max(0, lx // chunk), (lx + w - 1) // chunk + 1):
            p = cdir / f"{c}_{r}.png"
            if not p.exists():
                continue
            with Image.open(p) as im:
                a = np.asarray(im.convert("RGB"))
            iy0, iy1 = max(r * chunk, ly), min(r * chunk + a.shape[0], ly + h)
            ix0, ix1 = max(c * chunk, lx), min(c * chunk + a.shape[1], lx + w)
            if iy1 <= iy0 or ix1 <= ix0:
                continue
            out[iy0 - ly : iy1 - ly, ix0 - lx : ix1 - lx] = a[iy0 - r * chunk : iy1 - r * chunk, ix0 - c * chunk : ix1 - c * chunk]
    return out


def _gaps_src_tile(name: str) -> np.ndarray | None:
    """A source z0 tile: the backed-up original when there is one, so a re-run starts over."""
    for d in _GAPS["src"]:
        arr = _gaps_load(d / name)
        if arr is not None:
            return arr
    return None


def _gaps_cells(key: tuple[int, int]):
    import gaps as GP

    tx, ty = key
    cur = _gaps_src_tile(f"{tx}_{ty}.webp")
    orig = _gaps_load(_GAPS["orig0"] / f"{tx}_{ty}.webp")
    if cur is None or orig is None:
        return tx, ty, None
    lab, det = GP.cell_detail(cur)
    olab, _ = GP.cell_detail(orig)
    st = GP.cell_structure(cur, orig)
    return tx, ty, (lab, det, olab, st)


def _gaps_fill_one(gap):
    import gaps as GP

    C = GP.GAP_CELL
    x0, y0, x1, y1 = (v * C for v in gap.bbox)
    M = GP.GAP_MARGIN
    W, H = _GAPS["W"], _GAPS["H"]
    box = (max(0, x0 - M), max(0, y0 - M), min(W, x1 + M), min(H, y1 + M))
    cs = np.s_[box[1] // C : box[3] // C, box[0] // C : box[2] // C]
    lab = _GAPS["lab"][cs]
    region = lab == gap.id
    score = _GAPS["score"][cs]
    good = (score < 0.15) & (_GAPS["city"][cs] > 0.5) & (lab == 0)
    cur = _gaps_read_crop(_GAPS["src"], box)
    orig = _gaps_read_crop(_GAPS["orig0"], box, fill=(0x1A, 0x1E, 0x1C))
    region_px = np.repeat(np.repeat(region, C, 0), C, 1)
    wins = _gaps_window_crops(box, region_px)
    fill, alpha, used = GP.fill_region(cur, orig, _GAPS["style"], region, score, good,
                                       [(w[0], w[1]) for w in wins], pure=True)
    a = alpha[..., None]
    blended = np.clip(np.rint(cur * (1.0 - a) + fill * a), 0, 255).astype(np.uint8)
    # Do not make things worse: re-score the filled crop and drop a fill that does not help.
    ok, before, after = GP.verify_fill(cur, blended, orig, _GAPS["city"][cs], region)
    if not ok:
        return gap.id, box, fill, np.zeros(alpha.shape, np.float16), [
            {"source": "kept", "score_before": round(before, 3), "score_after": round(after, 3)}]
    used = [dict(u, score_before=round(before, 3), score_after=round(after, 3)) for u in used]
    for u in used:
        if u["source"] == "window":
            u.update(wins[u.pop("index")][2])
    return gap.id, box, fill, alpha.astype(np.float16), used


def gaps_fields(src: list[Path], proc_dest: Path, grid: dict, present: set[tuple[int, int]], workers: int = 8) -> dict:
    """Global 16-px cell fields of the current restyle and the original (one pass)."""
    import multiprocessing as mp

    import gaps as GP

    C = TILE // GP.GAP_CELL
    H, W = grid["rows"] * C, grid["cols"] * C
    F = {k: np.zeros((H, W, 3), np.float32) for k in ("cl", "ol")}
    F.update({k: np.zeros((H, W), np.float32) for k in ("cd", "ncc", "sc", "so", "ec", "eo", "po")})
    F["ok"] = np.zeros((H, W), bool)
    _GAPS.update(src=src, orig0=proc_dest / "0")
    with mp.get_context("fork").Pool(workers) as pool:
        for tx, ty, r in pool.imap_unordered(_gaps_cells, sorted(present), chunksize=8):
            if r is None:
                continue
            s = np.s_[ty * C : (ty + 1) * C, tx * C : (tx + 1) * C]
            F["cl"][s], F["cd"][s], F["ol"][s] = r[0], r[1], r[2]
            for k, v in r[3].items():
                F[k][s] = v
            F["ok"][s] = True
    return F


def gaps_city(F: dict, dest: Path) -> np.ndarray:
    """0..1 city weight: original content, not its edge fog, deep inside the restyle coverage."""
    import gaps as GP
    import tone as TN

    pad = TN.rgb_to_lab(np.array((0x1A, 0x1E, 0x1C), np.uint8))
    fog = TN.rgb_to_lab(np.array(VIEW_FOG, np.uint8))
    ol = F["ol"]
    m = (F["ok"] & (np.linalg.norm(ol - pad, axis=-1) > 8) & (np.linalg.norm(ol - fog, axis=-1) > 4)).astype(np.float32)
    dist_path = dest / ".vignette-dist.npz"
    if dist_path.exists():
        f = np.load(dist_path)["field"]
        rep = VIGNETTE_SCALE // GP.GAP_CELL
        if rep > 1:
            f = np.repeat(np.repeat(f, rep, 0), rep, 1)
        deep = np.zeros_like(m)
        h, w = min(f.shape[0], m.shape[0]), min(f.shape[1], m.shape[1])
        deep[:h, :w] = 1.0 - TN.smoothstep(f[:h, :w], *GP.GAP_DEEP)
        m = m * deep
    return m


def detect_gaps(F: dict, city: np.ndarray, grid: dict, districts: list[dict] | None) -> dict:
    import gaps as GP

    score, void = GP.gap_score(F["cl"], F["cd"], city, F)
    xl, yl = tone_candidate_lines(grid, districts)
    found, lab = GP.find_gaps(score, void, xl, yl)
    blurry = GP.blurry_tiles(F["ec"], F["eo"], city)
    return {"score": score, "void": void, "lab": lab, "gaps": found, "blurry": blurry}


def gaps_sheet(rows: list[dict], cols: list[list[Path]], out: Path, *, n: int = 40, cell: int = 300,
               per_row: int = 4, titles: list[str] | None = None) -> None:
    """Contact sheet of the worst `n` gaps: one crop per source column (original, before, after)."""
    from PIL import ImageDraw

    rows = rows[:n]
    k = len(cols)
    R = max(1, (len(rows) + per_row - 1) // per_row)
    im = Image.new("RGB", (per_row * (k * cell + 12), R * (cell + 22)), (30, 30, 30))
    draw = ImageDraw.Draw(im)
    for i, g in enumerate(rows):
        x0, y0, x1, y1 = g["px_box"]
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        w = min(max(x1 - x0 + 192, 384), 1400)
        h = min(max(y1 - y0 + 192, 384), 1400)
        box = (max(0, cx - w // 2), max(0, cy - h // 2), max(0, cx - w // 2) + w, max(0, cy - h // 2) + h)
        X, Y = (i % per_row) * (k * cell + 12), (i // per_row) * (cell + 22)
        for j, src in enumerate(cols):
            c = Image.fromarray(_gaps_read_crop(src, box))
            c.thumbnail((cell, cell))
            im.paste(c, (X + j * cell, Y + 20))
        src_txt = "+".join(sorted({u["source"] for u in g.get("used", [])})) or "-"
        draw.text((X + 2, Y + 4), f"#{i + 1} {g['kind']} {g['area_px'] // 1000}k px t{g['tiles'][0]} {src_txt}", fill=(255, 255, 0))
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, quality=88)


def rebuild_parents(dest: Path, out: Path, changed: set[tuple[int, int]], grid: dict,
                    present0: set[tuple[int, int]], pad_rgb=VIEW_FOG) -> int:
    """Rebuild only the pyramid tiles above `changed` z0 tiles, exactly as
    `_pyramid_from_z0` would. Children are read from `out` first, then `dest`;
    parents are written to `out`. Returns the number of tiles written."""
    cols, rows = grid["cols"], grid["rows"]
    prev_present = set(present0)
    dirty = set(changed)
    written = 0
    z = 1
    z_cols, z_rows = cols, rows
    while z_cols > 1 or z_rows > 1:
        z_cols, z_rows = (z_cols + 1) // 2, (z_rows + 1) // 2
        parents = {(x // 2, y // 2) for x, y in dirty}
        level_present = {(x // 2, y // 2) for x, y in prev_present}
        for tx, ty in sorted(parents):
            big = Image.new("RGB", (TILE * 2, TILE * 2), pad_rgb)
            any_child = False
            for dy in range(2):
                for dx in range(2):
                    cx, cy = tx * 2 + dx, ty * 2 + dy
                    if (cx, cy) not in prev_present:
                        continue
                    for base in (out, dest):
                        child = base / str(z - 1) / f"{cx}_{cy}.webp"
                        if child.exists():
                            with Image.open(child) as im:
                                big.paste(im.convert("RGB"), (dx * TILE, dy * TILE))
                            any_child = True
                            break
            if not any_child:
                continue
            small = big.resize((TILE, TILE), Image.Resampling.LANCZOS)
            if _almost_empty(np.asarray(small), pad_rgb):
                continue
            _atomic_save_webp(small, out / str(z) / f"{tx}_{ty}.webp", lossless=False)
            written += 1
        prev_present = level_present
        dirty = parents
        z += 1
    return written


def polish_gaps(
    dest: Path,
    grid: dict,
    *,
    proc_dest: Path | None,
    districts: list[dict] | None = None,
    stage: Path | None = None,
    report: Path | None = None,
    backup: Path | None = None,
    workers: int = 8,
) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    """Find gaps in the city and fill them for free (see gaps.py).

    Writes changed z0 tiles to `stage/<restyle_dir>/0` when `stage` is set
    (the live map is not touched), else in place after copying the touched
    tiles to `backup`. `report` gets gaps.json and a contact sheet (original,
    before, after). Returns (present, changed).
    """
    import multiprocessing as mp

    import gaps as GP
    import tone as TN

    t0 = time.time()
    if proc_dest is None or not (proc_dest / "0").is_dir():
        raise SystemExit("--polish-gaps needs the procedural tiles (the original the restyle was made from)")
    cols, rows = grid["cols"], grid["rows"]
    present = present_from_disk(dest, cols, rows)
    # Re-runs start from the backed-up originals of tiles an earlier run changed.
    src = ([backup / "0"] if backup is not None and (backup / "0").is_dir() else []) + [dest / "0"]
    F = gaps_fields(src, proc_dest, grid, present, workers)
    city = gaps_city(F, dest)
    det = detect_gaps(F, city, grid, districts)
    found = det["gaps"]
    print(f"  gaps: {len(found)} regions, {sum(g.cells for g in found)} cells, "
          f"{len(det['blurry'])} blurry tiles, {time.time() - t0:.0f}s", flush=True)
    v = (city > 0.5) & (det["score"] < 0.1) & F["ok"]
    style = TN.fit_house_style(F["ol"][v], F["cl"][v])
    _GAPS.update(
        grid=grid, districts=districts or [], src=src, orig0=proc_dest / "0", style=style,
        lab=det["lab"], score=det["score"], city=city, H=rows * TILE, W=cols * TILE,
    )
    del F
    out0 = (stage / dest.name / "0") if stage is not None else dest / "0"
    out0.mkdir(parents=True, exist_ok=True)
    tiles: dict[tuple[int, int], np.ndarray] = {}
    used_by: dict[int, list[dict]] = {}
    ctx = mp.get_context("fork")
    with ctx.Pool(workers) as pool:
        for n, (gid, box, fill, alpha, used) in enumerate(pool.imap_unordered(_gaps_fill_one, found, chunksize=1), 1):
            used_by[gid] = used
            _blend_into_tiles(tiles, present, box, fill, alpha)
            if n % 50 == 0:
                print(f"  gaps filled {n}/{len(found)}, {len(tiles)} tiles, {time.time() - t0:.0f}s", flush=True)
    changed = _write_changed_tiles(tiles, dest, out0, backup if stage is None else None)
    print(f"  gaps: {len(changed)} z0 tiles changed, {time.time() - t0:.0f}s", flush=True)
    if report is not None:
        report.mkdir(parents=True, exist_ok=True)
        rows_out = []
        for g in found:
            r = g.row()
            r["used"] = used_by.get(g.id, [])
            rows_out.append(r)
        (report / "gaps.json").write_text(json.dumps(
            {"version": GP.GAP_VERSION, "gaps": rows_out, "blurry_tiles": det["blurry"],
             "changed": sorted([list(t) for t in changed])}, indent=1) + "\n")
        before = [backup / "0"] if (backup is not None and (backup / "0").is_dir()) else []
        gaps_sheet(rows_out, [[proc_dest / "0"], before + [dest / "0"], [out0, dest / "0"]], report / "sheet.jpg")
    _GAPS.clear()
    return present, changed


# --- redraw: blend a paid redraw of chosen windows (see redraw.py) ---------


def _redraw_style(dest: Path, proc0: Path, present: set[tuple[int, int]], cache: Path) -> dict:
    """House style (original Lab -> restyle Lab) fit on a sample of intact cells; cached."""
    import gaps as GP
    import tone as TN

    if cache.exists():
        return pickle.loads(cache.read_bytes())
    keys = sorted(present)
    src, dst = [], []
    for tx, ty in keys[:: max(1, len(keys) // 300)]:
        cur = _gaps_load(dest / "0" / f"{tx}_{ty}.webp")
        orig = _gaps_load(proc0 / f"{tx}_{ty}.webp")
        if cur is None or orig is None:
            continue
        st = GP.cell_structure(cur, orig)
        ok = st["ncc"] > 0.4
        cl, _ = GP.cell_detail(cur)
        ol, _ = GP.cell_detail(orig)
        src.append(ol[ok])
        dst.append(cl[ok])
    style = TN.fit_house_style(np.concatenate(src), np.concatenate(dst))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(pickle.dumps(style))
    return style


def _redraw_one(target: dict):
    """Fill one merged target from the redraw windows. Returns (box, fill, alpha, row)."""
    import gaps as GP
    import redraw as RD

    C = GP.GAP_CELL
    M = GP.GAP_MARGIN
    W, H = _GAPS["W"], _GAPS["H"]
    x0, y0, x1, y1 = target["box"]
    box = (max(0, (x0 - M) // C * C), max(0, (y0 - M) // C * C),
           min(W, -(-(x1 + M) // C) * C), min(H, -(-(y1 + M) // C) * C))
    row = {"px_box": list(target["box"]), "kind": "+".join(RD.category(c) for c in target["cats"]),
           "cats": target["cats"], "parts": len(target["parts"]),
           "area_px": (x1 - x0) * (y1 - y0), "tiles": [[x0 // TILE, y0 // TILE]]}
    cur = _gaps_read_crop(_GAPS["src"], box)
    orig = _gaps_read_crop(_GAPS["orig0"], box, fill=(0x1A, 0x1E, 0x1C))
    city = RD.city_cells(orig)
    lab, det = GP.cell_detail(cur)
    score, _ = GP.gap_score(lab, det, city, GP.cell_structure(cur, orig))
    region = RD.target_region(target["parts"], box, score, city)
    none = np.zeros(cur.shape[:2], np.float16)
    if int(region.sum()) < RD.REDRAW_MIN_CELLS:
        return box, cur, none, dict(row, result="nothing", cells=int(region.sum()))
    region_px = np.repeat(np.repeat(region, C, 0), C, 1)
    wins = _gaps_window_crops(box, region_px)
    ch, cw = region.shape
    cover = np.zeros_like(region)
    for rgb, cov, _meta in wins:
        covered = cov[: ch * C, : cw * C].reshape(ch, C, cw, C).mean((1, 3)) > 0.99
        cover |= GP.window_good(GP.cell_structure(rgb, orig), covered)
    todo = region & cover
    if int(todo.sum()) < RD.REDRAW_MIN_CELLS:
        return box, cur, none, dict(row, result="no-window", cells=int(region.sum()), windows=len(wins))
    good = (score < 0.15) & (city > 0.5) & ~region
    fill, alpha, used = GP.fill_region(cur, orig, _GAPS["style"], todo, score, good,
                                       [(w[0], w[1]) for w in wins], pure=True)
    a = alpha[..., None]
    blended = np.clip(np.rint(cur * (1.0 - a) + fill * a), 0, 255).astype(np.uint8)
    before = GP.region_score(cur, orig, city, todo)
    after = GP.region_score(blended, orig, city, todo)
    row.update(cells=int(region.sum()), filled=int(todo.sum()), windows=len(wins),
               score_before=round(before, 3), score_after=round(after, 3))
    for u in used:
        if u["source"] == "window":
            u.update(wins[u.pop("index")][2])
    row["used"] = used
    if not RD.keep_fill(target["cats"], before, after):
        return box, cur, none, dict(row, result="dropped")
    return box, fill, alpha.astype(np.float16), dict(row, result="kept")


def _blend_into_tiles(tiles: dict, present: set[tuple[int, int]], box, fill: np.ndarray, alpha: np.ndarray) -> None:
    """Alpha-blend a crop fill into the z0 tiles it covers (loaded lazily from _GAPS['src'])."""
    x0, y0, x1, y1 = box
    a = alpha.astype(np.float32)
    for ty in range(y0 // TILE, (y1 - 1) // TILE + 1):
        for tx in range(x0 // TILE, (x1 - 1) // TILE + 1):
            if (tx, ty) not in present:
                continue
            ix0, iy0 = max(x0, tx * TILE), max(y0, ty * TILE)
            ix1, iy1 = min(x1, (tx + 1) * TILE), min(y1, (ty + 1) * TILE)
            sa = a[iy0 - y0 : iy1 - y0, ix0 - x0 : ix1 - x0]
            if float(sa.max()) <= 2e-3:
                continue
            if (tx, ty) not in tiles:
                tiles[(tx, ty)] = _gaps_src_tile(f"{tx}_{ty}.webp").astype(np.float32)
            t = tiles[(tx, ty)]
            ts = np.s_[iy0 - ty * TILE : iy1 - ty * TILE, ix0 - tx * TILE : ix1 - tx * TILE]
            sf = fill[iy0 - y0 : iy1 - y0, ix0 - x0 : ix1 - x0].astype(np.float32)
            t[ts] = t[ts] * (1.0 - sa[..., None]) + sf * sa[..., None]


def _write_changed_tiles(tiles: dict, dest: Path, out0: Path, backup: Path | None) -> set[tuple[int, int]]:
    """Save tiles that differ from what is on disk; copy each original to backup/0 first.
    A tile a previous run changed but this one does not gets its original back."""
    if backup is not None:
        (backup / "0").mkdir(parents=True, exist_ok=True)
        for p in sorted((backup / "0").glob("*.webp")):
            tx, ty = (int(v) for v in p.stem.split("_"))
            if (tx, ty) not in tiles:
                tiles[(tx, ty)] = _gaps_load(p).astype(np.float32)
    changed: set[tuple[int, int]] = set()
    for (tx, ty), arr in sorted(tiles.items()):
        out = np.clip(np.rint(arr), 0, 255).astype(np.uint8)
        src = dest / "0" / f"{tx}_{ty}.webp"
        cur = _gaps_load(src)
        if cur is not None and int(np.abs(cur.astype(np.int16) - out.astype(np.int16)).max()) <= 1:
            continue
        if backup is not None and not (backup / "0" / src.name).exists():
            shutil.copy2(src, backup / "0" / src.name)
        _atomic_save_webp(Image.fromarray(out, "RGB"), out0 / src.name, lossless=True)
        changed.add((tx, ty))
    return changed


def polish_redraw(
    dest: Path,
    grid: dict,
    *,
    proc_dest: Path | None,
    targets: Path,
    sources: list[dict],
    backup: Path,
    report: Path | None = None,
    workers: int = 8,
) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    """Blend a paid redraw (fresh restyle windows in `sources`) into the target regions.

    `targets` is json {category: [[x0, y0, x1, y1] grid px, ...]} (see redraw.py).
    Re-runs start from the z0 copies in `backup/0`. `report` gets redraw.json
    (each target kept, dropped or without a source) and a contact sheet.
    Returns (present, changed).
    """
    import multiprocessing as mp

    import redraw as RD

    t0 = time.time()
    if proc_dest is None or not (proc_dest / "0").is_dir():
        raise SystemExit("--polish-redraw needs the procedural tiles (the original the restyle was made from)")
    cols, rows = grid["cols"], grid["rows"]
    present = present_from_disk(dest, cols, rows)
    backup0 = backup / "0"
    backup0.mkdir(parents=True, exist_ok=True)
    src = [backup0, dest / "0"]
    raw = json.loads(targets.read_text())
    items = [(cat, tuple(b)) for cat, boxes in sorted(raw.items()) for b in boxes]
    merged = RD.merge_boxes(items)
    print(f"  redraw: {len(items)} targets → {len(merged)} merged regions", flush=True)
    style = _redraw_style(dest, proc_dest / "0", present, backup / "work" / "style.pkl")
    _GAPS.update(grid=grid, districts=sources, src=src, orig0=proc_dest / "0", style=style,
                 H=rows * TILE, W=cols * TILE)
    tiles: dict[tuple[int, int], np.ndarray] = {}
    rows_out = []
    ctx = mp.get_context("fork")
    with ctx.Pool(workers) as pool:
        for n, (box, fill, alpha, row) in enumerate(pool.imap_unordered(_redraw_one, merged, chunksize=1), 1):
            rows_out.append(row)
            if row["result"] == "kept":
                _blend_into_tiles(tiles, present, box, fill, alpha)
            if n % 25 == 0:
                print(f"  redraw {n}/{len(merged)}, {len(tiles)} tiles, {time.time() - t0:.0f}s", flush=True)
    changed = _write_changed_tiles(tiles, dest, dest / "0", backup)
    counts = {k: sum(1 for r in rows_out if r["result"] == k) for k in ("kept", "dropped", "no-window", "nothing")}
    print(f"  redraw: {counts}, {len(changed)} z0 tiles changed, {time.time() - t0:.0f}s", flush=True)
    if report is not None:
        report.mkdir(parents=True, exist_ok=True)
        rows_out.sort(key=lambda r: -r["area_px"])
        (report / "redraw.json").write_text(json.dumps(
            {"version": RD.REDRAW_VERSION, "counts": counts, "regions": rows_out,
             "changed": sorted([list(t) for t in changed])}, indent=1) + "\n")
        kept = [r for r in rows_out if r["result"] == "kept"]
        gaps_sheet(kept, [[proc_dest / "0"], [backup0, dest / "0"], [dest / "0"]], report / "sheet.jpg",
                   titles=None)
    _GAPS.clear()
    return present, changed


# --- edges: fix the restyle's broken edges (see edges.py) ------------------

_EDGES: dict = {}


def edges_rects(grid: dict, districts: list[dict] | None) -> list[tuple[int, int, int, int]]:
    """Each district's restyle rectangle in grid pixels."""
    out = []
    for d in districts or []:
        x0, y0 = d["origin"][0] - grid["ox"], d["origin"][1] - grid["oy"]
        out.append((x0, y0, x0 + d["width"], y0 + d["height"]))
    return out


def _edges_tile(key: tuple[int, int]):
    import edges as E

    tx, ty = key
    cur = _gaps_src_tile(f"{tx}_{ty}.webp")
    orig = _gaps_load(_GAPS["orig0"] / f"{tx}_{ty}.webp")
    if cur is None or orig is None:
        return tx, ty, None
    F = E.cell_fields(cur, orig)
    return tx, ty, np.concatenate([np.atleast_3d(F[k]) for k in E.FIELD_KEYS], axis=-1).astype(np.float32)


def _edges_unstack(stack: np.ndarray) -> dict:
    import edges as E

    out, i = {}, 0
    for k in E.FIELD_KEYS:
        n = 3 if k in ("cl", "ol") else 1
        out[k] = stack[..., i : i + n] if n == 3 else stack[..., i]
        i += n
    return out


def edges_fields(work: Path, keys: list[tuple[int, int]], grid: dict, workers: int = 8) -> dict:
    """Global cell fields for `keys` (cached in work/fields.npz, resumable by tile)."""
    import multiprocessing as mp

    import edges as E

    C = TILE // E.CELL
    cache = work / "fields.npz"
    have: dict[tuple[int, int], np.ndarray] = {}
    if cache.exists():
        z = np.load(cache)
        if str(z["version"]) == E.EDGE_VERSION:
            for (tx, ty), st in zip(z["keys"].tolist(), z["stack"]):
                have[(tx, ty)] = st
    todo = [k for k in keys if k not in have]
    t0 = time.time()
    if todo:
        with mp.get_context("fork").Pool(workers) as pool:
            for n, (tx, ty, st) in enumerate(pool.imap_unordered(_edges_tile, todo, chunksize=4), 1):
                if st is not None:
                    have[(tx, ty)] = st
                if n % 200 == 0 or n == len(todo):
                    print(f"  edges fields {n}/{len(todo)}, {time.time() - t0:.0f}s", flush=True)
                    ks = sorted(have)
                    tmp = work / "fields.tmp.npz"
                    np.savez(tmp, version=E.EDGE_VERSION, keys=np.array(ks, np.int32).reshape(-1, 2),
                             stack=np.stack([have[k] for k in ks]) if ks else np.zeros((0, C, C, 21), np.float32))
                    tmp.replace(cache)
    H, W = grid["rows"] * C, grid["cols"] * C
    nch = next(iter(have.values())).shape[-1] if have else 21
    big = np.zeros((H, W, nch), np.float32)
    ok = np.zeros((H, W), bool)
    want = set(keys)
    for (tx, ty), st in have.items():
        if (tx, ty) not in want:
            continue
        big[ty * C : (ty + 1) * C, tx * C : (tx + 1) * C] = st
        ok[ty * C : (ty + 1) * C, tx * C : (tx + 1) * C] = True
    F = _edges_unstack(big)
    F["ok"] = ok
    return F


EDGES_ABSENT_FADE = 96  # px: fixes fade out toward a z0 tile the map does not have (no straight cut at its edge)


def _edges_present_ramp(box: tuple[int, int, int, int]) -> np.ndarray:
    """0..1 per px over a grid-pixel box: 1 well inside present tiles, 0 at the edge
    of an absent tile, where the viewer shows its page colour."""
    x0, y0, x1, y1 = box
    r = EDGES_ABSENT_FADE
    X0, Y0, X1, Y1 = x0 - r, y0 - r, x1 + r, y1 + r
    m = np.zeros((Y1 - Y0, X1 - X0), np.float32)
    for ty in range(max(0, Y0 // TILE), (Y1 - 1) // TILE + 1):
        for tx in range(max(0, X0 // TILE), (X1 - 1) // TILE + 1):
            if (tx, ty) in _EDGES["present"]:
                m[max(Y0, ty * TILE) - Y0 : min(Y1, (ty + 1) * TILE) - Y0,
                  max(X0, tx * TILE) - X0 : min(X1, (tx + 1) * TILE) - X0] = 1.0
    if m.min() >= 1.0:
        return np.ones((y1 - y0, x1 - x0), np.float32)
    import tone as TN

    b = TN._box(TN._box(m, r, 0), r, 1) / float((2 * r + 1) ** 2)
    return (TN.smoothstep(b, 0.5, 1.0) * m)[r : r + y1 - y0, r : r + x1 - x0].astype(np.float32)


def _edges_fix_one(job):
    import edges as E
    import gaps as GP

    kind, gid, block = job
    path = _EDGES["work"] / "fix" / f"{kind}-{gid}-{block[0]}-{block[1]}.npz"
    if path.exists():
        return path
    C = E.CELL
    M = GP.GAP_MARGIN
    W, H = _GAPS["W"], _GAPS["H"]
    bx0, by0, bx1, by1 = block
    box = (max(0, bx0 - M), max(0, by0 - M), min(W, bx1 + M), min(H, by1 + M))
    cs = np.s_[box[1] // C : box[3] // C, box[0] // C : box[2] // C]
    lab = _EDGES["lab"][kind][cs]
    sc = _EDGES["score"][kind][cs]
    region = lab == gid
    good = (sc < 0.15) & (_EDGES["cont"][cs] > 0.5) & (lab == 0)
    cur = _gaps_read_crop(_GAPS["src"], box)
    orig = _gaps_read_crop(_GAPS["orig0"], box, fill=(0, 0, 0))
    inb = np.zeros(cur.shape[:2], bool)
    inb[by0 - box[1] : by1 - box[1], bx0 - box[0] : bx1 - box[0]] = True
    inb_c = inb[::C, ::C][: region.shape[0], : region.shape[1]]
    found = []
    if kind == "veil":
        found = _gaps_window_crops(box, np.repeat(np.repeat(region, C, 0), C, 1))
    fill, alpha, used = E.fix_region(kind, cur, orig, _EDGES["style"], region, sc, good, [(w[0], w[1]) for w in found])
    alpha = alpha * inb * _edges_present_ramp(box)
    out = E.blend(cur, fill, alpha)
    # Re-score only around the region's part in this block (3 cells of context).
    rb = region & inb_c
    ys, xs = np.nonzero(rb)
    if len(ys):
        cy0, cy1 = max(0, ys.min() - 3), min(rb.shape[0], ys.max() + 4)
        cx0, cx1 = max(0, xs.min() - 3), min(rb.shape[1], xs.max() + 4)
    else:
        cy0, cy1, cx0, cx1 = 0, rb.shape[0], 0, rb.shape[1]
    vs = np.s_[cy0 * C : cy1 * C, cx0 * C : cx1 * C]
    ok, before, after = E.verify(kind, cur[vs], out[vs], orig[vs], _EDGES["style"], rb[cy0:cy1, cx0:cx1])
    if not ok:
        alpha = np.zeros_like(alpha)
        used = [{"source": "kept"}]
    for u in used:
        if u.get("source") == "window" and "index" in u:
            u.update(found[u.pop("index")][2])
    used = [dict(u, score_before=round(before, 3), score_after=round(after, 3)) for u in used]
    sl = np.s_[by0 - box[1] : by1 - box[1], bx0 - box[0] : bx1 - box[0]]
    tmp = path.with_suffix(".tmp.npz")
    np.savez(tmp, block=np.array(block), fill=fill[sl], alpha=alpha[sl].astype(np.float16),
             used=np.array(json.dumps(used)))
    tmp.replace(path)
    return path


def _edges_compose_tile(item):
    """Compose every kept fix over one z0 tile (in KINDS order) and write it when it changed."""
    (tx, ty), paths = item
    name = f"{tx}_{ty}.webp"
    src = _gaps_src_tile(name)
    if src is None:
        return tx, ty, False
    t = src.astype(np.float32)
    touched = False
    for p in paths:
        z = np.load(p)
        bx0, by0, bx1, by1 = (int(v) for v in z["block"])
        a = z["alpha"].astype(np.float32)
        if float(a.max()) <= 2e-3:
            continue
        ix0, iy0 = max(bx0, tx * TILE), max(by0, ty * TILE)
        ix1, iy1 = min(bx1, (tx + 1) * TILE), min(by1, (ty + 1) * TILE)
        if ix1 <= ix0 or iy1 <= iy0:
            continue
        sa = a[iy0 - by0 : iy1 - by0, ix0 - bx0 : ix1 - bx0]
        if float(sa.max()) <= 2e-3:
            continue
        sf = z["fill"][iy0 - by0 : iy1 - by0, ix0 - bx0 : ix1 - bx0].astype(np.float32)
        ts = np.s_[iy0 - ty * TILE : iy1 - ty * TILE, ix0 - tx * TILE : ix1 - tx * TILE]
        t[ts] = t[ts] * (1.0 - sa[..., None]) + sf * sa[..., None]
        touched = True
    out = np.clip(np.rint(t), 0, 255).astype(np.uint8)
    dest0, backup0 = _EDGES["dest0"], _EDGES["backup0"]
    cur = _gaps_load(dest0 / name)
    if cur is not None and int(np.abs(cur.astype(np.int16) - out.astype(np.int16)).max()) <= 1:
        return tx, ty, touched and int(np.abs(src.astype(np.int16) - out.astype(np.int16)).max()) > 1
    if backup0 is not None and not (backup0 / name).exists():
        shutil.copy2(dest0 / name, backup0 / name)
    _atomic_save_webp(Image.fromarray(out, "RGB"), dest0 / name, lossless=True)
    return tx, ty, int(np.abs(src.astype(np.int16) - out.astype(np.int16)).max()) > 1


def polish_edges(
    dest: Path,
    grid: dict,
    *,
    proc_dest: Path | None,
    districts: list[dict] | None,
    backup: Path,
    report: Path | None = None,
    dry_run: bool = False,
    workers: int = 8,
) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    """Find and fix the restyle's broken edges for free (see edges.py).

    Audits the edge band (vignette field above edges.EDGE_DEEP) and every
    district rim. Re-runs start from the z0 copies in `backup/0`, so they land
    on the same tiles; work is cached in `backup/work` (cell fields, the house
    style, one file per fixed block), so an interrupted run resumes. With
    `dry_run`, only the report (edges.json, before.jpg) is written.
    Returns (present, changed).
    """
    import multiprocessing as mp

    import edges as E
    import gaps as GP
    import tone as TN

    t0 = time.time()
    if proc_dest is None or not (proc_dest / "0").is_dir():
        raise SystemExit("--polish-edges needs the procedural tiles (the original the restyle was made from)")
    cols, rows = grid["cols"], grid["rows"]
    C = TILE // E.CELL
    present = present_from_disk(dest, cols, rows)
    backup0 = backup / "0"
    backup0.mkdir(parents=True, exist_ok=True)
    work = backup / "work"
    (work / "fix").mkdir(parents=True, exist_ok=True)
    stamp = work / "stamp.txt"
    if stamp.exists() and stamp.read_text().strip() != E.EDGE_VERSION:
        shutil.rmtree(work)
        (work / "fix").mkdir(parents=True, exist_ok=True)
    stamp.write_text(E.EDGE_VERSION + "\n")
    src = [backup0, dest / "0"]
    _GAPS.update(grid=grid, districts=districts or [], src=src, orig0=proc_dest / "0", H=rows * TILE, W=cols * TILE)
    field = np.load(dest / ".vignette-dist.npz")["field"]
    rects = edges_rects(grid, districts)
    audit, outer = E.band_weight(field, (rows * C, cols * C), rects, field_scale=VIGNETTE_SCALE)
    band_tiles = {(tx, ty) for (tx, ty) in present
                  if audit[ty * C : (ty + 1) * C, tx * C : (tx + 1) * C].any()}
    deep = sorted(present - band_tiles)
    sample = deep[:: max(1, len(deep) // 400)]
    keys = sorted(band_tiles | set(sample))
    print(f"  edges: {len(band_tiles)} band tiles, {len(sample)} interior tiles for the house style", flush=True)
    F = edges_fields(work, keys, grid, workers)
    style_path = work / "style.pkl"
    if style_path.exists():
        style = pickle.loads(style_path.read_bytes())
    else:
        inner = np.zeros_like(audit)
        for tx, ty in sample:
            inner[ty * C : (ty + 1) * C, tx * C : (tx + 1) * C] = True
        v = inner & F["ok"] & (E.content_weight(F) > 0.5) & (F["ncc"] > 0.4)
        style = TN.fit_house_style(F["ol"][v], F["cl"][v])
        style_path.write_bytes(pickle.dumps(style))
    S = E.scores(F, style)
    xl, yl = tone_candidate_lines(grid, districts)
    zone = audit & F["ok"]
    labs, scs, found = {}, {}, []
    for kind in E.KINDS:
        sk = S[kind] * zone * (outer if kind == "ghost" else 1.0)
        void = S["white"] if kind == "veil" else np.zeros_like(sk)
        regs, lab = GP.find_gaps(sk, void, xl, yl, min_cells=8)
        labs[kind], scs[kind] = lab, sk
        for g in regs:
            r = g.row()
            sub = {"void": "white", "cutoff": "cutoff", "blur": "veil"}[g.kind] if kind == "veil" else kind
            if kind == "road":
                x0, y0, x1, y1 = g.bbox
                ys, xs = slice(max(0, y0 - 3), y1 + 3), slice(max(0, x0 - 3), x1 + 3)
                reg = lab[ys, xs] == g.id
                ring = GP.soft(reg, 1.5) > 0.05
                ring &= ~reg
                cont = ring & (F["orr"][ys, xs] > 0.1) & (F["rk"][ys, xs] > 0.6)
                sub = "road-cut" if cont.any() else "road-missing"
            r.update(kind=f"{kind}/{sub}" if sub != kind else kind, pass_kind=kind, sub=sub)
            found.append((kind, g, r))
    counts: dict[str, int] = {}
    for _, _, r in found:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    print(f"  edges: {len(found)} regions {counts}, {time.time() - t0:.0f}s", flush=True)
    _EDGES.update(work=work, lab=labs, score=scs, cont=S["cont"], style=style, present=present,
                  dest0=dest / "0", backup0=backup0)
    del F
    rows_out = sorted((r for _, _, r in found), key=lambda r: -r["rank"])
    if report is not None:
        report.mkdir(parents=True, exist_ok=True)
        (report / "edges.json").write_text(json.dumps(
            {"version": E.EDGE_VERSION, "counts": counts, "regions": rows_out}, indent=1) + "\n")
        before = [backup0, dest / "0"]
        gaps_sheet(rows_out, [[proc_dest / "0"], before], report / "before.jpg")
    if dry_run:
        _GAPS.clear(); _EDGES.clear()
        return present, set()
    B = E.EDGE_BLOCK
    jobs = []
    for kind, g, _ in found:
        x0, y0, x1, y1 = (v * E.CELL for v in g.bbox)
        for by in range(y0 // B * B, y1, B):
            for bx in range(x0 // B * B, x1, B):
                blk = (bx, by, min(bx + B, cols * TILE), min(by + B, rows * TILE))
                cs = np.s_[blk[1] // E.CELL : blk[3] // E.CELL, blk[0] // E.CELL : blk[2] // E.CELL]
                if (labs[kind][cs] == g.id).any():
                    jobs.append((kind, g.id, blk))
    print(f"  edges: {len(jobs)} blocks to fix", flush=True)
    with mp.get_context("fork").Pool(workers) as pool:
        for n, _ in enumerate(pool.imap_unordered(_edges_fix_one, jobs, chunksize=1), 1):
            if n % 25 == 0 or n == len(jobs):
                print(f"  edges fixed {n}/{len(jobs)}, {time.time() - t0:.0f}s", flush=True)
    used_by: dict[tuple[str, int], list] = {}
    per_tile: dict[tuple[int, int], list[tuple[int, Path]]] = {}
    order = {k: i for i, k in enumerate(E.KINDS)}
    for kind, gid, blk in jobs:
        p = work / "fix" / f"{kind}-{gid}-{blk[0]}-{blk[1]}.npz"
        z = np.load(p)
        used_by.setdefault((kind, gid), []).extend(json.loads(str(z["used"])))
        if float(z["alpha"].max()) <= 2e-3:
            continue
        for ty in range(blk[1] // TILE, (blk[3] - 1) // TILE + 1):
            for tx in range(blk[0] // TILE, (blk[2] - 1) // TILE + 1):
                if (tx, ty) in present:
                    per_tile.setdefault((tx, ty), []).append((order[kind], p))
    items = [(k, [p for _, p in sorted(v, key=lambda e: (e[0], str(e[1])))]) for k, v in sorted(per_tile.items())]
    # A tile an earlier run changed but this one does not: put the original back.
    for p in sorted(backup0.glob("*.webp")):
        tx, ty = (int(v) for v in p.stem.split("_"))
        if (tx, ty) not in per_tile:
            items.append(((tx, ty), []))
    changed: set[tuple[int, int]] = set()
    with mp.get_context("fork").Pool(workers) as pool:
        for tx, ty, ch in pool.imap_unordered(_edges_compose_tile, items, chunksize=2):
            if ch or (backup0 / f"{tx}_{ty}.webp").exists():
                changed.add((tx, ty))
    print(f"  edges: {len(changed)} z0 tiles changed, {time.time() - t0:.0f}s", flush=True)
    if report is not None:
        for r in rows_out:
            r["used"] = used_by.get((r["pass_kind"], r["id"]), [])
        fixed: dict[str, int] = {}
        for r in rows_out:
            if any(u.get("source") != "kept" for u in r["used"]):
                fixed[r["kind"]] = fixed.get(r["kind"], 0) + 1
        (report / "edges.json").write_text(json.dumps(
            {"version": E.EDGE_VERSION, "counts": counts, "fixed": fixed, "regions": rows_out,
             "changed": sorted([list(t) for t in changed])}, indent=1) + "\n")
        gaps_sheet(rows_out, [[proc_dest / "0"], [backup0, dest / "0"], [dest / "0"]], report / "after.jpg")
    _GAPS.clear()
    _EDGES.clear()
    return present, changed


def write_overview_from_z0(dest: Path, grid: dict, out_dir: Path, max_width: int = 2048, pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C),
                           overlay: Path | None = None) -> dict:
    """Build overview.webp from z0 tiles, without a full-resolution canvas.

    Each tile is resized to the rounded span between this cell and the
    next so neighbours abut. A tile in `overlay/0` replaces the one in
    `dest/0` (a staged pass). A single shared width plus a truncated
    paste position left one-pixel gaps of pad (a visible grid on the
    minimap) whenever `TILE * scale` was not an integer.
    """
    cols, rows = grid["cols"], grid["rows"]
    full_w, full_h = cols * TILE, rows * TILE
    pw = min(max_width, full_w)
    scale = pw / full_w
    ph = max(1, round(full_h * scale))
    prev = Image.new("RGB", (pw, ph), pad_rgb)
    z0 = dest / "0"
    if z0.exists():
        for path in z0.glob("*.webp"):
            stem = path.stem
            if "_" not in stem:
                continue
            sx, sy = stem.split("_", 1)
            try:
                tx, ty = int(sx), int(sy)
            except ValueError:
                continue
            x0 = int(round(tx * TILE * scale))
            y0 = int(round(ty * TILE * scale))
            x1 = min(pw, int(round((tx + 1) * TILE * scale)))
            y1 = min(ph, int(round((ty + 1) * TILE * scale)))
            tw, th = x1 - x0, y1 - y0
            if tw < 1 or th < 1 or x0 >= pw or y0 >= ph:
                continue
            if overlay is not None and (overlay / "0" / path.name).exists():
                path = overlay / "0" / path.name
            with Image.open(path) as im:
                small = im.convert("RGB").resize((tw, th), Image.Resampling.LANCZOS)
            prev.paste(small, (x0, y0))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "overview.webp"
    prev.save(path, "WEBP", quality=86, method=2)
    return {"src": "overview.webp", "width": pw, "height": ph}


def write_overview(image: Image.Image, dest: Path, max_width: int = 2048) -> dict:
    w, h = image.size
    pw = min(max_width, w)
    ph = max(1, round(h * (pw / w)))
    prev = image.convert("RGB").resize((pw, ph), Image.Resampling.LANCZOS)
    path = dest / "overview.webp"
    prev.save(path, "WEBP", quality=86, method=2)
    return {"src": "overview.webp", "width": pw, "height": ph}


def run_polish_coverage(args, grid: dict, dest: Path, proc_dest: Path | None, geo: dict) -> set[tuple[int, int]]:
    """--polish-coverage: fade the restyle and procedural z0 tiles along the outline, rebuild the
    procedural pyramid here, and return the restyle's z0 set (its pyramid is rebuilt by the caller)."""
    import coverage as CV

    outline = CV.Outline(CV.project_rings(CV.load_rings(args.polish_coverage), geo, (grid["ox"], grid["oy"])))
    lossless = lambda im, path: _atomic_save_webp(im, path, lossless=True)  # noqa: E731
    common = dict(fog=VIEW_FOG, noise=fade_boundary_noise, almost_empty=_almost_empty, tile=TILE)
    present, _, _ = CV.polish_coverage(dest, grid, outline, backup=args.coverage_backup / dest.name,
                                       save=lossless, **common)
    if proc_dest is not None:
        lossy = lambda im, path: _atomic_save_webp(im, path, lossless=False)  # noqa: E731
        proc_present, changed, dropped = CV.polish_coverage(
            proc_dest, grid, outline, backup=args.coverage_backup / proc_dest.name, save=lossy, **common)
        if changed or dropped:
            _pyramid_from_z0(proc_dest, grid, {0: proc_present}, pad_rgb=(0x1A, 0x1E, 0x1C))
            print(f"  procedural pyramid rebuilt from {len(proc_present)} z0 tiles", flush=True)
    return present


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", type=Path, default=Path("/workspace/tinyshenzhen-futian-restyled.png"))
    ap.add_argument("--chunks", type=Path, default=Path("/workspace/tinysz-futian"))
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("/workspace/tinyshenzen-restyle/tinyworlds-frontend/public/data/tinysz"),
    )
    ap.add_argument("--restyle-dir", default="tiles-restyled")
    ap.add_argument("--procedural-dir", default="tiles")
    ap.add_argument("--skip-procedural", action="store_true")
    ap.add_argument("--skip-restyle", action="store_true")
    ap.add_argument(
        "--repair",
        action="store_true",
        help="re-composite only z0 restyle tiles that contain viewer-fog beige, then fill gaps",
    )
    ap.add_argument(
        "--polish",
        action="store_true",
        help="feather dark edge fog to viewer fog and fill blocky bay water (free, no API)",
    )
    ap.add_argument(
        "--polish-edge",
        action="store_true",
        help="restore washed-out land from the procedural layer, then fade the coverage edge with a global distance field (no tile seams)",
    )
    ap.add_argument(
        "--polish-vignette",
        action="store_true",
        help="rebuild the edge from source (no per-tile blur) and apply one smooth global beige vignette (free, idempotent, no API)",
    )
    ap.add_argument(
        "--polish-water",
        action="store_true",
        help="after --polish-vignette: one bay water tone (texture kept, no tile steps) and a separate water fade (free, idempotent, no API)",
    )
    ap.add_argument(
        "--polish-forest",
        action="store_true",
        help="after --polish-vignette: cross-fade washed land just inside the edge band to the clean staging (free, idempotent, no API)",
    )
    ap.add_argument(
        "--polish-flats",
        action="store_true",
        help="with --polish-water/--polish-forest: fade featureless ground near the edge (flat fill, gray puddles, white lots) into the fog with a soft mask (free, idempotent)",
    )
    ap.add_argument(
        "--polish-redraw",
        type=Path,
        default=None,
        metavar="JSON",
        help="blend a paid redraw into target regions: JSON {category: [[x0,y0,x1,y1] grid px]} (see redraw.py); "
             "needs --redraw-src and --redraw-backup. Drops any fill that makes the gap score worse",
    )
    ap.add_argument(
        "--redraw-src",
        action="append",
        default=[],
        metavar="CHUNKS:TILES",
        help="with --polish-redraw: a district's chunks dir and its redrawn window PNGs (repeatable)",
    )
    ap.add_argument("--redraw-backup", type=Path, default=None, metavar="DIR",
                    help="with --polish-redraw (required): z0 copies before change; re-runs start from them")
    ap.add_argument("--redraw-report", type=Path, default=None, metavar="DIR",
                    help="with --polish-redraw: write redraw.json and sheet.jpg (original, before, after)")
    ap.add_argument(
        "--polish-coverage",
        type=Path,
        default=None,
        metavar="GEOJSON",
        help="fade every layer's z0 tiles to the viewer fog along a coverage outline and drop tiles left blank "
             "(see coverage.py); needs --coverage-backup. Run after the other polish passes, before --web-tiles",
    )
    ap.add_argument("--coverage-backup", type=Path, default=None, metavar="DIR",
                    help="with --polish-coverage (required): originals of changed or dropped z0 tiles, per layer")
    ap.add_argument(
        "--polish-fogline",
        action="store_true",
        help="last: fade fog-dominated pixels near the edge (dashed traces, gray-washed windows, straight cut-offs) to beige with a feathered, wobbled ramp (free, idempotent)",
    )
    ap.add_argument(
        "--polish-mint",
        action="store_true",
        help="after --polish-fogline: turn teal procedural-staging greens near the edge toward the restyled forest hue (free, idempotent)",
    )
    ap.add_argument(
        "--polish-spots",
        type=Path,
        default=None,
        metavar="JSON",
        help="apply listed one-off fixes (fog ellipses, water blob reshapes, milky river veils) from a spots file such as lora/spots/tinysz-core.json (free, idempotent)",
    )
    ap.add_argument(
        "--polish-seams",
        action="store_true",
        help="after spots: feather a tone jump across a z0 tile edge, and across a restyle-window hem when those windows are on disk; skip building cuts and river banks (free, idempotent)",
    )
    ap.add_argument(
        "--polish-tone",
        action="store_true",
        help="after spots, instead of --polish-seams: one house tone over the whole restyle (global original->restyle colour fit, seam-step solve, robust low-pass correction, washed patches refilled); free, idempotent from .tone-src",
    )
    ap.add_argument(
        "--polish-gaps",
        action="store_true",
        help="last: find beige voids, ghostly veils, cut-offs and blurry patches inside the city and fill them from an intact overlapping restyle window, else the house-styled original (free, no API)",
    )
    ap.add_argument(
        "--gaps-stage",
        type=Path,
        default=None,
        metavar="DIR",
        help="with --polish-gaps: write changed tiles, their pyramid parents, the overview and the manifest to DIR (same layout as --out) and leave the map untouched",
    )
    ap.add_argument(
        "--gaps-report",
        type=Path,
        default=None,
        metavar="DIR",
        help="with --polish-gaps: write gaps.json (ranked list, sources used) and sheet.jpg (worst 40: original, before, after)",
    )
    ap.add_argument(
        "--gaps-backup",
        type=Path,
        default=None,
        metavar="DIR",
        help="with --polish-gaps in place: copy each z0 tile to DIR/0 before changing it",
    )
    ap.add_argument(
        "--polish-edges",
        action="store_true",
        help="after --polish-gaps: fix the restyle's broken edges (washed or cut-off content, roads that stop dead, roads or bridges over nothing, half-faded smudges past the map) in the edge band and along district rims (free, no API; see edges.py)",
    )
    ap.add_argument(
        "--edges-backup",
        type=Path,
        default=None,
        metavar="DIR",
        help="with --polish-edges (required): copy each z0 tile to DIR/0 before changing it; re-runs start from those copies; DIR/work caches the fields and fixes so an interrupted run resumes",
    )
    ap.add_argument(
        "--edges-report",
        type=Path,
        default=None,
        metavar="DIR",
        help="with --polish-edges: write edges.json (regions by kind, sources used), before.jpg and after.jpg (worst 40: original, before, after)",
    )
    ap.add_argument(
        "--edges-dry-run",
        action="store_true",
        help="with --polish-edges: detect and write the report only; leave the map alone",
    )
    ap.add_argument(
        "--tone-region",
        default="",
        metavar="TX0,TY0,TX1,TY1",
        help="with --polish-tone: only write z0 tiles in this range (end exclusive), for a preview",
    )
    ap.add_argument(
        "--tone-out",
        type=Path,
        default=None,
        help="with --polish-tone: write toned z0 tiles here instead of in place, and stop (no pyramid)",
    )
    ap.add_argument(
        "--fade",
        type=int,
        default=None,
        help="global edge-fade radius in z0 pixels (default: 128 for --polish-edge, 768 for --polish-vignette)",
    )
    ap.add_argument(
        "--pyramid-only",
        action="store_true",
        help="rebuild the restyle pyramid and overview from existing z0, do not reslice or polish",
    )
    ap.add_argument(
        "--feather",
        type=int,
        default=EDGE_FEATHER,
        help=f"edge-fog feather radius in z0 pixels (default {EDGE_FEATHER})",
    )
    ap.add_argument("--tilewise", action="store_true", help="slice from chunks/windows, never a full canvas")
    ap.add_argument(
        "--district",
        action="append",
        default=[],
        metavar="CHUNKS:TILES",
        help="a restyle district as chunks_dir:tiles_dir (repeat; world coords). Implies --tilewise.",
    )
    ap.add_argument("--name", default="", help="manifest name (default: the chunks meta city id)")
    ap.add_argument("--start-lon", type=float, default=None)
    ap.add_argument("--start-lat", type=float, default=None)
    ap.add_argument(
        "--web-tiles",
        action="store_true",
        help="build the compressed web pyramid (tiles-web/) from --restyle-dir, point the manifest at it, and stop (free, idempotent; see web_tiles.py)",
    )
    ap.add_argument("--web-dir", default="tiles-web", help="with --web-tiles: the web pyramid dir (default tiles-web)")
    args = ap.parse_args()

    if args.web_tiles:
        import web_tiles as WT

        WT.build_web_tiles(args.out, args.restyle_dir, args.web_dir)
        return

    import tiles as T

    def load_district(spec: str, world_geo: dict) -> dict:
        if ":" in spec:
            chunks_s, tiles_s = spec.split(":", 1)
        elif "," in spec:
            chunks_s, tiles_s = spec.split(",", 1)
        else:
            raise SystemExit(f"bad --district {spec!r}: expected chunks_dir:tiles_dir")
        chunks = Path(chunks_s)
        tiles_dir = Path(tiles_s)
        dmeta = json.loads((chunks / "meta.json").read_text())
        wx, wy, dx, dy = district_world_origin(dmeta, world_geo)
        origin = (wx, wy)
        width, height = int(dmeta["width"]), int(dmeta["height"])
        windows = T.tiles_for(width, height, 1024, 256)
        city = dmeta.get("city") or chunks.name
        loc = (int(dmeta["origin"]["x"]), int(dmeta["origin"]["y"]))
        print(
            f"  district {city} local origin {loc} + delta ({dx:.1f},{dy:.1f}) → world {origin}",
            flush=True,
        )
        return {
            "chunks": chunks,
            "tiles": tiles_dir,
            "meta": dmeta,
            "origin": origin,
            "width": width,
            "height": height,
            "windows": windows,
        }

    meta = json.loads((args.chunks / "meta.json").read_text())
    world_geo = meta.get("geo") or {}
    districts = [load_district(s, world_geo) for s in args.district]
    tilewise = bool(args.tilewise or districts)

    origin = meta["origin"]
    min_x, min_y = int(origin["x"]), int(origin["y"])
    width, height = int(meta["width"]), int(meta["height"])
    max_x, max_y = min_x + width, min_y + height
    if districts:
        # Union the core canvas with every district so a slightly larger
        # restyle canvas is not clipped, and a slightly smaller one still sits
        # on the core grid. Origins are already in world coordinates.
        for d in districts:
            min_x = min(min_x, d["origin"][0])
            min_y = min(min_y, d["origin"][1])
            max_x = max(max_x, d["origin"][0] + d["width"])
            max_y = max(max_y, d["origin"][1] + d["height"])
    grid = make_grid(min_x, min_y, max_x, max_y)
    print(
        f"slice_frontend: grid {grid['cols']}×{grid['rows']} tiles of {TILE}, "
        f"origin ({grid['ox']},{grid['oy']}), image origin ({min_x},{min_y})"
        + (f", {len(districts)} districts" if districts else ""),
        flush=True,
    )

    args.out.mkdir(parents=True, exist_ok=True)
    restyle_levels = None
    proc_levels = None
    pad_rgb = (0x1A, 0x1E, 0x1C)

    if args.polish or args.polish_edge or args.polish_vignette or args.polish_water or args.polish_forest or args.polish_flats or args.polish_fogline or args.polish_mint or args.polish_spots or args.polish_seams or args.polish_tone or args.polish_gaps or args.polish_edges or args.polish_redraw or args.polish_coverage or args.pyramid_only:
        dest = args.out / args.restyle_dir
        if not (dest / "0").is_dir():
            raise SystemExit(f"--polish/--polish-edge/--polish-vignette/--pyramid-only needs existing z0 tiles at {dest / '0'}")
        pdest = args.out / args.procedural_dir
        proc_dest = pdest if (pdest / "0").is_dir() else None
        if args.polish_gaps:
            target = args.gaps_stage if args.gaps_stage is not None else args.out
            print(f"  gaps → {target / args.restyle_dir}", flush=True)
            z0_present, changed = polish_gaps(
                dest, grid,
                proc_dest=proc_dest,
                districts=districts,
                stage=args.gaps_stage,
                report=args.gaps_report,
                backup=args.gaps_backup,
            )
            if not changed:
                print("  gaps: nothing to change", flush=True)
                return
            written = rebuild_parents(dest, target / args.restyle_dir, changed, grid, z0_present, pad_rgb=VIEW_FOG)
            print(f"  gaps: rebuilt {written} pyramid tiles above them", flush=True)
            overview = write_overview_from_z0(dest, grid, target, pad_rgb=VIEW_FOG,
                                              overlay=(target / args.restyle_dir) if args.gaps_stage is not None else None)
            existing = json.loads((args.out / "manifest.json").read_text())
            restyle_levels = existing["levels"]
            args._manifest_dir = target
        elif args.polish_redraw:
            if args.redraw_backup is None or not args.redraw_src:
                raise SystemExit("--polish-redraw needs --redraw-src CHUNKS:TILES and --redraw-backup DIR")
            sources = [load_district(s, world_geo) for s in args.redraw_src]
            print(f"  redraw → {dest}", flush=True)
            z0_present, changed = polish_redraw(
                dest, grid,
                proc_dest=proc_dest,
                targets=args.polish_redraw,
                sources=sources,
                backup=args.redraw_backup,
                report=args.redraw_report,
            )
            if not changed:
                print("  redraw: nothing to change", flush=True)
                return
            written = rebuild_parents(dest, dest, changed, grid, z0_present, pad_rgb=VIEW_FOG)
            print(f"  redraw: rebuilt {written} pyramid tiles above them", flush=True)
            overview = write_overview_from_z0(dest, grid, args.out, pad_rgb=VIEW_FOG)
            existing = json.loads((args.out / "manifest.json").read_text())
            restyle_levels = existing["levels"]
        elif args.polish_edges:
            if args.edges_backup is None:
                raise SystemExit("--polish-edges needs --edges-backup DIR")
            print(f"  edges → {dest}", flush=True)
            z0_present, changed = polish_edges(
                dest, grid,
                proc_dest=proc_dest,
                districts=districts,
                backup=args.edges_backup,
                report=args.edges_report,
                dry_run=args.edges_dry_run,
            )
            if not changed:
                print("  edges: nothing to change", flush=True)
                return
            written = rebuild_parents(dest, dest, changed, grid, z0_present, pad_rgb=VIEW_FOG)
            print(f"  edges: rebuilt {written} pyramid tiles above them", flush=True)
            overview = write_overview_from_z0(dest, grid, args.out, pad_rgb=VIEW_FOG)
            existing = json.loads((args.out / "manifest.json").read_text())
            restyle_levels = existing["levels"]
        elif args.polish:
            print(f"  polishing restyle z0 → {dest}", flush=True)
            z0_present = polish_z0_tiles(
                dest, grid,
                districts=districts,
                proc_dest=proc_dest,
                pad_rgb=pad_rgb,
                feather=args.feather,
            )
        elif args.polish_vignette:
            if not districts:
                raise SystemExit("--polish-vignette needs --district chunks:tiles to reslice from source")
            fade = args.fade if args.fade is not None else VIGNETTE_FADE
            print(f"  vignette from source (fade={fade}px) → {dest}", flush=True)
            z0_present = polish_vignette(
                dest, grid, districts,
                proc_dest=proc_dest,
                pad_rgb=pad_rgb,
                fade=fade,
            )
        elif args.polish_mint or args.polish_spots or args.polish_seams or args.polish_tone:
            if args.polish_mint:
                fade = args.fade if args.fade is not None else VIGNETTE_FADE
                print(f"  mint rims → {dest}", flush=True)
                z0_present = polish_mint(dest, grid, fade=fade)
            if args.polish_spots:
                print(f"  spots from {args.polish_spots} → {dest}", flush=True)
                z0_present = polish_spots(dest, grid, load_spots(args.polish_spots))
            if args.polish_seams:
                print(f"  seam blend → {dest}", flush=True)
                z0_present = polish_seams(dest, grid, districts=districts)
            if args.polish_tone:
                region = tuple(int(v) for v in args.tone_region.split(",")) if args.tone_region else None
                if region is not None and len(region) != 4:
                    raise SystemExit("--tone-region needs TX0,TY0,TX1,TY1")
                print(f"  house tone → {args.tone_out or dest}", flush=True)
                z0_present = polish_tone(
                    dest, grid,
                    proc_dest=proc_dest,
                    districts=districts,
                    region=region,
                    out_dir=args.tone_out,
                )
                if args.tone_out is not None:
                    return
        elif args.polish_coverage:
            if args.coverage_backup is None:
                raise SystemExit("--polish-coverage needs --coverage-backup DIR")
            z0_present = run_polish_coverage(args, grid, dest, proc_dest, world_geo)
        elif args.polish_fogline:
            fade = args.fade if args.fade is not None else VIGNETTE_FADE
            print(f"  fog boundary cleanup → {dest}", flush=True)
            z0_present = polish_fogline(dest, grid, pad_rgb=pad_rgb, fade=fade)
        elif args.polish_water or args.polish_forest or args.polish_flats:
            if args.polish_water and not districts:
                raise SystemExit("--polish-water needs --district chunks:tiles for the water masks")
            fade = args.fade if args.fade is not None else VIGNETTE_FADE
            print(f"  bay water tone and fade / near-edge land → {dest}", flush=True)
            z0_present = polish_water(
                dest, grid, districts, pad_rgb=pad_rgb, fade=fade,
                water=args.polish_water, forest=args.polish_forest, flats=args.polish_flats,
            )
        elif args.polish_edge:
            fade = args.fade if args.fade is not None else EDGE_FADE
            print(f"  smoothing coverage edge → {dest}", flush=True)
            z0_present = polish_smooth_edge(
                dest, grid,
                proc_dest=proc_dest,
                pad_rgb=pad_rgb,
                fade=fade,
            )
        else:
            z0_present = present_from_disk(dest, grid["cols"], grid["rows"])
            print(f"  pyramid-only from {len(z0_present)} existing z0 tiles", flush=True)
        if not (args.polish_gaps or args.polish_edges or args.polish_redraw):
            # Viewer fog as pad: missing children blend into the page, no dark stairs.
            restyle_levels = _pyramid_from_z0(dest, grid, {0: z0_present}, pad_rgb=VIEW_FOG)
            overview = write_overview_from_z0(dest, grid, args.out, pad_rgb=VIEW_FOG)
        args.skip_restyle = True
        args.skip_procedural = True
        # Do not run fill_gaps: that would copy dark procedural edge fog back.
        args._skip_fill = True
    elif args.repair:
        if not districts:
            raise SystemExit("--repair needs --district chunks:tiles")
        dest = args.out / args.restyle_dir
        dest.mkdir(parents=True, exist_ok=True)
        print(f"  repairing beige restyle tiles → {dest}", flush=True)
        present = repair_beige_tiles(districts, grid, dest, pad_rgb=pad_rgb)
        removed = cleanup_absent_tiles(dest, present[0], grid["cols"], grid["rows"])
        if removed:
            print(f"  removed {removed} leftover z0 tiles", flush=True)
        # Fill and pyramid happen below, once procedural is known to be on disk.
        restyle_levels = [{"z": 0, "cols": grid["cols"], "rows": grid["rows"],
                           "tiles": pack_bits(present[0], grid["cols"], grid["rows"])}]
        # stash present for the fill pass
        args._repair_present = present[0]
        overview = {"src": "overview.webp", "width": 0, "height": 0}
        args.skip_restyle = True  # already have z0; skip a full reslice
        args.skip_procedural = True  # keep the existing procedural pyramid
    elif not args.skip_restyle:
        dest = args.out / args.restyle_dir
        dest.mkdir(parents=True, exist_ok=True)
        if districts:
            print(f"  restyle tilewise from {len(districts)} districts → {dest}", flush=True)
            restyle_levels = slice_restyle_tilewise(districts, grid, dest, pad_rgb=pad_rgb)
            overview = write_overview_from_z0(dest, grid, args.out, pad_rgb=pad_rgb)
        elif tilewise:
            raise SystemExit("--tilewise restyle needs --district chunks:tiles (or pass --image without --tilewise)")
        else:
            print(f"  opening {args.image}", flush=True)
            with Image.open(args.image) as im:
                im = im.convert("RGB")
                if im.size != (width, height):
                    raise SystemExit(f"image is {im.size}, meta says {width}×{height}")
                restyle_levels = slice_image(im, grid, (min_x, min_y), dest)
                overview = write_overview(im, args.out)
        print(f"  restyle pyramid → {dest}", flush=True)
    else:
        overview = {"src": "overview.webp", "width": 0, "height": 0}

    if not args.skip_procedural:
        dest = args.out / args.procedural_dir
        dest.mkdir(parents=True, exist_ok=True)
        if tilewise or (width * height > 80_000_000):
            print(f"  procedural tilewise from {args.chunks / 'chunks'} → {dest}", flush=True)
            proc_levels = slice_procedural_tilewise(args.chunks, meta, grid, dest, pad_rgb=pad_rgb)
            if args.skip_restyle:
                overview = write_overview_from_z0(dest, grid, args.out, pad_rgb=pad_rgb)
        else:
            chunk = int(meta.get("chunk") or 2048)
            cols, rows = int(meta["cols"]), int(meta["rows"])
            print(f"  assembling procedural {width}×{height} from {cols}×{rows} chunks", flush=True)
            proc = Image.new("RGB", (width, height), pad_rgb)
            cdir = args.chunks / "chunks"
            for row in range(rows):
                for col in range(cols):
                    src = cdir / f"{col}_{row}.png"
                    if not src.exists():
                        continue
                    with Image.open(src) as im:
                        proc.paste(im.convert("RGB"), (col * chunk, row * chunk))
            proc_levels = slice_image(proc, grid, (min_x, min_y), dest)
            if args.skip_restyle:
                overview = write_overview(proc, args.out)
            del proc
        print(f"  procedural pyramid → {dest}", flush=True)

    # When both layers exist, copy procedural z0 into restyle cells that have
    # no city restyle, then rebuild the restyle pyramid and overview so the
    # viewer's single restyle bitset covers every data cell.
    rdest = args.out / args.restyle_dir
    pdest = args.out / args.procedural_dir
    if (restyle_levels is not None or args.repair) and rdest.is_dir() and (pdest / "0").is_dir() and not getattr(args, "_skip_fill", False):
        if restyle_levels is not None and restyle_levels:
            z0 = restyle_levels[0]
            present = unpack_bits(z0["tiles"], z0["cols"], z0["rows"])
        else:
            present = getattr(args, "_repair_present", set())
        present = fill_gaps_from_procedural(rdest, pdest, grid, present, pad_rgb=pad_rgb)
        cleanup_absent_tiles(rdest, present, grid["cols"], grid["rows"])
        restyle_levels = _pyramid_from_z0(rdest, grid, {0: present}, pad_rgb=pad_rgb)
        overview = write_overview_from_z0(rdest, grid, args.out, pad_rgb=pad_rgb)
        print(f"  restyle pyramid (after fill) → {rdest}", flush=True)

    geo = meta.get("geo") or {}
    start_lon = args.start_lon if args.start_lon is not None else float(geo.get("lon0", 114.0683))
    start_lat = args.start_lat if args.start_lat is not None else float(geo.get("lat0", 22.5455))
    name = args.name or meta.get("city") or "tinysz"
    manifest = {
        "name": name,
        "build": format(int(time.time() * 1000), "x")[-9:],
        "tileSize": TILE,
        "origin": {"x": grid["ox"], "y": grid["oy"]},
        "levels": restyle_levels or proc_levels,
        "bounds": {"minX": min_x, "minY": min_y, "maxX": max_x, "maxY": max_y},
        "start": {"lon": start_lon, "lat": start_lat, "scale": 0.85},
        "maxScale": 3,
        "northDeg": 63.4,
        "geo": {
            "lon0": geo.get("lon0", 114.0683),
            "lat0": geo.get("lat0", 22.5455),
            "betaDeg": geo.get("betaDeg", 0),
            "k": geo.get("k", 2),
            "kz": geo.get("kz", 2.449489742783178),
        },
        "overview": overview,
        "attribution": [
            "© OpenStreetMap contributors",
            "Overture Maps Foundation",
            "CNBH-10m (Wu et al., CC BY 4.0)",
            "3D-GloBFP (Che et al., CC BY 4.0)",
        ],
    }
    if restyle_levels is not None and proc_levels is not None:
        # Both pyramids are on disk: the viewer defaults to the restyle and can switch.
        manifest["restyle"] = {"dir": args.restyle_dir}
        manifest["levels"] = restyle_levels
    elif restyle_levels is not None:
        manifest["levels"] = restyle_levels
    elif proc_levels is not None:
        manifest["levels"] = proc_levels
    existing_path = args.out / "manifest.json"
    existing = json.loads(existing_path.read_text()) if existing_path.exists() else {}
    if existing.get("indexes"):
        manifest["indexes"] = existing["indexes"]
    procedural_on_disk = (args.out / args.procedural_dir).is_dir()
    if restyle_levels is not None and (proc_levels is not None or procedural_on_disk):
        manifest["restyle"] = {"dir": args.restyle_dir}
        manifest["levels"] = restyle_levels
    manifest_dir = getattr(args, "_manifest_dir", args.out)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "manifest.json").write_text(json.dumps(manifest) + "\n")
    print(f"  wrote {manifest_dir / 'manifest.json'} build={manifest['build']}", flush=True)



if __name__ == "__main__":
    main()
