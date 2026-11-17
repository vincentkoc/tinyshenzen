import type { CoverMask } from "@/worlds/types";
import { coverCells } from "./cover";
import type { CoverageGrid } from "./engine";
import { featherMask } from "./edge-fog";

/**
 * Night, worked out once per tile picture (night.ts runs it off the main thread when a tile
 * arrives; the engine then draws the result in place of the tile). Everything here is a pure
 * function of the tile's pixels and its place on the map, so neighbours and levels agree:
 *
 * - a blue-hour grade over everything;
 * - a soft warm light on wide roads, and a little of it spilling around them;
 * - warmth on building faces in patches the size of a few blocks (map-space noise, no per-pixel
 *   randomness, nothing that moves);
 * - dark water that takes a faint copy of the road light near its shores.
 *
 * Lights stay inside the coverage field (1 inside the covered cells, a short ramp, 0 outside).
 */

/** Coverage over the finest grid, as bytes, and where it sits in map px. */
export interface NightField {
  data: Uint8Array;
  width: number;
  height: number;
  /** map px of data[0]'s top-left corner, and of one cell's side */
  x: number;
  y: number;
  cell: number;
}

/** Where a tile picture lies on the map: its top-left corner and its side, in map px. */
export interface TilePlace {
  left: number;
  top: number;
  span: number;
}

/** Cells per finest tile side when the world has no cover mask. */
const FIELD_SUB = 4;

export function buildNightField(
  grid: CoverageGrid | null,
  cover: CoverMask | null,
): NightField | null {
  if (!grid) return null;
  const sub = cover?.cells ?? FIELD_SUB;
  const pad = 4;
  const { data, width, height } = coverCells(grid, cover, sub, pad);
  // featherMask pulls the ramp inside: lights fade out before the outline, never past it
  const empty = featherMask(data, width, height, 1);
  const bytes = new Uint8Array(width * height);
  for (let i = 0; i < bytes.length; i++) bytes[i] = Math.round((1 - empty[i]) * 255);
  const cell = grid.span / sub;
  return {
    data: bytes,
    width,
    height,
    x: grid.originX - pad * cell,
    y: grid.originY - pad * cell,
    cell,
  };
}

/** The finest grid as plain data (a worker cannot be handed `has`): 1 per tile the map has. */
export interface GridData {
  cols: number;
  rows: number;
  originX: number;
  originY: number;
  span: number;
  tiles: Uint8Array;
}

export function gridData(grid: CoverageGrid | null): GridData | null {
  if (!grid) return null;
  const tiles = new Uint8Array(grid.cols * grid.rows);
  for (let ty = 0; ty < grid.rows; ty++) {
    for (let tx = 0; tx < grid.cols; tx++) tiles[ty * grid.cols + tx] = grid.has(tx, ty) ? 1 : 0;
  }
  // plain fields only: `has` cannot be posted to a worker
  const { cols, rows, originX, originY, span } = grid;
  return { cols, rows, originX, originY, span, tiles };
}

export function gridFrom(data: GridData | null): CoverageGrid | null {
  if (!data) return null;
  const { cols, rows, originX, originY, span, tiles } = data;
  return { cols, rows, originX, originY, span, has: (tx, ty) => tiles[ty * cols + tx] === 1 };
}

/** Coverage at a map point, 0–1, bilinear between cell centres. No field: lit everywhere. */
export function fieldAt(field: NightField | null, mx: number, my: number): number {
  if (!field) return 1;
  const fx = (mx - field.x) / field.cell - 0.5;
  const fy = (my - field.y) / field.cell - 0.5;
  const x0 = Math.floor(fx);
  const y0 = Math.floor(fy);
  const ax = fx - x0;
  const ay = fy - y0;
  const { data, width: w, height: h } = field;
  const at = (x: number, y: number) =>
    x < 0 || y < 0 || x >= w || y >= h ? 0 : data[y * w + x] / 255;
  const top = at(x0, y0) * (1 - ax) + at(x0 + 1, y0) * ax;
  const bottom = at(x0, y0 + 1) * (1 - ax) + at(x0 + 1, y0 + 1) * ax;
  return top * (1 - ay) + bottom * ay;
}

/** Px of the neighbours read around each tile, so what is blurred agrees across its edges. */
export const MARGIN = 32;

type Drawable = ImageBitmap;
interface Ctx2D {
  imageSmoothingEnabled: boolean;
  clearRect(x: number, y: number, w: number, h: number): void;
  drawImage(image: Drawable, dx: number, dy: number): void;
  drawImage(
    image: Drawable,
    sx: number,
    sy: number,
    sw: number,
    sh: number,
    dx: number,
    dy: number,
    dw: number,
    dh: number,
  ): void;
}

