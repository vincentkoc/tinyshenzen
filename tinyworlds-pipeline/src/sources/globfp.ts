import fs from "node:fs";
import path from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import { DuckDBInstance } from "@duckdb/node-api";
import { USER_AGENT } from "../env.js";
import { loadExtensions } from "../duckdb.js";
import type { DataSource, FetchContext } from "./types.js";

/** Che et al., Scientific Data 2024. CC BY 4.0. */
export const GLOBFP_DOI = "10.1038/s41597-024-03917-6";
const WORLD_GRID_URL = "https://zenodo.org/records/15487006/files/world_grid.zip";
const WORLD_GRID_RECORD = "15487006";

/**
 * 3D-GloBFP world-grid cells that cover Shenzhen, with the Figshare PART Ⅸ file for each.
 * Grid 2265 covers the Futian test box (and everything north of 22.5° N in the urban core);
 * grid 2269 covers the strip south of 22.5° N, which the full urban-core box just reaches.
 * Files are from https://figshare.com/articles/dataset/_/28904453 (CC BY 4.0).
 */
export interface GlobfpTile {
  id: number;
  west: number;
  south: number;
  east: number;
  north: number;
  file: string;
  url: string;
  /** Published size in bytes; used to skip a complete cached download. */
  size: number;
}

export const GLOBFP_TILES: GlobfpTile[] = [
  {
    id: 2265,
    west: 113.75,
    south: 22.5,
    east: 115.0,
    north: 23.75,
    file: "2265_113.75_22.5_115.0_23.75_HKMacauCN_guangdongCN.zip",
    url: "https://ndownloader.figshare.com/files/57131525",
    size: 170628543,
  },
  {
    id: 2269,
    west: 112.5,
    south: 20.0,
    east: 115.0,
    north: 22.5,
    file: "2269_112.5_20.0_115.0_22.5_HKMacauCN_guangdongCN.zip",
    url: "https://ndownloader.figshare.com/files/57131471",
    size: 71481999,
  },
];

/** World-grid cells whose rectangles intersect the bounding box. */
export function globfpTiles(bbox: { west: number; south: number; east: number; north: number }): GlobfpTile[] {
  return GLOBFP_TILES.filter(
    (t) => t.west < bbox.east && t.east > bbox.west && t.south < bbox.north && t.north > bbox.south,
  );
}

function envelope(bbox: FetchContext["bbox"], marginDeg: number): { west: number; south: number; east: number; north: number } {
  return {
    west: bbox.west - marginDeg,
    south: bbox.south - marginDeg,
    east: bbox.east + marginDeg,
    north: bbox.north + marginDeg,
  };
}

async function download(url: string, dest: string, expected: number): Promise<void> {
  if (fs.existsSync(dest) && fs.statSync(dest).size === expected) return;
  // a previously extracted copy on this machine (the fetch cache, or a one-off /tmp download)
  const hints = [
    dest,
    path.join("/tmp/globfp", path.basename(dest)),
    path.join("/tmp/globfp", `${path.basename(dest, ".zip").split("_")[0]}.zip`),
  ];
  for (const hint of hints) {
    if (hint !== dest && fs.existsSync(hint) && fs.statSync(hint).size === expected) {
      fs.copyFileSync(hint, dest);
      return;
    }
  }
  const res = await fetch(url, { headers: { "User-Agent": USER_AGENT }, redirect: "follow" });
  if (!res.ok || !res.body) throw new Error(`globfp: ${res.status} ${res.statusText} for ${url}`);
  const tmp = `${dest}.part`;
  await pipeline(Readable.fromWeb(res.body as never), fs.createWriteStream(tmp));
  const got = fs.statSync(tmp).size;
  if (expected > 0 && got !== expected) {
    fs.rmSync(tmp, { force: true });
    throw new Error(`globfp: ${path.basename(dest)} is ${got} bytes, expected ${expected}`);
  }
  fs.renameSync(tmp, dest);
}

