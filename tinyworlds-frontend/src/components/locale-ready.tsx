"use client";

import { useEffect } from "react";
import { activeLocale } from "@/i18n";
import { RELOCALIZING } from "@/i18n/early";
import { useLocale } from "@/hooks/use-i18n";

/**
 * Shows the page again once it speaks the visitor's language. The script in <head> hid it when
 * that language is not the one the HTML was baked in (`localeScript`); hydration renders the baked
 * one first, and React switches right after, which is when this lets it be seen.
 */
export function LocaleReady(): null {
  const locale = useLocale();
  useEffect(() => {
    if (locale === activeLocale()) document.documentElement.classList.remove(RELOCALIZING);
  }, [locale]);
  return null;
}
