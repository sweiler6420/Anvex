import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

// Read from disk rather than importing. Vite serves test modules over http, so
// `import.meta.url` is not a file: URL inside vitest, and vitest stubs `.css` imports
// (`?raw` included) unless `test.css` is turned on. `process.cwd()` is the project root
// because every documented way of running this suite starts in `frontend/` — the first
// test below asserts that rather than trusting it.
const PROJECT_ROOT = process.cwd()
const CSS = readFileSync(join(PROJECT_ROOT, 'src', 'styles', 'index.css'), 'utf8')

/**
 * RTFont has to actually load. In the old repo it did not: every `@font-face` pointed at
 * `/public/fonts/...`, but `public/` *is* the served root, so all ten requests 404'd and
 * `font-gothic` silently fell back to Poppins. jsdom does not fetch fonts, so what a unit
 * test can prove is that each declared URL resolves to a file that will be served — the
 * HTTP half is verified against the running dev server (see frontend/README.md).
 */

const faces = [...CSS.matchAll(/@font-face\s*\{([^}]*)\}/g)].map(([, block]) => ({
  family: block.match(/font-family:\s*'([^']+)'/)?.[1],
  url: block.match(/url\('([^']+)'\)/)?.[1],
  weight: block.match(/font-weight:\s*(\d+)/)?.[1],
  style: block.match(/font-style:\s*(\w+)/)?.[1],
}))

describe('RTFont', () => {
  it('runs from the project root, so the public/ lookups below mean something', () => {
    expect(existsSync(join(PROJECT_ROOT, 'package.json'))).toBe(true)
    expect(existsSync(join(PROJECT_ROOT, 'public', 'fonts'))).toBe(true)
  })

  it('declares all ten faces carried over from the old app', () => {
    expect(faces).toHaveLength(10)
    expect(new Set(faces.map((f) => f.family))).toEqual(new Set(['RTFont']))
    expect(new Set(faces.map((f) => `${f.weight}/${f.style}`))).toEqual(
      new Set([
        '400/normal',
        '400/italic',
        '500/normal',
        '500/italic',
        '600/normal',
        '600/italic',
        '700/normal',
        '700/italic',
        '800/normal',
        '800/italic',
      ]),
    )
  })

  it('serves each face from /fonts/, not /public/fonts/', () => {
    for (const face of faces) {
      expect(face.url).toMatch(/^\/fonts\/AllRoundGothic-[\w-]+\.ttf$/)
    }
  })

  it('has a real file behind every declared URL', () => {
    for (const face of faces) {
      const onDisk = join(PROJECT_ROOT, 'public', face.url)
      expect(existsSync(onDisk), `${face.url} is declared but missing from public/`).toBe(true)
    }
  })
})
