import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { ThemeProvider } from './ThemeProvider'
import {
  injectThemePrePaintScript,
  PREFERS_DARK_QUERY,
  resolveInitialTheme,
  THEME_CLASSES,
  THEME_PRE_PAINT_MARKER,
  THEME_PRE_PAINT_SCRIPT,
  THEME_STORAGE_KEY,
} from './themeStorage'

/**
 * The pre-paint script, and the one thing that can go wrong with it (ANV-28).
 *
 * ANV-25 applied the theme class from an effect, so everything painted before React mounts
 * uses Tailwind's unclassed light default — a flash of white on every cold load for a
 * dark-mode user. The fix is a blocking classic script in `<head>`, and a classic script
 * cannot import a module, so the *rule* has to exist in two forms: `resolveInitialTheme`,
 * which `ThemeProvider` calls, and the text of `THEME_PRE_PAINT_SCRIPT`.
 *
 * **Drift between those two is worse than the bug they fix.** A flash is one frame of the
 * wrong colour before the right one; a disagreement is a page that settles on one colour
 * and then *changes* a few hundred milliseconds later, on every single load.
 *
 * Two mechanisms keep them together, and both are tested here:
 *
 *  1. **The key, the class names and the media query are interpolated into the script from
 *     the same constants `ThemeProvider` imports**, so a rename cannot reach one and miss
 *     the other. There is one `'theme'` in the repository.
 *  2. **The rule is checked behaviourally, not by reading it.** The matrix below runs the
 *     real script text and the real provider over every combination of (stored value × OS
 *     preference) and asserts they put the *same* class on `<html>`. That is what fails
 *     when someone edits one and not the other — including an edit that keeps the key and
 *     changes only the fallback, which is the drift the key check cannot see.
 *
 * The `light`/`dark` class is the entire product of both, so comparing it is comparing the
 * only thing either one is for.
 */

const root = () => document.documentElement

/** Every input the two implementations have to agree on. */
const CASES = [
  { name: 'nothing stored, OS wants dark', stored: null, prefersDark: true },
  { name: 'nothing stored, OS wants light', stored: null, prefersDark: false },
  { name: 'nothing stored, OS will not say', stored: null, prefersDark: undefined },
  { name: 'stored dark, OS wants light', stored: 'dark', prefersDark: false },
  { name: 'stored light, OS wants dark', stored: 'light', prefersDark: true },
  { name: 'stored dark, OS wants dark', stored: 'dark', prefersDark: true },
  { name: 'stored light, OS wants light', stored: 'light', prefersDark: false },
  { name: 'stored garbage, OS wants dark', stored: 'midnight', prefersDark: true },
  { name: 'stored garbage, OS wants light', stored: 'midnight', prefersDark: false },
  { name: 'stored empty string, OS wants dark', stored: '', prefersDark: true },
]

let originalMatchMedia

beforeEach(() => {
  originalMatchMedia = window.matchMedia
  window.localStorage.clear()
  root().className = ''
})

afterEach(() => {
  window.matchMedia = originalMatchMedia
  window.localStorage.clear()
  root().className = ''
})

/**
 * Put the browser into one of the states above.
 *
 * `prefersDark: undefined` removes `matchMedia` entirely — a real state (an old embedded
 * webview, a headless environment), and the one where an unguarded `window.matchMedia(...)`
 * is a `TypeError`. Both implementations have to survive it, and "survive" means *light*,
 * not a blank page.
 */
function givenBrowser({ stored, prefersDark }) {
  if (stored !== null) window.localStorage.setItem(THEME_STORAGE_KEY, stored)

  if (prefersDark === undefined) {
    window.matchMedia = undefined
  } else {
    window.matchMedia = (query) => ({
      matches: query === PREFERS_DARK_QUERY && prefersDark,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    })
  }
}

/** The theme classes on `<html>`, as an array so "exactly one" is assertable. */
const themeClasses = () => THEME_CLASSES.filter((name) => root().classList.contains(name))

