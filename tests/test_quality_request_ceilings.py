"""The review envelopes' output ceilings — per-model and per-candidate-count.

Nothing else pins the ceilings. Without these checks, reverting the
`_output_ceiling` wiring ships the two-candidate blind review against the flat 16K
ceiling, where a model that spends more output tokens on the same request stops at
`max_tokens` on a calibration request the compose model finishes well inside it;
under zero retries that spend is paid and lost. The models and their multipliers are
read from the model registry. Hermetic: envelopes are built, never sent.
"""

from __future__ import annotations

from src.model_registry import known_model_ids, model_for, output_ceiling_multiplier
from src.tour.quality_requests import (
    ENJOY_MAX_OUTPUT_TOKENS,
    _output_ceiling,
    calibration_request_envelope,
)


def _dense_models() -> list[str]:
    return sorted(m for m in known_model_ids() if output_ceiling_multiplier(m) > 1)


def _plain_models() -> list[str]:
    return sorted(m for m in known_model_ids() if output_ceiling_multiplier(m) == 1)


def test_dense_output_models_get_a_multiplied_ceiling() -> None:
    """A dense model's adaptive thinking spends against the same max_tokens, so at the
    flat ceiling its output truncates mid-JSON. Every registered model's ceiling is
    the base times its registered multiplier, and the registry holds at least one
    dense model and one plain one."""
    assert _dense_models(), "the registry lists no model with a raised output ceiling"
    assert _plain_models(), "the registry lists no model with the base output ceiling"
    for model in known_model_ids():
        multiplier = output_ceiling_multiplier(model)
        assert _output_ceiling(model, 16_000) == 16_000 * multiplier
        assert _output_ceiling(model, 64_000) == 64_000 * multiplier
    for model in _dense_models():
        assert _output_ceiling(model, 16_000) > 16_000


def test_calibration_envelope_carries_the_per_model_ceiling() -> None:
    """The sealed request itself must carry the right ceiling — the constant alone
    proves nothing if the envelope builder stops calling it. A plain model's envelope
    stays at 16K (a paid calibration receipt reseeds only against an identical
    envelope); a dense model's is multiplied."""
    plain, dense = _plain_models()[0], _dense_models()[0]
    _, plain_request = calibration_request_envelope((), (), model=plain, effort=None)
    _, dense_request = calibration_request_envelope((), (), model=dense, effort=None)
    assert plain_request["max_tokens"] == ENJOY_MAX_OUTPUT_TOKENS == 16_000
    assert dense_request["max_tokens"] == ENJOY_MAX_OUTPUT_TOKENS * output_ceiling_multiplier(
        dense
    )


def test_envelope_sends_effort_only_when_given() -> None:
    """A request built with an effort level carries it in ``output_config``; one built
    without leaves the key out, so a request recorded without one rebuilds byte for
    byte."""
    model = model_for("fact_review")
    _, with_effort = calibration_request_envelope((), (), model=model, effort="high")
    _, without = calibration_request_envelope((), (), model=model, effort=None)
    assert with_effort["output_config"]["effort"] == "high"
    assert "effort" not in without["output_config"]
    assert with_effort["output_config"]["format"] == without["output_config"]["format"]
