import { describe, expect, it } from "vitest";
import type { Polygons } from "../cities/types.js";
import { makeGeo, makeProjection } from "../projection.js";
import { buildFogBuiltUp, distanceTransform, FOG, fogAt, type Footprint, type FogGrid } from "./fog.js";

describe("distanceTransform", () => {
  it("measures Euclidean distance to the nearest set cell", () => {
    const w = 9;
    const h = 7;
    const mask = new Uint8Array(w * h);
    mask[3 * w + 2] = 1;
    mask[1 * w + 7] = 1;
    const d = distanceTransform(mask, w, h);
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) {
        const expected = Math.min(Math.hypot(x - 2, y - 3), Math.hypot(x - 7, y - 1));
        expect(d[y * w + x]).toBeCloseTo(expected, 5);
      }
  });
});

describe("fogAt", () => {
  it("interpolates between cell centres and is far outside the grid", () => {
    const fog: FogGrid = {
      p0: 0,
      q0: 0,
      cell: 10,
      cols: 3,
      rows: 3,
      d: Float32Array.from([0, 10, 20, 0, 10, 20, 0, 10, 20]),
    };
    expect(fogAt(fog, 5, 5)).toBeCloseTo(0);
    expect(fogAt(fog, 10, 12)).toBeCloseTo(5);
    expect(fogAt(fog, 15, 15)).toBeCloseTo(10);
    expect(fogAt(fog, -50, 5)).toBeGreaterThan(1e6);
  });
});

describe("buildFogBuiltUp", () => {
  // a town built up everywhere but a park in its middle, sized from this city's fog distances
  const proj = makeProjection(makeGeo(0, 0, 0, 2));
  const KM = 1 / 111.195;
  const holeKm = (FOG.start * 0.15 + FOG.end * 0.85) / 1000;
  const townKm = holeKm + 3.5;
  const square = (half: number): Polygons => [
    [
      [
        [-half, -half],
        [half, -half],
        [half, half],
        [-half, half],
        [-half, -half],
      ],
    ],
  ];
  const town = square(townKm * KM);
  const park = square(holeKm * KM);
  const footprints: Footprint[] = [];
  for (let x = -(townKm - 0.1); x <= townKm - 0.1; x += 0.05)
    for (let y = -(townKm - 0.1); y <= townKm - 0.1; y += 0.05)
      if (Math.abs(x) > holeKm || Math.abs(y) > holeKm)
        footprints.push({ lon: x * KM, lat: y * KM, area: 400 });
  const middle = (fog: FogGrid): number => fogAt(fog, ...proj.toGrid(0, 0));

  it("leaves a hole where a park is too big to have buildings near its middle", () => {
    expect(middle(buildFogBuiltUp(town, footprints, proj))).toBeGreaterThan(FOG.start);
  });

  it("counts the parks it is given as city", () => {
    const fog = buildFogBuiltUp(town, footprints, proj, park);
    expect(middle(fog)).toBe(0);
    // and nothing outside the town's limits becomes city on their account
    expect(fogAt(fog, ...proj.toGrid(7 * KM, 0))).toBeGreaterThan(0);
  });
});
