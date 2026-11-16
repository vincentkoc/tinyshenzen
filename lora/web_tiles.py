#!/usr/bin/env python3
"""Build the web copy of the restyle pyramid (free, idempotent, no API).

The restyle z0 tiles are written lossless (~250 KB each), which is right for
the polish passes but far too heavy to serve. This step reads the original
pyramid (`tiles-restyled/`, never modified) and writes a sibling pyramid
(`tiles-web/` by default) that the viewer loads instead:

- z0 is re-encoded with cwebp, lossy, at `QUALITY` with `-m 6 -sharp_yuv`;
- a z0 tile whose lossy copy falls below `PSNR_FLOOR` dB (fine 1-px checker
  dither, where 4:2:0 chroma smears the colours) is written near-lossless
  instead; a tile whose new copy is not smaller keeps the original bytes;
- z1 and up are already lossy (q76) and small, so they are hard-linked (or
  copied across filesystems) unchanged;
- outputs that are newer than their source under the same encoding stamp are
  skipped, tiles missing from the source are removed, so reruns are cheap.

The manifest is re-pointed at the web pyramid: `restyle.dir` becomes the web
dir, `restyle.encoding` records the parameters and the `source` dir, and
`build` is bumped so `?v=` busts caches. To switch back to the originals, set
`restyle.dir` to `restyle.encoding.source` (one line) and bump `build`.

    python3 lora/web_tiles.py --out tinyworlds-frontend/public/data/tinysz-core
    python3 lora/slice_frontend.py --out ... --web-tiles   # same thing

Rerun it after any polish pass or reslice, since those rewrite the manifest
back to `tiles-restyled`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from multiprocessing import Pool
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

WEB_VERSION = "web-1"
QUALITY = 80
METHOD = 6
SHARP_YUV = True
PSNR_FLOOR = 28.0
NEAR_LOSSLESS = 60
STAMP = ".encoding.json"
CWEBP_HELP = (
    "cwebp not found: put libwebp's cwebp on PATH or pass --cwebp / set CWEBP "
    "(https://storage.googleapis.com/downloads.webmproject.org/releases/webp/)"
)


def encoding_params() -> dict:
    """The parameters recorded in the stamp and in manifest.restyle.encoding."""
    return {
        "version": WEB_VERSION,
        "encoder": "cwebp",
        "quality": QUALITY,
        "method": METHOD,
        "sharp_yuv": SHARP_YUV,
        "psnr_floor": PSNR_FLOOR,
        "fallback": f"near_lossless {NEAR_LOSSLESS}",
        "upper_levels": "copied",
    }


def find_cwebp(explicit: str | None = None) -> str:
    for cand in (explicit, os.environ.get("CWEBP"), shutil.which("cwebp")):
        if cand and Path(cand).is_file() and os.access(cand, os.X_OK):
            return cand
    raise SystemExit(CWEBP_HELP)


def _psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(((a.astype(np.float64) - b.astype(np.float64)) ** 2).mean())
    return 99.0 if mse == 0 else float(10 * np.log10(255.0**2 / mse))


def encode_tile(cwebp: str, src: Path, out: Path) -> dict:
    """Encode one z0 tile to `out` (atomic). Returns the decision record."""
    with tempfile.TemporaryDirectory(prefix="webtile-") as td:
        im = Image.open(src).convert("RGB")
        png = Path(td) / "s.png"
        im.save(png)
        lossy = Path(td) / "q.webp"
        cmd = [cwebp, "-quiet", "-q", str(QUALITY), "-m", str(METHOD)]
        if SHARP_YUV:
            cmd.append("-sharp_yuv")
        subprocess.run(cmd + [str(png), "-o", str(lossy)], check=True)
        with Image.open(lossy) as li:
            p = _psnr(np.asarray(im), np.asarray(li.convert("RGB")))
        pick, mode = lossy, f"q{QUALITY}"
        if p < PSNR_FLOOR:
            nl = Path(td) / "n.webp"
            subprocess.run(
                [cwebp, "-quiet", "-near_lossless", str(NEAR_LOSSLESS), "-z", "6", str(png), "-o", str(nl)],
                check=True,
            )
            pick, mode = nl, f"near_lossless{NEAR_LOSSLESS}"
        orig = src.stat().st_size
        if pick.stat().st_size >= orig:
            pick, mode = src, "orig"
        tmp = out.with_name(out.name + ".tmp")
        shutil.copyfile(pick, tmp)
        os.replace(tmp, out)
    return {"tile": src.name, "mode": mode, "orig": orig, "bytes": out.stat().st_size, "psnr": round(p, 2)}


def _link_or_copy(src: Path, dst: Path) -> None:
    tmp = dst.with_name(dst.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    try:
        os.link(src, tmp)
    except OSError:
        shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def _up_to_date(src: Path, dst: Path) -> bool:
    if not dst.exists():
        return False
    s, d = src.stat(), dst.stat()
    if (s.st_ino, s.st_dev) == (d.st_ino, d.st_dev):
        return True
    return d.st_mtime >= s.st_mtime


def plan(source: Path, dest: Path, stamp_ok: bool) -> tuple[list[str], dict[int, list[str]], list[Path]]:
    """What to do: z0 names to encode, upper-level names to link per z, stale files to remove."""
    encode: list[str] = []
    link: dict[int, list[str]] = {}
    stale: list[Path] = []
    for zdir in sorted(p for p in source.iterdir() if p.is_dir() and p.name.isdigit()):
        z = int(zdir.name)
        names = sorted(n for n in os.listdir(zdir) if n.endswith(".webp") and not n.startswith("."))
        out = dest / zdir.name
        for n in names:
            src, dst = zdir / n, out / n
            if z == 0:
                if not stamp_ok or not _up_to_date(src, dst):
                    encode.append(n)
            elif not _up_to_date(src, dst) or dst.stat().st_size != src.stat().st_size:
                link.setdefault(z, []).append(n)
        if out.is_dir():
            keep = set(names)
            stale += [out / n for n in os.listdir(out) if n not in keep]
    for out in (p for p in dest.iterdir() if p.is_dir() and p.name.isdigit()) if dest.is_dir() else ():
        if not (source / out.name).is_dir():
            stale += sorted(out.iterdir())
    return encode, link, stale


_CTX: dict = {}


def _encode_job(name: str) -> dict:
    return _CTX["encode"](_CTX["cwebp"], _CTX["src0"] / name, _CTX["dst0"] / name)


def build_web_tiles(
    world: Path,
    source_dir: str = "tiles-restyled",
    web_dir: str = "tiles-web",
    *,
    cwebp: str | None = None,
    workers: int = 8,
    encode: Callable[[str, Path, Path], dict] = encode_tile,
    write_manifest: bool = True,
) -> dict:
    """Build `world/web_dir` from `world/source_dir` and re-point the manifest."""
    source, dest = world / source_dir, world / web_dir
    if not (source / "0").is_dir():
        raise SystemExit(f"no z0 tiles at {source / '0'}")
    if source.resolve() == dest.resolve():
        raise SystemExit("--web-dir must differ from the source dir; originals are never overwritten")
    exe = cwebp if encode is not encode_tile else find_cwebp(cwebp)
    params = encoding_params()
    stamp_path = dest / STAMP
    stamp = json.loads(stamp_path.read_text()) if stamp_path.exists() else {}
    stamp_ok = stamp.get("params") == params
    dest.mkdir(parents=True, exist_ok=True)
    for tmp in dest.rglob("*.tmp"):
        tmp.unlink()
    todo, link, stale = plan(source, dest, stamp_ok)
    # Mark the stamp first: a crash leaves outputs newer than their sources, so
    # a rerun under the same params resumes where it stopped.
    stamp_path.write_text(json.dumps({"params": params, "source": source_dir}, indent=1) + "\n")
    for p in stale:
        p.unlink()
    for z, names in link.items():
        (dest / str(z)).mkdir(parents=True, exist_ok=True)
        for n in names:
            _link_or_copy(source / str(z) / n, dest / str(z) / n)
    (dest / "0").mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    print(f"web_tiles: {len(todo)} z0 to encode, {sum(map(len, link.values()))} upper tiles linked, {len(stale)} stale removed", flush=True)
    _CTX.update(encode=encode, cwebp=exe, src0=source / "0", dst0=dest / "0")
    log = dest / ".encode-log.jsonl"
    with open(log, "a") as lg:
        if workers > 1 and len(todo) > 1:
            with Pool(workers) as pool:
                it = pool.imap_unordered(_encode_job, todo, chunksize=4)
                for i, r in enumerate(it, 1):
                    lg.write(json.dumps(r) + "\n")
                    counts[r["mode"]] = counts.get(r["mode"], 0) + 1
                    if i % 500 == 0:
                        print(f"  {i}/{len(todo)}", flush=True)
        else:
            for r in map(_encode_job, todo):
                lg.write(json.dumps(r) + "\n")
                counts[r["mode"]] = counts.get(r["mode"], 0) + 1
    z0 = [p for p in (dest / "0").iterdir() if p.suffix == ".webp"]
    summary = {"encoded": counts, "z0_tiles": len(z0), "z0_bytes": sum(p.stat().st_size for p in z0)}
    print(f"web_tiles: {summary}", flush=True)
    if write_manifest:
        changed = bool(todo or link or stale)
        summary["build"] = point_manifest(world / "manifest.json", web_dir, source_dir, params, bump=changed)
    return summary


def new_build_id() -> str:
    """Same scheme as slice_frontend's manifest writer."""
    return format(int(time.time() * 1000), "x")[-9:]


def point_manifest(path: Path, web_dir: str, source_dir: str, params: dict, *, bump: bool = False) -> str:
    """Point manifest.restyle at the web pyramid; bump `build` when it or the tiles changed."""
    m = json.loads(path.read_text())
    want = {"dir": web_dir, "encoding": {**params, "source": source_dir}}
    if m.get("restyle") != want or bump:
        m["restyle"] = want
        m["build"] = new_build_id()
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(m) + "\n")
        os.replace(tmp, path)
        print(f"  wrote {path} restyle.dir={web_dir} build={m['build']}", flush=True)
    return m["build"]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True, help="world data dir (has manifest.json)")
    ap.add_argument("--restyle-dir", default="tiles-restyled", help="original pyramid (read only)")
    ap.add_argument("--web-dir", default="tiles-web", help="web pyramid to write")
    ap.add_argument("--cwebp", default=None, help="path to cwebp (default: $CWEBP or PATH)")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--no-manifest", action="store_true", help="build the pyramid but leave manifest.json alone")
    a = ap.parse_args(argv)
    build_web_tiles(a.out, a.restyle_dir, a.web_dir, cwebp=a.cwebp, workers=a.workers, write_manifest=not a.no_manifest)


if __name__ == "__main__":
    main()
