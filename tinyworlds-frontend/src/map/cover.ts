import type { CoverMask } from "@/worlds/types";
import { decodeBitset, bitAt } from "./bitset";
import type { CoverageGrid } from "./engine";

/**
 * The night layer's coverage mask over the finest level's grid, `sub` px per tile side and
 * padded by `pad` px: 1 where nothing may be lit (no tile, or outside the coverage outline),
 * 0 inside. Tiles in the cover mask take its cells (`sub` must be its `cells`); the rest are whole.
 */
export function coverCells(
  grid: Pick<CoverageGrid, "cols" | "rows" | "has">,
  cover: CoverMask | null,
  sub: number,
  pad: number,
): { data: Float32Array; width: number; height: number } {
  const width = grid.cols * sub + pad * 2;
  const height = grid.rows * sub + pad * 2;
  const data = new Float32Array(width * height).fill(1);
  const useCover = cover !== null && cover.cells === sub;
  for (let ty = 0; ty < grid.rows; ty++) {
    for (let tx = 0; tx < grid.cols; tx++) {
      if (!grid.has(tx, ty)) continue;
      const packed = useCover ? cover.tiles[`${tx}_${ty}`] : undefined;
      const bits = packed ? decodeBitset(packed) : null;
      for (let j = 0; j < sub; j++) {
        const row = (pad + ty * sub + j) * width + pad + tx * sub;
        if (!bits) {
          data.fill(0, row, row + sub);
          continue;
        }
        for (let i = 0; i < sub; i++) if (bitAt(bits, j * sub + i)) data[row + i] = 0;
      }
    }
  }
  return { data, width, height };
}
