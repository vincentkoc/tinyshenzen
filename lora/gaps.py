"""Find and fill gaps in the restyled city (free, no API).

The restyle has holes the tone pass cannot fix: beige voids or ghostly washes
where the original has buildings (mostly between district coverages, where
each district's own edge fade left a hole), content that stops on a z0 tile
or restyle-window line, and blurry squares with far less detail than the
original and the tiles around them.

`detect` scores every 16-px cell inside the city on one global grid:

- the city is where the original (procedural) tiles have content, deep
  inside the restyle coverage (the outer edge fog does not count);
- a cell is a gap when the original has detail and the restyle has much less
  (a log2 detail deficit), or when the restyle is flat beige there;
- gaps are grouped into connected regions and classed as `void` (flat
  beige), `cutoff` (a long side on a tile or window line) or `blur`;
- whole tiles whose detail ratio is far below their neighbours' are flagged
  as `blur` squares too.

`fill` repairs each region from the best free source: an overlapping restyle
window whose own pixels are intact there (windows overlap by 256 px), else the
original through the fitted house-tone transform with a local low-pass
correction toward the surrounding restyle. Both are blended in with a soft
global mask, so there is no seam.

Everything is computed on one global cell grid and sampled per tile in global
coordinates, so neighbouring tiles read one continuous surface.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

import tone as TN

GAP_VERSION = "gaps-1"
GAP_CELL = 16  # z0 px per cell; matches the vignette field
GAP_DEEP = (-860.0, -820.0)  # vignette field ramp: only this deep inside the coverage edge (it saturates at -864)
GAP_HAS = (0.08, 0.16)  # original sharp-edge density a gap needs under it
GAP_DEFICIT = (0.8, 1.5)  # log2 sharp-edge deficit ramp (original over restyle)
GAP_VOID_L = 72.0  # a beige void is light ...
GAP_VOID_CHROMA = 14.0  # ... and nearly neutral ...
GAP_MIN_CELLS = 12  # smaller regions are noise
GAP_GROW = 0.2  # a region grows through connected cells whose smoothed score is above this
GAP_BLUR_TILE = (-1.0, 0.8)  # tile log2 detail ratio, and drop below its neighbours, for a blurry square
GAP_LINE_TOL = 2  # cells: a region side within this of a tile/window line is on it
GAP_CUTOFF_FRAC = 0.25  # share of a region's outline on lines that makes it a cut-off


def cell_detail(rgb: np.ndarray, cell: int = GAP_CELL) -> tuple[np.ndarray, np.ndarray]:
    """Per-cell mean Lab and detail (mean |dL| right and below), as tone.cell_stats."""
    return TN.cell_stats(rgb, cell)


def chroma(lab: np.ndarray) -> np.ndarray:
    return np.hypot(lab[..., 1], lab[..., 2])


GAP_CONTRAST = (0.6, 1.1)  # -log2 high-pass contrast ratio (restyle over original) ramp
GAP_STRUCT = (2.0, 4.0)  # original high-pass contrast that must be there
GAP_WATER = (48.0, -10.0, 12.0, 1.5)  # restyle water: L below, a below, |b| below, detail below


def water_cells(cur_lab: np.ndarray, cur_detail: np.ndarray) -> np.ndarray:
    """Smooth dark teal restyle water. The original's water is dithered, so a
    flat restyle sea would read as a detail deficit."""
    L, a, b = cur_lab[..., 0], cur_lab[..., 1], cur_lab[..., 2]
    lmax, amax, bmax, dmax = GAP_WATER
    w = (L < lmax) & (a < amax) & (np.abs(b) < bmax) & (cur_detail < dmax)
    w = TN.blur_sum(w.astype(np.float32), 1.0) / np.maximum(TN.blur_sum(np.ones_like(L), 1.0), 1e-6)
    return np.clip(w * 2.0, 0.0, 1.0).astype(np.float32)


def gap_score(
    cur_lab: np.ndarray,
    cur_detail: np.ndarray,
    city: np.ndarray,
    st: dict,
) -> tuple[np.ndarray, np.ndarray]:
    """0..1 gap score and a 0..1 'void' share per cell.

    `city` is a 0..1 weight (original content deep inside the restyle
    coverage); `st` holds the `cell_structure` fields. A cell is a gap where the
    original has sharp edges (buildings, roads, trees) and the restyle has far
    fewer (a void, a blurry square), or keeps them at a fraction of their
    contrast (a ghostly veil).
    """
    k = city.astype(np.float32) * (1.0 - water_cells(cur_lab, cur_detail))
    if "po" in st:
        # The original's edge fog is a dark dither: lots of edges, no city.
        fogd = TN.blur_sum(st["po"], 1.0) / np.maximum(TN.blur_sum(np.ones_like(k), 1.0), 1e-6)
        k = k * (1.0 - TN.smoothstep(fogd, GAP_PAD_MAX * 0.5, GAP_PAD_MAX))
    norm = np.maximum(TN.blur_sum(k, 2.0), 1e-6)

    def sm(a):
        return TN.blur_sum(a * k, 2.0) / norm

    eo, ec = sm(st["eo"]), sm(st["ec"])
    so, sc = sm(st["so"]), sm(st["sc"])
    has = TN.smoothstep(eo, *GAP_HAS)
    lack = np.maximum(
        TN.smoothstep(np.log2((eo + 0.02) / (ec + 0.02)), *GAP_DEFICIT),
        TN.smoothstep(np.log2((so + 0.5) / (sc + 0.5)), *GAP_CONTRAST),
    )
    void = (
        TN.smoothstep(cur_lab[..., 0], GAP_VOID_L - 8.0, GAP_VOID_L)
        * (1.0 - TN.smoothstep(chroma(cur_lab), GAP_VOID_CHROMA * 0.6, GAP_VOID_CHROMA))
        * (1.0 - TN.smoothstep(st["ec"], 0.02, 0.06))
    )
    score = np.maximum(has * lack, has * void) * k
    return np.clip(score, 0.0, 1.0).astype(np.float32), (void * k).astype(np.float32)


def label(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """4-connected components of a bool mask (numpy + union-find on runs)."""
    h, w = mask.shape
    lab = np.zeros((h, w), np.int32)
    parent = [0]

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    prev_runs: list[tuple[int, int, int]] = []
    for y in range(h):
        row = mask[y]
        if not row.any():
            prev_runs = []
            continue
        d = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
        starts = np.nonzero(d == 1)[0]
        ends = np.nonzero(d == -1)[0]
        runs = []
        j = 0
        for s, e in zip(starts, ends):
            ids = []
            while j < len(prev_runs) and prev_runs[j][1] <= s:
                j += 1
            k = j
            while k < len(prev_runs) and prev_runs[k][0] < e:
                ids.append(prev_runs[k][2])
                k += 1
            if ids:
                root = find(ids[0])
                for o in ids[1:]:
                    ro = find(o)
                    if ro != root:
                        parent[max(ro, root)] = min(ro, root)
                        root = min(ro, root)
                lid = root
            else:
                lid = len(parent)
                parent.append(lid)
            lab[y, s:e] = lid
            runs.append((int(s), int(e), lid))
        prev_runs = runs
    roots = np.array([find(i) for i in range(len(parent))], np.int32)
    uniq, inv = np.unique(roots, return_inverse=True)
    lab = inv.astype(np.int32)[lab]
    # uniq[0] is 0 (background) because parent[0] == 0
    return lab, len(uniq) - 1


@dataclass
class Gap:
    id: int
    kind: str  # void | cutoff | blur
    cells: int
    severity: float
    bbox: tuple[int, int, int, int]  # cell x0, y0, x1, y1 (end exclusive)
    tiles: list[tuple[int, int]] = field(default_factory=list)
    line_frac: float = 0.0
    void_frac: float = 0.0

    @property
    def rank(self) -> float:
        return self.cells * self.severity

    def row(self, cell: int = GAP_CELL) -> dict:
        x0, y0, x1, y1 = self.bbox
        return {
            "id": self.id, "kind": self.kind, "cells": self.cells, "area_px": self.cells * cell * cell,
            "severity": round(self.severity, 3), "rank": round(self.rank, 1),
            "line_frac": round(self.line_frac, 3), "void_frac": round(self.void_frac, 3),
            "px_box": [x0 * cell, y0 * cell, x1 * cell, y1 * cell],
            "tiles": [list(t) for t in self.tiles],
        }


def line_cells(n: int, lines: np.ndarray, cell: int = GAP_CELL, tol: int = GAP_LINE_TOL) -> np.ndarray:
    """Bool (n,) of cell indices within `tol` cells of any pixel line."""
    out = np.zeros(n, bool)
    for v in np.asarray(lines):
        c = int(round(v / cell))
        out[max(0, c - tol): min(n, c + tol)] = True
    return out


def outline_on_lines(region: np.ndarray, on_x: np.ndarray, on_y: np.ndarray) -> float:
    """Share of a region's outline cells (in its own bbox crop) sitting on a tile/window line,
    counting only straight runs: a vertical outline step on an x line, a horizontal one on a y line."""
    r = region
    pad = np.pad(r, 1)
    left = r & ~pad[1:-1, :-2]
    right = r & ~pad[1:-1, 2:]
    up = r & ~pad[:-2, 1:-1]
    down = r & ~pad[2:, 1:-1]
    vert = left | right
    horz = up | down
    total = int(vert.sum() + horz.sum())
    if total == 0:
        return 0.0
    on = int((vert & on_x[None, :]).sum() + (horz & on_y[:, None]).sum())
    return on / total


def find_gaps(
    score: np.ndarray,
    void: np.ndarray,
    xlines: np.ndarray,
    ylines: np.ndarray,
    *,
    thresh: float = 0.4,
    grow: float = GAP_GROW,
    min_cells: int = GAP_MIN_CELLS,
    cell: int = GAP_CELL,
    tile: int = 512,
) -> tuple[list[Gap], np.ndarray]:
    """Group gap cells into regions and class them. Returns gaps and the label map.

    Hysteresis: a region is seeded where the smoothed score passes `thresh`
    and grows through connected cells above `grow`, so the half-washed rim
    around a hole goes with it while a lone weak cell does not start one.
    """
    sm = TN.blur_sum(score, 1.0) / np.maximum(TN.blur_sum(np.ones_like(score), 1.0), 1e-6)
    lab, n = label(sm > min(grow, thresh))
    if n == 0:
        return [], lab
    seeded = np.zeros(n + 1, bool)
    seeded[np.unique(lab[sm > thresh])] = True
    seeded[0] = False
    lab = np.where(seeded[lab], lab, 0).astype(np.int32)
    mask = lab > 0
    h, w = mask.shape
    on_x = line_cells(w, xlines, cell)
    on_y = line_cells(h, ylines, cell)
    flat = lab.ravel()
    counts = np.bincount(flat, minlength=n + 1)
    sev = np.bincount(flat, weights=score.ravel(), minlength=n + 1)
    vs = np.bincount(flat, weights=void.ravel(), minlength=n + 1)
    ys, xs = np.nonzero(lab)
    ids = lab[ys, xs]
    order = np.argsort(ids, kind="stable")
    ids, ys, xs = ids[order], ys[order], xs[order]
    bounds = np.searchsorted(ids, np.arange(1, n + 2))
    gaps: list[Gap] = []
    per = tile // cell
    small = counts < min_cells
    small[0] = True
    lab = np.where(small[lab], 0, lab).astype(np.int32)
    for i in range(1, n + 1):
        c = int(counts[i])
        if c < min_cells:
            continue
        s, e = bounds[i - 1], bounds[i]
        gy, gx = ys[s:e], xs[s:e]
        x0, x1, y0, y1 = int(gx.min()), int(gx.max()) + 1, int(gy.min()), int(gy.max()) + 1
        reg = lab[y0:y1, x0:x1] == i
        lf = outline_on_lines(reg, on_x[x0:x1], on_y[y0:y1])
        vf = float(vs[i] / c)
        kind = "void" if vf > 0.5 else ("cutoff" if lf >= GAP_CUTOFF_FRAC else "blur")
        tl = sorted({(int(x) // per, int(y) // per) for x, y in zip(gx, gy)})
        gaps.append(Gap(i, kind, c, float(sev[i] / c), (x0, y0, x1, y1), tl, lf, vf))
    gaps.sort(key=lambda g: -g.rank)
    return gaps, lab


def blurry_tiles(
    cur_detail: np.ndarray,
    orig_detail: np.ndarray,
    city: np.ndarray,
    *,
    cell: int = GAP_CELL,
    tile: int = 512,
    min_city: float = 0.5,
) -> list[dict]:
    """Whole z0 tiles with far fewer sharp edges (edge_density) than the original and than their neighbours."""
    per = tile // cell
    h, w = cur_detail.shape
    th, tw = h // per, w // per

    def pool(a):
        return a[: th * per, : tw * per].reshape(th, per, tw, per).sum((1, 3))

    k = city.astype(np.float32)
    has = (orig_detail > GAP_HAS[0]).astype(np.float32) * k
    n = pool(has)
    er = pool(cur_detail * has) / np.maximum(n, 1e-6)
    eo = pool(orig_detail * has) / np.maximum(n, 1e-6)
    ratio = np.log2((er + 0.02) / (eo + 0.02))
    cityfrac = pool(k) / (per * per)
    ok = (cityfrac >= min_city) & (n >= per * per * 0.15)
    out = []
    for ty in range(th):
        for tx in range(tw):
            if not ok[ty, tx]:
                continue
            ys, xs = slice(max(0, ty - 1), ty + 2), slice(max(0, tx - 1), tx + 2)
            nb = ratio[ys, xs][ok[ys, xs]]
            if len(nb) < 4:
                continue
            others = np.sort(nb)
            med = float(np.median(others))
            r = float(ratio[ty, tx])
            if r < GAP_BLUR_TILE[0] and med - r > GAP_BLUR_TILE[1] * 0.5:
                out.append({"tile": [tx, ty], "ratio": round(r, 3), "neighbours": round(med, 3),
                            "drop": round(med - r, 3), "eo": round(float(eo[ty, tx]), 3), "er": round(float(er[ty, tx]), 3)})
    out.sort(key=lambda d: d["ratio"])
    return out


# --- structure: does the restyle still draw what the original has? --------

GAP_HP = 6  # half-res px box radius for the high-pass (structures ~8-40 z0 px)


def _box2(a: np.ndarray, r: int) -> np.ndarray:
    return TN._box(TN._box(a, r, 0), r, 1) / float((2 * r + 1) ** 2)


GAP_PAD_RGB = 30  # the renderer's dark edge fog (black / pad), dithered into the original
GAP_PAD_MAX = 0.04  # a cell with more dithered fog pixels than this is not city
GAP_EDGE = 8.0  # Lab L step between neighbours that counts as a sharp edge


def edge_density(rgb: np.ndarray, cell: int = GAP_CELL) -> np.ndarray:
    """Per cell: share of pixels on a sharp lightness edge. Buildings, roads and
    trees have many; flat ground, smooth fog smudges and blurry squares few."""
    L = TN.rgb_to_lab(rgb)[..., 0]
    g = np.zeros_like(L)
    g[:, 1:] = np.maximum(g[:, 1:], np.abs(np.diff(L, axis=1)))
    g[1:, :] = np.maximum(g[1:, :], np.abs(np.diff(L, axis=0)))
    e = (g > GAP_EDGE).astype(np.float32)
    h, w = e.shape[0] // cell, e.shape[1] // cell
    return e[: h * cell, : w * cell].reshape(h, cell, w, cell).mean((1, 3))


def cell_structure(cur_rgb: np.ndarray, orig_rgb: np.ndarray, cell: int = GAP_CELL) -> dict:
    """Per cell: normalised cross-correlation of the restyle's high-pass lightness
    with the original's, both high-pass contrasts (std of L), and both sharp-edge
    densities.

    A good restyle redraws the original's geometry, so it correlates and keeps
    its contrast and edges. A void, a ghostly veil, a blurry square or a cut-off
    building does not. The high-pass runs at half resolution, so a 1-2 px
    redraw shift does not count.
    """
    def hp(rgb):
        L = TN.rgb_to_lab(rgb)[..., 0]
        h, w = L.shape
        L = L[: h // 2 * 2, : w // 2 * 2].reshape(h // 2, 2, w // 2, 2).mean((1, 3))
        return L - _box2(L, GAP_HP)

    c, o = hp(cur_rgb), hp(orig_rgb)
    k = cell // 2
    h, w = c.shape[0] // k, c.shape[1] // k

    def cells(a):
        return a[: h * k, : w * k].reshape(h, k, w, k).mean((1, 3))

    cc, oo, co = cells(c * c), cells(o * o), cells(c * o)
    sc, so = np.sqrt(np.maximum(cc, 0)), np.sqrt(np.maximum(oo, 0))
    ncc = co / np.maximum(sc * so, 1e-3)
    pad = (orig_rgb.max(axis=2) <= GAP_PAD_RGB).astype(np.float32)
    ph, pw = pad.shape[0] // cell, pad.shape[1] // cell
    po = pad[: ph * cell, : pw * cell].reshape(ph, cell, pw, cell).mean((1, 3))
    f = np.float32
    return {"po": po.astype(f), "ncc": ncc.astype(f), "sc": sc.astype(f), "so": so.astype(f),
            "ec": edge_density(cur_rgb, cell).astype(f), "eo": edge_density(orig_rgb, cell).astype(f)}


# --- fill -----------------------------------------------------------------

GAP_MARGIN = 320  # px of context around a gap for the tone correction (3 sigma)
GAP_CORR_SIGMA = 6.0  # cells (96 px): low-pass of the local tone correction
GAP_FEATHER = 1.5  # cells: blur of the region mask into a soft alpha
GAP_ALPHA = (0.15, 0.6)  # blurred-mask ramp to alpha
GAP_WIN_GOOD = (0.4, -0.7, -0.8)  # window cell is intact: ncc above, log2 contrast and edge ratios above
GAP_WIN_MIN = 0.15  # a window must repair at least this share of a region's cells
GAP_WIN_MAX = 4  # windows tried per region, greedily
GAP_WIN_COPY = 6.0  # mean |window - procedural| below this: an unrestyled copy, not a source
GAP_WATER_PX = (48.0, -10.0, 12.0)  # pixel water in the restyle: kept, never refilled from the original


def window_good(st: dict, covered: np.ndarray) -> np.ndarray:
    """Cells where a restyle window still draws the original's geometry at full contrast."""
    ncc, cr, er = GAP_WIN_GOOD
    return (
        (st["ncc"] > ncc)
        & (np.log2((st["sc"] + 0.5) / (st["so"] + 0.5)) > cr)
        & (np.log2((st["ec"] + 0.02) / (st["eo"] + 0.02)) > er)
        & covered
    )


