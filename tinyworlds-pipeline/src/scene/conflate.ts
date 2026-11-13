/**
 * Footprint conflation: whether a new outline overlaps one we already have, and by how much.
 *
 * A candidate is kept only when it does not share a meaningful fraction of its area (or of an
 * existing footprint's area) with anything already on the map. That is a little looser than
 * IoU alone: a small shed sitting inside a large block has a low IoU but should not be added,
 * and a large new polygon that swallows several small existing ones should not be added either.
 */
import polygonClipping from "polygon-clipping";

/** Intersection over either area at or above this means "the same building" (or a conflict). */
export const OVERLAP_THRESHOLD = 0.2;
/** IoU (or the same overlap test) at or above this is a height match for an existing footprint. */
export const HEIGHT_MATCH_THRESHOLD = 0.25;

export interface Overlap {
  intersection: number;
  union: number;
  iou: number;
  /** intersection / area(a) */
  overA: number;
  /** intersection / area(b) */
  overB: number;
}

/** Closed or open ring as [x0,y0,x1,y1,…]. Area is unsigned, in the ring's own units. */
export function ringArea(ring: ArrayLike<number>): number {
  const n = ring.length / 2;
  if (n < 3) return 0;
  let a = 0;
  for (let i = 0, j = n - 1; i < n; j = i++) a += ring[j * 2] * ring[i * 2 + 1] - ring[i * 2] * ring[j * 2 + 1];
  return Math.abs(a) / 2;
}

function asPoly(ring: ArrayLike<number>): polygonClipping.Polygon {
  const pts: [number, number][] = [];
  const n = ring.length / 2;
  for (let i = 0; i < n; i++) pts.push([ring[i * 2], ring[i * 2 + 1]]);
  const [x0, y0] = pts[0];
  const [xn, yn] = pts[pts.length - 1];
  if (x0 !== xn || y0 !== yn) pts.push([x0, y0]);
  return [pts];
}

function geomArea(geom: polygonClipping.MultiPolygon): number {
  let a = 0;
  for (const poly of geom) {
    if (!poly.length) continue;
    a += ringArea(flat(poly[0]));
    for (let i = 1; i < poly.length; i++) a -= ringArea(flat(poly[i]));
  }
  return a;
}

function flat(pts: polygonClipping.Ring): number[] {
  const out: number[] = [];
  for (const [x, y] of pts) {
    out.push(x, y);
  }
  return out;
}

/** Intersection, union and the two coverage fractions of two rings. Empty or invalid rings give 0. */
export function overlap(a: ArrayLike<number>, b: ArrayLike<number>): Overlap {
  const areaA = ringArea(a);
  const areaB = ringArea(b);
  const empty: Overlap = { intersection: 0, union: areaA + areaB, iou: 0, overA: 0, overB: 0 };
  if (areaA <= 0 || areaB <= 0) return empty;
  let interGeom: polygonClipping.MultiPolygon;
  try {
    interGeom = polygonClipping.intersection(asPoly(a), asPoly(b));
  } catch {
    return empty;
  }
  const intersection = geomArea(interGeom);
  const union = areaA + areaB - intersection;
  return {
    intersection,
    union,
    iou: union > 0 ? intersection / union : 0,
    overA: intersection / areaA,
    overB: intersection / areaB,
  };
}

/** True when the two outlines share a meaningful fraction of either footprint. */
export function overlapsEnough(a: ArrayLike<number>, b: ArrayLike<number>, threshold = OVERLAP_THRESHOLD): boolean {
  const o = overlap(a, b);
  return o.overA >= threshold || o.overB >= threshold;
}

/**
 * Add this footprint only when it does not overlap any existing one past the threshold.
 * `existing` is a list of outer rings.
 */
export function shouldAddFootprint(
  candidate: ArrayLike<number>,
  existing: ArrayLike<number>[],
  threshold = OVERLAP_THRESHOLD,
): boolean {
  if (ringArea(candidate) <= 0) return false;
  for (const e of existing) {
    if (overlapsEnough(candidate, e, threshold)) return false;
  }
  return true;
}

/** Best overlapping candidate for a height match: highest IoU, if it clears the match threshold. */
export function bestHeightMatch(
  ring: ArrayLike<number>,
  candidates: { ring: ArrayLike<number>; height: number }[],
  threshold = HEIGHT_MATCH_THRESHOLD,
): { height: number; iou: number } | null {
  let best: { height: number; iou: number } | null = null;
  for (const c of candidates) {
    const o = overlap(ring, c.ring);
    if (o.overA < threshold && o.overB < threshold && o.iou < threshold) continue;
    if (!best || o.iou > best.iou) best = { height: c.height, iou: o.iou };
  }
  return best;
}
