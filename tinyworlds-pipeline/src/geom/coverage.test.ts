import { describe, expect, it } from "vitest";
import { coverageTest, insidePolygons } from "./coverage.js";

const square = [
  [
    [0, 0],
    [10, 0],
    [10, 10],
    [0, 10],
    [0, 0],
  ],
];
const hole = [
  [4, 4],
  [6, 4],
  [6, 6],
  [4, 6],
  [4, 4],
];

describe("insidePolygons", () => {
  it("tests outer rings and holes", () => {
    expect(insidePolygons([square], 1, 1)).toBe(true);
    expect(insidePolygons([square], 11, 1)).toBe(false);
    expect(insidePolygons([[...square, hole]], 5, 5)).toBe(false);
    expect(insidePolygons([[...square, hole]], 2, 5)).toBe(true);
  });
});

describe("coverageTest", () => {
  it("covers everything without an outline", () => {
    expect(coverageTest(undefined)(500, -80)).toBe(true);
    expect(coverageTest([])(500, -80)).toBe(true);
  });
  it("follows the outline when there is one", () => {
    const covered = coverageTest([square]);
    expect(covered(3, 3)).toBe(true);
    expect(covered(-1, 3)).toBe(false);
  });
});
