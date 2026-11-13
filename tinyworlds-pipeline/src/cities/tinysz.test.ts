import { describe, expect, it } from "vitest";
import { CITIES, DEFAULT_CITY } from "./index.js";
import { SHENZHEN } from "../scene/styles.js";
import {
  CAIWUWEI,
  CENTER,
  CORE_BBOX,
  FULL_BBOX,
  HOUHAI,
  LUOHU_BBOX,
  NANSHAN_BBOX,
  TEST_BBOX,
  tinysz,
  tinyszCore,
  tinyszFull,
  tinyszLuohu,
  tinyszNanshan,
} from "./tinysz.js";

const inside = (
  bbox: { west: number; south: number; east: number; north: number },
  lon: number,
  lat: number,
) => lon >= bbox.west && lon <= bbox.east && lat >= bbox.south && lat <= bbox.north;

describe("tinysz", () => {
  it("is the default city and is registered under both box ids", () => {
    expect(DEFAULT_CITY).toBe("tinysz");
    expect(CITIES.tinysz).toBe(tinysz);
    expect(CITIES["tinysz-full"]).toBe(tinyszFull);
    expect(CITIES["tinysz-nanshan"]).toBe(tinyszNanshan);
    expect(CITIES["tinysz-luohu"]).toBe(tinyszLuohu);
    expect(CITIES["tinysz-core"]).toBe(tinyszCore);
    expect(tinysz.id).toBe("tinysz");
    expect(tinyszFull.id).toBe("tinysz-full");
    expect(tinyszNanshan.id).toBe("tinysz-nanshan");
    expect(tinyszLuohu.id).toBe("tinysz-luohu");
    expect(tinyszCore.id).toBe("tinysz-core");
  });

  it("puts the Civic Center and Ping An Finance Centre in the test box, and the test box in the full box", () => {
    expect(inside(TEST_BBOX, CENTER.lon, CENTER.lat)).toBe(true);
    expect(inside(TEST_BBOX, 114.050446, 22.536399)).toBe(true);
    expect(inside(FULL_BBOX, TEST_BBOX.west, TEST_BBOX.south)).toBe(true);
    expect(inside(FULL_BBOX, TEST_BBOX.east, TEST_BBOX.north)).toBe(true);
    // KK100, Window of the World and Shenzhen Bay sit in the urban core, not the test box
    expect(inside(TEST_BBOX, 114.10194, 22.54667)).toBe(false);
    expect(inside(FULL_BBOX, 114.10194, 22.54667)).toBe(true);
    expect(inside(FULL_BBOX, 113.9697556, 22.5375139)).toBe(true);
    expect(inside(FULL_BBOX, 113.994, 22.5095)).toBe(true);
  });

  it("draws from Overture, uses the Shenzhen style and tropical flora", () => {
    expect(tinysz.style).toBe(SHENZHEN);
    expect(tinyszFull.style).toBe(SHENZHEN);
    expect(tinysz.landmarks["ping an finance centre"]?.pattern).toBeDefined();
    expect(tinysz.heightFixes?.["ping an finance centre"]).toBe(599.1);
    expect(tinysz.look?.pixelsg).toBe(true);
    expect(tinysz.look?.habbo).toBeFalsy();
    expect(tinysz.look?.palms).toBe(true);
    expect(tinysz.look?.roofFurniture).toBe(true);
    expect(tinysz.flora).toBe("tropical");
    expect(tinyszFull.flora).toBe("tropical");
    expect(tinysz.sources.map((s) => s.name)).toEqual(tinyszFull.sources.map((s) => s.name));
    expect(tinysz.sources.map((s) => s.name)).toContain("cnbh");
    expect(tinysz.sources.map((s) => s.name)).toContain("globfp");
    expect(tinysz.heights.raster).toBe(true);
    expect(tinysz.attribution.some((a) => /CNBH-10m/.test(a))).toBe(true);
    expect(tinysz.attribution.some((a) => /3D-GloBFP/.test(a))).toBe(true);
    expect(tinysz.sources.length).toBeGreaterThanOrEqual(3);
    expect(tinysz.bbox).toEqual(TEST_BBOX);
    expect(tinyszFull.bbox).toEqual(FULL_BBOX);
    expect(tinysz.curated.some((p) => p.n === "Ping An Finance Centre")).toBe(true);
  });

  it("gives Nanshan and Luohu their own core boxes, contiguous with Futian and inside the full box", () => {
    // Shekou Sea World, Houhai (CR Headquarters), Science Park, Window of the World / OCT
    expect(inside(NANSHAN_BBOX, 113.916, 22.485)).toBe(true);
    expect(inside(NANSHAN_BBOX, HOUHAI.lon, HOUHAI.lat)).toBe(true);
    expect(inside(NANSHAN_BBOX, 113.951, 22.543)).toBe(true);
    expect(inside(NANSHAN_BBOX, 113.9697556, 22.5375139)).toBe(true);
    // Caiwuwei (KK100), Shun Hing Square, Dongmen, Luohu Port
    expect(inside(LUOHU_BBOX, CAIWUWEI.lon, CAIWUWEI.lat)).toBe(true);
    expect(inside(LUOHU_BBOX, 114.10583, 22.54528)).toBe(true);
    expect(inside(LUOHU_BBOX, 114.118, 22.548)).toBe(true);
    expect(inside(LUOHU_BBOX, 114.118, 22.532)).toBe(true);
    // Futian sits between them, not inside either district box
    expect(inside(NANSHAN_BBOX, CENTER.lon, CENTER.lat)).toBe(false);
    expect(inside(LUOHU_BBOX, CENTER.lon, CENTER.lat)).toBe(false);
    expect(NANSHAN_BBOX.east).toBe(TEST_BBOX.west);
    expect(LUOHU_BBOX.west).toBe(TEST_BBOX.east);
    expect(inside(CORE_BBOX, HOUHAI.lon, HOUHAI.lat)).toBe(true);
    expect(inside(CORE_BBOX, CENTER.lon, CENTER.lat)).toBe(true);
    expect(inside(CORE_BBOX, CAIWUWEI.lon, CAIWUWEI.lat)).toBe(true);
    expect(inside(FULL_BBOX, CORE_BBOX.west, CORE_BBOX.south)).toBe(true);
    expect(inside(FULL_BBOX, CORE_BBOX.east, CORE_BBOX.north)).toBe(true);
    expect(tinyszNanshan.bbox).toEqual(NANSHAN_BBOX);
    expect(tinyszLuohu.bbox).toEqual(LUOHU_BBOX);
    expect(tinyszCore.bbox).toEqual(CORE_BBOX);
    expect(tinyszNanshan.origin).toEqual(HOUHAI);
    expect(tinyszLuohu.origin).toEqual(CAIWUWEI);
    expect(tinyszCore.origin).toEqual(CENTER);
    expect(tinyszNanshan.look?.pixelsg).toBe(true);
    expect(tinyszLuohu.look?.pixelsg).toBe(true);
    expect(tinyszCore.look?.pixelsg).toBe(true);
    expect(tinyszNanshan.start.lon).toBe(HOUHAI.lon);
    expect(tinyszLuohu.start.lon).toBe(CAIWUWEI.lon);
  });
});
