#!/usr/bin/env node
/**
 * Render a Shenzhen city box as 2048×2048 chunks, plus a downscaled preview.
 * A single full-size PNG would exceed comfortable sharp/RAM limits on a 16 GB
 * box; MultiDiffusion and the OpenAI restyle read the chunks.
 *
 * Usage (from the repo root, pipeline already built, scene already written):
 *
 *   node --max-old-space-size=4096 lora/render_full.mjs \
 *     --city tinysz-nanshan --out /workspace/tinysz-nanshan
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..");
const pipeline = path.join(repo, "tinyworlds-pipeline");

const { values } = parseArgs({
  options: {
    out: { type: "string" },
    chunk: { type: "string", default: "2048" },
    preview: { type: "string", default: "4096" },
    city: { type: "string", default: "tinysz" },
  },
  strict: false,
});

const city = values.city || "tinysz";
// config.ts reads --city / TINY_CITY at import time — set them first.
if (!process.argv.includes("--city") && !process.argv.some((a) => a.startsWith("--city="))) {
  process.argv.splice(2, 0, "--city", city);
}
process.env.TINY_CITY = city;

const { DIRS } = await import(path.join(pipeline, "dist/config.js"));
const { makeProjection } = await import(path.join(pipeline, "dist/projection.js"));
const { binRect } = await import(path.join(pipeline, "dist/render/bin.js"));
const { ChunkRenderer } = await import(path.join(pipeline, "dist/render/chunk.js"));
const { toRgba, writePng } = await import(path.join(pipeline, "dist/render/encode.js"));
const { loadScene } = await import(path.join(pipeline, "dist/render/scene.js"));

const defaultOut = {
  tinysz: "/workspace/tinysz-futian",
  "tinysz-nanshan": "/workspace/tinysz-nanshan",
  "tinysz-luohu": "/workspace/tinysz-luohu",
  "tinysz-core": "/workspace/tinysz-core",
};
const out = path.resolve(values.out || defaultOut[city] || `/workspace/${city}`);
const chunk = Math.max(256, Number(values.chunk));
const previewW = Math.max(256, Number(values.preview));
const chunksDir = path.join(out, "chunks");
fs.mkdirSync(chunksDir, { recursive: true });

const { scene } = loadScene(DIRS.scene);
const { minX, minY, maxX, maxY } = scene.bounds;
const width = maxX - minX;
const height = maxY - minY;
const renderer = new ChunkRenderer(scene);

const cols = Math.ceil(width / chunk);
const rows = Math.ceil(height / chunk);
const meta = {
  city,
  look: scene.look ?? null,
  origin: { x: minX, y: minY },
  width,
  height,
  chunk,
  cols,
  rows,
  geo: scene.geo,
};
fs.writeFileSync(path.join(out, "meta.json"), JSON.stringify(meta, null, 2));
console.log(`render_full: ${width}×${height} in ${cols}×${rows} chunks of ${chunk} from (${minX},${minY})`);

const t0 = Date.now();
let n = 0;
for (let row = 0; row < rows; row++) {
  for (let col = 0; col < cols; col++) {
    const x0 = minX + col * chunk;
    const y0 = minY + row * chunk;
    const w = Math.min(chunk, maxX - x0);
    const h = Math.min(chunk, maxY - y0);
    const dest = path.join(chunksDir, `${col}_${row}.png`);
    n++;
    if (fs.existsSync(dest) && fs.statSync(dest).size > 1000) {
      console.log(`  skip ${n}/${cols * rows} ${col}_${row} ${w}×${h}`);
      continue;
    }
    const job = binRect(scene, { x0, y0, w, h });
    const indices = renderer.render(job);
    const rgba = toRgba(indices);
    if (!rgba) {
      // entirely in the fog — write a transparent (then flattened) empty tile
      const empty = Buffer.alloc(w * h * 4, 0);
      for (let i = 0; i < w * h; i++) {
        empty[i * 4] = 0xe9;
        empty[i * 4 + 1] = 0xe6;
        empty[i * 4 + 2] = 0xdf;
        empty[i * 4 + 3] = 255;
      }
      await writePng(dest, empty, w, h);
    } else {
      await writePng(dest, rgba, w, h);
    }
    console.log(`  ${n}/${cols * rows} ${col}_${row} ${w}×${h}  ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  }
}

// Downscaled preview, assembled in Python-friendly chunks via a second pass would
// need sharp at full size. Build a preview by resizing each chunk and pasting
// into a small canvas — that's well under sharp's limits.
const scale = previewW / width;
const previewH = Math.max(1, Math.round(height * scale));
const { createRequire } = await import("node:module");
const require = createRequire(path.join(pipeline, "package.json"));
const sharp = require("sharp");
const canvas = sharp({
  create: { width: previewW, height: previewH, channels: 3, background: "#1a1e1c" },
  limitInputPixels: false,
});
const composites = [];
for (let row = 0; row < rows; row++) {
  for (let col = 0; col < cols; col++) {
    const src = path.join(chunksDir, `${col}_${row}.png`);
    if (!fs.existsSync(src)) continue;
    const x0 = col * chunk;
    const y0 = row * chunk;
    const w = Math.min(chunk, width - x0);
    const h = Math.min(chunk, height - y0);
    const dw = Math.max(1, Math.round(w * scale));
    const dh = Math.max(1, Math.round(h * scale));
    const left = Math.min(previewW - dw, Math.round(x0 * scale));
    const top = Math.min(previewH - dh, Math.round(y0 * scale));
    const buf = await sharp(src, { limitInputPixels: false })
      .resize(dw, dh, { kernel: "lanczos3" })
      .png()
      .toBuffer();
    composites.push({ input: buf, left: Math.max(0, left), top: Math.max(0, top) });
  }
}
const previewPath = path.join(out, "procedural-preview.jpg");
await sharp({
  create: { width: previewW, height: previewH, channels: 3, background: "#1a1e1c" },
  limitInputPixels: false,
})
  .composite(composites)
  .jpeg({ quality: 90 })
  .toFile(previewPath);
console.log(`render_full: preview ${previewPath} ${previewW}×${previewH}, ${((Date.now() - t0) / 1000).toFixed(1)}s`);
