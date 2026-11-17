import { describe, expect, it } from "vitest";
import { coverCells } from "../cover";

/** "tx_ty" cells as the pipeline packs them: row-major, LSB first, base64. */
function pack(cells: number, inside: (i: number, j: number) => boolean): string {
  const bytes = new Uint8Array(Math.ceil((cells * cells) / 8));
  for (let j = 0; j < cells; j++)
    for (let i = 0; i < cells; i++) {
      const n = j * cells + i;
      if (inside(i, j)) bytes[n >> 3] |= 1 << (n & 7);
    }
  return btoa(String.fromCharCode(...bytes));
}

const grid = (present: string[]) => ({
  cols: 2,
  rows: 1,
  has: (tx: number, ty: number) => present.includes(`${tx}_${ty}`),
});

describe("coverCells", () => {
  it("lets nothing be lit where there is no tile, or in the padding", () => {
    const { data, width, height } = coverCells(grid(["0_0"]), null, 4, 1);
    expect(width).toBe(2 * 4 + 2);
    expect(height).toBe(4 + 2);
    expect(data[0]).toBe(1); // padding
    expect(data[1 * width + 1]).toBe(0); // inside tile 0_0
    expect(data[1 * width + 1 + 4]).toBe(1); // tile 1_0 is missing
  });

  it("takes an edge tile's cells from the cover mask and keeps other tiles whole", () => {
    // tile 1_0: only the left half of each row is inside the outline
    const cover = { cells: 4, tiles: { "1_0": pack(4, (i) => i < 2) } };
    const { data, width } = coverCells(grid(["0_0", "1_0"]), cover, 4, 0);
    const at = (x: number, y: number) => data[y * width + x];
    for (let y = 0; y < 4; y++) {
      for (let x = 0; x < 4; x++) expect(at(x, y)).toBe(0);
      expect(at(4, y)).toBe(0);
      expect(at(5, y)).toBe(0);
      expect(at(6, y)).toBe(1);
      expect(at(7, y)).toBe(1);
    }
  });

  it("ignores a cover mask drawn at another resolution, rather than misreading it", () => {
    const cover = { cells: 8, tiles: { "0_0": pack(8, () => false) } };
    const { data, width } = coverCells(grid(["0_0"]), cover, 4, 0);
    expect(data[0]).toBe(0);
    expect(data[3 * width + 3]).toBe(0);
  });

  it("never lights a cell of the mask whose tile is not there", () => {
    const cover = { cells: 4, tiles: { "1_0": pack(4, () => true) } };
    const { data, width } = coverCells(grid(["0_0"]), cover, 4, 0);
    expect(data[0 * width + 5]).toBe(1);
  });
});
