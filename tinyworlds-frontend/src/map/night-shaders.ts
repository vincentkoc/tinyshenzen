/**
 * GLSL for the night layer (night.ts). WebGL 1 / GLSL ES 1.00 so it runs on every phone.
 *
 * Nothing here knows what a building or a road is: the painted tiles say it with their
 * colours. A pixel is sorted by colour and the texture around it (water, trees, asphalt,
 * road paint, facades), and each kind reacts to the dark in its own way:
 *
 *   facades    the darker cells of a textured wall are windows; a stable hash per cell of
 *              map space decides which are lit, warm or cool, and in what order they come on
 *   asphalt    sodium-orange pools from street lamps; road paint and car lights glow
 *   water      a dark mirror: the lights above it, stretched and rippled, and a moving sheen
 *   the rest   a blue-hour grade that keeps the picture readable, lit by its neighbours'
 *              light (the blurred emission, a cheap stand-in for bounced light)
 *
 * Every light is cut by the coverage mask, so nothing glows where the map has no tiles.
 */

export const VERT = /* glsl */ `
attribute vec2 aPos;
varying vec2 vUv;
void main() {
  vUv = aPos * 0.5 + 0.5;
  gl_Position = vec4(aPos, 0.0, 1.0);
}
`;

const HEAD = /* glsl */ `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
varying vec2 vUv;
`;

/** Shared helpers: colour classes, hashes, noise, and screen ↔ map space. */
const COMMON = /* glsl */ `
uniform sampler2D uSrc;
uniform vec2 uSrcTexel;
/** visible map rectangle: left, top (map px, relative to uAnchor), width, height */
uniform vec4 uView;
uniform sampler2D uCov;
/** coverage texture's rectangle in the same map space */
uniform vec4 uCovRect;
/** how far the lights have come on, 0 to 1 */
uniform float uLights;
uniform float uTime;

float luma(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }

float hash12(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}

float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  float a = hash12(i);
  float b = hash12(i + vec2(1.0, 0.0));
  float c = hash12(i + vec2(0.0, 1.0));
  float d = hash12(i + vec2(1.0, 1.0));
  return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}

/** map px under a point of the screen (uv: GL's, y up) */
vec2 mapAt(vec2 uv) {
  return uView.xy + vec2(uv.x, 1.0 - uv.y) * uView.zw;
}

/** 1 inside the map's tiles, 0 outside, a short ramp at the edge */
float coverage(vec2 m) {
  vec2 c = (m - uCovRect.xy) / uCovRect.zw;
  if (c.x < 0.0 || c.y < 0.0 || c.x > 1.0 || c.y > 1.0) return 0.0;
  return texture2D(uCov, c).r;
}

/** x water, y trees and grass, z asphalt, w road paint (yellow and white lines) */
vec4 classify(vec3 c) {
  float hi = max(c.r, max(c.g, c.b));
  float lo = min(c.r, min(c.g, c.b));
  float sat = hi - lo;
  float l = luma(c);
  // the sea and lakes are painted teal to blue: far more blue than red, blue close to green
  float water = smoothstep(0.06, 0.14, c.b - c.r) * smoothstep(0.62, 0.76, c.b / max(c.g, 0.01))
    * smoothstep(0.1, 0.2, l) * (1.0 - smoothstep(0.82, 0.92, l));
  // leaves: green over blue by a clear margin, and at least as green as red
  float veg = smoothstep(0.05, 0.13, c.g - c.b) * smoothstep(-0.03, 0.03, c.g - c.r)
    * smoothstep(0.06, 0.13, sat);
  float asphalt = (1.0 - smoothstep(0.035, 0.075, sat)) * smoothstep(0.12, 0.2, l)
    * (1.0 - smoothstep(0.36, 0.45, l));
  // road paint and painted lit windows are yellow with red ahead of green; leaf highlights are not
  float yellow = smoothstep(0.18, 0.32, c.r - c.b) * smoothstep(0.55, 0.68, c.g)
    * smoothstep(0.0, 0.04, c.r - c.g);
  float white = smoothstep(0.78, 0.9, l) * (1.0 - smoothstep(0.05, 0.1, sat));
  return vec4(water, veg * (1.0 - water), asphalt, max(yellow, white * 0.15));
}

const vec3 SODIUM = vec3(1.0, 0.56, 0.22);
const vec3 WARM = vec3(1.0, 0.74, 0.42);
const vec3 COOL = vec3(0.72, 0.86, 1.0);

/** street-lamp pools on asphalt: soft discs on a jittered grid in map space */
float lampPools(vec2 m) {
  vec2 g = m / 26.0;
  vec2 cell = floor(g);
  vec2 j = vec2(hash12(cell), hash12(cell + 17.0)) * 0.5 + 0.25;
  float d = length(fract(g) - j);
  return 0.55 + 0.8 * pow(max(0.0, 1.0 - d * 2.6), 2.0);
}
`;

/**
 * Pass 1, at a quarter of the output size: how much light each spot gives off, averaged over
 * the four source pixels around it. Blurred twice afterwards for the glow and the bounce.
 */
