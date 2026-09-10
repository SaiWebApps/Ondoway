"""Test-only helpers for building and stamping ingest-model records.

Shared by every ingest-model test across steps 1-7 of
specs/2026-09-09-ingest-slice-1 (track A). Created in step 1; read-only
afterwards (decisions.tracks in specs/2026-09-09-ingest-slice-1/run-context.md).

`stamp()` recomputes the three hash-derived fields a beat record carries —
each claim's `verdict.bound_to`, `narration.claims_hash`, and
`narration.verdict.bound_to` — from the record's own claim/narration text,
so a test or fixture never hand-computes a SHA-256 digest. Step 1's
validator does not check these values for correctness (that lands in a
later step); `stamp()` exists so records built here are already consistent
when that check arrives.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.ingest.model import bind, claims_hash


def stamp(beat: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of `beat` with its hash-derived fields recomputed.

    - Each claim's `verdict.bound_to` = bind(claim.text, '\\n'.join(source
      spans), in claim.sources order).
    - `narration.claims_hash` = claims_hash(claims) (resolved claims only,
      in file order).
    - `narration.verdict.bound_to` = bind(narration.text).

    Leaves every other field untouched.
    """
    beat = deepcopy(beat)

    claims = beat.get("claims", [])
    for claim in claims:
        sources = claim.get("sources", [])
        span = "\n".join(s.get("span", "") for s in sources)
        verdict = claim.setdefault("verdict", {})
        verdict["bound_to"] = bind(claim["text"], span)

    narration = beat.get("narration", {})
    if narration:
        narration["claims_hash"] = claims_hash(claims)
        n_verdict = narration.setdefault("verdict", {})
        n_verdict["bound_to"] = bind(narration["text"])

    return beat


def minimal_beat(**overrides: Any) -> dict[str, Any]:
    """A single stamped, schema-valid new-shape beat record.

    For step-1 tests that need a well-formed record to mutate one field on
    — the real guggenheim-example.json fixture (four beats grounded in the
    tracked NYC chunks) lands in step 2. `overrides` replaces top-level
    fields only; nested mutation is the caller's job (mutate the returned
    dict directly, e.g. `beat["claims"][0]["kind"] = "fact"`).
    """
    beat: dict[str, Any] = {
        "beat_id": "new_york/test-poi/a-test-story",
        "city_name": "new_york",
        "poi_name": "Test POI",
        "story_slug": "a-test-story",
        "title": "A test story",
        "beat_type": "anecdote",
        "lenses": ["hidden_history"],
        "sub_location": None,
        "trigger_address": None,
        "claims": [
            {
                "claim_id": "c01",
                "text": "The building was completed in 1959.",
                "kind": "event",
                "status": "resolved",
                "sources": [
                    {
                        "source_id": "lonely-planet-new-york-city",
                        "chunk": "chunk-07-upper-east-side",
                        "span": "Construction was finally completed in 1959.",
                        "as_of": 2023,
                        "rights_basis": "owned_copy",
                        "stated_value": None,
                    }
                ],
                "resolved_value": None,
                "resolution": {"by": "corroborated", "decided_by": None, "decided_at": None},
                "verdict": {"judge_model": "claude-haiku-4-5", "entailed": True, "bound_to": ""},
            },
            {
                "claim_id": "c02",
                "text": "The architect was Frank Lloyd Wright.",
                "kind": "state",
                "status": "resolved",
                "sources": [
                    {
                        "source_id": "frommers-nyc-2024",
                        "chunk": "chunk-05-ch05-uptown",
                        "span": "designed by Frank Lloyd Wright",
                        "as_of": 2024,
                        "rights_basis": "owned_copy",
                        "stated_value": None,
                    }
                ],
                "resolved_value": None,
                "resolution": {"by": "corroborated", "decided_by": None, "decided_at": None},
                "verdict": {"judge_model": "claude-haiku-4-5", "entailed": True, "bound_to": ""},
            },
        ],
        "narration": {
            "text": "A short narration about the test POI.",
            "claims_hash": "",
            "author_model": "claude-opus-5",
            "verdict": {
                "judge_model": "claude-haiku-4-5",
                "sentences_entailed": 1,
                "sentences_total": 1,
                "bound_to": "",
            },
            "flags": [],
        },
        "physical_cues": [],
        "entities": [],
        "narrative_function": "establishing",
        "emotional_register": "neutral",
        "sensory_anchor": False,
        "inline_foreign_phrases": [],
        "pronunciation": None,
        "kid_friendly": "yes",
        "duration_sec": 20,
        "review": {"held": False, "reason": None},
    }
    beat.update(overrides)
    return stamp(beat)
