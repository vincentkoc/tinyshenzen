import type { CoverMask } from "@/worlds/types";
import { coverCells } from "./cover";
import { featherMask } from "./edge-fog";
import type { MapEngine, ViewState } from "./engine";
import { easeNight, stepNight } from "./night-mode";
import { BLUR_FRAG, EMIT_FRAG, FINAL_FRAG, VERT } from "./night-shaders";

/**
 * Night on the map: a WebGL pass over the tiles canvas. Each time the engine draws, its canvas
 * is copied to a texture; a quarter-size pass finds what gives off light, two blurs spread it,
 * and the last pass grades the picture to night and lights it (see night-shaders.ts).
 *
 * Loaded on demand (map-app imports it the first time night is wanted), so a visitor who never
 * turns it on never downloads it. The output canvas sits just above the tiles canvas; while it
 * shows, the tiles canvas is hidden but keeps drawing, as the source.
 */

/** The output is never denser than this many px per CSS px: the glow is soft anyway. */
const MAX_DPR = 2;
/** Coverage texture: px per finest-level tile side (the cover mask's cells, or this), and its feather. */
const COV_SUB = 4;
const COV_FEATHER = 1;
/** How far up (map px) each of the water's four reflection taps looks for lights. */
const REFLECT_MAP_PX = 9;

export interface NightLayerOptions {
  engine: MapEngine;
  /** the engine's tiles canvas: read, never drawn on */
  source: HTMLCanvasElement;
  /** the canvas the night picture goes to */
  target: HTMLCanvasElement;
  reducedMotion?: boolean;
  /** which parts of the edge tiles are inside the coverage outline; none: every tile in full */
  cover?: CoverMask | null;
  /** told whenever the output starts or stops covering the tiles */
  onShow?: (showing: boolean) => void;
}

interface Target {
  tex: WebGLTexture;
  fbo: WebGLFramebuffer;
  w: number;
  h: number;
}

interface Program {
  prog: WebGLProgram;
  u: Record<string, WebGLUniformLocation | null>;
}

export class NightLayer {
  private readonly engine: MapEngine;
  private readonly source: HTMLCanvasElement;
  private readonly target: HTMLCanvasElement;
  private readonly onShow?: (showing: boolean) => void;
  private readonly cover: CoverMask | null;
  private gl: WebGLRenderingContext;
  private reducedMotion: boolean;

  private emit!: Program;
  private blur!: Program;
  private final!: Program;
  private quad!: WebGLBuffer;
  private src!: WebGLTexture;
  private cov!: WebGLTexture;
  private covRect = { x: 0, y: 0, w: 1, h: 1 };
  private srcW = 0;
  private srcH = 0;
  private a: Target | null = null;
  private b: Target | null = null;
  private c: Target | null = null;
  private d: Target | null = null;

  private view: ViewState | null = null;
  private srcFresh = false;
  private mix = 0;
  private goal = 0;
  private raf = 0;
  private lastStep = 0;
  private showing = false;
  private lost = false;
  private destroyed = false;
  private readonly offDraw: () => void;
  /** map px subtracted before the shader sees them, so its floats keep their precision */
  private readonly anchor: { x: number; y: number };

  /** Null when the browser has no WebGL (the caller falls back to a plain tint). */
  static create(options: NightLayerOptions): NightLayer | null {
    const gl = options.target.getContext("webgl", {
      alpha: false,
      antialias: false,
      depth: false,
      stencil: false,
      premultipliedAlpha: false,
      preserveDrawingBuffer: false,
      powerPreference: "low-power",
    });
    if (!gl) return null;
    try {
      return new NightLayer(options, gl);
    } catch {
      return null;
    }
  }

  private constructor(options: NightLayerOptions, gl: WebGLRenderingContext) {
    this.engine = options.engine;
    this.source = options.source;
    this.target = options.target;
    this.onShow = options.onShow;
    this.cover = options.cover ?? null;
    this.reducedMotion = options.reducedMotion ?? false;
    this.gl = gl;
    const b = this.engine.manifest.bounds;
    this.anchor = { x: Math.round((b.minX + b.maxX) / 2), y: Math.round((b.minY + b.maxY) / 2) };
    this.setup();
    this.target.addEventListener("webglcontextlost", this.onLost);
    this.target.addEventListener("webglcontextrestored", this.onRestored);
    this.offDraw = this.engine.on("draw", (view, now) => {
      this.view = view;
      this.srcFresh = true;
      if (this.mix > 0 || this.goal > 0) this.render(now);
    });
  }

  /** Where the lighting should go: 0 day, 1 night (in between for dusk on the clock). */
  setGoal(goal: number): void {
    const g = Math.min(1, Math.max(0, goal));
    if (g === this.goal) return;
    this.goal = g;
    this.engine.invalidate(); // a fresh copy of the tiles, then the fade runs from it
    this.kick();
  }

  setReducedMotion(reduced: boolean): void {
    this.reducedMotion = reduced;
  }