export const EMIT_FRAG =
  HEAD +
  COMMON +
  /* glsl */ `
/** light given off at one spot; busy is how textured the wall around it is (windows) */
vec3 emitAt(vec2 uv, float busy) {
  vec3 c = texture2D(uSrc, uv).rgb;
  vec4 k = classify(c);
  float l = luma(c);
  float building = clamp(1.0 - k.x - k.y - k.z, 0.0, 1.0) * (1.0 - smoothstep(0.8, 0.92, l));
  vec2 m = mapAt(uv);
  float density = 0.18 + 0.42 * noise(m / 90.0);
  vec3 e = building * smoothstep(0.04, 0.16, busy) * density * 0.5 * WARM;
  e += k.z * SODIUM * 0.22 * lampPools(m);
  e += k.w * (1.0 - k.z) * WARM * 0.5;
  return e * coverage(m);
}

void main() {
  vec2 d = uSrcTexel * 1.5;
  vec2 p0 = vUv + vec2(-d.x, -d.y);
  vec2 p1 = vUv + vec2(d.x, -d.y);
  vec2 p2 = vUv + vec2(-d.x, d.y);
  vec2 p3 = vUv + vec2(d.x, d.y);
  float l0 = luma(texture2D(uSrc, p0).rgb);
  float l1 = luma(texture2D(uSrc, p1).rgb);
  float l2 = luma(texture2D(uSrc, p2).rgb);
  float l3 = luma(texture2D(uSrc, p3).rgb);
  float busy = abs(l0 - l3) + abs(l1 - l2);
  vec3 e = emitAt(p0, busy) + emitAt(p1, busy) + emitAt(p2, busy) + emitAt(p3, busy);
  // stored at half strength so a bright crossing does not clip in 8 bits
  gl_FragColor = vec4(e * 0.25 * uLights * 0.5, 1.0);
}
`;

/** A 9-tap Gaussian along one axis, in five linear fetches. */
export const BLUR_FRAG =
  HEAD +
  /* glsl */ `
uniform sampler2D uTex;
uniform vec2 uStep;
void main() {
  vec3 s = texture2D(uTex, vUv).rgb * 0.2270270;
  s += texture2D(uTex, vUv + uStep * 1.3846154).rgb * 0.3162162;
  s += texture2D(uTex, vUv - uStep * 1.3846154).rgb * 0.3162162;
  s += texture2D(uTex, vUv + uStep * 3.2307692).rgb * 0.0702703;
  s += texture2D(uTex, vUv - uStep * 3.2307692).rgb * 0.0702703;
  gl_FragColor = vec4(s, 1.0);
}
`;

