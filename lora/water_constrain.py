#!/usr/bin/env python3
"""Option (a): after a tiled restyle, put procedural pixels back wherever the
restyle invented water on land, or paved over real water, using a mask from
the renderer's ground kinds. Feather the seam. No API calls.

The raw mask marks every unpainted cell as water (ChunkRenderer's default).
Fog covers that default outside the city, so a pixel is "real water" only
when the mask says water AND the procedural image itself looks like water.
Restyle-water is teal/cyan/blue AND locally smooth, so window-grid glass is
left alone.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

import blend
import restyle_map
import tiles as T

Image.MAX_IMAGE_PIXELS = None

# Teal through cyan-blue, covering sgWater (#2f6d58, hue ~160) and foam (#55a0be, ~197).
HUE_LO, HUE_HI = 80.0, 210.0
SAT_MIN = 0.18
VAL_LO, VAL_HI = 0.12, 0.85
# Mean abs-luminance gradient below this is "smooth" (water); glass window grids are higher.
EDGE_MAX = 6.0
FEATHER = 16


def rgb_to_hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Hue in degrees [0, 360), saturation and value in [0, 1]."""
    x = rgb.astype(np.float32) / 255.0
    r, g, b = x[..., 0], x[..., 1], x[..., 2]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    df = mx - mn
    hue = np.zeros_like(mx)
    nz = df > 1e-6
    rd = np.zeros_like(mx)
    rd[nz] = (g[nz] - b[nz]) / df[nz]
    gd = np.zeros_like(mx)
    gd[nz] = (b[nz] - r[nz]) / df[nz]
    bd = np.zeros_like(mx)
    bd[nz] = (r[nz] - g[nz]) / df[nz]
    hue = np.where(nz & (mx == r), (rd % 6.0) * 60.0, hue)
    hue = np.where(nz & (mx == g) & (mx != r), (gd + 2.0) * 60.0, hue)
    hue = np.where(nz & (mx == b) & (mx != r) & (mx != g), (bd + 4.0) * 60.0, hue)
    hue = np.mod(hue, 360.0)
    sat = np.where(mx > 1e-6, df / np.maximum(mx, 1e-6), 0.0)
    return hue, sat, mx


def edge_map(rgb: np.ndarray) -> np.ndarray:
    """Per-pixel mean abs luminance gradient, a little blurred so a window grid reads high."""
    lum = rgb.astype(np.float32) @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    gy, gx = np.gradient(lum)
    e = (np.abs(gx) + np.abs(gy)).astype(np.float32)
    # 3×3 mean
    k = np.array([[1, 1, 1], [1, 1, 1], [1, 1, 1]], dtype=np.float32) / 9.0
    pad = np.pad(e, 1, mode="edge")
    acc = (
        k[0, 0] * pad[0:-2, 0:-2]
        + k[0, 1] * pad[0:-2, 1:-1]
        + k[0, 2] * pad[0:-2, 2:]
        + k[1, 0] * pad[1:-1, 0:-2]
        + k[1, 1] * pad[1:-1, 1:-1]
        + k[1, 2] * pad[1:-1, 2:]
        + k[2, 0] * pad[2:, 0:-2]
        + k[2, 1] * pad[2:, 1:-1]
        + k[2, 2] * pad[2:, 2:]
    )
    return acc


def looks_like_water(rgb: np.ndarray, require_smooth: bool = True, edge: np.ndarray | None = None) -> np.ndarray:
    hue, sat, val = rgb_to_hsv(rgb)
    in_hue = (hue >= HUE_LO) & (hue <= HUE_HI)
    body = in_hue & (sat >= SAT_MIN) & (val >= VAL_LO) & (val <= VAL_HI)
    if not require_smooth:
        return body
    if edge is None:
        edge = edge_map(rgb)
    return body & (edge <= EDGE_MAX)


def real_water(mask: np.ndarray, procedural: np.ndarray) -> np.ndarray:
    """Ground-kind water that the renderer actually painted as water, not fog-covered default."""
    return (mask > 127) & looks_like_water(procedural, require_smooth=False)


