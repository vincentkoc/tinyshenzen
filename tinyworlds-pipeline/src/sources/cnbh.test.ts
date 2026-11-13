import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import zlib from "node:zlib";
import { afterEach, describe, expect, it } from "vitest";
import { loadHeights } from "../scene/heights.js";
import type { HeightIndex } from "./openBuildings.js";
import { fromUtm } from "../geom/utm.js";
import { cnbhFileName, cnbhTiles } from "./cnbh.js";

describe("cnbhTiles", () => {
  it("puts the Futian test box on X115Y23, just east of 114° E", () => {
    expect(cnbhTiles({ west: 114.048, south: 22.527, east: 114.088, north: 22.564 })).toEqual([
      { x: 115, y: 23 },
    ]);
    expect(cnbhFileName({ x: 115, y: 23 })).toBe("CNBH10m_X115Y23.tif");
  });

  it("spans two longitude tiles for the full Shenzhen urban core", () => {
    const tiles = cnbhTiles({ west: 113.8, south: 22.45, east: 114.3, north: 22.75 });
    expect(tiles).toEqual([
      { x: 113, y: 23 },
      { x: 115, y: 23 },
    ]);
  });
});

describe("loadHeights identity curve", () => {
  const dirs: string[] = [];
  afterEach(() => {
    for (const d of dirs) fs.rmSync(d, { recursive: true, force: true });
    dirs.length = 0;
  });

  it("returns the stored metres without the Open Buildings correction, and reads uint16 half-metres past 127 m", () => {
    const rawDir = fs.mkdtempSync(path.join(os.tmpdir(), "cnbh-"));
    dirs.push(rawDir);
    const dir = path.join(rawDir, "heights");
    fs.mkdirSync(dir);
    // a 3×2 tile in UTM zone 50, 10 m pixels, top-left (200000, 2496500)
    // pixel (1,1) — the centre of the tile — is 30 m (60 half-metres)
    // pixel (2,0) is 200 m, which a uint8 half-metre grid could not store
    const width = 3;
    const height = 2;
    const grid = new Uint16Array(width * height).fill(65535);
    grid[1 * width + 1] = 60; // 30 m
    grid[0 * width + 2] = 400; // 200 m
    fs.writeFileSync(path.join(dir, "cnbh.u16.gz"), zlib.gzipSync(Buffer.from(grid.buffer)));
    const index: HeightIndex = {
      zone: 50,
      south: false,
      res: 10,
      curve: "identity",
      tiles: [
        {
          file: "cnbh.u16.gz",
          x0: 200000,
          y1: 2496500,
          width,
          height,
          bytes: 2,
          unit: "half-metres",
        },
      ],
    };
    fs.writeFileSync(path.join(dir, "index.json"), JSON.stringify(index));
    const sampler = loadHeights(rawDir);
    expect(sampler).not.toBeNull();
    // a tiny ring around the centre of pixel (1,1): x=200015, y=2496485
    // toUtm inverse is unused: estimate takes lon/lat and converts with toUtm(zone 50)
    // We pass a ring already in lon/lat that toUtm maps into that pixel.
    // Easier: call estimate with a square we compute via fromUtm
    const [lon, lat] = fromUtm(200015, 2496485, 50, false);
    const ring = new Float64Array([
      lon - 0.00002,
      lat - 0.00002,
      lon + 0.00002,
      lat - 0.00002,
      lon + 0.00002,
      lat + 0.00002,
      lon - 0.00002,
      lat + 0.00002,
      lon - 0.00002,
      lat - 0.00002,
    ]);
    expect(sampler!.estimate(ring)).toBeCloseTo(30, 5);

    const [lon2, lat2] = fromUtm(200025, 2496495, 50, false);
    const tall = new Float64Array([
      lon2 - 0.00002,
      lat2 - 0.00002,
      lon2 + 0.00002,
      lat2 - 0.00002,
      lon2 + 0.00002,
      lat2 + 0.00002,
      lon2 - 0.00002,
      lat2 + 0.00002,
      lon2 - 0.00002,
      lat2 - 0.00002,
    ]);
    expect(sampler!.estimate(tall)).toBeCloseTo(200, 5);
  });
});
