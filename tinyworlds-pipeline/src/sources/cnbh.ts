import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import { fromFile } from "geotiff";
import { USER_AGENT } from "../env.js";
import { fromUtm, toUtm, utmZone } from "../geom/utm.js";
import type { HeightIndex } from "./openBuildings.js";
import type { DataSource, FetchContext } from "./types.js";

/** Wu et al., Scientific Data 2023. CC BY 4.0. */
export const CNBH_DOI = "10.5281/zenodo.7923866";
const ZENODO_RECORD = "7923866";
const RES = 10;
/** Half-metres in a uint16; 65535 is no data. Plenty of room for a 600 m tower. */
const U16_NODATA = 65535;
const MARGIN = 2000;

export interface CnbhTile {
  x: number;
  y: number;
}

/**
 * CNBH-10m tiles are 2° cells centred on odd integers, so the edges fall on even degrees:
 * X113 covers roughly 112–114° E, Y23 covers 22–24° N. The Futian test box sits just east of
 * 114° E, so it is X115Y23; the full urban core needs X113Y23 as well.
 */
export function cnbhTiles(bbox: { west: number; south: number; east: number; north: number }): CnbhTile[] {
  const odds = (min: number, max: number): number[] => {
    const first = Math.floor(min / 2) * 2 + 1;
    const last = Math.floor(max / 2) * 2 + 1;
    const out: number[] = [];
    for (let v = first; v <= last; v += 2) out.push(v);
    return out;
  };
  const tiles: CnbhTile[] = [];
  for (const y of odds(bbox.south, bbox.north)) {
    for (const x of odds(bbox.west, bbox.east)) tiles.push({ x, y });
  }
  return tiles;
}

export function cnbhFileName(tile: CnbhTile): string {
  return `CNBH10m_X${tile.x}Y${tile.y}.tif`;
}

function cnbhUrl(tile: CnbhTile): string {
  return `https://zenodo.org/api/records/${ZENODO_RECORD}/files/${cnbhFileName(tile)}/content`;
}

function epsgToUtm(epsg: number | undefined): { zone: number; south: boolean } | null {
  if (!epsg) return null;
  if (epsg >= 32601 && epsg <= 32660) return { zone: epsg - 32600, south: false };
  if (epsg >= 32701 && epsg <= 32760) return { zone: epsg - 32700, south: true };
  return null;
}

/**
 * CNBH-10m (CC BY 4.0): a 10 m raster of building heights for all of China in 2020, published by
 * Wu et al. Used where a footprint has no mapped height or floors — in Shenzhen that is most of
 * the urban-village fabric, whose Overture outlines come from the East Asian Buildings dataset
 * with no height at all. Writes the same `heights/` layout as the open-buildings source, with
 * `curve: "identity"` so the Brazil correction is not applied, and uint16 so a 600 m tower is
 * not clipped at 127 m.
 */
