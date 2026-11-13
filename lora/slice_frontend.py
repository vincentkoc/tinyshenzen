#!/usr/bin/env python3
"""Slice a full-resolution Futian stitch into the 512-px webp pyramid
tinyworlds-frontend expects (same grid as the pipeline's makeGrid).

Default: write the restyled stitch to `tiles-restyled/`, and optionally a
procedural pyramid from the 2048-px chunks to `tiles/`. The manifest points
at both so the viewer can switch. Generated images are not committed.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

TILE = 512
CHUNK_TILES = 4
CHUNK = TILE * CHUNK_TILES  # 2048


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


def _save_webp(im: Image.Image, path: Path, *, lossless: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if lossless:
        im.save(path, "WEBP", lossless=True, method=2, exact=True)
    else:
        im.save(path, "WEBP", quality=76, method=2)


def _almost_empty(arr: np.ndarray, pad_rgb: tuple[int, int, int]) -> bool:
    """True when the tile is uniformly the pad colour (or transparent)."""
    if arr.ndim == 3 and arr.shape[2] == 4:
        if int(arr[:, :, 3].max()) == 0:
            return True
        rgb = arr[:, :, :3]
    else:
        rgb = arr
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
    args = ap.parse_args()

    meta = json.loads((args.chunks / "meta.json").read_text())
    origin = meta["origin"]
    min_x, min_y = int(origin["x"]), int(origin["y"])
    width, height = int(meta["width"]), int(meta["height"])
    max_x, max_y = min_x + width, min_y + height
    grid = make_grid(min_x, min_y, max_x, max_y)
    print(
        f"slice_frontend: grid {grid['cols']}×{grid['rows']} tiles of {TILE}, "
        f"origin ({grid['ox']},{grid['oy']}), image origin ({min_x},{min_y})",
        flush=True,
    )

    args.out.mkdir(parents=True, exist_ok=True)
    restyle_levels = None
    proc_levels = None

    if not args.skip_restyle:
        print(f"  opening {args.image}", flush=True)
        with Image.open(args.image) as im:
            im = im.convert("RGB")
            if im.size != (width, height):
                raise SystemExit(f"image is {im.size}, meta says {width}×{height}")
            dest = args.out / args.restyle_dir
            dest.mkdir(parents=True, exist_ok=True)
            restyle_levels = slice_image(im, grid, (min_x, min_y), dest)
            overview = write_overview(im, args.out)
        print(f"  restyle pyramid → {dest}", flush=True)
    else:
        overview = {"src": "overview.webp", "width": 0, "height": 0}

    if not args.skip_procedural:
        # Assemble a full procedural image from 2048 chunks, then slice the same way.
        chunk = int(meta.get("chunk") or 2048)
        cols, rows = int(meta["cols"]), int(meta["rows"])
        print(f"  assembling procedural {width}×{height} from {cols}×{rows} chunks", flush=True)
        proc = Image.new("RGB", (width, height), (0x1A, 0x1E, 0x1C))
        cdir = args.chunks / "chunks"
        for row in range(rows):
            for col in range(cols):
                src = cdir / f"{col}_{row}.png"
                if not src.exists():
                    continue
                with Image.open(src) as im:
                    proc.paste(im.convert("RGB"), (col * chunk, row * chunk))
        dest = args.out / args.procedural_dir
        dest.mkdir(parents=True, exist_ok=True)
        proc_levels = slice_image(proc, grid, (min_x, min_y), dest)
        if args.skip_restyle:
            overview = write_overview(proc, args.out)
        del proc
        print(f"  procedural pyramid → {dest}", flush=True)

    geo = meta.get("geo") or {}
    manifest = {
        "name": "tinysz",
        "build": format(int(time.time() * 1000), "x")[-9:],
        "tileSize": TILE,
        "origin": {"x": grid["ox"], "y": grid["oy"]},
        "levels": restyle_levels or proc_levels,
        "bounds": {"minX": min_x, "minY": min_y, "maxX": max_x, "maxY": max_y},
        "start": {"lon": 114.0683, "lat": 22.5455, "scale": 0.85},
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
        # Restyle is the only (and default) layer, written to --restyle-dir, usually tiles/.
        manifest["levels"] = restyle_levels
    elif proc_levels is not None:
        manifest["levels"] = proc_levels
    (args.out / "manifest.json").write_text(json.dumps(manifest) + "\n")
    print(f"  wrote {args.out / 'manifest.json'} build={manifest['build']}", flush=True)


if __name__ == "__main__":
    main()
