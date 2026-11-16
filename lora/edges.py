"""Find and fix broken edges of the restyle (free, no API).

Where a district's restyle ends, and around the outer edge of the restyled
area, the restyle left half-baked work that the gaps pass (deep inside the
city only) never looks at:

- `veil`: buildings, forest or roads the original draws, washed toward the
  viewer's beige or white, or kept at a fraction of their contrast;
- `road`: a road or motorway of the original that stops dead in the restyle
  (its pixels are not drawn as road any more);
- `phantom`: a road or bridge drawn by the restyle over water, blank ground or
  the original's edge fog, where the original has nothing;
- `ghost`: pale half-faded restyle content past the original map's own edge,
  which reads as a smudge instead of a clean fade to the paper.

The original (procedural) tiles are palette art: blank ground is one colour,
water is one teal, and the renderer's edge fog is a binary dither of pure
black pixels over the content. So each class is measured per pixel against
the original and summed on one global 16-px cell grid (the gaps grid).

Fixes reuse the gaps machinery: `veil` regions are filled from an intact
overlapping restyle window, else the house-styled original (black dither
removed first); `road` regions get only the original's road pixels, house
styled, so a cut-off motorway continues; `phantom` pixels are inpainted from
the surrounding restyle texture; `ghost` regions fade to the viewer fog
colour. Every fill is re-scored with the same measure and dropped when it
does not lower it (`"source": "kept"`).
"""

from __future__ import annotations

import numpy as np

import gaps as GP
import tone as TN

EDGE_VERSION = "edges-1"
CELL = GP.GAP_CELL
FOG = (0xE9, 0xE6, 0xDF)  # viewer fog / paper (slice_frontend.VIEW_FOG)
GROUND = (200, 194, 182)  # the original's blank ground (sgConcrete)
BLACK_MAX = 30  # the original's edge-fog dither pixels are pure black
WATER_O = ((25, 55), (12, 80), (8, 60))  # original water: r range, g - r range, b - r range
EDGE_DEEP = -860.0  # vignette field (z0 px, negative inside); it saturates at -864, so this is the city interior
EDGE_DISTRICT = 384  # px either side of a district's restyle rectangle that is audited too
EDGE_OUTER = -300.0  # ghost cleanup only this close to the coverage edge
EDGE_KEEP = 0.05  # a fix must lower its region's mean score by this much
EDGE_BLOCK = 1536  # big regions are fixed in blocks of this many px (with GP.GAP_MARGIN context)
ROAD_NEAR = 12  # px: a restyle road this close to anything the original draws on land is not a phantom
KINDS = ("ghost", "veil", "road", "phantom")  # fix order: later kinds are composed over earlier ones


# --- pixel classes ----------------------------------------------------------


def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    if r <= 0:
        return mask.copy()
    return TN._box(TN._box(mask.astype(np.float32), r, 0), r, 1) > 0.5


def orig_classes(orig: np.ndarray) -> dict:
    """Bool masks over an original crop: black dither, ground, water, road, content."""
    o = orig.astype(np.int16)
    r, g, b = o[..., 0], o[..., 1], o[..., 2]
    black = o.max(axis=2) <= BLACK_MAX
    ground = np.abs(o - np.array(GROUND, np.int16)).sum(axis=2) <= 9
    (r0, r1), (g0, g1), (b0, b1) = WATER_O
    water = (r >= r0) & (r <= r1) & (g - r >= g0) & (g - r <= g1) & (b - r >= b0) & (b - r <= b1) & (b < g)
    rng = o.max(axis=2) - o.min(axis=2)
    m = o.mean(axis=2)
    road = (rng <= 10) & (m >= 50) & (m <= 140) & ~black
    content = ~black & ~ground & ~water
    return {"black": black, "ground": ground, "water": water, "road": road, "content": content}


def cur_road(cur: np.ndarray) -> np.ndarray:
    """Restyle road / bridge pixels: low-chroma mid grey (the restyle keeps the original's roads grey)."""
    c = cur.astype(np.int16)
    rng = c.max(axis=2) - c.min(axis=2)
    m = c.mean(axis=2)
    return (rng <= 16) & (np.abs(c[..., 1] - c[..., 0]) <= 8) & (m >= 50) & (m <= 175)


