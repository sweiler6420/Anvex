#!/usr/bin/env sh
# Start the local stack.
#
# Usage: up [core|celery|frontend|db-test|all] [extra docker compose flags]
#
# Targets:
#   core      db, redis, minio, minio-init and api - the working dev stack (the default)
#   celery    the worker and the scheduler, behind the `celery` compose profile
#   frontend  the Vite dev server on :5173, behind the `frontend` profile
#   db-test   the throwaway Postgres the backend suite dials on :5433
#   all       every service above, in one go
#
# Anything after the target is handed to `docker compose up`, so `up core --build` works.
#
# Twin of up.ps1 - see _common.sh.

. "$(dirname -- "$0")/_common.sh"

anvex_target=core
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
    core)
        say 'Starting the working development stack'
        run_compose up -d "$@" db redis minio minio-init api
        ;;
    celery)
        say 'Starting the Celery worker and scheduler'
        run_compose --profile celery up -d "$@" worker beat
        ;;
    frontend)
        say 'Starting the Vite development server'
        run_compose --profile frontend up -d "$@" web
        ;;
    db-test)
        say 'Starting the throwaway Postgres the backend suite dials'
        run_compose up -d "$@" db-test
        ;;
    all)
        say 'Starting every service, including both profiles'
        run_compose --profile celery --profile frontend up -d "$@"
        ;;
    *)
        die "unknown target '$anvex_target' - expected core, celery, frontend, db-test or all"
        ;;
esac
