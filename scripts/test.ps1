<#
Run the test suites.

Usage: test [backend|frontend|all] [extra arguments for that runner]

  backend   pytest on the host. Add anything pytest takes: `test backend -k watchlist`.
            The suite runs with Docker stopped - the container tiers skip themselves -
            but `up db-test` first if you want the database tier to actually execute.
  frontend  vitest inside the `web` container, which is started if it is not running.
            It needs no services at all, so this half is fast and independent.
  all       both, in that order, and takes no extra arguments (default).

**pytest is invoked as `python -m pytest`, never as the `pytest` console script**: an
Application Control policy on the dev machine blocks the generated shim with
`os error 4551`. Do not simplify this back.

Twin of test.sh - see _common.ps1.
#>

. (Join-Path $PSScriptRoot '_common.ps1')

$arguments = @($args)
$target = 'all'
$runnerArgs = $arguments
if ($arguments.Count -gt 0 -and -not $arguments[0].StartsWith('-')) {
    $target = $arguments[0]
    $runnerArgs = @($arguments | Select-Object -Skip 1)
}

switch ($target) {
    'backend' {
        Write-Step 'Running the backend suite'
        Invoke-Uv -Arguments (@('python', '-m', 'pytest') + $runnerArgs)
    }
    'frontend' {
        Write-Step 'Running the frontend suite'
        Start-WebContainer
        if ($runnerArgs.Count -gt 0) {
            Invoke-Web -Arguments (@('npm', 'run', 'test', '--') + $runnerArgs)
        }
        else {
            Invoke-Web -Arguments @('npm', 'run', 'test')
        }
    }
    'all' {
        if ($runnerArgs.Count -gt 0) {
            Stop-WithError 'extra arguments need an explicit target, for example: test backend -k watchlist'
        }
        Write-Step 'Running the backend suite'
        Invoke-Uv -Arguments @('python', '-m', 'pytest')
        Write-Step 'Running the frontend suite'
        Start-WebContainer
        Invoke-Web -Arguments @('npm', 'run', 'test')
    }
    default {
        Stop-WithError "unknown target '$target' - expected backend, frontend or all"
    }
}
