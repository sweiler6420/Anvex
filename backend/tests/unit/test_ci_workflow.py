"""Assertions about the CI workflow itself (ANV-38).

``.github/workflows/ci.yml`` is a configuration file that nothing else in the repository
executes, which makes it the easiest kind of file to break silently: a typo skips a job, a
filter stops matching, a step quietly stops running the thing its name claims. GitHub tells
you none of that — it just reports a green tick over less work than you thought.

So this module asserts five kinds of thing:

1. **It parses**, with a real YAML parser, and declares the triggers and jobs it is
   supposed to.
2. **Every action is pinned to a commit SHA**, with the human-readable version beside it in
   a comment. A floating tag is someone else's code running on this repository's checkout.
3. **The path filters match the paths they intend.** This is the load-bearing one. The
   backend suite reads five files that live *outside* ``backend/`` — most importantly
   ``SignUpPage.jsx``, whose drift test is the only thing keeping the client and server
   password policies in step — so a backend filter of ``backend/**`` would silently
   disable a cross-stack guard on exactly the commits that need it. The filters are
   therefore checked against paths *discovered in the test sources*, not against a list
   somebody remembered to update.
4. **No job re-spells a command ``scripts/`` already owns.** ANV-37 exists to make "run the
   backend suite" have one implementation; a workflow with its own ``uv run python -m
   pytest`` is the second one.
5. **The machine-specific rules hold**: the whole repository is checked out, ``pwsh`` is
   present so the ``.ps1`` parser tests actually execute, the service tiers are asserted
   reachable rather than allowed to skip, and nothing anywhere sets ``NODE_ENV``.

Like :mod:`tests.unit.test_repo_scripts` this is a fast, fixtureless assertion about files
outside ``backend/``, and it carries the same consequence: **the backend suite needs the
whole repository checked out.** Which is, recursively, the thing item 5 is about.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import pytest
import yaml

from app.settings import REPO_ROOT

WORKFLOW_PATH: Final[Path] = REPO_ROOT / ".github" / "workflows" / "ci.yml"

#: The jobs `ci.yml` declares. `changes` computes the path filters the others consume.
#: `smoke` (ANV-41) is the only one that boots the stack, and the only one gated on *either*
#: stack having changed — it proves the two together, so a change to either invalidates it.
EXPECTED_JOBS: Final[tuple[str, ...]] = ("changes", "backend", "frontend", "smoke")

#: The two stacks that get their own filter and their own job.
STACKS: Final[tuple[str, ...]] = ("backend", "frontend")

#: Paths a backend test reads that are **not** matched by the backend filter, with the
#: reason. `.env` is gitignored: it can never appear in a diff, so a filter entry for it
#: would match nothing, ever. Anything else appearing here is a bug in the filter.
UNFILTERABLE_PATHS: Final[frozenset[str]] = frozenset({".env"})

#: Paths that must NOT wake the backend job. Documentation and the frontend at large are
#: the whole reason the filter exists; if these match, it is not filtering anything.
NOT_BACKEND: Final[tuple[str, ...]] = (
    "docs/build-log.md",
    "docs/ticket-log.md",
    "frontend/src/App.jsx",
    "frontend/vite.config.js",
)

#: And the mirror image for the frontend job.
NOT_FRONTEND: Final[tuple[str, ...]] = (
    "backend/app/main.py",
    "backend/tests/unit/test_ci_workflow.py",
    "scripts/test.sh",
    "docs/build-log.md",
    "README.md",
)

#: Backend tooling that `scripts/` already owns. A `run:` step naming one of these directly
#: is the second implementation ANV-37 deleted — the trap it encodes (`python -m pytest`,
#: never the console script) then lives in two places and only one of them is tested.
OWNED_BY_SCRIPTS: Final[tuple[str, ...]] = ("pytest", "ruff", "alembic")

#: `uses: owner/repo@<40 hex>`. Nothing floating, nothing tag-shaped.
PINNED_USES: Final[re.Pattern[str]] = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w.-]+)*@[0-9a-f]{40}$")

#: A `uses:` line in the raw file, with whatever comment follows it.
USES_LINE: Final[re.Pattern[str]] = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)\s*(#.*)?$", re.M)

#: `REPO_ROOT / "a" / "b"` in a test source, however it is wrapped across lines.
REPO_ROOT_PATH: Final[re.Pattern[str]] = re.compile(r"REPO_ROOT\s*(?:/\s*\"[^\"]+\"\s*)+", re.S)

#: A `.sh` under `scripts/` named by a `run:` step.
SCRIPT_CALL: Final[re.Pattern[str]] = re.compile(r"\./scripts/([\w-]+)\.sh\b")

#: `npm run <script>` in a `run:` step.
NPM_RUN: Final[re.Pattern[str]] = re.compile(r"\bnpm run ([\w:-]+)")


def workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def workflow() -> dict[str, Any]:
    """The parsed workflow.

    Not cached: every test wants an independent copy, and the file is three kilobytes.
    """
    loaded = yaml.safe_load(workflow_text())
    assert isinstance(loaded, dict), "ci.yml did not parse to a mapping"
    return loaded


def triggers(document: dict[str, Any]) -> dict[str, Any]:
    """The `on:` block.

    YAML 1.1 — which is what PyYAML implements — resolves a bare `on` to the boolean
    `True`, so the key is looked up both ways rather than only the way it is written.
    """
    found = document.get("on", document.get(True))
    assert isinstance(found, dict), "ci.yml has no `on:` block"
    return found


def jobs(document: dict[str, Any] | None = None) -> dict[str, Any]:
    found = (document or workflow()).get("jobs")
    assert isinstance(found, dict), "ci.yml has no `jobs:` block"
    return found


def steps(job: str) -> list[dict[str, Any]]:
    return list(jobs()[job].get("steps") or [])


def run_steps(job: str) -> list[str]:
    """Every shell command the job runs, in order."""
    return [str(step["run"]) for step in steps(job) if "run" in step]


def filters() -> dict[str, list[str]]:
    """The `dorny/paths-filter` filter definitions, parsed out of the step's string input.

    The action takes its filters as a YAML *string*, so this is a second parse rather than
    a lookup — which is exactly why it is worth asserting: a filter block that does not
    parse is not an error at any point, it is simply a filter that matches nothing.
    """
    for step in steps("changes"):
        given = (step.get("with") or {}).get("filters")
        if given is None:
            continue
        parsed = yaml.safe_load(given)
        assert isinstance(parsed, dict), "the paths-filter `filters:` input is not a mapping"
        return {name: list(patterns) for name, patterns in parsed.items()}
    pytest.fail("no step in the `changes` job supplies `filters:`")


def as_regex(pattern: str) -> re.Pattern[str]:
    """One filter glob, as a regex.

    Only the two shapes the filters actually use are supported — a literal path, or a
    directory followed by ``/**`` — and :meth:`TestThePathFilters.
    test_every_pattern_is_a_shape_this_test_can_reason_about` refuses anything else. That
    refusal is the point: a matcher that quietly mis-models a clever glob would report
    that the filters are correct when they are not, which is worse than no test at all.
    """
    if pattern.endswith("/**"):
        return re.compile(rf"^{re.escape(pattern[:-3])}/.+$")
    return re.compile(rf"^{re.escape(pattern)}$")


def matches(stack: str, path: str) -> bool:
    return any(as_regex(pattern).search(path) for pattern in filters()[stack])


def code_of(source: Path) -> str:
    """One Python source with its comment lines removed.

    :func:`repo_root_paths` searches for a code shape, and the prose in this repository
    quotes code constantly - including, three definitions above, the exact shape it looks
    for. Scanning the comments would make every explanation of the rule a violation of it.
    """
    text = source.read_text(encoding="utf-8")
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def walk_strings(node: object) -> Iterator[str]:
    """Every string anywhere in the parsed workflow - keys and values, at any depth.

    Used for the rules that must hold in the *configuration*. Reading them off the raw
    file would trip over the comments that explain them.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk_strings(key)
            yield from walk_strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk_strings(item)
    elif isinstance(node, str):
        yield node


def repo_root_paths() -> set[str]:
    """Every repo-root-relative path the backend tests build out of ``REPO_ROOT``.

    Discovered rather than listed, so a future test that reaches outside ``backend/``
    extends the backend path filter's obligations automatically — which is the only way
    this stays true. `REPO_ROOT / "scripts"` yields ``scripts``; a path filter question
    about a directory is asked with a file inside it, hence the ``/…`` below.
    """
    found: set[str] = set()
    for source in sorted((REPO_ROOT / "backend" / "tests").rglob("*.py")):
        for match in REPO_ROOT_PATH.finditer(code_of(source)):
            parts = re.findall(r"\"([^\"]+)\"", match.group(0))
            found.add("/".join(parts))
    return found


class TestTheWorkflowIsValid:
    """It exists, it parses, and it declares what ANV-38 asked for."""

    def test_the_workflow_file_is_present(self) -> None:
        assert WORKFLOW_PATH.is_file(), f"{WORKFLOW_PATH} is missing"

    def test_it_parses_as_yaml(self) -> None:
        """A broken workflow costs a push cycle to discover; this costs a millisecond."""
        assert workflow()

    def test_it_has_a_name_and_jobs(self) -> None:
        document = workflow()
        assert document.get("name")
        assert set(jobs(document)) == set(EXPECTED_JOBS)

    def test_it_runs_on_push_and_on_pull_request(self) -> None:
        """ANV-38's literal words. Both, not either."""
        assert set(triggers(workflow())) == {"push", "pull_request"}

    def test_push_builds_main_only(self) -> None:
        """A pull request already builds its branch; pushing it would build it twice."""
        assert triggers(workflow())["push"] == {"branches": ["main"]}

    def test_every_job_names_a_runner(self) -> None:
        for name, job in jobs().items():
            assert job.get("runs-on"), f"job `{name}` has no runs-on"

    def test_an_older_run_on_the_same_ref_is_cancelled(self) -> None:
        concurrency = workflow().get("concurrency")
        assert isinstance(concurrency, dict)
        assert concurrency.get("cancel-in-progress") is True

    def test_the_token_is_read_only(self) -> None:
        """Nothing here publishes, comments or tags."""
        assert workflow()["permissions"] == {"contents": "read", "pull-requests": "read"}


class TestEveryActionIsPinned:
    """A tag is a moving target; a SHA is the code that was reviewed."""

    def test_every_uses_names_a_commit(self) -> None:
        used = [step["uses"] for job in EXPECTED_JOBS for step in steps(job) if "uses" in step]
        assert used, "no actions are used at all - did the jobs lose their steps?"
        for reference in used:
            assert PINNED_USES.match(reference), f"`{reference}` is not pinned to a commit SHA"

    def test_every_pin_says_which_version_it_is(self) -> None:
        """`@3d3c42e5…` tells a reader nothing. `# v7.0.1` beside it tells them everything."""
        found = USES_LINE.findall(workflow_text())
        assert found, "no `uses:` lines found in ci.yml"
        for reference, comment in found:
            assert re.match(r"#\s*v\d", comment or ""), (
                f"`uses: {reference}` has no `# v…` comment naming the pinned version"
            )


class TestThePathFilters:
    """The filters, checked against the paths they are meant to match.

    A path filter is the one part of a CI configuration that can *remove* coverage without
    touching a test, so these are the assertions that matter most in this module.
    """

    def test_there_is_one_filter_per_stack(self) -> None:
        assert set(filters()) == set(STACKS)

    @pytest.mark.parametrize("stack", STACKS)
    def test_every_pattern_is_a_shape_this_test_can_reason_about(self, stack: str) -> None:
        """Literal paths and `dir/**`, and nothing else - see :func:`as_regex`."""
        for pattern in filters()[stack]:
            body = pattern[:-3] if pattern.endswith("/**") else pattern
            assert "*" not in body, (
                f"filter pattern `{pattern}` is more than a literal path or a `dir/**`, "
                f"so this module's matcher cannot honestly check it"
            )

    @pytest.mark.parametrize("stack", STACKS)
    def test_a_change_to_the_workflow_runs_everything(self, stack: str) -> None:
        assert matches(stack, ".github/workflows/ci.yml")

    def test_the_backend_filter_covers_its_own_stack(self) -> None:
        assert matches("backend", "backend/app/main.py")
        assert matches("backend", "backend/tests/unit/test_ci_workflow.py")
        assert matches("backend", "backend/pyproject.toml")

    def test_the_frontend_filter_covers_its_own_stack(self) -> None:
        assert matches("frontend", "frontend/src/App.jsx")
        assert matches("frontend", "frontend/package.json")

    def test_the_backend_filter_covers_the_password_drift_test(self) -> None:
        """The single most important entry in the whole file.

        ``tests/unit/test_domain_password.py`` parses ``SignUpPage.jsx`` so the client and
        server password policies cannot diverge (ANV-43). A backend filter of
        ``backend/**`` would skip the backend job on precisely the commit that edits the
        client rules — the guard would stop guarding, on the only change it guards against,
        with no test edited and nothing to see in the diff.
        """
        assert matches("backend", "frontend/src/features/auth/components/SignUpPage.jsx")

    @pytest.mark.parametrize("path", sorted(repo_root_paths() - UNFILTERABLE_PATHS))
    def test_the_backend_filter_covers_every_repo_root_file_a_test_reads(self, path: str) -> None:
        """Derived from the test sources, so a new cross-boundary read cannot be forgotten.

        Anything a backend test reads out of ``REPO_ROOT`` is an input to the backend
        suite; if editing it does not run that suite, the assertion about it is decoration.
        """
        probe = path if "." in Path(path).name else f"{path}/probe"
        assert matches("backend", probe), (
            f"a backend test reads `{path}`, but editing it would not run the backend job"
        )

    @pytest.mark.parametrize("path", NOT_BACKEND)
    def test_the_backend_filter_ignores_what_it_should(self, path: str) -> None:
        assert not matches("backend", path)

    @pytest.mark.parametrize("path", NOT_FRONTEND)
    def test_the_frontend_filter_ignores_what_it_should(self, path: str) -> None:
        assert not matches("frontend", path)

    @pytest.mark.parametrize("stack", STACKS)
    def test_the_job_is_gated_on_its_own_filter(self, stack: str) -> None:
        """A filter nothing consumes is a filter that filters nothing."""
        job = jobs()[stack]
        assert job.get("needs") == "changes" or "changes" in list(job.get("needs") or [])
        assert job.get("if") == f"needs.changes.outputs.{stack} == 'true'"

    @pytest.mark.parametrize("stack", STACKS)
    def test_the_changes_job_publishes_the_output_the_gate_reads(self, stack: str) -> None:
        outputs = jobs()["changes"].get("outputs") or {}
        assert stack in outputs, f"the `changes` job does not output `{stack}`"
        assert f"steps.filter.outputs.{stack}" in outputs[stack]


class TestNothingIsReSpelled:
    """ANV-37 gave every command one implementation. This keeps it that way."""

    def test_the_backend_job_goes_through_the_scripts(self) -> None:
        commands = " ".join(run_steps("backend"))
        assert "./scripts/lint.sh backend" in commands
        assert "./scripts/test.sh backend" in commands

    @pytest.mark.parametrize("tool", OWNED_BY_SCRIPTS)
    def test_no_step_invokes_a_tool_the_scripts_own(self, tool: str) -> None:
        """`uv run python -m pytest` in a YAML file is the bug this ticket must not add.

        The trap it carries - the console script dies under Application Control - is
        written down and tested in exactly one place. A copy here would be a copy of the
        command without a copy of the reason.
        """
        for job in EXPECTED_JOBS:
            for command in run_steps(job):
                assert tool not in command, f"job `{job}` invokes `{tool}` directly: {command!r}"

    def test_every_script_it_calls_exists(self) -> None:
        called = {
            stem
            for job in EXPECTED_JOBS
            for command in run_steps(job)
            for stem in SCRIPT_CALL.findall(command)
        }
        assert called, "the workflow calls no scripts at all"
        for stem in sorted(called):
            assert (REPO_ROOT / "scripts" / f"{stem}.sh").is_file(), (
                f"ci.yml calls scripts/{stem}.sh, which does not exist"
            )

    def test_it_calls_the_posix_half_and_never_the_powershell_one(self) -> None:
        """The runner is Linux. A `.ps1` here would be a paste from the dev machine."""
        for job in EXPECTED_JOBS:
            for command in run_steps(job):
                assert ".ps1" not in command, f"job `{job}` runs a PowerShell script: {command!r}"

    def test_every_npm_script_it_runs_is_defined(self) -> None:
        package = json.loads((REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
        defined = set(package["scripts"])
        called = {name for command in run_steps("frontend") for name in NPM_RUN.findall(command)}
        assert called, "the frontend job runs no npm scripts"
        assert called <= defined, f"ci.yml runs undefined npm scripts: {sorted(called - defined)}"

    def test_the_smoke_job_goes_through_the_script(self) -> None:
        """ANV-41's job is `scripts/smoke.sh` and a `.env`, and nothing else.

        A second spelling of the boot sequence in YAML is exactly what ANV-37 deleted, and
        it would be the *only* copy nobody runs on a developer's machine.
        """
        commands = " ".join(run_steps("smoke"))
        assert "./scripts/smoke.sh" in commands
        assert "cp .env.example .env" in commands

    def test_the_smoke_job_never_spends_the_vendor_quota(self) -> None:
        """`--live-vendor` on every push would spend somebody's 25 calls a day.

        There is no key on a runner to spend, so the flag could only ever fail there — but
        the reason it is absent is the quota, and an assertion is what keeps it absent when
        a key does eventually exist.
        """
        for command in run_steps("smoke"):
            assert "--live-vendor" not in command, f"the smoke job asks for a live call: {command}"

    def test_the_smoke_job_is_gated_on_either_stack(self) -> None:
        """It proves the two together, so a change to *either* invalidates the last run."""
        gate = str(jobs()["smoke"].get("if"))
        for stack in STACKS:
            assert f"needs.changes.outputs.{stack} == 'true'" in gate
        assert "||" in gate

    def test_the_smoke_job_captures_the_containers_on_a_failure(self) -> None:
        """The step says *what* failed; only the logs say what the containers were doing."""
        failure_steps = [step for step in steps("smoke") if step.get("if") == "failure()"]
        assert failure_steps, "a smoke failure leaves no way to see what the stack was doing"
        assert any("compose" in str(step.get("run")) for step in failure_steps)

    def test_the_frontend_job_covers_the_four_things_the_ticket_asks_for(self) -> None:
        commands = " ".join(run_steps("frontend"))
        assert "npm ci" in commands
        for script in ("lint", "test", "build"):
            assert f"npm run {script}" in commands


class TestTheFormatterIsGated:
    """ANV-15's finding, kept from happening twice.

    `ruff format --check` would have rewritten 37 files at that point, because only
    `ruff check` had ever been enforced. The repository was formatted once and the gate has
    to stay: an unenforced formatter drifts back within a few tickets. The gate lives in
    `scripts/lint.sh`, so this asserts both halves of the arrangement - the script runs it,
    and CI runs the script.
    """

    def test_the_lint_script_still_runs_the_formatter_in_check_mode(self) -> None:
        body = (REPO_ROOT / "scripts" / "lint.sh").read_text(encoding="utf-8")
        assert "ruff format --check" in body

    def test_ci_runs_the_lint_script(self) -> None:
        assert any("lint.sh backend" in command for command in run_steps("backend"))


class TestTheMachineSpecificRules:
    """Five ways a green run could mean less than it looks like it means."""

    @pytest.mark.parametrize("job", ["backend", "frontend"])
    def test_the_whole_repository_is_checked_out(self, job: str) -> None:
        """A narrowed checkout breaks the backend suite and the frontend's `envDir` alike.

        `test_domain_password.py` and `test_repo_scripts.py` *fail* rather than skip when
        their files are absent, and `vite.config.js` points `envDir` one level above
        `frontend/`.
        """
        checkouts = [step for step in steps(job) if "actions/checkout" in str(step.get("uses"))]
        assert checkouts, f"job `{job}` never checks the repository out"
        for step in checkouts:
            inputs = step.get("with") or {}
            assert "sparse-checkout" not in inputs
            assert "path" not in inputs

    def test_powershell_is_asserted_so_the_ps1_parser_tests_actually_run(self) -> None:
        """`test_repo_scripts.py` skips its eleven `.ps1` parser tests without an interpreter.

        Skipping is right on a developer's machine and wrong here, where it would mean half
        the script set is never parsed and nobody is told.
        """
        assert any("pwsh" in command for command in run_steps("backend"))

    def test_the_service_tiers_are_asserted_reachable(self) -> None:
        """Every tier skips when its service is absent, so a wiring mistake is invisible.

        A green run over a suite that skipped Postgres and Redis is the failure mode; the
        step asks the harness's own probes instead of trusting the services block.

        The interpreter is asserted as well as the probe. Mutating the step to `: <<'PY'`
        - a heredoc fed to the null command - leaves every word of the probe in the file
        and runs none of it, and a test that only searched for `unavailable_reason` said
        nothing. Found by mutation.
        """
        probes = [command for command in run_steps("backend") if "unavailable_reason" in command]
        assert probes, "nothing asserts that the service tiers can actually be reached"
        assert any("uv run python" in command for command in probes), (
            "the reachability probe is written down but never handed to an interpreter"
        )

    def test_the_backend_job_provides_postgres_and_redis(self) -> None:
        services = jobs()["backend"].get("services") or {}
        assert set(services) == {"postgres", "redis"}
        for name, service in services.items():
            assert "--health-cmd" in service.get("options", ""), f"`{name}` has no healthcheck"

    def test_the_harness_is_pointed_at_the_postgres_service(self) -> None:
        """The env and the service have to agree, or the db tier skips and the run is green."""
        job = jobs()["backend"]
        env = {key: str(value) for key, value in (job.get("env") or {}).items()}
        service = job["services"]["postgres"]
        published = str(service["ports"][0])
        assert env["POSTGRES_TEST_HOST"] == "localhost"
        assert published == f"{env['POSTGRES_TEST_PORT']}:5432"
        assert env["POSTGRES_TEST_DB"] == service["env"]["POSTGRES_DB"]
        assert env["POSTGRES_USER"] == service["env"]["POSTGRES_USER"]
        assert env["POSTGRES_PASSWORD"] == service["env"]["POSTGRES_PASSWORD"]

    def test_the_harness_is_pointed_at_the_redis_service(self) -> None:
        job = jobs()["backend"]
        env = {key: str(value) for key, value in (job.get("env") or {}).items()}
        published = str(job["services"]["redis"]["ports"][0])
        assert published == f"{env['REDIS_HOST_PORT']}:6379"

    def test_nothing_sets_node_env(self) -> None:
        """An inherited `NODE_ENV=development` ships a development bundle, silently.

        Asked of the parsed configuration rather than of the file, and about *setting* it
        rather than about naming it: both the workflow and the step that catches a
        development bundle say the words `NODE_ENV`, because that is what they are for. A
        substring search over the raw file cannot tell a warning from the thing it warns
        about, so this looks for an assignment and for an `env:` entry instead.
        """
        document = workflow()
        for value in walk_strings(document):
            assert "NODE_ENV=" not in value, f"ci.yml assigns NODE_ENV: {value!r}"

        declared: dict[str, Any] = dict(document.get("env") or {})
        for job in EXPECTED_JOBS:
            declared.update(jobs(document)[job].get("env") or {})
            for step in steps(job):
                declared.update(step.get("env") or {})
        assert "NODE_ENV" not in declared

    def test_the_production_bundle_is_asserted(self) -> None:
        """Not setting `NODE_ENV` is not the same as checking that nobody else did.

        `jsxDEV` appears in every development build and in no production one, so the
        frontend job counts it and fails on anything but zero. The *count* is what is
        asserted here, not the word: replacing the `grep` with a hard-coded `count=0` left
        the step's own echo line saying `jsxDEV` and a substring search satisfied. Found by
        mutation, like the probe above.

        `grep -c` is pinned rather than "any way of counting", for the same reason
        :func:`as_regex` refuses a glob it cannot model: this test is the only reader that
        step has, and it can only vouch for a shape it recognises. Re-spell the check and
        this fails, saying so.
        """
        checks = [command for command in run_steps("frontend") if "jsxDEV" in command]
        assert checks, "nothing checks that the build is a production build"
        assert any(re.search(r"grep\s+-c\s+'?jsxDEV'?", command) for command in checks), (
            "the bundle check names jsxDEV without counting it in dist/"
        )

    def test_coverage_is_reported(self) -> None:
        commands = " ".join(run_steps("backend"))
        assert "--cov-report=xml" in commands
        uploads = [step for step in steps("backend") if "upload-artifact" in str(step.get("uses"))]
        assert uploads, "coverage.xml is written and then thrown away"
        assert uploads[0]["with"]["path"] == "backend/coverage.xml"
        assert uploads[0]["with"]["if-no-files-found"] == "error"

    def test_dependencies_are_installed_from_the_lockfile(self) -> None:
        """`uv sync --locked` fails on a `pyproject.toml` edit that never regenerated it."""
        assert any("uv sync --locked" in command for command in run_steps("backend"))
        assert any("npm ci" in command for command in run_steps("frontend"))


class TestTheWorkflowIsDocumented:
    """A reader looking for "what does CI do" should find it in the README."""

    def readme_section(self) -> str:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        found = re.search(r"^## Continuous integration\b(.*?)(?=^## |\Z)", readme, re.M | re.S)
        assert found is not None, "README.md has no `## Continuous integration` section"
        return found.group(1)

    def test_the_readme_names_both_jobs(self) -> None:
        section = self.readme_section()
        for stack in STACKS:
            assert stack in section.lower()

    def test_the_readme_explains_why_the_backend_filter_reaches_into_the_frontend(self) -> None:
        """The one thing a reader would otherwise "tidy up"."""
        assert "SignUpPage.jsx" in self.readme_section()