/**
 * Draws a tile with MARGIN px of its neighbours around it (`around`: the 3×3 block, row by row,
 * the tile in the middle) on a canvas of side size + 2 × MARGIN. Where a neighbour is missing,
 * the tile's own edge is stretched out instead.
 */
export function drawFramed(ctx: Ctx2D, around: (Drawable | null)[], size: number): void {
  const tile = around[4];
  if (!tile) return;
  const m = MARGIN;
  const full = size + 2 * m;
  ctx.imageSmoothingEnabled = false;
  ctx.clearRect(0, 0, full, full);
  ctx.drawImage(tile, 0, 0, 1, size, 0, m, m, size);
  ctx.drawImage(tile, size - 1, 0, 1, size, m + size, m, m, size);
  ctx.drawImage(tile, 0, 0, size, 1, m, 0, size, m);
  ctx.drawImage(tile, 0, size - 1, size, 1, m, m + size, size, m);
  ctx.drawImage(tile, 0, 0, 1, 1, 0, 0, m, m);
  ctx.drawImage(tile, size - 1, 0, 1, 1, m + size, 0, m, m);
  ctx.drawImage(tile, 0, size - 1, 1, 1, 0, m + size, m, m);
  ctx.drawImage(tile, size - 1, size - 1, 1, 1, m + size, m + size, m, m);
  for (let i = 0; i < 9; i++) {
    const n = around[i];
    if (!n) continue;
    const dx = (i % 3) - 1;
    const dy = Math.floor(i / 3) - 1;
    ctx.drawImage(n, m + dx * size, m + dy * size);
  }
}

// ---- colour ---------------------------------------------------------------------------------

const SODIUM = [1.0, 0.6, 0.28] as const;
const WARM = [1.0, 0.74, 0.44] as const;
const WATER = [0.02, 0.043, 0.085] as const;

function smooth(e0: number, e1: number, x: number): number {
  const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
  return t * t * (3 - 2 * t);
}

/** x water, y trees and grass, z asphalt, w road paint (channels 0–1). */
function classify(r: number, g: number, b: number, out: Float32Array): void {
  const hi = Math.max(r, g, b);
  const lo = Math.min(r, g, b);
  const sat = hi - lo;
  const l = 0.299 * r + 0.587 * g + 0.114 * b;
  // the sea and lakes are painted teal to blue: far more blue than red, blue close to green
  const water =
    smooth(0.06, 0.14, b - r) *
    smooth(0.62, 0.76, b / Math.max(g, 0.01)) *
    smooth(0.1, 0.2, l) *
    (1 - smooth(0.82, 0.92, l));
  // leaves: green over blue by a clear margin, and at least as green as red
  const veg = smooth(0.05, 0.13, g - b) * smooth(-0.03, 0.03, g - r) * smooth(0.06, 0.13, sat);
  const asphalt =
    (1 - smooth(0.035, 0.075, sat)) * smooth(0.12, 0.2, l) * (1 - smooth(0.36, 0.45, l));
  // road paint is yellow with red ahead of green, or white
  const yellow = smooth(0.18, 0.32, r - b) * smooth(0.55, 0.68, g) * smooth(0.0, 0.04, r - g);
  const white = smooth(0.78, 0.9, l) * (1 - smooth(0.05, 0.1, sat));
  out[0] = water;
  out[1] = veg * (1 - water);
  out[2] = asphalt * (1 - water);
  out[3] = Math.max(yellow, white * 0.5);
}

/** The blue-hour grade alone (channels 0–1 in, 0–1 out): the paper, and the base of every pixel. */
function grade(r: number, g: number, b: number, out: Float32Array, o = 0): void {
  const l = 0.299 * r + 0.587 * g + 0.114 * b;
  const k = 1 - 0.55 * l * l;
  out[o] = (r + (l - r) * 0.35) * 0.34 * k + 0.018;
  out[o + 1] = (g + (l - g) * 0.35) * 0.4 * k + 0.027;
  out[o + 2] = (b + (l - b) * 0.35) * 0.6 * k + 0.056;
}

const scratch = new Float32Array(4);

/** What the paper around the map turns into at night (0–255): darker still than the land. */
export function nightPaper(rgb: readonly [number, number, number]): [number, number, number] {
  grade(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, scratch);
  return [0, 1, 2].map((i) => Math.round(Math.min(1, scratch[i] * 0.6) * 255)) as [
    number,
    number,
    number,
  ];
}

// ---- smooth noise in map space --------------------------------------------------------------

