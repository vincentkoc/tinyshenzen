"""One house tone for a restyled map (free, no API).

The restyle was painted window by window, so neighbouring windows came back
with different tone: a pale lime pond field beside a dark teal one, a whole
512-px square a shade lighter. Feathering a few pixels across a seam cannot
fix that; the difference is block-sized.

The procedural render the restyle was made from has one consistent tone.
This module

1. fits one global colour transform, original -> restyle, in CIE Lab
   (a robust cubic polynomial plus a shrunk residual LUT): the "house style";
2. measures, on a coarse cell grid over the whole stitched world, the
   restyle's low-frequency colour against the house-styled original, with
   a robust normalised Gaussian so content the restyle legitimately
   changed (a tower drawn on a field) does not pull its surroundings;
3. adds that smooth correction to every restyle pixel (high-frequency
   detail is untouched), and
4. fills washed-out patches (the restyle returned a flat beige wash where
   the original has buildings) from the house-styled original.

Everything is computed on one global grid, so there are no tile seams.
All arrays are float32 Lab unless named otherwise.
"""

from __future__ import annotations

import math

import numpy as np

TONE_VERSION = "tone-1"
TONE_CELL = 8  # z0 px per coarse cell
TONE_SIGMA = 12.0  # correction low-pass, in cells (96 z0 px)
TONE_ROBUST = 20.0  # Lab distance where a cell's weight halves
TONE_ITERS = 4
TONE_CONF = (0.1, 0.4)  # weight-coverage ramp: below, no correction
TONE_WASH_RATIO = (1.5, 2.5)  # log2 detail deficit ramp for a wash
TONE_WASH_DETAIL = (2.0, 4.0)  # original detail (mean |dL|) that must be there
TONE_WASH_L = 70.0  # a wash is light
TONE_WASH_BLUR = 2.0  # cells

_M = np.array(
    [[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]],
    np.float32,
)
_MI = np.linalg.inv(_M).astype(np.float32)
_WHITE = np.array([0.95047, 1.0, 1.08883], np.float32)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    c = rgb[..., :3].astype(np.float32) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    xyz = (c @ _M.T) / _WHITE
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16.0 / 116.0)
    lab = np.empty(f.shape, np.float32)
    lab[..., 0] = 116.0 * f[..., 1] - 16.0
    lab[..., 1] = 500.0 * (f[..., 0] - f[..., 1])
    lab[..., 2] = 200.0 * (f[..., 1] - f[..., 2])
    return lab


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    fy = (lab[..., 0] + 16.0) / 116.0
    f = np.stack([fy + lab[..., 1] / 500.0, fy, fy - lab[..., 2] / 200.0], -1)
    xyz = np.where(f > 0.206893, f**3, (f - 16.0 / 116.0) / 7.787) * _WHITE
    c = np.clip(xyz @ _MI.T, 0.0, 1.0)
    c = np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.power(c, 1.0 / 2.4) - 0.055)
    return np.clip(np.rint(c * 255.0), 0, 255).astype(np.uint8)


def cell_stats(rgb: np.ndarray, cell: int = TONE_CELL) -> tuple[np.ndarray, np.ndarray]:
    """Per-cell mean Lab (h, w, 3) and detail (mean |dL| to the right and below)."""
    lab = rgb_to_lab(rgb)
    h, w = lab.shape[0] // cell, lab.shape[1] // cell
    lab = lab[: h * cell, : w * cell]
    mean = lab.reshape(h, cell, w, cell, 3).mean((1, 3))
    L = lab[..., 0]
    g = np.zeros_like(L)
    g[:, 1:] += np.abs(np.diff(L, axis=1))
    g[1:, :] += np.abs(np.diff(L, axis=0))
    return mean.astype(np.float32), g.reshape(h, cell, w, cell).mean((1, 3)).astype(np.float32)


# --- blur -----------------------------------------------------------------


