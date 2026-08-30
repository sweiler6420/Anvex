<#
Throw the development database away and rebuild it from nothing.

Usage: reset-db [--yes]

  --yes  skip the confirmation prompt.

Deletes the `db` container and the named volume behind it, starts a fresh one, migrates
to head and loads the seed data. Destructive by definition, so it asks first.

Only the *development* database. `db-test` is untouched and needs no reset: it is backed
by tmpfs with no named volume, so it starts empty on every restart by construction. The
object store is untouched too - `down --volumes` is the blunter instrument for that.

The volume is named `anvex_pgdata` because docker-compose.yml pins the project name to
`anvex` (`name: anvex` at the top of the file), so the prefix cannot drift with the
directory the repo happens to be cloned into.

The migrate and seed steps are spelled out here rather than shelling out to their sibling
scripts: a nested script's exit code does not propagate on its own, and a reset that
reports success after a failed migration is worse than no reset at all.

Twin of reset-db.sh - see _common.ps1.
#>

. (Join-Path $PSScriptRoot '_common.ps1')

$arguments = @($args)
$yes = $false
foreach ($argument in $arguments) {
    if ($argument -eq '--yes') { $yes = $true }
    else { Stop-WithError "unknown flag '$argument' - reset-db takes --yes and nothing else" }
}

if (-not $yes) {
    if (-not (Confirm-Action -Message 'This destroys the development database and everything in it. Continue?')) {
        Stop-WithError 'aborted'
    }
}

Write-Step 'Removing the development database and its volume'
Invoke-Compose -Arguments @('rm', '--stop', '--force', '--volumes', 'db')
$existing = & docker volume ls --quiet --filter 'name=^anvex_pgdata$'
if ($existing) {
    Invoke-Native -Directory $AnvexRepoRoot -Exe 'docker' -Arguments @('volume', 'rm', 'anvex_pgdata')
}

Write-Step 'Starting a fresh database'
Invoke-Compose -Arguments @('up', '-d', '--wait', 'db')

Use-HostDatabase

Write-Step 'Applying migrations'
Invoke-Uv -Arguments @('alembic', 'upgrade', 'head')

Write-Step 'Loading the seed data'
Invoke-Uv -Arguments @('python', '-m', 'scripts.seed_politicians')
