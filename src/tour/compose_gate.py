"""VERIFY — the merged anti-hallucination report over an authored Script.

What survives of the old whole-tour gate. The recompose-once ladder
(``compose_and_verify``/``serve_or_block``) and its whole-tour repairs died with
``compose.py``: the one engine authors per stop, so a failure is refused (or
repaired) at the stop that produced it, not re-run over the whole tour.

``build_full_verifier`` merges a caller-selected base validator with provenance,
faithfulness, and coverage checks. The legacy lane defaults to
``validation.validate_script``; certification supplies its structural-only
validator so prose is judged by meaning rather than lexical vetoes.
``ComposeVerificationError`` is the refusal the persisted compose endpoint
surfaces as its 422.
"""

from __future__ import annotations

import functools
from collections.abc import Callable

from .claim_dedup import verify_claim_coverage
from .contract import BeatRef, BeatSequence, Script, ValidationReport
from .validation import validate_script
from .verify import (
    FaithfulnessChecker,
    MockFaithfulnessChecker,
    verify_faithfulness,
    verify_provenance,
)


class ComposeVerificationError(Exception):
    """Raised when a Script still fails VERIFY after the bounded recompose.

    Carries the final report and the attempt count so the caller can surface
    why the flavour was refused.
    """

    def __init__(self, report: ValidationReport, attempts: int):
        self.report = report
        self.attempts = attempts
        super().__init__(
            f"Script failed VERIFY after {attempts} compose attempt(s): "
            f"{len(report.untraceable_sentences)} untraceable, "
            f"{len(report.forbidden_phrase_hits)} forbidden, "
            f"{len(report.provenance_failures)} provenance, "
            f"{len(report.faithfulness_failures)} faithfulness, "
            f"{len(report.coverage_failures)} coverage"
        )


def build_full_verifier(
    beat_sequence: BeatSequence,
    beats_by_id: dict[str, BeatRef],
    *,
    chunk_text_by_slug: dict[str, str] | None = None,
    faithfulness_checker: FaithfulnessChecker | None = None,
    allow_unverified_faithfulness: bool = False,
    expected_claim_ids: set[tuple[str, int]] | None = None,
    spine_area: str | None = None,
    disclosed_place_names: tuple[str, ...] = (),
    base_validator: Callable[[Script, BeatSequence], ValidationReport] | None = None,
) -> Callable[[Script], ValidationReport]:
    """A ``verify(script)`` that merges the VERIFY checks into one report.

    ``chunk_text_by_slug`` empty → provenance is a no-op (corpus not backfilled).
    ``expected_claim_ids`` (from ``claims_realized_by`` on the PRE-compose stitch)
    enables the content-loss / coverage check — omit it and coverage is a no-op.

    FAITHFULNESS FAILS CLOSED. This line used to read ``faithfulness_checker or
    MockFaithfulnessChecker()``, so a caller who passed nothing silently got a
    checker that approves every sentence — and an empty ``faithfulness_failures``
    then read exactly like "checked and clean". ``scripts/tour_build.py``, the
    script the owner reads tours with, called it that way, so every tour he read
    carried a report that looked verified having verified nothing. The live API
    was never affected (``src/api/dependencies.py`` always injects the real Haiku
    checker), which is why it survived: the lying path was the one a human read,
    not the one under test.

    An offline caller may still skip the pass, but must SAY SO with
    ``allow_unverified_faithfulness=True`` — and the resulting report carries
    ``faithfulness_checked=False`` so the artifact itself records that nothing
    was verified. Passing both a checker and the opt-out is a contradiction and
    raises rather than silently picking a winner.
    """

    # THE SPINE IS PART OF THE TOUR'S OWN VOCABULARY. The opener says where the
    # walker is standing, and the neighbourhood it names is the engine's choice
    # from the corpus — often one no seated stop belongs to, because a walk starts
    # in one quarter and ends in another. Without it the invention scan reads
    # "You're starting in Opera-Garnier" as glue asserting a place out of nowhere
    # and fails a correct tour, which is worse than not checking: a report that
    # cries wolf gets ignored.
    #
    # An injected `base_validator` (tests, certification) is left exactly as it
    # was, so this cannot change what those callers measure.
    #
    # ``disclosed_place_names`` — the names the route's own disclosure records
    # carry (clock exclusions) — rides the default the same way the spine does:
    # the closed-start line names a place on no stop list, and a default that
    # drops the licence fails the honest sentence as an invention.
    validator = base_validator or functools.partial(
        validate_script,
        spine_area=spine_area,
        disclosed_place_names=disclosed_place_names,
    )
    if faithfulness_checker is not None and allow_unverified_faithfulness:
        raise ValueError(
            "build_full_verifier got both a faithfulness_checker and "
            "allow_unverified_faithfulness=True. These contradict; pass exactly one."
        )
    if faithfulness_checker is None and not allow_unverified_faithfulness:
        raise ValueError(
            "build_full_verifier requires a faithfulness_checker. There is no "
            "default: substituting the trusting MockFaithfulnessChecker is what "
            "made unverified tours read as verified. To skip the pass "
            "deliberately, pass allow_unverified_faithfulness=True — the report "
            "will then record faithfulness_checked=False."
        )
    checked = faithfulness_checker is not None
    # Spelled as if/else, not `or`. The guard clauses above have already rejected
    # a bare None, so the trusting checker is reachable ONLY through the explicit
    # opt-out — but `faithfulness_checker or MockFaithfulnessChecker()` is the
    # exact line that hid this defect for months, and a structural sweep for
    # stand-ins flags it identically either way. Keep the intent legible.
    if faithfulness_checker is not None:
        checker = faithfulness_checker
    else:
        checker = MockFaithfulnessChecker()
    chunks = chunk_text_by_slug or {}

    def verify(script: Script) -> ValidationReport:
        base = validator(script, beat_sequence)
        # No chunk library loaded -> provenance is a genuine no-op. Since the
        # Step-4.0 backfill, live beats DO carry source_passage/chunk_slug;
        # without this guard every one of them would fail at 0.0 against an
        # empty dict. A missing slug only means something when chunks exist.
        provenance = tuple(verify_provenance(beat_sequence, chunks)) if chunks else ()
        coverage = (
            verify_claim_coverage(script, expected_claim_ids, beats_by_id)
            if expected_claim_ids
            else ()
        )
        return base.model_copy(
            update={
                "provenance_failures": provenance,
                "faithfulness_failures": tuple(
                    verify_faithfulness(script, beats_by_id, checker)
                ),
                "faithfulness_checked": checked,
                "coverage_failures": coverage,
            }
        )

    return verify
