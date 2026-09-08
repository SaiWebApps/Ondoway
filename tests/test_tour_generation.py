"""Phase 3 — generation.py: deterministic structural assembly + glue routing.

Tests use a MockGlueClient — the LLM is never invoked. The point of
generation is the deterministic shape of the Script; the only LLM-driven
fragments are glue sentences whose category is asserted via mock calls.
"""

from __future__ import annotations

import pytest

from src.tour.contract import (
    POI,
    BeatRef,
    BeatSequence,
    PhysicalCue,
    POIBeats,
    Route,
    Script,
    ScriptPOI,
    Sentence,
    TourInput,
    TransitSegment,
    ValidationReport,
)
from src.tour.generation import (
    FORBIDDEN_PHRASES,
    GLUE_LABELS,
    GLUE_NAV,
    GLUE_PACING,
    GLUE_REFLECTION,
    SENSORY_INVITATION,
    SPOKEN_WPM,
    SYNTHESIZED_OPENER,
    _area_article,
    _find_directional_transit_beat,
    _nav_walk_minutes,
    _segment_leg_seconds,
    _sum_audio,
    _synth_first_leg_text,
    _template_nav,
    generate,
    is_walk_concurrent,
    split_sentences,
    transit_class_beat_ids,
)
from src.tour.glue_client import NO_GLUE_SENTINEL, MockGlueClient
from src.tour.options import build_route_option
from src.tour.selection import CorpusSnapshot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _poi(pid: str, name: str | None = None, *, tier: int = 5) -> POI:
    return POI(
        id=pid,
        name=name or pid,
        tier=tier,
        poi_role="stop",
        lat=48.8555,
        lng=2.3656,
        areas=("Le Marais",),
    )


def _beat(
    bid: str,
    poi_id: str,
    *,
    body: str = "",
    nf: str | None = None,
    sub: str | None = None,
    addr: str | None = None,
    lenses: tuple[str, ...] = (),
    word_count: int | None = None,
) -> BeatRef:
    return BeatRef(
        id=bid,
        poi_id=poi_id,
        sub_location=sub,
        trigger_address=addr,
        narrative_function=nf,
        word_count=word_count if word_count is not None else len(body.split()),
        lenses=lenses,
        script_body=body or None,
    )


def _input(round_trip: bool = True, duration: int = 60) -> TourInput:
    return TourInput(
        start=(48.8555, 2.3656),
        duration_min=duration,
        city_slug="paris",
        round_trip=round_trip,
    )


def _route(
    pois: tuple[POI, ...],
    duration_min: int = 60,
    visit_seconds: dict[str, int] | None = None,
) -> Route:
    """Minimal Route: pretend each transit walks 60s for cap-budget arithmetic.

    ``visit_seconds`` is how long this visitor spends AT each stop, priced in
    selection. Omitted, it is empty and every stop falls back to the length of
    its own narration — which is what an unpriced corpus produces.
    """
    transits = tuple(
        TransitSegment(
            from_poi_id=None if i == 0 else pois[i - 1].id,
            to_poi_id=p.id,
            distance_m=100.0,
            walk_seconds=60,
        )
        for i, p in enumerate(pois)
    )
    walk = sum(t.walk_seconds for t in transits)
    return Route(
        pois=pois,
        transits=transits,
        total_walk_distance_m=100.0 * len(pois),
        total_walk_seconds=walk,
        spine_area="Le Marais",
        target_dwell_seconds=duration_min * 30,
        err_short_total_seconds=int(duration_min * 60 * 0.83),
        planned_visit_seconds=visit_seconds or {},
    )


# ---------------------------------------------------------------------------
# Sentence splitter
# ---------------------------------------------------------------------------


def test_split_sentences_basic():
    out = split_sentences("Henri IV built it. The square opened in 1612. Crowds came.")
    assert out == [
        "Henri IV built it.",
        "The square opened in 1612.",
        "Crowds came.",
    ]


def test_split_sentences_keeps_no_dot_address():
    out = split_sentences("Look at no. 6 across the way. Hugo lived there.")
    assert out == [
        "Look at no. 6 across the way.",
        "Hugo lived there.",
    ]


def test_split_sentences_handles_quoted_material():
    out = split_sentences('He wrote "It has to hurt." That was 1752. Brice argued his case.')
    assert "Brice argued his case." in out


def test_split_sentences_keeps_name_initials():
    """Personal-name initials (J.-B., J. M. W.) are not sentence boundaries — the
    splitter must not read "…a portrait by J.-B." as a stuttered sentence (TTS
    artifact, User-agent finding)."""
    assert split_sentences("A portrait by J.-B. Vanloo hangs here.") == [
        "A portrait by J.-B. Vanloo hangs here."
    ]
    assert split_sentences("Boileau, Racine, and J.-B. Lully rehearsed here.") == [
        "Boileau, Racine, and J.-B. Lully rehearsed here."
    ]
    assert split_sentences("She met J. M. W. Turner in London.") == [
        "She met J. M. W. Turner in London."
    ]
    # A real boundary AFTER an initials phrase still splits.
    assert split_sentences("A work by J.-B. Vanloo. It hangs upstairs.") == [
        "A work by J.-B. Vanloo.",
        "It hangs upstairs.",
    ]


def test_split_sentences_regnal_numerals_end_the_sentence():
    """A single-letter Roman REGNAL numeral ("Elizabeth I.", "Louis X.", "World
    War I.") DOES end its sentence and must NOT be re-glued forward — else
    "…by Elizabeth I. The abbey…" reads as one run-on (the mirror of the
    initials-stutter bug, and the cause of London beat truncations). It is told
    apart from a name-INITIAL of the same shape ("by C. W. Stephens") by its
    neighbours: a regnal follows a full word and is not followed by an initial."""
    assert split_sentences("It was made a peculiar by Elizabeth I. The abbey grew.") == [
        "It was made a peculiar by Elizabeth I.",
        "The abbey grew.",
    ]
    assert split_sentences("She obeyed her husband Louis X. The scandal spread.") == [
        "She obeyed her husband Louis X.",
        "The scandal spread.",
    ]
    assert split_sentences("The arch commemorated World War I. It ended in 1967.") == [
        "The arch commemorated World War I.",
        "It ended in 1967.",
    ]
    # ACCEPTED tradeoff: a regnal terminal before a NON-opener-initial sentence
    # ("World War I. Waterloo…") stays glued — a minor missing pause, deliberately
    # preferred over ever risking a mid-name break. Documented, not a bug.
    assert split_sentences("It marked World War I. Waterloo closed later.") == [
        "It marked World War I. Waterloo closed later."
    ]
    # ...but a single-letter initial CHAIN that merely opens on a Roman-shaped
    # letter ("C. W. Stephens") is still ONE sentence — the next token is an initial.
    assert split_sentences("It was designed by C. W. Stephens for Harrod.") == [
        "It was designed by C. W. Stephens for Harrod."
    ]
    assert split_sentences("I. M. Pei drew the plan. It opened in 1989.") == [
        "I. M. Pei drew the plan.",
        "It opened in 1989.",
    ]
    # CRITICAL: a single-letter MIDDLE INITIAL that IS a Roman letter (C/D/I/L/M/V/X)
    # followed by a SURNAME must NOT be split — "John D. Rockefeller" (~15 such names
    # live in the NY corpus) must stay whole, or TTS breaks mid-name. The regnal
    # split fires ONLY when the next word is a sentence-opener function word, never a
    # surname. (Regression the judge caught in an earlier "neither-neighbour-initial"
    # rule that split C/D/I/L/M/V/X middle initials.)
    for name in ("John D. Rockefeller", "Franklin D. Roosevelt", "Arthur C. Clarke",
                 "William M. Tweed", "Daniel D. Tompkins"):
        assert split_sentences(f"A plaque honours {name} here. Fans visit.") == [
            f"A plaque honours {name} here.",
            "Fans visit.",
        ], name


def test_split_sentences_never_breaks_mid_name_across_corpus():
    """Runtime corpus guard (judge-mandated): ``split_sentences`` must NEVER emit a
    piece ending in a single-letter initial immediately followed by a piece starting
    with a capitalized NON-opener word — that is a mid-name break ("John D." |
    "Rockefeller's firm") that TTS would read with a wrong full stop. Structurally
    distinct from the data-integrity fragment guard (which checks stored whole
    bodies, not runtime sub-segmentation), so it catches splitter regressions the
    stored-body checks cannot see. Runs against every real beat body in every city."""
    import json
    import re
    from itertools import pairwise
    from pathlib import Path

    from src.tour.generation import _SENTENCE_OPENERS, split_sentences

    # A SINGLE-letter initial only ("D.", "C.") — the middle-initial class the regnal
    # split regresses. KEEP the period (it is what makes it an initial). Deliberately
    # NOT multi-part dotted abbrevs (U.S., B.C., D.C.): those legitimately END a
    # sentence, so a split after them is not a mid-name break and must not be flagged.
    single_initial = re.compile(r"^[A-Z]\.$")

    def _strip(tok: str) -> str:
        return tok.rstrip("\"'»)")

    data_root = Path(__file__).resolve().parent.parent / "data"
    offences: list[str] = []
    for beats_file in sorted(data_root.glob("*/beats.json")):
        for b in json.loads(beats_file.read_text()):
            body = (b.get("script_body") or "").strip()
            if not body:
                continue
            parts = split_sentences(body)
            for a, z in pairwise(parts):
                last = _strip(a.split()[-1]) if a.split() else ""
                nxt = z.split()[0] if z.split() else ""
                if (
                    single_initial.match(last)  # piece ends in a lone initial "D."
                    and nxt[:1].isupper()  # next opens on a capital…
                    and not single_initial.match(_strip(nxt))  # …not another initial
                    and nxt.rstrip("\"'»).,").lower() not in _SENTENCE_OPENERS  # …not an opener
                ):
                    offences.append(f"{b.get('beat_id')}: …{a[-25:]!r} || {z[:25]!r}…")
    assert not offences, "split_sentences breaks mid-name:\n  " + "\n  ".join(offences[:20])


_NELSON_50W = (
    "Nelson's Column is a monument in Trafalgar Square in the City of Westminster, "
    "Central London, England, United Kingdom, built to commemorate British Royal Navy "
    "officer Horatio Nelson's decisive victory at the Battle of Trafalgar over the "
    "combined French and Spanish navies, during which he was killed by a French sniper."
)


def test_vignette_one_liner_caps_a_long_run_on_but_keeps_the_poi_name():
    """A walk-past one-liner must land in one breath. A raw 50-word Wikipedia lead
    (Nelson's Column — surfaced by a live Opus-vs-ChatGPT A/B) is clause-capped to its
    first top-level clause, which stays under the word cap AND still names the POI.
    UNDO: make vignette_one_liner_text return split_sentences(body)[0] unchanged ->
    this returns the 50-word line -> RED."""
    from src.tour.generation import (
        VIGNETTE_ONE_LINER_MAX_WORDS,
        split_sentences,
        vignette_one_liner_text,
    )

    assert len(split_sentences(_NELSON_50W)[0].split()) == 50  # the whole thing is one sentence
    out = vignette_one_liner_text(_NELSON_50W, "Nelson's Column")
    assert len(out.split()) <= VIGNETTE_ONE_LINER_MAX_WORDS, out
    assert "nelson's column" in out.casefold(), out  # never drops its own name
    assert out.endswith("."), out  # a clean sentence, not a mid-clause fragment


def test_vignette_one_liner_serves_full_sentence_when_the_clause_would_drop_the_name():
    """No mis-attribution: if the POI name is NOT in the first clause of a long
    sentence, serve the FULL first sentence rather than a name-less clause (which
    would read as the seated stop's content). UNDO: drop the name-in-clause guard ->
    a name-less clause is served -> RED."""
    from src.tour.generation import vignette_one_liner_text

    body = (
        "Standing proudly over the fountains and the lions below, the great column "
        "here honours Nelson, the admiral who fell at the Battle of Trafalgar in 1805."
    )  # 27 words, name only in the 2nd clause
    out = vignette_one_liner_text(body, "Nelson")
    assert out == body, out  # full sentence, because the first clause omits "Nelson"

    # And a short line is never touched.
    short = "The Médicis fountain dates from 1685."
    assert vignette_one_liner_text(short, "Médicis fountain") == short


