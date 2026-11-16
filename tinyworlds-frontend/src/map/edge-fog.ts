// The paper the map is printed on, and the fog its edges dissolve into.
//
// Tiles exist only where the city is, in square boxes, and the rest of the canvas used to stay
// transparent: on a dark page (dusk) the space around the map was black, and zoomed out the
// boxes' own fog showed as pale rectangles on it. The engine now fills the canvas with the
// paper colour first, and once zoomed out lays a soft mask over the tile boxes' outline, so the
// stepped silhouette fades into the paper, further the further out you are.

/** The fog baked into the tiles' empty edges (and the stylesheet's --fog): a warm off-white. */
export const DEFAULT_PAPER = "#e9e6df";

/** From this scale (screen px per map px) up, native tiles are drawn and their edges left alone. */
export const FOG_FROM = 1;
/**
 * By this scale the edge fog is at full strength: the first scale that draws a coarser level,
 * whose tiles bake in whatever fills their missing children (fog, or dark in procedural sets).
 */
export const FOG_FULL = 0.5;
/** How far, in CSS px, the fog reaches into the map: just zoomed out, and fully zoomed out. */
export const FOG_REACH_NEAR = 10;
export const FOG_REACH_FAR = 56;
/** Mask pixels per finest-level tile side: the outline follows the boxes to half a tile. */
export const FOG_SUBCELLS = 2;
/** Box-blur passes; three make a near-Gaussian ramp. */
const PASSES = 3;
/** The widest box radius, in mask pixels, so a huge zoom-out cannot make the blur slow. */
const MAX_RADIUS = 24;

export interface EdgeFog {
  /** 0 (none) to 1: how strongly the mask is drawn */
  opacity: number;
  /** how far the fog reaches into the map, in CSS px */
  reach: number;
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/**
 * How much edge fog to draw at a scale: none from FOG_FROM up, then a reach that grows
 * (log-linearly with the zoom) from FOG_REACH_NEAR to FOG_REACH_FAR at the widest zoom.
 */
export function edgeFog(scale: number, minScale: number): EdgeFog {
  if (!(scale > 0) || scale >= FOG_FROM) return { opacity: 0, reach: 0 };
  const opacity = clamp(Math.log(FOG_FROM / scale) / Math.log(FOG_FROM / FOG_FULL), 0, 1);
  const span = Math.log(FOG_FROM / Math.min(minScale, FOG_FROM * 0.999));
  const t = span > 0 ? clamp(Math.log(FOG_FROM / scale) / span, 0, 1) : 1;
  return { opacity, reach: FOG_REACH_NEAR + (FOG_REACH_FAR - FOG_REACH_NEAR) * t };
}

/**
 * The box radius (in mask pixels) that makes the fog reach `reach` CSS px at `scale`, for a mask
 * of `cell` map px per pixel. Three box passes of radius b blur about as far as 2b.
 */
export function fogRadius(reach: number, scale: number, cell: number): number {
  if (!(reach > 0) || !(scale > 0) || !(cell > 0)) return 0;
  return clamp(Math.round(reach / (scale * cell) / 2), 0, MAX_RADIUS);
}

/**
 * The fog mask over a cols × rows tile grid, `sub` pixels per tile, padded by `pad` pixels on
 * every side: 1 where there is no tile (or outside the grid), 0 where there is one.
 */
export function coverageMask(
  cols: number,
  rows: number,
  has: (tx: number, ty: number) => boolean,
  sub: number,
  pad: number,
): { data: Float32Array; width: number; height: number } {
  const width = cols * sub + pad * 2;
  const height = rows * sub + pad * 2;
  const data = new Float32Array(width * height).fill(1);
  for (let ty = 0; ty < rows; ty++) {
    for (let tx = 0; tx < cols; tx++) {
      if (!has(tx, ty)) continue;
      for (let j = 0; j < sub; j++) {
        const row = (pad + ty * sub + j) * width + pad + tx * sub;
        data.fill(0, row, row + sub);
      }
    }
  }
  return { data, width, height };
}

/** One running-sum box blur along rows (step 1) or columns (step width), edges clamped. */
function boxPass(
  src: Float32Array,
  dst: Float32Array,
  width: number,
  height: number,
  r: number,
  horizontal: boolean,
): void {
  const lines = horizontal ? height : width;
  const len = horizontal ? width : height;
  const step = horizontal ? 1 : width;
  const norm = 1 / (2 * r + 1);
  for (let line = 0; line < lines; line++) {
    const base = horizontal ? line * width : line;
    const at = (i: number) => src[base + clamp(i, 0, len - 1) * step];
    let sum = 0;
    for (let i = -r; i <= r; i++) sum += at(i);
    for (let i = 0; i < len; i++) {
      dst[base + i * step] = sum * norm;
      sum += at(i + r + 1) - at(i - r);
    }
  }
}

/**
 * Blurs the mask, then pulls the ramp inside the map: the fog is solid right on a box's edge
 * (where a tile stops) and thins out to nothing `≈2r` pixels in, so no hard edge is left.
 */
export function featherMask(
  mask: Float32Array,
  width: number,
  height: number,
  r: number,
): Float32Array {
  const a = Float32Array.from(mask);
  if (r > 0) {
    const b = new Float32Array(a.length);
    for (let p = 0; p < PASSES; p++) {
      boxPass(a, b, width, height, r, true);
      boxPass(b, a, width, height, r, false);
    }
  }
  for (let i = 0; i < a.length; i++) a[i] = Math.min(1, a[i] * 2);
  return a;
}

/** "#rgb" or "#rrggbb" as [r, g, b]; anything else is the default paper. */
export function paperRgb(color: string | undefined): [number, number, number] {
  const hex = (color ?? "").trim().replace(/^#/, "");
  const full = /^[0-9a-f]{3}$/i.test(hex) ? [...hex].map((c) => c + c).join("") : hex;
  if (!/^[0-9a-f]{6}$/i.test(full)) return paperRgb(DEFAULT_PAPER);
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16)) as [number, number, number];
}
