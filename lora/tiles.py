"""Overlapping windows and a smooth-step hem, matching the pipeline's restyle.ts."""

from __future__ import annotations

from dataclasses import dataclass


DEFAULT_WINDOW = 1024
DEFAULT_OVERLAP = 256


@dataclass(frozen=True)
class Tile:
    x: int
    y: int
    w: int
    h: int


def tiles_for(
    width: int,
    height: int,
    window: int = DEFAULT_WINDOW,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Tile]:
    if overlap >= window:
        raise ValueError("overlap must be smaller than the window")
    if width <= window and height <= window:
        return [Tile(0, 0, width, height)]
    step = window - overlap
    tiles: list[Tile] = []
    for y in range(0, height, step):
        for x in range(0, width, step):
            w = min(window, width - x)
            h = min(window, height - y)
            ox = width - window if (x + w == width and w < window and width > window) else x
            oy = height - window if (y + h == height and h < window and height > window) else y
            tiles.append(
                Tile(
                    x=max(0, ox),
                    y=max(0, oy),
                    w=min(window, width - max(0, ox)),
                    h=min(window, height - max(0, oy)),
                )
            )
    seen: set[tuple[int, int, int, int]] = set()
    unique: list[Tile] = []
    for t in tiles:
        k = (t.x, t.y, t.w, t.h)
        if k in seen:
            continue
        seen.add(k)
        unique.append(t)
    return unique


def edge_weight(px: float, py: float, tile: Tile, overlap: int) -> float:
    dl = px - tile.x
    dr = tile.x + tile.w - px
    dt = py - tile.y
    db = tile.y + tile.h - py
    d = min(dl, dr, dt, db)
    if d >= overlap:
        return 1.0
    if d <= 0:
        return 0.0
    t = d / overlap
    return t * t * (3 - 2 * t)