def test_split_sentences_terminal_abbreviations_still_split():
    """Dotted-caps abbreviations that legitimately END a sentence (U.S., A.D.,
    P.M.) must NOT be re-glued to the next sentence — the mirror of the initials
    bug. They share the shape of name-initials, so an over-broad re-glue would
    fuse two real sentences (TTS runs them together with no pause)."""
    assert split_sentences("He is from the U.S. The trip was long.") == [
        "He is from the U.S.",
        "The trip was long.",
    ]
    assert split_sentences("We saw the U.K. It was raining.") == [
        "We saw the U.K.",
        "It was raining.",
    ]
    assert split_sentences("It happened in 1914 A.D. The war began.") == [
        "It happened in 1914 A.D.",
        "The war began.",
    ]
    assert split_sentences("The exhibit runs A.M. to P.M. Come early.") == [
        "The exhibit runs A.M. to P.M.",
        "Come early.",
    ]


def test_split_sentences_empty():
    assert split_sentences("") == []
    assert split_sentences("   ") == []


# ---------------------------------------------------------------------------
# Cold open
# ---------------------------------------------------------------------------


def test_c9g_overflow_surfaces_on_scriptpoi():
    """C9g: BeatSequence.overflow_by_poi flows onto ScriptPOI.overflow_beat_ids so
    the governor's trimmed beats are SURFACED (keep-exploring extras), never
    silently dropped — the panel's blocking bug 3."""
    poi = _poi("p1", "Place des Vosges")
    kept = _beat("kept-1", poi.id, body="Henri IV built the square.", nf="establishing")
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=poi.id,
                poi_name=poi.name,
                ordering_strategy="narrative_function",
                beats=(kept,),
            ),
        ),
        overflow_by_poi={poi.id: ("trimmed-a", "trimmed-b")},
    )
    script = generate(seq, _route((poi,)), _input(), glue_client=MockGlueClient())
    sp = {p.id: p for p in script.selected_pois}[poi.id]
    assert sp.overflow_beat_ids == ("trimmed-a", "trimmed-b"), "overflow surfaced, not dropped"
    assert sp.beat_ids == ("kept-1",), "kept beats unchanged"


def test_cold_open_uses_stop_orientation_when_present():
    poi = _poi("p1", "Place des Vosges")
    orient = _beat(
        "orient-1",
        poi.id,
        body="Find a bench. Pronounced plass-day-voge.",
        nf="stop_orientation",
    )
    body = _beat("body-1", poi.id, body="Henri IV built the square.", nf="establishing")

    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=poi.id,
                poi_name=poi.name,
                ordering_strategy="narrative_function",
                beats=(orient, body),
            ),
        )
    )
    script = generate(seq, _route((poi,)), _input(), glue_client=MockGlueClient())
    cold_open_ids = [s.source_id for s in script.script if s.stop_idx == 0][:3]
    # Pacing glue first, then the orientation beat sentences.
    assert cold_open_ids[0] == GLUE_PACING
    assert orient.id in cold_open_ids
    # Body beat appears later, not consumed twice.
    body_count = sum(1 for s in script.script if s.source_id == body.id)
    assert body_count >= 1


def test_cold_open_synthesizes_when_no_orientation():
    poi = _poi("p1", "Notre-Dame Cathedral")
    body = _beat(
        "body-1", poi.id, body="The cathedral rears up like a great ship.", nf="establishing"
    )
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=poi.id,
                poi_name=poi.name,
                ordering_strategy="sub_location",
                beats=(body,),
            ),
        )
    )
    script = generate(
        seq, _route((poi,), duration_min=60), _input(round_trip=False), glue_client=MockGlueClient()
    )
    sources = [s.source_id for s in script.script]
    assert SYNTHESIZED_OPENER in sources
    # Phase 7.5 (Fix 2): synthesized opener anchors on the spine Area name
    # when present (corpus-honest "you're starting in the Marais"), and
    # falls back to the POI name only when no spine_area exists. Either
    # way the opener fires and writes traceable glue tokens.
    synth = next(s for s in script.script if s.source_id == SYNTHESIZED_OPENER)
    # The route fixture sets spine_area="Le Marais", so the location
    # anchor should reference the Marais.
    assert "Marais" in synth.text or "Notre-Dame Cathedral" in synth.text


# ---------------------------------------------------------------------------
# Anchor block — orientation not consumed twice
# ---------------------------------------------------------------------------


def test_orientation_beat_not_emitted_twice():
    poi = _poi("p1", "Place des Vosges")
    orient = _beat("orient-1", poi.id, body="Find a bench.", nf="stop_orientation")
    body = _beat("body-1", poi.id, body="Henri IV.", nf="establishing")
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=poi.id,
                poi_name=poi.name,
                ordering_strategy="narrative_function",
                beats=(orient, body),
            ),
        )
    )
    script = generate(seq, _route((poi,)), _input(), glue_client=MockGlueClient())
    orient_count = sum(1 for s in script.script if s.source_id == orient.id)
    # Orientation appears once (during cold open), not in the anchor block again.
    assert orient_count == 1


# ---------------------------------------------------------------------------
# Multi-stop transit routing
# ---------------------------------------------------------------------------


def test_transit_uses_corpus_beat_when_present():
    p1 = _poi("p1", "Pont Neuf")
    p2 = _poi("p2", "Conciergerie")
    orient = _beat("orient-1", p1.id, body="Stand on the bridge.", nf="stop_orientation")
    p1_body = _beat("p1-body", p1.id, body="The Pont Neuf is the oldest stone bridge.")
    # Phase 7: transit beat must reference the previous stop (Pont Neuf)
    # so the direction-awareness check accepts it.
    p2_transit = _beat(
        "p2-transit",
        p2.id,
        body=(
            "From the Pont Neuf, cross the road into place Dauphine "
            "and walk around the Conciergerie."
        ),
        nf="transition",
    )
    p2_body = _beat("p2-body", p2.id, body="The Conciergerie is Paris's oldest prison.")
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=p1.id,
                poi_name=p1.name,
                ordering_strategy="narrative_function",
                beats=(orient, p1_body),
            ),
            POIBeats(
                poi_id=p2.id,
                poi_name=p2.name,
                ordering_strategy="narrative_function",
                beats=(p2_transit, p2_body),
            ),
        )
    )
    client = MockGlueClient()
    script = generate(seq, _route((p1, p2)), _input(round_trip=False), glue_client=client)
    sources = [s.source_id for s in script.script]
    # The corpus transit beat got cited; Haiku was NOT called for nav glue.
    assert p2_transit.id in sources
    assert not any(c[0] == GLUE_NAV for c in client.calls)


def test_phase7_transit_rejects_wrong_direction_beat():
    """Transit beat at `current` whose origin doesn't match `previous` must be rejected.

    Mirrors the phase-6-rerun Tour 4 stop 3 failure: the corpus transit
    beat at Pont Alexandre III says "Starting at Invalides Metro
    station…" but the user is arriving from Pont de la Concorde. The
    Phase 7 direction check must reject it and fall through to GLUE_NAV
    with explicit ``from previous, walk to current`` context.
    """
    p1 = _poi("p1", "Pont de la Concorde")
    p2 = _poi("p2", "Pont Alexandre III")
    orient = _beat("orient-1", p1.id, body="Stand at the parapet.", nf="stop_orientation")
    p1_body = _beat("p1-body", p1.id, body="The bridge dates to 1791.")
    # Wrong-direction transit: origin is Invalides Metro, not Pont de la Concorde.
    p2_transit = _beat(
        "p2-transit",
        p2.id,
        body="Starting at Invalides Metro station, walk to Pont Alexandre III.",
        nf="transition",
    )
    p2_body = _beat("p2-body", p2.id, body="The Beaux-Arts span opened in 1900.")
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=p1.id,
                poi_name=p1.name,
                ordering_strategy="narrative_function",
                beats=(orient, p1_body),
            ),
            POIBeats(
                poi_id=p2.id,
                poi_name=p2.name,
                ordering_strategy="narrative_function",
                beats=(p2_transit, p2_body),
            ),
        )
    )
    client = MockGlueClient(responses={"GLUE_NAV": "Walk along the embankment."})
    script = generate(seq, _route((p1, p2)), _input(round_trip=False), glue_client=client)
    sources = [s.source_id for s in script.script]
    # The wrong-direction transit beat must NOT be cited — Haiku glue replaces it.
    assert p2_transit.id not in sources
    nav_sentences = [s for s in script.script if s.source_id == GLUE_NAV]
    assert len(nav_sentences) == 1
    # The nav request to Haiku carried the explicit "from previous, walk to current" context.
    assert client.calls and client.calls[0][0] == GLUE_NAV
    nav_request = client.calls[0][2].lower()
    assert "pont de la concorde" in nav_request
    assert "pont alexandre iii" in nav_request


def test_phase7_transit_accepts_directionally_consistent_beat():
    """Transit beat whose origin substring matches `previous` is accepted.

    The body names its endpoints and no bearing: a name match says the beat describes
    THIS segment, and a compass word would be refused on its own terms (S1.M3) whatever
    the names say, so a fixture carrying both could not tell the two rules apart.
    """
    p1 = _poi("p1", "Place du Tertre")
    p2 = _poi("p2", "Saint-Pierre")
    orient = _beat("orient-1", p1.id, body="Stand by the easels.", nf="stop_orientation")
    # Direction-consistent: body explicitly mentions Place du Tertre.
    p2_transit = _beat(
        "p2-transit",
        p2.id,
        body="Leave Place du Tertre by the far corner and step into Saint-Pierre.",
        nf="transition",
    )
    p2_body = _beat("p2-body", p2.id, body="The little church is older than Sacré-Cœur.")
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=p1.id,
                poi_name=p1.name,
                ordering_strategy="narrative_function",
                beats=(orient,),
            ),
            POIBeats(
                poi_id=p2.id,
                poi_name=p2.name,
                ordering_strategy="narrative_function",
                beats=(p2_transit, p2_body),
            ),
        )
    )
    client = MockGlueClient()
    script = generate(seq, _route((p1, p2)), _input(round_trip=False), glue_client=client)
    sources = [s.source_id for s in script.script]
    assert p2_transit.id in sources
    assert not any(c[0] == GLUE_NAV for c in client.calls)


def test_phase7_transit_falls_back_when_origin_mismatch():
    """Even when the previous-POI side has a transit beat, mismatched
    direction → fall through to GLUE_NAV. Both sides must satisfy the
    direction check before a corpus transit beat is reused.
    """
    p1 = _poi("p1", "A Random Start")
    p2 = _poi("p2", "Saint-Pierre")
    p1_body = _beat("p1-body", p1.id, body="Some setup.", nf="establishing")
    p2_transit = _beat(
        "p2-transit",
        p2.id,
        body="From the Sacré-Cœur, walk down rue du Mont-Cenis to Saint-Pierre.",
        nf="transition",
    )
    p2_body = _beat("p2-body", p2.id, body="The little church.")
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=p1.id,
                poi_name=p1.name,
                ordering_strategy="narrative_function",
                beats=(p1_body,),
            ),
            POIBeats(
                poi_id=p2.id,
                poi_name=p2.name,
                ordering_strategy="narrative_function",
                beats=(p2_transit, p2_body),
            ),
        )
    )
    client = MockGlueClient(responses={"GLUE_NAV": "Walk down the street."})
    script = generate(seq, _route((p1, p2)), _input(round_trip=False), glue_client=client)
    sources = [s.source_id for s in script.script]
    assert p2_transit.id not in sources  # rejected — origin doesn't match prev
    assert any(s == GLUE_NAV for s in sources)