  destroy(): void {
    this.destroyed = true;
    cancelAnimationFrame(this.raf);
    this.offDraw();
    this.target.removeEventListener("webglcontextlost", this.onLost);
    this.target.removeEventListener("webglcontextrestored", this.onRestored);
    this.show(false);
    this.gl.getExtension("WEBGL_lose_context")?.loseContext();
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
    const dt = this.lastStep ? Math.min(64, now - this.lastStep) : 16;
    this.lastStep = now;
    this.mix = stepNight(this.mix, this.goal, dt, this.reducedMotion);
    // the engine may be idle: draw from the last copy rather than ask it for a new frame
    if (this.view) this.render(now);
    if (this.mix !== this.goal) this.raf = requestAnimationFrame(this.tick);
    else if (this.mix === 0) this.show(false);
  };

  private show(on: boolean): void {
    if (this.showing === on) return;
    this.showing = on;
    this.onShow?.(on);
  }

  // ---- GL setup --------------------------------------------------------------------------

  private setup(): void {
    const gl = this.gl;
    this.emit = this.program(EMIT_FRAG, [
      "uSrc",
      "uSrcTexel",
      "uView",
      "uCov",
      "uCovRect",
      "uLights",
      "uTime",
    ]);
    this.blur = this.program(BLUR_FRAG, ["uTex", "uStep"]);
    this.final = this.program(FINAL_FRAG, [
      "uSrc",
      "uSrcTexel",
      "uView",
      "uCov",
      "uCovRect",
      "uLights",
      "uTime",
      "uGlowNear",
      "uGlowFar",
      "uMix",
      "uDevScale",
      "uSrcScale",
      "uAspect",
      "uReflect",
    ]);
    const quad = gl.createBuffer();
    if (!quad) throw new Error("no buffer");
    this.quad = quad;
    gl.bindBuffer(gl.ARRAY_BUFFER, quad);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    this.src = this.texture();
    this.cov = this.texture();
    this.uploadCoverage();
    this.srcW = this.srcH = 0;
    this.a = this.b = this.c = this.d = null;
  }

