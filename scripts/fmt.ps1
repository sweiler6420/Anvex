<#
Format the code in place.

Usage: fmt [--check]

  --check  report what would change and exit non-zero instead of writing. This is what
           `lint backend` already runs, and what CI gates.

Backend only, and that is a statement rather than an omission: the frontend has no
formatter. Its toolchain is eslint, which is a linter, and adding prettier is a decision
with a repo-wide diff attached - it needs its own ticket, not a line in a helper script.
Until then `lint frontend` is the whole frontend story.

`ruff check --fix` runs after the formatter because the two do different jobs: the
formatter never reorders imports, and the `I` rules in pyproject.toml are what do.

Twin of fmt.sh - see _common.ps1.
#>

. (Join-Path $PSScriptRoot '_common.ps1')

$arguments = @($args)
$check = $false
$rest = $arguments
if ($arguments.Count -gt 0 -and $arguments[0] -eq '--check') {
    $check = $true
    $rest = @($arguments | Select-Object -Skip 1)
}

if ($rest.Count -gt 0) {
    Stop-WithError 'fmt takes --check and nothing else'
}

if ($check) {
    Write-Step 'Checking the backend formatting'
    Invoke-Uv -Arguments @('ruff', 'format', '--check', '.')
    Invoke-Uv -Arguments @('ruff', 'check', '.')
}
else {
    Write-Step 'Formatting the backend'
    Invoke-Uv -Arguments @('ruff', 'format', '.')
    Invoke-Uv -Arguments @('ruff', 'check', '--fix', '.')
}
