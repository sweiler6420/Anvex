# syntax=docker/dockerfile:1.7
#
# Anvex frontend image. Build context is `frontend/` (see the root docker-compose.yml).
#
# Node is deliberately **not installed on the dev host**, so this image is not an
# optimisation — it is the only way any npm / vite / vitest command runs at all. See
# frontend/README.md for the one-liners.
#
# Stages, mirroring backend/Dockerfile:
#   deps    — `npm ci` from package.json + package-lock.json only, so editing source does
#             not invalidate the (slow) dependency layer. `ci` fails on a drifting lock
#             rather than silently re-resolving, which is `uv sync --frozen`'s argument.
#   dev     — the Vite dev server. Compose bind-mounts ./frontend over /app.
#   build   — `npm run build`, producing /app/dist.
#   runtime — the built static site on a tiny static server; not used by the dev stack, but
#             it is what makes `build` more than a smoke test.
#
# The load-bearing detail: **node_modules lives at /node_modules, not /app/node_modules.**
# The dev compose service bind-mounts ./frontend over /app, which would hide anything
# installed inside it — exactly the problem backend/Dockerfile solves by putting its venv
# at /opt/venv. Node's resolution algorithm walks *up* from the importing file, so
# /node_modules is the one location outside the mount that `/app/src/main.jsx` still finds,
# and `npm run` puts every ancestor `node_modules/.bin` on PATH. No volume is involved, so
# a dependency change is a rebuild and can never be a stale volume.

ARG NODE_VERSION=22


# ------------------------------------------------------------------------------ deps
FROM node:${NODE_VERSION}-alpine AS deps

ENV NPM_CONFIG_FUND=false \
    NPM_CONFIG_AUDIT=false \
    NPM_CONFIG_UPDATE_NOTIFIER=false

WORKDIR /
COPY package.json package-lock.json .npmrc ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --include=dev


# ------------------------------------------------------------------------------- dev
FROM node:${NODE_VERSION}-alpine AS dev

# NODE_ENV is deliberately **not** set here. Vite honours an inherited NODE_ENV over its
# own mode, so `ENV NODE_ENV=development` in this stage made `npm run build` — which is run
# in this same container, there being nowhere else to run it — silently bundle
# `react-dom.development` and ship a 330 kB development build. Vite sets NODE_ENV itself:
# `development` for the dev server, `production` for a build. Leave it alone.
ENV NPM_CONFIG_FUND=false \
    NPM_CONFIG_AUDIT=false \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    VITE_PORT=5173

# The node image already ships a `node` user at uid/gid 1000, matching the backend image's
# `anvex` user and the usual desktop uid.
COPY --from=deps --chown=node:node /node_modules /node_modules
COPY --from=deps --chown=node:node /package.json /package-lock.json /

# Vite writes its dependency cache to `cacheDir` (/tmp/anvex-vite, set in vite.config.js)
# so nothing is written back into the bind-mounted source tree.
RUN mkdir -p /tmp/anvex-vite && chown -R node:node /tmp/anvex-vite

WORKDIR /app
COPY --chown=node:node . /app

USER node

EXPOSE 5173

# busybox wget exists in alpine, but node is already here and cannot be uninstalled by an
# image update — same stdlib-only argument as the backend's urllib healthcheck.
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=6 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:'+(process.env.VITE_PORT||5173)+'/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]

CMD ["npm", "run", "dev"]


# ----------------------------------------------------------------------------- build
FROM node:${NODE_VERSION}-alpine AS build

ENV NPM_CONFIG_FUND=false \
    NPM_CONFIG_AUDIT=false \
    NPM_CONFIG_UPDATE_NOTIFIER=false

COPY --from=deps /node_modules /node_modules
COPY --from=deps /package.json /package-lock.json /

WORKDIR /app
COPY . /app

# `envDir` in vite.config.js points at the repo root, one level above the build context,
# which is not present here. Vite treats a missing env file as "no vars", so the browser
# bundle would inline an empty VITE_API_BASE_URL. Pass it as a build arg instead — a
# production bundle bakes its API URL in at build time either way.
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN npm run build


# --------------------------------------------------------------------------- runtime
FROM node:${NODE_VERSION}-alpine AS runtime

ENV NODE_ENV=production \
    PORT=5173

COPY --from=deps /node_modules /node_modules
WORKDIR /app
COPY --from=build --chown=node:node /app/dist /app/dist

USER node

EXPOSE 5173

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=5 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:'+(process.env.PORT||5173)+'/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]

# Vite's own preview server, so the runtime stage needs no second web server in the image.
# A real deployment puts dist/ behind CloudFront or nginx; this is the local equivalent.
CMD ["npx", "vite", "preview", "--host", "0.0.0.0", "--port", "5173", "--outDir", "dist"]