def pick_windows(region: np.ndarray, goods: list[np.ndarray], *, min_share: float = GAP_WIN_MIN,
                 max_windows: int = GAP_WIN_MAX) -> list[tuple[int, np.ndarray]]:
    """Greedy cover of a region's cells by intact windows. Returns (index, cells) pairs."""
    left = region.copy()
    total = max(int(region.sum()), 1)
    out: list[tuple[int, np.ndarray]] = []
    for _ in range(max_windows):
        best, gain = -1, 0
        for i, g in enumerate(goods):
            n = int((g & left).sum())
            if n > gain:
                best, gain = i, n
        if best < 0 or gain < min_share * total:
            break
        cells = goods[best] & left
        out.append((best, cells))
        left &= ~cells
    return out


def soft(mask: np.ndarray, sigma: float = GAP_FEATHER) -> np.ndarray:
    m = mask.astype(np.float32)
    return TN.blur_sum(m, sigma) / np.maximum(TN.blur_sum(np.ones_like(m), sigma), 1e-6)


def region_alpha(region: np.ndarray, score: np.ndarray) -> np.ndarray:
    """Soft 0..1 alpha (cells) for one region: its mask, dilated a little and feathered,
    gated by the gap score around it so a feather does not reach into good restyle."""
    a = TN.smoothstep(soft(region), *GAP_ALPHA)
    gate = TN.smoothstep(soft(score > 0.2, 1.0), 0.1, 0.4)
    return np.clip(a * np.maximum(gate, region.astype(np.float32)), 0.0, 1.0).astype(np.float32)


