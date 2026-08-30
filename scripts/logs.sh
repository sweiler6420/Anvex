#!/usr/bin/env sh
# Follow the container logs.
#
# Usage: logs [service ...] [extra docker compose logs flags]
#
# With no service named it follows everything the project is running. Both profiles are
# enabled so `worker`, `beat` and `web` can be named without enabling a profile by hand.
#
# Twin of logs.ps1 - see _common.sh.

. "$(dirname -- "$0")/_common.sh"

say 'Following the container output - press Ctrl-C to stop'
run_compose --profile celery --profile frontend logs --follow --tail 100 "$@"
