#!/usr/bin/env node
/**
 * Rasterize the tinysz scene's ground (and roads) the same way ChunkRenderer
 * does, and write a 1-channel mask: 255 where the kind is water or lake, 0
 * on land. Unpainted cells default to water, matching paintGround, so a later
 * step must AND this with "the procedural pixel actually shows water" — fog
 * covers default-water outside the city.
 *
 * Usage (pipeline already built, scene on disk):
 *
 *   node --max-old-space-size=4096 lora/render_water_mask.mjs \
 *     --out /workspace/tinysz-futian/water_mask.png
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..");
const pipeline = path.join(repo, "tinyworlds-pipeline");

process.argv.splice(2, 0, "--city", "tinysz");
process.env.TINY_CITY = "tinysz";

const { DIRS } = await import(path.join(pipeline, "dist/config.js"));
const { binRect } = await import(path.join(pipeline, "dist/render/bin.js"));
const { ChunkRenderer } = await import(path.join(pipeline, "dist/render/chunk.js"));
const { loadScene } = await import(path.join(pipeline, "dist/render/scene.js"));
const { isWater } = await import(path.join(pipeline, "dist/scene/kinds.js"));

const { values } = parseArgs({
  options: {
    out: { type: "string", default: "/workspace/tinysz-futian/water_mask.png" },
    chunk: { type: "string", default: "2048" },
    preview: { type: "string", default: "" },
    city: { type: "string" },
  },
  strict: false,
});

const out = path.resolve(values.out);
const chunk = Math.max(256, Number(values.chunk));
fs.mkdirSync(path.dirname(out), { recursive: true });

const { scene } = loadScene(DIRS.scene);
const { minX, minY, maxX, maxY } = scene.bounds;
const width = maxX - minX;
const height = maxY - minY;
const renderer = new ChunkRenderer(scene);
const mask = Buffer.alloc(width * height); // 0 = land, will set 255 for water

const cols = Math.ceil(width / chunk);
const rows = Math.ceil(height / chunk);
const t0 = Date.now();
console.log(`render_water_mask: ${width}×${height} in ${cols}×${rows} chunks of ${chunk}`);

let waterPx = 0;
for (let row = 0; row < rows; row++) {
  for (let col = 0; col < cols; col++) {
    const x0 = minX + col * chunk;
    const y0 = minY + row * chunk;
    const w = Math.min(chunk, maxX - x0);
    const h = Math.min(chunk, maxY - y0);
    const job = binRect(scene, { x0, y0, w, h });
    renderer.allocate(job);
    renderer.paintGround(job);
    renderer.paintLines(job);
    const gk = renderer.gk;
    const gx0 = renderer.gx0;
    const gy0 = renderer.gy0;
    const GW = renderer.GW;
    const ox = x0 - minX;
    const oy = y0 - minY;
    const sx = x0 - gx0;
    const sy = y0 - gy0;
    for (let y = 0; y < h; y++) {
      const src = (sy + y) * GW + sx;
      const dst = (oy + y) * width + ox;
      for (let x = 0; x < w; x++) {
        if (isWater(gk[src + x])) {
          mask[dst + x] = 255;
          waterPx++;
        }
      }
    }
  }
}

const raw = out.replace(/\.png$/i, ".raw");
fs.writeFileSync(raw, mask);
fs.writeFileSync(
  out.replace(/\.png$/i, ".json"),
  JSON.stringify(
    {
      width,
      height,
      origin: { x: minX, y: minY },
      waterPixels: waterPx,
      raw: path.basename(raw),
      encoding: "uint8",
      water: 255,
      land: 0,
    },
    null,
    2,
  ) + "\n",
);
console.log(
  `render_water_mask: ${waterPx} water pixels (${((100 * waterPx) / (width * height)).toFixed(1)}%) in ${((Date.now() - t0) / 1000).toFixed(1)}s`,
);
console.log(`  raw ${raw}`);

const { createRequire } = await import("node:module");
const require = createRequire(path.join(pipeline, "package.json"));
const sharp = require("sharp");
await sharp(mask, { raw: { width, height, channels: 1 }, limitInputPixels: false })
  .png({ compressionLevel: 4 })
  .toFile(out);
console.log(`  png ${out}`);

const previewPath = values.preview || out.replace(/\.png$/i, "-preview.png");
const previewW = 4096;
const previewH = Math.max(1, Math.round((height * previewW) / width));
await sharp(mask, { raw: { width, height, channels: 1 }, limitInputPixels: false })
  .resize(previewW, previewH, { kernel: "nearest" })
  .png()
  .toFile(previewPath);
console.log(`  preview ${previewPath} ${previewW}×${previewH}`);
