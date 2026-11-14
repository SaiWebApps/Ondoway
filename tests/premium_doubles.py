"""Hermetic Premium compose doubles.

``OfflinePremiumExecutor`` answers every stop request with the stitched source
sentences it was asked to rewrite, at $0 and with no provider call. The hermetic
money guard in ``tests/conftest.py`` builds it in place of the real Anthropic
executor, and API tests inject it as the compose executor.
"""

from __future__ import annotations

from src.tour.authoring import COMPOSE_MODEL
from src.tour.certification_provider import PhysicalProviderResponse
from src.tour.corpus_places import canonical_bytes
from src.tour.premium_tour import PremiumComposeUnit


class OfflinePremiumExecutor:
    """Returns each stop's stitched source sentences as the provider response."""

    cost_bearing = False
    provider_name = "offline"

    def execute(self, unit: PremiumComposeUnit) -> PhysicalProviderResponse:
        body = canonical_bytes(
            {
                "sentences": [
                    sentence.model_dump(mode="json")
                    for sentence in unit.authorized_request.stitched.script
                ]
            }
        )
        return PhysicalProviderResponse(
            body=body,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            model=COMPOSE_MODEL,
            provider_request_id=f"offline-{unit.stop_index}",
            stop_reason="end_turn",
        )


__all__ = ["OfflinePremiumExecutor"]
