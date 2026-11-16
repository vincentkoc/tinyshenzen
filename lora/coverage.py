"""Fade the tile pyramids out along a coverage outline (free, no API).

The pipeline's `coverage` command writes a city's outline as GeoJSON
(`npm run cli -- --city <id> coverage --out FILE`). This pass projects it
onto the z0 grid and, for every z0 tile in a layer:

- leaves tiles well inside the outline alone;
- fades tiles that cross it toward the viewer's paper with the same
  smoothstep ramp the vignette uses, its isoline wobbled by the shared
  low-frequency noise, so the outline reads as the map's own soft edge;
- drops tiles that end up all paper to their edges, so they leave the
  manifest's bitset without a step against a faded neighbour.

Every tile it changes or drops is first copied to a backup directory
(`<backup>/<layer>/0/`); a re-run starts from those copies, so it is
idempotent and the originals are never lost. Run it after every other
polish pass and before `--web-tiles`.
"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

EARTH_RADIUS = 6371008.8
COVERAGE_VERSION = "coverage-1"
COVERAGE_INSET = 32  # z0 px inside the outline where the fade starts
COVERAGE_FADE = 320  # z0 px outside the outline where it reaches paper
COVERAGE_NOISE = 32.0  # isoline wobble amplitude, z0 px
COVERAGE_NOISE_PERIOD = 320.0
COVERAGE_STEP = 8  # the distance field is sampled every this many px, then interpolated


def load_rings(path: Path) -> list[np.ndarray]:
    """All rings of a GeoJSON (Multi)Polygon, Feature or FeatureCollection, as [lon, lat] arrays."""
    data = json.loads(Path(path).read_text())
    geoms = []
    if data.get("type") == "FeatureCollection":
        geoms = [f["geometry"] for f in data["features"]]
    elif data.get("type") == "Feature":
        geoms = [data["geometry"]]
    else:
        geoms = [data]
    rings: list[np.ndarray] = []
    for g in geoms:
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        for poly in polys:
            for ring in poly:
                rings.append(np.asarray(ring, dtype=np.float64))
    if not rings:
        raise SystemExit(f"{path}: no polygon rings")
    return rings


def project_rings(rings: list[np.ndarray], geo: dict, origin: tuple[float, float]) -> list[np.ndarray]:
    """lon/lat rings → global z0 px on the grid (the viewer's projection at ground level)."""
    rad = math.pi / 180
    beta = float(geo.get("betaDeg", 0)) * rad
    k = float(geo.get("k", 2))
    lon0, lat0 = float(geo["lon0"]), float(geo["lat0"])
    m_lon = rad * EARTH_RADIUS * math.cos(lat0 * rad)
    m_lat = rad * EARTH_RADIUS
    out = []
    for ring in rings:
        east = (ring[:, 0] - lon0) * m_lon
        north = (ring[:, 1] - lat0) * m_lat
        p = east * math.sin(beta) + north * math.cos(beta)
        q = east * math.cos(beta) - north * math.sin(beta)
        x = (p + q) * k - origin[0]
        y = (q - p) * k / 2 - origin[1]
        out.append(np.stack([x, y], axis=1))
    return out


class Outline:
    """Signed distance to a set of rings, in z0 px: negative inside, positive outside."""

    def __init__(self, rings: list[np.ndarray]):
        a = np.concatenate([r[:-1] if np.allclose(r[0], r[-1]) else r for r in rings])
        b = np.concatenate([np.roll(r[:-1] if np.allclose(r[0], r[-1]) else r, -1, axis=0) for r in rings])
        self.ax, self.ay = a[:, 0], a[:, 1]
        self.dx, self.dy = b[:, 0] - a[:, 0], b[:, 1] - a[:, 1]
        self.len2 = np.maximum(self.dx * self.dx + self.dy * self.dy, 1e-12)
        self.by = b[:, 1]

    def signed(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        shape = x.shape
        px = x.reshape(-1, 1).astype(np.float64)
        py = y.reshape(-1, 1).astype(np.float64)
        t = np.clip(((px - self.ax) * self.dx + (py - self.ay) * self.dy) / self.len2, 0.0, 1.0)
        ex = px - (self.ax + t * self.dx)
        ey = py - (self.ay + t * self.dy)
        dist = np.sqrt((ex * ex + ey * ey).min(axis=1))
        # even-odd crossing count
        crosses = (self.ay > py) != (self.by > py)
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = self.ax + (py - self.ay) * self.dx / np.where(self.dy == 0, 1e-12, self.dy)
        inside = (np.sum(crosses & (px < xint), axis=1) % 2) == 1
        return np.where(inside, -dist, dist).reshape(shape)


def _interp_matrix(n_out: int, step: int) -> np.ndarray:
    """(n_out × n_in) bilinear weights from lattice points every `step` px."""
    n_in = n_out // step + 1
    pos = np.arange(n_out) / step
    i0 = np.clip(np.floor(pos).astype(int), 0, n_in - 2)
    t = pos - i0
    w = np.zeros((n_out, n_in), dtype=np.float64)
    w[np.arange(n_out), i0] = 1 - t
    w[np.arange(n_out), i0 + 1] = t
    return w


def tile_field(outline: Outline, tx: int, ty: int, tile: int, step: int = COVERAGE_STEP) -> np.ndarray:
    """Signed distance for every pixel of z0 tile (tx, ty), from a coarse lattice."""
    lat = np.arange(0, tile + 1, step, dtype=np.float64)
    gx, gy = np.meshgrid(tx * tile + lat, ty * tile + lat)
    coarse = outline.signed(gx, gy)
    w = _interp_matrix(tile, step)
    return (w @ coarse @ w.T).astype(np.float32)


def _smoothstep(x: np.ndarray, a: float, b: float) -> np.ndarray:
    t = np.clip((x - a) / max(b - a, 1e-6), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def polish_coverage(
    dest: Path,
    grid: dict,
    outline: Outline,
    *,
    backup: Path,
    fog: tuple[int, int, int],
    noise: Callable[..., np.ndarray],
    almost_empty: Callable[[np.ndarray, tuple[int, int, int]], bool],
    save: Callable[[Image.Image, Path], None],
    tile: int = 512,
    inset: int = COVERAGE_INSET,
    fade: int = COVERAGE_FADE,
    noise_amp: float = COVERAGE_NOISE,
    noise_period: float = COVERAGE_NOISE_PERIOD,
    log: Callable[[str], None] = print,
) -> tuple[set[tuple[int, int]], set[tuple[int, int]], set[tuple[int, int]]]:
    """Fade one layer's z0 tiles (`dest/0`) along the outline.

    Returns (present, changed, dropped). `backup/0` keeps the original of
    every tile changed or dropped; re-runs read from it.
    """
    z0 = dest / "0"
    bz0 = backup / "0"
    bz0.mkdir(parents=True, exist_ok=True)
    cols, rows = grid["cols"], grid["rows"]

    def coords(folder: Path) -> set[tuple[int, int]]:
        out = set()
        for p in folder.glob("*.webp"):
            try:
                sx, sy = p.stem.split("_", 1)
                tx, ty = int(sx), int(sy)
            except ValueError:
                continue
            if 0 <= tx < cols and 0 <= ty < rows:
                out.add((tx, ty))
        return out

    candidates = coords(z0) | coords(bz0)
    half_diag = tile * math.sqrt(2) / 2
    reach = fade + noise_amp + 2
    cx = np.array([t[0] * tile + tile / 2 for t in sorted(candidates)], dtype=np.float64)
    cy = np.array([t[1] * tile + tile / 2 for t in sorted(candidates)], dtype=np.float64)
    centre = dict(zip(sorted(candidates), outline.signed(cx, cy))) if candidates else {}

    vf = np.array(fog, dtype=np.float32)
    present: set[tuple[int, int]] = set()
    changed: set[tuple[int, int]] = set()
    dropped: set[tuple[int, int]] = set()
    for (tx, ty), sd in sorted(centre.items()):
        name = f"{tx}_{ty}.webp"
        live, orig = z0 / name, bz0 / name
        if sd < -(half_diag + inset + noise_amp + 2):
            if orig.exists():  # an earlier run touched it: put the original back
                shutil.copy2(orig, live)
                changed.add((tx, ty))
            if live.exists():
                present.add((tx, ty))
            continue
        fresh = not orig.exists()
        if fresh:
            if not live.exists():
                continue
            shutil.copy2(live, orig)
        if sd > half_diag + reach:
            live.unlink(missing_ok=True)
            dropped.add((tx, ty))
            continue
        field = tile_field(outline, tx, ty, tile)
        if noise_amp > 0:
            field = field + noise(tx, ty, amplitude=noise_amp, period=noise_period)
        alpha = _smoothstep(field, -float(inset), float(fade))
        if float(alpha.max()) < 0.004:
            if not fresh:
                shutil.copy2(orig, live)
                changed.add((tx, ty))
            orig.unlink()
            present.add((tx, ty))
            continue
        with Image.open(orig) as im:
            arr = np.asarray(im.convert("RGB"), dtype=np.float32)
        out = np.clip(arr * (1.0 - alpha[:, :, None]) + vf * alpha[:, :, None] + 0.5, 0, 255).astype(np.uint8)
        # drop only what reads as paper right up to its edges, so no neighbour shows a step
        if float(alpha.min()) > 0.985 or (almost_empty(out, fog) and int(np.abs(out.astype(np.int16) - vf).max()) <= 3):
            live.unlink(missing_ok=True)
            dropped.add((tx, ty))
            continue
        save(Image.fromarray(out, "RGB"), live)
        present.add((tx, ty))
        changed.add((tx, ty))
    # tiles outside the candidate set (none expected) are kept as they are
    present |= coords(z0) - set(centre)
    (backup / "coverage.json").write_text(
        json.dumps(
            {
                "version": COVERAGE_VERSION,
                "inset": inset,
                "fade": fade,
                "noise": noise_amp,
                "period": noise_period,
                "changed": sorted(changed),
                "dropped": sorted(dropped),
            }
        )
        + "\n"
    )
    log(f"  coverage {dest.name}: {len(changed)} faded, {len(dropped)} dropped, {len(present)} kept")
    return present, changed, dropped
