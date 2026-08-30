"""Smoke tests for the repo-wide developer scripts in ``scripts/`` (ANV-37).

``scripts/`` ships every command twice — ``up.ps1`` and ``up.sh``, ``test.ps1`` and
``test.sh``, and so on — because the dev machine is Windows and CI is Linux. Two halves of
one script set that quietly drift apart is the entire failure mode of that arrangement:
the PowerShell half gets a fix, the sh half keeps the bug, and the difference is invisible
until CI does something the developer's machine does not.

So this module asserts four different kinds of thing, and only the first is what "a smoke
test" usually means:

1. **Every script exists**, in both languages, with nothing extra and nothing missing.
2. **Every script parses**, using the real parsers — PowerShell's
   ``[Parser]::ParseFile`` and ``sh -n`` — not a regex that hopes.
3. **The two halves are behaviourally equivalent**: the same compose services, the same
   flags, the same tools, the same helper calls in the same numbers, and the same lines
   printed to the developer.
4. **The machine-specific rules hold.** ``python -m pytest`` and never the ``pytest``
   console script (an Application Control policy blocks the shim with ``os error 4551``);
   ``-T`` on every ``docker compose exec`` (without it they hang in a non-TTY shell); and
   no ``NODE_ENV`` anywhere (an inherited ``development`` silently ships a 330 kB dev
   bundle).

**What skips and what fails.** Missing, unpaired, undocumented or divergent scripts *fail*
— they are facts about the repository and are true on any machine. Only the two parser
tests skip, and only when the interpreter itself is absent: a POSIX shell on a bare Windows
box, or PowerShell on a Linux runner. A test that cannot find its parser has learned
nothing, but a test that cannot find a script has.

This lives in ``tests/unit/`` for the same reason
``tests/unit/test_domain_password.py`` reads ``SignUpPage.jsx``: it is a fast, fixtureless
assertion about files that happen to sit outside ``backend/``. Consequence, and it is the
same one: **the backend suite needs the whole repository checked out**, not just
``backend/``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Final

import pytest

from app.settings import REPO_ROOT

SCRIPTS_DIR: Final[Path] = REPO_ROOT / "scripts"

#: The commands ANV-37 specifies, plus the sourced helper both halves share.
EXPECTED_STEMS: Final[tuple[str, ...]] = (
    "_common",
    "down",
    "fmt",
    "lint",
    "logs",
    "makemigration",
    "migrate",
    "reset-db",
    "seed",
    "test",
    "up",
)

#: `_common` is sourced, not run, so it is the one stem without an executable bit.
RUNNABLE_STEMS: Final[tuple[str, ...]] = tuple(s for s in EXPECTED_STEMS if not s.startswith("_"))

#: Service names from docker-compose.yml. Both halves of a script must name the same ones.
COMPOSE_SERVICES: Final[tuple[str, ...]] = (
    "api",
    "beat",
    "db",
    "db-test",
    "minio",
    "minio-init",
    "redis",
    "web",
    "worker",
)

#: Compose subcommands. `up.sh` running `up` while `up.ps1` runs `start` is the bug.
COMPOSE_SUBCOMMANDS: Final[tuple[str, ...]] = ("up", "down", "logs", "exec", "rm")

#: Flags whose presence changes behaviour rather than cosmetics.
SIGNIFICANT_FLAGS: Final[tuple[str, ...]] = (
    "-T",
    "-d",
    "-m",
    "--autogenerate",
    "--check",
    "--filter",
    "--fix",
    "--follow",
    "--force",
    "--no-deps",
    "--profile celery",
    "--profile frontend",
    "--quiet",
    "--stop",
    "--tail",
    "--volumes",
    "--wait",
    "--yes",
)

#: Tool invocations, as they read once the two languages' quoting is normalised away.
TOOL_MARKERS: Final[tuple[str, ...]] = (
    "alembic revision",
    "alembic upgrade",
    "anvex_pgdata",
    "npm run lint",
    "npm run test",
    "python -m pytest",
    "ruff check --fix",
    "ruff check .",
    "ruff format --check",
    "ruff format .",
    "scripts.seed_politicians",
    "volume ls",
    "volume rm",
)

#: The helpers in `_common`, paired by the job they do rather than by name. The *count* is
#: compared, not just the presence: a half that calls the compose helper three times where
#: its twin calls it twice is doing something different.
HELPER_ALIASES: Final[dict[str, tuple[str, str]]] = {
    "announce a step": (r"\bsay\b", r"\bWrite-Step\b"),
    "fail with a message": (r"\bdie\b", r"\bStop-WithError\b"),
    "ask before destroying": (r"\bconfirm\b", r"\bConfirm-Action\b"),
    "run uv": (r"\brun_uv\b", r"\bInvoke-Uv\b"),
    "run docker compose": (r"\brun_compose\b", r"\bInvoke-Compose\b"),
    "run inside web": (r"\brun_web\b", r"\bInvoke-Web\b"),
    "start web": (r"\bstart_web_container\b", r"\bStart-WebContainer\b"),
    "retarget the database": (r"\buse_host_database\b", r"\bUse-HostDatabase\b"),
}

#: How each half must hand `run` to uv. Spelled out per language rather than compared
#: between them, because the bug this pins was present in *both* halves at once.
UV_RUN_PATTERNS: Final[dict[str, str]] = {
    ".sh": r"ANVEX_UV\"? run\b",
    ".ps1": r"'run'",
}

#: How each half clears the inherited virtualenv. Per language for the same reason as above.
CLEAR_VIRTUALENV_PATTERNS: Final[dict[str, str]] = {
    ".sh": r"unset VIRTUAL_ENV\b",
    ".ps1": r"\$env:VIRTUAL_ENV = \$null",
}

#: Lines carrying these call a helper that prints something. Their quoted text is compared.
MESSAGE_CALLS: Final[dict[str, str]] = {
    ".sh": r"\bsay\b|\bdie\b|\bconfirm\b",
    ".ps1": r"\bWrite-Step\b|\bStop-WithError\b|\bConfirm-Action\b",
}

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*")
_QUOTED = re.compile(r"'([^']*)'|\"([^\"]*)\"")
_VARIABLE = re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?")
_USAGE = re.compile(r"^\s*#?\s*Usage:\s*(.+?)\s*$", re.M)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def script(stem: str, suffix: str) -> Path:
    return SCRIPTS_DIR / f"{stem}{suffix}"


def strip_comments(text: str, suffix: str) -> str:
    """Everything that is not a comment.

    Comparing two halves without this compares their prose, and the prose *should* differ —
    each half names its own twin and its own helpers.
    """
    if suffix == ".ps1":
        text = re.sub(r"<#.*?#>", "", text, flags=re.S)
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def normalise(text: str, suffix: str) -> str:
    """Comment-free code with the two languages' quoting and array syntax flattened.

    ``@('npm', 'run', 'test')`` and ``npm run test`` both become ``npm run test``, which is
    what makes a single marker list readable against both halves.
    """
    code = strip_comments(text, suffix)
    code = re.sub(r"[\"'(),\[\]@]", " ", code)
    return " " + re.sub(r"\s+", " ", code) + " "


def facts(path: Path) -> dict[str, object]:
    """The behaviour of one script, reduced to things its twin must match."""
    text = read(path)
    suffix = path.suffix
    code = normalise(text, suffix)
    tokens = set(_TOKEN.findall(code))
    language = 0 if suffix == ".sh" else 1
    return {
        "services": frozenset(s for s in COMPOSE_SERVICES if s in tokens),
        "compose subcommands": frozenset(s for s in COMPOSE_SUBCOMMANDS if s in tokens),
        "flags": frozenset(f for f in SIGNIFICANT_FLAGS if f" {f} " in code),
        "tools": frozenset(m for m in TOOL_MARKERS if m in code),
        "helper calls": {
            name: len(re.findall(pattern[language], strip_comments(text, suffix)))
            for name, pattern in HELPER_ALIASES.items()
        },
        "messages": messages(text, suffix),
    }


def messages(text: str, suffix: str) -> frozenset[str]:
    """Every string the script prints, with variable names normalised away.

    ``$anvex_target`` and ``$Target`` are the same value under two spellings, so both
    collapse to ``$`` before the two halves are compared.
    """
    found: set[str] = set()
    for line in strip_comments(text, suffix).splitlines():
        if not re.search(MESSAGE_CALLS[suffix], line):
            continue
        for single, double in _QUOTED.findall(line):
            found.add(_VARIABLE.sub("$", single or double))
    return frozenset(found)


def usage(path: Path) -> str:
    found = _USAGE.search(read(path))
    assert found is not None, f"{path.name} has no `Usage:` line"
    return found.group(1)


def find_sh() -> str | None:
    """A POSIX shell, falling back to the one Git for Windows installs."""
    found = shutil.which("sh")
    if found:
        return found
    git = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Git"
    for candidate in (git / "bin" / "sh.exe", git / "usr" / "bin" / "sh.exe"):
        if candidate.exists():
            return str(candidate)
    return None


def find_powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


PAIR_IDS: Final[list[str]] = list(EXPECTED_STEMS)
ALL_SCRIPTS: Final[list[Path]] = [
    script(stem, suffix) for stem in EXPECTED_STEMS for suffix in (".ps1", ".sh")
]


class TestTheScriptsExist:
    """ANV-37's literal deliverable: `scripts/` in both PowerShell and sh."""

    @pytest.mark.parametrize("stem", EXPECTED_STEMS)
    @pytest.mark.parametrize("suffix", [".ps1", ".sh"])
    def test_the_script_is_present(self, stem: str, suffix: str) -> None:
        assert script(stem, suffix).is_file()

    @pytest.mark.parametrize("stem", EXPECTED_STEMS)
    @pytest.mark.parametrize("suffix", [".ps1", ".sh"])
    def test_the_script_is_not_empty(self, stem: str, suffix: str) -> None:
        assert read(script(stem, suffix)).strip()

    def test_nothing_unpaired_or_unexpected_lives_in_the_directory(self) -> None:
        """A new script must arrive as a pair, and this test is how it is told so."""
        present = sorted(p.name for p in SCRIPTS_DIR.iterdir() if p.is_file())
        assert present == sorted(p.name for p in ALL_SCRIPTS)

    def test_the_backend_only_entry_point_stays_in_backend_scripts(self) -> None:
        """`scripts/seed` wraps `backend/scripts/`; it does not reimplement it."""
        assert (REPO_ROOT / "backend" / "scripts" / "seed_politicians.py").is_file()
        for suffix in (".ps1", ".sh"):
            assert "scripts.seed_politicians" in normalise(read(script("seed", suffix)), suffix)


