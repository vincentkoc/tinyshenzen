#!/usr/bin/env python3
"""OpenAI --look pixelsg restyle of a chunked Futian render, with resume and feathered stitch.

Does not send third-party images. Uses the pipeline CLI (OPENAI_API_KEY already
configured for that client). Each 1024 window is checkpointed so a crash can
resume without paying twice.

Usage (from the repo root, after lora/render_full.mjs):

    python3 lora/restyle_map.py \
        --chunks /workspace/tinysz-futian \
        --out /workspace/tinysz-futian-restyled \
        --preview /workspace/tinyshenzen-futian-restyled-preview.jpg \
        --concurrency 4
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image

import tiles as T
from blend import stitch_bands

Image.MAX_IMAGE_PIXELS = None

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "tinyworlds-pipeline"
USAGE_RE = re.compile(
    r"restyle: usage\s+(\S+)\s+quality=(\S+)\s+text_in=(\d+)\s+image_in=(\d+)\s+image_out=(\d+)\s+~\$([0-9.]+)"
)


def edge_energy(im: Image.Image, size: int = 128) -> float:
    """Mean absolute luminance gradient. Pixel-art city is sharp (~18–35); fog is smooth (~0–4)."""
    lum = np.asarray(im.convert("L").resize((size, size)), dtype=np.float32)
    dx = np.abs(np.diff(lum, axis=1, prepend=lum[:, :1]))
    dy = np.abs(np.diff(lum, axis=0, prepend=lum[:1, :]))
    return float((dx + dy).mean())


def is_mostly_fog(im: Image.Image, edge_min: float = 8.0) -> bool:
    """True for empty or smooth fog windows. Dark renderer fog is smooth; pavement and
    buildings are sharp. A low threshold would send fog to the API, which invents a city."""
    return edge_energy(im) < edge_min


def composite_dark_fog(procedural: Image.Image, restyled: Image.Image, lum_max: float = 48.0, feather: int = 16) -> Image.Image:
    """Keep the renderer's dark fog; use the restyle everywhere else (not on pale pavement)."""
    from PIL import ImageFilter
    proc = procedural.convert("RGB")
    rest = restyled.convert("RGB").resize(proc.size, Image.Resampling.NEAREST)
    lum = np.asarray(proc, dtype=np.float32).mean(axis=2)
    core = (lum < lum_max).astype(np.uint8) * 255
    w = np.asarray(Image.fromarray(core, "L").filter(ImageFilter.GaussianBlur(feather / 2)), dtype=np.float32) / 255.0
    out = np.asarray(rest, dtype=np.float32) * (1 - w[:, :, None]) + np.asarray(proc, dtype=np.float32) * w[:, :, None]
    return Image.fromarray(out.clip(0, 255).astype(np.uint8), "RGB")



def load_meta(chunks: Path) -> dict:
    return json.loads((chunks / "meta.json").read_text())


def extract_window(chunks: Path, meta: dict, tile: T.Tile) -> Image.Image:
    chunk = int(meta["chunk"])
    canvas = Image.new("RGBA", (tile.w, tile.h), (0xE9, 0xE6, 0xDF, 255))
    c0, r0 = tile.x // chunk, tile.y // chunk
    c1, r1 = (tile.x + tile.w - 1) // chunk, (tile.y + tile.h - 1) // chunk
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            path = chunks / "chunks" / f"{c}_{r}.png"
            if not path.exists():
                continue
            with Image.open(path) as im:
                canvas.paste(im.convert("RGBA"), (c * chunk - tile.x, r * chunk - tile.y))
    return canvas


def parse_usage(text: str) -> dict | None:
    m = USAGE_RE.search(text)
    if not m:
        return None
    return {
        "size": m.group(1),
        "quality": m.group(2),
        "text_in": int(m.group(3)),
        "image_in": int(m.group(4)),
        "image_out": int(m.group(5)),
        "usd": float(m.group(6)),
    }


