import { describe, expect, it } from "vitest";
import {
  coverageMask,
  DEFAULT_PAPER,
  edgeFog,
  featherMask,
  FOG_FROM,
  FOG_FULL,
  FOG_REACH_FAR,
  FOG_REACH_NEAR,
  fogRadius,
  paperRgb,
} from "../edge-fog";
import { WORLDS } from "@/worlds";

describe("edgeFog", () => {
  it("leaves the edges alone zoomed in", () => {
    expect(edgeFog(FOG_FROM, 0.01)).toEqual({ opacity: 0, reach: 0 });
    expect(edgeFog(3, 0.01).opacity).toBe(0);
  });

  it("fades in, then reaches further the further out you zoom", () => {
    const near = edgeFog(0.8, 0.01);
    expect(near.opacity).toBeGreaterThan(0);
    expect(near.opacity).toBeLessThan(1);
    expect(edgeFog(FOG_FULL, 0.01).opacity).toBe(1);
    let last = 0;
    for (const scale of [0.9, 0.6, 0.3, 0.1, 0.03, 0.01]) {
      const { reach } = edgeFog(scale, 0.01);
      expect(reach).toBeGreaterThan(last);
      last = reach;
    }
    expect(edgeFog(0.01, 0.01).reach).toBeCloseTo(FOG_REACH_FAR);
    expect(edgeFog(FOG_FROM * 0.999, 0.01).reach).toBeCloseTo(FOG_REACH_NEAR, 0);
    // full strength wherever a coarser level (with its baked-in fill) is drawn
    expect(edgeFog(0.3, 0.01).opacity).toBe(1);
  });

  it("is off for a nonsense scale", () => {
    expect(edgeFog(0, 0.01).opacity).toBe(0);
    expect(edgeFog(NaN, 0.01).opacity).toBe(0);
  });
});

describe("fogRadius", () => {
  it("turns a reach on screen into mask pixels, capped", () => {
    // 40 CSS px at scale 0.05 is 800 map px: 3.1 cells of 256, so a box radius of 2
    expect(fogRadius(40, 0.05, 256)).toBe(2);
    expect(fogRadius(56, 0.0001, 256)).toBe(24);
    expect(fogRadius(0, 0.05, 256)).toBe(0);
  });
});

describe("coverageMask and featherMask", () => {
  // a 4 × 3 grid with tiles in an L: the stepped outline the fog has to hide
  const tiles = new Set(["0,0", "1,0", "2,0", "0,1", "1,1", "0,2"]);
  const has = (tx: number, ty: number) => tiles.has(`${tx},${ty}`);

  it("is fog outside the tiles and clear on them, padding included", () => {
    const { data, width, height } = coverageMask(4, 3, has, 2, 3);
    expect(width).toBe(4 * 2 + 6);
    expect(height).toBe(3 * 2 + 6);
    const at = (x: number, y: number) => data[y * width + x];
    expect(at(0, 0)).toBe(1); // padding
    expect(at(3, 3)).toBe(0); // tile (0, 0)
    expect(at(3 + 6, 3)).toBe(1); // tile (3, 0) is missing
    expect(at(3 + 2, 3 + 4)).toBe(1); // tile (1, 2) is missing
  });

  it("is solid where a tile stops and fades to clear inside, with no hard step", () => {
    // 16 × 16 tiles, the right quarter missing; look along the middle row
    const { data, width, height } = coverageMask(16, 16, (tx) => tx < 12, 2, 8);
    const out = featherMask(data, width, height, 2);
    const row = (8 + 16) * width;
    const edge = 8 + 12 * 2; // first mask pixel of the missing tile (12, 8)
    expect(out[row + edge]).toBe(1);
    expect(out[row + edge - 1]).toBeGreaterThan(0.5);
    expect(out[row + edge - 12]).toBe(0); // well inside the map: untouched
    // a gentle ramp: no neighbour jumps by more than half the way
    for (let x = edge - 12; x < edge; x++)
      expect(Math.abs(out[row + x + 1] - out[row + x])).toBeLessThan(0.5);
  });

  it("without blur keeps the outline as it is", () => {
    const { data, width, height } = coverageMask(2, 1, (tx) => tx === 0, 1, 1);
    expect([...featherMask(data, width, height, 0)]).toEqual([...data]);
  });
});

describe("paper", () => {
  it("defaults to the tiles' fog", () => {
    expect(paperRgb(undefined)).toEqual([0xe9, 0xe6, 0xdf]);
    expect(paperRgb("nope")).toEqual(paperRgb(DEFAULT_PAPER));
    expect(paperRgb("#fff")).toEqual([255, 255, 255]);
    expect(paperRgb("#0a0b0c")).toEqual([10, 11, 12]);
  });

  it("is a valid colour wherever a world sets one", () => {
    for (const world of Object.values(WORLDS)) {
      if (world.paper !== undefined) expect(world.paper).toMatch(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
    }
  });
});
