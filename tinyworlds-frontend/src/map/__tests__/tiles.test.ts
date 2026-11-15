import { describe, expect, it } from "vitest";
import { ancestors, levelForScale, pinLevels, planTiles, Pyramid, tileKey } from "../tiles";

/** row-major, LSB-first, like the pipeline writes it */
function bitset(cols: number, rows: number, has: (tx: number, ty: number) => boolean): string {
  const bytes = new Uint8Array(Math.ceil((cols * rows) / 8));
  for (let ty = 0; ty < rows; ty++)
    for (let tx = 0; tx < cols; tx++) {
      const i = tx + ty * cols;
      if (has(tx, ty)) bytes[i >> 3] |= 1 << (i & 7);
    }
  return Buffer.from(bytes).toString("base64");
}

const pyramid = new Pyramid({
  tileSize: 512,
  origin: { x: -2048, y: -1024 },
  levels: [
    { z: 0, cols: 8, rows: 4, tiles: bitset(8, 4, (tx, ty) => !(tx === 3 && ty === 1)) },
    { z: 1, cols: 4, rows: 2, tiles: bitset(4, 2, () => true) },
    { z: 2, cols: 2, rows: 1, tiles: bitset(2, 1, () => true) },
  ],
});

describe("levelForScale", () => {
  it("uses native tiles at and above scale 1", () => {
    expect(levelForScale(1, 5)).toBe(0);
    expect(levelForScale(3, 5)).toBe(0);
  });
  it("steps down one level per halving", () => {
    expect(levelForScale(0.75, 5)).toBe(0);
    expect(levelForScale(0.5, 5)).toBe(1);
    expect(levelForScale(0.3, 5)).toBe(1);
    expect(levelForScale(0.25, 5)).toBe(2);
    expect(levelForScale(0.124, 5)).toBe(3);
  });
  it("clamps to the levels that exist", () => {
    expect(levelForScale(0.001, 3)).toBe(3);
    expect(levelForScale(0, 3)).toBe(3);
    expect(levelForScale(Number.NaN, 3)).toBe(3);
  });
});

describe("Pyramid", () => {
  it("knows which tiles exist", () => {
    expect(pyramid.has(0, 0, 0)).toBe(true);
    expect(pyramid.has(0, 3, 1)).toBe(false);
    expect(pyramid.has(0, 8, 0)).toBe(false);
    expect(pyramid.has(0, -1, 0)).toBe(false);
    expect(pyramid.has(7, 0, 0)).toBe(false);
    expect(pyramid.maxZ).toBe(2);
    expect(pyramid.all(0)).toHaveLength(31);
  });

  it("places tiles from the shared origin, doubling per level", () => {
    expect(pyramid.tileRect(0, 0, 0)).toEqual({
      left: -2048,
      top: -1024,
      right: -1536,
      bottom: -512,
    });
    expect(pyramid.tileRect(1, 1, 1)).toEqual({ left: -1024, top: 0, right: 0, bottom: 1024 });
    expect(pyramid.tileRect(2, 1, 0)).toEqual({ left: 0, top: -1024, right: 2048, bottom: 1024 });
  });

  it("finds the tiles a view touches", () => {
    const r = pyramid.range(0, { left: -100, top: -100, right: 600, bottom: 100 });
    expect(r).toEqual({ z: 0, tx0: 3, ty0: 1, tx1: 5, ty1: 2 });
  });

  it("does not include a tile the view only touches at its edge", () => {
    const r = pyramid.range(0, { left: 0, top: 0, right: 512, bottom: 512 });
    expect(r).toEqual({ z: 0, tx0: 4, ty0: 2, tx1: 4, ty1: 2 });
  });

  it("clamps to the grid, and is empty beyond it", () => {
    const r = pyramid.range(0, { left: -99999, top: -99999, right: 99999, bottom: 99999 });
    expect(r).toEqual({ z: 0, tx0: 0, ty0: 0, tx1: 7, ty1: 3 });
    const off = pyramid.range(0, { left: 5000, top: 0, right: 6000, bottom: 100 });
    expect(off.tx1).toBeLessThan(off.tx0);
  });

  it("names tiles the way the files are named", () => {
    expect(tileKey(2, 10, 7)).toBe("2/10_7");
  });
});

describe("ancestors", () => {
  it("walks to coarser tiles, nearest first", () => {
    expect(ancestors(0, 5, 3, 3)).toEqual([
      { z: 1, tx: 2, ty: 1 },
      { z: 2, tx: 1, ty: 0 },
      { z: 3, tx: 0, ty: 0 },
    ]);
  });
  it("is empty at the top of the pyramid", () => {
    expect(ancestors(3, 0, 0, 3)).toEqual([]);
  });
});