/** What the *script* leaves on `<html>`, run the way a browser runs it. */
function classFromPrePaintScript() {
  // `new Function` is the closest thing to a classic <script>: no module scope, `window`
  // and `document` resolved as globals, exactly as in <head>.
  new Function(THEME_PRE_PAINT_SCRIPT)()
  return themeClasses()
}

/** What *ThemeProvider* leaves on `<html>` once it has mounted. */
function classFromProvider() {
  render(
    <ThemeProvider>
      <span />
    </ThemeProvider>,
  )
  return themeClasses()
}

describe('the pre-paint script and ThemeProvider cannot drift apart', () => {
  it.each(CASES)('agrees on the theme when $name', ({ stored, prefersDark }) => {
    givenBrowser({ stored, prefersDark })

    const fromScript = classFromPrePaintScript()
    root().className = ''
    const fromProvider = classFromProvider()

    // "Exactly one" matters as much as "the same": a script that added `dark` without
    // removing `light` would agree on the presence of `dark` and still leave Tailwind
    // reading two conflicting roots.
    expect(fromScript).toHaveLength(1)
    expect(fromScript).toEqual(fromProvider)
  })

  it.each(CASES)('matches resolveInitialTheme directly when $name', ({ stored, prefersDark }) => {
    // The same comparison against the *function* rather than the rendered provider, so a
    // failure says which of the two moved.
    givenBrowser({ stored, prefersDark })

    expect(classFromPrePaintScript()).toEqual([resolveInitialTheme()])
  })

  it('reads the one exported key and no other', () => {
    // Belt to the matrix's braces: a rename of THEME_STORAGE_KEY has to reach the script,
    // and it does because the script interpolates it rather than spelling it out.
    expect(THEME_PRE_PAINT_SCRIPT).toContain(JSON.stringify(THEME_STORAGE_KEY))

    givenBrowser({ stored: null, prefersDark: false })
    window.localStorage.setItem(THEME_STORAGE_KEY, 'dark')
    expect(classFromPrePaintScript()).toEqual(['dark'])
  })

  it('does nothing rather than throwing when storage is refused', () => {
    // A script in <head> that throws stops the parser reaching the module below it, so the
    // cost of a browser with site data blocked has to be a flash, never a blank page.
    givenBrowser({ stored: null, prefersDark: true })
    const getItem = Storage.prototype.getItem
    Storage.prototype.getItem = () => {
      throw new Error('site data blocked')
    }

    try {
      expect(() => classFromPrePaintScript()).not.toThrow()
      // It still follows the OS, because only the *stored* half failed.
      expect(themeClasses()).toEqual(['dark'])
    } finally {
      Storage.prototype.getItem = getItem
    }
  })
})

describe('injection into index.html', () => {
  // vitest's `import.meta.url` is an http: URL, so the file is read relative to the working
  // directory (CLAUDE.md §6) — which is `frontend/`, where index.html lives.
  const indexHtml = () => readFileSync(resolve(process.cwd(), 'index.html'), 'utf8')

  it('finds its marker in the real index.html', () => {
    expect(indexHtml()).toContain(THEME_PRE_PAINT_MARKER)
  })

  it('produces a blocking classic script in <head>, ahead of the app module', () => {
    const html = injectThemePrePaintScript(indexHtml())

    expect(html).toContain(`<script>${THEME_PRE_PAINT_SCRIPT}</script>`)
    expect(html).not.toContain(THEME_PRE_PAINT_MARKER)
    // Not `type="module"`: a module script is deferred by definition, which is the delay
    // being removed.
    expect(html).not.toContain(`<script type="module">${THEME_PRE_PAINT_SCRIPT}`)
    expect(html.indexOf(THEME_PRE_PAINT_SCRIPT)).toBeLessThan(html.indexOf('</head>'))
    expect(html.indexOf(THEME_PRE_PAINT_SCRIPT)).toBeLessThan(html.indexOf('/src/main.jsx'))
  })

  it('fails loudly when the marker is deleted', () => {
    // The alternative to throwing is a build that quietly ships without the fix, and a
    // flash of white is exactly the kind of regression nobody files a bug for.
    expect(() => injectThemePrePaintScript('<html><head></head></html>')).toThrow(
      /theme-pre-paint/,
    )
  })
})
