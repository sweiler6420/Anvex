"""How every ``app/data/`` reference file is read. The pattern, in one module.

``CLAUDE.md`` §3 gives this layer one job: parse checked-in reference data and hand back
parsed structures. **No network calls and no database writes** — persisting is a service's
job, and a loader that wrote to Postgres would be a service wearing a loader's name.
ANV-16's politician roster is the first user of the layer, so the shape it needed is
generalised here rather than written once inside it.

**A data file is an envelope, not a bare array.** Every JSON file under ``app/data/`` is an
object with two required keys::

    {"provenance": "where this came from, in prose", "rows": [ {...}, {...} ]}

``provenance`` is required and must be non-empty, and :func:`load_document` refuses the file
without it. That is the point of the key: reference data is the one thing in the repo that
somebody will later mistake for *sourced* data, and the only reliable defence is making an
unattributed file impossible to load. A synthetic fixture says it is synthetic there; a
licensed roster names its licence there. Anything else in the object is ignored, so a file
may carry a ``generated`` date or a version without the loader growing a schema.

**Rows are validated against a pydantic model on the way out.** :func:`load_rows` returns
``list[Model]``, not ``list[dict]`` — which is what turns a malformed roster into a clear
error naming the file, the row index and the field, at *load* time, instead of an
``IntegrityError`` halfway through a bulk insert with half the batch already written. The
model is the ``XCreate`` schema the corresponding service would have accepted, so the seed
path and the (hypothetical) HTTP path agree on what a valid row is by construction.

**Failures are** :class:`SeedDataError`, **a** ``ValueError``. This layer has no Anvex
error vocabulary and deliberately does not import :mod:`app.domain.errors`: a broken
checked-in file is not a request anybody made, it is a repository defect, and it should be
loud everywhere it can happen rather than mapped to a status code. The seed path is reached
from a script and (later) a Celery task, never from a route, so there is no HTTP response
for it to become. A service that ever *does* expose a loader over HTTP is the layer that
translates it, exactly as ``CLAUDE.md`` §4 requires for ``app/utils/`` exceptions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ValidationError

#: The directory every checked-in reference file lives in. Loaders resolve their own file
#: relative to this rather than to the working directory, so a script started from anywhere
#: — or an image whose source is bind-mounted somewhere else — finds it.
DATA_DIR: Final[Path] = Path(__file__).resolve().parent

#: The two keys :func:`load_document` requires of every file.
PROVENANCE_KEY: Final[str] = "provenance"
ROWS_KEY: Final[str] = "rows"


class SeedDataError(ValueError):
    """A checked-in reference file is missing, unparseable, or shaped wrongly.

    A ``ValueError`` rather than an :mod:`app.domain.errors` exception — see the module
    docstring. It carries the offending path so the message names the file even when it
    surfaces three frames away in a script.
    """

    def __init__(self, message: str, *, path: Path | None = None) -> None:
        self.path = path
        super().__init__(f"{path}: {message}" if path is not None else message)


def load_document(path: Path) -> tuple[str, list[Any]]:
    """Read one reference file and return its ``(provenance, rows)``.

    Everything that can be wrong about the *file* is wrong here rather than in a caller:
    it is missing, it is not JSON, it is not an object, it has no attribution, or its
    ``rows`` are not a list.

    :raises SeedDataError: on any of the above.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SeedDataError(f"could not be read ({error.strerror})", path=path) from error

    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise SeedDataError(f"is not valid JSON ({error})", path=path) from error

    if not isinstance(document, dict):
        raise SeedDataError(
            f"must be a JSON object with {PROVENANCE_KEY!r} and {ROWS_KEY!r} keys, "
            f"not a {type(document).__name__}",
            path=path,
        )

    provenance = document.get(PROVENANCE_KEY)
    if not isinstance(provenance, str) or not provenance.strip():
        raise SeedDataError(
            f"has no {PROVENANCE_KEY!r}. Every reference file has to say where its data "
            "came from — synthetic fixtures included.",
            path=path,
        )

    rows = document.get(ROWS_KEY)
    if not isinstance(rows, list):
        raise SeedDataError(
            f"has no {ROWS_KEY!r} list (found {type(rows).__name__})",
            path=path,
        )

    return provenance, rows


def load_rows[ModelT: BaseModel](path: Path, model: type[ModelT]) -> list[ModelT]:
    """Parse ``path`` and validate every row against ``model``.

    The index in the message is the row's position in the file, which is what somebody
    fixing the file actually needs — a pydantic error alone says which *field* is wrong but
    not which of fifty-four rows it was on.

    :raises SeedDataError: the file is unusable (see :func:`load_document`), or any row
        fails validation. The first bad row stops the load: a partially valid roster is not
        a roster, and continuing would hand a service a batch it never asked for.
    """
    _, rows = load_document(path)
    return [_validate(row, model, index=index, path=path) for index, row in enumerate(rows)]


def _validate[ModelT: BaseModel](
    row: Any, model: type[ModelT], *, index: int, path: Path
) -> ModelT:
    if not isinstance(row, dict):
        raise SeedDataError(f"row {index} is a {type(row).__name__}, not an object", path=path)
    try:
        return model.model_validate(row)
    except ValidationError as error:
        raise SeedDataError(
            f"row {index} is not a valid {model.__name__}: {error}", path=path
        ) from error


def provenance_of(path: Path) -> str:
    """The attribution string a reference file carries, for logs and for tests."""
    provenance, _ = load_document(path)
    return provenance


__all__ = [
    "DATA_DIR",
    "PROVENANCE_KEY",
    "ROWS_KEY",
    "SeedDataError",
    "load_document",
    "load_rows",
    "provenance_of",
]
