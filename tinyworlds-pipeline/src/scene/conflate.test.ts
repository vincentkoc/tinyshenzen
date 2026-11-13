import { describe, expect, it } from "vitest";
import {
  HEIGHT_MATCH_THRESHOLD,
  OVERLAP_THRESHOLD,
  bestHeightMatch,
  overlap,
  overlapsEnough,
  ringArea,
  shouldAddFootprint,
} from "./conflate.js";

/** Axis-aligned rectangle as a closed ring: (x,y) is the lower-left, in any unit. */
function rect(x: number, y: number, w: number, h: number): number[] {
  return [x, y, x + w, y, x + w, y + h, x, y + h];
}

describe("footprint conflation", () => {
  it("measures area and IoU of axis-aligned rectangles", () => {
    expect(ringArea(rect(0, 0, 2, 2))).toBe(4);
    // 2×2 at the origin and 2×2 offset by (1,1): intersection 1, union 7, IoU 1/7
    const o = overlap(rect(0, 0, 2, 2), rect(1, 1, 2, 2));
    expect(o.intersection).toBeCloseTo(1, 8);
    expect(o.union).toBeCloseTo(7, 8);
    expect(o.iou).toBeCloseTo(1 / 7, 8);
    expect(o.overA).toBeCloseTo(0.25, 8);
    expect(o.overB).toBeCloseTo(0.25, 8);
  });

  it("treats identical footprints as a complete overlap", () => {
    const a = rect(10, 10, 4, 3);
    const o = overlap(a, a);
    expect(o.iou).toBeCloseTo(1, 8);
    expect(o.overA).toBe(1);
    expect(overlapsEnough(a, a)).toBe(true);
  });

  it("does not add a new footprint that sits inside, swallows, or substantially overlaps an existing one", () => {
    const block = rect(0, 0, 10, 10);
    // a shed fully inside the block: IoU is small (4/100) but overA is 1
    expect(shouldAddFootprint(rect(2, 2, 2, 2), [block])).toBe(false);
    // a large new polygon that swallows the block
    expect(shouldAddFootprint(rect(-5, -5, 20, 20), [block])).toBe(false);
    // substantial partial overlap (half the new building)
    expect(shouldAddFootprint(rect(5, 0, 10, 10), [block])).toBe(false);
  });

  it("adds a footprint that only nicks an existing one, or sits clear of it", () => {
    const block = rect(0, 0, 10, 10);
    // 1×1 corner nick: 1/100 of the block, 1/1 of a 1×1 would be 100% — use a 5×5 that overlaps 1
    // overlap 1 on a 5×5 is 0.04, on the 10×10 is 0.01: both under 0.2
    expect(shouldAddFootprint(rect(9, 9, 5, 5), [block], OVERLAP_THRESHOLD)).toBe(true);
    expect(shouldAddFootprint(rect(20, 20, 3, 3), [block])).toBe(true);
  });

  it("picks the highest-IoU overlapping height and ignores weak matches", () => {
    const building = rect(0, 0, 4, 4);
    const weak = bestHeightMatch(building, [{ ring: rect(10, 10, 2, 2), height: 99 }]);
    expect(weak).toBeNull();
    const hit = bestHeightMatch(building, [
      { ring: rect(0, 0, 3, 4), height: 18 }, // IoU = 12/16 = 0.75
      { ring: rect(2, 0, 4, 4), height: 40 }, // IoU = 8/24 ≈ 0.33
    ]);
    expect(hit?.height).toBe(18);
    expect(hit!.iou).toBeGreaterThan(HEIGHT_MATCH_THRESHOLD);
  });
});
