import fs from "node:fs";
import path from "node:path";
import sharp from "sharp";

/** Default window and overlap, in pixels: a 256 px hem, as used on the large isometric maps. */
export const DEFAULT_WINDOW = 1024;
export const DEFAULT_OVERLAP = 256;

export const DEFAULT_PROMPT =
  "Redraw this aerial isometric view as detailed isometric pixel art. " +
  "Keep the exact building footprints, heights, streets, parks and camera. " +
  "Richer materials, sharp pixel edges, tropical Shenzhen: glass curtain-wall towers, " +
  "white and pastel mid-rises, dense urban villages, palms and greenery. " +
  "Do not add, remove or move buildings. Do not change the perspective.";

export interface Tile {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Overlapping windows that cover a canvas, stepped by `window - overlap`. */
export function tilesFor(
  width: number,
  height: number,
  window = DEFAULT_WINDOW,
  overlap = DEFAULT_OVERLAP,
): Tile[] {
  if (overlap >= window) throw new Error("overlap must be smaller than the window");
  if (width <= window && height <= window) return [{ x: 0, y: 0, w: width, h: height }];
  const step = window - overlap;
  const tiles: Tile[] = [];
  for (let y = 0; y < height; y += step) {
    for (let x = 0; x < width; x += step) {
      const w = Math.min(window, width - x);
      const h = Math.min(window, height - y);
      // if this last tile is a sliver, pull it back so it has more context
      const ox = x + w === width && w < window && width > window ? width - window : x;
      const oy = y + h === height && h < window && height > window ? height - window : y;
      tiles.push({
        x: Math.max(0, ox),
        y: Math.max(0, oy),
        w: Math.min(window, width - Math.max(0, ox)),
        h: Math.min(window, height - Math.max(0, oy)),
      });
    }
  }
  // unique by origin (the sliver adjustment can collide)
  const seen = new Set<string>();
  return tiles.filter((t) => {
    const k = `${t.x},${t.y},${t.w},${t.h}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

/**
 * How much a pixel at (px, py) in a tile (x, y, w, h) should count: 1 in the inner part,
 * falling smoothly to 0 at the tile edge over `overlap` pixels. A short "hem", so
 * neighbouring restyles meet without a seam.
 */
export function edgeWeight(px: number, py: number, tile: Tile, overlap: number): number {
  const dl = px - tile.x;
  const dr = tile.x + tile.w - px;
  const dt = py - tile.y;
  const db = tile.y + tile.h - py;
  const d = Math.min(dl, dr, dt, db);
  if (d >= overlap) return 1;
  if (d <= 0) return 0;
  const t = d / overlap;
  return t * t * (3 - 2 * t);
}

/** Blend a tile of RGBA into an accumulating canvas (rgba * weight, and a weight plane). */
export function accumulate(
  acc: Float32Array,
  weight: Float32Array,
  width: number,
  tile: Tile,
  rgba: Uint8Array,
  overlap: number,
): void {
  for (let row = 0; row < tile.h; row++) {
    for (let col = 0; col < tile.w; col++) {
      const px = tile.x + col;
      const py = tile.y + row;
      const w = edgeWeight(px + 0.5, py + 0.5, tile, overlap);
      if (w <= 0) continue;
      const i = (py * width + px) * 4;
      const s = (row * tile.w + col) * 4;
      acc[i] += rgba[s] * w;
      acc[i + 1] += rgba[s + 1] * w;
      acc[i + 2] += rgba[s + 2] * w;
      acc[i + 3] += rgba[s + 3] * w;
      weight[py * width + px] += w;
    }
  }
}

export function finalise(acc: Float32Array, weight: Float32Array, width: number, height: number): Uint8Array {
  const out = new Uint8Array(width * height * 4);
  for (let i = 0; i < width * height; i++) {
    const w = weight[i] || 1;
    const o = i * 4;
    out[o] = Math.max(0, Math.min(255, Math.round(acc[o] / w)));
    out[o + 1] = Math.max(0, Math.min(255, Math.round(acc[o + 1] / w)));
    out[o + 2] = Math.max(0, Math.min(255, Math.round(acc[o + 2] / w)));
    out[o + 3] = Math.max(0, Math.min(255, Math.round(acc[o + 3] / w)));
  }
  return out;
}

export type Provider = "gemini" | "openai";

export function detectProvider(): { provider: Provider; key: string; model: string } | { error: string } {
  const forced = (process.env.RESTYLE_PROVIDER ?? "").trim().toLowerCase();
  const gemini = (process.env.GEMINI_API_KEY ?? process.env.GOOGLE_API_KEY ?? "").trim();
  const openai = (process.env.OPENAI_API_KEY ?? "").trim();
  const modelEnv = (process.env.RESTYLE_MODEL ?? "").trim();
  if (forced === "gemini") {
    if (!gemini)
      return {
        error:
          "RESTYLE_PROVIDER=gemini but no GEMINI_API_KEY (or GOOGLE_API_KEY) is set. " +
          "Get a key at https://aistudio.google.com/apikey and export GEMINI_API_KEY.",
      };
    return { provider: "gemini", key: gemini, model: modelEnv || "gemini-2.5-flash-image" };
  }
  if (forced === "openai") {
    if (!openai)
      return {
        error:
          "RESTYLE_PROVIDER=openai but no OPENAI_API_KEY is set. " +
          "Create one at https://platform.openai.com/api-keys and export OPENAI_API_KEY.",
      };
    return { provider: "openai", key: openai, model: modelEnv || "gpt-image-1" };
  }
  if (gemini) return { provider: "gemini", key: gemini, model: modelEnv || "gemini-2.5-flash-image" };
  if (openai) return { provider: "openai", key: openai, model: modelEnv || "gpt-image-1" };
  return {
    error:
      "No image-model API key is set, so restyle cannot run. " +
      "Recommended: Gemini 2.5 Flash Image (the 'Nano Banana' class of model people use as a " +
      "teacher for isometric maps). Export GEMINI_API_KEY from https://aistudio.google.com/apikey. " +
      "Alternatively, export OPENAI_API_KEY and optionally RESTYLE_PROVIDER=openai. " +
      "A 1536×1024 preview is one or two calls (a few cents). The Futian test map " +
      "(~23,500×11,175 px, 1024 px windows stepped by 768) is about 450 calls: roughly " +
      "US$15–25 with Flash Image, or US$40–70 with a Pro / gpt-image model. A full-city " +
      "urban-core render would be several times that.",
  };
}

export interface RestyleOpts {
  input: string;
  output: string;
  window?: number;
  overlap?: number;
  prompt?: string;
  /** Injected in tests: (png bytes) => restyled png bytes. */
  paint?: (png: Buffer) => Promise<Buffer>;
  log?: (msg: string) => void;
}

async function paintGemini(png: Buffer, prompt: string, key: string, model: string): Promise<Buffer> {
  const url = `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent?key=${encodeURIComponent(key)}`;
  const body = {
    contents: [
      {
        role: "user",
        parts: [{ text: prompt }, { inline_data: { mime_type: "image/png", data: png.toString("base64") } }],
      },
    ],
    generationConfig: { responseModalities: ["IMAGE", "TEXT"] },
  };
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Gemini ${model} HTTP ${res.status}: ${text.slice(0, 400)}`);
  }
  const json = (await res.json()) as {
    candidates?: {
      content?: { parts?: { inlineData?: { data?: string }; inline_data?: { data?: string } }[] };
    }[];
  };
  const parts = json.candidates?.[0]?.content?.parts ?? [];
  for (const part of parts) {
    const data = part.inlineData?.data ?? part.inline_data?.data;
    if (data) return Buffer.from(data, "base64");
  }
  throw new Error(`Gemini ${model} returned no image`);
}

async function paintOpenAI(png: Buffer, prompt: string, key: string, model: string): Promise<Buffer> {
  const form = new FormData();
  form.set("model", model);
  form.set("prompt", prompt);
  const bytes = new Uint8Array(png.byteLength);
  bytes.set(png);
  form.set("image", new Blob([bytes], { type: "image/png" }), "tile.png");
  form.set("size", "1024x1024");
  const res = await fetch("https://api.openai.com/v1/images/edits", {
    method: "POST",
    headers: { Authorization: `Bearer ${key}` },
    body: form,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`OpenAI ${model} HTTP ${res.status}: ${text.slice(0, 400)}`);
  }
  const json = (await res.json()) as { data?: { b64_json?: string }[] };
  const data = json.data?.[0]?.b64_json;
  if (!data) throw new Error(`OpenAI ${model} returned no image`);
  return Buffer.from(data, "base64");
}

export async function restyleImage(opts: RestyleOpts): Promise<void> {
  const window = opts.window ?? DEFAULT_WINDOW;
  const overlap = opts.overlap ?? DEFAULT_OVERLAP;
  const prompt = opts.prompt ?? DEFAULT_PROMPT;
  const log = opts.log ?? console.log;
  let paint = opts.paint;
  if (!paint) {
    const detected = detectProvider();
    if ("error" in detected) throw new Error(detected.error);
    log(`restyle: ${detected.provider} (${detected.model})`);
    paint =
      detected.provider === "gemini"
        ? (png) => paintGemini(png, prompt, detected.key, detected.model)
        : (png) => paintOpenAI(png, prompt, detected.key, detected.model);
  }

  const src = sharp(opts.input).ensureAlpha();
  const { width, height } = await src.metadata();
  if (!width || !height) throw new Error(`cannot read ${opts.input}`);
  const raw = await src.raw().toBuffer();
  const tiles = tilesFor(width, height, window, overlap);
  log(`restyle: ${width}×${height} in ${tiles.length} window${tiles.length === 1 ? "" : "s"}`);
  const acc = new Float32Array(width * height * 4);
  const weight = new Float32Array(width * height);
  let n = 0;
  for (const tile of tiles) {
    const extract = await sharp(raw, { raw: { width, height, channels: 4 } })
      .extract({ left: tile.x, top: tile.y, width: tile.w, height: tile.h })
      .png()
      .toBuffer();
    const painted = await paint(extract);
    const piece = sharp(painted).ensureAlpha().resize(tile.w, tile.h, { kernel: "nearest" });
    const rgba = await piece.raw().toBuffer();
    accumulate(acc, weight, width, tile, rgba, tiles.length === 1 ? 0 : overlap);
    n++;
    log(`restyle: ${n}/${tiles.length} (${tile.x},${tile.y} ${tile.w}×${tile.h})`);
  }
  const out = finalise(acc, weight, width, height);
  await sharp(out, { raw: { width, height, channels: 4 } })
    .png()
    .toFile(opts.output);
  const mb = (fs.statSync(opts.output).size / 1e6).toFixed(2);
  log(`restyle: wrote ${path.relative(process.cwd(), opts.output)} (${mb} MB)`);
}
