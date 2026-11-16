/**
 * The languages the interface speaks. Which one a visitor gets is decided in their browser, at
 * run time: their own choice in the settings if they made one, otherwise the first language
 * their browser asks for that we have.
 */
export const LOCALES = ["en-US", "pt-BR", "es-ES", "zh-CN"] as const;
export type Locale = (typeof LOCALES)[number];

/** The setting: a language, or "follow the browser". */
export type LocalePref = "auto" | Locale;

export const DEFAULT_LOCALE: Locale = "en-US";

/** Each language under its own name, for the list where one is chosen. */
export const LOCALE_NAMES: Record<Locale, string> = {
  "en-US": "English",
  "pt-BR": "Português",
  "es-ES": "Español",
  "zh-CN": "简体中文",
};

/**
 * Any variety of a language gets the one variety we have: pt-PT reads pt-BR, es-MX reads es-ES,
 * and Chinese in any script or region (zh-TW, zh-HK, zh-Hant) reads Simplified Chinese, the only
 * Chinese there is. The script that settles the language before the page paints reads it too.
 */
export const BY_LANGUAGE: Readonly<Record<string, Locale>> = {
  en: "en-US",
  pt: "pt-BR",
  es: "es-ES",
  zh: "zh-CN",
};

export const isLocale = (value: unknown): value is Locale =>
  typeof value === "string" && (LOCALES as readonly string[]).includes(value);

/**
 * The first of the browser's languages (`navigator.languages`, most wanted first) that we speak,
 * matched on the primary subtag; `fallback` when none is: the language the page was baked in, so
 * a visitor we cannot serve in their own keeps what they first saw. English unless said.
 */
export function resolveLocale(
  languages: readonly string[] | null | undefined,
  fallback: Locale = DEFAULT_LOCALE,
): Locale {
  for (const tag of languages ?? []) {
    if (typeof tag !== "string") continue;
    const match = BY_LANGUAGE[tag.trim().toLowerCase().split(/[-_]/)[0]];
    if (match) return match;
  }
  return fallback;
}

/** "pt-BR" as Open Graph writes it: "pt_BR". */
export const ogLocale = (locale: Locale): string => locale.replace("-", "_");