def textured(rgb: np.ndarray, r: int = 12, min_std: float = 5.0) -> np.ndarray:
    """Pixels with local colour structure (Lab std over a (2r+1)^2 window): a drawn
    road or bridge stands out from the water around it, a smooth fade from water
    to the paper does not (its mid tones look grey too)."""
    lab = TN.rgb_to_lab(rgb).astype(np.float32)
    n = float((2 * r + 1) ** 2)
    var = np.zeros(lab.shape[:2], np.float32)
    for k in range(3):
        c = lab[..., k]
        m = TN._box(TN._box(c, r, 0), r, 1) / n
        m2 = TN._box(TN._box(c * c, r, 0), r, 1) / n
        var += np.maximum(m2 - m * m, 0.0)
    return np.sqrt(var) > min_std


def nothing_mask(oc: dict) -> np.ndarray:
    """Where the original has nothing a road could be on: open water or its edge fog.
    Blank ground is not nothing: in the original it is also piers, dykes, plazas and roofs."""
    land = oc["road"] | oc["content"] | oc["ground"]
    return (oc["water"] | oc["black"]) & ~_dilate(land, ROAD_NEAR)


def cur_fog(cur: np.ndarray, tol: int = 12) -> np.ndarray:
    return np.abs(cur.astype(np.int16) - np.array(FOG, np.int16)).max(axis=2) <= tol


def undither(orig: np.ndarray, passes: int = 3) -> np.ndarray:
    """The original with its black fog-dither pixels replaced by their non-black
    neighbours, so a house-styled fill is not speckled or darkened. Pixels in
    solid fog stay black (nothing to recover)."""
    out = orig.astype(np.float32)
    bad = orig.max(axis=2) <= BLACK_MAX
    for r in range(1, passes + 1):
        if not bad.any():
            break
        ok = (~bad).astype(np.float32)
        num = np.stack([TN._box(TN._box(out[..., k] * ok, r, 0), r, 1) for k in range(3)], -1)
        den = TN._box(TN._box(ok, r, 0), r, 1)
        fill = bad & (den > 0.5)
        out[fill] = num[fill] / den[fill][:, None]
        bad = bad & ~fill
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def _cells(a: np.ndarray, cell: int = CELL) -> np.ndarray:
    h, w = a.shape[0] // cell, a.shape[1] // cell
    a = a[: h * cell, : w * cell].astype(np.float32)
    return a.reshape(h, cell, w, cell, *a.shape[2:]).mean((1, 3))


def cell_fields(cur: np.ndarray, orig: np.ndarray, cell: int = CELL) -> dict:
    """Per-cell fields of a restyle crop against the original crop (same box)."""
    oc = orig_classes(orig)
    ou = undither(orig)
    lab, det = TN.cell_stats(cur, cell)
    olab, _ = TN.cell_stats(ou, cell)
    st = GP.cell_structure(cur, ou, cell)
    cg = cur_road(cur)
    road = oc["road"]
    tex = textured(cur)
    kept = road & _dilate(cg, 2)
    nroad = _cells(road, cell)
    phantom = cg & tex & nothing_mask(oc)
    clab = TN.rgb_to_lab(cur)
    f = np.float32
    out = {
        "cl": lab.astype(f), "cd": det.astype(f), "ol": olab.astype(f),
        "po": _cells(oc["black"], cell), "og": _cells(oc["ground"], cell), "ow": _cells(oc["water"], cell),
        "oc": _cells(oc["content"], cell), "orr": nroad,
        "rk": np.where(nroad > 0, _cells(kept, cell) / np.maximum(nroad, 1e-6), 1.0).astype(f),
        "pf": _cells(phantom, cell), "cf": _cells(cur_fog(cur), cell),
        "cw": _cells(GP.water_pixels(clab), cell),
    }
    for k in ("ncc", "sc", "so", "ec", "eo"):
        out[k] = st[k].astype(f)
    return out


FIELD_KEYS = ("cl", "cd", "ol", "po", "og", "ow", "oc", "orr", "rk", "pf", "cf", "cw", "ncc", "sc", "so", "ec", "eo")


# --- scores -----------------------------------------------------------------


def content_weight(F: dict) -> np.ndarray:
    """0..1: the original shows real content here (not blank, water or solid fog)."""
    vis = F["oc"] / np.maximum(1.0 - F["po"], 1e-3)
    return (TN.smoothstep(vis, 0.35, 0.6)
            * (1.0 - TN.smoothstep(F["po"], 0.3, 0.5))
            * (1.0 - TN.smoothstep(F["ow"], 0.3, 0.6))
            * (1.0 - TN.smoothstep(F["cw"], 0.3, 0.6))).astype(np.float32)


