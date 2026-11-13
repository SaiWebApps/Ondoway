"""Reflection placement.

A reflection is a short synthesis of what has ALREADY been visited, spoken
during a long walking leg to fill the audio deficit. This module owns only
the deterministic PLACEMENT — which legs get one. The reflection TEXT is
composed at COMPOSE time (an LLM synthesis over the visited beats'
key_claims — a template cannot write it) and is gated fail-closed by VERIFY.

Placement rule:

- The leg INTO stop ``k`` (``k >= 1``) is eligible iff its walking time
  exceeds the audio the leg already carries by at least
  ``REFLECTION_MIN_DEFICIT_SECONDS``. Leg audio = the directional corpus
  transit beat ``_build_transit`` would pick when one exists, else the flat
  ~4s glue line (mirrors ``generation._sum_audio``).
- Slot 0 never fires — nothing has been visited yet.
- Longest deficits win; never two consecutive legs; cap = max(1, stops // 2).
"""

from __future__ import annotations

from .contract import BeatSequence, Route
from .generation import _find_directional_transit_beat, _segment_leg_seconds
from .routing import beat_spoken_seconds

# A leg qualifies for a reflection when walking exceeds its audio by this much.
REFLECTION_MIN_DEFICIT_SECONDS: int = 90

# Flat per-glue-sentence estimate — matches generation._sum_audio.
GLUE_SENTENCE_SECONDS: int = 4


def _leg_audio_seconds(beat_sequence: BeatSequence, stop_idx: int) -> int:
    """Audio the leg into ``stop_idx`` already carries.

    Mirrors ``_build_transit``'s pick: a directional corpus transit beat at
    the current stop (origin match) or the previous one (destination match);
    without one, the single glue nav line.
    """
    poi_beats = beat_sequence.poi_beats
    previous, current = poi_beats[stop_idx - 1], poi_beats[stop_idx]
    beat = _find_directional_transit_beat(current, prev_name=previous.poi_name)
    if beat is None:
        beat = _find_directional_transit_beat(previous, dest_name=current.poi_name)
    if beat is None:
        return GLUE_SENTENCE_SECONDS
    seconds = beat_spoken_seconds(beat)
    return seconds if seconds > 0 else GLUE_SENTENCE_SECONDS


def reflection_slots(route: Route, beat_sequence: BeatSequence) -> tuple[int, ...]:
    """The stop indices whose INCOMING leg gets a reflection, ascending.

    Deterministic: deficits sort descending (ties break on the lower
    stop_idx); a slot adjacent to an already-chosen one is skipped so two
    reflections are never spoken on consecutive legs.
    """
    n = len(beat_sequence.poi_beats)
    if n < 2:
        return ()
    deficits: dict[int, int] = {}
    for k in range(1, n):
        deficit = _segment_leg_seconds(route, k) - _leg_audio_seconds(beat_sequence, k)
        if deficit >= REFLECTION_MIN_DEFICIT_SECONDS:
            deficits[k] = deficit
    cap = max(1, n // 2)
    chosen: set[int] = set()
    for k in sorted(deficits, key=lambda idx: (-deficits[idx], idx)):
        if len(chosen) >= cap:
            break
        if (k - 1) in chosen or (k + 1) in chosen:
            continue
        chosen.add(k)
    return tuple(sorted(chosen))


__all__ = [
    "GLUE_SENTENCE_SECONDS",
    "REFLECTION_MIN_DEFICIT_SECONDS",
    "reflection_slots",
]
