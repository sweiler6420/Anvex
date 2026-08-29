import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import postcss from 'postcss'
import tailwindcss from 'tailwindcss'
import { beforeAll, describe, expect, it } from 'vitest'

import config from '../../tailwind.config.js'

// See fonts.test.js: vitest stubs `.css` imports, so the stylesheet is read from disk.
const stylesheet = readFileSync(join(process.cwd(), 'src', 'styles', 'index.css'), 'utf8')

/**
 * The carried-over Tailwind config has to *take effect*, not merely exist — the old repo's
 * did not (v4 installed, v3 config, no postcss config, so nothing was ever generated).
 * These run the real Tailwind v3 PostCSS pipeline over a synthetic content file and assert
 * on the CSS that comes out, so a token that stops being emitted fails here rather than
 * showing up as an unstyled screen in ANV-28..36.
 */

const MARKUP = `
  <div class="font-gothic font-base text-4xl text-sm font-xl font-demi
              text-brand-600 bg-brand-50 border-neutral-800
              dark:bg-neutral-950 shadow shadow-lg shadow-neon-primary shadow-neon-primary-sm
              3xl:grid"></div>
`

let css = ''

beforeAll(async () => {
  const result = await postcss([
    tailwindcss({ ...config, content: [{ raw: MARKUP, extension: 'html' }] }),
  ]).process('@tailwind utilities;', { from: undefined })
  css = result.css
}, 30_000)

const rule = (selector) => {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`))
  return match ? match[1].replace(/\s+/g, ' ').trim() : null
}

describe('tailwind config', () => {
  it('keeps the RTFont/Poppins families', () => {
    expect(rule('.font-gothic')).toContain('RTFont, Poppins, sans-serif')
    expect(rule('.font-base')).toContain('Poppins')
  })

  it('keeps the custom font-size scale rather than the Tailwind default', () => {
    expect(rule('.text-4xl')).toContain('2.442rem')
    expect(rule('.text-sm')).toContain('0.800rem')
  })

  it('keeps the custom font weights', () => {
    expect(rule('.font-xl')).toContain('800')
    expect(rule('.font-demi')).toContain('600')
  })

  it('aliases brand to cyan and neutral to slate', () => {
    // cyan-600 and slate-800 from tailwindcss/colors.
    expect(rule('.text-brand-600')).toMatch(/8 145 178|#0891b2/)
    expect(rule('.border-neutral-800')).toMatch(/30 41 59|#1e293b/)
  })

  it('uses class-based dark mode, not the media query', () => {
    // Tailwind 3.4 compiles `darkMode: 'class'` to `.dark\:x:is(.dark *)`. What matters is
    // that the variant is keyed on an ancestor `.dark` class — the ThemeProvider ported in
    // ANV-29 toggles that class on <html> — and never on the OS preference.
    expect(css).toMatch(/\.dark\\:bg-neutral-950:is\(\.dark \*\)/)
    expect(css).not.toContain('prefers-color-scheme')
  })

  it('keeps the neon box shadows', () => {
    expect(rule('.shadow-neon-primary')).toContain('var(--primary)')
    expect(rule('.shadow-neon-primary-sm')).toContain('var(--primary)')
    expect(rule('.shadow-lg')).toContain('#08f')
  })

  it('keeps the 3xl breakpoint the default scale does not have', () => {
    expect(css).toContain('min-width: 2000px')
  })
})

describe('the app stylesheet', () => {
  it('defines --primary, which the neon shadows interpolate', () => {
    // The old repo never did, so shadow-neon-* rendered as a bare white ring.
    expect(stylesheet).toMatch(/--primary:\s*#06b6d4/)
  })

  it('still declares class-based dark mode base styles', () => {
    expect(stylesheet).toContain('@apply bg-neutral-50 text-neutral-900')
    expect(stylesheet).toContain('@apply bg-neutral-950 text-neutral-300')
  })
})