def test_transit_falls_back_to_glue_when_no_corpus_beat():
    p1 = _poi("p1", "Place des Vosges")
    p2 = _poi("p2", "Hôtel de Sully")
    p1_orient = _beat("p1-orient", p1.id, body="Find a bench.", nf="stop_orientation")
    p1_body = _beat("p1-body", p1.id, body="Henri IV built the square.")
    p2_body = _beat("p2-body", p2.id, body="The Hôtel de Sully sits just south.")
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=p1.id,
                poi_name=p1.name,
                ordering_strategy="narrative_function",
                beats=(p1_orient, p1_body),
            ),
            POIBeats(
                poi_id=p2.id,
                poi_name=p2.name,
                ordering_strategy="narrative_function",
                beats=(p2_body,),
            ),
        )
    )
    client = MockGlueClient(responses={"GLUE_NAV": "Walk on to the gate, just ahead."})
    script = generate(seq, _route((p1, p2)), _input(round_trip=False), glue_client=client)
    nav_sentences = [s for s in script.script if s.source_id == GLUE_NAV]
    assert len(nav_sentences) == 1
    assert nav_sentences[0].text == "Walk on to the gate, just ahead."
    assert nav_sentences[0].stop_idx == 1
    assert client.calls and client.calls[0][0] == GLUE_NAV


# ---------------------------------------------------------------------------
# Phase 8 S8.3a (W8.2 R1, 11/11): the nav glue knows its leg — the request
# hands Haiku the routed minutes and the leg-voice rules (never compass;
# left/right/straight-ahead or a visible landmark), and the coercion guard
# refuses an output that breaks them, falling back to the deterministic
# template (which is compass-free and speaks the routed number by
# construction). Carried instances: Camille/Théo/Aiko x2/Paulo/Greta/Sofia x3
# wrong-compass/wrong-minutes lines (phase7-ledger carry 1).
# ---------------------------------------------------------------------------


def test_nav_glue_request_carries_the_routed_minutes_and_the_leg_voice_rules():
    p1 = _poi("p1", "Place des Vosges")
    p2 = _poi("p2", "Hôtel de Sully")
    o = _beat("o", p1.id, body="Find a bench.", nf="stop_orientation")
    a = _beat("a", p1.id, body="A fact.")
    b = _beat("b", p2.id, body="Another fact.")
    seq = BeatSequence(
        poi_beats=(
            POIBeats(poi_id=p1.id, poi_name=p1.name,
                     ordering_strategy="narrative_function", beats=(o, a)),
            POIBeats(poi_id=p2.id, poi_name=p2.name,
                     ordering_strategy="narrative_function", beats=(b,)),
        )
    )
    client = MockGlueClient()
    pois = (p1, p2)
    route = _route(pois)
    route = route.model_copy(
        update={
            "transits": (
                route.transits[0],
                route.transits[1].model_copy(update={"walk_seconds": 540}),  # 9 min
            )
        }
    )
    generate(seq, route, _input(round_trip=False), glue_client=client)
    assert client.calls and client.calls[0][0] == GLUE_NAV
    request = client.calls[0][2]
    assert "about 9 minutes" in request, request
    lowered = request.lower()
    assert "never compass" in lowered, request
    assert "left" in lowered and "landmark" in lowered, request


def test_coerce_glue_output_rejects_compass_and_wrong_minutes():
    """The guard is the second line of defence behind the prompt: an output that
    speaks compass, or a minute count that is not the routed leg's own number
    (digits or words), falls back to the deterministic template. A line in the
    legal voice passes through untouched."""
    from src.tour.generation import _coerce_glue_output

    default = "From A, carry on to B, just ahead."
    assert _coerce_glue_output(
        "Walk northwest along the Seine.", default=default, leg_minutes=3
    ) == default
    assert _coerce_glue_output(
        "Carry on for about ten minutes.", default=default, leg_minutes=3
    ) == default
    assert _coerce_glue_output(
        "Head for the gate, a five-minute walk.", default=default, leg_minutes=9
    ) == default
    kept = "Turn left and follow the quai for about three minutes."
    assert _coerce_glue_output(kept, default=default, leg_minutes=3) == kept
    no_number = "Cross the bridge ahead toward the tower."
    assert _coerce_glue_output(no_number, default=default, leg_minutes=3) == no_number


def test_transit_glue_falls_back_to_default_on_no_glue_sentinel():
    p1 = _poi("p1", "Stop A")
    p2 = _poi("p2", "Stop B")
    o = _beat("o", p1.id, body="Settle in.", nf="stop_orientation")
    a = _beat("a", p1.id, body="A history beat.")
    b = _beat("b", p2.id, body="A second history beat.")
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=p1.id, poi_name=p1.name, ordering_strategy="narrative_function", beats=(o, a)
            ),
            POIBeats(
                poi_id=p2.id, poi_name=p2.name, ordering_strategy="narrative_function", beats=(b,)
            ),
        )
    )
    client = MockGlueClient(responses={"GLUE_NAV": NO_GLUE_SENTINEL})
    script = generate(seq, _route((p1, p2)), _input(round_trip=False), glue_client=client)
    nav = next(s for s in script.script if s.source_id == GLUE_NAV)
    # Default carries the next-stop name; no factual claim invented.
    assert "Stop B" in nav.text


# ---------------------------------------------------------------------------
# Glue whitelist enforcement
# ---------------------------------------------------------------------------


def test_glue_label_set_matches_design_doc():
    # GLUE_REFLECTION joined the whitelist in Phase 4 Step 4.1 (canonical
    # spec §6: "a new whitelisted glue token GLUE_REFLECTION").
    expected = {
        "GLUE_NAV",
        "GLUE_STAGING",
        "GLUE_PACING",
        "GLUE_CALLBACK",
        "GLUE_CLOSING",
        "GLUE_REFLECTION",
        "ARITH",
        "SYNTHESIZED_OPENER",
    }
    assert set(GLUE_LABELS) == expected


def test_every_glue_sentence_has_whitelisted_source_id():
    poi = _poi("p1", "Place des Vosges")
    orient = _beat("o", poi.id, body="Find a bench.", nf="stop_orientation")
    body = _beat("b", poi.id, body="Henri IV built the square.")
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=poi.id,
                poi_name=poi.name,
                ordering_strategy="trigger_address",
                beats=(orient, body),
            ),
        )
    )
    script = generate(seq, _route((poi,)), _input(), glue_client=MockGlueClient())
    for s in script.script:
        if s.source_type in ("glue", "arith"):
            assert s.source_id in GLUE_LABELS


def test_haiku_invented_forbidden_phrase_is_replaced_with_default():
    # If the LLM tries to inject "imagine", coercion should drop the
    # output and use the default safe glue.
    p1 = _poi("p1", "A")
    p2 = _poi("p2", "B")
    o = _beat("o", p1.id, body="Find a bench.", nf="stop_orientation")
    a = _beat("a", p1.id, body="A fact.")
    b = _beat("b", p2.id, body="Another fact.")
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=p1.id, poi_name=p1.name, ordering_strategy="narrative_function", beats=(o, a)
            ),
            POIBeats(
                poi_id=p2.id, poi_name=p2.name, ordering_strategy="narrative_function", beats=(b,)
            ),
        )
    )
    client = MockGlueClient(responses={"GLUE_NAV": "Imagine the river. Picture this scene."})
    script = generate(seq, _route((p1, p2)), _input(round_trip=False), glue_client=client)
    nav = next(s for s in script.script if s.source_id == GLUE_NAV)
    for phrase in FORBIDDEN_PHRASES:
        assert phrase not in nav.text.lower()


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_generation_is_deterministic_under_mock():
    poi = _poi("p1", "Place des Vosges")
    orient = _beat("o", poi.id, body="Find a bench.", nf="stop_orientation")
    body = _beat("b", poi.id, body="Henri IV built the square.")
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=poi.id,
                poi_name=poi.name,
                ordering_strategy="trigger_address",
                beats=(orient, body),
            ),
        )
    )
    a = generate(seq, _route((poi,)), _input(), glue_client=MockGlueClient())
    b = generate(seq, _route((poi,)), _input(), glue_client=MockGlueClient())
    # `generated_at` will differ; everything else must match.
    assert tuple(s.text for s in a.script) == tuple(s.text for s in b.script)
    assert tuple(s.source_id for s in a.script) == tuple(s.source_id for s in b.script)


# ---------------------------------------------------------------------------
# Source attribution
# ---------------------------------------------------------------------------


def test_every_beat_sentence_carries_beat_id():
    poi = _poi("p1", "Place des Vosges")
    orient = _beat("o", poi.id, body="Find a bench.", nf="stop_orientation")
    body = _beat("b", poi.id, body="Henri IV built it. Crowds came.")
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=poi.id,
                poi_name=poi.name,
                ordering_strategy="trigger_address",
                beats=(orient, body),
            ),
        )
    )
    script = generate(seq, _route((poi,)), _input(), glue_client=MockGlueClient())
    for s in script.script:
        if s.source_type == "beat":
            assert s.source_id in {orient.id, body.id}


# ---------------------------------------------------------------------------
# Output rollup
# ---------------------------------------------------------------------------


def test_lens_coverage_counts_beats_per_lens():
    poi = _poi("p1", "Place des Vosges")
    o = _beat(
        "o", poi.id, body="Find a bench.", nf="stop_orientation", lenses=("famous_residents",)
    )
    a = _beat("a", poi.id, body="A fact.", lenses=("famous_residents", "literary_heritage"))
    b = _beat("b", poi.id, body="Another fact.", lenses=("famous_residents",))
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=poi.id,
                poi_name=poi.name,
                ordering_strategy="trigger_address",
                beats=(o, a, b),
            ),
        ),
    )
    script = generate(seq, _route((poi,)), _input(), glue_client=MockGlueClient())
    assert script.lens_coverage["famous_residents"] == 3
    assert script.lens_coverage["literary_heritage"] == 1


def test_selected_pois_carry_beat_ids_in_order():
    poi = _poi("p1", "Place des Vosges")
    o = _beat("o", poi.id, body="x.", nf="stop_orientation")
    a = _beat("a", poi.id, body="y.")
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=poi.id, poi_name=poi.name, ordering_strategy="trigger_address", beats=(o, a)
            ),
        ),
    )
    script = generate(seq, _route((poi,)), _input(), glue_client=MockGlueClient())
    assert script.selected_pois[0].beat_ids == ("o", "a")


def test_empty_beat_sequence_produces_empty_script():
    seq = BeatSequence(poi_beats=())
    route = _route(())
    script = generate(seq, route, _input(), glue_client=MockGlueClient())
    assert script.script == ()
    assert script.selected_pois == ()
    assert script.validation.passed


# ---------------------------------------------------------------------------
# Phase 7.5 Fix 2 — improved SYNTHESIZED_OPENER
# ---------------------------------------------------------------------------


def _beat_with_cues(
    bid: str,
    poi_id: str,
    *,
    cues: tuple[PhysicalCue, ...] = (),
    pronunciation: str | None = None,
    body: str = "",
    nf: str = "establishing",
) -> BeatRef:
    return BeatRef(
        id=bid,
        poi_id=poi_id,
        narrative_function=nf,
        word_count=len(body.split()) or 1,
        script_body=body or None,
        physical_cues=cues,
        pronunciation=pronunciation,
    )


def test_synthesized_opener_uses_physical_cues():
    """Synthesized opener references the start POI's strongest cue + Area name."""
    poi = _poi("p1", "Hotel de Sully")
    body = _beat_with_cues(
        "body",
        poi.id,
        cues=(
            PhysicalCue(
                cue="the wrought-iron balconies",
                direction="up",
                feature_type="architectural_detail",
            ),
        ),
        body="A square fact.",
    )
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=poi.id,
                poi_name=poi.name,
                ordering_strategy="narrative_function",
                beats=(body,),
            ),
        )
    )
    script = generate(seq, _route((poi,)), _input(round_trip=True), glue_client=MockGlueClient())
    cold_open = [s for s in script.script if s.stop_idx == 0]
    texts = [s.text for s in cold_open]
    # Area-anchored location line.
    assert any("Marais" in t for t in texts)
    # Architectural-detail cue surfaces via "Notice X."
    # (not "Look up at" - that's the view feature_type).
    assert any("the wrought-iron balconies" in t for t in texts)


