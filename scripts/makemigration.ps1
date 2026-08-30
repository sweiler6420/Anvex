<#
Autogenerate a new alembic revision from the models.

Usage: makemigration <message> [extra alembic flags]

Autogenerate compares the live database against `Base.metadata`, so the database has to
be up *and* already at head - run `migrate` first, or the diff is written against the
wrong baseline. Read the generated file before committing it: autogenerate does not see
renames, data migrations or anything a check constraint cannot express.

Runs on the host rather than in the api container on purpose: `alembic.ini` lints every
generated revision with a ruff post-write hook, and ruff is a dev dependency that the
runtime image deliberately does not carry.

Twin of makemigration.sh - see _common.ps1.
#>

. (Join-Path $PSScriptRoot '_common.ps1')

$arguments = @($args)

if ($arguments.Count -eq 0) {
    Stop-WithError 'a message is required, for example: makemigration "add holdings table"'
}

$message = $arguments[0]
$alembicArgs = @($arguments | Select-Object -Skip 1)

Use-HostDatabase
Write-Step 'Autogenerating a revision from the models'
Invoke-Uv -Arguments (@('alembic', 'revision', '--autogenerate', '-m', $message) + $alembicArgs)