/**
 * 3D-GloBFP (Che et al., CC BY 4.0): individual building footprints with a predicted height,
 * used as a supplement where Overture is missing a building, and as a per-footprint height
 * where OSM has neither height nor floors. The 10 m CNBH raster sits below this source:
 * 3D-GloBFP is one height per outline (published RMSE 1.9–14.6 m by region) rather than a
 * 10 m cell that averages neighbours (CNBH RMSE 6.1 m). OSM mapped heights and floors still
 * win. Fetch downloads only the world-grid tiles that intersect the city box, clips them to
 * that box, and writes a compact `globfp.parquet`. The raw tiles are not committed.
 */
export const globfp: DataSource = {
  name: "globfp",
  files: ["globfp.parquet"],

  async fetch(ctx: FetchContext) {
    const dest = path.join(ctx.rawDir, "globfp.parquet");
    if (!ctx.force && fs.existsSync(dest)) return;
    const tiles = globfpTiles(ctx.bbox);
    if (tiles.length === 0) {
      throw new Error(
        `globfp: bounding box ${ctx.bbox.west},${ctx.bbox.south}–${ctx.bbox.east},${ctx.bbox.north} ` +
          `is outside the bundled Shenzhen tiles (grids 2265 and 2269). ` +
          `World-grid index: Zenodo ${WORLD_GRID_RECORD} (${WORLD_GRID_URL}).`,
      );
    }
    const cache = path.join(ctx.rawDir, "globfp");
    fs.mkdirSync(cache, { recursive: true });
    const zips: string[] = [];
    for (const tile of tiles) {
      const zip = path.join(cache, tile.file);
      ctx.log(`globfp: fetching grid ${tile.id} (${(tile.size / 1e6).toFixed(1)} MB)`);
      await download(tile.url, zip, tile.size);
      zips.push(zip);
    }

    const box = envelope(ctx.bbox, 0.004);
    const db = await DuckDBInstance.create(":memory:");
    const conn = await db.connect();
    await loadExtensions(conn, "spatial", "parquet");
    await conn.run("SET geometry_always_xy = true");
    const reads = zips
      .map((zip, i) => {
        const tile = tiles[i];
        const shp = tile.file.replace(/\.zip$/i, ".shp");
        const src = `/vsizip/${zip.replaceAll("'", "''")}/${shp}`;
        return `SELECT Height::DOUBLE AS height, geom FROM ST_Read('${src}')
                WHERE ST_Intersects(geom, ST_MakeEnvelope(${box.west}, ${box.south}, ${box.east}, ${box.north}))
                  AND Height IS NOT NULL AND Height > 0`;
      })
      .join("\nUNION ALL\n");
    // clip to the box, drop empties and tiny slivers / huge campus-sized artefacts
    const tmp = dest.replace(/\.parquet$/i, "") + ".clip.parquet";
    const qDest = dest.replaceAll("'", "''");
    const qTmp = tmp.replaceAll("'", "''");
    await conn.run(`
      COPY (
        SELECT height,
               ST_Intersection(geom, ST_MakeEnvelope(${box.west}, ${box.south}, ${box.east}, ${box.north})) AS geometry
        FROM (${reads})
        WHERE ST_Area(ST_Intersection(geom, ST_MakeEnvelope(${box.west}, ${box.south}, ${box.east}, ${box.north}))) > 0
      ) TO '${qTmp}' (FORMAT PARQUET)
    `);
    await conn.run(`
      COPY (
        SELECT height, ST_SetCRS(geometry, 'OGC:CRS84') AS geometry FROM '${qTmp}'
        WHERE NOT ST_IsEmpty(geometry)
          AND ST_Area_Spheroid(geometry) >= 15
          AND ST_Area_Spheroid(geometry) <= 20000
      ) TO '${qDest}' (FORMAT PARQUET)
    `);
    const stats = await conn.runAndReadAll(`
      SELECT count(*), round(sum(ST_Area_Spheroid(geometry)) / 1e6, 4),
             round(median(height), 1), round(avg(height), 1)
      FROM '${qDest}'
    `);
    conn.closeSync();
    fs.rmSync(tmp, { force: true });
    const [n, km2, med, avg] = stats.getRows()[0] as [number | bigint, number, number, number];
    ctx.log(
      `globfp: globfp.parquet, ${Number(n)} footprints, ${km2} km², median height ${med} m, mean ${avg} m ` +
        `(clipped to the city box; raw tiles stay in raw/globfp/ and are not committed)`,
    );
  },
};
