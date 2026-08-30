<#
Stop the local stack.

Usage: down [--volumes] [--yes] [extra docker compose flags]

  --volumes  also delete the named volumes - the dev database and the object store.
             Destructive, so it asks first unless --yes is given.
  --yes      answer that question with yes.

Both profiles are named so `worker`, `beat` and `web` are in scope whether or not the
shell that started them enabled the profile.

Twin of down.sh - see _common.ps1.
#>

. (Join-Path $PSScriptRoot '_common.ps1')

$arguments = @($args)
$volumes = $false
$yes = $false
$composeArgs = @()
foreach ($argument in $arguments) {
    if ($argument -eq '--volumes') { $volumes = $true }
    elseif ($argument -eq '--yes') { $yes = $true }
    else { $composeArgs += $argument }
}

if ($volumes) {
    if (-not $yes) {
        if (-not (Confirm-Action -Message 'This deletes the database and object-store volumes and everything in them. Continue?')) {
            Stop-WithError 'aborted'
        }
    }
    Write-Step 'Stopping the stack and deleting its volumes'
    Invoke-Compose -Arguments (@('--profile', 'celery', '--profile', 'frontend', 'down', '--volumes') + $composeArgs)
}
else {
    Write-Step 'Stopping the stack, keeping its volumes'
    Invoke-Compose -Arguments (@('--profile', 'celery', '--profile', 'frontend', 'down') + $composeArgs)
}
