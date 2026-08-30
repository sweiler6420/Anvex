#!/usr/bin/env sh
# Throw the development database away and rebuild it from nothing.
#
# Usage: reset-db [--yes]
#
#   --yes  skip the confirmation prompt.
#
# Deletes the `db` container and the named volume behind it, starts a fresh one, migrates
# to head and loads the seed data. Destructive by definition, so it asks first.
#
# Only the *development* database. `db-test` is untouched and needs no reset: it is backed
# by tmpfs with no named volume, so it starts empty on every restart by construction. The
# object store is untouched too - `down --volumes` is the blunter instrument for that.
#
# The volume is named `anvex_pgdata` because docker-compose.yml pins the project name to
# `anvex` (`name: anvex` at the top of the file), so the prefix cannot drift with the
# directory the repo happens to be cloned into.
#
# The migrate and seed steps are spelled out here rather than shelling out to their sibling
# scripts: a nested script's exit code does not propagate on its own, and a reset that
# reports success after a failed migration is worse than no reset at all.
#
# Twin of reset-db.ps1 - see _common.sh.

. "$(dirname -- "$0")/_common.sh"

anvex_yes=0
for anvex_arg in "$@"; do
    case "$anvex_arg" in
        --yes) anvex_yes=1 ;;
        *) die "unknown flag '$anvex_arg' - reset-db takes --yes and nothing else" ;;
    esac
done

if [ "$anvex_yes" -eq 0 ]; then
    if ! confirm 'This destroys the development database and everything in it. Continue?'; then
        die 'aborted'
    fi
fi

say 'Removing the development database and its volume'
run_compose rm --stop --force --volumes db
if [ -n "$(docker volume ls --quiet --filter name=^anvex_pgdata$)" ]; then
    docker volume rm anvex_pgdata
fi

say 'Starting a fresh database'
run_compose up -d --wait db

use_host_database

say 'Applying migrations'
run_uv alembic upgrade head

say 'Loading the seed data'
run_uv python -m scripts.seed_politicians
