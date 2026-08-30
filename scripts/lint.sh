#!/usr/bin/env sh
# Check the code without changing it.
#
# Usage: lint [backend|frontend|all]
#
#   backend   ruff's lint rules and then its formatter in check mode. The formatter is
#             part of lint on purpose: a diff nobody ran `fmt` on should fail here rather
#             than in review, and CI gates the same two commands.
#   frontend  eslint inside the `web` container, which is started if it is not running.
#   all       both (default).
#
# Nothing here writes to a file. `fmt` is the half that does.
#
# Twin of lint.ps1 - see _common.sh.

. "$(dirname -- "$0")/_common.sh"

anvex_target=${1:-all}

case "$anvex_target" in
    backend)
        say 'Linting the backend'
        run_uv ruff check .
        run_uv ruff format --check .
        ;;
    frontend)
        say 'Linting the frontend'
        start_web_container
        run_web npm run lint
        ;;
    all)
        say 'Linting the backend'
        run_uv ruff check .
        run_uv ruff format --check .
        say 'Linting the frontend'
        start_web_container
        run_web npm run lint
        ;;
    *)
        die "unknown target '$anvex_target' - expected backend, frontend or all"
        ;;
esac
