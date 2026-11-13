#!/usr/bin/env python3
"""OpenAI restyle of a chunked city render, with resume, sharding and a feathered stitch.

Does not send third-party images. Uses the pipeline CLI (OPENAI_API_KEY already
configured for that client). Each 1024 window is checkpointed so a crash — or a
parallel worker on a disjoint --shard — can resume without paying twice.

Workers may share one --out directory: each shard writes only its own windows,
appends usage.jsonl under a file lock, and defers the stitch until every window
is on disk. A final --stitch-only pass merges them.

Usage (from the repo root, after lora/render_full.mjs):

    python3 lora/restyle_map.py \\
        --chunks /workspace/tinysz-nanshan \\
        --out /workspace/tinysz-nanshan-restyled \\
        --quality medium --shard 0/8 --concurrency 4

    python3 lora/restyle_map.py --chunks ... --out ... --stitch-only
"""

from __future__ import annotations

import argparse
import fcntl
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
QUALITIES = ("low", "medium", "high")


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


def parse_shard(spec: str) -> tuple[int, int]:
    """Parse `I/N` as a 0-based shard index and a shard count. 0 <= I < N."""
    if "/" not in spec:
        raise ValueError(f"bad --shard {spec!r}: expected I/N with 0 <= I < N")
    left, right = spec.split("/", 1)
    try:
        i, n = int(left), int(right)
    except ValueError as e:
        raise ValueError(f"bad --shard {spec!r}: expected I/N with 0 <= I < N") from e
    if n < 1 or i < 0 or i >= n:
        raise ValueError(f"bad --shard {spec!r}: need 0 <= I < N")
    return i, n


def parse_windows(spec: str) -> list[tuple[int, int]]:
    """Parse `x,y;x,y` (semicolon, comma or whitespace separated) window origins."""
    out: list[tuple[int, int]] = []
    for part in re.split(r"[;\s]+", spec.strip()):
        if not part:
            continue
        if "," not in part:
            raise ValueError(f"bad --windows entry {part!r}: expected x,y")
        xs, ys = part.split(",", 1)
        out.append((int(xs), int(ys)))
    return out


def select_tiles(
    ts: list[T.Tile],
    shard: tuple[int, int] | None = None,
    windows: list[tuple[int, int]] | None = None,
) -> list[T.Tile]:
    """Return a disjoint subset of `ts`. `--windows` wins over `--shard`."""
    if windows is not None:
        want = set(windows)
        selected = [t for t in ts if (t.x, t.y) in want]
        missing = want - {(t.x, t.y) for t in selected}
        if missing:
            sample = ", ".join(f"{x},{y}" for x, y in sorted(missing)[:6])
            raise ValueError(f"--windows not on the grid: {sample}")
        return selected
    if shard is not None:
        i, n = shard
        return [t for k, t in enumerate(ts) if k % n == i]
    return list(ts)


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.write(json.dumps(row) + "\n")
            fh.flush()
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def rebuild_summary(usage_path: Path) -> dict:
    summary = {"calls": 0, "usd": 0.0, "skipped": 0, "failed": 0, "fog": 0}
    if not usage_path.exists():
        return summary
    with usage_path.open() as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_SH)
        try:
            lines = fh.read().splitlines()
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    for line in lines:
        if not line.strip():
            continue
        row = json.loads(line)
        summary["calls"] += int(row.get("calls") or 0)
        summary["usd"] += float(row.get("usd") or 0)
        if row.get("skipped"):
            summary["skipped"] += 1
        if row.get("fog"):
            summary["fog"] += 1
        if row.get("failed"):
            summary["failed"] += 1
    return summary


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