def test_synthesized_opener_uses_pronunciation_when_present():
    poi = _poi("p1", "Place des Vosges")
    body = _beat_with_cues(
        "body",
        poi.id,
        pronunciation="plass-day-voge",
        body="A square fact.",
    )
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=poi.id,
                poi_name=poi.name,
                ordering_strategy="narrative_function",
                beats=(body,),
            ),
        )
    )
    script = generate(seq, _route((poi,)), _input(round_trip=True), glue_client=MockGlueClient())
    cold_open = [s for s in script.script if s.stop_idx == 0]
    texts = [s.text for s in cold_open]
    assert any("plass-day-voge" in t for t in texts)
    assert any("That's pronounced" in t for t in texts)


def test_synthesized_opener_falls_back_gracefully_with_no_cues():
    poi = _poi("p1", "Some New Anchor")
    body = _beat_with_cues("body", poi.id, body="A fact.")
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=poi.id,
                poi_name=poi.name,
                ordering_strategy="narrative_function",
                beats=(body,),
            ),
        )
    )
    script = generate(
        seq,
        _route((poi,), duration_min=60),
        _input(round_trip=True, duration=60),
        glue_client=MockGlueClient(),
    )
    cold_open = [s for s in script.script if s.stop_idx == 0]
    texts = [s.text for s in cold_open]
    # Minimal but readable: pacing + location anchor + first-stop walk grounding.
    assert any("Settle in" in t for t in texts)
    assert any("Marais" in t for t in texts)  # spine_area anchor
    # #20: the first stop is named and framed as a walk ahead (honest — en route),
    # replacing the old generic "we're going to walk for N minutes" primer.
    assert any("head for Some New Anchor" in t for t in texts)


def test_synthesized_opener_view_cue_uses_look_up_verb():
    """A 'view' feature_type cue uses 'Look up at...' staging."""
    poi = _poi("p1", "Some Square")
    body = _beat_with_cues(
        "body",
        poi.id,
        cues=(PhysicalCue(cue="the gilded statue", direction="up", feature_type="view"),),
        body="A fact.",
    )
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=poi.id,
                poi_name=poi.name,
                ordering_strategy="narrative_function",
                beats=(body,),
            ),
        )
    )
    script = generate(seq, _route((poi,)), _input(round_trip=True), glue_client=MockGlueClient())
    cold_open = [s for s in script.script if s.stop_idx == 0]
    cold_open_texts = [s.text for s in cold_open]
    assert any("Look up at the gilded statue" in t for t in cold_open_texts)
    # View cues also unlock the "Take a moment to take it in." invitation.
    assert any("Take a moment" in t for t in cold_open_texts)
    invitation = next(s for s in cold_open if s.text == SENSORY_INVITATION)
    assert invitation.source_id == GLUE_PACING


def test_synthesized_opener_does_not_double_a_cue_that_already_stages():
    """A physical_cue that already opens with its own staging directive ('Look up
    at the dome') must not get a second 'Look up at' prefixed — the workbench
    'Look up at Look up at the golden dome' defect."""
    poi = _poi("p1", "Some Square")
    body = _beat_with_cues(
        "body",
        poi.id,
        cues=(PhysicalCue(cue="Look up at the golden dome", direction="up", feature_type="view"),),
        body="A fact.",
    )
    seq = BeatSequence(
        poi_beats=(
            POIBeats(poi_id=poi.id, poi_name=poi.name,
                     ordering_strategy="narrative_function", beats=(body,)),
        )
    )
    script = generate(seq, _route((poi,)), _input(round_trip=True), glue_client=MockGlueClient())
    texts = [s.text for s in script.script if s.stop_idx == 0]
    assert any("Look up at the golden dome" in t for t in texts)
    assert not any("Look up at Look up at" in t for t in texts)


def test_synthesized_opener_lowercases_raw_leading_the_in_cue():
    """A raw title-case cue field ('The square...') reads 'Look up at the square',
    not 'Look up at The square' — the capital-The template seam."""
    poi = _poi("p1", "Some Square")
    body = _beat_with_cues(
        "body",
        poi.id,
        cues=(PhysicalCue(cue="The square, now half-pedestrianised", direction="up",
                          feature_type="view"),),
        body="A fact.",
    )
    seq = BeatSequence(
        poi_beats=(
            POIBeats(poi_id=poi.id, poi_name=poi.name,
                     ordering_strategy="narrative_function", beats=(body,)),
        )
    )
    script = generate(seq, _route((poi,)), _input(round_trip=True), glue_client=MockGlueClient())
    texts = [s.text for s in script.script if s.stop_idx == 0]
    assert any("Look up at the square" in t for t in texts)
    assert not any("at The square" in t for t in texts)


# ---------------------------------------------------------------------------
# 2026-07-03 — per-stop emission guards (the "thin single-beat-feeling
# narration" half of the 2026-07-02 regression).
#
# validate_script checks only traceability + forbidden phrases, and
# ScriptPOI.beat_ids reflects the PLAN, not emissions — so a stop can ship
# glue-only narration while every existing check stays green. The first test
# below is the per-stop emitted-beat floor for healthy pools; the two
# "current_contract_pin" tests make BOTH known zero-emission holes executable
# and version-controlled. The per-stop floor/disclosure for those holes is
# deferred design owned by specs/2026-07-02-dwell-audio-reconciliation/ —
# when it lands, flip the pinned assertions DELIBERATELY, don't delete them.
# ---------------------------------------------------------------------------


def _poi_beats(poi: POI, beats: tuple[BeatRef, ...]) -> POIBeats:
    return POIBeats(
        poi_id=poi.id,
        poi_name=poi.name,
        ordering_strategy="narrative_function",
        beats=beats,
    )


@pytest.mark.parametrize("n_stops", (2, 3, 4, 5))
def test_every_stop_with_dwellworthy_pool_emits_own_beat_sentence(n_stops: int):
    """Every stop whose plan holds real (non-transit) beats emits >= 1 of them.

    Fails if (a) the anchor block's transit-class filter broadens to swallow
    establishing beats, (b) consumed_beat_ids leaks so an earlier stop
    consumes a later stop's pool, (c) reorder_final_stop_for_closing or
    _beat_to_sentences starts dropping bodies, or (d) a future dedupe pass
    empties a stop's emissions while ScriptPOI.beat_ids stays populated —
    every one of which reproduces glue-only stop narration that passes ALL
    existing generation checks.

    Fixture notes: stop 0 carries its OWN stop_orientation beat, so the cold
    open is satisfied locally and never raids a sibling stop's pool (the
    Area hoist fires only when stop 0 lacks one). Later stops carry
    'establishing' beats — outside the transit-class set — with no
    trigger_address, so the transit stage's direction check never adopts
    them. POIs get real multi-word names so the direction check's substring
    premise never hinges on short-name luck.
    """
    from src.tour.render_md import stop_narration_text

    pois = tuple(_poi(f"p{k}", f"Waypoint Number {k}") for k in range(n_stops))
    plans = [
        _poi_beats(
            pois[0],
            (
                _beat("p0-orient", "p0", body="Find the corner bench.", nf="stop_orientation"),
                _beat(
                    "p0-body-a",
                    "p0",
                    body="Story A at stop 0. It happened here.",
                    nf="establishing",
                ),
                _beat("p0-body-b", "p0", body="Story B at stop 0.", nf="establishing"),
            ),
        )
    ]
    bodies_by_stop: dict[int, list[str]] = {
        0: ["Find the corner bench.", "Story A at stop 0.", "Story B at stop 0."]
    }
    for k in range(1, n_stops):
        beats = (
            _beat(f"p{k}-est-a", f"p{k}", body=f"First tale at waypoint {k}.", nf="establishing"),
            _beat(f"p{k}-est-b", f"p{k}", body=f"Second tale at waypoint {k}.", nf="establishing"),
        )
        plans.append(_poi_beats(pois[k], beats))
        bodies_by_stop[k] = [f"First tale at waypoint {k}.", f"Second tale at waypoint {k}."]

    seq = BeatSequence(poi_beats=tuple(plans))
    script = generate(seq, _route(pois), _input(), glue_client=MockGlueClient())
    per_stop = stop_narration_text(script)

    for k in range(n_stops):
        plan_ids = set(script.selected_pois[k].beat_ids)
        assert plan_ids, f"stop {k}: plan lost its beats"
        assert any(
            s.source_type == "beat" and s.stop_idx == k and s.source_id in plan_ids
            for s in script.script
        ), f"stop {k} emitted ZERO of its own beats — glue-only narration"
        # The same floor at the render level the preview wire serves.
        narration = per_stop.get(k, "")
        assert narration, f"stop {k}: empty narration"
        assert any(body in narration for body in bodies_by_stop[k]), (
            f"stop {k}: narration carries none of its beat bodies"
        )


def test_transit_only_stop_dropped_beat_is_not_reported_as_voiced():
    """#8 fix (was a CURRENT-CONTRACT PIN): a stop whose only beat is
    transit-class AND directionally rejected emits zero beat sentences — and
    that dropped beat must NOT be reported as voiced.

    Before the #8 fix, ``_flatten_pois`` derived ``beat_ids`` from the raw plan,
    so a directionally-rejected transit beat that ``_build_transit`` never
    consumed AND the anchor block filters out still appeared in the stop's
    ``beat_ids``. That double-lied: it over-reported dwell by the beat's spoken
    seconds AND made keep-exploring (``extra_beat_ids``, which excludes anything
    in ``beat_ids``) treat the unreachable content as already-voiced — so the
    listener could never reach it. The fix computes ``beat_ids`` from the beats
    that actually emitted a sentence, so the dropped beat is (a) absent from
    ``beat_ids``, (b) not counted in ``dwell_seconds``, and (c) recoverable as a
    keep-exploring extra.

    This also guards the inverse regression: if the anchor block's transit-class
    filter is ever DROPPED, assertion (2) fails, preventing out-of-place
    navigation sentences from leaking back into anchor blocks (the original
    Phase 7 bug the filter exists for).
    """
    from src.tour.beat_select import extra_beat_ids

    p1 = _poi("p1", "Place des Vosges")
    p2 = _poi("p2", "Saint-Pierre")
    p1_orient = _beat("p1-orient", "p1", body="Find the corner bench.", nf="stop_orientation")
    p1_body = _beat("p1-body", "p1", body="Henri IV built the square in 1612.", nf="establishing")
    # Transit-class AND directionally rejected: neither the trigger_address
    # nor the body mentions p1's name, so _build_transit's Phase-7 direction
    # check refuses it and falls through to GLUE_NAV without consuming it;
    # the anchor block then filters it as transit-class -> zero emission.
    p2_transit = _beat(
        "p2-transit",
        "p2",
        body="Leave the Old Mill and cross toward the gate.",
        nf="transition",
        addr="Rue Imaginaire 12",
        word_count=300,  # 300w @150wpm = 120s of dwell it would falsely add
    )
    # Precondition: the rejection premise is explicit, not accidental — a
    # helper-default or body edit that lets p1's name slip in would flip the
    # beat into an ACCEPTED transit beat and fail this loudly.
    haystack = f"{p2_transit.trigger_address or ''} {p2_transit.script_body or ''}".lower()
    assert p1.name.lower() not in haystack, "fixture drifted: transit beat now matches p1"

    seq = BeatSequence(poi_beats=(_poi_beats(p1, (p1_orient, p1_body)),
                                  _poi_beats(p2, (p2_transit,))))
    # p2 is priced at 300 s of visit time, which is what selection supplies on a
    # real route. Without it the stop would floor at its narration and this test
    # could not tell "does not over-report unvoiced audio" from "reports nothing".
    script = generate(
        seq,
        _route((p1, p2), visit_seconds={p2.id: 300}),
        _input(),
        glue_client=MockGlueClient(),
    )

    # (1) FIX: the dropped, unvoiced beat is NOT reported as voiced — beat_ids is
    # empty (nothing was actually voiced at this stop).
    sp2 = script.selected_pois[1]
    assert sp2.beat_ids == ()
    # (1b) FIX: dwell does not over-report the 120s of never-voiced audio. It
    # floors at what this visitor spends AT the stop — 300 s here, supplied by
    # the route exactly as selection supplies it. It was the flat tier-5 dwell
    # until 2026-08-06; the number is the same, the reason is not, and the
    # property under test is unchanged: never ABOVE the floor on unvoiced audio.
    assert sp2.dwell_seconds == 300
    # (1c) P9R-S1.M6 (Paulo's acceptance finding): a rejected transit beat is
    # NOT an extra either. Extras play STANDING at the stop, and this beat's
    # words are a walk's narration for a walk this route does not take —
    # re-classifying it as reachable was how "Walk up boulevard de Sébastopol"
    # reached the keep-exploring tap. Unreachable is correct: transit-class
    # beats never enter the extras pool (one function set with the pick and
    # the anchor block).
    extras = extra_beat_ids(p2, (p2_transit,), sp2.beat_ids)
    assert "p2-transit" not in extras
    # (2) The zero-emission condition still holds (anchor filter intact).
    assert not any(s.source_type == "beat" and s.stop_idx == 1 for s in script.script), (
        "stop 1 emitted a beat sentence — the transit-class filter changed; "
        "update this pin consciously (specs/2026-07-02-dwell-audio-reconciliation/)"
    )
    # (3) Stop 1's narration is non-empty but consists ONLY of glue.
    stop1 = [s for s in script.script if s.stop_idx == 1]
    assert stop1, "stop 1 must still get glue narration"
    assert all(s.source_type in ("glue", "arith") for s in stop1)
    # (4) validate_script still passes (structural validity is unaffected).
    assert script.validation.passed is True


