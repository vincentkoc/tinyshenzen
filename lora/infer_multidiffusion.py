#!/usr/bin/env python3
"""MultiDiffusion / tiled LoRA inference over a chunked (or single) render.

Default backend is musubi-tuner's Qwen-Image-Edit-2511 generator with our LoRA
on each overlapping 1024 window, then the same smooth-step hem as restyle.ts.
An `identity` backend copies the source (used by tests, and to debug stitch).

This is the open-model path. It does not call OpenAI and does not start a paid
GPU job — run it on a machine that already has a GPU and the weights.

Usage:

    python3 lora/infer_multidiffusion.py \
        --chunks /workspace/tinysz-futian \
        --lora lora/output/tinysz-pixelsg-edit.safetensors \
        --musubi $HOME/musubi-tuner \
        --models lora/models \
        --out /workspace/tinysz-futian-lora \
        --backend musubi

    python3 lora/infer_multidiffusion.py --chunks ... --backend identity --stitch-only
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image

import prepare_dataset
import tiles as T
from blend import stitch_bands
from restyle_map import extract_window, load_meta

Image.MAX_IMAGE_PIXELS = None


def run_musubi(
    src: Path,
    dest: Path,
    args: argparse.Namespace,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    script = Path(args.musubi) / "src" / "musubi_tuner" / "qwen_image_generate_image.py"
    if not script.exists():
        raise SystemExit(f"musubi generate script not found: {script}")
    cmd = [
        sys.executable,
        str(script),
        "--dit",
        str(Path(args.models) / "qwen_image_edit_2511_bf16.safetensors"),
        "--vae",
        str(Path(args.models) / "qwen_image_vae.safetensors"),
        "--text_encoder",
        str(Path(args.models) / "qwen_2.5_vl_7b.safetensors"),
        "--model_version",
        "edit-2511",
        "--control_image_path",
        str(src),
        "--prompt",
        prepare_dataset.CAPTION,
        "--image_size",
        "1024",
        "1024",
        "--infer_steps",
        str(args.steps),
        "--guidance_scale",
        str(args.guidance),
        "--network_module",
        "networks.lora_qwen_image",
        "--network_weights",
        str(args.lora),
        "--network_mul",
        str(args.strength),
        "--fp8_base",
        "--fp8_scaled",
        "--blocks_to_swap",
        str(args.blocks_to_swap),
        "--save_path",
        str(dest),
        "--seed",
        str(args.seed),
    ]
    if args.extra:
        cmd.extend(args.extra)
    print("  $ " + " ".join(cmd[:8]) + " …", flush=True)
    proc = subprocess.run(cmd, cwd=args.musubi)
    if proc.returncode != 0 or not dest.exists():
        raise SystemExit(f"musubi generate failed for {src} (exit {proc.returncode})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chunks", type=Path, default=Path("/workspace/tinysz-futian"))
    ap.add_argument("--out", type=Path, default=Path("/workspace/tinysz-futian-lora"))
    ap.add_argument("--preview", type=Path, default=Path("/workspace/tinyshenzen-futian-lora-preview.jpg"))
    ap.add_argument("--full", type=Path, default=Path("/workspace/tinyshenzen-futian-lora.png"))
    ap.add_argument("--window", type=int, default=1024)
    ap.add_argument("--overlap", type=int, default=256)
    ap.add_argument("--backend", choices=("musubi", "identity"), default="musubi")
    ap.add_argument("--lora", type=Path, default=Path("lora/output/tinysz-pixelsg-edit.safetensors"))
    ap.add_argument("--musubi", type=Path, default=Path(os.environ.get("MUSUBI_DIR", os.path.expanduser("~/musubi-tuner"))))
    ap.add_argument("--models", type=Path, default=Path("lora/models"))
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--guidance", type=float, default=4.0)
    ap.add_argument("--strength", type=float, default=1.0)
    ap.add_argument("--blocks-to-swap", dest="blocks_to_swap", type=int, default=16)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--stitch-only", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--extra", nargs="*", default=[])
    args = ap.parse_args()

    meta = load_meta(args.chunks)
    width, height = int(meta["width"]), int(meta["height"])
    ts = T.tiles_for(width, height, args.window, args.overlap)
    ckpt = args.out / "tiles"
    raw = args.out / "raw"
    ckpt.mkdir(parents=True, exist_ok=True)
    raw.mkdir(parents=True, exist_ok=True)
    print(f"infer: {width}×{height} in {len(ts)} windows, backend={args.backend}", flush=True)

    if not args.stitch_only:
        pending = []
        for t in ts:
            dest = ckpt / f"{t.x}_{t.y}.png"
            if dest.exists() and dest.stat().st_size > 1000:
                continue
            pending.append(t)
        if args.limit:
            pending = pending[: args.limit]
        print(f"  {len(pending)} to draw, {len(ts) - len(pending)} on disk", flush=True)
        for i, t in enumerate(pending, 1):
            src = raw / f"{t.x}_{t.y}.png"
            dest = ckpt / f"{t.x}_{t.y}.png"
            if not src.exists():
                extract_window(args.chunks, meta, t).save(src)
            if args.backend == "identity":
                Image.open(src).convert("RGB").save(dest)
            else:
                run_musubi(src, dest, args)
            print(f"  {i}/{len(pending)} ({t.x},{t.y})", flush=True)

    have = [t for t in ts if (ckpt / f"{t.x}_{t.y}.png").exists()]
    if len(have) < len(ts):
        print(f"stitch deferred: {len(have)}/{len(ts)} tiles. Re-run to finish.", flush=True)
        return
    paths = {(t.x, t.y): ckpt / f"{t.x}_{t.y}.png" for t in ts}
    stitch_bands(ts, paths, width, height, overlap=args.overlap, out_path=args.full, preview_path=args.preview)


if __name__ == "__main__":
    main()
