"""The unit of work an ingest phase operates on: one chunk of one source.

Docs/ingestion/rebuild-spec.md pins the shape below.

Step 1 landed `Unit` itself — frozen, extra='forbid', built from
a source_id/chunk/text triple plus the same as_of/rights_basis fields a
`model.Source` carries — its derived `.key` and `.custom_id()` (the Batch
API's `custom_id` contract, `^[a-zA-Z0-9_-]{1,64}$`, capped at 60 chars of
key plus a 3-char `-aN` suffix so it never exceeds 64), its `.source()`
factory (stamps a span with the unit's own source_id/chunk/as_of/
rights_basis), and `load_unit()` (reads a real chunk file from disk
byte-for-byte — no whitespace normalization — and raises FileNotFoundError,
never a silent empty unit, when the chunk is missing).

`Unit` deliberately does not re-derive `model.Source`'s own as_of/
rights_basis rules (that would be two places disagreeing eventually): a
`model_validator(mode="after")` builds a throwaway probe `model.Source`
from the unit's own fields and lets ITS validation run, so a bad as_of or
rights_basis surfaces as the same pydantic ValidationError a bad
`model.Source` would raise — through `model.Source`'s public constructor
only, never a private import of `model._validate_as_of`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from src.ingest import model

_VALID_CUSTOM_ID_ATTEMPTS = (1, 2)
_CUSTOM_ID_KEY_CAP = 60


class Unit(BaseModel):
    """One chunk of one source, ready for a decompose/group phase call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    city: str
    source_id: str
    chunk: str
    text: str
    as_of: int | str
    rights_basis: Literal[*model.RIGHTS_BASIS_VALUES]
    path: Path | None = None

    @model_validator(mode="after")
    def _as_of_and_rights_basis_match_a_real_source(self) -> Unit:
        """Delegate to model.Source's own rules rather than re-deriving them."""
        model.Source(
            source_id=self.source_id,
            chunk=self.chunk,
            span="",
            as_of=self.as_of,
            rights_basis=self.rights_basis,
        )
        return self

    @property
    def key(self) -> str:
        """The unit's stable identity: slug(source_id-chunk)."""
        return model.slug(f"{self.source_id}-{self.chunk}")

    def custom_id(self, attempt: int) -> str:
        """The Batch API custom_id for one attempt (1 = first ask, 2 = re-ask).

        Capped at `_CUSTOM_ID_KEY_CAP` characters of `.key` so the result
        always fits the API's 64-character limit regardless of source_id
        length.
        """
        if attempt not in _VALID_CUSTOM_ID_ATTEMPTS:
            raise ValueError(f"attempt must be one of {_VALID_CUSTOM_ID_ATTEMPTS}, got {attempt!r}")
        return f"{self.key[:_CUSTOM_ID_KEY_CAP]}-a{attempt}"

    def source(self, span: str) -> model.Source:
        """Build a model.Source over `span`, carrying the unit's own fields."""
        return model.Source(
            source_id=self.source_id,
            chunk=self.chunk,
            span=span,
            as_of=self.as_of,
            rights_basis=self.rights_basis,
        )


def load_unit(
    books_root: Path,
    *,
    city: str,
    source_id: str,
    chunk: str,
    as_of: int | str,
    rights_basis: str,
) -> Unit:
    """Load one chunk file verbatim into a Unit.

    `books_root` is a city's Books directory (e.g. `Books/new_york`); the
    chunk file is `books_root/source_id/chunk.txt`. Reads the file
    byte-for-byte (no whitespace normalization) and lets a missing file
    raise FileNotFoundError rather than returning a silent empty unit.
    """
    path = Path(books_root) / source_id / f"{chunk}.txt"
    text = path.read_text()
    return Unit(
        city=city,
        source_id=source_id,
        chunk=chunk,
        text=text,
        as_of=as_of,
        rights_basis=rights_basis,
        path=path,
    )
