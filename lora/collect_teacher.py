#!/usr/bin/env python3
"""Render procedural 1024×1024 windows and restyle them with the pipeline CLI.

Teacher targets are OpenAI `--look pixelsg` edits of OUR geometry. This script
never sends a third-party image as a style reference.

Images are written outside the repo (default: /workspace/tinysz-teacher) and
are not committed. Reruns skip work that is already on disk.

Usage (from the repo root, with OPENAI_API_KEY set):

    python3 lora/collect_teacher.py --out /workspace/tinysz-teacher
    python3 lora/collect_teacher.py --out /workspace/tinysz-teacher --limit 5 --priority
    python3 lora/collect_teacher.py --out /workspace/tinysz-teacher --sheet-only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "tinyworlds-pipeline"
MANIFEST = Path(__file__).resolve().parent / "teacher-manifest.json"
USAGE_RE = re.compile(
    r"restyle: usage\s+(\S+)\s+quality=(\S+)\s+text_in=(\d+)\s+image_in=(\d+)\s+image_out=(\d+)\s+~\$([0-9.]+)"
)


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text())


def run(cmd: list[str], log_path: Path, cwd: Path) -> str:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"\n$ {' '.join(cmd)}\n")
        fh.flush()
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=os.environ.copy(),
        )
        fh.write(proc.stdout)
        fh.flush()
    if proc.returncode != 0:
        raise SystemExit(f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stdout[-2000:]}")
    return proc.stdout


def parse_usage(text: str) -> list[dict]:
    rows = []
    for m in USAGE_RE.finditer(text):
        rows.append(
            {
                "size": m.group(1),
                "quality": m.group(2),
                "text_in": int(m.group(3)),
                "image_in": int(m.group(4)),
                "image_out": int(m.group(5)),
                "usd": float(m.group(6)),
            }
        )
    return rows


def preview(win: dict, dest: Path, size: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 1000:
        return
    out = run(
        [
            "node",
            "dist/cli.js",
            "--city",
            "tinysz",
            "preview",
            "--at",
            f"{win['lon']},{win['lat']}",
            "--size",
            f"{size},{size}",
            "--out",
            str(dest),
        ],
        dest.with_suffix(".log") if False else dest.parent.parent / "logs" / f"{win['id']}-preview.log",
        PIPELINE,
    )
    print(out.strip().splitlines()[-1] if out.strip() else f"previewed {dest.name}", flush=True)


def restyle(src: Path, dest: Path, log_path: Path, look: str) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 1000:
        # already done; cost is in the usage log if we wrote it
        return {"skipped": True, "usd": 0.0}
    text = run(
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
        log_path,
        PIPELINE,
    )
    print(text.strip().splitlines()[-1] if text.strip() else f"restyled {dest.name}", flush=True)
    usage = parse_usage(text)
    usd = sum(u["usd"] for u in usage)
    return {"skipped": False, "usd": usd, "usage": usage, "calls": max(1, len(usage))}


def make_sheet(out: Path, windows: list[dict], thumb: int = 256) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    cols = 5
    rows = (len(windows) + cols - 1) // cols
    pad, label_h, gap = 8, 22, 4
    cell_w = thumb * 2 + gap + pad * 2
    cell_h = thumb + label_h + pad * 2
    sheet_w = cols * cell_w
    sheet_h = rows * cell_h + 36
    sheet = Image.new("RGB", (sheet_w, sheet_h), (26, 30, 28))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
    except OSError:
        font = font_sm = ImageFont.load_default()
    draw.text((pad, 8), "Tiny Shenzhen teacher set  —  procedural (left)  |  OpenAI --look pixelsg (right)", fill=(220, 220, 210), font=font)

    proc_dir = out / "procedural"
    rest_dir = out / "restyled"
    for i, win in enumerate(windows):
        r, c = divmod(i, cols)
        x = c * cell_w + pad
        y = r * cell_h + 36 + pad
        pid = win["id"]
        p = proc_dir / f"{pid}.png"
        s = rest_dir / f"{pid}.png"
        for j, path in enumerate((p, s)):
            box = (x + j * (thumb + gap), y)
            if path.exists():
                im = Image.open(path).convert("RGB").resize((thumb, thumb), Image.Resampling.LANCZOS)
                sheet.paste(im, box)
            else:
                draw.rectangle([box[0], box[1], box[0] + thumb, box[1] + thumb], fill=(40, 44, 42), outline=(80, 80, 80))
                draw.text((box[0] + 8, box[1] + thumb // 2), "missing", fill=(160, 160, 160), font=font_sm)
        tags = ",".join(win.get("tags", []))
        draw.text((x, y + thumb + 4), f"{pid}  {tags}", fill=(200, 200, 190), font=font_sm)

    sheet_path = out / "sheet.jpg"
    sheet.save(sheet_path, "JPEG", quality=88, optimize=True)
    print(f"sheet: {sheet_path} {sheet.size[0]}×{sheet.size[1]}", flush=True)
    return sheet_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("/workspace/tinysz-teacher"))
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    ap.add_argument("--limit", type=int, default=0, help="restyle at most this many windows (0 = all)")
    ap.add_argument("--priority", action="store_true", help="restyle priority windows first")
    ap.add_argument("--skip-restyle", action="store_true")
    ap.add_argument("--sheet-only", action="store_true")
    ap.add_argument("--max-usd", type=float, default=0, help="stop restyle if running cost would exceed this (0 = no cap)")
    args = ap.parse_args()

    man = json.loads(args.manifest.read_text()) if args.manifest != MANIFEST else load_manifest()
    windows = list(man["windows"])
    if args.priority:
        windows.sort(key=lambda w: (0 if w.get("priority") else 1, w["id"]))
    size = int(man.get("size", [1024, 1024])[0])
    look = man.get("look", "pixelsg")
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    (out / "procedural").mkdir(exist_ok=True)
    (out / "restyled").mkdir(exist_ok=True)
    (out / "logs").mkdir(exist_ok=True)

    if args.sheet_only:
        make_sheet(out, man["windows"])
        return

    if not (PIPELINE / "dist" / "cli.js").exists():
        raise SystemExit(f"build the pipeline first: cd {PIPELINE} && npx tsc -p tsconfig.json")

    for win in man["windows"]:
        dest = out / "procedural" / f"{win['id']}.png"
        print(f"preview {win['id']} …", flush=True)
        preview(win, dest, size)

    usage_path = out / "usage.jsonl"
    summary = {"calls": 0, "usd": 0.0, "windows": []}
    if usage_path.exists():
        for line in usage_path.read_text().splitlines():
            if line.strip():
                try:
                    row = json.loads(line)
                    if not row.get("skipped"):
                        summary["calls"] += int(row.get("calls") or 1)
                        summary["usd"] += float(row.get("usd") or 0)
                except json.JSONDecodeError:
                    pass

    if not args.skip_restyle:
        done = 0
        for win in windows:
            if args.limit and done >= args.limit:
                break
            src = out / "procedural" / f"{win['id']}.png"
            dest = out / "restyled" / f"{win['id']}.png"
            already = dest.exists() and dest.stat().st_size > 1000
            if already:
                print(f"skip restyle {win['id']} (exists)", flush=True)
                continue
            if args.max_usd and summary["usd"] >= args.max_usd:
                print(f"stopping: cost ${summary['usd']:.4f} reached --max-usd {args.max_usd}", flush=True)
                break
            print(f"restyle {win['id']} …", flush=True)
            log_path = out / "logs" / f"{win['id']}-restyle.log"
            result = restyle(src, dest, log_path, look)
            result["id"] = win["id"]
            with usage_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(result) + "\n")
            if not result.get("skipped"):
                summary["calls"] += int(result.get("calls") or 1)
                summary["usd"] += float(result.get("usd") or 0)
                done += 1
            print(f"  running total: {summary['calls']} calls, ${summary['usd']:.4f}", flush=True)

    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    make_sheet(out, man["windows"])
    print(f"done: {summary['calls']} calls, ${summary['usd']:.4f}", flush=True)


if __name__ == "__main__":
    main()
