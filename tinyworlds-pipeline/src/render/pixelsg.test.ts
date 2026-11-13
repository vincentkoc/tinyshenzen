import { describe, expect, it } from "vitest";
import { CITIES } from "../cities/index.js";
import { tinysz, tinyszCore, tinyszFull, tinyszLuohu, tinyszNanshan } from "../cities/tinysz.js";
import { SHENZHEN, SHENZHEN_HABBO } from "../scene/styles.js";
import { G } from "../scene/kinds.js";
import { idx, R, S_LIT } from "./palette.js";
import { PIXELSG_PROMPT } from "../restyle.js";
import { pixelsgGroundColor, type GroundContext } from "./ground.js";

describe("pixelsg look", () => {
  it("is the default Shenzhen look, and is off for every other city", () => {
    expect(tinysz.look?.pixelsg).toBe(true);
    expect(tinyszFull.look?.pixelsg).toBe(true);
    expect(tinyszNanshan.look?.pixelsg).toBe(true);
    expect(tinyszLuohu.look?.pixelsg).toBe(true);
    expect(tinyszCore.look?.pixelsg).toBe(true);
    expect(tinysz.look?.habbo).toBeFalsy();
    expect(tinysz.style).toBe(SHENZHEN);
    expect(tinysz.style).not.toBe(SHENZHEN_HABBO);
    for (const [id, city] of Object.entries(CITIES)) {
      if (id.startsWith("tinysz")) continue;
      expect(city.look?.pixelsg, id).toBeFalsy();
    }
  });

  it("paints teal-green water and lush lawn, not diamond grout", () => {
    const wd = new Uint8Array(8).fill(40);
    wd[0] = 4; // near shore
    const ctx: GroundContext = { gk: new Uint8Array(8), wd, GW: 1, pixelsg: true };
    const water = pixelsgGroundColor(ctx, G.Water, 10, 10, 1);
    const shallow = pixelsgGroundColor(ctx, G.Water, 10, 10, 0);
    expect(water).toBe(idx(R.sgWaterDeep, S_LIT));
    expect(shallow).toBe(idx(R.sgWater, S_LIT));
    const park = pixelsgGroundColor(ctx, G.Park, 3, 5, 0);
    const park2 = pixelsgGroundColor(ctx, G.Park, 3, 5, 0);
    expect(park).toBe(park2);
    expect(park).toBe(idx(R.sgLawn, S_LIT));
  });

  it("describes a Pixel-Singapore-inspired restyle without asking for a third-party image", () => {
    expect(PIXELSG_PROMPT).toMatch(/detailed isometric pixel art/i);
    expect(PIXELSG_PROMPT).toMatch(/teal/i);
    expect(PIXELSG_PROMPT).toMatch(/terracotta/i);
    expect(PIXELSG_PROMPT.toLowerCase()).not.toMatch(/yadong|upload this image|style reference image/);
    expect(PIXELSG_PROMPT.toLowerCase()).toMatch(/do not invent water/);
    expect(PIXELSG_PROMPT.toLowerCase()).toMatch(/do not add rooftop gardens/);
  });
});
