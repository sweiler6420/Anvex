#!/usr/bin/env sh
# Bring the database schema up to a revision.
#
# Usage: migrate [revision] [extra alembic flags]
#
# The revision defaults to `head`. `migrate <older-revision>` downgrades nothing - use
# `uv run alembic downgrade` for that, deliberately, because it is not a routine operation.
#
# Runs on the host against the database compose publishes, so `up` (or at least
# `docker compose up -d db`) has to have happened first. See `use_host_database`
# in _common.sh for why the host and the containers reach the same database by
# different names.
#
# Twin of migrate.ps1 - see _common.sh.

. "$(dirname -- "$0")/_common.sh"

anvex_revision=head
if [ "$#" -gt 0 ]; then
    case "$1" in
        -*) : ;;
        *)
            anvex_revision=$1
            shift
            ;;
    esac
fi

use_host_database
say 'Applying migrations'
run_uv alembic upgrade "$anvex_revision" "$@"
