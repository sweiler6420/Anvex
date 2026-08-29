# Anvex frontend

Vite + React 18 + Tailwind. Established by **ANV-23**; the architectural rules live in
[`../CLAUDE.md`](../CLAUDE.md) §5 and this file is the operating manual.

---

## Node is not installed on this machine

That is deliberate, and it is the single most important thing to know here. There is no
`npm` on the host. **Every** `npm` / `vite` / `vitest` / `eslint` command runs inside a
container built from [`Dockerfile`](./Dockerfile).

### Running a script

Two ways, both fine:

```powershell
# 1. Against the running dev container (the stack must be up).
docker compose --profile frontend up -d web
docker compose exec web npm run test

# 2. One-shot, no stack required. Mount the REPO ROOT, not just frontend/ —
#    vite.config.js reads the root .env through `envDir`.
docker run --rm `
  -v "C:\Users\sweil\OneDrive\Documents\Projects\Anvex:/repo" `
  -w /repo/frontend anvex/web:dev npm run test
```

Swap `test` for `build`, `lint`, `preview`, or use `npx <anything>`.

### Rebuilding after a dependency change

`node_modules` is baked into the image at **`/node_modules`** — one level above `/app`,
which is where compose bind-mounts the source. There is no `node_modules` volume, so:

```powershell
# Regenerate the lock (no node on the host, so this runs in a container too).
docker run --rm -v "${PWD}:/app" -w /app node:22-alpine npm install --package-lock-only

# Then rebuild the image.
docker compose --profile frontend up -d --build web
```

Why `/node_modules` and not `/app/node_modules`: the bind mount would hide anything
installed inside `/app`. This is the same problem `backend/Dockerfile` solves by putting
its virtualenv at `/opt/venv`. Node resolves modules by walking *up* from the importing
file, so `/node_modules` is found from `/app/src/**` and from `/repo/frontend/src/**`
alike, and `npm run` puts every ancestor `node_modules/.bin` on `PATH`.

### Scripts