class TestTheScriptsParse:
    """Parsed by the real parsers. Skips only when the interpreter itself is absent."""

    @pytest.mark.parametrize("stem", EXPECTED_STEMS)
    def test_the_powershell_half_parses(self, stem: str) -> None:
        shell = find_powershell()
        if shell is None:
            pytest.skip("neither pwsh nor powershell is on PATH")
        path = script(stem, ".ps1")
        program = (
            "$errors = $null; "
            "$null = [System.Management.Automation.Language.Parser]::ParseFile("
            f"'{path}', [ref]$null, [ref]$errors); "
            "if ($errors.Count -gt 0) { $errors | ForEach-Object { $_.ToString() }; exit 1 }"
        )
        done = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-Command", program],
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, f"{path.name} does not parse:\n{done.stdout}{done.stderr}"

    @pytest.mark.parametrize("stem", EXPECTED_STEMS)
    def test_the_sh_half_parses(self, stem: str) -> None:
        shell = find_sh()
        if shell is None:
            pytest.skip("no POSIX shell found on PATH or in the Git installation")
        path = script(stem, ".sh")
        done = subprocess.run([shell, "-n", str(path)], capture_output=True, text=True)
        assert done.returncode == 0, f"{path.name} does not parse:\n{done.stdout}{done.stderr}"


