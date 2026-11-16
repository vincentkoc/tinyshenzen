import type { Polygons } from "../cities/types.js";

/** Even-odd test of a lon/lat point against one ring. */
function inRing(ring: number[][], lon: number, lat: number): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

/** True when the point is inside any polygon (outer ring in, holes out). */
export function insidePolygons(polygons: Polygons, lon: number, lat: number): boolean {
  for (const [outer, ...holes] of polygons) {
    if (!outer || !inRing(outer, lon, lat)) continue;
    if (!holes.some((hole) => inRing(hole, lon, lat))) return true;
  }
  return false;
}

/**
 * A point test for a city's optional `coverage` outline. Without one, everything is covered.
 */
export function coverageTest(coverage: Polygons | undefined): (lon: number, lat: number) => boolean {
  if (!coverage || coverage.length === 0) return () => true;
  return (lon, lat) => insidePolygons(coverage, lon, lat);
}