def restyle_one(src: Path, dest: Path, log_path: Path, look: str, retries: int = 4) -> dict:
    if dest.exists() and dest.stat().st_size > 1000:
        return {"skipped": True, "usd": 0.0, "calls": 0, "path": str(dest)}
    dest.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    last_err = ""
    for attempt in range(1, retries + 1):
        proc = subprocess.run(
            [
                "node",
                "dist/cli.js",
                "restyle",
                "--in",
                str(src),
                "--out",
                str(dest),
                "--window",
                "1024",
                "--look",
                look,
            ],
            cwd=PIPELINE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=os.environ.copy(),
        )
        log_path.write_text(proc.stdout)
        if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 1000:
            usage = parse_usage(proc.stdout) or {}
            return {"skipped": False, "usd": usage.get("usd", 0.0), "calls": 1, "usage": usage, "path": str(dest)}
        last_err = proc.stdout[-1500:]
        # never keep a key in our own summary; the CLI already redacts
        if re.search(r"sk-[A-Za-z0-9_-]{8,}", last_err):
            last_err = USAGE_RE.sub("restyle: usage [redacted]", last_err)
            last_err = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "[redacted]", last_err)
        wait = min(120, 8 * attempt)
        print(f"  retry {attempt}/{retries} for {dest.name} in {wait}s (exit {proc.returncode})", flush=True)
        time.sleep(wait)
    raise SystemExit(f"restyle failed for {src.name} after {retries} attempts:\n{last_err}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chunks", type=Path, default=Path("/workspace/tinysz-futian"))
    ap.add_argument("--out", type=Path, default=Path("/workspace/tinysz-futian-restyled"))
    ap.add_argument("--preview", type=Path, default=Path("/workspace/tinyshenzen-futian-restyled-preview.jpg"))
    ap.add_argument("--full", type=Path, default=Path("/workspace/tinyshenzen-futian-restyled.png"))
    ap.add_argument("--look", default="pixelsg")
    ap.add_argument("--window", type=int, default=1024)
    ap.add_argument("--overlap", type=int, default=256)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--stitch-only", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="restyle at most N new windows (0 = all)")
    ap.add_argument("--no-skip-fog", action="store_true", help="also send near-empty fog windows to the API")
    args = ap.parse_args()

    meta = load_meta(args.chunks)
    width, height = int(meta["width"]), int(meta["height"])
    ts = T.tiles_for(width, height, args.window, args.overlap)
    ckpt = args.out / "tiles"
    raw = args.out / "raw"
    logs = args.out / "logs"
    for d in (ckpt, raw, logs):
        d.mkdir(parents=True, exist_ok=True)

    print(f"restyle_map: {width}×{height} in {len(ts)} windows, concurrency {args.concurrency}", flush=True)

    usage_path = args.out / "usage.jsonl"
    summary = {"calls": 0, "usd": 0.0, "skipped": 0, "failed": 0}
    if usage_path.exists():
        for line in usage_path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            summary["calls"] += int(row.get("calls") or 0)
            summary["usd"] += float(row.get("usd") or 0)
            if row.get("skipped"):
                summary["skipped"] += 1

    if not args.stitch_only:
        pending: list[T.Tile] = []
        for t in ts:
            dest = ckpt / f"{t.x}_{t.y}.png"
            if dest.exists() and dest.stat().st_size > 1000:
                continue
            pending.append(t)
        if args.limit:
            pending = pending[: args.limit]
        print(f"  {len(pending)} to restyle, {len(ts) - len(pending)} already on disk", flush=True)

        def work(t: T.Tile) -> dict:
            src = raw / f"{t.x}_{t.y}.png"
            dest = ckpt / f"{t.x}_{t.y}.png"
            if not src.exists():
                extract_window(args.chunks, meta, t).save(src)
            if not args.no_skip_fog:
                with Image.open(src) as im:
                    fog = is_mostly_fog(im)
                if fog:
                    Image.open(src).convert("RGB").save(dest)
                    return {"skipped": True, "fog": True, "usd": 0.0, "calls": 0, "x": t.x, "y": t.y, "path": str(dest)}
            result = restyle_one(src, dest, logs / f"{t.x}_{t.y}.log", args.look)
            if dest.exists() and not result.get("fog"):
                with Image.open(src) as proc, Image.open(dest) as rest:
                    composite_dark_fog(proc, rest).save(dest)
            result["x"], result["y"] = t.x, t.y
            return result

        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            futs = {pool.submit(work, t): t for t in pending}
            done = 0
            for fut in as_completed(futs):
                t = futs[fut]
                try:
                    result = fut.result()
                except Exception as e:
                    summary["failed"] += 1
                    print(f"  FAIL {t.x},{t.y}: {e}", flush=True)
                    raise
                with usage_path.open("a") as fh:
                    fh.write(json.dumps(result) + "\n")
                if result.get("skipped"):
                    summary["skipped"] += 1
                else:
                    summary["calls"] += int(result.get("calls") or 1)
                    summary["usd"] += float(result.get("usd") or 0)
                done += 1
                print(
                    f"  {done}/{len(pending)}  ({t.x},{t.y})  "
                    f"running {summary['calls']} calls, ${summary['usd']:.4f}",
                    flush=True,
                )

    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"restyle_map: {summary['calls']} calls, ${summary['usd']:.4f}", flush=True)

    have = sum(1 for t in ts if (ckpt / f"{t.x}_{t.y}.png").exists())
    if have < len(ts):
        print(f"stitch deferred: {have}/{len(ts)} tiles present. Re-run to finish, then --stitch-only.", flush=True)
        return

    paths = {(t.x, t.y): ckpt / f"{t.x}_{t.y}.png" for t in ts}
    stitch_bands(
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