def test_cold_open_area_hoist_strips_donor_stop_current_contract_pin():
    """CURRENT-CONTRACT PIN: the cold open's Area hoist can strip its donor
    stop down to zero beat emissions.

    The Area-level fallback hoists a sibling stop's stop_orientation beat
    into the opener (emitted with stop_idx=0) and adds it to
    consumed_beat_ids so it never fires at its own stop; when it was the
    donor's ONLY beat, the donor emits zero beat sentences while its
    ScriptPOI.beat_ids stays non-empty — the second known glue-only-stop
    path. The per-stop floor is owned by
    specs/2026-07-02-dwell-audio-reconciliation/; flip assertion (2)
    DELIBERATELY when it lands. Assertion (3) simultaneously guards the
    CROSS-stop double-fire regression (the same-stop case is covered by
    test_orientation_beat_not_emitted_twice above).
    """
    # Both _poi() POIs share the helper's default area ('Le Marais') and the
    # same coordinates, so the hoist's Area-match and proximity gates pass;
    # the beat carries no physical_cues, so the honesty gate passes too.
    p1 = _poi("p1", "Hotel de Sully")
    p2 = _poi("p2", "Place des Vosges")
    p1_est_a = _beat("p1-est-a", "p1", body="The facade dates to 1624.", nf="establishing")
    p1_est_b = _beat("p1-est-b", "p1", body="Sully bought it in 1634.", nf="establishing")
    # p1 has NO stop_orientation beat — forces the Area-fallback search.
    p2_orient = _beat(
        "p2-orient", "p2", body="Stand where the two lanes meet.", nf="stop_orientation"
    )

    seq = BeatSequence(poi_beats=(_poi_beats(p1, (p1_est_a, p1_est_b)),
                                  _poi_beats(p2, (p2_orient,))))
    script = generate(seq, _route((p1, p2)), _input(), glue_client=MockGlueClient())

    # Precondition: the hoist actually happened. If the fallback ever stops
    # hoisting, this test's premise is gone — fail HERE, loudly, instead of
    # passing vacuously.
    hoisted = [
        s for s in script.script if s.source_type == "beat" and s.source_id == "p2-orient"
    ]
    assert any(s.stop_idx == 0 for s in hoisted), (
        "the Area hoist no longer fires — this pin's premise is gone; rewrite it"
    )
    # (1) The donor's plan is unchanged.
    assert script.selected_pois[1].beat_ids == ("p2-orient",)
    # (2) The donor stop is stripped to zero beat emissions (the pinned hole).
    assert not any(s.source_type == "beat" and s.stop_idx == 1 for s in script.script), (
        "donor stop emitted a beat — the hoist/consumed contract changed; "
        "update this pin consciously (specs/2026-07-02-dwell-audio-reconciliation/)"
    )
    # (3) No double emission: the consumed-set contract that motivated
    # consumed_beat_ids — the orientation beat is spoken exactly ONCE.
    assert len(hoisted) == 1


# ---------------------------------------------------------------------------
# C8 — honest REPORTED per-stop minutes (dwell_seconds = max(tier floor,
# planned voiced audio)). Reporting surface only; routes are unchanged.
# The hermetic fixtures elsewhere carry no word_count, so max() == the tier
# floor there; these two tests exercise the honest path that C8 changes.
# ---------------------------------------------------------------------------


def test_reported_dwell_is_planned_audio_when_a_stop_is_beat_rich():
    # Two 500-word beats @150wpm = 200s + 200s = 400s of voiced narration at a
    # tier-5 stop whose tier floor is only 300s -> the stop card must report the
    # REAL 400s, not the fictional 300s (the pool-vs-delivered honesty fix).
    poi = _poi("rich", "Place des Vosges", tier=5)
    beats = (
        _beat("b1", poi.id, body="Henri IV built the square.", word_count=500),
        _beat("b2", poi.id, body="The arcades run all around.", word_count=500),
    )
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=poi.id,
                poi_name=poi.name,
                ordering_strategy="narrative_function",
                beats=beats,
            ),
        )
    )
    script = generate(seq, _route((poi,)), _input(), glue_client=MockGlueClient())
    sp = next(s for s in script.selected_pois if s.id == poi.id)
    assert sp.dwell_seconds == 400  # planned voiced audio, NOT the 300s tier floor


def test_reported_dwell_floors_at_its_visit_time_when_a_stop_is_beat_thin():
    # A 100-word beat @150wpm = 40s of narration -> the card still reports the
    # 300s the visitor actually spends there (a stop takes look-around time even when
    # the audio is short). max(tier_floor, planned) keeps the floor.
    poi = _poi("thin", "Small Chapel", tier=5)
    beats = (_beat("b1", poi.id, body="A brief note here.", word_count=100),)
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=poi.id,
                poi_name=poi.name,
                ordering_strategy="narrative_function",
                beats=beats,
            ),
        )
    )
    # WAS the flat 300 s tier-5 dwell, deleted 2026-08-06. The floor is now what
    # THIS visitor spends at THIS place, priced in selection and carried on the
    # route — so the fixture must supply it, exactly as a real route does.
    route = _route((poi,), visit_seconds={poi.id: 300})
    script = generate(seq, route, _input(), glue_client=MockGlueClient())
    sp = next(s for s in script.selected_pois if s.id == poi.id)
    assert sp.dwell_seconds == 300  # the visit time wins over the 40s of narration

    # And with no visit time priced, the stop is exactly as long as it speaks.
    bare = generate(seq, _route((poi,)), _input(), glue_client=MockGlueClient())
    assert next(s for s in bare.selected_pois if s.id == poi.id).dwell_seconds == 40


# ---------------------------------------------------------------------------
# #20 — grounded, honest cold-open (area article + first-stop walk framing)
# ---------------------------------------------------------------------------

def test_area_article_covers_english_descriptive_areas():
    assert _area_article("Latin Quarter") == "the "
    assert _area_article("Left Bank") == "the "
    assert _area_article("Île de la Cité") == "the "
    assert _area_article("4th Arrondissement") == "the "
    # French place-names take no article.
    assert _area_article("Montmartre") == ""
    assert _area_article("Saint-Germain-des-Prés") == ""
    # "Le Marais" already carries its article.
    assert _area_article("Le Marais") == ""


def _stop(name: str) -> POIBeats:
    return POIBeats(poi_id="p", poi_name=name, ordering_strategy="narrative_function", beats=())


def _route_with_first_leg(secs: int) -> Route:
    poi = _poi("p", "x")
    return Route(
        pois=(poi,),
        transits=(TransitSegment(from_poi_id=None, to_poi_id="p", distance_m=100.0,
                                 walk_seconds=secs),),
        total_walk_distance_m=100.0, total_walk_seconds=secs,
        spine_area="Le Marais",
        target_dwell_seconds=1800, err_short_total_seconds=2988,
    )


def test_first_leg_text_names_stop_and_walk_minutes():
    line = _synth_first_leg_text(_stop("Pantheon"), _route_with_first_leg(300))  # 5 min
    assert line.text == "When you're ready, head for Pantheon — about a 5-minute walk from here."


def test_first_leg_text_uses_an_before_vowel_minutes():
    line = _synth_first_leg_text(_stop("Pantheon"), _route_with_first_leg(480))  # 8 min
    assert "about an 8-minute walk" in line.text


def test_first_leg_text_lowercases_leading_the():
    line = _synth_first_leg_text(_stop("The Sorbonne"), _route_with_first_leg(300))
    assert "head for the Sorbonne" in line.text
    assert "The Sorbonne" not in line.text


def test_first_leg_text_short_leg_is_just_ahead():
    line = _synth_first_leg_text(_stop("Pantheon"), _route_with_first_leg(40))  # <1 min
    assert line.text == "When you're ready, head for Pantheon — it's just ahead."


def test_first_leg_text_at_the_stop_does_not_say_head_for():
    """When the first stop is essentially where the walker is already standing
    (routed leg under ~20s), don't tell them to 'head for' a place they're on
    top of — the Tuileries/Concorde 'walk to where you already are' complaint."""
    line = _synth_first_leg_text(_stop("Place de la Concorde"), _route_with_first_leg(5))
    assert line.text == (
        "You're starting right at Place de la Concorde. Take a moment to take it in."
    )
    assert "head for" not in line.text


def test_first_leg_text_none_without_stop_name():
    assert _synth_first_leg_text(_stop(""), _route_with_first_leg(300)) is None


# ---------------------------------------------------------------------------
# Phase 8 S8.3c (W8.2 R2; phase7-ledger carry 1, Paulo's Finding 14): the
# opener's WALKING line rides the first LEG, not the standing cold-open — a
# queued first stop must not auto-play "head for X" to someone standing in
# its queue. The one leg-vs-stop rule (``is_walk_concurrent``) is untouched;
# the line's LABEL moves to GLUE_NAV, whose placement is already the leg's.
# ---------------------------------------------------------------------------


def test_first_leg_walking_line_rides_the_leg_and_the_standing_line_does_not():
    """UNDO: emit the head-for line as SYNTHESIZED_OPENER again -> RED."""
    from src.tour.generation import is_walk_concurrent

    walking = _synth_first_leg_text(_stop("Pantheon"), _route_with_first_leg(300))
    assert walking.source_id == GLUE_NAV, (
        "the head-for line is navigation and must ride the first leg"
    )
    assert is_walk_concurrent(walking), "the leg's own rule must claim the walking line"

    # Standing right at the first stop: nothing is walking anywhere — the line
    # stays a stationary opener piece.
    standing = _synth_first_leg_text(_stop("Concorde"), _route_with_first_leg(5))
    assert standing.source_id == SYNTHESIZED_OPENER
    assert not is_walk_concurrent(standing)

    # No leg at all (fixture routes): there is no leg to ride — stationary, so
    # the playback partition never points at an absent transit.
    poi = _poi("p", "x")
    no_leg = Route(
        pois=(poi,), transits=(), total_walk_distance_m=0.0, total_walk_seconds=0,
        spine_area="Le Marais",
    )
    line = _synth_first_leg_text(_stop("Pantheon"), no_leg)
    assert line.source_id == SYNTHESIZED_OPENER
    assert not is_walk_concurrent(line)


