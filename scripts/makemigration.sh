#!/usr/bin/env sh
# Autogenerate a new alembic revision from the models.
#
# Usage: makemigration <message> [extra alembic flags]
#
# Autogenerate compares the live database against `Base.metadata`, so the database has to
# be up *and* already at head - run `migrate` first, or the diff is written against the
# wrong baseline. Read the generated file before committing it: autogenerate does not see
# renames, data migrations or anything a check constraint cannot express.
#
# Runs on the host rather than in the api container on purpose: `alembic.ini` lints every
# generated revision with a ruff post-write hook, and ruff is a dev dependency that the
# runtime image deliberately does not carry.
#
# Twin of makemigration.ps1 - see _common.sh.

. "$(dirname -- "$0")/_common.sh"

if [ "$#" -eq 0 ]; then
    die 'a message is required, for example: makemigration "add holdings table"'
fi

anvex_message=$1
shift

use_host_database
say 'Autogenerating a revision from the models'
run_uv alembic revision --autogenerate -m "$anvex_message" "$@"
