import { R } from "../render/palette.js";
import { Pattern } from "../scene/kinds.js";
import type { LandmarkStyle } from "./types.js";

const glass = (wall: number, pyramid?: number): LandmarkStyle => ({
  wall,
  roof: wall,
  pattern: Pattern.Curtain,
  ...(pyramid != null ? { pyramid } : {}),
});

/**
 * Named towers and civic buildings, matched on the lower-case name in the map data.
 * Several spellings and the Chinese names are listed, because Overture may use any of them.
 */
export const LANDMARKS: Record<string, LandmarkStyle> = {
  // 599 m, a tapered silver-blue glass blade
  "ping an finance centre": glass(R.glassSilver, 0.16),
  "ping an finance center": glass(R.glassSilver, 0.16),
  "ping an international finance centre": glass(R.glassSilver, 0.16),
  "ping an international finance center": glass(R.glassSilver, 0.16),
  平安金融中心: glass(R.glassSilver, 0.16),
  平安国际金融中心: glass(R.glassSilver, 0.16),
  // 442 m, a very slender tapering blue tower
  kk100: glass(R.glassBlue, 0.18),
  "kingkey 100": glass(R.glassBlue, 0.18),
  "kingkey finance tower": glass(R.glassBlue, 0.18),
  京基100: glass(R.glassBlue, 0.18),
  // 384 m, Diwang, the first supertall in Luohu
  "shun hing square": glass(R.glassBlue, 0.08),
  "diwang building": glass(R.glassBlue, 0.08),
  信兴广场: glass(R.glassBlue, 0.08),
  地王大厦: glass(R.glassBlue, 0.08),
  // 392 m, the "Spring Bamboo"
  "china resources headquarters": glass(R.glassGreen, 0.35),
  "spring bamboo": glass(R.glassGreen, 0.35),
  春笋: glass(R.glassGreen, 0.35),
  中国华润大厦: glass(R.glassGreen, 0.35),
  "dabaihui plaza": glass(R.glassSilver, 0.1),
  "shenzhen center": glass(R.glassSilver, 0.1),
  "shum yip upperhills tower 1": glass(R.glassBlue, 0.1),
  "shenzhen civic center": { wall: R.whiteBrick, roof: R.roofWhite, pattern: Pattern.None },
  深圳市民中心: { wall: R.whiteBrick, roof: R.roofWhite, pattern: Pattern.None },
  "shenzhen stock exchange": { wall: R.glassDark, roof: R.glassDark, pattern: Pattern.Curtain },
  深圳证券交易所: { wall: R.glassDark, roof: R.glassDark, pattern: Pattern.Curtain },
  "china merchants bank tower global hq": glass(R.glassTeal, 0.1),
};

/** Known architectural heights, in metres, used if the mapped figure is missing or short. */
export const HEIGHT_FIXES: Record<string, number> = {
  "ping an finance centre": 599.1,
  "ping an finance center": 599.1,
  "ping an international finance centre": 599.1,
  平安金融中心: 599.1,
  平安国际金融中心: 599.1,
  kk100: 441.8,
  "kingkey 100": 441.8,
  京基100: 441.8,
  "shun hing square": 384,
  地王大厦: 384,
  "china resources headquarters": 392.5,
  春笋: 392.5,
  "dabaihui plaza": 375.6,
  "shenzhen center": 375.6,
  "shum yip upperhills tower 1": 388.1,
};