def restyle_one(
    src: Path,
    dest: Path,
    log_path: Path,
    look: str,
    quality: str = "high",
    retries: int = 4,
) -> dict:
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
                "--quality",
                quality,
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


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chunks", type=Path, default=Path("/workspace/tinysz-futian"))
    ap.add_argument("--out", type=Path, default=Path("/workspace/tinysz-futian-restyled"))
    ap.add_argument("--preview", type=Path, default=Path("/workspace/tinyshenzen-futian-restyled-preview.jpg"))
    ap.add_argument("--full", type=Path, default=Path("/workspace/tinyshenzen-futian-restyled.png"))
    ap.add_argument("--look", default="pixelsg")
    ap.add_argument("--quality", choices=QUALITIES, default="high", help="OpenAI Images API quality (default: high)")
    ap.add_argument("--window", type=int, default=1024)
    ap.add_argument("--overlap", type=int, default=256)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--stitch-only", action="store_true")
    ap.add_argument("--count-only", action="store_true", help="count fog vs non-fog windows, no API calls")
    ap.add_argument("--limit", type=int, default=0, help="restyle at most N new windows (0 = all)")
    ap.add_argument("--no-skip-fog", action="store_true", help="also send near-empty fog windows to the API")
    ap.add_argument("--shard", default="", help="disjoint slice I/N (0-based, e.g. 0/8). Safe to share --out.")
    ap.add_argument("--windows", default="", help="explicit window origins: 'x,y;x,y'")
    args = ap.parse_args(argv)

    meta = load_meta(args.chunks)
    width, height = int(meta["width"]), int(meta["height"])
    ts = T.tiles_for(width, height, args.window, args.overlap)
    shard = parse_shard(args.shard) if args.shard else None
    windows = parse_windows(args.windows) if args.windows else None
    selected = select_tiles(ts, shard=shard, windows=windows)
    ckpt = args.out / "tiles"
    raw = args.out / "raw"
    logs = args.out / "logs"
    for d in (ckpt, raw, logs):
        d.mkdir(parents=True, exist_ok=True)

    label = f"shard {shard[0]}/{shard[1]}" if shard else ("windows" if windows is not None else "all")
    print(
        f"restyle_map: {width}×{height} in {len(ts)} windows, {label}={len(selected)}, "
        f"quality={args.quality}, concurrency {args.concurrency}",
        flush=True,
    )

    usage_path = args.out / "usage.jsonl"

    if args.count_only:
        fog = 0
        rows = []
        for t in selected:
            src = raw / f"{t.x}_{t.y}.png"
            if not src.exists():
                extract_window(args.chunks, meta, t).save(src)
            with Image.open(src) as im:
                is_fog = is_mostly_fog(im)
            if is_fog:
                fog += 1
            rows.append({"x": t.x, "y": t.y, "fog": is_fog})
        census = {
            "chunks": str(args.chunks),
            "city": meta.get("city"),
            "width": width,
            "height": height,
            "windows": len(selected),
            "fog": fog,
            "non_fog": len(selected) - fog,
            "quality": args.quality,
            "tiles": rows,
        }
        dest = args.out / "fog-census.json"
        dest.write_text(json.dumps({k: v for k, v in census.items() if k != "tiles"}, indent=2) + "\n")
        (args.out / "fog-census-windows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        print(
            f"restyle_map: count {census['non_fog']} non-fog / {census['windows']} windows "
            f"({census['fog']} fog skipped). Wrote {dest}",
            flush=True,
        )
        return

    if not args.stitch_only:
        pending: list[T.Tile] = []
        for t in selected:
            dest = ckpt / f"{t.x}_{t.y}.png"
            if dest.exists() and dest.stat().st_size > 1000:
                continue
            pending.append(t)
        if args.limit:
            pending = pending[: args.limit]
        print(f"  {len(pending)} to restyle, {len(selected) - len(pending)} already on disk", flush=True)

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
            result = restyle_one(src, dest, logs / f"{t.x}_{t.y}.log", args.look, quality=args.quality)
            if dest.exists() and not result.get("fog"):
                with Image.open(src) as proc, Image.open(dest) as rest:
                    composite_dark_fog(proc, rest).save(dest)
            result["x"], result["y"] = t.x, t.y
            result["quality"] = args.quality
            return result

        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            futs = {pool.submit(work, t): t for t in pending}
            done = 0
            for fut in as_completed(futs):
                t = futs[fut]
                try:
                    result = fut.result()
                except Exception as e:
                    append_jsonl(usage_path, {"failed": True, "x": t.x, "y": t.y, "error": str(e)[:400]})
                    print(f"  FAIL {t.x},{t.y}: {e}", flush=True)
                    raise
                append_jsonl(usage_path, result)
                done += 1
                running = rebuild_summary(usage_path)
                print(
                    f"  {done}/{len(pending)}  ({t.x},{t.y})  "
                    f"running {running['calls']} calls, ${running['usd']:.4f}",
                    flush=True,
                )

    summary = rebuild_summary(usage_path)
    shard_name = f"summary-shard-{shard[0]}-of-{shard[1]}.json" if shard else "summary.json"
    (args.out / shard_name).write_text(json.dumps(summary, indent=2) + "\n")
    # always refresh the combined summary from the locked jsonl, so parallel shards agree
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
