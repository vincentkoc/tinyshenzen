import { describe, expect, it } from "vitest";
import { MARGIN, gridData, gridFrom, nightPaper, nightTile } from "../night-look";

/** A busy made-up city: roads, towers, trees and water, as RGBA. */
function city(w: number, h: number): Uint8ClampedArray {
  const px = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const o = (y * w + x) * 4;
      const road = (x + 2 * y) % 23 < 5;
      const water = y > h * 0.8;
      const tree = !road && (x * 7 + y * 3) % 53 < 9;
      const pane = (x % 4 < 2 ? 40 : 0) + (y % 5 < 2 ? 30 : 0);
      const [r, g, b] = water
        ? [40, 120, 150]
        : road
          ? [72, 73, 78]
          : tree
            ? [60, 140, 50]
            : [90 + pane, 130 + pane, 170 + pane];
      // a little unevenness everywhere, so no two stretches of the picture are alike
      const n = (((x >> 1) * 73856093) ^ ((y >> 1) * 19349663)) & 31;
      px.set([r + n, g + n, b - n, 255], o);
    }
  }
  return px;
}

function crop(src: Uint8ClampedArray, sw: number, x0: number, y0: number, w: number, h: number) {
  const out = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++)
    out.set(src.subarray(((y0 + y) * sw + x0) * 4, ((y0 + y) * sw + x0 + w) * 4), y * w * 4);
  return out;
}

describe("nightTile", () => {
  it("agrees across a tile edge when each tile is handed its neighbours", () => {
    const S = 128;
    const M = MARGIN;
    const W = 3 * S + 2 * M;
    const H = S + 2 * M;
    const world = city(W, H);
    // a wide paved square just past the edge, in B: what A's edge glows with comes from it
    for (let y = 0; y < H; y++) {
      for (let x = M + S + 6; x < M + S + 20; x++) world.set([72, 73, 78, 255], (y * W + x) * 4);
    }
    // tile A, tile B to its right, and a tile straddling the edge between them
    const run = (offset: number) => {
      const px = crop(world, W, offset, 0, S + 2 * M, H);
      nightTile(px, S + 2 * M, H, { left: offset, top: 0, span: S }, null, M);
      return px;
    };
    const a = run(0);
    const b = run(S);
    const mid = run(S / 2);
    const at = (px: Uint8ClampedArray, x: number, y: number) =>
      Array.from(
        px.subarray(((y + M) * (S + 2 * M) + x + M) * 4, ((y + M) * (S + 2 * M) + x + M) * 4 + 3),
      );
    for (let y = 0; y < S; y += 7) {
      expect(at(a, S - 1, y)).toEqual(at(mid, S / 2 - 1, y));
      expect(at(b, 0, y)).toEqual(at(mid, S / 2, y));
    }
  });

  it("keeps transparent pixels transparent and darkens the rest", () => {
    const px = city(64, 64);
    px[3] = 0;
    const before = px.slice();
    nightTile(px, 64, 64, { left: 0, top: 0, span: 64 }, null, 0);
    expect(px[3]).toBe(0);
    let lighter = 0;
    for (let i = 4; i < px.length; i += 4)
      if (px[i] + px[i + 1] + px[i + 2] > before[i] + before[i + 1] + before[i + 2] + 30) lighter++;
    expect(lighter / (px.length / 4)).toBeLessThan(0.2);
  });
});

describe("nightPaper", () => {
  it("is darker than the paper", () => {
    const [r, g, b] = nightPaper([238, 234, 224]);
    expect(r + g + b).toBeLessThan(238 + 234 + 224);
  });
});

describe("gridData", () => {
  it("can be posted to a worker", () => {
    const grid = {
      cols: 2,
      rows: 1,
      originX: 0,
      originY: 0,
      span: 512,
      has: (tx: number) => tx === 1,
    };
    const data = gridData(grid);
    expect(() => structuredClone(data)).not.toThrow();
    expect(gridFrom(structuredClone(data))?.has(1, 0)).toBe(true);
    expect(gridFrom(structuredClone(data))?.has(0, 0)).toBe(false);
  });
});
