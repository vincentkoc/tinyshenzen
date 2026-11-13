import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import sharp from "sharp";
import { afterEach, describe, expect, it } from "vitest";
import {
  accumulate,
  DEFAULT_OPENAI_MODEL,
  detectProvider,
  edgeWeight,
  finalise,
  openaiEditSize,
  restyleImage,
  tilesFor,
  usdFromUsage,
} from "./restyle.js";

describe("tilesFor", () => {
  it("returns one tile when the picture fits in the window", () => {
    expect(tilesFor(800, 600, 1024, 256)).toEqual([{ x: 0, y: 0, w: 800, h: 600 }]);
  });

  it("covers a large canvas with overlapping windows and no gaps", () => {
    const tiles = tilesFor(2000, 1500, 1024, 256);
    expect(tiles.length).toBeGreaterThan(1);
    const hit = new Uint8Array(2000 * 1500);
    for (const t of tiles)
      for (let y = 0; y < t.h; y++) for (let x = 0; x < t.w; x++) hit[(t.y + y) * 2000 + (t.x + x)] = 1;
    expect([...hit].every((v) => v === 1)).toBe(true);
    expect(tiles.some((t) => t.x === 0 && t.y === 0)).toBe(true);
    expect(tiles.some((t) => t.x > 0 && t.x < 1024)).toBe(true);
  });
});

describe("edgeWeight", () => {
  const tile = { x: 0, y: 0, w: 100, h: 80 };
  it("is 1 in the middle and 0 on the outer edge, rising smoothly through the hem", () => {
    expect(edgeWeight(50, 40, tile, 16)).toBe(1);
    expect(edgeWeight(0, 40, tile, 16)).toBe(0);
    const mid = edgeWeight(8, 40, tile, 16);
    expect(mid).toBeGreaterThan(0.2);
    expect(mid).toBeLessThan(0.8);
  });
});

describe("accumulate and finalise", () => {
  it("feathers two overlapping solid colours into a blend in the hem, and keeps each colour outside it", () => {
    const w = 40;
    const h = 16;
    const acc = new Float32Array(w * h * 4);
    const weight = new Float32Array(w * h);
    const red = new Uint8Array(24 * h * 4);
    const blue = new Uint8Array(24 * h * 4);
    for (let i = 0; i < 24 * h; i++) {
      red[i * 4] = 255;
      red[i * 4 + 3] = 255;
      blue[i * 4 + 2] = 255;
      blue[i * 4 + 3] = 255;
    }
    const overlap = 8;
    accumulate(acc, weight, w, { x: 0, y: 0, w: 24, h }, red, overlap);
    accumulate(acc, weight, w, { x: 16, y: 0, w: 24, h }, blue, overlap);
    const out = finalise(acc, weight, w, h);
    expect([out[4], out[5], out[6]]).toEqual([255, 0, 0]);
    const r = ((h / 2) * w + 38) * 4;
    expect([out[r], out[r + 1], out[r + 2]]).toEqual([0, 0, 255]);
    const m = ((h / 2) * w + 20) * 4;
    expect(out[m]).toBeGreaterThan(20);
    expect(out[m + 2]).toBeGreaterThan(20);
  });
});

describe("detectProvider", () => {
  const snapshot = { ...process.env };
  afterEach(() => {
    for (const k of Object.keys(process.env)) {
      if (!(k in snapshot)) delete process.env[k];
    }
    Object.assign(process.env, snapshot);
  });

  it("explains how to set a key when none is present", () => {
    delete process.env.GEMINI_API_KEY;
    delete process.env.GOOGLE_API_KEY;
    delete process.env.OPENAI_API_KEY;
    delete process.env.RESTYLE_PROVIDER;
    delete process.env.RESTYLE_MODEL;
    const r = detectProvider();
    expect("error" in r).toBe(true);
    if ("error" in r) {
      expect(r.error).toMatch(/GEMINI_API_KEY/);
      expect(r.error).not.toMatch(/AIza|sk-/);
    }
  });

  it("picks Gemini when GEMINI_API_KEY is set, without needing a live call", () => {
    delete process.env.OPENAI_API_KEY;
    delete process.env.GOOGLE_API_KEY;
    delete process.env.RESTYLE_PROVIDER;
    delete process.env.RESTYLE_MODEL;
    process.env.GEMINI_API_KEY = "test-not-a-real-key";
    const r = detectProvider();
    expect(r).toMatchObject({ provider: "gemini", model: "gemini-2.5-flash-image" });
  });

  it("picks the current OpenAI image-edit model when OPENAI_API_KEY is set", () => {
    delete process.env.GEMINI_API_KEY;
    delete process.env.GOOGLE_API_KEY;
    delete process.env.RESTYLE_PROVIDER;
    delete process.env.RESTYLE_MODEL;
    process.env.OPENAI_API_KEY = "test-not-a-real-key";
    const r = detectProvider();
    expect(r).toMatchObject({ provider: "openai", model: DEFAULT_OPENAI_MODEL });
    expect(DEFAULT_OPENAI_MODEL).toMatch(/^gpt-image-/);
    expect(DEFAULT_OPENAI_MODEL).not.toBe("gpt-image-1");
  });
});

describe("restyleImage", () => {
  it("paints overlapping windows through a fake provider and writes a PNG of the same size", async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "restyle-"));
    const input = path.join(dir, "in.png");
    const output = path.join(dir, "out.png");
    await sharp({
      create: { width: 80, height: 40, channels: 4, background: { r: 0, g: 200, b: 0, alpha: 1 } },
    })
      .png()
      .toFile(input);
    let calls = 0;
    await restyleImage({
      input,
      output,
      window: 48,
      overlap: 16,
      paint: async (png) => {
        calls++;
        const meta = await sharp(png).metadata();
        return sharp({
          create: {
            width: meta.width ?? 1,
            height: meta.height ?? 1,
            channels: 4,
            background: { r: 255, g: 0, b: 0, alpha: 1 },
          },
        })
          .png()
          .toBuffer();
      },
      log: () => {},
    });
    expect(calls).toBeGreaterThan(1);
    const { data, info } = await sharp(output).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
    expect(info.width).toBe(80);
    expect(info.height).toBe(40);
    // every pixel is red: the fake painter replaced the green
    expect(data[0]).toBe(255);
    expect(data[1]).toBe(0);
    expect(data[2]).toBe(0);
    const last = ((40 - 1) * 80 + 79) * 4;
    expect(data[last]).toBe(255);
    expect(data[last + 2]).toBe(0);
    fs.rmSync(dir, { recursive: true, force: true });
  });
});

describe("openaiEditSize", () => {
  it("keeps official 1536×1024 and 1024×1024 sizes exact, so previews are not squashed", () => {
    expect(openaiEditSize(1536, 1024)).toBe("1536x1024");
    expect(openaiEditSize(1024, 1024)).toBe("1024x1024");
    expect(openaiEditSize(1024, 1536)).toBe("1024x1536");
  });

  it("rounds odd tile sizes to multiples of 16", () => {
    expect(openaiEditSize(800, 600)).toBe("800x608");
  });
});

describe("usdFromUsage", () => {
  it("prices text input, image input and image output at the official per-million rates", () => {
    const usd = usdFromUsage({
      input_tokens_details: { text_tokens: 200, image_tokens: 1250 },
      output_tokens_details: { image_tokens: 1372 },
      output_tokens: 1372,
    });
    // 200*5 + 1250*8 + 1372*30 = 1000 + 10000 + 41160 = 52160 / 1e6
    expect(usd).toBeCloseTo(0.05216, 5);
  });
});
