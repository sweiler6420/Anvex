<#
Shared helpers for the Anvex developer scripts (ANV-37). Dot-sourced, never run directly.

Usage: sourced by the scripts beside it

Every script in this directory has a POSIX sh twin of the same name, and the two halves
are kept behaviourally identical on purpose. `backend/tests/unit/test_repo_scripts.py`
compares them: the compose services and flags they use, the tools they invoke, and the
lines they print. Change one half, change the other - two divergent halves of one script
set is the failure mode this pairing exists to prevent.

Three machine facts are handled here rather than repeated in every script:

  * `uv` is not necessarily on PATH - it is not on the primary dev machine - so it is
    resolved with a fallback to ~/.local/bin.
  * A stale VIRTUAL_ENV pointing at an unrelated virtualenv makes `uv run` target the
    wrong interpreter, so it is cleared before every uv invocation.
  * `.env` sets POSTGRES_HOST=db, which is a compose service name and resolves only
    inside the compose network. Anything alembic-shaped runs on the host, so
    `Use-HostDatabase` translates that to the published host port. See its comment.

**pytest is always invoked as `python -m pytest`, never as the `pytest` console script.**
An Application Control policy on the dev machine blocks the generated shim and the run
dies with `os error 4551`. Do not "simplify" `python -m pytest` back to `pytest`.

Written for Windows PowerShell 5.1 as well as PowerShell 7: no `&&`, no ternary, no
null-coalescing, and every native exit code checked by hand.

**Flags are parsed by hand rather than declared as `[switch]` parameters**, so both shells
take exactly the same command line - `down --volumes --yes` is one string a developer can
paste into either. A `[switch]$Volumes` would answer to `-Volumes` and nothing else, and
PowerShell binds a bare `--volumes` positionally, so the idiomatic spelling would have
silently made the two halves two different programs.

**And the command scripts read `$args` rather than declaring a `param()` block at all.** A
single `[Parameter()]` attribute makes a script *advanced*, which gives it PowerShell's
common parameters, which are matched by prefix - so `test backend -p no:cacheprovider`
bound `-p` to `-PipelineVariable` and failed before the script ran, and `-v` would have
gone to `-Verbose`. Both are ordinary pytest flags. A plain script has no common
parameters, so every argument reaches `$args` exactly as typed.
#>

$ErrorActionPreference = 'Stop'

$AnvexScriptsDir = Split-Path -Parent $PSCommandPath
$AnvexRepoRoot = Split-Path -Parent $AnvexScriptsDir

# ------------------------------------------------------------------------------ output

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host ''
    Write-Host "== $Message"
}

function Stop-WithError {
    param([Parameter(Mandatory = $true)][string]$Message)
    [Console]::Error.WriteLine("error: $Message")
    exit 1
}

function Confirm-Action {
    param([Parameter(Mandatory = $true)][string]$Message)
    $reply = Read-Host "$Message [y/N]"
    return @('y', 'Y', 'yes', 'YES') -contains $reply
}

# --------------------------------------------------------------------------------- env

# One value out of the repo-root `.env`. Deliberately not a full dotenv parser: it reads
# the last plain `KEY=value` line, which is all these scripts need and all `.env` contains.
function Get-EnvValue {
    param([Parameter(Mandatory = $true)][string]$Key, [string]$Fallback = '')
    $envPath = Join-Path $AnvexRepoRoot '.env'
    if (Test-Path $envPath) {
        $matched = @(Get-Content -LiteralPath $envPath) -match "^$Key="
        if ($matched.Count -gt 0) {
            $value = $matched[-1].Substring($Key.Length + 1).Trim()
            if ($value) { return $value }
        }
    }
    return $Fallback
}

# Point host-side database tooling at the published port.
#
# `.env` is the single source of configuration (CLAUDE.md 2), and it names the database `db`
# because that is what the api, worker and beat containers dial. alembic and the seed script
# run on the *host*, where `db` resolves to nothing. So: only when `.env` still says `db` -
# i.e. the developer has not repointed it themselves - and only when the environment does
# not already carry an override, swap in localhost and the port compose publishes. That
# port is read from the same `.env`, so there is still exactly one number to change.
function Use-HostDatabase {
    if ($env:POSTGRES_HOST) {
        return
    }
    if ((Get-EnvValue -Key 'POSTGRES_HOST' -Fallback 'db') -ne 'db') {
        return
    }
    $env:POSTGRES_HOST = 'localhost'
    $env:POSTGRES_PORT = (Get-EnvValue -Key 'POSTGRES_HOST_PORT' -Fallback '5442')
}

# ------------------------------------------------------------------------ running things

# Native commands do not throw on a non-zero exit in PowerShell, so every one of them goes
# through here and the exit code is checked by hand. The script exits with the child's code,
# which is what makes `scripts\test.ps1` usable as a CI step.
#
# The `Continue` window around the call is load-bearing, not tidying. Windows PowerShell
# turns a native command's stderr into an ErrorRecord whenever the caller redirects it -
# `.\test.ps1 2>&1`, a CI log capture, an agent harness - and with $ErrorActionPreference
# set to 'Stop' that record is *terminating*. `docker compose` writes its progress to
# stderr, so the script would die on the first line of a perfectly healthy `up`. The exit
# code is the only thing here that gets to decide whether something failed.
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$Arguments = @()
    )
    Push-Location -LiteralPath $Directory
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Arguments
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    finally {
        $ErrorActionPreference = $previous
        Pop-Location
    }
}

# ---------------------------------------------------------------------------------- uv

$AnvexUv = ''
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($uvCommand) {
    $AnvexUv = $uvCommand.Source
}
else {
    foreach ($candidate in @((Join-Path $HOME '.local\bin\uv.exe'), (Join-Path $HOME '.local\bin\uv'))) {
        if (Test-Path $candidate) {
            $AnvexUv = $candidate
            break
        }
    }
}

# Everything here is `uv run <what you passed>`, from `backend/`, with the environment
# cleaned first. Callers name the tool - `Invoke-Uv @('ruff', 'check', '.')` - and never
# repeat `run`.
function Invoke-Uv {
    param([string[]]$Arguments = @())
    if (-not $AnvexUv) {
        Stop-WithError 'uv was not found on PATH or in ~/.local/bin - install it from https://docs.astral.sh/uv/'
    }
    $env:VIRTUAL_ENV = $null
    Invoke-Native -Directory (Join-Path $AnvexRepoRoot 'backend') -Exe $AnvexUv -Arguments (@('run') + $Arguments)
}

# ----------------------------------------------------------------------------- compose

function Invoke-Compose {
    param([string[]]$Arguments = @())
    Invoke-Native -Directory $AnvexRepoRoot -Exe 'docker' -Arguments (@('compose') + $Arguments)
}

# Every frontend command runs inside the `web` container: there is no node on the dev host.
# `-T` disables TTY allocation and is **required** - without it these hang forever in a
# non-interactive shell, which is every script, every CI job and every agent session.
function Invoke-Web {
    param([string[]]$Arguments = @())
    Invoke-Compose -Arguments (@('--profile', 'frontend', 'exec', '-T', 'web') + $Arguments)
}

# vitest and eslint need no api, no database, no broker and no object store, so `--no-deps`
# keeps a lint or a test run from dragging the whole stack up behind it. Idempotent: a
# container that is already running is left alone.
function Start-WebContainer {
    Invoke-Compose -Arguments @('--profile', 'frontend', 'up', '-d', '--no-deps', 'web')
}
