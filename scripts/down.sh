#!/usr/bin/env sh
# Stop the local stack.
#
# Usage: down [--volumes] [--yes] [extra docker compose flags]
#
#   --volumes  also delete the named volumes - the dev database and the object store.
#              Destructive, so it asks first unless --yes is given.
#   --yes      answer that question with yes.
#
# Both profiles are named so `worker`, `beat` and `web` are in scope whether or not the
# shell that started them enabled the profile.
#
# Twin of down.ps1 - see _common.sh.

. "$(dirname -- "$0")/_common.sh"

anvex_volumes=0
anvex_yes=0
for anvex_arg in "$@"; do
    shift
    case "$anvex_arg" in
        --volumes) anvex_volumes=1 ;;
        --yes) anvex_yes=1 ;;
        *) set -- "$@" "$anvex_arg" ;;
    esac
done

if [ "$anvex_volumes" -eq 1 ]; then
    if [ "$anvex_yes" -eq 0 ]; then
        if ! confirm 'This deletes the database and object-store volumes and everything in them. Continue?'; then
            die 'aborted'
        fi
    fi
    say 'Stopping the stack and deleting its volumes'
    run_compose --profile celery --profile frontend down --volumes "$@"
else
    say 'Stopping the stack, keeping its volumes'
    run_compose --profile celery --profile frontend down "$@"
fi
