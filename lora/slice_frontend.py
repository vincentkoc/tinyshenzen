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
from source.
Higher zoom levels are rebuilt with viewer-fog padding so missing
children do not become dark squares. Generated images are not
committed.
"""

from __future__ import annotations

import argparse
import json
import math
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


def write_overview_from_z0(dest: Path, grid: dict, out_dir: Path, max_width: int = 2048, pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C)) -> dict:
    """Build overview.webp from z0 tiles, without a full-resolution canvas.

    Each tile is resized to the rounded span between this cell and the
    next so neighbours abut. A single shared width plus a truncated
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
            with Image.open(path) as im:
                small = im.convert("RGB").resize((tw, th), Image.Resampling.LANCZOS)
            prev.paste(small, (x0, y0))
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", type=Path, default=Path("/workspace/tinyshenzen-futian-restyled.png"))
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
    args = ap.parse_args()

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

    if args.polish or args.polish_edge or args.polish_vignette or args.pyramid_only:
        dest = args.out / args.restyle_dir
        if not (dest / "0").is_dir():
            raise SystemExit(f"--polish/--polish-edge/--polish-vignette/--pyramid-only needs existing z0 tiles at {dest / '0'}")
        pdest = args.out / args.procedural_dir
        proc_dest = pdest if (pdest / "0").is_dir() else None
        if args.polish:
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
    (args.out / "manifest.json").write_text(json.dumps(manifest) + "\n")
    print(f"  wrote {args.out / 'manifest.json'} build={manifest['build']}", flush=True)



if __name__ == "__main__":
    main()
