<#
Start the local stack.

Usage: up [core|celery|frontend|db-test|all] [extra docker compose flags]

Targets:
  core      db, redis, minio, minio-init and api - the working dev stack (the default)
  celery    the worker and the scheduler, behind the `celery` compose profile
  frontend  the Vite dev server on :5173, behind the `frontend` profile
  db-test   the throwaway Postgres the backend suite dials on :5433
  all       every service above, in one go

Anything after the target is handed to `docker compose up`, so `up core --build` works.

Twin of up.sh - see _common.ps1.
#>

. (Join-Path $PSScriptRoot '_common.ps1')

$arguments = @($args)
$target = 'core'
$composeArgs = $arguments
if ($arguments.Count -gt 0 -and -not $arguments[0].StartsWith('-')) {
    $target = $arguments[0]
    $composeArgs = @($arguments | Select-Object -Skip 1)
}

switch ($target) {
    'core' {
        Write-Step 'Starting the working development stack'
        Invoke-Compose -Arguments (@('up', '-d') + $composeArgs + @('db', 'redis', 'minio', 'minio-init', 'api'))
    }
    'celery' {
        Write-Step 'Starting the Celery worker and scheduler'
        Invoke-Compose -Arguments (@('--profile', 'celery', 'up', '-d') + $composeArgs + @('worker', 'beat'))
    }
    'frontend' {
        Write-Step 'Starting the Vite development server'
        Invoke-Compose -Arguments (@('--profile', 'frontend', 'up', '-d') + $composeArgs + @('web'))
    }
    'db-test' {
        Write-Step 'Starting the throwaway Postgres the backend suite dials'
        Invoke-Compose -Arguments (@('up', '-d') + $composeArgs + @('db-test'))
    }
    'all' {
        Write-Step 'Starting every service, including both profiles'
        Invoke-Compose -Arguments (@('--profile', 'celery', '--profile', 'frontend', 'up', '-d') + $composeArgs)
    }
    default {
        Stop-WithError "unknown target '$target' - expected core, celery, frontend, db-test or all"
    }
}
