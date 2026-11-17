"use client";

import { useEffect, useRef, useState, type RefObject } from "react";
import type { NightPref } from "@/lib/prefs";
import type { MapEngine } from "@/map/engine";
import type { NightLayer } from "@/map/night";
import { nightTarget } from "@/map/night-mode";

/** How often "auto" looks at the clock again, ms. */
const CLOCK_MS = 60_000;

export interface NightState {
  /** the night picture covers the tiles (the tiles canvas can be hidden) */
  showing: boolean;
  /** no WebGL here: night is a plain tint instead */
  flat: boolean;
}

/**
 * Night lighting, wired to the setting. The renderer (map/night.ts and its shaders) is fetched
 * the first time night is wanted, and never for a visitor who leaves it off.
 */
export function useNight(
  engine: MapEngine | null,
  pref: NightPref,
  source: RefObject<HTMLCanvasElement | null>,
  target: RefObject<HTMLCanvasElement | null>,
  reducedMotion: boolean,
): NightState {
  const [goal, setGoal] = useState(0);
  const [showing, setShowing] = useState(false);
  const [flat, setFlat] = useState(false);
  const layer = useRef<NightLayer | null>(null);
  const [ready, setReady] = useState(0);

  // the goal: plain on and off, or the visitor's clock (looked at again every minute)
  useEffect(() => {
    const update = () => setGoal(nightTarget(pref));
    update();
    if (pref !== "auto") return;
    const timer = setInterval(update, CLOCK_MS);
    return () => clearInterval(timer);
  }, [pref]);

  // the renderer: loaded once night is first wanted, kept for the engine's life
  useEffect(() => {
    if (!engine || goal <= 0 || layer.current || flat) return;
    const src = source.current;
    const out = target.current;
    if (!src || !out) return;
    let cancelled = false;
    import("@/map/night")
      .then(({ NightLayer }) => {
        if (cancelled) return;
        const made = NightLayer.create({
          engine,
          source: src,
          target: out,
          reducedMotion,
          onShow: setShowing,
        });
        if (!made) {
          setFlat(true);
          return;
        }
        layer.current = made;
        setReady((n) => n + 1);
      })
      .catch(() => {
        if (!cancelled) setFlat(true);
      });
    return () => {
      cancelled = true;
    };
    // reducedMotion is passed on below once the layer exists
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engine, goal, flat, source, target]);

  useEffect(() => {
    return () => {
      layer.current?.destroy();
      layer.current = null;
      setShowing(false);
    };
  }, [engine]);

  useEffect(() => layer.current?.setGoal(goal), [goal, ready]);
  useEffect(() => layer.current?.setReducedMotion(reducedMotion), [reducedMotion, ready]);

  return { showing, flat: flat && goal > 0 };
}