def _box(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    a = np.moveaxis(a, axis, 0)
    n = a.shape[0]
    c = np.zeros((n + 1,) + a.shape[1:], np.float64)
    np.cumsum(a, axis=0, dtype=np.float64, out=c[1:])
    idx = np.arange(n)
    out = (c[np.minimum(idx + r + 1, n)] - c[np.maximum(idx - r, 0)]).astype(np.float32)
    return np.moveaxis(out, 0, axis)


def blur_sum(a: np.ndarray, sigma: float) -> np.ndarray:
    """Unnormalised Gaussian (three box passes) over axes 0 and 1; zero outside."""
    if sigma <= 0:
        return a.astype(np.float32)
    r = max(1, int(round((math.sqrt(4.0 * sigma * sigma + 1.0) - 1.0) / 2.0)))
    a = a.astype(np.float32)
    for _ in range(3):
        a = _box(_box(a, r, 0), r, 1)
    return a


def normalized_blur(values: np.ndarray, weights: np.ndarray, sigma: float) -> tuple[np.ndarray, np.ndarray]:
    """Weighted Gaussian mean of (h, w, c) values, and the blurred weight (coverage, 0..1)."""
    num = blur_sum(values * weights[..., None], sigma)
    den = blur_sum(weights, sigma)
    full = blur_sum(np.ones_like(weights), sigma)
    return num / np.maximum(den, 1e-6)[..., None], den / np.maximum(full, 1e-6)


def smoothstep(x: np.ndarray, a: float, b: float) -> np.ndarray:
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


# --- house style ----------------------------------------------------------

_LUT_LO = np.array([0.0, -50.0, -50.0], np.float32)
_LUT_HI = np.array([100.0, 50.0, 70.0], np.float32)
_LUT_N = np.array([24, 20, 24])
_LUT_SHRINK = 200.0


def _features(x: np.ndarray) -> np.ndarray:
    L, a, b = x[:, 0] / 50.0 - 1.0, x[:, 1] / 40.0, x[:, 2] / 40.0
    one = np.ones_like(L)
    return np.stack([one, L, a, b, L * L, a * a, b * b, L * a, L * b, a * b, L**3], 1).astype(np.float64)


def _lut_pos(x: np.ndarray) -> np.ndarray:
    return np.clip((x - _LUT_LO) / (_LUT_HI - _LUT_LO) * _LUT_N - 0.5, 0.0, _LUT_N - 1.0001)


def fit_house_style(src: np.ndarray, dst: np.ndarray, *, samples: int = 3_000_000, seed: int = 0) -> dict:
    """Fit Lab src -> dst over (n, 3) paired cells. Robust (IRLS, Cauchy) polynomial + residual LUT."""
    src = src.reshape(-1, 3)
    dst = dst.reshape(-1, 3)
    if len(src) > samples:
        idx = np.random.default_rng(seed).choice(len(src), samples, replace=False)
        src, dst = src[idx], dst[idx]
    X = _features(src)
    Y = dst.astype(np.float64)
    w = np.ones(len(X))
    coef = np.zeros((X.shape[1], 3))
    for _ in range(6):
        sw = np.sqrt(w)[:, None]
        coef = np.linalg.lstsq(X * sw, Y * sw, rcond=None)[0]
        e = np.linalg.norm(X @ coef - Y, axis=1)
        s = 1.5 * float(np.median(e)) + 1e-3
        w = 1.0 / (1.0 + (e / s) ** 2)
    res = Y - X @ coef
    bins = np.floor(_lut_pos(src) + 0.5).astype(np.int64)
    flat = (bins[:, 0] * _LUT_N[1] + bins[:, 1]) * _LUT_N[2] + bins[:, 2]
    n = int(np.prod(_LUT_N))
    cnt = np.bincount(flat, minlength=n)
    order = np.argsort(flat, kind="stable")
    fs = flat[order]
    starts = np.searchsorted(fs, np.arange(n))
    ends = np.searchsorted(fs, np.arange(n), side="right")
    lut = np.zeros((n, 3), np.float32)
    for k in np.nonzero(cnt)[0]:
        lut[k] = np.median(res[order[starts[k] : ends[k]]], axis=0) * (cnt[k] / (cnt[k] + _LUT_SHRINK))
    return {"coef": coef.astype(np.float64), "lut": lut.reshape(tuple(_LUT_N) + (3,))}


def apply_house_style(style: dict, lab: np.ndarray, chunk: int = 1 << 20) -> np.ndarray:
    shape = lab.shape
    x = lab.reshape(-1, 3)
    out = np.empty_like(x, dtype=np.float32)
    lut = style["lut"]
    nl, na, nb = lut.shape[:3]
    for s in range(0, len(x), chunk):
        xs = x[s : s + chunk]
        y = _features(xs) @ style["coef"]
        p = _lut_pos(xs)
        i0 = np.floor(p).astype(np.int64)
        fr = p - i0
        for dl in (0, 1):
            wl = fr[:, 0] if dl else 1.0 - fr[:, 0]
            il = np.minimum(i0[:, 0] + dl, nl - 1)
            for da in (0, 1):
                wa = fr[:, 1] if da else 1.0 - fr[:, 1]
                ia = np.minimum(i0[:, 1] + da, na - 1)
                for db in (0, 1):
                    wb = fr[:, 2] if db else 1.0 - fr[:, 2]
                    ib = np.minimum(i0[:, 2] + db, nb - 1)
                    y += (wl * wa * wb)[:, None] * lut[il, ia, ib]
        out[s : s + chunk] = y
    return out.reshape(shape)


# --- correction field -----------------------------------------------------


def wash_weight(
    rest_lab: np.ndarray,
    rest_detail: np.ndarray,
    orig_detail: np.ndarray,
    known: np.ndarray,
    *,
    ratio: tuple[float, float] = TONE_WASH_RATIO,
    detail: tuple[float, float] = TONE_WASH_DETAIL,
    light: float = TONE_WASH_L,
    blur: float = TONE_WASH_BLUR,
) -> np.ndarray:
    """0..1 per cell: the restyle is a light, flat wash where the original has detail."""
    k = known.astype(np.float32)
    # The restyle side is blurred less, so a drawn building beside a wash is
    # not counted as washed; the original side is blurred more, so a wash over
    # a quiet street between buildings still counts.
    er = blur_sum(rest_detail * k, 1.0) / np.maximum(blur_sum(k, 1.0), 1e-6)
    eo = blur_sum(orig_detail * k, 3.0) / np.maximum(blur_sum(k, 3.0), 1e-6)
    deficit = np.log2((eo + 0.5) / (er + 0.5))
    w = smoothstep(deficit, *ratio) * smoothstep(eo, *detail) * smoothstep(rest_lab[..., 0], light - 10.0, light)
    w = w * k
    if blur > 0:
        # Decide, then feather: a half-weight wash would ghost the original
        # over the restyle.
        w = blur_sum(w, blur) / np.maximum(blur_sum(np.ones_like(w), blur), 1e-6)
        w = smoothstep(w, 0.3, 0.6)
        w = blur_sum(w, 1.0) / np.maximum(blur_sum(np.ones_like(w), 1.0), 1e-6)
    return np.clip(w, 0.0, 1.0).astype(np.float32)


def correction_field(
    residual: np.ndarray,
    weights: np.ndarray,
    *,
    sigma: float = TONE_SIGMA,
    robust: float = TONE_ROBUST,
    iters: int = TONE_ITERS,
    conf: tuple[float, float] = TONE_CONF,
) -> tuple[np.ndarray, np.ndarray]:
    """Robust low-pass of (h, w, 3) residual = house(original) - restyle.

    Returns the correction and a 0..1 strength from weight coverage, so a
    patch where most cells disagree with the house style (changed content)
    is left alone instead of being pulled by a few agreeing cells.
    """
    w = weights.astype(np.float32)
    corr, cover = normalized_blur(residual, w, sigma)
    for _ in range(max(0, iters - 1)):
        e = np.linalg.norm(residual - corr, axis=-1)
        w = weights / (1.0 + (e / robust) ** 2)
        corr, cover = normalized_blur(residual, w, sigma)
    return corr.astype(np.float32), smoothstep(cover, *conf).astype(np.float32)


def sample_cells(field: np.ndarray, x0: int, y0: int, size: int, cell: int = TONE_CELL) -> np.ndarray:
    """Bilinear sample of a global cell field at z0 pixels [x0, x0+size) x [y0, y0+size).

    Coordinates are global, so two neighbouring tiles read one continuous surface.
    """
    h, w = field.shape[:2]
    u = (np.arange(x0, x0 + size, dtype=np.float32) + 0.5) / cell - 0.5
    v = (np.arange(y0, y0 + size, dtype=np.float32) + 0.5) / cell - 0.5
    u = np.clip(u, 0, w - 1)
    v = np.clip(v, 0, h - 1)
    iu = np.minimum(np.floor(u).astype(np.int64), w - 2) if w > 1 else np.zeros(size, np.int64)
    iv = np.minimum(np.floor(v).astype(np.int64), h - 2) if h > 1 else np.zeros(size, np.int64)
    fu = (u - iu) if w > 1 else np.zeros(size, np.float32)
    fv = (v - iv) if h > 1 else np.zeros(size, np.float32)
    iu1 = np.minimum(iu + 1, w - 1)
    iv1 = np.minimum(iv + 1, h - 1)
    f = field.astype(np.float32)
    if f.ndim == 2:
        f = f[..., None]
    top = f[iv][:, iu] * (1 - fu)[None, :, None] + f[iv][:, iu1] * fu[None, :, None]
    bot = f[iv1][:, iu] * (1 - fu)[None, :, None] + f[iv1][:, iu1] * fu[None, :, None]
    out = top * (1 - fv)[:, None, None] + bot * fv[:, None, None]
    return out if field.ndim == 3 else out[..., 0]


def tone_tile(
    rest_rgb: np.ndarray,
    orig_rgb: np.ndarray | None,
    style: dict,
    corr: np.ndarray,
    strength: np.ndarray,
    wash: np.ndarray,
) -> np.ndarray:
    """Apply a sampled correction (size, size, 3) and wash fill to one tile."""
    lab = rgb_to_lab(rest_rgb)
    lab = lab + corr * strength[..., None]
    if orig_rgb is not None and float(wash.max()) > 1e-3:
        house = apply_house_style(style, rgb_to_lab(orig_rgb))
        lab = lab + (house - lab) * wash[..., None]
    return lab_to_rgb(lab)



# --- seam steps -----------------------------------------------------------
#
# Hard tone steps sit on straight, axis-aligned lines: z0 tile edges (per-tile
# passes matched each tile on its own) and restyle-window / district edges
# (a later district pasted over an earlier one). The smooth field above cannot
# reproduce a step, so it would leave one half-corrected. Instead the map is cut
# into rectangles by every candidate line, the residual step across each
# rectangle side is measured at full resolution, and one offset per rectangle
# is solved so that real (consistent) steps cancel and everything else stays
# continuous.

TONE_BAND = 16  # px each side of a line
TONE_GAP = 2  # px skipped at the line itself
TONE_STEP_CONSIST = (0.8, 2.0)  # |median restyle step| / (row MAD + 1)
TONE_STEP_MIN = (3.0, 6.0)  # Lab, restyle step
TONE_STEP_ROWS = 24
TONE_STEP_REACH = 160.0  # px a cancelled step fades over, each side
TONE_STEP_RAMP = 48.0  # px a step fades in and out along its line
TONE_STEP_GATE = 0.5  # below this gate a step is not corrected at all
TONE_MERGE = 4  # px: candidate lines closer than this collapse


def merge_lines(lines: list[int] | np.ndarray, merge: int = TONE_MERGE) -> np.ndarray:
    out: list[int] = []
    for x in sorted(int(v) for v in lines):
        if not out or x - out[-1] >= merge:
            out.append(x)
    return np.array(out, np.int64)


def band_bounds(lines: np.ndarray, k: int, band: int = TONE_BAND, gap: int = TONE_GAP) -> tuple[int, int] | None:
    """Usable band width on each side of lines[k] (same width both sides), or None."""
    left = lines[k] - lines[k - 1] if k > 0 else band * 4
    right = lines[k + 1] - lines[k] if k + 1 < len(lines) else band * 4
    b = min(band, left // 2 - gap, right // 2 - gap)
    if b < 4:
        return None
    return gap, gap + b


def step_stat(left: np.ndarray, right: np.ndarray, ok: np.ndarray, *, min_rows: int = TONE_STEP_ROWS) -> np.ndarray:
    """Gated residual step (3,) across a line from per-row band means (n, 6).

    Channels 0-2 are the residual (house original - restyle), 3-5 the restyle.
    The gate looks at the restyle alone: a step is only corrected where the
    restyle itself jumps consistently along the line. A step that is only in
    the original (lossy procedural tiles, a road on a tile edge) is ignored.
    """
    n = int(ok.sum())
    if n < max(min_rows, int(0.3 * len(ok))):
        return np.zeros(3, np.float32)
    d = (right - left)[ok]
    s = np.median(d[:, :3], axis=0)
    sr = np.median(d[:, 3:6], axis=0)
    mad = float(np.median(np.linalg.norm(d[:, 3:6] - sr, axis=1)))
    mag = float(np.linalg.norm(sr))
    gate = float(smoothstep(np.float32(mag / (mad + 1.0)), *TONE_STEP_CONSIST) * smoothstep(np.float32(mag), *TONE_STEP_MIN))
    if gate < TONE_STEP_GATE:
        return np.zeros(3, np.float32)
    return s.astype(np.float32)


def step_field(segments: np.ndarray, x0: float, y0: float, w: int, h: int, scale: float = 1.0,
               *, reach: float = None, ramp: float = None) -> np.ndarray:
    """Correction (h, w, 3) that cancels measured seam steps, sampled at
    x0 + (i + 0.5) * scale, y0 + (j + 0.5) * scale (grid pixels).

    segments: (n, 7) rows of axis (0 = vertical line at x = c, 1 = horizontal
    at y = c), c, a, b (extent along the line), step (3) = residual on the
    far side minus the near side. Each side takes half the step at the line,
    fading to nothing over `reach` px; along the line the segment fades in
    and out over `ramp` px, so neighbouring segments of one seam hand over
    smoothly. Nothing is piecewise constant, so flat water gets no terraces.
    """
    reach = TONE_STEP_REACH if reach is None else reach
    ramp = TONE_STEP_RAMP if ramp is None else ramp
    out = np.zeros((h, w, 3), np.float32)
    if len(segments) == 0:
        return out
    xs = x0 + (np.arange(w, dtype=np.float32) + 0.5) * scale
    ys = y0 + (np.arange(h, dtype=np.float32) + 0.5) * scale
    x1, y1 = xs[-1] + scale, ys[-1] + scale
    for axis, c, a, b, s0, s1, s2 in segments:
        if axis == 0:
            if c + reach < x0 or c - reach > x1 or b + ramp < y0 or a - ramp > y1:
                continue
            across, along = xs, ys
        else:
            if c + reach < y0 or c - reach > y1 or b + ramp < x0 or a - ramp > x1:
                continue
            across, along = ys, xs
        d = across - c
        f = np.where(d >= 0, 0.5, -0.5) * (1.0 - smoothstep(np.abs(d), 0.0, reach))
        g = smoothstep(along, a - ramp, a + ramp) * (1.0 - smoothstep(along, b - ramp, b + ramp))
        ia = np.nonzero(f)[0]
        ib = np.nonzero(g)[0]
        if len(ia) == 0 or len(ib) == 0:
            continue
        sl_a = slice(ia[0], ia[-1] + 1)
        sl_b = slice(ib[0], ib[-1] + 1)
        m = g[sl_b][:, None] * f[sl_a][None, :]  # (along, across)
        step = np.array([s0, s1, s2], np.float32)
        if axis == 0:
            out[sl_b, sl_a] += m[..., None] * step
        else:
            out[sl_a, sl_b] += m.T[..., None] * step
    return out


def tile_measure(
    res: np.ndarray,
    rest: np.ndarray,
    valid: np.ndarray,
    x0: int,
    y0: int,
    xl: np.ndarray,
    yl: np.ndarray,
) -> dict:
    """Per-tile statistics for the step solve.

    res: (S, S, 3) residual (house original - restyle), rest: (S, S, 3)
    restyle Lab, valid: (S, S) bool.
    xl / yl: global candidate lines, which must include this tile's edges.
    Returns interior steps and edge bands for the caller to pair with the
    neighbouring tile.
    """
    size = res.shape[0]
    x1, y1 = x0 + size, y0 + size
    ix0, ix1 = int(np.searchsorted(xl, x0)), int(np.searchsorted(xl, x1))
    iy0, iy1 = int(np.searchsorted(yl, y0)), int(np.searchsorted(yl, y1))
    steps = []
    both = np.concatenate([res, rest], axis=-1)

    def bands(axis_res: np.ndarray, axis_valid: np.ndarray, lo: int, hi: int):
        v = axis_valid[:, lo:hi]
        cnt = v.sum(axis=1)
        mean = (axis_res[:, lo:hi] * v[..., None]).sum(axis=1) / np.maximum(cnt, 1)[:, None]
        return mean.astype(np.float32), cnt >= 0.75 * (hi - lo)

    # Interior vertical lines (x), per y segment; then horizontal by transposing.
    for axis in ("x", "y"):
        r = both if axis == "x" else both.transpose(1, 0, 2)
        vv = valid if axis == "x" else valid.T
        lines, other = (xl, yl) if axis == "x" else (yl, xl)
        a0, a1 = (x0, x1) if axis == "x" else (y0, y1)
        b0, b1 = (iy0, iy1) if axis == "x" else (ix0, ix1)
        ob = y0 if axis == "x" else x0
        for k in range(int(np.searchsorted(lines, a0)) + 1, int(np.searchsorted(lines, a1))):
            bb = band_bounds(lines, k)
            if bb is None:
                continue
            g, e = bb
            c = lines[k] - a0
            lm, lok = bands(r, vv, c - e, c - g)
            rm, rok = bands(r, vv, c + g, c + e)
            for j in range(b0, b1):
                sa, sb = other[j] - ob, other[j + 1] - ob
                steps.append((axis, k, j, step_stat(lm[sa:sb], rm[sa:sb], lok[sa:sb] & rok[sa:sb])))
    # Edge bands for lines on the tile border.
    edges = {}
    for axis in ("x", "y"):
        r = both if axis == "x" else both.transpose(1, 0, 2)
        vv = valid if axis == "x" else valid.T
        lines = xl if axis == "x" else yl
        a0, a1 = (x0, x1) if axis == "x" else (y0, y1)
        k0, k1 = int(np.searchsorted(lines, a0)), int(np.searchsorted(lines, a1))
        bb0, bb1 = band_bounds(lines, k0), band_bounds(lines, k1)
        if bb0 is not None:
            edges[(axis, "lo")] = bands(r, vv, bb0[0], bb0[1])  # right side of the line at a0
        if bb1 is not None:
            edges[(axis, "hi")] = bands(r, vv, size - bb1[1], size - bb1[0])  # left side of the line at a1
    return {"steps": steps, "edges": edges}
