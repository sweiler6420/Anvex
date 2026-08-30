<#
Bring the database schema up to a revision.

Usage: migrate [revision] [extra alembic flags]

The revision defaults to `head`. `migrate <older-revision>` downgrades nothing - use
`uv run alembic downgrade` for that, deliberately, because it is not a routine operation.

Runs on the host against the database compose publishes, so `up` (or at least
`docker compose up -d db`) has to have happened first. See `Use-HostDatabase`
in _common.ps1 for why the host and the containers reach the same database by
different names.

Twin of migrate.sh - see _common.ps1.
#>

. (Join-Path $PSScriptRoot '_common.ps1')

$arguments = @($args)
$revision = 'head'
$alembicArgs = $arguments
if ($arguments.Count -gt 0 -and -not $arguments[0].StartsWith('-')) {
    $revision = $arguments[0]
    $alembicArgs = @($arguments | Select-Object -Skip 1)
}

Use-HostDatabase
Write-Step 'Applying migrations'
Invoke-Uv -Arguments (@('alembic', 'upgrade', $revision) + $alembicArgs)