class TestTheShellHalfIsRunnable:
    """Three things that make an `.sh` file a program rather than a text file."""

    @pytest.mark.parametrize("stem", EXPECTED_STEMS)
    def test_it_starts_with_a_posix_shebang(self, stem: str) -> None:
        assert read(script(stem, ".sh")).startswith("#!/usr/bin/env sh\n")

    @pytest.mark.parametrize("stem", EXPECTED_STEMS)
    def test_it_has_unix_line_endings(self, stem: str) -> None:
        """A CRLF `.sh` dies at the shebang with `bad interpreter: /usr/bin/env sh^M`.

        `.gitattributes` normalises this on checkout; the assertion is here because the
        symptom names the interpreter rather than the line endings, and nobody guesses it
        the first time.
        """
        assert "\r" not in read(script(stem, ".sh"))

    def test_every_runnable_script_carries_the_executable_bit(self) -> None:
        """Mode is read from git's index: the working tree has no bit to read on Windows."""
        if shutil.which("git") is None:
            pytest.skip("git is not on PATH")
        listing = subprocess.run(
            ["git", "ls-files", "--stage", "scripts"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert listing.returncode == 0, listing.stderr
        modes = {
            line.split("\t", 1)[1].rsplit("/", 1)[-1]: line.split(" ", 1)[0]
            for line in listing.stdout.splitlines()
            if line
        }
        assert modes, "scripts/ is not tracked by git"
        for stem in RUNNABLE_STEMS:
            assert modes.get(f"{stem}.sh") == "100755", f"{stem}.sh is not executable"
        assert modes.get("_common.sh") == "100644", "_common.sh is sourced, not run"


class TestTheTwoHalvesAgree:
    """The point of the pairing. Each fact is its own test so a failure names the drift."""

    @pytest.mark.parametrize("stem", EXPECTED_STEMS)
    @pytest.mark.parametrize(
        "fact",
        ["services", "compose subcommands", "flags", "tools", "helper calls", "messages"],
    )
    def test_the_halves_share_a_fact(self, stem: str, fact: str) -> None:
        powershell = facts(script(stem, ".ps1"))
        shell = facts(script(stem, ".sh"))
        assert shell[fact] == powershell[fact], (
            f"{stem}.sh and {stem}.ps1 disagree on {fact}: {shell[fact]!r} vs {powershell[fact]!r}"
        )

    @pytest.mark.parametrize("stem", EXPECTED_STEMS)
    def test_the_halves_document_the_same_usage(self, stem: str) -> None:
        assert usage(script(stem, ".sh")) == usage(script(stem, ".ps1"))

    @pytest.mark.parametrize("stem", EXPECTED_STEMS)
    def test_the_usage_line_names_the_command(self, stem: str) -> None:
        """`Usage:` is extension-free, so the two halves can state the same thing."""
        line = usage(script(stem, ".sh"))
        assert ".sh" not in line
        assert ".ps1" not in line
        if stem in RUNNABLE_STEMS:
            assert line.split()[0] == stem


class TestTheMachineSpecificRules:
    """Three rules earlier tickets paid for. Each has a one-line way to be broken."""

    @pytest.mark.parametrize("path", ALL_SCRIPTS, ids=lambda p: p.name)
    def test_pytest_is_never_the_console_script(self, path: Path) -> None:
        """`uv run pytest` dies with `os error 4551` under Application Control.

        Only `python -m pytest` survives, which is why every occurrence of the word has to
        have `-m ` immediately in front of it.
        """
        code = normalise(read(path), path.suffix)
        for match in re.finditer(r"pytest", code):
            assert code[: match.start()].endswith("-m "), (
                f"{path.name} invokes pytest without `python -m`: "
                f"{code[max(0, match.start() - 40) : match.end() + 10]!r}"
            )

    @pytest.mark.parametrize("suffix", [".ps1", ".sh"])
    def test_the_reason_for_python_dash_m_is_written_down(self, suffix: str) -> None:
        """The ticket asks for this in the comments so it is not simplified back later."""
        for stem in ("_common", "test"):
            assert "4551" in read(script(stem, suffix))

    @pytest.mark.parametrize("path", ALL_SCRIPTS, ids=lambda p: p.name)
    def test_every_compose_exec_disables_the_tty(self, path: Path) -> None:
        """Without `-T` these hang forever in any non-interactive shell."""
        code = normalise(read(path), path.suffix)
        for match in re.finditer(r" exec ", code):
            assert code[match.end() :].startswith("-T "), (
                f"{path.name} runs `compose exec` without -T: "
                f"{code[match.start() : match.end() + 40]!r}"
            )

    @pytest.mark.parametrize("suffix", [".ps1", ".sh"])
    def test_uv_is_asked_to_run_something(self, suffix: str) -> None:
        """`uv ruff check` is not `uv run ruff check` — uv answers `unrecognized subcommand`.

        Both halves shipped exactly that during ANV-37, and every parity test passed: two
        halves wrong in the *same* way agree perfectly. Only executing them found it, so
        this is the one place the shape is asserted per language rather than compared
        between them. Every other tool name is a caller's argument; `run` is the helper's.
        """
        body = strip_comments(read(script("_common", suffix)), suffix)
        assert re.search(UV_RUN_PATTERNS[suffix], body), (
            f"_common{suffix} invokes uv without `run`, so every backend command in the "
            f"script set fails with `unrecognized subcommand`"
        )

    @pytest.mark.parametrize("suffix", [".ps1", ".sh"])
    def test_the_inherited_virtualenv_is_cleared(self, suffix: str) -> None:
        """A stale VIRTUAL_ENV makes `uv run` use *that* interpreter, silently.

        The dev machine carries one pointing at an unrelated project, so a script that
        forgets this runs the backend against the wrong dependencies rather than failing.
        Per language, like the `run` above: both halves could forget it together.
        """
        body = strip_comments(read(script("_common", suffix)), suffix)
        assert re.search(CLEAR_VIRTUALENV_PATTERNS[suffix], body), (
            f"_common{suffix} does not clear VIRTUAL_ENV before invoking uv"
        )

    @pytest.mark.parametrize("path", ALL_SCRIPTS, ids=lambda p: p.name)
    def test_nothing_sets_node_env(self, path: Path) -> None:
        """An inherited NODE_ENV=development ships a 330 kB dev bundle, silently."""
        assert "NODE_ENV" not in read(path)


class TestTheScriptsAreDocumented:
    """ANV-37 says "documented in README.md", so that is an assertion, not a habit."""

    def readme_section(self) -> str:
        readme = read(REPO_ROOT / "README.md")
        found = re.search(r"^## Scripts\b(.*?)(?=^## )", readme, re.M | re.S)
        assert found is not None, "README.md has no `## Scripts` section"
        return found.group(1)

    def readme_table(self) -> set[str]:
        """The command column of the README's table, and only that column.

        Deliberately not "the name appears somewhere in the section": every command is
        also named in the prose around the table, so a substring search passes while the
        table itself says something else. Found by mutation — renaming the `reset-db` row
        was invisible until this looked at the row.
        """
        return set(re.findall(r"^\| `([^`]+)` \|", self.readme_section(), re.M))

    @pytest.mark.parametrize("stem", RUNNABLE_STEMS)
    def test_the_readme_lists_the_command(self, stem: str) -> None:
        assert stem in self.readme_table()

    def test_the_readme_lists_nothing_that_does_not_exist(self) -> None:
        assert self.readme_table() == set(RUNNABLE_STEMS)

    def test_the_readme_shows_both_invocations(self) -> None:
        """Both shells, spelled out - a Windows reader should not have to translate."""
        section = self.readme_section()
        assert "up.ps1" in section
        assert "up.sh" in section
