import type { CoverMask } from "@/worlds/types";
import type { MapEngine, TileLook } from "./engine";
import {
  MARGIN,
  buildNightField,
  drawFramed,
  gridData,
  gridFrom,
  nightPaper,
  nightTile,
  type GridData,
  type NightField,
  type TilePlace,
} from "./night-look";
import { easeNight, stepNight } from "./night-mode";
import type { NightReply, NightRequest } from "./night.worker";

/**
 * Night on the map. Each tile picture is turned into its night version once, off the main thread
 * (night.worker.ts runs night-look.ts), and the engine draws those in place of the tiles: panning
 * at night costs what it costs by day. Nothing is drawn per frame here; the fade only moves the
 * engine's mix.
 *
 * Loaded on demand (use-night imports it the first time night is wanted).
 */

/** Waits at most this long for the view's night pictures before fading anyway (ms). */
const WARM_UP_MS = 1500;
/** Back to day, the night pictures are kept this long in case night comes back (ms). */
const KEEP_MS = 60_000;

export interface NightLookOptions {
  engine: MapEngine;
  /** which parts of the edge tiles are inside the coverage outline; none: every tile in full */
  cover?: CoverMask | null;
  reducedMotion?: boolean;
  /** told whenever the night starts or stops showing */
  onShow?: (showing: boolean) => void;
}

interface Job {
  resolve: (bitmap: ImageBitmap | null) => void;
}

export class NightLook implements TileLook {
  readonly paper: [number, number, number];
  private readonly engine: MapEngine;
  /** the coverage field: built in the workers; here only when pictures are made here */
  private readonly grid: GridData | null;
  private readonly cover: CoverMask | null;
  private field: NightField | null | undefined;
  private readonly onShow?: (showing: boolean) => void;
  private reducedMotion: boolean;
  private workers: Worker[] = [];
  private nextWorker = 0;
  private readonly jobs = new Map<number, Job>();
  private jobId = 0;
  /** no workers here (or they cannot draw): pictures are made on the main thread, one at a time */
  private inline = false;
  private inlineChain: Promise<unknown> = Promise.resolve();

  private mix = 0;
  private goal = 0;
  private raf = 0;
  private lastStep = 0;
  private wantedAt = 0;
  private attached = false;
  private release: ReturnType<typeof setTimeout> | undefined;
  private destroyed = false;

  constructor(options: NightLookOptions) {
    this.engine = options.engine;
    this.onShow = options.onShow;
    this.reducedMotion = options.reducedMotion ?? false;
    this.paper = nightPaper(this.engine.getPaper());
    this.grid = gridData(this.engine.getCoverage());
    this.cover = options.cover ?? null;
    this.startWorkers();
  }

  /** Where the lighting should go: 0 day, 1 night (in between for dusk on the clock). */
  setGoal(goal: number): void {
    const g = Math.min(1, Math.max(0, goal));
    if (g === this.goal) return;
    this.goal = g;
    if (g <= 0) this.engine.setLookWarm(false);
    if (g > 0) {
      clearTimeout(this.release);
      if (!this.attached) {
        this.attached = true;
        this.engine.setLook(this);
      }
      if (this.mix === 0) this.wantedAt = performance.now();
      this.engine.setLookWarm(true);
      this.onShow?.(true);
    }
    this.kick();
  }

  setReducedMotion(reduced: boolean): void {
    this.reducedMotion = reduced;
  }

  destroy(): void {
    this.destroyed = true;
    cancelAnimationFrame(this.raf);
    clearTimeout(this.release);
    if (this.attached) this.engine.setLook(null);
    for (const w of this.workers) w.terminate();
    this.workers = [];
    for (const job of this.jobs.values()) job.resolve(null);
    this.jobs.clear();
  }

  // ---- TileLook --------------------------------------------------------------------------

  make(
    bitmap: ImageBitmap,
    place: TilePlace | null,
    around: (ImageBitmap | null)[] | null,
  ): Promise<ImageBitmap | null> {
    if (this.destroyed) return Promise.resolve(null);
    if (this.inline || this.workers.length === 0) return this.makeInline(bitmap, place, around);
    // copies go to the worker: the engine keeps its own pictures
    const pictures = around ?? [bitmap];
    return Promise.all(pictures.map((b) => (b ? createImageBitmap(b) : null))).then(
      (copies) =>
        new Promise<ImageBitmap | null>((resolve) => {
          const sent = copies.filter((c): c is ImageBitmap => c !== null);
          if (this.destroyed || this.inline || this.workers.length === 0) {
            for (const c of sent) c.close();
            resolve(this.destroyed ? null : this.makeInline(bitmap, place, around));
            return;
          }
          const id = ++this.jobId;
          this.jobs.set(id, { resolve });
          const worker = this.workers[this.nextWorker++ % this.workers.length];
          const msg: NightRequest = around
            ? { kind: "tile", id, bitmap: null, around: copies, place }
            : { kind: "tile", id, bitmap: copies[0], around: null, place };
          worker.postMessage(msg, sent);
        }),
    );
  }

