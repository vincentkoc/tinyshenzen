"use client";

import { useEffect, useRef, useState } from "react";
import type { NightPref } from "@/lib/prefs";
import type { MapEngine } from "@/map/engine";
import type { NightLook } from "@/map/night";
import { nightTarget } from "@/map/night-mode";
import { WORLD } from "@/worlds";

/** How often "auto" looks at the clock again, ms. */
const CLOCK_MS = 60_000;

/**
 * Night lighting, wired to the setting. The night look (map/night.ts and its worker) is fetched
 * the first time night is wanted, and never for a visitor who leaves it off. Returns whether the
 * night shows (the page dims what sits around the map to match).
 */
export function useNight(
  engine: MapEngine | null,
  pref: NightPref,
  reducedMotion: boolean,
): boolean {
  const [goal, setGoal] = useState(0);
  const [showing, setShowing] = useState(false);
  const look = useRef<NightLook | null>(null);
  const [ready, setReady] = useState(0);

  // the goal: plain on and off, or the visitor's clock (looked at again every minute)
  useEffect(() => {
    const update = () => setGoal(nightTarget(pref));
    update();
    if (pref !== "auto") return;
    const timer = setInterval(update, CLOCK_MS);
    return () => clearInterval(timer);
  }, [pref]);

  // the look: loaded once night is first wanted, kept for the engine's life
  useEffect(() => {
    if (!engine || goal <= 0 || look.current) return;
    let cancelled = false;
    // the code and the world's cover mask arrive together; a mask that fails lights every tile
    Promise.all([import("@/map/night"), WORLD.cover?.().catch(() => null) ?? null])
      .then(([{ NightLook }, cover]) => {
        if (cancelled || look.current) return;
        look.current = new NightLook({ engine, cover, reducedMotion, onShow: setShowing });
        setReady((n) => n + 1);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
    // reducedMotion is passed on below once the look exists
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engine, goal]);

  useEffect(() => {
    return () => {
      look.current?.destroy();
      look.current = null;
      setShowing(false);
    };
  }, [engine]);

  useEffect(() => look.current?.setGoal(goal), [goal, ready]);
  useEffect(() => look.current?.setReducedMotion(reducedMotion), [reducedMotion, ready]);

  return showing;
}
