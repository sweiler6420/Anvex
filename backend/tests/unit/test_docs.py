"""Drift tests for the documentation (ANV-39).

Documentation rots silently. Nothing fails when a file named in a walkthrough is renamed,
when a route is added and the API surface table is not, when a `TODO` the docs promise is
still there gets deleted, or when an ADR is written and never listed. These tests are the
mechanism that makes those things fail, and they are the deliverable of the docs ticket as
much as the prose is.

What is checked, and against what:

* **Every repository path named in the docs exists.** Discovered from the inline code spans
  in the documents themselves — not from a list here — so a document that starts naming a
  new file gets it checked without anyone remembering to.
* **Every relative markdown link resolves**, so the cross-references between the documents
  cannot rot into each other.
* **The API surface table in `docs/architecture.md` equals the application's own route
  table**, in *both* directions, read out of the live OpenAPI document. A route added
  without a row, or a row naming a route that does not exist, fails here.
* **The seven layers the walkthrough claims to follow are real directories**, and each file
  it lists genuinely lives in the layer it is listed under.
* **Every `TODO(ANV-…)` the docs claim exists is still in the file they name**, and — the
  half that matters more — every such marker under `app/` and `infra/` appears in the
  limitations table. A gap the docs stop mentioning is worse than one they never mentioned.
* **The ADRs are numbered sequentially from 0001 with no gaps or duplicates**, carry the
  four required sections in order, and agree with their index in both directions.

The rule these follow, learned by ANV-38 and again by ANV-40: **assert the mechanism, not
the vocabulary.** Nothing here matches a sentence. The route check parses the OpenAPI
document, the marker check reads the annotated file, the layer check stats a directory, and
the path check resolves a real path — so a document that keeps the words and loses the
substance still fails.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

from app.main import create_app
from app.settings import REPO_ROOT

# ---------------------------------------------------------------------------------------
# The documents under test
# ---------------------------------------------------------------------------------------

ARCHITECTURE: Final[Path] = REPO_ROOT / "docs" / "architecture.md"
ADR_DIR: Final[Path] = REPO_ROOT / "docs" / "adr"
BACKEND_DOCS: Final[Path] = REPO_ROOT / "backend" / "docs"
APP_DIR: Final[Path] = REPO_ROOT / "backend" / "app"
INFRA_DIR: Final[Path] = REPO_ROOT / "backend" / "infra"
README: Final[Path] = REPO_ROOT / "README.md"

RUNBOOK: Final[Path] = BACKEND_DOCS / "runbook.md"
TESTING: Final[Path] = BACKEND_DOCS / "testing.md"
WALKTHROUGH: Final[Path] = BACKEND_DOCS / "adding-an-endpoint.md"
ADR_INDEX: Final[Path] = ADR_DIR / "README.md"

#: The documents ANV-39 owns, plus the README that points at them. Everything below is
#: derived from these files; nothing restates their contents.
FIXED_DOCS: Final[tuple[Path, ...]] = (README, ARCHITECTURE, RUNBOOK, TESTING, WALKTHROUGH)


def adr_files() -> list[Path]:
    """Every numbered record in ``docs/adr/``, in filename order.

    The index is deliberately *not* the source of this list — an ADR that exists and is
    unlisted has to be discoverable, or the "listed in both directions" test below could
    only ever fail in one of them.
    """
    return sorted(ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md"))


def documents() -> list[Path]:
    return [*FIXED_DOCS, ADR_INDEX, *adr_files()]


def text_of(path: Path) -> str:
    return path.read_text(encoding="utf-8")


#: A fenced block, so its contents are not mistaken for prose. Mermaid diagrams, JSON
#: samples and directory trees all live in these and none of them are code spans.
FENCE: Final[re.Pattern[str]] = re.compile(r"^```.*?^```", re.M | re.S)

#: An inline code span. Deliberately single-backtick and single-line: a path is never
#: written any other way in these documents.
CODE_SPAN: Final[re.Pattern[str]] = re.compile(r"`([^`\n]+)`")


def prose_of(path: Path) -> str:
    """The document with its fenced blocks removed."""
    return FENCE.sub("", text_of(path))


def code_spans(path: Path) -> list[str]:
    return CODE_SPAN.findall(prose_of(path))


# ---------------------------------------------------------------------------------------
# Paths named in the documents
# ---------------------------------------------------------------------------------------

#: A code span is treated as a repository path when its first segment is one of these. The
#: set is what keeps `/v1/stocks`, `redis://redis:6379/0`, `minio/minio` and `anvex/api:dev`
#: out — each of them contains a slash and none of them is a file.
TOP_LEVEL: Final[frozenset[str]] = frozenset(
    {"backend", "frontend", "docs", "scripts", ".github", "app", "tests"}
)

#: `app/…` and `tests/…` are written backend-relative in `backend/docs/`, because that is
#: how `CLAUDE.md` §3 spells a layer. Everything else is repository-root-relative.
BACKEND_RELATIVE: Final[frozenset[str]] = frozenset({"app", "tests"})

#: Characters that mean the span is a pattern or a placeholder rather than a path:
#: `docs/adr/**`, `scripts/<name>.ps1`, `settings/config.{env}.json`.
NOT_A_PATH: Final[str] = "*<>{}|? "


def looks_like_a_repo_path(span: str) -> bool:
    if any(character in span for character in NOT_A_PATH):
        return False
    trimmed = span.removeprefix("./").rstrip("/")
    if not trimmed or trimmed.startswith("/"):
        return False
    return trimmed.split("/")[0] in TOP_LEVEL


def resolve(span: str) -> Path:
    trimmed = span.removeprefix("./").rstrip("/")
    root = REPO_ROOT / "backend" if trimmed.split("/")[0] in BACKEND_RELATIVE else REPO_ROOT
    return root / trimmed


def documented_paths() -> list[tuple[str, str]]:
    """``(document name, path)`` for every repository path the documents name."""
    found: set[tuple[str, str]] = set()
    for document in documents():
        for span in code_spans(document):
            if looks_like_a_repo_path(span):
                found.add((document.name, span.removeprefix("./").rstrip("/")))
    return sorted(found)


#: A markdown link target: `](…)`. Anchors, mail and absolute URLs are somebody else's
#: problem; a relative one is ours.
LINK: Final[re.Pattern[str]] = re.compile(r"\]\(([^)\s]+)\)")


def documented_links() -> list[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for document in documents():
        for target in LINK.findall(text_of(document)):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            found.add((str(document.relative_to(REPO_ROOT)).replace("\\", "/"), target))
    return sorted(found)


class TestEveryPathTheDocsNameExists:
    """A renamed file makes the document that names it wrong, silently, forever."""

    def test_the_documents_themselves_are_present(self) -> None:
        for document in documents():
            assert document.is_file(), f"{document} is missing"

    def test_the_scan_found_something(self) -> None:
        """A guard: an extraction bug would otherwise make every case below vacuous."""
        found = documented_paths()
        assert len(found) > 50, f"only {len(found)} paths extracted - the scan is broken"
        assert {document for document, _ in found} >= {document.name for document in FIXED_DOCS}, (
            "at least one document contributed no paths at all"
        )

    @pytest.mark.parametrize(("document", "path"), documented_paths())
    def test_the_path_exists(self, document: str, path: str) -> None:
        resolved = resolve(path)
        assert resolved.exists(), f"{document} names `{path}`, which is not in the repository"

    @pytest.mark.parametrize(("document", "target"), documented_links())
    def test_every_relative_link_resolves(self, document: str, target: str) -> None:
        """Cross-references between the documents, and out of them into the code."""
        source = REPO_ROOT / document
        destination = (source.parent / target.split("#", 1)[0]).resolve()
        assert destination.exists(), f"{document} links to `{target}`, which does not exist"


# ---------------------------------------------------------------------------------------
# The API surface table against the application
# ---------------------------------------------------------------------------------------

#: A row of the API surface table: `| `GET` | `/v1/stocks` | … |`.
ROUTE_ROW: Final[re.Pattern[str]] = re.compile(
    r"^\|\s*`(GET|POST|PUT|PATCH|DELETE)`\s*\|\s*`(/[^`]*)`\s*\|", re.M
)


def documented_routes() -> set[tuple[str, str]]:
    return {(method, path) for method, path in ROUTE_ROW.findall(text_of(ARCHITECTURE))}


def application_routes() -> set[tuple[str, str]]:
    """Every operation in the live OpenAPI document.

    Read from the schema rather than from ``app.routes``: the schema is what a client is
    generated from, and it is the artefact the table is a copy of.
    """
    schema = create_app().openapi()
    return {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method in operations
        if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}
    }


class TestTheDocumentedApiSurfaceIsTheRealOne:
    """`docs/architecture.md` §2 claims to list every operation. This is that claim."""

    def test_the_table_was_parsed(self) -> None:
        assert len(documented_routes()) > 10, "the API surface table did not parse"

    def test_every_documented_route_exists(self) -> None:
        missing = sorted(documented_routes() - application_routes())
        assert not missing, f"documented but not mounted: {missing}"

    def test_every_mounted_route_is_documented(self) -> None:
        """The direction that catches the common mistake: shipping a route and no docs."""
        missing = sorted(application_routes() - documented_routes())
        assert not missing, (
            f"mounted but absent from `docs/architecture.md`: {missing}. "
            f"Add a row to the API surface table."
        )

    def test_the_error_status_table_matches_the_middleware(self) -> None:
        """The status map is the public contract, so a change to it is a breaking change."""
        from app.middleware.errors import ERROR_STATUS_CODES

        documented = dict(
            re.findall(r"^\|\s*`(\w+)`\s*\|\s*(\d{3})\s*\|", text_of(ARCHITECTURE), re.M)
        )
        actual = {error.__name__: str(status) for error, status in ERROR_STATUS_CODES.items()}
        assert documented == actual


# ---------------------------------------------------------------------------------------
# The seven layers of the walkthrough
# ---------------------------------------------------------------------------------------

#: A row of the walkthrough's layer table: `| 3 | `app/domain/` | `backend/app/domain/x.py` |`.
LAYER_ROW: Final[re.Pattern[str]] = re.compile(
    r"^\|\s*(\d+)\s*\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|", re.M
)

LAYER_HEADING: Final[str] = "## The seven layers, in order"


def layer_rows() -> list[tuple[int, str, str]]:
    """The layer table, and only it — the section is located by its heading."""
    body = text_of(WALKTHROUGH)
    start = body.index(LAYER_HEADING)
    end = body.index("\n## ", start + len(LAYER_HEADING))
    return [(int(index), layer, file) for index, layer, file in LAYER_ROW.findall(body[start:end])]


class TestTheWalkthroughFollowsRealLayers:
    """It claims to follow one feature through seven layers. Seven real ones."""

    def test_there_are_exactly_seven_numbered_in_order(self) -> None:
        rows = layer_rows()
        assert [index for index, _, _ in rows] == [1, 2, 3, 4, 5, 6, 7]

    def test_every_layer_is_a_distinct_directory_under_app(self) -> None:
        layers = [layer for _, layer, _ in layer_rows()]
        assert len(set(layers)) == len(layers), f"a layer is listed twice: {layers}"
        for layer in layers:
            resolved = resolve(layer)
            assert resolved.is_dir(), f"`{layer}` is not a directory"
            assert resolved.parent == APP_DIR, f"`{layer}` is not a layer of `app/`"

    def test_every_file_lives_in_the_layer_it_is_listed_under(self) -> None:
        """The assertion a path-existence check alone would not make.

        `app/api/v1/watchlists.py` is *under* `app/api/` rather than in it, so this is a
        containment test — but listing a service file under `app/repos/` still fails.
        """
        for _, layer, file in layer_rows():
            resolved = resolve(file)
            assert resolved.is_file(), f"`{file}` is not a file"
            assert resolved.is_relative_to(resolve(layer)), (
                f"`{file}` is listed under `{layer}` and does not live there"
            )


# ---------------------------------------------------------------------------------------
# The `TODO(ANV-…)` markers
# ---------------------------------------------------------------------------------------

MARKER: Final[re.Pattern[str]] = re.compile(r"TODO\(ANV-[a-z0-9-]+\)")

LIMITATIONS_HEADING: Final[str] = "## 6. Known limitations"

#: A limitations row: `| what | `where` | `TODO(ANV-x)` |`, with `—` where there is no
#: marker. Only the last two columns are read; the first is prose.
LIMITATION_ROW: Final[re.Pattern[str]] = re.compile(r"^\|.*\|\s*`([^`]+)`\s*\|\s*(\S+)\s*\|$", re.M)


def limitation_rows() -> list[tuple[str, str]]:
    body = text_of(ARCHITECTURE)
    start = body.index(LIMITATIONS_HEADING)
    end = body.index("\n## ", start + len(LIMITATIONS_HEADING))
    return LIMITATION_ROW.findall(body[start:end])


def markers_in_source() -> dict[str, list[str]]:
    """Every `TODO(ANV-…)` under `app/` and `infra/`, mapped to the files carrying it.

    Tests are excluded deliberately: a test that *asserts* a marker exists quotes it, and
    counting that as a second home would make the table's other direction unfalsifiable.
    """
    found: dict[str, list[str]] = {}
    for root in (APP_DIR, INFRA_DIR):
        for source in sorted(root.rglob("*")):
            if not source.is_file() or source.suffix not in {".py", ".tf", ".json"}:
                continue
            for marker in MARKER.findall(source.read_text(encoding="utf-8")):
                found.setdefault(marker, []).append(
                    str(source.relative_to(REPO_ROOT)).replace("\\", "/")
                )
    return found


class TestTheKnownLimitationsAreStillTrue:
    """A gap the docs stop mentioning is worse than one they never mentioned."""

    def test_the_table_was_parsed(self) -> None:
        rows = limitation_rows()
        assert len(rows) >= 8, f"only {len(rows)} limitations parsed - the table changed shape"

    def test_the_source_still_carries_markers(self) -> None:
        """A guard, so the two directions below cannot both pass over an empty set."""
        assert markers_in_source(), "no `TODO(ANV-…)` found in the source at all"

    @pytest.mark.parametrize(
        ("where", "marker"),
        [
            (where, marker)
            for where, marker in limitation_rows()
            if MARKER.fullmatch(marker.strip("`"))
        ],
    )
    def test_a_documented_marker_is_still_in_the_file_it_names(
        self, where: str, marker: str
    ) -> None:
        """Not "the marker is somewhere in the repository" — in *that* file.

        ANV-40 lost this exact mutation twice: a marker deleted beside the value it
        annotates while a paragraph elsewhere still quoted it.
        """
        marker = marker.strip("`")
        source = resolve(where)
        assert source.is_file(), f"the limitations table names `{where}`, which is missing"
        assert marker in source.read_text(encoding="utf-8"), (
            f"`docs/architecture.md` says `{where}` carries `{marker}`; it does not"
        )

    def test_every_marker_in_the_source_is_documented(self) -> None:
        """The direction that catches a new gap nobody wrote down."""
        documented = {
            marker.strip("`")
            for _, marker in limitation_rows()
            if MARKER.fullmatch(marker.strip("`"))
        }
        assert set(markers_in_source()) == documented, (
            "the `TODO(ANV-…)` markers in `app/`/`infra/` and the ones in "
            "`docs/architecture.md` §6 disagree"
        )


# ---------------------------------------------------------------------------------------
# The ADRs
# ---------------------------------------------------------------------------------------

#: The four sections every record carries, in this order. A record missing "Consequences"
#: is a decision with no cost written down, which is the half people skip.
ADR_SECTIONS: Final[tuple[str, ...]] = ("## Status", "## Context", "## Decision", "## Consequences")

ADR_TITLE: Final[re.Pattern[str]] = re.compile(r"^# ADR-(\d{4}) — (.+)$", re.M)

#: A row of the index table: `| 0001 | [Title](./0001-slug.md) | Accepted |`.
INDEX_ROW: Final[re.Pattern[str]] = re.compile(
    r"^\|\s*(\d{4})\s*\|\s*\[[^\]]+\]\(\./([^)]+)\)", re.M
)


def index_rows() -> list[tuple[str, str]]:
    return INDEX_ROW.findall(text_of(ADR_INDEX))


class TestTheAdrsAreWellFormed:
    """Structure, not prose. An ADR nobody can find is an ADR nobody reads."""

    def test_there_are_records(self) -> None:
        assert adr_files(), "docs/adr/ holds no numbered records"

    def test_the_numbering_is_sequential_with_no_gaps_or_duplicates(self) -> None:
        numbers = [int(path.name[:4]) for path in adr_files()]
        assert numbers == list(range(1, len(numbers) + 1)), (
            f"ADR numbers are {numbers}; they must run 1..n with no gaps or duplicates"
        )

    @pytest.mark.parametrize("record", adr_files(), ids=lambda path: path.name)
    def test_the_heading_number_matches_the_filename(self, record: Path) -> None:
        found = ADR_TITLE.search(text_of(record))
        assert found is not None, f"{record.name} has no `# ADR-NNNN — Title` heading"
        assert found.group(1) == record.name[:4], f"{record.name} is headed ADR-{found.group(1)}"
        assert found.group(2).strip(), f"{record.name} has an empty title"

    @pytest.mark.parametrize("record", adr_files(), ids=lambda path: path.name)
    def test_the_four_sections_are_present_and_in_order(self, record: Path) -> None:
        body = text_of(record)
        positions = []
        for section in ADR_SECTIONS:
            assert f"\n{section}\n" in body, f"{record.name} has no `{section}` section"
            positions.append(body.index(f"\n{section}\n"))
        assert positions == sorted(positions), (
            f"{record.name}'s sections are out of order; the order is {ADR_SECTIONS}"
        )

    @pytest.mark.parametrize("record", adr_files(), ids=lambda path: path.name)
    def test_every_section_has_a_body(self, record: Path) -> None:
        """A heading with nothing under it is the shape a half-written ADR takes."""
        body = text_of(record)
        for section, following in zip(ADR_SECTIONS, [*ADR_SECTIONS[1:], None], strict=True):
            start = body.index(f"\n{section}\n") + len(section) + 2
            end = body.index(f"\n{following}\n") if following else len(body)
            assert len(body[start:end].strip()) > 40, (
                f"{record.name}'s `{section}` section is empty or a stub"
            )

    def test_every_record_is_listed_in_the_index(self) -> None:
        listed = {file for _, file in index_rows()}
        assert {path.name for path in adr_files()} == listed, (
            "docs/adr/README.md and the files in docs/adr/ disagree"
        )

    def test_the_index_numbers_match_the_files_they_link_to(self) -> None:
        for number, file in index_rows():
            assert file.startswith(number), f"the index lists {number} pointing at {file}"
