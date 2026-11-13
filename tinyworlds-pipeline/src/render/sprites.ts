import { hash2 } from "../scene/hash.js";
import { R } from "./palette.js";

/** A tiny bitmap anchored at its foot. Pixel codes: 0 empty, 1 highlight, 2 body, 3 shade, 4 trunk. */
export interface Sprite {
  w: number;
  h: number;
  /** Column of the foot within the sprite. */
  anchorX: number;
  /** Radius of the crown, px: sizes the shadow it casts. */
  r: number;
  px: Uint8Array;
}

/**
 * A tree: a lumpy ball of leaves lit from the upper left, on a short trunk. Drawn by rule rather
 * than by hand so there can be many sizes and no two alike.
 */
function makeTree(r: number, variant: number): Sprite {
  const rx = r;
  const ry = Math.max(2, Math.round(r * 0.9));
  const w = rx * 2 + 1;
  const crown = ry * 2 + 1;
  const trunk = Math.max(1, Math.round(r * 0.4));
  const h = crown + trunk;
  const px = new Uint8Array(w * h);
  for (let y = 0; y < crown; y++) {
    for (let x = 0; x < w; x++) {
      const nx = (x - rx) / (rx + 0.35);
      const ny = (y - ry) / (ry + 0.35);
      const noise = hash2(x + variant * 31, y + variant * 17);
      // a ragged outline: the rim comes and goes
      if (nx * nx + ny * ny > 1 - ((noise & 7) / 7) * 0.2) continue;
      const light = -nx * 0.62 - ny * 0.7 + (((noise >>> 3) & 7) / 7 - 0.5) * 0.3;
      px[y * w + x] = light > 0.4 ? 1 : light > -0.32 ? 2 : 3;
    }
  }
  for (let y = crown - 1; y < h; y++) {
    for (let x = rx - (r > 5 ? 1 : 0); x <= rx; x++) if (px[y * w + x] === 0) px[y * w + x] = 4;
  }
  return { w, h, anchorX: rx, r, px };
}

/** Tree sizes by setting: 0 street, 1 park, 2 forest, 3 scrub. */
const SIZES: number[][] = [
  [3, 4, 4, 5],
  [4, 5, 5, 6, 6, 7],
  [5, 6, 6, 7, 7, 8],
  [2, 2, 3],
];
const VARIANTS = 4;

export const TREE_STREET = 0;
export const TREE_PARK = 1;
export const TREE_FOREST = 2;
export const TREE_SCRUB = 3;

const TREES: Sprite[][] = SIZES.map((sizes) =>
  sizes.flatMap((r) => Array.from({ length: VARIANTS }, (_, v) => makeTree(r, v + r * 7))),
);

/**
 * A palm: a slim trunk and a few drooping fronds, drawn by rule so sizes and angles vary.
 * Anchored at the foot of the trunk, like the leafy trees.
 */
function makePalm(r: number, variant: number): Sprite {
  const frondR = Math.max(3, r + 1);
  const trunkH = Math.max(3, Math.round(r * 1.7));
  const w = frondR * 2 + 1;
  const cx = frondR;
  const cy = Math.max(2, Math.round(frondR * 0.85));
  const h = cy + 1 + trunkH;
  const px = new Uint8Array(w * h);
  const fronds = 6 + (variant % 3);
  const tilt = ((variant * 17) % 11) * 0.05;
  for (let i = 0; i < fronds; i++) {
    const ang = (i / fronds) * Math.PI * 2 + tilt;
    const reach = frondR * (0.75 + ((variant + i) % 3) * 0.08);
    const len = Math.max(3, Math.round(reach));
    for (let t = 0; t <= len; t++) {
      const drop = (t / len) * (t / len) * 0.55;
      const x = Math.round(cx + Math.cos(ang) * t);
      const y = Math.round(cy + Math.sin(ang) * t * 0.62 + drop * frondR * 0.35);
      if (x < 0 || x >= w || y < 0 || y >= h) continue;
      const lit = Math.cos(ang) < 0.15 && Math.sin(ang) < 0.2;
      const tip = t > len * 0.72;
      px[y * w + x] = tip || lit ? 1 : Math.cos(ang) < -0.35 ? 3 : 2;
      if (t > 1 && t < len - 1) {
        const x2 = Math.round(x - Math.sin(ang));
        const y2 = Math.round(y + Math.cos(ang) * 0.4);
        if (x2 >= 0 && x2 < w && y2 >= 0 && y2 < h && px[y2 * w + x2] === 0) px[y2 * w + x2] = 2;
      }
    }
  }
  // a bright crown at the join
  if (cx < w && cy < h) px[cy * w + cx] = 1;
  for (let y = cy; y < h; y++) {
    if (px[y * w + cx] === 0) px[y * w + cx] = 4;
    if (r >= 5 && cx + 1 < w && y > cy + 1 && px[y * w + cx + 1] === 0) px[y * w + cx + 1] = 4;
  }
  return { w, h, anchorX: cx, r: frondR, px };
}


/**
 * A chunky Habbo-style palm: fat trunk, a simple round crown of blocky fronds, 1 px dark outline
 * (sprite code 5). Toy-like and regular, not a wispy spray of fronds.
 */
