#!/usr/bin/env sh
# Shared helpers for the Anvex developer scripts (ANV-37). Sourced, never run directly.
#
# Usage: sourced by the scripts beside it
#
# Every script in this directory has a PowerShell twin of the same name, and the two halves
# are kept behaviourally identical on purpose. `backend/tests/unit/test_repo_scripts.py`
# compares them: the compose services and flags they use, the tools they invoke, and the
# lines they print. Change one half, change the other — two divergent halves of one script
# set is the failure mode this pairing exists to prevent.
#
# Three machine facts are handled here rather than repeated in every script:
#
#   * `uv` is not necessarily on PATH — it is not on the primary dev machine — so it is
#     resolved with a fallback to ~/.local/bin.
#   * A stale VIRTUAL_ENV pointing at an unrelated virtualenv makes `uv run` target the
#     wrong interpreter, so it is cleared before every uv invocation.
#   * `.env` sets POSTGRES_HOST=db, which is a compose service name and resolves only
#     inside the compose network. Anything alembic-shaped runs on the host, so
#     `use_host_database` translates that to the published host port. See its comment.
#
# Both shells take exactly the same command line - `down --volumes --yes` is one string a
# developer can paste into either. That is why the PowerShell halves parse `--flag` by hand
# instead of declaring `[switch]` parameters, which would answer only to `-Volumes`, and read
# `$args` instead of declaring a `param()` block, which would hand `-p` and `-v` to
# PowerShell's own common parameters before the script saw them. Here, `"$@"` just works.
#
# **pytest is always invoked as `python -m pytest`, never as the `pytest` console script.**
# An Application Control policy on the dev machine blocks the generated shim and the run
# dies with `os error 4551`. Do not "simplify" `python -m pytest` back to `pytest`.

set -eu

ANVEX_SCRIPTS_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
ANVEX_REPO_ROOT=$(CDPATH='' cd -- "$ANVEX_SCRIPTS_DIR/.." && pwd)

# ------------------------------------------------------------------------------ output

say() {
    printf '\n== %s\n' "$1"
}

die() {
    printf 'error: %s\n' "$1" >&2
    exit 1
}

confirm() {
    printf '%s [y/N] ' "$1"
    read -r anvex_reply
    case "$anvex_reply" in
        y | Y | yes | YES) return 0 ;;
        *) return 1 ;;
    esac
}

# --------------------------------------------------------------------------------- env

# One value out of the repo-root `.env`. Deliberately not a full dotenv parser: it reads
# the last plain `KEY=value` line, which is all these scripts need and all `.env` contains.
env_value() {
    anvex_value=$(sed -n "s/^$1=//p" "$ANVEX_REPO_ROOT/.env" 2>/dev/null | tail -n 1)
    if [ -z "$anvex_value" ]; then
        anvex_value=$2
    fi
    printf '%s\n' "$anvex_value"
}

# Point host-side database tooling at the published port.
#
# `.env` is the single source of configuration (CLAUDE.md 2), and it names the database `db`
# because that is what the api, worker and beat containers dial. alembic and the seed script
# run on the *host*, where `db` resolves to nothing. So: only when `.env` still says `db` —
# i.e. the developer has not repointed it themselves — and only when the environment does
# not already carry an override, swap in localhost and the port compose publishes. That
# port is read from the same `.env`, so there is still exactly one number to change.
use_host_database() {
    if [ -n "${POSTGRES_HOST:-}" ]; then
        return 0
    fi
    if [ "$(env_value POSTGRES_HOST db)" != "db" ]; then
        return 0
    fi
    POSTGRES_HOST=localhost
    POSTGRES_PORT=$(env_value POSTGRES_HOST_PORT 5442)
    export POSTGRES_HOST
    export POSTGRES_PORT
}

# ---------------------------------------------------------------------------------- uv

ANVEX_UV=""
if command -v uv > /dev/null 2>&1; then
    ANVEX_UV=uv
elif [ -x "$HOME/.local/bin/uv" ]; then
    ANVEX_UV="$HOME/.local/bin/uv"
elif [ -x "$HOME/.local/bin/uv.exe" ]; then
    ANVEX_UV="$HOME/.local/bin/uv.exe"
fi

# Everything here is `uv run <what you passed>`, from `backend/`, with the environment
# cleaned first. Callers name the tool - `run_uv ruff check .` - and never repeat `run`.
run_uv() {
    if [ -z "$ANVEX_UV" ]; then
        die 'uv was not found on PATH or in ~/.local/bin - install it from https://docs.astral.sh/uv/'
    fi
    unset VIRTUAL_ENV
    (cd "$ANVEX_REPO_ROOT/backend" && "$ANVEX_UV" run "$@")
}

# ----------------------------------------------------------------------------- compose

run_compose() {
    (cd "$ANVEX_REPO_ROOT" && docker compose "$@")
}

# Every frontend command runs inside the `web` container: there is no node on the dev host.
# `-T` disables TTY allocation and is **required** — without it these hang forever in a
# non-interactive shell, which is every script, every CI job and every agent session.
run_web() {
    run_compose --profile frontend exec -T web "$@"
}

# vitest and eslint need no api, no database, no broker and no object store, so `--no-deps`
# keeps a lint or a test run from dragging the whole stack up behind it. Idempotent: a
# container that is already running is left alone.
start_web_container() {
    run_compose --profile frontend up -d --no-deps web
}
