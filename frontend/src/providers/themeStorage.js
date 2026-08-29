/**
 * The theme's storage policy and the pre-paint script that shares it (ANV-28).
 *
 * ---------------------------------------------------------------------------------------
 * ## Why this module exists
 *
 * ANV-25 applied the `light`/`dark` class on `<html>` from a `useEffect`, which is correct
 * but late: everything between the first paint and React mounting renders against
 * Tailwind's *unclassed* default, so a dark-mode user sees a white flash on every cold
 * load. The fix is a **blocking classic script in `<head>`** that sets the class before the
 * body is parsed.
 *
 * That script cannot be a module (`type="module"` is deferred by definition, which is
 * exactly the delay being removed), so it cannot `import` anything. The danger is
 * therefore duplication: a second copy of the storage key and of the "stored choice wins,
 * otherwise follow the OS" rule, sitting in `index.html` where nothing type-checks it. If
 * the two ever disagree the page **flips on mount**, which is worse than the flash it was
 * meant to fix.
 *
 * So the key, the class names and the resolution rule live here, once, and the script text
 * is *built* from them (`THEME_PRE_PAINT_SCRIPT`) and injected into `index.html` at
 * transform time by a plugin in `vite.config.js`. `ThemeProvider` imports the same
 * constants and the same `resolveInitialTheme`. There is one spelling of `'theme'` in the
 * repository and `grep` proves it.
 *
 * `themeStorage.test.jsx` closes the remaining gap — that the *rule* the script text
 * expresses still matches `resolveInitialTheme` — by running both over the same matrix of
 * (stored value × OS preference) and asserting they agree on the class.
 *
 * The parallel is deliberate: `features/auth/authStorage.js` is the only module that writes
 * an auth value to browser storage, and this is the only one that touches the theme key.
 */

/** The themes this app knows about. The class names on `<html>` *are* these strings. */
export const THEMES = Object.freeze({ LIGHT: 'light', DARK: 'dark' })

/** Every class the theme is allowed to put on, and therefore must take off. */
export const THEME_CLASSES = Object.freeze(Object.values(THEMES))

/** The `localStorage` key. The **only** place this string is written down. */
export const THEME_STORAGE_KEY = 'theme'

/** The media query consulted when nothing is stored. */
export const PREFERS_DARK_QUERY = '(prefers-color-scheme: dark)'

/** Is `value` one of the themes we know how to render? */
export function isTheme(value) {
  return THEME_CLASSES.includes(value)
}

/**
 * The stored theme, or `null` when there is none, it is unrecognised, or storage is
 * unreachable.
 *
 * An unrecognised value is treated as absent rather than applied: a stale `"midnight"`
 * from a future build must not put an unknown class on `<html>`.
 *
 * Every access is inside the `try` — the `window.localStorage` property read as well as
 * `getItem` — because a browser with site data blocked can refuse either one independently.
 */
export function readStoredTheme(win = globalThis.window) {
  if (!win) return null
  try {
    const stored = win.localStorage.getItem(THEME_STORAGE_KEY)
    return isTheme(stored) ? stored : null
  } catch {
    return null
  }
}

/** Persist, or don't. A browser that refuses to store is not an error worth surfacing. */
export function writeStoredTheme(theme, win = globalThis.window) {
  if (!win) return
  try {
    win.localStorage.setItem(THEME_STORAGE_KEY, theme)
  } catch {
    // Site data blocked, private mode, quota exceeded. The class is already on <html>, so
    // this session is correct; only the *next* one loses the preference.
  }
}

/** `true` when the OS asks for dark. `false` when it doesn't, or won't say. */
export function prefersDarkScheme(win = globalThis.window) {
  if (!win) return false
  try {
    return win.matchMedia?.(PREFERS_DARK_QUERY)?.matches === true
  } catch {
    return false
  }
}

/**
 * A stored choice wins; otherwise follow the OS.
 *
 * Consulted **only** when nothing is stored, so an explicit choice — including an explicit
 * choice of *light* on a dark machine — is never overridden by the OS. This is the one
 * sentence the pre-paint script below has to agree with, and the drift test is written
 * against exactly this function.
 */
export function resolveInitialTheme(win = globalThis.window) {
  return readStoredTheme(win) ?? (prefersDarkScheme(win) ? THEMES.DARK : THEMES.LIGHT)
}

/**
 * The marker `index.html` carries where the pre-paint script goes.
 *
 * A marker rather than a bare `injectTo: 'head-prepend'` so the HTML file *says* what
 * happens to it, and so removing the line is a build failure (`injectThemePrePaintScript`
 * throws) rather than the silent return of the flash.
 */
export const THEME_PRE_PAINT_MARKER = '<!--anvex:theme-pre-paint-->'

/**
 * The body of the blocking script, built from the constants above.
 *
 * Written as a string because it is not part of this bundle: it runs in `<head>` before any
 * module has loaded. Two properties are load-bearing.
 *
 *  - **The key and the class names are interpolated, never retyped.** Renaming
 *    `THEME_STORAGE_KEY` changes this script in the same edit.
 *  - **The rule is `stored ?? (prefers dark ? dark : light)`** — the same one
 *    `resolveInitialTheme` implements, in the same order, with the same treatment of an
 *    unrecognised stored value (fall through to the OS). Anything else and the class the
 *    browser paints differs from the class React settles on, so the page changes colour a
 *    few hundred milliseconds after it appears.
 *
 * Everything is wrapped in one `try` and the fallback is "do nothing": a script in `<head>`
 * that throws stops the parser from reaching the module below it, so a browser refusing
 * `localStorage` must cost a flash, never a blank page.
 */
export const THEME_PRE_PAINT_SCRIPT = `(function(){try{
var c=${JSON.stringify(THEME_CLASSES)};
var s=null;try{s=window.localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)})}catch(e){}
if(c.indexOf(s)===-1){s=null}
if(s===null){var m=window.matchMedia&&window.matchMedia(${JSON.stringify(PREFERS_DARK_QUERY)});
s=m&&m.matches===true?${JSON.stringify(THEMES.DARK)}:${JSON.stringify(THEMES.LIGHT)}}
var r=document.documentElement;r.classList.remove.apply(r.classList,c);r.classList.add(s);
}catch(e){}})();`

/**
 * Put the script into an `index.html`. Called by the `anvex-theme-pre-paint` plugin in
 * `vite.config.js`, and by `themeStorage.test.jsx` against the real file on disk.
 *
 * Throws when the marker is gone. That is the point: the alternative to a loud failure is
 * a build that quietly ships without the fix, and a flash of white is exactly the kind of
 * regression nobody files a bug for.
 */
export function injectThemePrePaintScript(html) {
  if (!html.includes(THEME_PRE_PAINT_MARKER)) {
    throw new Error(
      `index.html is missing ${THEME_PRE_PAINT_MARKER} — the theme pre-paint script has ` +
        'nowhere to go, and without it a dark-mode user gets a flash of white on every load.',
    )
  }
  return html.replace(THEME_PRE_PAINT_MARKER, `<script>${THEME_PRE_PAINT_SCRIPT}</script>`)
}