def test_generate_puts_the_opening_walk_line_on_the_leg():
    """Through ``generate`` itself: the head-for sentence lands with GLUE_NAV at
    stop 0, so the phone's frozen playback assignment books it onto leg 0."""
    poi = _poi("p1", "Pantheon")
    body = _beat_with_cues("body", poi.id, body="A grounded fact.")
    seq = BeatSequence(
        poi_beats=(
            POIBeats(poi_id=poi.id, poi_name=poi.name,
                     ordering_strategy="narrative_function", beats=(body,)),
        )
    )
    route = _route((poi,))
    route = route.model_copy(
        update={"transits": (route.transits[0].model_copy(update={"walk_seconds": 300}),)}
    )
    script = generate(seq, route, _input(round_trip=True), glue_client=MockGlueClient())
    head_for = [s for s in script.script if "head for Pantheon" in s.text]
    assert head_for, [s.text for s in script.script]
    assert all(s.source_id == GLUE_NAV and s.stop_idx == 0 for s in head_for)


# ---------------------------------------------------------------------------
# Connective-tissue nav glue (replacing the flat "Walk to the next stop.").
# ---------------------------------------------------------------------------

def test_nav_template_names_both_ends_and_the_walk():
    text = _template_nav("Palais Garnier", "Galeries Lafayette", 360, 1)  # 360s -> 6 min
    assert "Palais Garnier" in text and "Galeries Lafayette" in text
    assert "6-minute walk" in text
    assert "Walk to the next stop" not in text
    assert "—" not in text  # no em-dash (TTS-clean)


def test_nav_template_short_leg_reads_just_ahead():
    text = _template_nav("Rue de la Paix", "Place Vendome", 60, 2)  # 60s -> 1 min, <2
    assert "just ahead" in text
    assert "Place Vendome" in text


def test_nav_template_varies_between_consecutive_legs():
    a = _template_nav("A Place", "B Place", 200, 1)
    b = _template_nav("B Place", "C Place", 200, 2)
    assert a.split()[0:3] != b.split()[0:3], "consecutive legs must not read identically"


def test_nav_template_final_destination_is_a_graceful_arrival():
    text = _template_nav("Bourse de Commerce", "Destination", 300, 6,
                         is_final_destination=True)
    assert "final destination" in text.lower()
    assert "Destination" not in text  # never voice the placeholder POI name
    assert "next stop" not in text.lower()


# ---------------------------------------------------------------------------
# D6-ETA: nav glue minutes must derive from the engine's own leg-seconds
# budget (routed leg_seconds, else walk_seconds) — NOT a separate
# round(distance_m / 80) display-pace estimate that can silently diverge
# from the displayed tour total (specs/2026-07-18-tour-qa-campaign/REPORT.md).
# ---------------------------------------------------------------------------


def test_template_nav_minutes_from_leg_seconds_when_routed():
    """A routed leg speaks minutes computed from leg_seconds, not the old
    round(distance_m / 80) haversine-at-80m/min formula. distance_m (240) and
    leg_seconds (660) intentionally disagree here — a slow, winding routed path
    easily beats straight-line distance at 80 m/min — so a regression to the old
    formula would read a different (wrong) minute count.

    TIGHTENED 2026-08-06: a leg is routed when VALHALLA SAID SO, not merely when
    ``leg_seconds`` is set. The fixture therefore declares its provenance, and the
    second half below pins the case that distinction exists for — a client that
    answered with seconds but produced no shape, which is what a mid-route
    degradation looks like. Its number is not one anybody can vouch for, so the
    leg is worth its honest fallback."""
    route = Route(
        pois=(_poi("p", "B Place"),),
        transits=(TransitSegment(from_poi_id=None, to_poi_id="p", distance_m=240.0,
                                 walk_seconds=300, leg_seconds=660,
                                 polyline="fake", source="valhalla"),),
        total_walk_distance_m=240.0, total_walk_seconds=300,
    )
    leg_seconds = _segment_leg_seconds(route, 0)
    assert leg_seconds == 660  # the routed figure, never walk_seconds/distance
    text = _template_nav("A Place", "B Place", leg_seconds, 0)
    assert "11-minute walk" in text
    assert "3-minute" not in text  # what round(240/80) would have said

    degraded = Route(
        pois=(_poi("p", "B Place"),),
        transits=(TransitSegment(from_poi_id=None, to_poi_id="p", distance_m=240.0,
                                 walk_seconds=300, leg_seconds=660,
                                 source="haversine"),),
        total_walk_distance_m=240.0, total_walk_seconds=300,
    )
    assert _segment_leg_seconds(degraded, 0) == 300, (
        "a leg Valhalla did not route must be worth its honest fallback, whatever "
        "number a degraded client left behind"
    )


def test_template_nav_minutes_fall_back_to_walk_seconds_when_unrouted():
    """When a leg has no routed leg_seconds (Valhalla unavailable), minutes
    fall back to walk_seconds — the same fallback options.py's eta_seconds,
    reflection.py, and audit.py already use for this leg."""
    route = Route(
        pois=(_poi("p", "B Place"),),
        transits=(TransitSegment(from_poi_id=None, to_poi_id="p", distance_m=100.0,
                                 walk_seconds=480, leg_seconds=None),),
        total_walk_distance_m=100.0, total_walk_seconds=480,
    )
    leg_seconds = _segment_leg_seconds(route, 0)
    assert leg_seconds == 480
    text = _template_nav("A Place", "B Place", leg_seconds, 0)
    assert "8-minute walk" in text


def test_nav_minutes_stay_consistent_with_options_eta_accounting():
    """The 'announced legs sum to the displayed tour total' honesty
    criterion: the leg-seconds _segment_leg_seconds feeds to _template_nav
    must be the exact same per-leg figures options.py's build_route_option
    sums into eta_seconds — one routed leg, one walk_seconds fallback leg."""
    pois = (
        POI(id="p1", name="Anchor", tier=5, poi_role="stop", lat=48.85, lng=2.35),
        POI(id="p2", name="Second", tier=5, poi_role="stop", lat=48.86, lng=2.36),
    )
    transits = (
        TransitSegment(from_poi_id=None, to_poi_id="p1", distance_m=500.0,
                       walk_seconds=810, leg_seconds=600,
                       polyline="fake", source="valhalla"),  # routed: 600s -> 10 min
        TransitSegment(from_poi_id="p1", to_poi_id="p2", distance_m=300.0,
                       walk_seconds=360, leg_seconds=None),  # fallback: 360s -> 6 min
    )
    route = Route(pois=pois, transits=transits, total_walk_distance_m=800.0,
                  total_walk_seconds=1170)
    script = Script(
        city_slug="paris", generated_at="2026-07-18T00:00:00Z",
        inputs=TourInput(start=(48.85, 2.35), duration_min=60, city_slug="paris"),
        total_audio_seconds=600, total_walking_seconds=1170, total_walk_distance_m=800,
        total_planned_seconds=1770,
        selected_pois=(
            ScriptPOI(id="p1", name="Anchor", tier=5, lat=48.85, lng=2.35, dwell_seconds=300),
            ScriptPOI(id="p2", name="Second", tier=5, lat=48.86, lng=2.36, dwell_seconds=180),
        ),
        lens_coverage={}, script=(), validation=ValidationReport(),
    )
    snapshot = CorpusSnapshot(pois=list(pois), beats_by_poi={}, area_types={}, adjacent_areas={})
    opt = build_route_option(
        route,
        script,
        {},
        route_id="rt",
        snapshot=snapshot,
        sequence=BeatSequence(poi_beats=()),
    )

    leg_seconds_from_generation = [_segment_leg_seconds(route, i) for i in range(len(transits))]
    dwell_total = sum(sp.dwell_seconds for sp in script.selected_pois)
    assert sum(leg_seconds_from_generation) + dwell_total == opt.eta_seconds

    announced_minutes = [_nav_walk_minutes(s) for s in leg_seconds_from_generation]
    assert announced_minutes == [10, 6]


def test_generate_nav_glue_names_destinations_not_flat_walk_to_next_stop():
    """With the mock glue client, a multi-stop tour's nav lines name the
    destination (connective tissue), never the flat 'Walk to the next stop.'."""
    pois = tuple(_poi(f"p{i}", f"Place {i}") for i in range(3))
    seq = BeatSequence(poi_beats=tuple(
        _poi_beats(p, (_beat_with_cues(f"b{i}", p.id, body=f"A fact about place {i} here."),))
        for i, p in enumerate(pois)
    ))
    script = generate(seq, _route(pois), _input(round_trip=True), glue_client=MockGlueClient())
    nav = [s.text for s in script.script if s.source_id == GLUE_NAV]
    assert nav, "expected nav glue between stops"
    assert all(t != "Walk to the next stop." for t in nav)
    assert any("Place" in t for t in nav)  # destinations are named


def test_sum_audio_drops_when_dedup_suppresses_sentences():
    """#8's GUARANTEE, preserved across the 2026-07-19 audio-clock rewrite: a
    claim-dedup-trimmed beat reports only its SURVIVING audio, never the whole
    beat — so tour minutes stay honest when content is suppressed.

    The arithmetic changed (words / SPOKEN_WPM, not a scaled corpus estimate) but
    the property is identical, and now falls out directly: suppressed sentences
    contribute no words, so they contribute no seconds. The old fixture pinned
    est_spoken_seconds=90 on a 9-word body — 6 words per minute, physically
    impossible; the live corpus implies 150 wpm tightly (p10 147, p90 153).
    """
    beat = _beat("b1", "p", body="First fact here. Second fact here. Third fact here.")
    seq = BeatSequence(
        poi_beats=(POIBeats(poi_id="p", poi_name="P", ordering_strategy="narrative_function",
                            beats=(beat,)),)
    )

    def _s(text: str) -> Sentence:
        return Sentence(text=text, source_id="b1", source_type="beat", stop_idx=0)

    all_three = [_s("First fact here."), _s("Second fact here."), _s("Third fact here.")]
    two = [_s("First fact here."), _s("Third fact here.")]

    full = _sum_audio(all_three, seq)
    trimmed = _sum_audio(two, seq)

    # 9 words at SPOKEN_WPM; 6 words survive the dedup -> two thirds of the audio.
    assert full == round(9 / SPOKEN_WPM * 60)
    assert trimmed == round(6 / SPOKEN_WPM * 60)
    assert trimmed < full, "suppressed content must reduce reported audio"


def test_sum_audio_gives_full_credit_when_compose_merges_sentences():
    """#8 (compose refinement)'s GUARANTEE, preserved across the 2026-07-19 rewrite:
    a composer that MERGES a beat's sentences while voicing every word keeps FULL
    audio credit. Re-merging text must never look like losing content.

    Under the words/SPOKEN_WPM clock this is exact rather than approximate: the
    seconds depend only on the words voiced, so any regrouping of the same words
    scores identically by construction.
    """
    body = "First fact here. Second fact here. Third fact here."  # 3 sentences, 9 words
    beat = _beat("b1", "p", body=body)
    seq = BeatSequence(
        poi_beats=(POIBeats(poi_id="p", poi_name="P", ordering_strategy="narrative_function",
                            beats=(beat,)),)
    )

    def _s(text: str) -> Sentence:
        return Sentence(text=text, source_id="b1", source_type="beat", stop_idx=0)

    unmerged = [_s("First fact here."), _s("Second fact here."), _s("Third fact here.")]
    merged = [_s("First fact here, second fact here."), _s("Third fact here.")]

    assert sum(len(m.text.split()) for m in merged) == 9  # words preserved
    assert len(merged) == 2  # fewer sentences than the 3-sentence body
    assert _sum_audio(merged, seq) == _sum_audio(unmerged, seq), (
        "a faithful merge must not change the audio clock"
    )
    assert _sum_audio(merged, seq) == round(9 / SPOKEN_WPM * 60)