function makeHabboPalm(r: number, variant: number): Sprite {
  const frondR = Math.max(4, r + 1);
  const trunkH = Math.max(4, Math.round(r * 1.4));
  const pad = 1;
  const innerW = frondR * 2 + 1;
  const cx = frondR + pad;
  const cy = Math.max(3, Math.round(frondR * 0.7)) + pad;
  const w = innerW + pad * 2;
  const h = cy + 1 + trunkH + pad;
  const body = new Uint8Array(w * h);
  const fronds = 7 + (variant % 2);
  const tilt = ((variant * 13) % 9) * 0.04;
  for (let i = 0; i < fronds; i++) {
    const ang = (i / fronds) * Math.PI * 2 + tilt;
    const len = Math.max(3, Math.round(frondR * (0.85 + ((variant + i) % 3) * 0.05)));
    for (let t = 0; t <= len; t++) {
      const drop = (t / len) * (t / len) * 0.4;
      const x = Math.round(cx + Math.cos(ang) * t);
      const y = Math.round(cy + Math.sin(ang) * t * 0.58 + drop * frondR * 0.28);
      for (const [dx, dy] of [[0, 0], [1, 0], [0, 1], [-1, 0]] as const) {
        const xx = x + dx;
        const yy = y + dy;
        if (xx < 0 || xx >= w || yy < 0 || yy >= h) continue;
        const lit = Math.cos(ang) < 0.2 && Math.sin(ang) < 0.25;
        const tip = t > len * 0.7;
        const code = tip || lit ? 1 : Math.cos(ang) < -0.3 ? 3 : 2;
        if (body[yy * w + xx] === 0) body[yy * w + xx] = code;
      }
    }
  }
  if (cx < w && cy < h) body[cy * w + cx] = 1;
  // fat 2 px trunk
  for (let y = cy; y < h - pad; y++) {
    for (const dx of r >= 5 ? [-1, 0, 1] : [0, 1]) {
      const xx = cx + dx;
      if (xx >= 0 && xx < w && body[y * w + xx] === 0) body[y * w + xx] = 4;
    }
  }
  // 1 px ink outline in empty neighbours
  const px = new Uint8Array(body);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      if (body[y * w + x] !== 0) continue;
      let n = false;
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]] as const) {
        const xx = x + dx;
        const yy = y + dy;
        if (xx < 0 || yy < 0 || xx >= w || yy >= h) continue;
        const c = body[yy * w + xx];
        if (c !== 0) n = true;
      }
      if (n) px[y * w + x] = 5;
    }
  }
  return { w, h, anchorX: cx, r: frondR, px };
}

const PALMS: Sprite[] = [3, 4, 4, 5, 6, 7].flatMap((r, i) => [0, 1, 2, 3].map((v) => makePalm(r, v + i * 5)));
const HABBO_PALMS: Sprite[] = [4, 5, 5, 6, 7, 8].flatMap((r, i) =>
  [0, 1, 2, 3].map((v) => makeHabboPalm(r, v + i * 5)),
);


/** True when this seed should be a palm rather than a leafy crown. */
export function isPalmSeed(seed: number, setting: number): boolean {
  const r = (seed >>> 1) % 10;
  // more palms along streets than in a forest
  if (setting === TREE_STREET) return r < 5;
  if (setting === TREE_PARK) return r < 3;
  if (setting === TREE_SCRUB) return r < 2;
  return r < 1;
}

export function treeSprite(setting: number, seed: number, palms = false, habbo = false): Sprite {
  if (palms && isPalmSeed(seed, setting)) {
    const set = habbo ? HABBO_PALMS : PALMS;
    return set[seed % set.length];
  }
  const set = TREES[setting];
  const s = set[seed % set.length];
  return habbo ? outlineSprite(s) : s;
}

/** Expand a sprite by 1 px and ink every empty neighbour of a filled pixel (Habbo outline). */
function outlineSprite(s: Sprite): Sprite {
  const w = s.w + 2;
  const h = s.h + 2;
  const body = new Uint8Array(w * h);
  for (let y = 0; y < s.h; y++) {
    for (let x = 0; x < s.w; x++) body[(y + 1) * w + (x + 1)] = s.px[y * s.w + x];
  }
  const px = new Uint8Array(body);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      if (body[y * w + x] !== 0) continue;
      let n = false;
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]] as const) {
        const xx = x + dx;
        const yy = y + dy;
        if (xx < 0 || yy < 0 || xx >= w || yy >= h) continue;
        if (body[yy * w + xx] !== 0) n = true;
      }
      if (n) px[y * w + x] = 5;
    }
  }
  return { w, h, anchorX: s.anchorX + 1, r: s.r, px };
}

export type Flora = "temperate" | "tropical" | "subtropical" | "boreal" | "montane";

/**
 * Mostly green. In a temperate city now and then one has already turned; in a tropical one it is
 * in flower instead: a yellow or pink ipê, a red flamboyant; in a subtropical one it is all green;
 * in the far north it is dark evergreens, some of them white with snow; in the mountains of the
 * west it is dark pines and firs with the lighter green of aspens and willows here and there.
 */
export function treeRamp(seed: number, flora: Flora = "temperate"): number {
  const r = seed % 200;
  if (flora === "boreal") {
    if (r < 104) return R.pine;
    if (r < 168) return R.leafDark;
    return R.snow;
  }
  if (flora === "montane") {
    if (r < 100) return R.pine;
    if (r < 160) return R.leafDark;
    if (r < 188) return R.leaf;
    return R.leafLight;
  }
  if (flora === "subtropical") {
    // evergreen: live oaks and palms, nothing turning and nothing in flower
    if (r < 70) return R.leaf;
    if (r < 118) return R.leafLight;
    return R.leafDark;
  }
  if (flora === "tropical") {
    if (r < 66) return R.leaf;
    if (r < 108) return R.leafLight;
    if (r < 190) return R.leafDark;
    if (r < 194) return R.ipeYellow;
    if (r < 197) return R.ipePink;
    return R.flamboyant;
  }
  if (r < 78) return R.leaf;
  if (r < 124) return R.leafLight;
  if (r < 189) return R.leafDark;
  if (r < 197) return R.leafAutumn;
  return R.leafRed;
}
