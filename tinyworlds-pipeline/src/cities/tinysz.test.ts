import { describe, expect, it } from "vitest";
import { CITIES, DEFAULT_CITY } from "./index.js";
import { SHENZHEN } from "../scene/styles.js";
import { CENTER, FULL_BBOX, TEST_BBOX, tinysz, tinyszFull } from "./tinysz.js";

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
    expect(tinysz.id).toBe("tinysz");
    expect(tinyszFull.id).toBe("tinysz-full");
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
    expect(tinysz.look?.skyGlass).toBe(true);
    expect(tinysz.look?.canyonAO).toBe(true);
    expect(tinysz.look?.palms).toBe(true);
    expect(tinysz.look?.roofFurniture).toBe(true);
    expect(tinysz.flora).toBe("tropical");
    expect(tinyszFull.flora).toBe("tropical");
    expect(tinysz.sources.map((s) => s.name)).toEqual(tinyszFull.sources.map((s) => s.name));
    expect(tinysz.sources.length).toBeGreaterThanOrEqual(2);
    expect(tinysz.bbox).toEqual(TEST_BBOX);
    expect(tinyszFull.bbox).toEqual(FULL_BBOX);
    expect(tinysz.curated.some((p) => p.n === "Ping An Finance Centre")).toBe(true);
  });
});
