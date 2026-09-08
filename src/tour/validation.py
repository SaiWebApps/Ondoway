"""Source-traceability + forbidden-phrase validation — §3.5/§3.6.

Runs after ``generate(...)`` and produces a ``ValidationReport``. The
caller (skill orchestrator, CLI, or test harness) treats a non-empty
report as a hard failure — these checks are the gate between
generation and TTS per phase-1-design rule 8.

Two independent checks:

1. **Source-traceability.** Every Sentence must satisfy one of:
   - ``source_type == 'beat'`` and ``source_id`` is a known beat ID in
     the BeatSequence; OR
   - ``source_type in {'glue','arith'}`` and ``source_id`` is a member
     of ``generation.GLUE_LABELS``.
   Anything else lands in ``untraceable_sentences``.

2. **Forbidden-phrase scan.** Glue sentences may not contain
   "imagine" / "picture this" / "envision" / "visualize" (rule 32 +
   feedback_tour_tone_default). Glue sentences may not introduce a
   proper noun or 4-digit year that does not appear in any cited
   beat's ``script_body`` from the same Script.

Beat-cited sentences are NOT scanned for forbidden words — the corpus
itself is canonical, and editorial review caught those at extraction
time. Validation guards against runtime invention only.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping

from .contract import BeatSequence, Script, Sentence, ValidationReport
from .generation import (
    _COMPASS_RE,
    FORBIDDEN_PHRASES,
    FORWARD_PROMISE_PHRASES,
    FORWARD_SIGHT_PHRASES,
    GLUE_LABELS,
    GLUE_NAV,
    TRANSIT_NARRATIVE_FUNCTIONS,
    is_walk_concurrent,
    split_sentences,
    spoken_minute_counts,
)

# Capitalized tokens past the first word are candidate proper nouns.
# Limited to 3+ letters so single-letter "I" and "A" don't trip it. The
# accent class admits the diacritics that show up in French names.
_CAP_TOKEN_RE = re.compile(r"\b[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'\-]{2,}\b")
_YEAR_RE = re.compile(r"\b1[0-9]{3}\b|\b20[0-2][0-9]\b")

# Common English/French sentence-starter words that shouldn't count as
# proper nouns when they appear capitalized at the head of a sentence.
_SENTENCE_HEAD_WORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "but",
        "or",
        "so",
        "for",
        "yet",
        "this",
        "that",
        "these",
        "those",
        "here",
        "there",
        "you",
        "your",
        "we",
        "our",
        "i",
        "walk",
        "stand",
        "step",
        "find",
        "look",
        "turn",
        "press",
        "now",
        "next",
        "then",
        "settle",
        "take",
        "stop",
        "end",
        "cross",
        "exit",
        "le",
        "la",
        "les",
        "un",
        "une",
        "des",
        "du",
        "de",
        "you've",
        "we've",
        "let's",
    }
)


def validate_script(
    script: Script,
    beat_sequence: BeatSequence,
    *,
    spine_area: str | None = None,
    disclosed_place_names: tuple[str, ...] = (),
) -> ValidationReport:
    """Run both gates and return the report.

    ``disclosed_place_names`` are the names the day's own disclosure records
    carry (the route's clock exclusions) — engine-chosen corpus records, not
    glue inventions, and the closed-start acknowledgment must name a place
    that is on no stop list at all. The caller holding the Route supplies
    them; the empty default keeps every route-less call byte-identical."""
    traceability = validate_source_traceability(script, beat_sequence)
    forbidden = _forbidden_phrase_hits(
        script,
        beat_sequence,
        spine_area=spine_area,
        disclosed_place_names=disclosed_place_names,
    )
    return traceability.model_copy(
        update={"forbidden_phrase_hits": tuple(forbidden)}
    )


def validate_source_traceability(
    script: Script,
    beat_sequence: BeatSequence,
    *,
    allowed_derived_source_ids: frozenset[str] | None = None,
) -> ValidationReport:
    """Structural provenance only; semantic FACT review owns prose meaning."""
    return ValidationReport(
        untraceable_sentences=tuple(
            _untraceable_sentences(
                script,
                beat_sequence,
                allowed_derived_source_ids=allowed_derived_source_ids,
            )
        )
    )


# ---------------------------------------------------------------------------
# Source-traceability
# ---------------------------------------------------------------------------


def _untraceable_sentences(
    script: Script,
    beat_sequence: BeatSequence,
    *,
    allowed_derived_source_ids: frozenset[str] | None = None,
) -> list[Sentence]:
    # Track B (B.4): the known-id set derives from poi_beats + vignette_beats
    # INTERNALLY — a walk-past one-liner is beat-cited against a vignette
    # beat, which is not a POIBeats entry. The signature does not change.
    known_beat_ids = {b.id for plan in beat_sequence.poi_beats for b in plan.beats}
    known_beat_ids |= {b.id for beats in beat_sequence.vignette_beats.values() for b in beats}
    derived_source_ids = (
        GLUE_LABELS
        if allowed_derived_source_ids is None
        else allowed_derived_source_ids
    )
    out: list[Sentence] = []
    for sentence in script.script:
        if sentence.source_type == "beat":
            # Multi-beat citation: the primary AND every fused (also_cites) id
            # must trace to a real beat.
            if any(bid not in known_beat_ids for bid in sentence.cited_beat_ids):
                out.append(sentence)
        elif sentence.source_type in ("glue", "arith"):
            if sentence.source_id not in derived_source_ids:
                out.append(sentence)
        else:
            out.append(sentence)
    return out


# ---------------------------------------------------------------------------
# Forbidden-phrase scan
# ---------------------------------------------------------------------------


def _forbidden_phrase_hits(
    script: Script,
    beat_sequence: BeatSequence,
    *,
    spine_area: str | None = None,
    disclosed_place_names: tuple[str, ...] = (),
) -> list[tuple[Sentence, str]]:
    out: list[tuple[Sentence, str]] = []
    cited_text = _cited_beat_corpus_text(script, beat_sequence)
    cited_proper_nouns = _proper_nouns_in(cited_text)
    # The day's own disclosure records (clock exclusions) name places the
    # engine chose from corpus records and the notes channel already states;
    # the closed-start line speaks one such name at a place on no stop list.
    for name in disclosed_place_names:
        cited_proper_nouns |= _proper_nouns_in(name)
    # THE TOUR'S OWN PLACE VOCABULARY IS NOT AN INVENTION. This scan exists to
    # catch glue asserting a fact the corpus never gave it — a name, a date, a
    # claim the walker cannot check. The names of the stops the engine SEATED, and
    # of the areas they sit in, are corpus records the engine chose the route from;
    # they are the one vocabulary glue is entitled to use.
    #
    # Without this, the synthesized opener fails whenever the spine area's name
    # happens not to appear inside a cited beat's body — "You're starting in
    # Opéra-Garnier" was flagged on the 300-minute Rue Royale tour purely because
    # the beats about its stops never spell the neighbourhood out. That is a
    # validation FAILURE on a correct tour, which is worse than no check: it
    # teaches whoever reads the report to ignore it.
    for stop in script.selected_pois:
        cited_proper_nouns |= _proper_nouns_in(stop.name)
        if stop.area:
            cited_proper_nouns |= _proper_nouns_in(stop.area)
    # The SPINE is the neighbourhood the walk starts in, and it is not always one
    # any stop belongs to — a Rue Royale tour starts in Opéra-Garnier and its first
    # stop is already in the 1st. The opener's whole job is to say where the walker
    # is standing, so it has to be allowed to name that, and the name is the
    # engine's own choice from the corpus rather than anything glue made up.
    if spine_area:
        cited_proper_nouns |= _proper_nouns_in(spine_area)
    # THE CITY'S OWN NAME AND DEMONYM ARE THE WALK'S VOCABULARY (Phase 6 W6.12,
    # measured: "the height of Parisian luxury" in an authored close was refused as
    # new_proper_noun:Parisian, three attempts, the day dead). The tour's own city
    # can never be an invention.
    cited_proper_nouns |= _city_vocabulary(script.city_slug)
    cited_years = set(_YEAR_RE.findall(cited_text))

    # Phase 6 S6.5 (W6.2 R4): the names of stops LATER than each stop index — a
    # "you'll see"-class phrase is a promise only when it points past the stop.
    stop_names = [poi.name for poi in script.selected_pois]

    def _names_a_later_stop(text: str, stop_idx: int) -> bool:
        folded = _fold(text).lower()
        return any(
            _fold(name).lower() in folded
            for name in stop_names[stop_idx + 1 :]
            if name
        )

    # A transit beat's BEARING is the one corpus claim that can be false on this
    # route while being true of the walk its guidebook took: a place's facts hold
    # for whoever walks up to it, a direction does not. The pick refuses these
    # beats; this is the backstop that names one arriving by any other path.
    transit_beat_ids = {
        beat.id
        for plan in beat_sequence.poi_beats
        for beat in plan.beats
        if (beat.narrative_function or "").lower() in TRANSIT_NARRATIVE_FUNCTIONS
    }

    for sentence in script.script:
        if sentence.source_type == "beat":
            # Corpus is canonical about what a place IS — only the bearing is scanned.
            if sentence.source_id in transit_beat_ids:
                for match in _COMPASS_RE.finditer(sentence.text):
                    out.append((sentence, f"leg_voice_compass:{match.group(0).lower()}"))
            continue
        lower = sentence.text.lower()
        for phrase in FORBIDDEN_PHRASES:
            if phrase in lower:
                out.append((sentence, f"forbidden_phrase:{phrase}"))

        # Phase 6 S6.5 (W6.2 R4, LOCKED 8/11): a stop's text may NAME its neighbour
        # as a fact but never PROMISE it — the session may trade the next stop away.
        # GLUE_NAV is the map speaking ("Next, walk to X" is its job) and is exempt.
        if sentence.source_id != GLUE_NAV:
            for phrase in FORWARD_PROMISE_PHRASES:
                if phrase in lower:
                    out.append((sentence, f"forward_promise:{phrase}"))
            for phrase in FORWARD_SIGHT_PHRASES:
                if phrase in lower and _names_a_later_stop(sentence.text, sentence.stop_idx):
                    out.append((sentence, f"forward_promise:{phrase}"))

        # Proper-noun + year leakage in glue. GLUE_NAV is exempt from the
        # proper-noun half (Phase 6 W6.12, measured: the nav line naming the
        # Seine was refused as new_proper_noun:Seine): the nav line is the MAP
        # speaking — its nouns are places by nature, exactly as the
        # forward-promise scan already treats it. Years and the phrase list
        # still apply to it; story glue keeps the full scan. What the nav voice
        # may NOT do is speak compass (Phase 8 S8.3, W8.2 R1 11/11): a leg line
        # names direction only as left/right/straight-ahead or a visible
        # landmark — "Walk northwest along the Seine" pointed Greta the wrong
        # way because the words never knew the route's own bearing.
        if sentence.source_id == GLUE_NAV:
            for match in _COMPASS_RE.finditer(sentence.text):
                out.append((sentence, f"leg_voice_compass:{match.group(0).lower()}"))
            for year in _YEAR_RE.findall(sentence.text):
                if year not in cited_years:
                    out.append((sentence, f"new_year:{year}"))
            continue
        for token in _proper_nouns_in(sentence.text, drop_first_word=True):
            if token.lower() in _SENTENCE_HEAD_WORDS:
                continue
            if _name_is_licensed(token, cited_proper_nouns):
                continue
            out.append((sentence, f"new_proper_noun:{token}"))
        for year in _YEAR_RE.findall(sentence.text):
            if year in cited_years:
                continue
            out.append((sentence, f"new_year:{year}"))
    return out


#: The city's own name and demonym, licensed for glue in every tour of that city.
#: Keyed by city_slug; an unknown slug licenses its title-cased form alone.
_CITY_VOCABULARY: dict[str, tuple[str, ...]] = {
    "paris": ("Paris", "Parisian", "Parisians"),
    "london": ("London", "Londoner", "Londoners"),
    "new_york": ("New", "York", "Yorker", "Yorkers"),
}


def _city_vocabulary(city_slug: str | None) -> set[str]:
    if not city_slug:
        return set()
    return set(
        _CITY_VOCABULARY.get(
            city_slug.lower(), (city_slug.replace("_", " ").title(),)
        )
    )


# The possessive, plural and plural-possessive endings the composer writes onto a
# name — "Ravaillac's knife", "Ravaillac\u2019s" (the curly apostrophe), "the Ravaillacs'"
# (the tokenizer drops a trailing bare apostrophe, so that one arrives as "Ravaillacs").
_POSSESSIVE_ENDINGS: tuple[str, ...] = ("'s", "\u2019s", "s'", "s\u2019", "s", "'", "\u2019")


def _fold(name: str) -> str:
    """A name without its diacritics — "André" and "Andre" are one name."""
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", name) if not unicodedata.combining(ch)
    )


def _name_is_licensed(token: str, licensed: set[str]) -> bool:
    """Is ``token`` a name the cited corpus carries — in ANY orthographic form?

    An INFLECTED or RE-ACCENTED form of a licensed name is the same name, not a new
    one: the tokenizer (``_CAP_TOKEN_RE``) keeps an apostrophe-s inside the token, so
    "Ravaillac's" was compared against a set holding "Ravaillac" and refused as an
    invention; "André" was refused against a corpus that spells "Andre". Measured
    2026-08-19 (Phase 6 W6.1): Fiona & Dev's compose was refused over the wire, every
    attempt, for "…died under Francis Ravaillac's knife" and "André Maurois ranks
    him…" — reflections drawing on a voiced beat's own key claims. Fold diacritics,
    strip the possessive/plural endings, compare again; a genuinely different name
    ("François" for a corpus that says "Francis") still fails — that is a rename, and
    the prompt's own rule is to use the name the beats give.
    """
    if token in licensed:
        return True
    folded = {_fold(name) for name in licensed}
    candidates = [token]
    for ending in _POSSESSIVE_ENDINGS:
        if token.endswith(ending) and len(token) > len(ending) + 2:
            bare = token[: -len(ending)]
            candidates.append(bare)
            if ending in ("s'", "s\u2019"):
                candidates.append(f"{bare}s")
    # A HYPHENATED COMPOUND whose capitalised head is licensed is that name in
    # adjectival dress, not a new one (Phase 6 W6.12, measured on Camille's day:
    # "From a Roman-style arch for Napoleon…" was refused as
    # new_proper_noun:Roman-style against a corpus that says "Roman" freely).
    if "-" in token:
        candidates.extend(part for part in token.split("-") if part and part[0].isupper())
    return any(c in licensed or _fold(c) in folded for c in candidates)


def _cited_beat_corpus_text(script: Script, beat_sequence: BeatSequence) -> str:
    cited_ids: set[str] = {s.source_id for s in script.script if s.source_type == "beat"}
    chunks: list[str] = []
    for plan in beat_sequence.poi_beats:
        chunks.append(plan.poi_name)  # the POI's own name is canonical context
        for beat in plan.beats:
            if beat.id in cited_ids:
                if beat.script_body:
                    chunks.append(beat.script_body)
                # Phase 4 (Step 4.2): key_claims are corpus-derived facts a
                # reflection legitimately quotes; their proper nouns/years are
                # canonical context, same class as cues/pronunciation.
                chunks.extend(beat.key_claims)
            # Phase 7.5: physical_cues + pronunciation are corpus-derived
            # facts that can surface in the synthesized cold-open. Include
            # them in canonical context so glue-validation doesn't flag
            # cue proper nouns ("Café Ma Bourgogne") as runtime invention.
            for cue in beat.physical_cues:
                if cue.cue:
                    chunks.append(cue.cue)
            if beat.pronunciation:
                chunks.append(beat.pronunciation)
    # Track B (B.4): cited vignette beats are corpus text too — their
    # script_body/key_claims join the canonical context exactly like
    # anchor-beat text (the walk-past one-liner is beat-cited, and glue may
    # legitimately reference facts the corpus already voiced).
    for beats in beat_sequence.vignette_beats.values():
        for beat in beats:
            if beat.id in cited_ids:
                if beat.script_body:
                    chunks.append(beat.script_body)
                chunks.extend(beat.key_claims)
    # Phase 7.5: Area names that surface in the synthesized opener
    # ("the Marais", "the Île de la Cité") are canonical context too.
    for poi in script.selected_pois:
        if poi.area:
            chunks.append(poi.area)
    return "\n".join(chunks)


# ---------------------------------------------------------------------------
# Phase 8 S8.3 — the placement floors: a sentence is true where it PLAYS
# (W8.2 LOCKED RULINGS R1/R2/R5; phase7-ledger carry 1; design §5.6/§8.2).
# Pure text checks over the composed script; the ROUTE-aware inputs (the legs'
# routed minutes, each stop's door, which stops are tap-only) arrive as plain
# mappings built by the authoring finalizer's closure, so this module stays
# route-free and there is ONE definition of every floor.
# ---------------------------------------------------------------------------

#: Arrived deixis — words only true standing AT the place (R2's own list: here,
#: this, you're standing, look up). "From here" is the leg's START and is true
#: where it plays (the final-destination template's own words).
_ARRIVED_DEIXIS_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\byou'?re standing\b|\byou are standing\b", re.IGNORECASE),
    re.compile(r"\blook up\b", re.IGNORECASE),
    re.compile(r"(?<![Ff]rom )\bhere\b", re.IGNORECASE),
    re.compile(r"\bthis\b|\bthese\b", re.IGNORECASE),
)

#: A through-the-door line — legal ONLY where the wire says door=true (R2,
#: Aiko's rule: her Bourse's words invited her into a closed rotunda). Verb
#: phrases only: "a walk in the park" or "the walk to the tower" are nouns and
#: must never read as an invitation.
_DOOR_LINE_RE = re.compile(
    r"\b(?:step in(?:side|to)?|go in(?:side)?|head inside|enter)\b",
    re.IGNORECASE,
)

#: An imperative of motion — navigation language. Legal only in the nav line
#: (R5: "navigation lives only in leg lines"); auto-played at a stop it MOVES
#: the listener (Rosemary's "Step around the corner to 31 rue de Bellechasse").
#: The mid-sentence half fires only on unambiguous VERB uses — an unambiguous
#: phrase, or a motion verb chained after and/then/now/a comma — so a close's
#: "brings our walk to a close" (the noun) never reads as an instruction.
_MOTION_LEAD_RE = re.compile(
    r"^\W*(?:walk|head|turn|cross|continue|follow|climb|leave|exit|go|"
    r"carry on|make your way|step around)\b",
    re.IGNORECASE,
)
_MOTION_MID_RE = re.compile(
    r"\b(?:step around the corner|make your way|carry on to|head (?:for|to)|"
    r"turn (?:left|right))\b"
    r"|(?:\b(?:and|then|now)\b|,)\s+(?:walk|cross|turn|continue|follow)\b",
    re.IGNORECASE,
)


def _arrived_word_in(unit: str) -> str | None:
    for pattern in _ARRIVED_DEIXIS_RES:
        match = pattern.search(unit)
        if match:
            return match.group(0).lower()
    return None


def placement_floor_hits(
    script: Script,
    *,
    vignette_beat_ids: frozenset[str] | set[str],
    leg_minutes_by_stop: Mapping[int, int],
    goes_inside_by_stop: Mapping[int, bool],
    tap_only_stops: frozenset[int] | set[int] = frozenset(),
    transit_beat_ids: frozenset[str] | set[str] = frozenset(),
) -> list[tuple[Sentence, str]]:
    """(sentence, code) for every sentence untrue WHERE IT PLAYS (W8.2 R1/R2/R5).

    Placement is the frozen rule itself (``is_walk_concurrent`` + the vignette
    and transit ids — a corpus transit beat is the walk's own narration and
    plays ON the leg, so its motion imperatives are true where they play),
    never inferred from prose. Checked SENTENCE BY SENTENCE within each
    piece (R2, Marcus: "never the opening alone"). The floors:

    - ``fused_across_playback_contexts`` — one sentence citing both a seated
      beat and a walk-past vignette beat cannot be placed at all (W8.1(f)
      mechanism (c): the bare ``partition_final_script`` ValueError, named).
    - ``arrived_word_on_leg:<word>`` — no standing verb or arrived deictic in
      any leg piece (nav, thread, vignette one-liner alike).
    - ``leg_voice_minutes:<spoken>_routed_<n>`` — the nav line speaks minutes
      only as the routed leg's own number (Théo: "written five, measured
      nine"). A leg whose routed minutes are unknown is skipped, never guessed.
    - ``moving_line_auto_played:<phrase>`` / ``door_line_without_door:<phrase>``
      — no imperative of motion in an auto-played stop piece; a through-the-door
      line only where the wire says door=true. Tap-only stops (the full
      telling) are exempt: moving sentences are tap-only or leg-only.
    """
    out: list[tuple[Sentence, str]] = []
    vignettes = frozenset(vignette_beat_ids)
    transits = frozenset(transit_beat_ids)
    for sentence in script.script:
        cited = set(sentence.cited_beat_ids)
        if cited & vignettes and cited - vignettes:
            out.append((sentence, "fused_across_playback_contexts"))
        units = split_sentences(sentence.text) or [sentence.text]
        if is_walk_concurrent(sentence, vignettes, transits):
            for unit in units:
                word = _arrived_word_in(unit)
                if word:
                    out.append((sentence, f"arrived_word_on_leg:{word}"))
            if sentence.source_id == GLUE_NAV:
                routed = leg_minutes_by_stop.get(sentence.stop_idx)
                if routed is not None:
                    for count in spoken_minute_counts(sentence.text):
                        if count != routed:
                            out.append(
                                (sentence, f"leg_voice_minutes:{count}_routed_{routed}")
                            )
            continue
        if sentence.stop_idx in tap_only_stops:
            continue
        for unit in units:
            door = _DOOR_LINE_RE.search(unit)
            if door:
                if not goes_inside_by_stop.get(sentence.stop_idx, False):
                    out.append(
                        (sentence, f"door_line_without_door:{door.group(0).lower()}")
                    )
                continue  # a licensed door line is staging, not a moving line
            move = _MOTION_LEAD_RE.match(unit) or _MOTION_MID_RE.search(unit)
            if move:
                out.append(
                    (sentence, f"moving_line_auto_played:{move.group(0).strip().lower()}")
                )
    return out


def _proper_nouns_in(text: str, *, drop_first_word: bool = False) -> set[str]:
    """Return the set of capitalized multi-letter tokens.

    When ``drop_first_word=True``, the first word of every sentence is
    excluded so that mere sentence-start capitalization doesn't read as
    a proper noun.
    """
    if not text:
        return set()
    if not drop_first_word:
        return set(_CAP_TOKEN_RE.findall(text))

    out: set[str] = set()
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        tokens = _CAP_TOKEN_RE.findall(sentence)
        if not tokens:
            continue
        # Skip the first capitalized token (sentence-start position).
        # Any token after the first that is also capitalized is a real
        # candidate proper noun — even if the sentence is short.
        first_match = _CAP_TOKEN_RE.search(sentence)
        if first_match is None:
            continue
        first_span_end = first_match.end()
        rest = sentence[first_span_end:]
        out.update(_CAP_TOKEN_RE.findall(rest))
    return out


__all__ = ["placement_floor_hits", "validate_script"]