def local_correction(target: np.ndarray, src: np.ndarray, weights: np.ndarray,
                     sigma: float = GAP_CORR_SIGMA) -> tuple[np.ndarray, np.ndarray]:
    """Robust low-pass (cells) of target - src where weights > 0, and its 0..1 strength."""
    return TN.correction_field(target - src, weights, sigma=sigma)


def upsample(field: np.ndarray, h: int, w: int, cell: int = GAP_CELL) -> np.ndarray:
    return TN.sample_cells(field, 0, 0, max(h, w), cell=cell)[:h, :w]


def water_pixels(lab: np.ndarray) -> np.ndarray:
    lmax, amax, bmax = GAP_WATER_PX
    w = (lab[..., 0] < lmax) & (lab[..., 1] < amax) & (np.abs(lab[..., 2]) < bmax)
    return w


def fill_region(
    cur: np.ndarray,
    orig: np.ndarray,
    style: dict,
    region: np.ndarray,
    score: np.ndarray,
    good_cur: np.ndarray,
    windows: list[tuple[np.ndarray, np.ndarray]],
    cell: int = GAP_CELL,
    pure: bool = False,
) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    """Fill one region on a crop (pixel-aligned to the cell grid).

    cur, orig: (H, W, 3) uint8 crops; region, score, good_cur: (H/cell, W/cell)
    cell fields; windows: (rgb crop, covered px mask) candidates. Returns the
    filled crop (uint8), the per-pixel alpha actually used, and a list of the
    sources used with their share of the region. With `pure`, the first value
    is the fill itself, to be blended by the caller with the alpha.
    """
    H, W = cur.shape[:2]
    ch, cw = H // cell, W // cell
    cur_lab = TN.rgb_to_lab(cur)
    house = TN.apply_house_style(style, TN.rgb_to_lab(orig))

    def cells(lab):
        return lab[: ch * cell, : cw * cell].reshape(ch, cell, cw, cell, 3).mean((1, 3))

    cur_c, house_c = cells(cur_lab), cells(house)
    # House source: the house-styled original, nudged toward the restyle around the region.
    hw = good_cur.astype(np.float32)
    hcorr, hstr = local_correction(cur_c, house_c, hw)
    house_c2 = house_c + hcorr * hstr[..., None]
    house_px = house + upsample(hcorr * hstr[..., None], H, W, cell)
    # Target tone inside the region (for window correction): the corrected house cells.
    target = np.where(good_cur[..., None], cur_c, house_c2)

    goods, srcs = [], []
    for rgb, cov in windows:
        covered = cov[: ch * cell, : cw * cell].reshape(ch, cell, cw, cell).mean((1, 3)) > 0.99
        st = cell_structure(rgb, orig, cell)
        goods.append(window_good(st, covered))
        srcs.append((rgb, cov, covered))
    picks = pick_windows(region, goods)
    used = []
    num = np.zeros((H, W, 3), np.float32)
    den = np.zeros((H, W), np.float32)
    assigned = np.zeros_like(region)
    for i, cells_i in picks:
        rgb, cov, covered = srcs[i]
        wl = TN.rgb_to_lab(rgb)
        wc = cells(wl)
        wcorr, wstr = local_correction(target, wc, goods[i].astype(np.float32))
        wpx = wl + upsample(wcorr * wstr[..., None], H, W, cell)
        wgt = upsample(soft(cells_i), H, W, cell) * cov
        num += wpx * wgt[..., None]
        den += wgt
        assigned |= cells_i
        used.append({"source": "window", "index": i, "share": round(float(cells_i.sum() / max(region.sum(), 1)), 3)})
    rest = region & ~assigned
    if rest.any():
        gate = ~water_pixels(cur_lab) & (orig.max(axis=2) > GAP_PAD_RGB)
        gate = TN.blur_sum(gate.astype(np.float32), 2.0) / np.maximum(TN.blur_sum(np.ones((H, W), np.float32), 2.0), 1e-6)
        hwgt = upsample(soft(rest), H, W, cell) * gate
        num += house_px * hwgt[..., None]
        den += hwgt
        used.append({"source": "house", "share": round(float(rest.sum() / max(region.sum(), 1)), 3)})
    alpha = upsample(region_alpha(region, score), H, W, cell)
    full = upsample(soft(region), H, W, cell)
    # Where no source has weight (gated water, dithered fog), keep the restyle.
    have = np.clip(den / np.maximum(full, 1e-3), 0.0, 1.0)
    alpha = alpha * have
    fill = num / np.maximum(den, 1e-6)[..., None]
    if pure:
        f = np.where(den[..., None] > 1e-6, fill, cur_lab)
        return TN.lab_to_rgb(f), alpha.astype(np.float32), used
    out = cur_lab + (fill - cur_lab) * alpha[..., None]
    out = np.where(alpha[..., None] > 1e-3, out, cur_lab)
    res = TN.lab_to_rgb(out)
    res[alpha <= 1e-3] = cur[alpha <= 1e-3]
    return res, alpha.astype(np.float32), used


GAP_KEEP = 0.03  # a fill must lower the region's mean gap score by this much, or it is dropped


def region_score(rgb: np.ndarray, orig: np.ndarray, city: np.ndarray, region: np.ndarray) -> float:
    """Mean gap score over a region's cells for a crop (the same measure the detector uses)."""
    lab, det = cell_detail(rgb)
    score, _ = gap_score(lab, det, city, cell_structure(rgb, orig))
    return float(score[region].mean()) if region.any() else 0.0


def verify_fill(cur: np.ndarray, fill: np.ndarray, orig: np.ndarray, city: np.ndarray,
                region: np.ndarray, keep: float = GAP_KEEP) -> tuple[bool, float, float]:
    """Re-score a filled crop: keep the fill only when it clearly lowers the gap score."""
    before = region_score(cur, orig, city, region)
    after = region_score(fill, orig, city, region)
    return after <= before - keep, before, after