def test_sum_audio_credits_glue_by_its_real_length_not_a_flat_estimate():
    """THE FOUNDING DEFECT of the 2026-07-19 audio-clock rewrite.

    The old model credited EVERY non-beat sentence a flat 4 seconds regardless of
    length (``total += glue_count * 4``). A 60-word reflection takes ~24 s to speak
    and was counted as 4 s. That made walk-leg content invisible to the tour's own
    audio clock: the engine would narrate a leg, refuse to count it, conclude it
    was short on audio, and respond by seating ANOTHER STOP — producing more
    walking, hence more silence, which it also would not fill.

    UNDO TEST: restore the flat ``glue_count * 4`` and this goes RED, because a
    long reflection and a short nav line would score identically.
    """
    seq = BeatSequence(poi_beats=())

    def _glue(text: str) -> Sentence:
        return Sentence(text=text, source_id=GLUE_REFLECTION, source_type="glue", stop_idx=1)

    short = [_glue("Head north.")]  # 2 words
    long_reflection = [_glue(" ".join(f"word{i}" for i in range(60)))]  # 60 words

    assert _sum_audio(short, seq) == round(2 / SPOKEN_WPM * 60)
    assert _sum_audio(long_reflection, seq) == round(60 / SPOKEN_WPM * 60)
    assert _sum_audio(long_reflection, seq) > _sum_audio(short, seq) * 10, (
        "a 60-word reflection must not be credited the same as a 2-word nav line"
    )


def test_sensory_invitation_is_not_said_twice_at_the_first_stop():
    """The cold-open and the staging step must not BOTH emit the invitation.

    Seen in the workbench on a real Île de la Cité preview: "You're starting right at
    Pont Neuf. Take a moment to take it in. Look up at the Seine quais... Take a moment
    to take it in." — the same sentence twice, four sentences apart, in the first stop a
    listener ever hears. `_synth_first_leg` ends with the invitation on the
    you-are-already-here branch (leg < 20s), and step 4 appended it again on any view cue.

    UNDO: drop the `already_invited` guard in _synthesized_opener -> this goes RED.
    """
    poi = _poi("p1", "Pont Neuf")
    body = _beat_with_cues(
        "body",
        poi.id,
        cues=(
            PhysicalCue(
                cue="the Seine quais visible from the bridge",
                direction="up",
                feature_type="view",
            ),
        ),
        body="A grounded fact about the bridge.",
    )
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=poi.id,
                poi_name=poi.name,
                ordering_strategy="narrative_function",
                beats=(body,),
            ),
        )
    )
    route = _route((poi,))
    # Standing AT the first stop: this is the branch whose copy ends with the invitation.
    route = route.model_copy(
        update={
            "transits": (
                route.transits[0].model_copy(update={"walk_seconds": 5}),
                *route.transits[1:],
            )
        }
    )

    script = generate(seq, route, _input(round_trip=True), glue_client=MockGlueClient())
    texts = [s.text for s in script.script if s.stop_idx == 0]

    said = sum(t.count(SENSORY_INVITATION) for t in texts)
    assert said <= 1, f"the sensory invitation was said {said} times: {texts}"
    # And the view cue itself must still be staged — the guard must not swallow step 4.
    assert any("Seine quais" in t for t in texts), texts


# ---------------------------------------------------------------------------
# The shut-door filter — the stitched lane never invites the listener through
# a door the plan already priced shut. The premium lane's placement floor
# (validation.door_line_without_door) refuses such a line and a writer
# rewrites it; the stitched lane ships corpus text with no writer, so the
# invitation is dropped at the stitch. Keyed on EXPLICIT False in
# Route.visit_goes_inside: an empty map (dateless or legacy route) licenses
# everything, byte-identical.
# ---------------------------------------------------------------------------


_DOOR_BEAT_BODY = "Step inside the nave. The rose window glass is world famous."


def _one_stop_door_fixture():
    poi = _poi("p1", "Sainte-Chapelle")
    beat = _beat("door-beat", poi.id, body=_DOOR_BEAT_BODY, nf="establishing")
    seq = BeatSequence(poi_beats=(_poi_beats(poi, (beat,)),))
    return poi, seq


def test_a_doorless_stop_drops_its_door_line_and_keeps_the_story():
    """A stop the plan prices outside-only voices no through-the-door line —
    the invitation drops, the facts stay. UNDO: remove the shut-door filter in
    generate() -> "Step inside the nave." plays at a shut door -> RED."""
    poi, seq = _one_stop_door_fixture()
    route = _route((poi,)).model_copy(update={"visit_goes_inside": {poi.id: False}})
    script = generate(seq, route, _input(), glue_client=MockGlueClient())

    texts = [s.text for s in script.script]
    assert not any("Step inside" in t for t in texts), texts
    assert any("rose window glass" in t for t in texts), (
        "only the invitation may drop — never the stop's facts"
    )


def test_a_stop_with_an_open_door_keeps_its_door_line():
    """goes_inside True licenses the invitation — the filter may only ever act
    where the plan says the visit stays outside."""
    poi, seq = _one_stop_door_fixture()
    route = _route((poi,)).model_copy(update={"visit_goes_inside": {poi.id: True}})
    script = generate(seq, route, _input(), glue_client=MockGlueClient())

    assert any("Step inside" in s.text for s in script.script)


def test_a_dateless_route_with_no_door_map_filters_nothing():
    """The identity default: an EMPTY visit_goes_inside map (a dateless or
    legacy route) means nobody priced a door, and the stitch ships byte-
    identical — missing is never treated as False."""
    poi, seq = _one_stop_door_fixture()
    script = generate(seq, _route((poi,)), _input(), glue_client=MockGlueClient())

    assert any("Step inside" in s.text for s in script.script)


def _closed_route(pois, closed_ids, *, all_day=False):
    from src.tour.contract import ClockExclusion

    route = _route(pois)
    return route.model_copy(
        update={
            "visit_goes_inside": {p.id: (p.id not in closed_ids) for p in pois},
            "clock_exclusions": tuple(
                ClockExclusion(
                    poi_id=pid, name=pid, reason="closed all day Monday",
                    kept_outside=True, all_day=all_day,
                )
                for pid in closed_ids
            ),
        }
    )


def test_a_closed_stop_says_so_before_any_of_its_story():
    """Owner ruling: interior description at a closed stop is fine — a gift to
    Camille — as long as the closure is acknowledged ONCE, and first, so Aiko
    is never invited into a shut rotunda and Paulo hears orientation before
    description. The acknowledgment is the stop's FIRST stationary sentence.
    UNDO: drop the closure-line emission -> the stop opens on room-13-style
    content with the word never spoken -> RED."""
    p1 = _poi("p1", "Pantheon")
    p2 = _poi("p2", "Musee de Cluny")
    p1_orient = _beat("p1-orient", "p1", body="Find the dome.", nf="stop_orientation")
    p1_body = _beat("p1-body", "p1", body="Soufflot designed it.", nf="establishing")
    p2_body = _beat(
        "p2-body", "p2", body="The tapestries are in room 13 on the first floor.",
        nf="establishing",
    )
    seq = BeatSequence(poi_beats=(_poi_beats(p1, (p1_orient, p1_body)),
                                  _poi_beats(p2, (p2_body,))))
    route = _closed_route((p1, p2), closed_ids={"p2"})
    script = generate(seq, route, _input(), glue_client=MockGlueClient())

    from src.tour.generation import GLUE_STAGING, is_walk_concurrent

    stop2_stationary = [
        s for s in script.script if s.stop_idx == 1 and not is_walk_concurrent(s)
    ]
    first = stop2_stationary[0]
    assert first.text == (
        "Musee de Cluny is closed at the moment, so we'll take it in from out here."
    ), [s.text for s in stop2_stationary][:3]
    assert first.source_id == GLUE_STAGING and first.source_type == "glue"
    # Said ONCE — and Camille keeps her tapestries.
    said = [s for s in script.script if "closed at the moment" in s.text]
    assert len(said) == 1
    assert any("room 13" in s.text for s in script.script)
    # The line is legal glue: the whole script still validates.
    assert script.validation.passed is True


def test_a_closed_first_stop_says_so_right_after_settle_in():
    """Stop 0's acknowledgment lands after the 'Settle in.' pacing breath and
    before any orientation or story content — first thing said ABOUT the place."""
    poi = _poi("p1", "Pantheon")
    orient = _beat("o", poi.id, body="Find the dome.", nf="stop_orientation")
    seq = BeatSequence(poi_beats=(_poi_beats(poi, (orient,)),))
    route = _closed_route((poi,), closed_ids={poi.id})
    script = generate(seq, route, _input(), glue_client=MockGlueClient())

    texts = [s.text for s in script.script if s.stop_idx == 0]
    assert texts[0] == "Settle in."
    assert texts[1] == (
        "Pantheon is closed at the moment, so we'll take it in from out here."
    ), texts[:3]


def test_an_all_day_closure_says_closed_today_not_at_the_moment():
    """Aiko's wasted return trip (07, step 6): "closed at the moment" reads as
    "opens later today", which is a lie at a museum shut ALL day. The all_day
    flag — set by selection from the day's own empty window list, never from
    words — picks the honest branch. A partial closure keeps "at the moment"
    (true whether the door opens later or already shut)."""
    poi = _poi("p1", "Musee de Cluny")
    beat = _beat("b", poi.id, body="The tapestries are in room 13.", nf="establishing")
    seq = BeatSequence(poi_beats=(_poi_beats(poi, (beat,)),))

    all_day = generate(
        seq, _closed_route((poi,), {poi.id}, all_day=True), _input(),
        glue_client=MockGlueClient(),
    )
    assert any(
        s.text == "Musee de Cluny is closed today, so we'll take it in from out here."
        for s in all_day.script
    ), [s.text for s in all_day.script][:4]
    assert not any("at the moment" in s.text for s in all_day.script)

    partial = generate(
        seq, _closed_route((poi,), {poi.id}, all_day=False), _input(),
        glue_client=MockGlueClient(),
    )
    assert any("closed at the moment" in s.text for s in partial.script)


def test_no_staging_cue_points_behind_a_shut_door():
    """Acceptance's Aiko finding: three sentences after "we'll take it in from
    out here", the synthesized opener said "Look up at crypt with tombs of…" —
    the guide's own voice directing her at something behind the shut door. At
    a clock-closed stop the cue staging line is suppressed; the closure line
    already stages the stop. An open stop keeps its cue (the existing
    view-cue tests are the pin)."""
    poi = _poi("p1", "Pantheon")
    body = _beat_with_cues(
        "body", poi.id,
        cues=(PhysicalCue(cue="crypt with tombs of Voltaire", direction="up",
                          feature_type="view"),),
        body="A grounded fact.",
    )
    seq = BeatSequence(poi_beats=(_poi_beats(poi, (body,)),))
    script = generate(
        seq, _closed_route((poi,), {poi.id}, all_day=False), _input(),
        glue_client=MockGlueClient(),
    )
    texts = [s.text for s in script.script if s.stop_idx == 0]
    assert not any("Look up at" in t or "Notice " in t for t in texts), texts
    assert any("closed at the moment" in t for t in texts)


def test_an_open_day_and_screen_only_notes_speak_no_closure_line():
    """Identity pins: no exclusions → no line, byte-identical; and an exclusion
    with kept_outside=False (a no-time step-outside, a dusk note, a not-on-route
    removal) is a SCREEN note, never a spoken one — the place is not closed and
    saying so would be the checkable lie."""
    from src.tour.contract import ClockExclusion

    poi = _poi("p1", "Pantheon")
    body = _beat("b", poi.id, body="Soufflot designed it.", nf="establishing")
    seq = BeatSequence(poi_beats=(_poi_beats(poi, (body,)),))

    plain_script = generate(seq, _route((poi,)), _input(), glue_client=MockGlueClient())
    assert not any("closed at the moment" in s.text for s in plain_script.script)

    no_time = _route((poi,)).model_copy(
        update={
            "visit_goes_inside": {poi.id: False},
            "clock_exclusions": (
                ClockExclusion(
                    poi_id=poi.id, name=poi.name,
                    reason="its interior does not fit the 60 minutes you asked for, "
                           "so we will see it from the outside",
                    kept_outside=False,
                ),
            ),
        }
    )
    no_time_script = generate(seq, no_time, _input(), glue_client=MockGlueClient())
    assert not any("closed at the moment" in s.text for s in no_time_script.script)