def feather_alpha(binary: np.ndarray, radius: int = FEATHER) -> np.ndarray:
    """0–1 alpha, 1 on the core of `binary`, smooth-stepped over `radius` pixels."""
    if radius <= 0:
        return binary.astype(np.float32)
    im = Image.fromarray((binary.astype(np.uint8)) * 255, "L")
    # Dilate slightly so the core stays solid, then Gaussian-blur the edge.
    grown = im.filter(ImageFilter.MaxFilter(size=3))
    blurred = grown.filter(ImageFilter.GaussianBlur(radius=max(1.0, radius / 2.0)))
    a = np.asarray(blurred, dtype=np.float32) / 255.0
    # Keep a firm 1 on the original core.
    return np.maximum(a, binary.astype(np.float32))


def large_regions(binary: np.ndarray, min_size: int = 200) -> np.ndarray:
    """Keep 8-connected components of at least `min_size` pixels. Pure numpy."""
    if min_size <= 1:
        return binary
    img = np.asarray(binary, dtype=np.uint8)
    h, w = img.shape
    # Flattened union-find over on-pixels. Neighbours already processed: W, NW, N, NE.
    n = h * w
    parent = np.arange(n, dtype=np.int32)
    on = img.reshape(-1) > 0

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for y in range(h):
        row = y * w
        for x in range(w):
            i = row + x
            if not on[i]:
                continue
            if x > 0 and on[i - 1]:
                union(i, i - 1)
            if y > 0 and on[i - w]:
                union(i, i - w)
            if y > 0 and x > 0 and on[i - w - 1]:
                union(i, i - w - 1)
            if y > 0 and x + 1 < w and on[i - w + 1]:
                union(i, i - w + 1)

    for i in range(n):
        if on[i]:
            parent[i] = find(i)
    counts = np.bincount(parent[on], minlength=n)
    keep = counts >= min_size
    out = np.zeros(n, dtype=bool)
    out[on] = keep[parent[on]]
    return out.reshape(h, w)


def constrain(
    procedural: Image.Image,
    restyled: Image.Image,
    mask: Image.Image | np.ndarray,
    feather: int = FEATHER,
    min_region: int = 200,
) -> tuple[Image.Image, dict]:
    """Composite procedural pixels over false water and missing water. Returns the
    image and a small report (pixel counts)."""
    proc = np.asarray(procedural.convert("RGB"))
    rest = np.asarray(restyled.convert("RGB"))
    if proc.shape != rest.shape:
        raise ValueError(f"size mismatch: procedural {proc.shape} vs restyle {rest.shape}")
    if isinstance(mask, Image.Image):
        m = np.asarray(mask.convert("L"))
    else:
        m = np.asarray(mask)
    if m.shape[:2] != proc.shape[:2]:
        raise ValueError(f"mask size {m.shape} vs image {proc.shape}")

    gt = real_water(m, proc)
    # No smoothness gate: restyle water is often a textured pixel-art wash, and
    # glass is already teal in the procedural image, which we exclude below.
    rest_water = looks_like_water(rest, require_smooth=False)
    proc_water = looks_like_water(proc, require_smooth=False)
    false_water = large_regions(rest_water & ~gt & ~proc_water, min_region)
    rest_edge = edge_map(rest)
    missing_water = gt & ~rest_water & (rest_edge <= EDGE_MAX * 2.5)
    missing_water = large_regions(missing_water, min_region)
    fix = false_water | missing_water
    alpha = feather_alpha(fix, feather)
    out = rest.astype(np.float32) * (1.0 - alpha[..., None]) + proc.astype(np.float32) * alpha[..., None]
    image = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")
    n = int(proc.shape[0] * proc.shape[1])
    report = {
        "pixels": n,
        "false_water": int(false_water.sum()),
        "missing_water": int(missing_water.sum()),
        "fixed": int(fix.sum()),
        "frac": float(fix.sum()) / n if n else 0.0,
        "gt_water": int(gt.sum()),
        "restyle_water": int(rest_water.sum()),
    }
    return image, report


