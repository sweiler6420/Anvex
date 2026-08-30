#!/usr/bin/env sh
# Boot the whole stack and prove it works end to end.
#
# Usage: smoke [--clean --yes] [--live-vendor] [--skip-frontend] [--steps]
#
# The one command that runs the real containers, the real migrations, the real Postgres,
# the real broker and the real built bundle in the order a developer meets them — twenty
# steps from `docker compose up` to loading `/research` in a DOM with nothing but a refresh
# token. Every other suite in this repository proves a component with its neighbours
# replaced by fixtures; this proves they fit together. `docs/smoke.md` is the checklist and
# `backend/scripts/smoke.py` is the implementation.
#
# Flags:
#   --clean          destroy the stack and its named volumes first, so the boot is from
#                    nothing. Asks before it does — `--yes` skips the question.
#   --live-vendor    spend ONE real AlphaVantage call. OFF BY DEFAULT: the free tier is
#                    about 25 calls a day and they are the owner's. Without it the vendor
#                    is stubbed at the client-base transport seam and the output says so.
#   --skip-frontend  stop after the API and the worker; skips the build and the cold load.
#   --steps          print the step ids and exit, running nothing.
#
# A wrapper and nothing more, like `seed`: backend-only entry points live in
# `backend/scripts/`, and this directory only ever wraps them. What it adds is the two
# machine facts every script here handles — resolving uv with a cleared VIRTUAL_ENV, and
# retargeting the host-side database tooling, because alembic and the seed run on the host
# where `POSTGRES_HOST=db` resolves to nothing.
#
# Twin of smoke.ps1 — see _common.sh.

. "$(dirname -- "$0")/_common.sh"

use_host_database
say 'Running the end-to-end smoke'
run_uv python -m scripts.smoke "$@"