def test_a_closed_start_not_in_the_day_is_named_first():
    """The walker STANDS at the start beside a shut door the plan removed
    (Docs/adr/0004's honest removal, or a pool-rule ejection). Walking away
    from it wordlessly is the checkable lie by omission: the person can SEE
    the place. An exclusion flagged ``at_start`` — set by selection from
    coordinates, never recovered from words — whose place is NOT on the route
    is named right after the "Settle in." breath, with the same
    today/at-the-moment honesty split the on-route line carries.
    UNDO: drop the at_start branch -> the day opens straight into p1's story
    with the shut door never spoken -> RED."""
    from src.tour.contract import ClockExclusion

    poi = _poi("p1", "Pantheon")
    body = _beat("b", poi.id, body="Soufflot designed it.", nf="establishing")
    seq = BeatSequence(poi_beats=(_poi_beats(poi, (body,)),))

    def _start_route(all_day: bool):
        return _route((poi,)).model_copy(
            update={
                "clock_exclusions": (
                    ClockExclusion(
                        poi_id="orsay", name="Musee d'Orsay",
                        reason="closed all day Monday",
                        kept_outside=False, all_day=all_day, at_start=True,
                    ),
                ),
            }
        )

    script = generate(seq, _start_route(all_day=True), _input(), glue_client=MockGlueClient())
    texts = [s.text for s in script.script if s.stop_idx == 0]
    assert texts[0] == "Settle in."
    assert texts[1] == (
        "Musee d'Orsay, right here at the start, is closed today, "
        "so the walk goes on without it."
    ), texts[:3]
    from src.tour.generation import GLUE_STAGING

    said = [s for s in script.script if "right here at the start" in s.text]
    assert len(said) == 1
    assert said[0].source_id == GLUE_STAGING and said[0].source_type == "glue"
    # The line is legal glue: the whole script still validates.
    assert script.validation.passed is True

    partial = generate(
        seq, _start_route(all_day=False), _input(), glue_client=MockGlueClient()
    )
    assert any(
        "right here at the start, is closed at the moment" in s.text
        for s in partial.script
    )


def test_a_closed_start_yields_to_the_first_stops_own_closure_line():
    """When stop 0 itself is clock-closed, its own acknowledgment holds the
    first-stationary slot and the off-route start line stands down — one
    closure sentence opens the day, never two stacked. The screen channel
    still carries both exclusions."""
    from src.tour.contract import ClockExclusion

    poi = _poi("p1", "Pantheon")
    body = _beat("b", poi.id, body="Soufflot designed it.", nf="establishing")
    seq = BeatSequence(poi_beats=(_poi_beats(poi, (body,)),))
    route = _route((poi,)).model_copy(
        update={
            "clock_exclusions": (
                ClockExclusion(
                    poi_id=poi.id, name=poi.name, reason="closed all day Monday",
                    kept_outside=True, all_day=True,
                ),
                ClockExclusion(
                    poi_id="orsay", name="Musee d'Orsay",
                    reason="closed all day Monday",
                    kept_outside=False, all_day=True, at_start=True,
                ),
            ),
        }
    )
    script = generate(seq, route, _input(), glue_client=MockGlueClient())
    texts = [s.text for s in script.script if s.stop_idx == 0]
    assert texts[1] == "Pantheon is closed today, so we'll take it in from out here."
    assert not any("right here at the start" in s.text for s in script.script)


def test_a_dropped_only_invitation_beat_is_not_reported_as_voiced():
    """A beat that was ONLY an invitation at a shut door emits nothing — and
    the voiced roster must say so (the same truth-from-emissions rule the
    transit-drop case pins): its id leaves beat_ids, so keep-exploring can
    reclassify it rather than treating it as already heard."""
    poi = _poi("p1", "Sainte-Chapelle")
    invitation_only = _beat("inv", poi.id, body="Step inside.", nf="establishing")
    story = _beat("story", poi.id, body="Louis IX built it for the relics.", nf="establishing")
    seq = BeatSequence(poi_beats=(_poi_beats(poi, (invitation_only, story)),))
    route = _route((poi,)).model_copy(update={"visit_goes_inside": {poi.id: False}})
    script = generate(seq, route, _input(), glue_client=MockGlueClient())

    sp = script.selected_pois[0]
    assert "story" in sp.beat_ids
    assert "inv" not in sp.beat_ids, (
        "a beat whose every sentence dropped at the shut door must not be "
        "reported as voiced"
    )


def test_a_transit_beat_that_speaks_a_bearing_is_never_picked_for_the_leg():
    """S1.M3: the refusal happens at the PICK, not at the report. A beat whose encoded
    bearing cannot be verified against this route's own direction is not used, and the
    leg falls through to the deterministic template — which names both ends, speaks the
    routed minutes and cannot say a compass point."""
    bearing = _beat(
        "t-compass",
        "b",
        body="Leave Notre-Dame by the north portal and turn right.",
        nf="transition",
    )
    stop = POIBeats(
        poi_id="b", poi_name="B", ordering_strategy="narrative_function", beats=(bearing,)
    )
    assert _find_directional_transit_beat(stop, prev_name="Notre-Dame") is None


def test_a_transit_beat_is_only_reusable_when_a_name_can_vouch_for_its_direction():
    """Called with no names there is nothing to check the beat's direction against, so
    it cannot be shown to describe this segment. The old fail-open returned it anyway.

    P9R-S1.M1 amendment (decisions doc §2: "the corpus transit-beat substring
    gate ... under-covers", wrong directions on ANY day): the second assertion
    used to accept "the square" as origin evidence when the body merely walks
    INTO the square — the destination-mention fault. A previous stop now
    vouches as ORIGIN only (trigger_address, or the opening origin clause), so
    the same call picks nothing."""
    plain = _beat("t-plain", "b", body="Walk on to the square.", nf="transition")
    stop = POIBeats(
        poi_id="b", poi_name="B", ordering_strategy="narrative_function", beats=(plain,)
    )
    assert _find_directional_transit_beat(stop) is None
    assert _find_directional_transit_beat(stop, prev_name="the square") is None


def test_a_destination_street_mention_never_vouches_for_an_origin():
    """P9R-S1.M1 — Paulo's fatal pick, as measured. Frommer's Châtelet approach
    ("Walk up boulevard de Sébastopol and turn right into rue de Rivoli into
    square de la Tour St-Jacques") was picked for a walker arriving ALONG rue
    de Rivoli from the east, because the previous stop is a street the beat
    merely turns into. The line is true of the guidebook's own approach and
    false of his; the pick must refuse it and fall to the template that names
    both real ends. UNDO: restore the substring haystack match for prev_name
    -> the Frommer's beat is picked again -> RED."""
    frommers = _beat(
        "t-seb",
        "tsj",
        body="Walk up boulevard de Sébastopol and turn right into rue de Rivoli "
        "into square de la Tour St-Jacques.",
        nf="transition",
    )
    stop = POIBeats(
        poi_id="tsj",
        poi_name="Tour Saint-Jacques",
        ordering_strategy="narrative_function",
        beats=(frommers,),
    )
    assert _find_directional_transit_beat(stop, prev_name="Rue de Rivoli") is None


def test_a_verified_origin_still_vouches_two_ways():
    """The pick keeps its value where the direction is real: a previous stop
    named in the beat's trigger_address, or named by the body's opening origin
    clause, is a verified origin — the beat genuinely describes arriving from
    there, so its turns are true for this walk."""
    by_addr = _beat(
        "t-addr",
        "tsj",
        body="Walk up to the square and the tower rises ahead.",
        nf="transition",
        addr="corner of Rue de Rivoli",
    )
    by_clause = _beat(
        "t-clause",
        "tsj",
        body="From the Rue de Rivoli, cut through to the square de la Tour "
        "St-Jacques and the tower rises ahead.",
        nf="transition",
    )
    stop_addr = POIBeats(
        poi_id="tsj", poi_name="Tour Saint-Jacques",
        ordering_strategy="narrative_function", beats=(by_addr,),
    )
    stop_clause = POIBeats(
        poi_id="tsj", poi_name="Tour Saint-Jacques",
        ordering_strategy="narrative_function", beats=(by_clause,),
    )
    assert _find_directional_transit_beat(stop_addr, prev_name="Rue de Rivoli") is by_addr
    assert _find_directional_transit_beat(stop_clause, prev_name="Rue de Rivoli") is by_clause


def test_the_walks_own_narration_is_walk_concurrent():
    """P9R-S1.M2 — a corpus transit beat is picked FOR the leg (`_build_transit`)
    but its sentences used to classify as stationary: `is_walk_concurrent` knew
    only glue labels and vignette ids, so the playback freeze booked the walk's
    own narration onto the STOP, and the placement floor then refused the day
    for a motion imperative that is true while walking. The transit ids are the
    third input to THE one rule; omitted, the conservative stationary default
    stands. UNDO: drop the `transit_beat_ids` membership check -> RED."""
    line = Sentence(
        text="Walk up the boulevard and the tower rises ahead.",
        source_id="t-1",
        source_type="beat",
        stop_idx=1,
    )
    assert not is_walk_concurrent(line), "omitted ids stay the conservative default"
    assert is_walk_concurrent(line, frozenset(), frozenset({"t-1"}))
    assert not is_walk_concurrent(line, frozenset(), frozenset({"other"}))


def test_transit_class_beat_ids_reads_the_one_function_set():
    """The helper every caller derives the leg's beat ids with reads
    TRANSIT_NARRATIVE_FUNCTIONS — one definition, no drift."""
    walk = _beat("t-w", "p", body="Walk on.", nf="transition")
    story = _beat("s-1", "p", body="A story.", nf="establishing")
    assert transit_class_beat_ids([walk, story]) == frozenset({"t-w"})


def test_a_destination_mentioned_in_passing_never_vouches_either():
    """P9R exit-panel finding: the departure lane kept a whole-body substring,
    so a beat at the departure stop walking to Les Halles — which merely says
    "you will pass the Tour Saint-Jacques on your right" — was pickable for
    the leg TO the tower. A departure beat names its destination up front
    (trigger_address or the first sentence); scenery in a later sentence
    vouches for nothing. UNDO: restore the haystack substring for dest_name
    -> the pass-by beat is picked -> RED."""
    passing = _beat(
        "t-lh",
        "chatelet",
        body="Leave the Place du Chatelet and walk up boulevard de Sebastopol "
        "to Les Halles. You will pass the Tour Saint-Jacques on your right.",
        nf="transition",
    )
    stop = POIBeats(
        poi_id="chatelet", poi_name="Place du Châtelet",
        ordering_strategy="narrative_function", beats=(passing,),
    )
    assert _find_directional_transit_beat(stop, dest_name="Tour Saint-Jacques") is None
    assert _find_directional_transit_beat(stop, dest_name="Les Halles") is passing, (
        "the destination the first sentence names still vouches"
    )


def test_an_itinerary_phrase_is_refused_at_the_pick_like_a_compass_point():
    """A guidebook's own itinerary ("where the walk ends", "our next stop") is
    true of its route and never of this one — the same class as compass, refused
    at the same gate."""
    itinerary = _beat(
        "t-itin",
        "tsj",
        body="From the Rue de Rivoli, walk on to where the walk ends at the tower.",
        nf="transition",
    )
    stop = POIBeats(
        poi_id="tsj", poi_name="Tour Saint-Jacques",
        ordering_strategy="narrative_function", beats=(itinerary,),
    )
    assert _find_directional_transit_beat(stop, prev_name="Rue de Rivoli") is None