def load_mask(path: Path, width: int, height: int, x: int, y: int, w: int, h: int) -> np.ndarray:
    """Crop a window from a raw uint8 mask or a PNG."""
    raw = path.with_suffix(".raw") if path.suffix.lower() == ".png" else path
    if raw.exists() and raw.suffix.lower() == ".raw":
        mm = np.memmap(raw, dtype=np.uint8, mode="r", shape=(height, width))
        crop = np.array(mm[y : y + h, x : x + w])
        return crop
    with Image.open(path) as im:
        return np.asarray(im.convert("L").crop((x, y, x + w, y + h)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chunks", type=Path, default=Path("/workspace/tinysz-futian"))
    ap.add_argument("--restyled", type=Path, default=Path("/workspace/tinysz-futian-restyled"))
    ap.add_argument("--mask", type=Path, default=Path("/workspace/tinysz-futian/water_mask.png"))
    ap.add_argument("--out-tiles", type=Path, default=Path("/workspace/tinysz-futian-restyled/tiles-constrained"))
    ap.add_argument("--preview", type=Path, default=Path("/workspace/tinyshenzen-futian-restyled-preview.jpg"))
    ap.add_argument("--full", type=Path, default=Path("/workspace/tinyshenzen-futian-restyled.png"))
    ap.add_argument("--window", type=int, default=1024)
    ap.add_argument("--overlap", type=int, default=256)
    ap.add_argument("--feather", type=int, default=FEATHER)
    ap.add_argument("--min-frac", type=float, default=0.01, help="report a window if fixed fraction exceeds this")
    ap.add_argument("--stitch-only", action="store_true")
    args = ap.parse_args()

    meta = restyle_map.load_meta(args.chunks)
    width, height = int(meta["width"]), int(meta["height"])
    ts = T.tiles_for(width, height, args.window, args.overlap)
    src_tiles = args.restyled / "tiles"
    raw = args.restyled / "raw"
    dest = args.out_tiles
    dest.mkdir(parents=True, exist_ok=True)

    if not args.mask.exists() and not args.mask.with_suffix(".raw").exists():
        sys.exit(f"water mask not found: {args.mask} (run lora/render_water_mask.mjs first)")

    reports = []
    if not args.stitch_only:
        for i, t in enumerate(ts, 1):
            rest_path = src_tiles / f"{t.x}_{t.y}.png"
            proc_path = raw / f"{t.x}_{t.y}.png"
            out_path = dest / f"{t.x}_{t.y}.png"
            if not rest_path.exists():
                print(f"  skip missing restyle {t.x},{t.y}", flush=True)
                continue
            if not proc_path.exists():
                restyle_map.extract_window(args.chunks, meta, t).save(proc_path)
            mask = load_mask(args.mask, width, height, t.x, t.y, t.w, t.h)
            with Image.open(proc_path) as proc, Image.open(rest_path) as rest:
                proc = proc.convert("RGB").resize((t.w, t.h), Image.Resampling.NEAREST)
                rest = rest.convert("RGB").resize((t.w, t.h), Image.Resampling.NEAREST)
                out, report = constrain(proc, rest, mask, feather=args.feather)
            out.save(out_path)
            report.update({"x": t.x, "y": t.y, "path": str(out_path)})
            reports.append(report)
            flag = " *" if report["frac"] >= args.min_frac else ""
            if i % 20 == 0 or flag:
                print(
                    f"  {i}/{len(ts)} ({t.x},{t.y})  "
                    f"false={report['false_water']} missing={report['missing_water']} "
                    f"frac={report['frac']:.3f}{flag}",
                    flush=True,
                )

        affected = [r for r in reports if r["frac"] >= args.min_frac]
        summary = {
            "windows": len(reports),
            "affected": len(affected),
            "min_frac": args.min_frac,
            "false_water_pixels": int(sum(r["false_water"] for r in reports)),
            "missing_water_pixels": int(sum(r["missing_water"] for r in reports)),
            "affected_windows": [
                {"x": r["x"], "y": r["y"], "false_water": r["false_water"], "missing_water": r["missing_water"], "frac": round(r["frac"], 4)}
                for r in sorted(affected, key=lambda r: -r["frac"])
            ],
            "openai_calls": 0,
            "usd": 0.0,
        }
        (args.restyled / "water_constrain.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(
            f"water_constrain: {len(affected)}/{len(reports)} windows above {args.min_frac:.0%} "
            f"({summary['false_water_pixels']} false, {summary['missing_water_pixels']} missing). $0.",
            flush=True,
        )

    have = sum(1 for t in ts if (dest / f"{t.x}_{t.y}.png").exists())
    if have < len(ts):
        print(f"stitch deferred: {have}/{len(ts)} constrained tiles", flush=True)
        return

    paths = {(t.x, t.y): dest / f"{t.x}_{t.y}.png" for t in ts}
    blend.stitch_bands(
        ts,
        paths,
        width,
        height,
        overlap=args.overlap,
        out_path=args.full,
        preview_path=args.preview,
        preview_max_width=4096,
    )


if __name__ == "__main__":
    main()
