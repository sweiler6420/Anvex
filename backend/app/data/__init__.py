"""Checked-in reference data and the loaders that read it (``CLAUDE.md`` §3).

A loader parses a file that ships with the repository and returns parsed structures. **No
network calls, no database writes** — persisting is a service's job. What a loader owns is
the answer to "is this file usable", and it answers it at load time so nothing downstream
has to.

Read :mod:`app.data.loader` for the pattern every file here follows: a JSON object with a
required ``provenance`` string and a ``rows`` list, validated row by row against the
resource's ``XCreate`` schema. :mod:`app.data.politicians` is the worked example.

Nothing is re-exported from here. A caller reaches for the dataset it wants
(``from app.data.politicians import load_politicians``), so importing one never parses
another.
"""
