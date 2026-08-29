import { fileURLToPath, URL } from 'node:url'

import react from '@vitejs/plugin-react'
import svgr from 'vite-plugin-svgr'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'

import { injectThemePrePaintScript } from './src/providers/themeStorage.js'

const here = (p) => fileURLToPath(new URL(p, import.meta.url))

/**
 * Put the theme pre-paint script into `index.html` (ANV-28).
 *
 * The script has to be a **classic, blocking** `<script>` in `<head>`: `type="module"` is
 * deferred by definition, which is precisely the delay that produces the flash of white.
 * A classic script cannot `import`, so the alternative to this plugin is a second copy of
 * the storage key and of the light/dark resolution rule, hand-written in an HTML file where
 * nothing checks it against `ThemeProvider`. When those two disagree the page does not
 * merely flash, it **flips** — visibly, a few hundred milliseconds in.
 *
 * So the script text is built from the same constants `ThemeProvider` imports, and the
 * injection is a one-line call to a pure function that `themeStorage.test.jsx` runs against
 * the real `index.html`. `transformIndexHtml` covers `vite dev` and `vite build` alike.
 */
const themePrePaintPlugin = () => ({
  name: 'anvex-theme-pre-paint',
  // `order: 'pre'` so the marker is gone before any other plugin reads the HTML.
  transformIndexHtml: { order: 'pre', handler: injectThemePrePaintScript },
})

/**
 * The repo root, one level above `frontend/`.
 *
 * CLAUDE.md §2: there is exactly one `.env`, and it lives at the repo root. Vite looks for
 * env files in `root` (i.e. `frontend/`) unless told otherwise, so `envDir` is what stops a
 * second `frontend/.env` from ever being needed. Everything `VITE_`-prefixed in the root
 * file is then exposed on `import.meta.env`; everything else stays server-side.
 */
const REPO_ROOT = here('..')

export default defineConfig(({ mode }) => {
  // Prefix '' loads *every* key, not just VITE_*, so config-time-only values (the dev proxy
  // target, the published port) can live in the same file without being baked into the
  // browser bundle. Only the VITE_* subset reaches `import.meta.env`.
  const env = loadEnv(mode, REPO_ROOT, '')

  return {
    envDir: REPO_ROOT,

    plugins: [
      react(),
      // The CRA components ported in ANV-28..36 use SVGR
      // (`import { ReactComponent as Logo } from './x.svg'`). vite-plugin-svgr's default
      // include is `**/*.svg?react`, so a plain `import url from './x.svg'` keeps
      // resolving to a URL and a ported import gains a `?react` suffix and nothing else.
      svgr(),
      themePrePaintPlugin(),
    ],

    resolve: {
      alias: {
        '@': here('./src'),
        '@assets': here('./src/assets'),
        '@components': here('./src/components'),
        '@features': here('./src/features'),
        '@hooks': here('./src/hooks'),
        '@lib': here('./src/lib'),
        '@providers': here('./src/providers'),
        '@routes': here('./src/routes'),
        '@styles': here('./src/styles'),
        '@test': here('./src/test'),
      },
    },

    // Dependency pre-bundling and the build cache would otherwise land in
    // `frontend/node_modules/.vite` — inside the compose bind mount, i.e. on the Windows
    // host, which is both slow and pointless. Same reasoning as `beat --schedule /tmp/...`
    // in docker-compose.yml: container-local and disposable.
    cacheDir: '/tmp/anvex-vite',

    server: {
      // 0.0.0.0 so the published port reaches the dev server from outside the container.
      host: true,
      port: 5173,
      strictPort: true,
      // Bind mounts on Docker Desktop for Windows do not deliver inotify events, so the
      // watcher has to poll or nothing hot-reloads.
      watch: { usePolling: true, interval: 300 },
      proxy: {
        // A same-origin alternative to the CORS path. Leave VITE_API_BASE_URL empty and the
        // app talks to `/v1/...` on the dev server, which forwards here. In compose the
        // target is the `api` service by name (CLAUDE.md §4: service names are hostnames).
        '/v1': {
          target: env.WEB_DEV_PROXY_TARGET || 'http://api:8000',
          changeOrigin: true,
        },
        '/health': {
          target: env.WEB_DEV_PROXY_TARGET || 'http://api:8000',
          changeOrigin: true,
        },
      },
    },

    preview: {
      host: true,
      port: 4173,
      strictPort: true,
    },

    build: {
      outDir: 'dist',
      sourcemap: mode !== 'production',
    },

    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test/setup.js'],
      include: ['src/**/*.{test,spec}.{js,jsx}'],
      restoreMocks: true,
    },
  }
})