  private program(frag: string, uniforms: string[]): Program {
    const gl = this.gl;
    const compile = (type: number, text: string) => {
      const s = gl.createShader(type);
      if (!s) throw new Error("no shader");
      gl.shaderSource(s, text);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS) && !gl.isContextLost())
        throw new Error(gl.getShaderInfoLog(s) ?? "shader");
      return s;
    };
    const prog = gl.createProgram();
    if (!prog) throw new Error("no program");
    gl.attachShader(prog, compile(gl.VERTEX_SHADER, VERT));
    gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, frag));
    gl.bindAttribLocation(prog, 0, "aPos");
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS) && !gl.isContextLost())
      throw new Error(gl.getProgramInfoLog(prog) ?? "link");
    const u: Program["u"] = {};
    for (const name of uniforms) u[name] = gl.getUniformLocation(prog, name);
    return { prog, u };
  }

  private texture(): WebGLTexture {
    const gl = this.gl;
    const tex = gl.createTexture();
    if (!tex) throw new Error("no texture");
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return tex;
  }

  private renderTarget(w: number, h: number, old: Target | null): Target {
    const gl = this.gl;
    if (old && old.w === w && old.h === h) return old;
    if (old) {
      gl.deleteTexture(old.tex);
      gl.deleteFramebuffer(old.fbo);
    }
    const tex = this.texture();
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    const fbo = gl.createFramebuffer();
    if (!fbo) throw new Error("no framebuffer");
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    return { tex, fbo, w, h };
  }

  /** The finest level's tile boxes as a small mask: 255 where there are tiles. */
  private uploadCoverage(): void {
    const gl = this.gl;
    const grid = this.engine.getCoverage();
    gl.bindTexture(gl.TEXTURE_2D, this.cov);
    if (!grid) {
      gl.texImage2D(
        gl.TEXTURE_2D,
        0,
        gl.LUMINANCE,
        1,
        1,
        0,
        gl.LUMINANCE,
        gl.UNSIGNED_BYTE,
        new Uint8Array([255]),
      );
      this.covRect = { x: -1e9, y: -1e9, w: 2e9, h: 2e9 };
      return;
    }
    const pad = COV_FEATHER * 3 + 1;
    const sub = this.cover?.cells ?? COV_SUB;
    const { data, width, height } = coverCells(grid, this.cover, sub, pad);
    // featherMask pulls the ramp inside: lights fade out before the outline, never past it
    const empty = featherMask(data, width, height, COV_FEATHER);
    const bytes = new Uint8Array(width * height);
    for (let i = 0; i < bytes.length; i++) bytes[i] = Math.round((1 - empty[i]) * 255);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    gl.texImage2D(
      gl.TEXTURE_2D,
      0,
      gl.LUMINANCE,
      width,
      height,
      0,
      gl.LUMINANCE,
      gl.UNSIGNED_BYTE,
      bytes,
    );
    const cell = grid.span / sub;
    this.covRect = {
      x: grid.originX - pad * cell - this.anchor.x,
      y: grid.originY - pad * cell - this.anchor.y,
      w: width * cell,
      h: height * cell,
    };
  }

  // ---- drawing ---------------------------------------------------------------------------

  private render(now: number): void {
    const gl = this.gl;
    const v = this.view;
    if (!v || this.lost || this.destroyed || gl.isContextLost()) return;
    const sw = this.source.width;
    const sh = this.source.height;
    if (sw < 2 || sh < 2) return;

    // output size: the source's, capped in density
    const k = Math.min(1, MAX_DPR / v.dpr);
    const ow = Math.max(1, Math.round(sw * k));
    const oh = Math.max(1, Math.round(sh * k));
    if (this.target.width !== ow) this.target.width = ow;
    if (this.target.height !== oh) this.target.height = oh;
    const qw = Math.max(1, Math.ceil(ow / 4));
    const qh = Math.max(1, Math.ceil(oh / 4));
    const fw = Math.max(1, Math.ceil(qw / 4));
    const fh = Math.max(1, Math.ceil(qh / 4));
    this.a = this.renderTarget(qw, qh, this.a);
    this.b = this.renderTarget(qw, qh, this.b);
    this.c = this.renderTarget(fw, fh, this.c);
    this.d = this.renderTarget(fw, fh, this.d);

    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.src);
    if (this.srcFresh || sw !== this.srcW || sh !== this.srcH) {
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
      if (sw !== this.srcW || sh !== this.srcH) {
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, this.source);
        this.srcW = sw;
        this.srcH = sh;
      } else {
        gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, gl.RGBA, gl.UNSIGNED_BYTE, this.source);
      }
      this.srcFresh = false;
    }

    const s = v.scale * v.dpr; // source px per map px
    const viewRect: [number, number, number, number] = [
      v.left - this.anchor.x,
      v.top - this.anchor.y,
      sw / s,
      sh / s,
    ];
    const eased = easeNight(this.mix);
    const lights = Math.min(1, Math.max(0, (eased - 0.25) / 0.75));
    const time = this.reducedMotion ? 0 : (now % 600_000) / 1000;
    const r = this.covRect;

    gl.bindBuffer(gl.ARRAY_BUFFER, this.quad);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    gl.disable(gl.BLEND);

    const shared = (p: Program) => {
      gl.uniform1i(p.u.uSrc, 0);
      gl.uniform2f(p.u.uSrcTexel, 1 / sw, 1 / sh);
      gl.uniform4f(p.u.uView, ...viewRect);
      gl.uniform1i(p.u.uCov, 1);
      gl.uniform4f(p.u.uCovRect, r.x, r.y, r.w, r.h);
      gl.uniform1f(p.u.uLights, lights);
      gl.uniform1f(p.u.uTime, time);
    };
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, this.cov);

    // 1. what gives off light, at a quarter size
    gl.useProgram(this.emit.prog);
    shared(this.emit);
    this.pass(this.a);

    // 2. near glow: blur across, then down (quarter size)
    this.blurPass(this.a, this.b, 1, 0);
    this.blurPass(this.b, this.a, 0, 1);
    // 3. far glow: the near glow at a sixteenth, blurred again
    this.blurPass(this.a, this.c, 1, 0);
    this.blurPass(this.c, this.d, 0, 1);
    this.blurPass(this.d, this.c, 1.6, 0);
    this.blurPass(this.c, this.d, 0, 1.6);

    // 4. the picture
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.viewport(0, 0, ow, oh);
    gl.useProgram(this.final.prog);
    const u = this.final.u;
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.src);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, this.cov);
    gl.activeTexture(gl.TEXTURE2);
    gl.bindTexture(gl.TEXTURE_2D, this.a.tex);
    gl.activeTexture(gl.TEXTURE3);
    gl.bindTexture(gl.TEXTURE_2D, this.d.tex);
    shared(this.final);
    gl.uniform1i(u.uGlowNear, 2);
    gl.uniform1i(u.uGlowFar, 3);
    gl.uniform1f(u.uMix, eased);
    gl.uniform1f(u.uDevScale, v.scale * Math.min(v.dpr, MAX_DPR));
    gl.uniform1f(u.uSrcScale, s);
    gl.uniform1f(u.uAspect, ow / oh);
    gl.uniform1f(u.uReflect, (REFLECT_MAP_PX * s) / sh);
    gl.drawArrays(gl.TRIANGLES, 0, 3);

    if (this.mix > 0) this.show(true);
  }

  private pass(to: Target): void {
    const gl = this.gl;
    gl.bindFramebuffer(gl.FRAMEBUFFER, to.fbo);
    gl.viewport(0, 0, to.w, to.h);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  private blurPass(from: Target, to: Target, dx: number, dy: number): void {
    const gl = this.gl;
    gl.useProgram(this.blur.prog);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, from.tex);
    gl.uniform1i(this.blur.u.uTex, 0);
    gl.uniform2f(this.blur.u.uStep, dx / from.w, dy / from.h);
    this.pass(to);
  }

  // ---- context loss ----------------------------------------------------------------------

  private readonly onLost = (e: Event): void => {
    e.preventDefault();
    this.lost = true;
    this.show(false);
  };

  private readonly onRestored = (): void => {
    this.lost = false;
    try {
      this.setup();
    } catch {
      return;
    }
    this.engine.invalidate();
    this.kick();
  };
}
