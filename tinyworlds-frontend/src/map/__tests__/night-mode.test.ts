import { describe, expect, it } from "vitest";
import {
  clockNight,
  easeNight,
  NIGHT_FADE_MS,
  nightNeeded,
  nightTarget,
  stepNight,
} from "../night-mode";

const at = (h: number, m = 0) => new Date(2026, 10, 17, h, m, 0);

describe("nightTarget", () => {
  it("follows plain on and off whatever the time", () => {
    expect(nightTarget("off", at(23))).toBe(0);
    expect(nightTarget("off", at(12))).toBe(0);
    expect(nightTarget("on", at(12))).toBe(1);
    expect(nightTarget("on", at(23))).toBe(1);
  });

  it("follows the visitor's clock on auto", () => {
    expect(nightTarget("auto", at(12))).toBe(0);
    expect(nightTarget("auto", at(22))).toBe(1);
    expect(nightTarget("auto", at(2))).toBe(1);
  });
});

describe("clockNight", () => {
  it("is day at midday and night before dawn", () => {
    expect(clockNight(at(13))).toBe(0);
    expect(clockNight(at(0))).toBe(1);
    expect(clockNight(at(4, 59))).toBe(1);
  });

  it("ramps through dusk and dawn without jumping", () => {
    const dusk = [18, 18.25, 18.5, 18.75, 19, 19.25].map((h) =>
      clockNight(at(Math.floor(h), (h % 1) * 60)),
    );
    for (let i = 1; i < dusk.length; i++) expect(dusk[i]).toBeGreaterThanOrEqual(dusk[i - 1]);
    expect(dusk[0]).toBe(0);
    expect(dusk[dusk.length - 1]).toBe(1);
    const mid = clockNight(at(5, 52));
    expect(mid).toBeGreaterThan(0);
    expect(mid).toBeLessThan(1);
    expect(clockNight(at(6, 30))).toBe(0);
  });
});

describe("stepNight", () => {
  it("takes the whole fade time to go from day to night", () => {
    let mix = 0;
    let t = 0;
    while (mix < 1 && t < 10_000) {
      mix = stepNight(mix, 1, 16);
      t += 16;
    }
    expect(mix).toBe(1);
    expect(t).toBeGreaterThanOrEqual(NIGHT_FADE_MS);
    expect(t).toBeLessThan(NIGHT_FADE_MS + 32);
  });

  it("goes back down, lands exactly, and jumps with reduced motion", () => {
    expect(stepNight(1, 0, NIGHT_FADE_MS / 2)).toBeCloseTo(0.5);
    expect(stepNight(0.01, 0, 100)).toBe(0);
    expect(stepNight(0, 1, 16, true)).toBe(1);
    expect(stepNight(0.4, 0.4, 16)).toBe(0.4);
  });

  it("eases the ends", () => {
    expect(easeNight(0)).toBe(0);
    expect(easeNight(1)).toBe(1);
    expect(easeNight(0.1)).toBeLessThan(0.1);
  });
});

describe("nightNeeded", () => {
  it("loads nothing for a visitor who never turns it on", () => {
    expect(nightNeeded("off", 0, at(23))).toBe(false);
    expect(nightNeeded("auto", 0, at(12))).toBe(false);
  });

  it("is needed while on, on auto at night, and while fading out", () => {
    expect(nightNeeded("on", 0, at(12))).toBe(true);
    expect(nightNeeded("auto", 0, at(22))).toBe(true);
    expect(nightNeeded("off", 0.3, at(12))).toBe(true);
  });
});