export const cnbh: DataSource = {
  name: "cnbh",
  files: ["heights/index.json"],

  async fetch(ctx: FetchContext) {
    const dir = path.join(ctx.rawDir, "heights");
    const indexFile = path.join(dir, "index.json");
    if (!ctx.force && fs.existsSync(indexFile)) return;
    fs.mkdirSync(dir, { recursive: true });

    const tiles = cnbhTiles(ctx.bbox);
    if (tiles.length === 0) throw new Error("cnbh: bounding box picks no tiles");
    const cache = path.join(ctx.rawDir, "cnbh");
    fs.mkdirSync(cache, { recursive: true });

    const sources: {
      path: string;
      zone: number;
      south: boolean;
      originX: number;
      originY: number;
      resX: number;
      resY: number;
      width: number;
      height: number;
    }[] = [];

    for (const tile of tiles) {
      const dest = path.join(cache, cnbhFileName(tile));
      if (!fs.existsSync(dest) || ctx.force) {
        const started = Date.now();
        const url = cnbhUrl(tile);
        const res = await fetch(url, {
          headers: { "User-Agent": USER_AGENT, Accept: "*/*" },
          redirect: "follow",
        });
        if (res.status === 404) {
          ctx.log(`cnbh: ${cnbhFileName(tile)} is not in the dataset (skipped)`);
          continue;
        }
        if (!res.ok) throw new Error(`cnbh: ${res.status} ${res.statusText} for ${cnbhFileName(tile)}`);
        const buf = Buffer.from(await res.arrayBuffer());
        fs.writeFileSync(dest + ".part", buf);
        fs.renameSync(dest + ".part", dest);
        ctx.log(
          `cnbh: downloaded ${cnbhFileName(tile)} ${(buf.length / 1e6).toFixed(1)} MB in ${((Date.now() - started) / 1000).toFixed(0)} s`,
        );
      }
      const tiff = await fromFile(dest);
      const image = await tiff.getImage();
      const keys = image.getGeoKeys() as { ProjectedCSTypeGeoKey?: number; GTCitationGeoKey?: string };
      const utm = epsgToUtm(keys.ProjectedCSTypeGeoKey);
      if (!utm)
        throw new Error(
          `cnbh: ${cnbhFileName(tile)} is not a UTM GeoTIFF (${keys.GTCitationGeoKey ?? "unknown"})`,
        );
      const origin = image.getOrigin();
      const resolution = image.getResolution();
      sources.push({
        path: dest,
        zone: utm.zone,
        south: utm.south,
        originX: origin[0],
        originY: origin[1],
        resX: resolution[0],
        resY: resolution[1],
        width: image.getWidth(),
        height: image.getHeight(),
      });
    }
    if (sources.length === 0) throw new Error("cnbh: no tiles cover this city");

    const midLon = (ctx.bbox.west + ctx.bbox.east) / 2;
    const midLat = (ctx.bbox.south + ctx.bbox.north) / 2;
    const zone = utmZone(midLon);
    const isSouth = midLat < 0;
    const corners = (
      [
        [ctx.bbox.west, ctx.bbox.south],
        [ctx.bbox.east, ctx.bbox.south],
        [ctx.bbox.west, ctx.bbox.north],
        [ctx.bbox.east, ctx.bbox.north],
      ] as [number, number][]
    ).map(([lon, lat]) => toUtm(lon, lat, zone, isSouth));
    const minX = Math.min(...corners.map((c) => c[0])) - MARGIN;
    const maxX = Math.max(...corners.map((c) => c[0])) + MARGIN;
    const minY = Math.min(...corners.map((c) => c[1])) - MARGIN;
    const maxY = Math.max(...corners.map((c) => c[1])) + MARGIN;
    const width = Math.max(1, Math.ceil((maxX - minX) / RES));
    const height = Math.max(1, Math.ceil((maxY - minY) / RES));
    const x0 = minX;
    const y1 = maxY;
    const grid = new Uint16Array(width * height);
    grid.fill(U16_NODATA);

    let known = 0;
    for (const src of sources) {
      const tiff = await fromFile(src.path);
      const image = await tiff.getImage();
      // window in the source raster that covers this city's clip, in the source's UTM
      const srcCorners = (
        [
          [ctx.bbox.west, ctx.bbox.south],
          [ctx.bbox.east, ctx.bbox.south],
          [ctx.bbox.west, ctx.bbox.north],
          [ctx.bbox.east, ctx.bbox.north],
        ] as [number, number][]
      ).map(([lon, lat]) => toUtm(lon, lat, src.zone, src.south));
      const sx0 = Math.min(...srcCorners.map((c) => c[0])) - MARGIN;
      const sx1 = Math.max(...srcCorners.map((c) => c[0])) + MARGIN;
      const sy0 = Math.min(...srcCorners.map((c) => c[1])) - MARGIN;
      const sy1 = Math.max(...srcCorners.map((c) => c[1])) + MARGIN;
      const col0 = Math.max(0, Math.floor((sx0 - src.originX) / src.resX));
      const col1 = Math.min(src.width, Math.ceil((sx1 - src.originX) / src.resX));
      // resY is negative (north-up)
      const row0 = Math.max(0, Math.floor((sy1 - src.originY) / src.resY));
      const row1 = Math.min(src.height, Math.ceil((sy0 - src.originY) / src.resY));
      if (col1 <= col0 || row1 <= row0) {
        ctx.log(`cnbh: ${path.basename(src.path)} does not overlap the clip`);
        continue;
      }
      const rasters = (await image.readRasters({
        window: [col0, row0, col1, row1],
      })) as unknown as [Float32Array];
      const win = rasters[0];
      const ww = col1 - col0;
      const wh = row1 - row0;

      const atSrc = (sx: number, sy: number): number => {
        const c = Math.floor((sx - src.originX) / src.resX) - col0;
        const r = Math.floor((sy - src.originY) / src.resY) - row0;
        if (c < 0 || r < 0 || c >= ww || r >= wh) return -1;
        const v = win[r * ww + c];
        if (!Number.isFinite(v) || v <= 0) return -1;
        return v;
      };

      for (let row = 0; row < height; row++) {
        for (let col = 0; col < width; col++) {
          const i = row * width + col;
          if (grid[i] !== U16_NODATA) continue;
          const x = x0 + (col + 0.5) * RES;
          const y = y1 - (row + 0.5) * RES;
          let metres: number;
          if (src.zone === zone && src.south === isSouth) metres = atSrc(x, y);
          else {
            const [lon, lat] = fromUtm(x, y, zone, isSouth);
            const [sx, sy] = toUtm(lon, lat, src.zone, src.south);
            metres = atSrc(sx, sy);
          }
          if (metres < 0) continue;
          const half = Math.max(1, Math.min(U16_NODATA - 1, Math.round(metres * 2)));
          grid[i] = half;
          known++;
        }
      }
    }

    const file = "cnbh.u16.gz";
    const raw = Buffer.from(grid.buffer, grid.byteOffset, grid.byteLength);
    fs.writeFileSync(path.join(dir, file), zlib.gzipSync(raw, { level: 6 }));
    const index: HeightIndex = {
      zone,
      south: isSouth,
      res: RES,
      curve: "identity",
      tiles: [{ file, x0, y1, width, height, bytes: 2, unit: "half-metres" }],
    };
    fs.writeFileSync(indexFile, JSON.stringify(index));
    ctx.log(
      `cnbh: clip ${width}×${height} at ${RES} m, ${Math.round((known / grid.length) * 100)}% with a height, zone ${zone}`,
    );
    if (known === 0)
      throw new Error("cnbh: the clip has no height values; the tiles may not cover this city");
  },
};
