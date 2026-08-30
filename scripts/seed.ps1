<#
Load the checked-in seed data into the database.

Usage: seed [extra flags for the seed entry point]

A wrapper and nothing more: the work lives in `backend/scripts/seed_politicians.py`,
which is itself a thin entry point onto `PoliticianService.seed_roster`. Backend-only
entry points stay in `backend/scripts/`; this directory only ever wraps them, so there
is one implementation and the Celery job that wants the same behaviour calls the same
service method rather than shelling out.

Idempotent - the seed upserts on the natural key, so running it twice leaves the same
rows. Needs the database up and migrated.

Twin of seed.sh - see _common.ps1.
#>

. (Join-Path $PSScriptRoot '_common.ps1')

$arguments = @($args)

Use-HostDatabase
Write-Step 'Loading the seed data'
Invoke-Uv -Arguments (@('python', '-m', 'scripts.seed_politicians') + $arguments)
