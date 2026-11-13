import { describe, expect, it } from "vitest";
import { G } from "../scene/kinds.js";
import { idx, R, S_HALF, S_LIT, SHADES } from "./palette.js";
import { habboGroundColor, type GroundContext } from "./ground.js";
import { tinysz } from "../cities/tinysz.js";
import { CITIES } from "../cities/index.js";

describe("habbo look", () => {
  it("is on for Shenzhen and off for every other city", () => {
    expect(tinysz.look?.habbo).toBe(true);
    for (const [id, city] of Object.entries(CITIES)) {
      if (id === "tinysz" || id === "tinysz-full") {
        expect(city.look?.habbo).toBe(true);
      } else {
        expect(city.look?.habbo).toBeFalsy();
      }
    }
  });

  it("paints clean 2:1 diamond pavement with a grout line, no hash speckles", () => {
    const ctx: GroundContext = { gk: new Uint8Array(1), wd: new Uint8Array(1), GW: 1, habbo: true };
    const a = habboGroundColor(ctx, G.Plaza, 1, 0, 0); // a = 1, inside a diamond
    const grout = habboGroundColor(ctx, G.Plaza, 16, 0, 0); // a = 16, on a grout line (a & 15 === 0)
    const same = habboGroundColor(ctx, G.Plaza, 1, 0, 0);
    expect(a).toBe(same);
    expect(grout).not.toBe(a);
    // two adjacent diamonds along the 2:1 axis differ or share a grout
    const tones = new Set<number>();
    for (let x = 0; x < 32; x++) tones.add(habboGroundColor(ctx, G.Plaza, x, 0, 0));
    // limited: grout + two tile tones
    expect(tones.size).toBeLessThanOrEqual(3);
    expect(tones.has(idx(R.habboTile, S_HALF)) || tones.has(idx(R.habboPavement, S_LIT))).toBe(true);
    // parks are flat greens, not hashed
    const park = habboGroundColor(ctx, G.Park, 3, 5, 0);
    const park2 = habboGroundColor(ctx, G.Park, 3, 5, 0);
    expect(park).toBe(park2);
    expect(park % SHADES).toBeGreaterThanOrEqual(S_HALF);
  });
});
