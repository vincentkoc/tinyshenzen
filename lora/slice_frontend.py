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
way. Generated images are not committed.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import time
from pathlib import Path

import numpy as np
from PIL import Image

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
    cols, rows = grid["cols"], grid["rows"]
    levels = [{"z": 0, "cols": cols, "rows": rows, "tiles": pack_bits(present[0], cols, rows)}]
    z = 1
    z_cols, z_rows = cols, rows
    while z_cols > 1 or z_rows > 1:
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


def write_overview_from_z0(dest: Path, grid: dict, out_dir: Path, max_width: int = 2048, pad_rgb: tuple[int, int, int] = (0x1A, 0x1E, 0x1C)) -> dict:
    """Build overview.webp from z0 tiles, without a full-resolution canvas."""
    cols, rows = grid["cols"], grid["rows"]
    full_w, full_h = cols * TILE, rows * TILE
    pw = min(max_width, full_w)
    scale = pw / full_w
    ph = max(1, round(full_h * scale))
    tw = max(1, round(TILE * scale))
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
            with Image.open(path) as im:
                small = im.convert("RGB").resize((tw, max(1, round(im.height * scale))), Image.Resampling.LANCZOS)
            prev.paste(small, (int(tx * TILE * scale), int(ty * TILE * scale)))
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

    if args.repair:
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
    if (restyle_levels is not None or args.repair) and rdest.is_dir() and (pdest / "0").is_dir():
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
