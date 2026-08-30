<#
Check the code without changing it.

Usage: lint [backend|frontend|all]

  backend   ruff's lint rules and then its formatter in check mode. The formatter is
            part of lint on purpose: a diff nobody ran `fmt` on should fail here rather
            than in review, and CI gates the same two commands.
  frontend  eslint inside the `web` container, which is started if it is not running.
  all       both (default).

Nothing here writes to a file. `fmt` is the half that does.

Twin of lint.sh - see _common.ps1.
#>

. (Join-Path $PSScriptRoot '_common.ps1')

$arguments = @($args)
$target = 'all'
if ($arguments.Count -gt 0) {
    $target = $arguments[0]
}

switch ($target) {
    'backend' {
        Write-Step 'Linting the backend'
        Invoke-Uv -Arguments @('ruff', 'check', '.')
        Invoke-Uv -Arguments @('ruff', 'format', '--check', '.')
    }
    'frontend' {
        Write-Step 'Linting the frontend'
        Start-WebContainer
        Invoke-Web -Arguments @('npm', 'run', 'lint')
    }
    'all' {
        Write-Step 'Linting the backend'
        Invoke-Uv -Arguments @('ruff', 'check', '.')
        Invoke-Uv -Arguments @('ruff', 'format', '--check', '.')
        Write-Step 'Linting the frontend'
        Start-WebContainer
        Invoke-Web -Arguments @('npm', 'run', 'lint')
    }
    default {
        Stop-WithError "unknown target '$target' - expected backend, frontend or all"
    }
}
