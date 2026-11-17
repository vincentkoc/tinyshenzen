/// <reference lib="webworker" />
import type { CoverMask } from "@/worlds/types";
import {
  MARGIN,
  buildNightField,
  drawFramed,
  gridFrom,
  nightTile,
  type GridData,
  type NightField,
  type TilePlace,
} from "./night-look";

/** Night versions of tile pictures, made off the main thread (see night-look.ts and night.ts). */

export type NightRequest =
  | { kind: "field"; grid: GridData | null; cover: CoverMask | null }
  | {
      kind: "tile";
      id: number;
      /** the picture alone, or (`around`) the 3×3 block around it, row by row */
      bitmap: ImageBitmap | null;
      around: (ImageBitmap | null)[] | null;
      place: TilePlace | null;
    };

export type NightReply =
  { kind: "tile"; id: number; bitmap: ImageBitmap | null } | { kind: "unable" };

declare const self: DedicatedWorkerGlobalScope;

let field: NightField | null = null;
let canvas: OffscreenCanvas | null = null;
let ctx: OffscreenCanvasRenderingContext2D | null = null;

self.onmessage = (event: MessageEvent<NightRequest>) => {
  const msg = event.data;
  if (msg.kind === "field") {
    field = buildNightField(gridFrom(msg.grid), msg.cover);
    return;
  }
  const { id, bitmap, around, place } = msg;
  const all = [bitmap, ...(around ?? [])];
  const free = () => all.forEach((b) => b?.close());
  try {
    const one = around ? around[4] : bitmap;
    if (!one) throw new Error("no picture");
    const margin = around ? MARGIN : 0;
    const size = one.width;
    const w = size + 2 * margin;
    const h = one.height + 2 * margin;
    if (typeof OffscreenCanvas === "undefined") throw new Error("no OffscreenCanvas");
    if (!canvas || canvas.width !== w || canvas.height !== h) {
      canvas = new OffscreenCanvas(w, h);
      ctx = canvas.getContext("2d", { willReadFrequently: true });
    }
    if (!ctx) throw new Error("no 2d");
    if (around) drawFramed(ctx, around, size);
    else {
      ctx.clearRect(0, 0, w, h);
      ctx.drawImage(one, 0, 0);
    }
    free();
    const img = ctx.getImageData(0, 0, w, h);
    nightTile(img.data, w, h, place, field, margin);
    ctx.putImageData(img, 0, 0);
    createImageBitmap(canvas, margin, margin, w - 2 * margin, h - 2 * margin).then(
      (out) => self.postMessage({ kind: "tile", id, bitmap: out } satisfies NightReply, [out]),
      () => self.postMessage({ kind: "unable" } satisfies NightReply),
    );
  } catch {
    free();
    self.postMessage({ kind: "unable" } satisfies NightReply);
  }
};