| Script | Does |
| --- | --- |
| `npm run dev` | Vite dev server on :5173 (the container's `CMD`) |
| `npm run build` | production bundle into `frontend/dist/` |
| `npm run preview` | serve `dist/` on :4173 |
| `npm run test` | `vitest run` — the whole suite, once |
| `npm run test:watch` | `vitest` in watch mode |
| `npm run lint` | `eslint .` |

> **Do not put `NODE_ENV=development` in the image or the environment.** Vite honours an
> inherited `NODE_ENV` over its own mode, so it makes `npm run build` bundle
> `react-dom.development` and ship a development build (330 kB instead of 145 kB) with no
> warning. The dev stage had it briefly during ANV-23; the comment in the Dockerfile is
> there so it does not come back.

---

## Configuration: the root `.env`, and only the root `.env`

`vite.config.js` sets `envDir` to the repo root, so the frontend reads
[`../.env`](../.env.example) — the same file the API, the worker and beat read
(CLAUDE.md §2). **There is no `frontend/.env` and there must never be one.** The old app's
`src/app-config.json` is not ported for the same reason.

Under compose the values arrive a second way as well: `env_file: .env` injects them into
the container's process environment, and Vite gives process env priority over the file. So
inside compose the `envDir` lookup is belt-and-braces; outside compose (the `docker run`
one-liner above) it is the only mechanism — which is why that one-liner mounts the repo
root rather than `frontend/`.

Only `VITE_`-prefixed keys reach the browser bundle.

| Key | Used by | Notes |
| --- | --- | --- |
| `VITE_API_BASE_URL` | the browser | Base URL for API calls, no trailing slash. Read once in `src/lib/env.js`. |
| `WEB_DEV_PROXY_TARGET` | the dev server | Where the proxy forwards `/v1` and `/health`. **No `VITE_` prefix on purpose** — it names an in-network host and must not be inlined into the bundle. |
| `WEB_HOST_PORT` | compose | Host side of the `5173` publication. |

### Two ways to reach the API, and both work

- **Cross-origin (the default).** `VITE_API_BASE_URL=http://localhost:8000`; the browser
  calls the API's published port directly and `API_CORS_ORIGINS` allows the dev server's
  origin.
- **Same-origin.** Set `VITE_API_BASE_URL=` (empty). `src/lib/env.js` then produces
  relative URLs, they hit the dev server, and the Vite proxy forwards `/v1` and `/health`
  to `WEB_DEV_PROXY_TARGET`. No CORS involved. Useful when a cookie or a strict CSP makes
  the cross-origin path awkward.

---

## Tailwind: **v3**, deliberately

The config is carried over token-for-token from `AverageInvestorWeb/tailwind.config.js` —
`brand`=cyan, `neutral`=slate, the compressed font-size scale, the custom font weights, the
`RTFont`/Poppins families, the `3xl` breakpoint, class-based dark mode, and the neon box
shadows. `src/styles/tailwind.test.js` runs the real PostCSS pipeline and asserts on the
generated CSS, so a token that stops being emitted fails the suite.

The old repo declared `tailwindcss: ^4.1.15` in `devDependencies`. That is not a reason to
adopt v4 here:

1. **It never worked there.** The repo has no `postcss.config.js` and no
   `@tailwindcss/postcss`, and `src/index.css` used the v3 `@tailwind base/components/
   utilities` directives. Nothing was being generated. There is no working v4 setup to
   preserve — only a v3-shaped config file.
2. **v4 moves configuration into CSS.** `theme.fontSize` / `fontFamily` / `fontWeight`
   overrides, `darkMode: 'class'` and `require('tailwindcss/colors')` all have to be
   re-expressed as `@theme` and `@custom-variant`; `fontWeight` is not even a theme
   namespace in v4. The `@config` escape hatch covers some of that and not all of it.
3. **v4 changes defaults the ported components were authored against.** Default border
   colour becomes `currentColor`, the default ring becomes 1px `currentColor`, `shadow-sm`
   is renamed, `outline-none` becomes `outline-hidden`. ANV-28..36 port ~40 components
   verbatim; adopting v4 in the same ticket as the scaffold would silently restyle every
   one of them, and ANV-23's contract is that the visual design does not drift.

v4 is a real migration with a real test surface. It deserves its own ticket, after the
ports land.

---

## RTFont actually loads (it did not before)

The old `src/index.css` pointed every `@font-face` at `/public/fonts/...`. `public/` is the
*served root* in CRA and in Vite alike, so all ten requests 404'd and `font-gothic` silently
fell back to Poppins. Here they are `/fonts/...`, `src/styles/fonts.test.js` asserts every
declared URL has a file behind it, and the HTTP half was verified against the running dev
server:

```
GET http://localhost:5173/fonts/AllRoundGothic-Bold.ttf
200  content-type: font/ttf  69620 bytes  magic 0x00010000 (TrueType)
```

`--primary`, which the neon shadows interpolate and which the old repo never defined, is
set to `#06b6d4` (cyan-500) in `src/styles/index.css`.

---

## Layout

```
frontend/
├── Dockerfile           dev / build / runtime stages
├── index.html           Vite entry
├── vite.config.js       envDir, aliases, dev proxy, vitest config
├── tailwind.config.js   carried over from AverageInvestorWeb
├── postcss.config.js    tailwind + autoprefixer
├── eslint.config.js     flat config
├── public/              served at /: favicons, logos, fonts/
└── src/
    ├── main.jsx         createRoot
    ├── App.jsx          the ANV-23 shell; ANV-25 replaces it with the router
    ├── assets/          imported assets (SVGR: `import Logo from './x.svg?react'`)
    ├── components/      shared presentational components  ┐
    ├── features/        one folder per domain area         │ see CLAUDE.md §5
    ├── hooks/           cross-feature hooks                │ (empty until ANV-24+)
    ├── lib/             env.js today; api client from ANV-24
    ├── providers/       React context providers            │
    ├── routes/          TanStack Router modules            ┘
    ├── styles/          index.css
    └── test/            setup.js + msw/
```

Path aliases: `@`, `@assets`, `@components`, `@features`, `@hooks`, `@lib`, `@providers`,
`@routes`, `@styles`, `@test` — all defined in `vite.config.js` and therefore honoured by
vitest too.

---

## Tests

`vitest` + `jsdom` + `@testing-library/react` + `msw`. Colocated as `*.test.jsx` beside the
unit under test (CLAUDE.md §6).

- `src/test/setup.js` is the **one** setup file: it installs `@testing-library/jest-dom`,
  starts the MSW server, and does `cleanup()` + `resetHandlers()` after every test.
- `src/test/msw/server.js` holds the **one** `setupServer`. Do not call `setupServer`
  anywhere else.
- `src/test/msw/handlers.js` holds the defaults, plus `errorResponse()` and
  `pageResponse()` — use them so a mock cannot invent an envelope the backend would never
  send.
- `onUnhandledRequest: 'error'`. A request nobody mocked fails the test with its URL in the
  message, rather than escaping to the real network.

Overriding for one test:

```js
import { http } from 'msw'
import { apiUrl } from '@lib/env'
import { pageResponse } from '@test/msw/handlers'
import { server } from '@test/msw/server'

server.use(http.get(apiUrl('/v1/stocks'), () => pageResponse([{ ticker: 'AAPL' }])))
```

Note that vitest stubs `.css` imports (including `?raw`) unless `test.css` is turned on, so
a test that needs the stylesheet's text reads it from disk relative to `process.cwd()`.