  // ---- fade ------------------------------------------------------------------------------

  private kick(): void {
    if (!this.raf && !this.destroyed) {
      this.lastStep = 0;
      this.raf = requestAnimationFrame(this.tick);
    }
  }

  private readonly tick = (now: number): void => {
    this.raf = 0;
    if (this.destroyed) return;
    // on the way in, wait (a little) for the view's pictures, so night never comes in patchy
    const warming =
      this.goal > this.mix &&
      this.mix === 0 &&
      this.engine.getLookReady() < 0.95 &&
      performance.now() - this.wantedAt < WARM_UP_MS;
    if (warming) {
      this.raf = requestAnimationFrame(this.tick);
      return;
    }
    // real time, so a slow device does not drag the fade out (a stalled tab still jumps no more
    // than a quarter second)
    const dt = this.lastStep ? Math.min(250, now - this.lastStep) : 16;
    this.lastStep = now;
    this.mix = stepNight(this.mix, this.goal, dt, this.reducedMotion);
    this.engine.setLookMix(easeNight(this.mix));
    if (this.mix !== this.goal) {
      this.raf = requestAnimationFrame(this.tick);
    } else if (this.mix === 0 && this.attached) {
      // back to day: the night pictures are let go a while later, unless night comes back
      this.onShow?.(false);
      clearTimeout(this.release);
      this.release = setTimeout(() => {
        if (this.goal > 0 || this.destroyed) return;
        this.attached = false;
        this.engine.setLook(null);
      }, KEEP_MS);
    }
  };

  // ---- workers ---------------------------------------------------------------------------

  private startWorkers(): void {
    if (typeof Worker === "undefined" || typeof OffscreenCanvas === "undefined") {
      this.inline = true;
      return;
    }
    const cores = navigator.hardwareConcurrency || 2;
    const count = Math.max(1, Math.min(2, cores - 1));
    try {
      for (let i = 0; i < count; i++) {
        const worker = new Worker(new URL("./night.worker.ts", import.meta.url), {
          type: "module",
        });
        worker.onmessage = this.onReply;
        worker.onerror = this.onWorkerError;
        const msg: NightRequest = { kind: "field", grid: this.grid, cover: this.cover };
        worker.postMessage(msg);
        this.workers.push(worker);
      }
    } catch {
      this.onWorkerError();
    }
  }

  private readonly onReply = (event: MessageEvent<NightReply>): void => {
    const msg = event.data;
    if (msg.kind === "unable") {
      this.onWorkerError();
      return;
    }
    const job = this.jobs.get(msg.id);
    this.jobs.delete(msg.id);
    if (!job || this.destroyed) {
      msg.bitmap?.close();
      return;
    }
    job.resolve(msg.bitmap);
  };

  /** A worker that cannot do the job: all of them stop, and the main thread takes over. */
  private readonly onWorkerError = (): void => {
    this.inline = true;
    for (const w of this.workers) w.terminate();
    this.workers = [];
    // the engine asks again for what was in flight
    for (const job of this.jobs.values()) job.resolve(null);
    this.jobs.clear();
  };

  private canvas: HTMLCanvasElement | null = null;

  private makeInline(
    bitmap: ImageBitmap,
    place: TilePlace | null,
    around: (ImageBitmap | null)[] | null,
  ): Promise<ImageBitmap | null> {
    const run = async (): Promise<ImageBitmap | null> => {
      // a frame between pictures, so the page keeps moving
      await new Promise((r) => setTimeout(r, 0));
      if (this.destroyed) return null;
      const margin = around ? MARGIN : 0;
      const size = bitmap.width;
      const w = size + 2 * margin;
      const h = bitmap.height + 2 * margin;
      const canvas = (this.canvas ??= document.createElement("canvas"));
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      if (!ctx) return null;
      if (around) drawFramed(ctx, around, size);
      else ctx.drawImage(bitmap, 0, 0);
      const img = ctx.getImageData(0, 0, w, h);
      if (this.field === undefined) this.field = buildNightField(gridFrom(this.grid), this.cover);
      nightTile(img.data, w, h, place, this.field, margin);
      ctx.putImageData(img, 0, 0);
      return createImageBitmap(canvas, margin, margin, size, bitmap.height);
    };
    const next = this.inlineChain.then(run, run);
    this.inlineChain = next.catch(() => null);
    return next.catch(() => null);
  }
}