/** The picture: day, night, or a mix of both while it turns. */
export const FINAL_FRAG =
  HEAD +
  COMMON +
  /* glsl */ `
uniform sampler2D uGlowNear;
uniform sampler2D uGlowFar;
/** 0 day, 1 night */
uniform float uMix;
/** output px per map px, and source px per map px */
uniform float uDevScale;
uniform float uSrcScale;
uniform float uAspect;
/** how far up a light's reflection is taken from, in uv */
uniform float uReflect;

void main() {
  vec3 day = texture2D(uSrc, vUv).rgb;
  if (uMix <= 0.0) {
    gl_FragColor = vec4(day, 1.0);
    return;
  }
  vec3 c = day;
  float l = luma(c);
  vec2 m = mapAt(vUv);
  float cov = coverage(m);

  // the texture around the pixel, a map px away (a screen px when zoomed out): windows are
  // cells darker than the wall around them
  vec2 t = uSrcTexel * max(1.0, uSrcScale * 0.9);
  float ln = luma(texture2D(uSrc, vUv + vec2(t.x, 0.0)).rgb);
  float ls = luma(texture2D(uSrc, vUv - vec2(t.x, 0.0)).rgb);
  float le = luma(texture2D(uSrc, vUv + vec2(0.0, t.y)).rgb);
  float lw = luma(texture2D(uSrc, vUv - vec2(0.0, t.y)).rgb);
  float mean = (ln + ls + le + lw) * 0.25;
  float hp = l - mean;
  float busy = abs(ln - ls) + abs(le - lw);

  vec4 k = classify(c);
  float water = k.x * (1.0 - smoothstep(0.08, 0.2, busy));
  float building = clamp(1.0 - k.x - k.y - k.z, 0.0, 1.0) * (1.0 - smoothstep(0.86, 0.95, l));

  // ---- blue hour: cool, darker, highlights pulled down hardest so pale ground and paper sink
  float grey = l;
  vec3 graded = mix(c, vec3(grey), 0.42) * vec3(0.2, 0.25, 0.42);
  graded *= 1.0 - 0.6 * l * l;
  graded += vec3(0.010, 0.016, 0.034);

  // ---- light from the neighbourhood: glow (added) and bounce (lights the painted colour)
  vec3 near = texture2D(uGlowNear, vUv).rgb * 2.0;
  vec3 far = texture2D(uGlowFar, vUv).rgb * 2.0;
  vec3 bounce = c * (near * 0.9 + far * 0.7);
  vec3 halo = near * 0.22 + far * 0.3;

  // ---- windows
  float detail = smoothstep(0.55, 1.6, uDevScale);
  vec2 cell = floor(m / 3.0);
  float h1 = hash12(cell);
  float h2 = hash12(cell + 71.3);
  float h3 = hash12(cell + 19.7);
  // a block of map decides how busy its offices are; within it, cells come on one by one
  float block = noise(m / 90.0);
  float density = 0.18 + 0.42 * block;
  float lit = step(h1, density) * step(h2, uLights * 1.05);
  float flick = 1.0 - 0.6 * step(0.993, h3) * step(0.0, sin(uTime * (2.0 + h3 * 9.0) + h1 * 40.0));
  vec3 winCol = mix(WARM, COOL, step(0.78, h3)) * (0.75 + 0.5 * h2);
  // dark glass in a wall; pale roofs and their grout lines stay dark
  float glass = smoothstep(0.004, -0.045, hp) * smoothstep(0.05, 0.16, busy)
    * (1.0 - smoothstep(0.42, 0.52, l));
  vec3 windowsNear = winCol * glass * lit * flick * 1.5;
  vec2 fcell = floor(m * uDevScale / 1.5);
  float f1 = hash12(fcell + 3.1);
  float f2 = hash12(fcell + 9.7);
  float sparkle = step(f1, density * 0.7) * step(f2, uLights * 1.05);
  vec3 windowsFar = mix(WARM, COOL, step(0.8, f2)) * smoothstep(0.06, 0.18, busy)
    * (sparkle * 0.9 + density * 0.12) * uLights;
  vec3 windows = building * mix(windowsFar, windowsNear, detail);

  // ---- streets: lamp pools on asphalt, road paint, and the cars' lights
  vec3 streets = k.z * SODIUM * mix(0.42, 0.3, detail) * lampPools(m) * uLights;
  streets += k.w * (1.0 - k.z) * WARM * 0.9 * uLights;
  float sat = max(c.r, max(c.g, c.b)) - min(c.r, min(c.g, c.b));
  float car = (1.0 - k.z) * smoothstep(0.1, 0.25, busy)
    * max(smoothstep(0.35, 0.55, sat) * step(c.g, c.r) * step(c.b, c.r), smoothstep(0.8, 0.92, l));
  float road = smoothstep(0.25, 0.6, near.r - near.b);
  streets += car * road * c * 1.4 * uLights;

  vec3 lights = (windows + streets) * cov;
  vec3 night = graded + (bounce + halo) * cov + lights;

  // ---- water: a dark mirror. The lights above it come back as streaks broken by ripples,
  // over a sky that deepens towards the top of the view, with slow moonlit bands and glints.
  // (skipped where there is no water: most of the screen, most of the time)
  if (water > 0.004) {
    vec3 mirror = vec3(0.0);
    for (int i = 1; i <= 4; i++) {
      float fi = float(i);
      vec2 rip = vec2(
        (noise(m * vec2(0.04, 0.5) + vec2(uTime * 0.3, fi * 3.1)) - 0.5) * 0.006 * fi,
        0.0
      );
      vec2 ruv = vUv + vec2(0.0, uReflect * fi) + rip;
      float w = 1.1 - fi * 0.22;
      mirror += (texture2D(uGlowNear, ruv).rgb * 2.2 + texture2D(uGlowFar, ruv).rgb * 1.6) * w;
    }
    mirror *= cov * 0.55;
    float depth = 1.0 - vUv.y;
    vec3 sky = mix(vec3(0.016, 0.032, 0.075), vec3(0.04, 0.066, 0.13), depth) + c * vec3(0.04, 0.06, 0.1);
    float band = smoothstep(0.55, 1.0, noise(m * vec2(0.012, 0.07) + vec2(uTime * 0.04, uTime * 0.015)));
    float glint = step(0.992, hash12(floor(m / 2.0) + floor(uTime * 2.5) * 7.0))
      * smoothstep(0.3, 0.8, band + 0.3);
    vec3 waterNight = sky + mirror * vec3(1.0, 0.8, 0.58)
      + vec3(0.32, 0.42, 0.62) * (band * 0.12 + glint * 0.5) * cov;
    night = mix(night, waterNight, water);
  }

  // ---- film: a soft shoulder for the brightest lights, a vignette, a grain too fine to see
  night = night / (1.0 + max(vec3(0.0), night - 0.75));
  vec2 q = (vUv - 0.5) * vec2(uAspect, 1.0);
  night *= mix(0.68, 1.0, smoothstep(1.05, 0.3, length(q)));
  night += (hash12(gl_FragCoord.xy + fract(uTime) * 61.0) - 0.5) * 0.012;

  gl_FragColor = vec4(mix(day, clamp(night, 0.0, 1.0), uMix), 1.0);
}
`;