def wash_alpha(F: dict, style: dict) -> np.ndarray:
    """0..1 share of the viewer fog mixed into the restyle cell, against the house-styled original."""
    fog = TN.rgb_to_lab(np.array(FOG, np.uint8)).astype(np.float32)
    eh = TN.apply_house_style(style, F["ol"])
    d = fog - eh
    den = (d * d).sum(-1)
    a = ((F["cl"] - eh) * d).sum(-1) / np.maximum(den, 1e-3)
    return (np.clip(a, 0.0, 1.0) * TN.smoothstep(np.sqrt(den), 10.0, 20.0)).astype(np.float32)


def scores(F: dict, style: dict) -> dict:
    """0..1 per-cell scores of each kind, and the 'white' share for classing veils."""
    cont = content_weight(F)
    aw = wash_alpha(F, style)
    has = TN.smoothstep(F["eo"], 0.08, 0.16)
    lack = np.maximum(
        TN.smoothstep(np.log2((F["eo"] + 0.02) / (F["ec"] + 0.02)), 0.8, 1.5),
        TN.smoothstep(np.log2((F["so"] + 0.5) / (F["sc"] + 0.5)), 0.6, 1.1),
    )
    veil = cont * np.maximum(TN.smoothstep(aw, 0.3, 0.6), has * lack)
    white = cont * TN.smoothstep(aw, 0.6, 0.85)
    road = (TN.smoothstep(F["orr"], 0.06, 0.15) * TN.smoothstep(1.0 - F["rk"], 0.5, 0.8)
            * (1.0 - TN.smoothstep(F["po"], 0.3, 0.5)) * (1.0 - F["cw"]))
    phantom = TN.smoothstep(F["pf"], 0.04, 0.12) * (1.0 - TN.smoothstep(F["oc"] + F["orr"], 0.3, 0.6))
    vis = F["oc"] / np.maximum(1.0 - F["po"], 1e-3)
    none = np.maximum(TN.smoothstep(1.0 - vis, 0.85, 0.97) * (1.0 - TN.smoothstep(F["ow"], 0.3, 0.6)),
                      TN.smoothstep(F["po"], 0.6, 0.8))
    L = F["cl"][..., 0]
    pale = (TN.smoothstep(L, 62.0, 72.0) * (1.0 - TN.smoothstep(GP.chroma(F["cl"]), 14.0, 22.0))
            * (1.0 - F["cf"]) * (1.0 - F["cw"]))
    ghost = none * pale * TN.smoothstep(F["ec"], 0.06, 0.14)
    f = np.float32
    return {"veil": veil.astype(f), "white": white.astype(f), "road": road.astype(f),
            "phantom": phantom.astype(f), "ghost": ghost.astype(f), "cont": cont, "wash": aw}


def band_weight(field: np.ndarray, shape: tuple[int, int], rects: list[tuple[int, int, int, int]],
                cell: int = CELL, field_scale: int = 16) -> tuple[np.ndarray, np.ndarray]:
    """Audit masks on the cell grid: (edge band or district rims, outer ghost band).

    `field` is the vignette signed distance (z0 px, negative inside) at
    `field_scale` px per sample; `rects` are district restyle rectangles in grid px.
    """
    h, w = shape
    rep = field_scale // cell
    f = np.repeat(np.repeat(field, rep, 0), rep, 1) if rep > 1 else field
    ff = np.full((h, w), 1e4, np.float32)
    hh, ww = min(h, f.shape[0]), min(w, f.shape[1])
    ff[:hh, :ww] = f[:hh, :ww]
    band = ff > EDGE_DEEP
    rim = np.zeros((h, w), bool)
    m = EDGE_DISTRICT // cell
    for x0, y0, x1, y1 in rects:
        cx0, cy0, cx1, cy1 = x0 // cell, y0 // cell, x1 // cell, y1 // cell
        outer = np.zeros((h, w), bool)
        outer[max(0, cy0 - m): max(0, cy1 + m), max(0, cx0 - m): max(0, cx1 + m)] = True
        inner = np.zeros((h, w), bool)
        if cx1 - cx0 > 2 * m and cy1 - cy0 > 2 * m:
            inner[max(0, cy0 + m): max(0, cy1 - m), max(0, cx0 + m): max(0, cx1 - m)] = True
        rim |= outer & ~inner
    audit = band | (rim & (ff < 0))
    return audit, ff > EDGE_OUTER


# --- fixes ------------------------------------------------------------------


