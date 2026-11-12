import { describe, expect, it } from "vitest";
import { isPalmSeed, TREE_PARK, TREE_STREET, treeSprite } from "./sprites.js";

describe("palm sprites", () => {
  it("stand on a trunk and have a crown of fronds", () => {
    // find a seed that is a palm on the street
    let seed = 0;
    while (!isPalmSeed(seed, TREE_STREET) && seed < 40) seed++;
    expect(isPalmSeed(seed, TREE_STREET)).toBe(true);
    const leafy = treeSprite(TREE_STREET, seed, false);
    const palm = treeSprite(TREE_STREET, seed, true);
    expect(palm.h).toBeGreaterThan(leafy.h - 2);
    const filled = [...palm.px].filter((c) => c !== 0);
    expect(filled.length).toBeGreaterThan(8);
    expect(filled.some((c) => c === 4)).toBe(true); // trunk
    expect(filled.some((c) => c === 1 || c === 2)).toBe(true); // fronds
    // the foot of the trunk is on the bottom row, at the anchor
    const foot = palm.px[(palm.h - 1) * palm.w + palm.anchorX];
    expect(foot).toBe(4);
  });

  it("leave leafy trees unchanged when palms are off", () => {
    const a = treeSprite(TREE_PARK, 17, false);
    const b = treeSprite(TREE_PARK, 17, false);
    expect(a).toBe(b);
    expect(treeSprite(TREE_PARK, 17, true) === a || !isPalmSeed(17, TREE_PARK)).toBe(true);
  });
});
