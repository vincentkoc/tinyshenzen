import { describe, expect, it } from "vitest";
import { FULL_BBOX, TEST_BBOX } from "../cities/tinysz.js";
import { GLOBFP_DOI, GLOBFP_TILES, globfp, globfpTiles } from "./globfp.js";

describe("3D-GloBFP tiles", () => {
  it("picks only grid 2265 for the Futian test box, and both 2265 and 2269 for the urban core", () => {
    expect(globfpTiles(TEST_BBOX).map((t) => t.id)).toEqual([2265]);
    expect(globfpTiles(FULL_BBOX).map((t) => t.id)).toEqual([2265, 2269]);
    // a box entirely north of 22.5° and west of 113.75° still hits 2265's west edge at 113.75
    expect(globfpTiles({ west: 113.8, south: 22.55, east: 113.9, north: 22.7 }).map((t) => t.id)).toEqual([2265]);
    expect(globfpTiles({ west: 10, south: 50, east: 11, north: 51 })).toEqual([]);
  });

  it("names the Figshare Guangdong / Hong Kong files and is a fetchable source", () => {
    expect(GLOBFP_TILES[0].file).toMatch(/guangdongCN/);
    expect(GLOBFP_TILES[0].url).toMatch(/figshare/);
    expect(GLOBFP_DOI).toMatch(/^10\./);
    expect(globfp.name).toBe("globfp");
    expect(globfp.files).toEqual(["globfp.parquet"]);
  });
});