def inpaint(rgb: np.ndarray, mask: np.ndarray, levels: int = 6) -> np.ndarray:
    """Fill masked pixels from the unmasked surroundings (push-pull normalised blur),
    so a removed bridge leaves the water or ground tone around it, not a smear."""
    out = rgb.astype(np.float32)
    known = (~mask).astype(np.float32)
    fill = np.zeros_like(out)
    have = np.zeros(mask.shape, np.float32)
    for lv in range(levels):
        sigma = 2.0 * (2 ** lv)
        val, cov = TN.normalized_blur(out, known, sigma)
        take = (have < 0.999) & (cov > 1e-4)
        wgt = np.where(take, np.clip(cov * 8.0, 0.0, 1.0) * (1.0 - have), 0.0)
        fill += val * wgt[..., None]
        have += wgt
    fill /= np.maximum(have, 1e-6)[..., None]
    res = np.where(mask[..., None] & (have[..., None] > 1e-6), fill, out)
    return np.clip(np.rint(res), 0, 255).astype(np.uint8)


def phantom_mask(cur: np.ndarray, orig: np.ndarray, region_px: np.ndarray) -> np.ndarray:
    nothing = nothing_mask(orig_classes(orig))
    return _dilate(cur_road(cur) & textured(cur) & nothing & region_px, 2) & nothing


def road_alpha(orig: np.ndarray) -> np.ndarray:
    """Soft per-pixel alpha over the original's road pixels (a little wider, feathered)."""
    m = _dilate(orig_classes(orig)["road"], 2).astype(np.float32)
    return np.clip(TN.blur_sum(m, 1.0) / np.maximum(TN.blur_sum(np.ones_like(m), 1.0), 1e-6) * 1.5, 0.0, 1.0)


def fix_region(kind: str, cur: np.ndarray, orig: np.ndarray, style: dict, region: np.ndarray,
               score: np.ndarray, good: np.ndarray, windows: list, cell: int = CELL
               ) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    """One region on a crop: returns (fill uint8, alpha float32 per px, sources used)."""
    H, W = cur.shape[:2]
    region_px = GP.upsample(GP.soft(region, 1.0), H, W, cell) > 0.05
    if kind == "phantom":
        m = phantom_mask(cur, orig, region_px)
        fill = inpaint(cur, m)
        a = TN.blur_sum(m.astype(np.float32), 1.0) / np.maximum(TN.blur_sum(np.ones((H, W), np.float32), 1.0), 1e-6)
        return fill, np.clip(a * 1.6, 0.0, 1.0).astype(np.float32), [{"source": "inpaint", "px": int(m.sum())}]
    if kind == "ghost":
        fill = np.empty_like(cur)
        fill[:] = FOG
        a = GP.upsample(TN.smoothstep(GP.soft(region), 0.1, 0.6), H, W, cell) * 0.95
        return fill, a.astype(np.float32), [{"source": "fog"}]
    ou = undither(orig)
    wins = windows if kind == "veil" else []
    fill, alpha, used = GP.fill_region(cur, ou, style, region, score, good, wins, cell, pure=True)
    if kind == "road":
        alpha = alpha * road_alpha(orig)
    else:
        # Only where the original really draws something: no dark fog, no blank, no water.
        oc = orig_classes(orig)
        real = (oc["content"] | oc["road"]).astype(np.float32)
        real = TN.blur_sum(real, 2.0) / np.maximum(TN.blur_sum(np.ones((H, W), np.float32), 2.0), 1e-6)
        alpha = alpha * np.clip(real * 1.4, 0.0, 1.0)
    return fill, alpha.astype(np.float32), used


def blend(cur: np.ndarray, fill: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    a = alpha[..., None]
    return np.clip(np.rint(cur * (1.0 - a) + fill.astype(np.float32) * a), 0, 255).astype(np.uint8)


def region_score(kind: str, rgb: np.ndarray, orig: np.ndarray, style: dict, region: np.ndarray) -> float:
    s = scores(cell_fields(rgb, orig), style)[kind]
    return float(s[region].mean()) if region.any() else 0.0


def verify(kind: str, cur: np.ndarray, out: np.ndarray, orig: np.ndarray, style: dict,
           region: np.ndarray, keep: float = EDGE_KEEP) -> tuple[bool, float, float]:
    """Keep a fix only where it measurably lowers its own score."""
    before = region_score(kind, cur, orig, style, region)
    after = region_score(kind, out, orig, style, region)
    return after <= before - keep, before, after
