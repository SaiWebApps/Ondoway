"""VERIFY must never silently downgrade to a checker that trusts everything.

``build_full_verifier`` resolved its faithfulness checker as
``faithfulness_checker or MockFaithfulnessChecker()``. The Mock trusts the
corpus — it approves every sentence — so a caller that passed nothing got a
verifier whose ``faithfulness_failures`` was ALWAYS empty, and an empty tuple is
indistinguishable from "checked and clean".

That is not hypothetical. ``scripts/tour_build.py`` — the script the owner uses
to read a tour — called it with no checker. So every tour he read came with a
validation report that looked verified when the faithfulness pass had not run at
all. The live API path was never affected (``src/api/dependencies.py`` always
injects the real Haiku checker), which is precisely why this survived: the code
path that lied was the one a human read, not the one under test.

The fix has two halves, and both are asserted here:

1. **No silent substitution.** Passing nothing now RAISES. A caller that truly
   wants no faithfulness pass must say so, in a named argument.
2. **The report tells the truth.** ``ValidationReport.faithfulness_checked``
   distinguishes "checked and clean" from "never checked", so an empty failure
   tuple can no longer be read as a pass.

Pure, hermetic, $0.
"""

from __future__ import annotations

import pytest

from src.tour.compose_gate import build_full_verifier
from src.tour.verify import MockFaithfulnessChecker
from tests.test_claim_dedup import _bbi, _beat, _s, _script_of, _seq


def _fixture():
    beat = _beat("A", ("The arch was built between 1806 and 1808",))
    return _seq(beat), _bbi(beat), _script_of(
        _s("The arch was built between 1806 and 1808.", "A")
    )


def test_missing_checker_does_not_silently_report_verified() -> None:
    """Omitting the checker must RAISE, not quietly hand back a trusting one.

    UNDO TEST: restore ``checker = faithfulness_checker or
    MockFaithfulnessChecker()`` in src/tour/compose_gate.py -> RED.
    """
    seq, bbi, _ = _fixture()
    with pytest.raises(ValueError) as exc:
        build_full_verifier(seq, bbi)
    message = str(exc.value)
    assert "faithfulness" in message.lower(), (
        f"the refusal must name what is missing so a caller can fix it; got: {message}"
    )


def test_an_explicit_opt_out_is_allowed_but_marks_the_report_unverified() -> None:
    """The offline paths may opt out — but the report must SAY it was not checked.

    This is what makes the opt-out honest rather than a renamed default: the
    caller states the intent, and the artifact carries the consequence, so a
    human reading the report can tell the difference between "clean" and "never
    looked".
    """
    seq, bbi, script = _fixture()
    verify = build_full_verifier(seq, bbi, allow_unverified_faithfulness=True)
    report = verify(script)
    assert report.faithfulness_failures == ()
    assert report.faithfulness_checked is False, (
        "an opted-out report claims its faithfulness was checked — that is the "
        "exact ambiguity this field exists to remove"
    )


def test_a_supplied_checker_marks_the_report_checked() -> None:
    """The positive half: with a checker supplied, the flag must be True.

    Without this, setting the field to a constant False would satisfy the test
    above and the flag would carry no information.
    """
    seq, bbi, script = _fixture()
    verify = build_full_verifier(seq, bbi, faithfulness_checker=MockFaithfulnessChecker())
    report = verify(script)
    assert report.faithfulness_checked is True


def test_the_default_validator_licenses_the_days_own_disclosures() -> None:
    """The closed-start line names a place on no stop list; the exclusion
    record's name is the day's own disclosure, never a glue invention. The
    DEFAULT validator — the one ``scripts/tour_build.py`` rides — must carry
    ``disclosed_place_names`` exactly as ``generate()``'s in-line call does:
    one wiring holding the licence while the other flags the honest sentence
    as ``new_proper_noun`` makes the harness fail the very day the feature
    exists for.

    UNDO TEST: drop the disclosed_place_names threading from
    build_full_verifier's default partial -> the hit returns -> RED."""
    seq, bbi, _ = _fixture()
    closed_start = _s(
        "Musee d'Orsay, right here at the start, is closed today, "
        "so the walk goes on without it.",
        "GLUE_STAGING",
        source_type="glue",
    )
    script = _script_of(
        closed_start, _s("The arch was built between 1806 and 1808.", "A")
    )

    bare = build_full_verifier(seq, bbi, allow_unverified_faithfulness=True)
    assert any(
        code.startswith("new_proper_noun:")
        for _sent, code in bare(script).forbidden_phrase_hits
    ), "without the licence the scan must still flag the unknown name"

    licensed = build_full_verifier(
        seq, bbi,
        allow_unverified_faithfulness=True,
        disclosed_place_names=("Musee d'Orsay",),
    )
    assert licensed(script).forbidden_phrase_hits == (), (
        "the day's own disclosure was flagged as an invention — the default "
        "validator dropped the licence generate() carries"
    )


def test_opting_out_and_supplying_a_checker_is_a_contradiction() -> None:
    """Belt and braces: the two arguments must not silently disagree.

    A caller that passes a real checker AND the opt-out has a bug; answering it
    with either behaviour silently would hide which one won.
    """
    seq, bbi, _ = _fixture()
    with pytest.raises(ValueError):
        build_full_verifier(
            seq,
            bbi,
            faithfulness_checker=MockFaithfulnessChecker(),
            allow_unverified_faithfulness=True,
        )
