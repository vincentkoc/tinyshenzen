import path from "node:path";
import { readAreas } from "../geom/geojson.js";
import { SHENZHEN } from "../scene/styles.js";
import { cnbh } from "../sources/cnbh.js";
import { globfp } from "../sources/globfp.js";
import { overture } from "../sources/overture.js";
import { overtureAreas } from "../sources/overtureAreas.js";
import { HEIGHT_FIXES, LANDMARKS } from "./tinysz.landmarks.js";
import { CURATED } from "./tinysz.places.js";
import type { Areas, City } from "./types.js";

/** Civic Center / Futian, the ceremonial axis of the city. */
export const CENTER = { lon: 114.0683, lat: 22.5455 };

/**
 * A roughly two-kilometre box around the Civic Center: Ping An Finance Centre,
 * Lianhuashan Park and the Futian CBD. The default city, for a fast first render.
 */
export const TEST_BBOX: City["bbox"] = { west: 114.048, south: 22.527, east: 114.088, north: 22.564 };

/** Urban core: Futian, Nanshan, Luohu and the neighbouring districts. */
export const FULL_BBOX: City["bbox"] = { west: 113.8, south: 22.45, east: 114.3, north: 22.75 };

/**
 * Nanshan core: Shekou, Houhai, the Science Park and OCT. The east edge abuts
 * the Futian test box at 114.048° E, so the two renders sit side by side.
 */
export const NANSHAN_BBOX: City["bbox"] = { west: 113.9, south: 22.47, east: 114.048, north: 22.564 };

/**
 * Luohu core: Caiwuwei (KK100), Dongmen and Luohu Port. The west edge abuts
 * the Futian test box at 114.088° E.
 */
export const LUOHU_BBOX: City["bbox"] = { west: 114.088, south: 22.522, east: 114.14, north: 22.564 };

/** Contiguous strip covering the Nanshan core, Futian, and the Luohu core. */
export const CORE_BBOX: City["bbox"] = { west: 113.9, south: 22.47, east: 114.14, north: 22.564 };

/** Houhai / China Resources Headquarters, a natural start for the Nanshan box. */
export const HOUHAI = { lon: 113.94167, lat: 22.51694 };

/** KK100 in Caiwuwei, a natural start for the Luohu box. */
export const CAIWUWEI = { lon: 114.10194, lat: 22.54667 };

/** English names for districts the data often has only in Chinese. */
const DISTRICT_NAMES: Record<string, string> = {
  福田区: "Futian",
  福田: "Futian",
  南山区: "Nanshan",
  南山: "Nanshan",
  罗湖区: "Luohu",
  罗湖: "Luohu",
  盐田区: "Yantian",
  盐田: "Yantian",
  宝安区: "Bao'an",
  宝安: "Bao'an",
  龙岗区: "Longgang",
  龙岗: "Longgang",
  龙华区: "Longhua",
  龙华: "Longhua",
  坪山区: "Pingshan",
  坪山: "Pingshan",
  光明区: "Guangming",
  光明: "Guangming",
  大鹏新区: "Dapeng",
  大鹏: "Dapeng",
};

function areas(rawDir: string): Areas {
  const features = readAreas(path.join(rawDir, "areas.geojson"));
  return {
    districts: features
      .filter((f) => f.properties.kind === "locality")
      .map((f) => ({ name: f.properties.name, polygons: f.polygons })),
    hoods: features
      .filter((f) => f.properties.kind === "hood")
      .map((f) => ({
        name: f.properties.name,
        district: f.properties.district ?? "Shenzhen",
        polygons: f.polygons,
      })),
  };
}

