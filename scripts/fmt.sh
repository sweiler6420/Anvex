#!/usr/bin/env sh
# Format the code in place.
#
# Usage: fmt [--check]
#
#   --check  report what would change and exit non-zero instead of writing. This is what
#            `lint backend` already runs, and what CI gates.
#
# Backend only, and that is a statement rather than an omission: the frontend has no
# formatter. Its toolchain is eslint, which is a linter, and adding prettier is a decision
# with a repo-wide diff attached - it needs its own ticket, not a line in a helper script.
# Until then `lint frontend` is the whole frontend story.
#
# `ruff check --fix` runs after the formatter because the two do different jobs: the
# formatter never reorders imports, and the `I` rules in pyproject.toml are what do.
#
# Twin of fmt.ps1 - see _common.sh.

. "$(dirname -- "$0")/_common.sh"

anvex_check=0
if [ "$#" -gt 0 ] && [ "$1" = "--check" ]; then
    anvex_check=1
    shift
fi

if [ "$#" -gt 0 ]; then
    die 'fmt takes --check and nothing else'
fi

if [ "$anvex_check" -eq 1 ]; then
    say 'Checking the backend formatting'
    run_uv ruff format --check .
    run_uv ruff check .
else
    say 'Formatting the backend'
    run_uv ruff format .
    run_uv ruff check --fix .
fi
