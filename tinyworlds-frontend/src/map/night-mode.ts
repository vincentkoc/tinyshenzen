import type { NightPref } from "@/lib/prefs";

/**
 * When the map is lit for night, and how it gets there. Kept apart from the renderer
 * (night.ts) so this stays in the first load and the renderer only loads when it is wanted.
 */

/** Whether the night lights are offered at all (the setting, the N key). Off: always day. */
export const NIGHT_ENABLED = true;

/** How long day turns to night (or back), ms. */
export const NIGHT_FADE_MS = 1600;

/** Local clock, minutes after midnight: dusk deepens over the first span, dawn over the second. */
export const DUSK = { from: 18 * 60, to: 19 * 60 + 15 };
export const DAWN = { from: 5 * 60 + 15, to: 6 * 60 + 30 };

const smooth = (t: number) => {
  const x = Math.min(1, Math.max(0, t));
  return x * x * (3 - 2 * x);
};

/** How dark the visitor's own clock says it is: 0 in daytime, 1 at night, a ramp at dusk and dawn. */
export function clockNight(date: Date): number {
  const m = date.getHours() * 60 + date.getMinutes() + date.getSeconds() / 60;
  if (m >= DUSK.to || m < DAWN.from) return 1;
  if (m >= DUSK.from) return smooth((m - DUSK.from) / (DUSK.to - DUSK.from));
  if (m < DAWN.to) return 1 - smooth((m - DAWN.from) / (DAWN.to - DAWN.from));
  return 0;
}

/** Where the lighting should end up for a setting: 0 day, 1 night. */
export function nightTarget(pref: NightPref, date: Date = new Date()): number {
  if (pref === "on") return 1;
  if (pref === "auto") return clockNight(date);
  return 0;
}

/**
 * One step of the fade from `current` towards `target`: a whole fade takes NIGHT_FADE_MS
 * (none at all with reduced motion), so a half fade takes half as long.
 */
export function stepNight(
  current: number,
  target: number,
  dtMs: number,
  reducedMotion = false,
): number {
  if (reducedMotion || !(dtMs >= 0)) return target;
  const step = dtMs / NIGHT_FADE_MS;
  if (Math.abs(target - current) <= step) return target;
  return current + Math.sign(target - current) * step;
}

/** The fade as drawn: eased, so the change starts and lands softly. */
export const easeNight = smooth;

/** The N key: night on from off or auto, off from on. */
export function toggledNight(pref: NightPref): NightPref {
  return pref === "on" ? "off" : "on";
}

/** Whether the renderer is needed at all: night is on, coming, or still fading out. */
export function nightNeeded(pref: NightPref, mix: number, date: Date = new Date()): boolean {
  return mix > 0 || nightTarget(pref, date) > 0;
}
