#!/usr/bin/env sh
# Run the test suites.
#
# Usage: test [backend|frontend|all] [extra arguments for that runner]
#
#   backend   pytest on the host. Add anything pytest takes: `test backend -k watchlist`.
#             The suite runs with Docker stopped - the container tiers skip themselves -
#             but `up db-test` first if you want the database tier to actually execute.
#   frontend  vitest inside the `web` container, which is started if it is not running.
#             It needs no services at all, so this half is fast and independent.
#   all       both, in that order, and takes no extra arguments (default).
#
# **pytest is invoked as `python -m pytest`, never as the `pytest` console script**: an
# Application Control policy on the dev machine blocks the generated shim with
# `os error 4551`. Do not simplify this back.
#
# Twin of test.ps1 - see _common.sh.

. "$(dirname -- "$0")/_common.sh"

anvex_target=all
if [ "$#" -gt 0 ]; then
    case "$1" in
        -*) : ;;
        *)
            anvex_target=$1
            shift
            ;;
    esac
fi

case "$anvex_target" in
    backend)
        say 'Running the backend suite'
        run_uv python -m pytest "$@"
        ;;
    frontend)
        say 'Running the frontend suite'
        start_web_container
        if [ "$#" -gt 0 ]; then
            run_web npm run test -- "$@"
        else
            run_web npm run test
        fi
        ;;
    all)
        if [ "$#" -gt 0 ]; then
            die 'extra arguments need an explicit target, for example: test backend -k watchlist'
        fi
        say 'Running the backend suite'
        run_uv python -m pytest
        say 'Running the frontend suite'
        start_web_container
        run_web npm run test
        ;;
    *)
        die "unknown target '$anvex_target' - expected backend, frontend or all"
        ;;
esac