function hash(x: number, y: number): number {
  let h = (Math.imul(x | 0, 374761393) + Math.imul(y | 0, 668265263)) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

/** Value noise, 0–1, smooth between integer lattice points. */
export function valueNoise(x: number, y: number): number {
  const ix = Math.floor(x);
  const iy = Math.floor(y);
  let fx = x - ix;
  let fy = y - iy;
  fx = fx * fx * (3 - 2 * fx);
  fy = fy * fy * (3 - 2 * fy);
  const a = hash(ix, iy);
  const b = hash(ix + 1, iy);
  const c = hash(ix, iy + 1);
  const d = hash(ix + 1, iy + 1);
  return (a + (b - a) * fx) * (1 - fy) + (c + (d - c) * fx) * fy;
}

/** Map px per side of a window block (some floors of an office), and of a cluster of them. */
const BLOCK_MAP_PX = 30;
const CLUSTER_MAP_PX = 150;
/** The share of blocks lit, on average. */
export const WINDOW_SHARE = 0.4;

/**
 * Whether the windows around a map point are lit, 0 or 1: blocks laid along the drawing's two
 * slopes (2:1), so their edges run with the walls and floors, lit more often in some clusters
 * than others. Fixed in map space: the same at every level, nothing moves.
 */
export function windowBlock(mx: number, my: number): number {
  const u = (mx + 2 * my) / BLOCK_MAP_PX;
  const v = (mx - 2 * my) / BLOCK_MAP_PX;
  const cluster = valueNoise(mx / CLUSTER_MAP_PX + 31.7, my / CLUSTER_MAP_PX + 3.3);
  const share = WINDOW_SHARE + (cluster - 0.5) * 0.7;
  return hash(Math.floor(u) + 9001, Math.floor(v) - 77) < share ? 1 : 0;
}

// ---- blur -----------------------------------------------------------------------------------

/** Box blur of a w×h field, radius r, edges held (so a tile's border reads like its inside). */
function boxBlur(src: Float32Array, w: number, h: number, r: number, tmp: Float32Array): void {
  if (r < 1) return;
  const n = 2 * r + 1;
  for (let y = 0; y < h; y++) {
    const row = y * w;
    let sum = src[row] * (r + 1);
    for (let i = 1; i <= r; i++) sum += src[row + Math.min(i, w - 1)];
    for (let x = 0; x < w; x++) {
      tmp[row + x] = sum / n;
      sum += src[row + Math.min(x + r + 1, w - 1)] - src[row + Math.max(x - r, 0)];
    }
  }
  for (let x = 0; x < w; x++) {
    let sum = tmp[x] * (r + 1);
    for (let i = 1; i <= r; i++) sum += tmp[Math.min(i, h - 1) * w + x];
    for (let y = 0; y < h; y++) {
      src[y * w + x] = sum / n;
      sum += tmp[Math.min(y + r + 1, h - 1) * w + x] - tmp[Math.max(y - r, 0) * w + x];
    }
  }
}

/** Near glow and spill reach, in map px. */
const NEAR_MAP_PX = 3;
const FAR_MAP_PX = 14;

/**
 * Turns a tile picture (RGBA, w×h, read in place) into its night version. `place` is where it
 * lies on the map; `field` keeps the lights inside the coverage outline (null: lit everywhere).
 */
export function nightTile(
  px: Uint8ClampedArray,
  w: number,
  h: number,
  place: TilePlace | null,
  field: NightField | null,
  /** px of neighbouring tiles around the picture: read for context, `place` is inside them */
  margin = 0,
): void {
  const n = w * h;
  const kinds = new Float32Array(n * 4);
  const near = new Float32Array(n);
  const tmp = new Float32Array(n);
  const luma = new Float32Array(n);
  const busy = new Float32Array(n);
  const k = new Float32Array(4);
  for (let i = 0, o = 0; i < n; i++, o += 4) {
    luma[i] = (0.299 * px[o] + 0.587 * px[o + 1] + 0.114 * px[o + 2]) / 255;
  }
  // texture around each pixel: glass and windows are busy, water and paint are calm
  for (let y = 0; y < h; y++) {
    const up = Math.max(0, y - 1) * w;
    const down = Math.min(h - 1, y + 1) * w;
    const row = y * w;
    for (let x = 0; x < w; x++) {
      const l = row + Math.max(0, x - 1);
      const r = row + Math.min(w - 1, x + 1);
      busy[row + x] = Math.abs(luma[r] - luma[l]) + Math.abs(luma[down + x] - luma[up + x]);
    }
  }
  boxBlur(busy, w, h, 2, tmp);
  const around = new Float32Array(luma);
  boxBlur(around, w, h, 2, tmp);
  const vegAround = new Float32Array(n);
  for (let i = 0, o = 0; i < n; i++, o += 4) {
    classify(px[o] / 255, px[o + 1] / 255, px[o + 2] / 255, k);
    // blue glass is not water: water is the calm blue
    const water = k[0] * (1 - smooth(0.05, 0.12, busy[i]));
    kinds[o] = water;
    kinds[o + 1] = k[1];
    kinds[o + 2] = k[2];
    kinds[o + 3] = k[3];
    near[i] = k[2];
    vegAround[i] = k[1];
  }
  // flowers and highlights among trees are not buildings
  boxBlur(vegAround, w, h, 3, tmp);

  // tile px per map px: the glow keeps its size on the ground at every level
  const density = place ? (w - 2 * margin) / place.span : 1;
  const far = new Float32Array(near);
  boxBlur(near, w, h, Math.round(NEAR_MAP_PX * density), tmp);
  const farR = Math.round(FAR_MAP_PX * density * 0.6);
  boxBlur(far, w, h, farR, tmp);
  boxBlur(far, w, h, farR, tmp);

  const step = place ? place.span / (w - 2 * margin) : 1;
  // zoomed out, a window block is a few px: blocks give way to their average warmth
  const blocks = smooth(0.08, 0.3, density);
  // and the panes blur into the wall: their light is spread over it
  const panes = smooth(0.25, 1, density);
  const g = new Float32Array(3);
  for (let y = 0; y < h; y++) {
    const my = place ? place.top + (y - margin + 0.5) * step : 0;
    for (let x = 0; x < w; x++) {
      const i = y * w + x;
      const o = i * 4;
      const r = px[o] / 255;
      const gr = px[o + 1] / 255;
      const b = px[o + 2] / 255;
      const l = 0.299 * r + 0.587 * gr + 0.114 * b;
      const water = kinds[o];
      const veg = kinds[o + 1];
      const asphalt = kinds[o + 2];
      const paint = kinds[o + 3];
      grade(r, gr, b, g);
      let cr = g[0];
      let cg = g[1];
      let cb = g[2];

      const mx = place ? place.left + (x - margin + 0.5) * step : 0;
      const cov = place ? fieldAt(field, mx, my) : 0;
      if (cov > 0.001) {
        // wide roads: plenty of asphalt all around, not a grey roof or a lane between blocks
        const main = smooth(0.3, 0.7, far[i]);
        const road = Math.max(asphalt, paint * 0.8) * smooth(0.25, 0.6, near[i]) * main;
        const spill = far[i] * main * (1 - water) * 0.08;
        const lr = SODIUM[0] * (spill + road * 0.08) + r * SODIUM[0] * road * 0.45;
        const lg = SODIUM[1] * (spill + road * 0.08) + gr * SODIUM[1] * road * 0.45;
        const lb = SODIUM[2] * (spill + road * 0.08) + b * SODIUM[2] * road * 0.45;
        // building faces, by patches: their painted colour lit warm (pale roofs stay dark)
        const sat = Math.max(r, gr, b) - Math.min(r, gr, b);
        const building =
          Math.max(0, 1 - water - veg - asphalt - paint) *
          (1 - smooth(0.68, 0.86, l)) *
          (1 - smooth(0.25, 0.5, vegAround[i])) *
          (1 - smooth(0.3, 0.45, sat)) *
          // walls of windows are busy; paving and flat roofs are calm
          smooth(0.1, 0.22, busy[i]);
        const lit =
          building *
          (blocks > 0
            ? windowBlock(mx, my) * blocks + WINDOW_SHARE * 0.6 * (1 - blocks)
            : WINDOW_SHARE * 0.6);
        if (lit > 0.001) {
          // the painted panes brighter than the wall around them turn to warm light; the rest
          // of the patch only warms a little
          const pane = smooth(0.0, 0.06, l - around[i]);
          const amount = lit * ((0.08 + 0.62 * pane) * panes + 0.26 * (1 - panes));
          const glow = 0.1 + 0.8 * l;
          cr += (WARM[0] * glow - cr) * amount * cov;
          cg += (WARM[1] * glow - cg) * amount * cov;
          cb += (WARM[2] * glow - cb) * amount * cov;
        }
        cr += lr * cov;
        cg += lg * cov;
        cb += lb * cov;
      }
      if (water > 0.004) {
        // dark water, the road light along its shores laid faintly on it
        const glow = far[i] * 0.3 * cov;
        const wr = WATER[0] + r * 0.04 + SODIUM[0] * glow;
        const wg = WATER[1] + gr * 0.06 + SODIUM[1] * glow;
        const wb = WATER[2] + b * 0.1 + SODIUM[2] * glow;
        cr += (wr - cr) * water;
        cg += (wg - cg) * water;
        cb += (wb - cb) * water;
      }
      // a soft shoulder for the brightest light
      px[o] = shoulder(cr);
      px[o + 1] = shoulder(cg);
      px[o + 2] = shoulder(cb);
    }
  }
}

function shoulder(v: number): number {
  const s = v > 0.75 ? v / (1 + (v - 0.75)) : v;
  return Math.round(Math.min(1, Math.max(0, s)) * 255);
}