function shenzhen(spec: {
  id: string;
  title: string;
  bbox: City["bbox"];
  fog: City["fog"];
  origin?: City["origin"];
  start?: City["start"];
  card?: City["card"];
}): City {
  return {
    id: spec.id,
    title: spec.title,
    origin: spec.origin ?? CENTER,
    // Futian's grid follows the compass: Shennan Road runs east–west, the Civic Center axis north–south
    betaDeg: 0,
    k: 2,
    bbox: spec.bbox,
    start: spec.start ?? { ...CENTER, scale: 0.85 },
    maxScale: 3,
    attribution: [
      "© OpenStreetMap contributors",
      "Overture Maps Foundation",
      "CNBH-10m (Wu et al., CC BY 4.0)",
      "3D-GloBFP (Che et al., CC BY 4.0)",
    ],
    sources: [
      overture,
      cnbh,
      globfp,
      overtureAreas({
        hoods: ["macrohood", "neighborhood"],
        rename: DISTRICT_NAMES,
        skip: /^(Shenzhen|深圳市|广东|广东省|Guangdong|China|中国)$/i,
        maxArea: 0.12,
      }),
    ],
    accent: "#1aa6c1",
    // Ping An Finance Centre and the Civic Center together, unless a district box overrides
    card: spec.card ?? { lon: 114.059, lat: 22.542, shrink: 4 },

    limits: {
      kind: "built-up",
      parks: [
        "Lianhuashan Park",
        "莲花山公园",
        "Shenzhen Bay Park",
        "深圳湾公园",
        "Litchi Park",
        "荔枝公园",
        "Window of the World",
        "世界之窗",
        "Fairy Lake Botanical Garden",
        "仙湖植物园",
        "Wutong Mountain",
        "梧桐山",
      ],
    },
    fog: spec.fog,
    areas,

    // Chinese residential storeys are a little under 3.3 m; where a model's height reads low, the floors decide
    heights: { storey: 3.1, raster: true, floorsFirst: true },
    style: SHENZHEN,
    landmarks: LANDMARKS,
    heightFixes: HEIGHT_FIXES,
    statues: [],
    roofs: { waterTanks: false },
    look: { pixelsg: true, roofFurniture: true, palms: true },
    trees: { street: { spacing: 12, chance: 0.36 } },
    flora: "tropical",
    ground: {},

    curated: CURATED,
    flights: [],
    sights: {
      placeWords: ["Shenzhen", "深圳", "Shumchun", "Shum Chun"],
      aliases: {
        "ping an finance centre": ["PAFC", "Ping An IFC", "平安金融中心", "平安国际金融中心"],
        kk100: ["Kingkey 100", "Kingkey Finance Tower", "京基100"],
        "window of the world": ["世界之窗"],
      },
      wikipedia: {
        "ping an finance centre": "Ping An Finance Centre",
        "ping an finance center": "Ping An Finance Centre",
        kk100: "KK100",
        "kingkey 100": "KK100",
        "window of the world": "Window of the World",
        "shenzhen civic center": "Shenzhen Civic Center",
        "shenzhen stock exchange": "Shenzhen Stock Exchange",
        "china resources headquarters": "China Resources Headquarters",
        "shun hing square": "Shun Hing Square",
      },
    },
    search: {
      districtOnly: false,
      bridge: /bridge|大桥|桥/i,
      stationSuffix: " station",
      districtRank: 80,
      namedBuildings: {
        minHeight: 80,
        classes: ["civic", "government", "cathedral", "church", "university", "museum"],
      },
    },
  };
}

/** Shenzhen, around the Civic Center: the default, a small box that renders quickly. */
export const tinysz: City = shenzhen({
  id: "tinysz",
  title: "Shenzhen",
  bbox: TEST_BBOX,
  fog: { start: 600, end: 1300 },
});

/** The same city over the full urban-core bounding box. */
export const tinyszFull: City = shenzhen({
  id: "tinysz-full",
  title: "Shenzhen (urban core)",
  bbox: FULL_BBOX,
  fog: { start: 1400, end: 2800 },
});

/** Nanshan core, west of and contiguous with the Futian test box. */
export const tinyszNanshan: City = shenzhen({
  id: "tinysz-nanshan",
  title: "Shenzhen (Nanshan)",
  bbox: NANSHAN_BBOX,
  fog: { start: 800, end: 1700 },
  origin: HOUHAI,
  start: { ...HOUHAI, scale: 0.85 },
  card: { lon: HOUHAI.lon, lat: HOUHAI.lat, shrink: 4 },
});

/** Luohu core, east of and contiguous with the Futian test box. */
export const tinyszLuohu: City = shenzhen({
  id: "tinysz-luohu",
  title: "Shenzhen (Luohu)",
  bbox: LUOHU_BBOX,
  fog: { start: 600, end: 1300 },
  origin: CAIWUWEI,
  start: { ...CAIWUWEI, scale: 0.85 },
  card: { lon: CAIWUWEI.lon, lat: CAIWUWEI.lat, shrink: 4 },
});

/** Nanshan core, Futian and Luohu core as one contiguous strip. */
export const tinyszCore: City = shenzhen({
  id: "tinysz-core",
  title: "Shenzhen (Nanshan–Futian–Luohu)",
  bbox: CORE_BBOX,
  fog: { start: 1200, end: 2400 },
});
