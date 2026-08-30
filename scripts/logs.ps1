<#
Follow the container logs.

Usage: logs [service ...] [extra docker compose logs flags]

With no service named it follows everything the project is running. Both profiles are
enabled so `worker`, `beat` and `web` can be named without enabling a profile by hand.

Twin of logs.sh - see _common.ps1.
#>

. (Join-Path $PSScriptRoot '_common.ps1')

$arguments = @($args)

Write-Step 'Following the container output - press Ctrl-C to stop'
Invoke-Compose -Arguments (@('--profile', 'celery', '--profile', 'frontend', 'logs', '--follow', '--tail', '100') + $arguments)