describe("Pyramid.pad and count", () => {
  it("grows a range by a margin, clamped to the grid", () => {
    const r = pyramid.range(0, { left: 0, top: 0, right: 512, bottom: 512 });
    expect(r).toEqual({ z: 0, tx0: 4, ty0: 2, tx1: 4, ty1: 2 });
    expect(pyramid.pad(r, 1)).toEqual({ z: 0, tx0: 3, ty0: 1, tx1: 5, ty1: 3 });
    expect(pyramid.pad(r, 99)).toEqual({ z: 0, tx0: 0, ty0: 0, tx1: 7, ty1: 3 });
  });
  it("leaves an empty range empty", () => {
    const off = pyramid.range(0, { left: 5000, top: 0, right: 6000, bottom: 100 });
    expect(pyramid.pad(off, 1).tx1).toBeLessThan(pyramid.pad(off, 1).tx0);
  });
  it("counts the tiles that exist", () => {
    expect(pyramid.count(0)).toBe(31);
    expect(pyramid.count(1)).toBe(8);
    expect(pyramid.count(7)).toBe(0);
  });
});

describe("pinLevels", () => {
  it("pins every coarse level that stays under the cap", () => {
    // z=2 has 2 tiles, z=1 has 8, z=0 has 31: with a small cap only the top two are pinned
    expect(pinLevels(pyramid, 40, 16)).toEqual([2, 1]);
    expect(pinLevels(pyramid, 40, 40)).toEqual([2, 1, 0]);
  });
  it("pins nothing when even the top level is too large", () => {
    expect(pinLevels(pyramid, 1, 16)).toEqual([]);
  });
});

describe("planTiles", () => {
  const view = {
    left: 0,
    top: 0,
    right: 512,
    bottom: 512,
    x: 256,
    y: 256,
    scale: 1,
    dpr: 1,
  };

  it("asks for the visible tile first, then its parents, then a one-tile ring", () => {
    const plan = planTiles(
      pyramid,
      view,
      () => false,
      () => false,
    );
    expect(plan.z).toBe(0);
    expect(plan.range).toEqual({ z: 0, tx0: 4, ty0: 2, tx1: 4, ty1: 2 });
    const byPri: Record<number, string[]> = { 0: [], 1: [], 2: [] };
    for (const w of plan.wanted) byPri[w.pri].push(tileKey(w.z, w.tx, w.ty));
    expect(byPri[0]).toEqual(["0/4_2"]);
    // nearest parent, then the level above; the hole at 0/3_1 is skipped in the ring
    expect(byPri[1]).toEqual(["1/2_1", "2/1_0"]);
    expect(byPri[2]).toContain("0/5_2");
    expect(byPri[2]).toContain("0/4_1");
    expect(byPri[2]).not.toContain("0/3_1"); // missing from the pyramid
    expect(byPri[2]).not.toContain("0/4_2"); // already wanted as visible
    expect(plan.wanted[0].pri).toBe(0);
    expect(plan.wanted.some((w, i) => i > 0 && w.pri < plan.wanted[i - 1].pri)).toBe(false);
  });

  it("keeps cached parents and does not ask for a tile that is already ready", () => {
    const ready = new Set(["0/4_2", "1/2_1"]);
    const plan = planTiles(
      pyramid,
      view,
      (key) => ready.has(key),
      (key) => ready.has(key),
    );
    expect(plan.wanted.some((w) => w.z === 0 && w.tx === 4 && w.ty === 2)).toBe(false);
    expect(plan.wanted.some((w) => w.z === 1)).toBe(false);
    expect(plan.retain).toContainEqual({ z: 1, tx: 2, ty: 1 });
    // ring tiles are still fetched
    expect(plan.wanted.some((w) => w.pri === 2)).toBe(true);
  });

  it("still asks for a parent when the visible tile is only loading", () => {
    const has = new Set(["0/4_2"]);
    const plan = planTiles(
      pyramid,
      view,
      (key) => has.has(key),
      () => false,
    );
    expect(plan.wanted.some((w) => w.z === 0 && w.tx === 4 && w.ty === 2)).toBe(false);
    expect(plan.wanted).toContainEqual(expect.objectContaining({ z: 1, tx: 2, ty: 1, pri: 1 }));
  });

  it("can skip the ring and skip native tiles while a pan is in flight", () => {
    const none = planTiles(
      pyramid,
      view,
      () => false,
      () => false,
      { ring: 0 },
    );
    expect(none.wanted.every((w) => w.pri !== 2)).toBe(true);
    expect(none.wanted.some((w) => w.z === 0 && w.tx === 4 && w.ty === 2)).toBe(true);

    const deferred = planTiles(
      pyramid,
      view,
      () => false,
      () => false,
      {
        ring: 0,
        deferNative: true,
      },
    );
    expect(deferred.wanted.some((w) => w.z === 0)).toBe(false);
    expect(deferred.wanted.some((w) => w.z === 1 && w.tx === 2 && w.ty === 1)).toBe(true);
  });

  it("stretches the ring in the direction of a pan", () => {
    const east = planTiles(
      pyramid,
      view,
      () => false,
      () => false,
      { ring: 0, aheadTx: 1 },
    );
    const keys = east.wanted.filter((w) => w.pri === 2).map((w) => tileKey(w.z, w.tx, w.ty));
    expect(keys).toContain("0/5_2");
    expect(keys).not.toContain("0/3_2");
  });
});
