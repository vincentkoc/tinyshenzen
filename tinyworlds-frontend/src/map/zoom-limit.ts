import type { Bounds } from "./manifest";

/**
 * How far out the view may go, as a share of the scale at which the map's bounds fill the view
 * edge to edge: 1 keeps the map filling the screen, above 1 stops closer in, below 1 lets paper
 * show around it. A world can set its own (World.minZoom).
 */
export const DEFAULT_MIN_ZOOM = 1;

/** The furthest-out scale for a view of width × height CSS px. */
export function minScaleFor(
  width: number,
  height: number,
  bounds: Bounds,
  maxScale: number,
  minZoom: number = DEFAULT_MIN_ZOOM,
): number {
  const fill = Math.max(width / (bounds.maxX - bounds.minX), height / (bounds.maxY - bounds.minY));
  const share = Number.isFinite(minZoom) && minZoom > 0 ? minZoom : DEFAULT_MIN_ZOOM;
  return Math.min(fill * share, maxScale);
}

/**
 * A view centre along one axis, kept so the view (half of it `half` map px) stays inside lo–hi;
 * a view wider than that range is centred on it.
 */
export function clampCentre(centre: number, half: number, lo: number, hi: number): number {
  if (half * 2 >= hi - lo) return (lo + hi) / 2;
  return Math.min(hi - half, Math.max(lo + half, centre));
}
