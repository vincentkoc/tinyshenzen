"""Blend a paid redraw into the map, one target region at a time.

A redraw is a fresh restyle of chosen windows (restyle_map --windows). Each
target is a grid-pixel box with a category:

  A  pier and other cut-offs        whole box
  B  flat procedural patches        whole box
  C  shoreline wedges               whole box
  D  interior gaps, blurry tiles    only cells the gap score still flags

Overlapping targets are merged first so no two fills touch the same pixels.
Only cells where a new window still draws the original's geometry
(gaps.window_good) are filled; the rest of the map is left alone. A fill is
dropped if it makes the gap score worse (A, B, C) or does not clearly lower it
(D, the same bar as the free gaps pass).
"""

from __future__ import annotations

import numpy as np

import gaps as GP
import tone as TN

REDRAW_VERSION = "redraw-1"
REDRAW_TOUCH = 32  # px: boxes closer than this are merged into one target
REDRAW_D_SCORE = 0.15  # a D cell is redrawn only where the gap score is above this
REDRAW_WORSE = 0.02  # A/B/C: a fill may raise the region's gap score by at most this
REDRAW_MIN_CELLS = 4  # smaller fills are noise


def category(name: str) -> str:
    """'A_pier_cutoffs' -> 'A'."""
    return name[:1].upper()


def merge_boxes(items: list[tuple[str, tuple[int, int, int, int]]], touch: int = REDRAW_TOUCH) -> list[dict]:
    """Union-find merge of (category, box) pairs whose boxes are within `touch` px.

    Returns [{"box": union box, "parts": [(cat, box), ...], "cats": sorted cats}]."""
    boxes = [b for _, b in items]
    changed = True
    groups = {i: [i] for i in range(len(items))}
    gbox = {i: tuple(boxes[i]) for i in range(len(items))}
    # Merge until stable: a merged box can reach a box neither part touched.
    while changed:
        changed = False
        keys = sorted(groups)
        for a_i, a in enumerate(keys):
            if a not in groups:
                continue
            for b in keys[a_i + 1 :]:
                if b not in groups or a not in groups:
                    continue
                ax0, ay0, ax1, ay1 = gbox[a]
                bx0, by0, bx1, by1 = gbox[b]
                if ax0 - touch < bx1 and bx0 - touch < ax1 and ay0 - touch < by1 and by0 - touch < ay1:
                    groups[a] += groups.pop(b)
                    gbox[a] = (min(ax0, bx0), min(ay0, by0), max(ax1, bx1), max(ay1, by1))
                    del gbox[b]
                    changed = True
    out = []
    for g, members in sorted(groups.items()):
        parts = [(items[i][0], tuple(items[i][1])) for i in members]
        out.append({"box": gbox[g], "parts": parts, "cats": sorted({c for c, _ in parts})})
    return out


def city_cells(orig: np.ndarray, cell: int = GP.GAP_CELL) -> np.ndarray:
    """0..1 per cell: how much of the original is drawn city (not the dark edge dither)."""
    H, W = orig.shape[:2]
    ch, cw = H // cell, W // cell
    drawn = (orig[: ch * cell, : cw * cell].max(axis=2) > GP.GAP_PAD_RGB).astype(np.float32)
    share = drawn.reshape(ch, cell, cw, cell).mean((1, 3))
    return TN.smoothstep(share, 0.5, 0.9).astype(np.float32)


def target_region(parts: list[tuple[str, tuple]], crop: tuple[int, int, int, int], score: np.ndarray,
                  city: np.ndarray, cell: int = GP.GAP_CELL) -> np.ndarray:
    """Cells to redraw inside a crop: whole boxes for A/B/C, still-flagged cells for D."""
    region = np.zeros(score.shape, bool)
    cx0, cy0 = crop[0], crop[1]
    flagged = score > REDRAW_D_SCORE
    # grow D cells one ring so the fill covers the gap's soft rim
    grown = np.zeros_like(flagged)
    grown[:-1] |= flagged[1:]
    grown[1:] |= flagged[:-1]
    grown[:, :-1] |= flagged[:, 1:]
    grown[:, 1:] |= flagged[:, :-1]
    grown |= flagged
    for cat, (x0, y0, x1, y1) in parts:
        m = np.zeros_like(region)
        m[max(0, (y0 - cy0) // cell) : max(0, -(-(y1 - cy0) // cell)),
          max(0, (x0 - cx0) // cell) : max(0, -(-(x1 - cx0) // cell))] = True
        if category(cat) == "D":
            m &= grown
        region |= m
    return region & (city > 0.5)


def keep_fill(cats: list[str], before: float, after: float) -> bool:
    """D-only targets must clearly improve (the free gaps pass bar); A/B/C must not get worse."""
    if all(category(c) == "D" for c in cats):
        return after <= before - GP.GAP_KEEP
    return after <= before + REDRAW_WORSE
