import { BY_LANGUAGE, LOCALES, type Locale } from "./locale";

/**
 * The class on <html> while the page shows a language other than the visitor's: the HTML is baked
 * in the site's home language, and React only switches once it has hydrated. The stylesheet keeps
 * the page hidden meanwhile (globals.css), so a visitor never sees it change language under them.
 */
export const RELOCALIZING = "relocalizing";

/** How long the page stays hidden at most, should the script never get to show it. */
export const RELOCALIZE_TIMEOUT_MS = 2500;

/**
 * The script that runs in <head>, before the first paint, and settles the visitor's language the
 * way `localeStore` will once React runs: the language chosen in the settings, else the first of
 * the browser's that we speak, else the home language. It sets <html lang> (so the right CJK
 * face and hyphenation apply from the start) and, when that is not the home language, hides the
 * page until React has switched (`LocaleReady` shows it again). A static export has no server to
 * negotiate with; this is the browser's half of it. It must stay small, old-browser JavaScript.
 */
export function localeScript(prefsKey: string, home: Locale): string {
  const byLanguage = JSON.stringify(BY_LANGUAGE);
  return (
    `(function(){try{var d=document.documentElement,h=${JSON.stringify(home)},l="auto",` +
    `k=${JSON.stringify(LOCALES)},m=${byLanguage};` +
    `try{var p=JSON.parse(localStorage.getItem(${JSON.stringify(prefsKey)})||"{}");` +
    `if(p&&k.indexOf(p.lang)>=0)l=p.lang}catch(e){}` +
    `if(l==="auto"){l=h;var n=navigator.languages&&navigator.languages.length?navigator.languages:[navigator.language];` +
    `for(var i=0;i<n.length;i++){var t=m[String(n[i]||"").replace(/^\\s+|\\s+$/g,"").toLowerCase().split(/[-_]/)[0]];` +
    `if(t){l=t;break}}}` +
    `d.lang=l;if(l!==h){d.classList.add(${JSON.stringify(RELOCALIZING)});` +
    `setTimeout(function(){d.classList.remove(${JSON.stringify(RELOCALIZING)})},${RELOCALIZE_TIMEOUT_MS})}` +
    `}catch(e){}})()`
  );
}
