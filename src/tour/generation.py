"""Tour-builder generation — §3.4 of phase-1-design.

Turns a ``BeatSequence`` into a ``Script``: cold-open, anchor essays,
circumnavigation, transit, closing. Sentence-level traceable output —
every sentence carries either a NarrativeBeat UUID or a whitelisted
glue label.

The whole module is deterministic apart from the optional Haiku call,
which is bounded to glue boundaries that template-defaults can't fill.
For tests, pass a ``MockGlueClient``; the LLM is never invoked and the
test asserts the deterministic structural shape.

Design notes:

- Stage 1 (cold open) prefers the first stop's ``stop_orientation``
  beat. When absent (most tier-5 anchors per §1.4), falls back to a
  ``SYNTHESIZED_OPENER`` template per §3.4 / Q7. This documents the
  data gap honestly rather than masking it.
- Stages 2/3 (anchor essay + circumnavigation) just stream the
  pre-ordered beats from ``BeatSequence``. Beat ordering already
  encodes the spatial primitive (sub_location / trigger_address).
- Stage 4 (transit) picks a corpus transit beat for the segment when
  one exists at either endpoint POI; otherwise emits a single
  ``GLUE_NAV`` glue sentence (Haiku or template).
- Stage 5 (closing, Phase 6 S6.4) emits ONE ``GLUE_CLOSING`` fallback line at
  the end of EVERY stop — the day's at the last stop — which the per-stop
  writer rewrites into the authored close.

Glue is the only place generation invents text. The whitelist
categories below are the universe of valid glue ``source_id`` values;
``validation.py`` enforces this gate.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections import Counter
from collections.abc import Iterable

from .claim_dedup import suppress_exact_repeats, suppress_repeated_claims
from .contract import (
    END_B_SENTINEL_PREFIX,
    GENERIC_OPEN_TOUR_CLOSING,
    BeatRef,
    BeatSequence,
    ClockExclusion,
    POIBeats,
    Route,
    Script,
    ScriptPOI,
    Sentence,
    TourInput,
    ValidationReport,
)
from .glue_client import NO_GLUE_SENTINEL, GlueClient, HaikuGlueClient
from .routing import leg_walk_seconds, planned_audio_seconds
from .visit_time import stop_seconds

# ---------------------------------------------------------------------------
# The audio clock
# ---------------------------------------------------------------------------

#: Glue labels spoken WHILE WALKING rather than standing at a stop. GLUE_NAV is a
#: leg's navigation line; GLUE_REFLECTION is placed by ``reflection.py`` on a long
#: WALKING leg by definition. Vignette one-liners are also walk-concurrent but are
#: beat-sourced, so they are identified by id — see ``is_walk_concurrent``.
#:
#: ONE definition, shared by every consumer, so they cannot drift: the quality
#: rubric's C7/C7b time model and the workbench preview's leg cards must agree on
#: what "spoken while walking" means, or the editor sees content in a card that the
#: rubric scored as stationary.
CONCURRENT_GLUE_LABELS: frozenset[str] = frozenset({"GLUE_NAV", "GLUE_REFLECTION"})


def is_walk_concurrent(
    sentence,
    vignette_beat_ids: frozenset[str] | set[str] = frozenset(),
    transit_beat_ids: frozenset[str] | set[str] = frozenset(),
) -> bool:
    """Is this sentence spoken WHILE THE TOURIST WALKS, rather than at a stop?

    Per the 2026-07-19 product ruling ("Audio overlaps the walking. It is a part of
    the tour experience."), walk-concurrent narration costs no elapsed time — it
    rides a leg the tourist is walking anyway. Everything else is spoken standing
    still at a stop and does consume the tour's minutes.

    ``vignette_beat_ids`` identifies walk-past one-liners, and
    ``transit_beat_ids`` the corpus transit beats ``_build_transit`` voices FOR
    the leg (derive them with ``transit_class_beat_ids``) — both beat-sourced
    rather than glue. Omitting either is the CONSERVATIVE direction: those
    sentences then count as stationary, which can only make a time-budget check
    stricter.
    """
    if sentence.source_id in CONCURRENT_GLUE_LABELS:
        return True
    return sentence.source_id in vignette_beat_ids or sentence.source_id in transit_beat_ids


def transit_class_beat_ids(beats: Iterable[BeatRef]) -> frozenset[str]:
    """Ids of the transit-class beats among ``beats`` — the walk's own corpus
    narration, keyed by TRANSIT_NARRATIVE_FUNCTIONS (one definition, shared with
    the pick and the anchor-block filter, so a beat is leg content everywhere or
    nowhere)."""
    return frozenset(
        beat.id
        for beat in beats
        if (beat.narrative_function or "").lower() in TRANSIT_NARRATIVE_FUNCTIONS
    )


#: Words per minute of narrated speech — the ONE rate the tour's audio clock uses.
#: NOT a new constant: ``routing.beat_spoken_seconds`` already falls back to
#: ``word_count / 150 * 60`` and density's ``word_count / 2.5`` is the same figure.
#: The live Paris corpus's populated ``est_spoken_seconds`` implies it tightly
#: (486 beats: p10 147, median 150, p90 153). The standard's cited external range
#: is 130-150 wpm (Nubart, Musa Guide) — 150 is the end this corpus was built at.
SPOKEN_WPM: int = 150

# ---------------------------------------------------------------------------
# Whitelisted glue labels — §3.5 of phase-1-design
# ---------------------------------------------------------------------------

GLUE_NAV: str = "GLUE_NAV"
GLUE_STAGING: str = "GLUE_STAGING"
# The one sensory-invitation phrase, shared by the cold-open and the staging step so the
# two emitters cannot drift apart and re-introduce the duplicate they once produced.
SENSORY_INVITATION: str = "Take a moment to take it in."
GLUE_PACING: str = "GLUE_PACING"
GLUE_CALLBACK: str = "GLUE_CALLBACK"
GLUE_CLOSING: str = "GLUE_CLOSING"
# Phase 4 (spec §6): a reflection synthesizes what has ALREADY been visited,
# spoken on a long leg. Placement is deterministic (reflection.py); the text is
# LLM-composed at COMPOSE time and gated fail-closed by VERIFY — the stitcher
# itself never writes one (a template cannot).
GLUE_REFLECTION: str = "GLUE_REFLECTION"
ARITH: str = "ARITH"
SYNTHESIZED_OPENER: str = "SYNTHESIZED_OPENER"

GLUE_LABELS: frozenset[str] = frozenset(
    {
        GLUE_NAV,
        GLUE_STAGING,
        GLUE_PACING,
        GLUE_CALLBACK,
        GLUE_CLOSING,
        GLUE_REFLECTION,
        ARITH,
        SYNTHESIZED_OPENER,
    }
)

# Phrases generation must never emit (rule 32 + feedback_tour_tone_default).
FORBIDDEN_PHRASES: tuple[str, ...] = ("imagine", "picture this", "envision", "visualize")

#: Phase 6 S6.5 (W6.2 R4, LOCKED 8/11): phrases that can ONLY be a promise of a later
#: place or telling — banned from story glue outright. The session may trade the next
#: stop away; a promise to a place the walker never reaches is a small shut door.
FORWARD_PROMISE_PHRASES: tuple[str, ...] = (
    "at the next stop",
    "at our next stop",
    "our next stop",
    "the next stop",
    "in a minute",
    "in a moment",
    "coming up",
    "we'll go inside",
    "we\u2019ll go inside",
    "we'll get to",
    "we\u2019ll get to",
    "we'll come back",
    "we\u2019ll come back",
    "more on that",
    "more on him",
    "more on her",
    "wait until",
    "save that for",
)

#: "You'll see/reach" is a promise only when it points PAST the stop — at the thing in
#: front of you it is direction-giving, and a validation failure on a correct tour is
#: worse than no check. The scan pairs these with a LATER stop's name.
FORWARD_SIGHT_PHRASES: tuple[str, ...] = (
    "you'll see",
    "you\u2019ll see",
    "you'll reach",
    "you\u2019ll reach",
    "as we head to",
    "on our way to",
)

#: Phase 8 S8.3 (W8.2 R1, 11/11): the LEG'S VOICE knows its geometry. A nav line
#: names direction ONLY as left/right/straight-ahead or a visible landmark \u2014
#: NEVER compass (compounds included; a chained "south-south-east" is caught by
#: its first component) \u2014 and speaks minutes ONLY as the routed leg's own
#: number. ONE vocabulary, shared by the coercion guard here and the validation
#: floors (validation.py imports these), so the two cannot drift.
_COMPASS_RE = re.compile(
    r"\b(?:north|south)(?:-?(?:east|west))?\b|\b(?:east|west)\b", re.IGNORECASE
)

#: The narrative functions whose beats describe a WALK rather than a place. Their
#: text is the one corpus claim that can be wrong about this route while being
#: perfectly true of the guidebook's own: a place's facts hold whoever walks up to
#: it, a direction does not. ONE definition, read by the pick and by the scan.
TRANSIT_NARRATIVE_FUNCTIONS: frozenset[str] = frozenset(
    {"transition", "transit", "navigation"}
)

#: A body whose OPENING clause is one of these describes arriving FROM somewhere —
#: the one shape that lets a previous stop's name vouch for a transit beat's
#: direction. A name that merely appears later in the body is where the beat is
#: GOING (a destination street), and matching it picked the guidebook's own
#: approach for a walker arriving the other way.
_ORIGIN_CLAUSE_RE = re.compile(
    r"^(?:from|leave|leaving|starting at|exit(?:ing)?)\b", re.IGNORECASE
)

#: A guidebook's own itinerary — true of its route, never of this one. The same
#: class as compass, refused at the same gate.
_ITINERARY_RE = re.compile(
    r"\bwhere (?:the|our|this) (?:walk|tour) (?:ends|begins|starts)\b"
    r"|\bour next stop\b|\bthe (?:walk|tour) continues\b",
    re.IGNORECASE,
)
_MINUTE_WORDS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}
_SPOKEN_MINUTES_RE = re.compile(
    r"\b(\d{1,3}|" + "|".join(_MINUTE_WORDS) + r")[- ]minutes?\b", re.IGNORECASE
)


def spoken_minute_counts(text: str) -> set[int]:
    """Every minute count a line speaks, as integers (digits or number words)."""
    out: set[int] = set()
    for match in _SPOKEN_MINUTES_RE.finditer(text):
        raw = match.group(1).lower()
        out.add(int(raw) if raw.isdigit() else _MINUTE_WORDS[raw])
    return out


# ---------------------------------------------------------------------------
# Sentence splitting
# ---------------------------------------------------------------------------

# Corpus place assessments bind the exact splitter semantics that produced
# their sentence indexes/hashes.  Any behavior-changing splitter edit must
# increment this value and migrate/review the manifest; old evidence may not be
# silently reinterpreted.
SENTENCE_SPLITTER_VERSION: str = "ondoway-sentence-splitter-v1"

# Split on .?! followed by whitespace, but never inside common abbreviations.
# Best-effort — corpus is well-edited prose. Validation catches invention.
_ABBREVIATIONS: frozenset[str] = frozenset(
    {"mr", "mrs", "ms", "dr", "st", "no", "vs", "etc", "e.g", "i.e", "mme", "mlle"}
)
_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Þ«„(\"'])")
# Personal-name initials — a single capital letter + period, optionally chained
# with '-' or more initials: "J.", "J.-B.", "J.B.". These end in a period but are
# NEVER a sentence boundary, so the splitter must re-glue them (else TTS reads
# "…a portrait by J.-B." as its own stuttered sentence — the User-agent finding).
_INITIALS_RE = re.compile(r"^[A-ZÀ-ÖØ-Þ]\.(-?[A-ZÀ-ÖØ-Þ]\.)*$")
# ...but these dotted-caps tokens have the SAME shape as name-initials yet DO
# legitimately end a sentence ("the U.S.", "1914 A.D.", "by 5 P.M."). They must
# NOT be re-glued, or two real sentences fuse (the mirror of the stutter bug).
# Keyed by lowercased, trailing-period-stripped form.
_TERMINAL_ABBREVS: frozenset[str] = frozenset(
    {"u.s", "u.k", "u.s.a", "u.n", "e.u", "a.d", "b.c", "a.m", "p.m", "d.c"}
)


def split_sentences(text: str) -> list[str]:
    """Split a beat's script_body into audio-sentence units.

    Emits sentence strings (no trailing whitespace); preserves inline
    French phrases and quoted material verbatim. Empty/whitespace-only
    input yields ``[]``.
    """
    if not text or not text.strip():
        return []
    raw = _SPLIT_RE.split(text.strip())
    # Re-glue accidentally-split abbreviation pieces ("Mme.", "no. 6").
    out: list[str] = []
    for piece in raw:
        piece = piece.strip()
        if not piece:
            continue
        if (
            out
            and _last_word_is_abbrev(out[-1])
            and not _is_regnal_terminal(out[-1], piece)
        ):
            out[-1] = f"{out[-1]} {piece}"
        else:
            out.append(piece)
    return out


# Single-letter Roman numerals — the ones that share the shape of a name-initial.
_ROMAN_SINGLE: frozenset[str] = frozenset("IVXLCDM")
# Closed-class words that OPEN a new sentence. The one reliable signal that a
# trailing single-letter Roman numeral is a REGNAL terminal ("Elizabeth I. The
# abbey…") and not a middle INITIAL of a name ("John D. Rockefeller"): a real new
# sentence begins with a function-word opener, whereas a surname is an open-class
# proper noun. If the next token is NOT in this set we GLUE (assume a name), which
# keeps every "<First> <RomanLetter>. <Surname>" intact at the cost of leaving the
# rarer "…World War I. Waterloo…" (regnal before a proper-noun-initial sentence)
# glued — a minor missing pause, never a mid-name break.
_SENTENCE_OPENERS: frozenset[str] = frozenset({
    "the", "a", "an", "it", "he", "she", "they", "we", "you", "i", "this", "that",
    "these", "those", "his", "her", "its", "their", "our", "in", "on", "at", "by",
    "for", "from", "with", "after", "before", "during", "since", "when", "while",
    "as", "although", "though", "but", "and", "however", "later", "today", "now",
    "here", "there", "then", "once", "under", "over", "between", "among",
    "following", "despite", "because", "if", "to", "of",
})


def _is_regnal_terminal(prev: str, nxt: str) -> bool:
    """True when ``prev`` ends with a REGNAL numeral that legitimately ENDS the
    sentence — "Elizabeth I. The abbey…", "Louis X. The scandal…" — so the
    abbreviation re-glue must NOT fuse it with ``nxt`` (the mirror of the initial-
    stutter bug: without this, "…by Elizabeth I. The abbey…" reads as one run-on).

    The hard case is telling a regnal numeral from an ordinary single-letter MIDDLE
    INITIAL: C/D/I/L/M/V/X are both ("John D. Rockefeller"). The distinguisher is
    the NEXT token — a regnal ends a sentence, so what follows is a sentence-opener
    function word (The/It/During/…); a middle initial is followed by a SURNAME
    (an open-class proper noun). So we split ONLY when the next token is a known
    opener; otherwise we glue, which keeps "<First> <RomanLetter>. <Surname>" whole.
    Also glue when a neighbour is itself an initial (an initial CHAIN: "J. M. W.
    Turner", "by C. W. Stephens"). Multi-letter regnals ("Henry VIII.") never match
    ``_INITIALS_RE``, so this only governs the single-letter case."""
    words = prev.split()
    if len(words) < 2:
        return False  # a bare "I." with no preceding word is treated as an initial
    if words[-1].rstrip("\"'»).,") not in _ROMAN_SINGLE:  # last token a lone Roman letter
        return False
    if _INITIALS_RE.match(words[-2].rstrip("\"'»)")):
        return False  # preceded by an initial -> it's a chain (J. M. W.), glue
    nxt_first = nxt.split()[0] if nxt.split() else ""
    if _INITIALS_RE.match(nxt_first.rstrip("\"'»)")):
        return False  # next opens on an initial -> chain (C. -> W. Stephens), glue
    # regnal iff the next sentence opens on a function word, not a surname.
    return nxt_first.rstrip("\"'»).,").lower() in _SENTENCE_OPENERS


def _last_word_is_abbrev(s: str) -> bool:
    words = s.split()
    if not words:
        return False
    last = words[-1].rstrip("\"'»)")  # keep the periods; drop a trailing quote/paren
    norm = last.lower().rstrip(".")
    # A personal-name initial (J., J.-B.) is never a sentence end — but a dotted-
    # caps abbreviation that CAN end a sentence (U.S., A.D., P.M.) is excluded.
    if _INITIALS_RE.match(last) and norm not in _TERMINAL_ABBREVS:
        return True
    return norm in _ABBREVIATIONS


# A walk-past vignette is ONE line heard in passing. A raw Wikipedia lead sentence
# ("Nelson's Column is a monument in Trafalgar Square in the City of Westminster,
# Central London, England, United Kingdom, built to commemorate …" — 50 words) recited
# as a walk-past is the "reading Wikipedia aloud" failure a live A/B surfaced. Cap it.
VIGNETTE_ONE_LINER_MAX_WORDS: int = 24


def _first_clause(sentence: str) -> str:
    """The first top-level clause of ``sentence`` — up to the first comma or semicolon
    that is NOT inside parentheses ("169 feet (51.59 m), tall" keeps "(51.59 m)"
    intact). No such boundary -> the whole sentence."""
    depth = 0
    for i, ch in enumerate(sentence):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif ch in ",;" and depth == 0:
            return sentence[:i].rstrip()
    return sentence.strip()


def vignette_one_liner_text(body: str | None, poi_name: str) -> str:
    """The spoken walk-past one-liner for a vignette beat, shared by the audio/script
    voicing (``_vignette_one_liners``) and the workbench preview so the two never
    diverge. It is the beat's FIRST sentence, clause-capped when that sentence is a
    long run-on: if the first sentence exceeds ``VIGNETTE_ONE_LINER_MAX_WORDS`` words,
    serve only its first top-level clause — but ONLY when that clause still names the
    POI (``poi_name`` in it) and is a substantial clause (≥5 words); otherwise serve
    the full first sentence unchanged. So a bad extraction is tightened, but the line
    is NEVER a fragment and NEVER drops the POI's own name (the mis-attribution the
    self-naming preference exists to prevent). Returns "" for a bodyless beat."""
    sents = split_sentences(body or "")
    if not sents:
        return ""
    first = sents[0]
    if len(first.split()) <= VIGNETTE_ONE_LINER_MAX_WORDS:
        return first
    clause = _first_clause(first)
    if (
        poi_name
        and poi_name.casefold() in clause.casefold()
        and len(clause.split()) >= 5
        and clause != first
    ):
        return clause if clause.endswith((".", "!", "?")) else clause + "."
    return first


# ---------------------------------------------------------------------------
# Top-level entrypoint
# ---------------------------------------------------------------------------


def generate(
    beat_sequence: BeatSequence,
    route: Route,
    tour_input: TourInput,
    *,
    glue_client: GlueClient | None = None,
    now: _dt.datetime | None = None,
    validate_output: bool = True,
) -> Script:
    """Build the Script from a planned BeatSequence + Route.

    Validation runs at the end and is attached to the returned Script.
    No exception is raised on validation failure — the caller (skill
    orchestrator) decides whether to block on a non-empty report.
    """
    from .beat_select import reorder_final_stop_for_closing  # avoid cycles
    from .validation import validate_script  # avoid import cycle

    # REAL BY DEFAULT (owner ruling 2026-07-31). This line used to read
    # `glue_client or MockGlueClient()`, which made the CANNED client the default on
    # every live path — trips.py generate_trip, trips.py compose_trip and
    # premium_tour.plan_premium_tour all call generate() without a client, so every
    # transition sentence in every tour the owner has ever read was a fixed string
    # ("Stand here.", "Take a moment.", "End the walk here."). HaikuGlueClient, the real
    # implementation, had ZERO call sites in product code. Tests were green throughout,
    # because the tests were the code using the mock.
    #
    # The fake is no longer the default. A caller that wants a double must pass one
    # explicitly; production passes nothing and gets the real thing.
    client = glue_client if glue_client is not None else HaikuGlueClient()
    sentences: list[Sentence] = []

    # Phase 7 Fix 5: ensure the final stop's last beat is closing-friendly
    # (callback > climax > longest body). Closing glue lands after this.
    beat_sequence = reorder_final_stop_for_closing(beat_sequence)
    poi_beats = beat_sequence.poi_beats
    # consumed_beat_ids tracks every beat already emitted across the
    # whole script, so transit + anchor stages never emit the same
    # beat twice. Without this, a transit beat picked from `current`'s
    # pool (or a transit beat shared by adjacent POIs) re-appeared in
    # `_build_anchor_block` after the transit stage.
    consumed_beat_ids: set[str] = set()
    # A clock-closed stop says so, once, FIRST — before any of its story.
    closure_lines = _closure_opening_lines(route)
    if poi_beats:
        cold_open_sents, consumed_in_cold_open = _build_cold_open(
            beat_sequence, route, client, tour_input=tour_input, stop_idx=0
        )
        # A closed start not in the day (at_start, off-route) is named in the
        # same slot, FIRST — the walker is standing at that door; a closed
        # stop 0 is "just ahead" and its own line follows. Two shut doors are
        # two facts, each said once.
        openings = [
            s for s in (_closed_start_line(route), closure_lines.get(0)) if s is not None
        ]
        if openings:
            # After the "Settle in." breath, before anything about the place.
            cold_open_sents = (
                [cold_open_sents[0], *openings, *cold_open_sents[1:]]
                if cold_open_sents
                else openings
            )
        sentences.extend(cold_open_sents)
        consumed_beat_ids |= consumed_in_cold_open

    for stop_idx, current in enumerate(poi_beats):
        if stop_idx > 0:
            previous = poi_beats[stop_idx - 1]
            transit_sents = _build_transit(
                previous,
                current,
                route,
                client,
                stop_idx=stop_idx,
                consumed_beat_ids=consumed_beat_ids,
                vignette_beats=beat_sequence.vignette_beats.get(stop_idx, ()),
            )
            sentences.extend(transit_sents)
            for s in transit_sents:
                if s.source_type == "beat":
                    consumed_beat_ids.add(s.source_id)
            opening = closure_lines.get(stop_idx)
            if opening is not None:
                # The stop's first STATIONARY sentence (transit plays walking).
                sentences.append(opening)
        anchor_sents = _build_anchor_block(current, stop_idx, skip_beat_ids=consumed_beat_ids)
        sentences.extend(anchor_sents)
        for s in anchor_sents:
            if s.source_type == "beat":
                consumed_beat_ids.add(s.source_id)
        # EVERY stop ends on a close (Phase 6 S6.4; design §5.3 "every named stretch
        # carries a written one-line close that can play wherever the stretch actually
        # ends"; §7.4.5 every prefix is decent). The stitch's line is the FALLBACK — the
        # Basic lane plays it, and it is the GLUE_CLOSING source the one writer is
        # licensed to rewrite into the authored close (the grounding rule: glue keeps a
        # source_id supplied in THIS stop's stitched script). The last stop's close is
        # the DAY's (W6.2 R2: one close at the end, never the stop's and the day's in
        # a row).
        sentences.append(
            _build_stop_close(
                beat_sequence, tour_input, route, stop_idx=stop_idx, n_stops=len(poi_beats)
            )
        )

    # #22: drop beat sentences that restate a claim already voiced by an earlier
    # beat (cross-book / cross-POI repetition). Runs after the full stitch so it
    # sees the whole route; never empties a beat, so emitted beat-ids are stable.
    sentences = suppress_repeated_claims(sentences, beat_sequence)
    # Then drop byte-identical restatements the claim pass leaves behind (two beats
    # at one stop sharing an exact sentence): pure repetition, zero content loss.
    sentences = suppress_exact_repeats(sentences, beat_sequence)
    # THE SHUT-DOOR FILTER: a stop the plan prices outside-only never voices a
    # through-the-door line. The premium lane's placement floor refuses such a
    # line and its writer rewrites the stop; the stitched lane ships corpus
    # text with no writer, so the invitation is dropped here — at the one place
    # the stitch still holds the Route — and the story around it is kept.
    sentences = _drop_door_lines_at_doorless_stops(
        sentences,
        route,
        vignette_beat_ids=frozenset(
            beat.id for beats in beat_sequence.vignette_beats.values() for beat in beats
        ),
        transit_beat_ids=transit_class_beat_ids(
            beat for plan in beat_sequence.poi_beats for beat in plan.beats
        ),
    )
    # NOTE: same-beat NEAR-verbatim de-dup (suppress_same_beat_near_duplicates) is
    # deliberately NOT run here. It is token-similarity only (not claim-aware), and
    # rapidfuzz.token_set_ratio scores a SUPERSET sentence >= 90 against its subset —
    # so at the stitch root, BEFORE the coverage baseline is computed, it could
    # silently drop a sentence that adds a distinct fact, with nothing downstream to
    # catch it. That pass runs only where it is coverage-checked: inside _assemble
    # and on each repair candidate via _prefer_deduped (both AFTER expected_claim_ids
    # is fixed, so a drop that loses a fact fails verify and is undone).

    # A beat is "voiced" only if at least one of its sentences actually survived
    # the full stitch (cold-open/anchor/transit) AND #22 claim-dedup. A
    # transit-class beat the direction check rejected, or a beat whose every
    # sentence was deduped, emits nothing — it must NOT be reported as voiced
    # (dwell over-report) nor block keep-exploring (its ScriptPOI.beat_ids feeds
    # extra_beat_ids's exclusion set). Compute the truth from emitted sentences.
    voiced_beat_ids = {s.source_id for s in sentences if s.source_type == "beat"}
    selected_pois = _flatten_pois(beat_sequence, route, voiced_beat_ids)
    lens_coverage = _lens_coverage(beat_sequence)
    total_audio = _sum_audio(sentences, beat_sequence)
    walking = int(route.total_walk_seconds)
    distance = int(route.total_walk_distance_m)
    planned = int(route.err_short_total_seconds)

    script = Script(
        city_slug=tour_input.city_slug,
        generated_at=(now or _dt.datetime.now(_dt.UTC)).isoformat(),
        inputs=tour_input,
        total_audio_seconds=total_audio,
        total_walking_seconds=walking,
        total_walk_distance_m=distance,
        total_planned_seconds=planned,
        selected_pois=selected_pois,
        lens_coverage=lens_coverage,
        script=tuple(sentences),
        validation=ValidationReport(),  # placeholder — replaced below
    )
    report = (
        validate_script(
            script,
            beat_sequence,
            spine_area=route.spine_area,
            disclosed_place_names=tuple(e.name for e in route.clock_exclusions),
        )
        if validate_output
        else ValidationReport()
    )
    return script.model_copy(update={"validation": report})


# ---------------------------------------------------------------------------
# Stage 1 — cold open
# ---------------------------------------------------------------------------


def _build_cold_open(
    beat_sequence: BeatSequence,
    route: Route,
    client: GlueClient,
    *,
    tour_input: TourInput,
    stop_idx: int,
) -> tuple[list[Sentence], set[str]]:
    """Cold open: prefer a stop_orientation beat; otherwise synthesize.

    Phase 7 (2026-04-29) added Area-level fallback: when the start POI
    has no stop_orientation beat, search the rest of the Route for a
    sibling POI that shares an Area with the start POI and carries one.
    The found beat is hoisted to the cold-open and added to
    ``consumed_beat_ids`` so it doesn't double-fire at its original
    stop. Falls through to SYNTHESIZED_OPENER only when no Area-mate
    carries an orientation beat either.

    Returns ``(sentences, consumed_beat_ids)``. The anchor-block stage
    skips any beat in ``consumed_beat_ids`` so cold-open content isn't
    emitted twice.
    """
    from .beat_select import find_area_orientation_beat  # avoid cycles

    poi_beats = beat_sequence.poi_beats
    first_stop = poi_beats[stop_idx]
    orientation = _find_orientation_beat(first_stop)
    if orientation is None:
        orientation = find_area_orientation_beat(beat_sequence, route, start_idx=stop_idx)
    sentences: list[Sentence] = []
    consumed: set[str] = set()

    # Pacing cue first: settle in.
    sentences.append(
        Sentence(
            text="Settle in.",
            source_id=GLUE_PACING,
            source_type="glue",
            stop_idx=stop_idx,
        )
    )

    if orientation is not None and orientation.script_body:
        sentences.extend(_beat_to_sentences(orientation, stop_idx))
        consumed.add(orientation.id)
    else:
        # SYNTHESIZED_OPENER fallback per Q7. Phase 7.5 (Fix 2)
        # composes the opener from real corpus data at the start POI:
        # Area name + pronunciation + a single physical_cue + an
        # optional sensory invitation if any beat has feature_type='view'.
        # Every phrase traces to a glue-whitelist token or an extracted
        # corpus field — no Haiku invention. Marked SYNTHESIZED_OPENER
        # so audits can prioritise stop_orientation back-fill.
        sentences.extend(_build_synthesized_opener(first_stop, route, tour_input, stop_idx))
        first_beat = next((b for b in first_stop.beats if b.script_body), None)
        if first_beat is not None:
            sentences.extend(_beat_to_sentences(first_beat, stop_idx))
            consumed.add(first_beat.id)

    return sentences, consumed


# Feature types whose cues are safe to read aloud as physical staging.
# 'view' and 'architectural_detail' are the strongest direct anchors;
# 'plaque' and 'adjacent_landmark' fall back when the stronger types
# are absent. 'interior' is excluded — it usually requires the listener
# to already be inside, which the cold-open can't guarantee.
_SYNTH_PRIMARY_FEATURE_TYPES: tuple[str, ...] = ("view", "architectural_detail")
_SYNTH_FALLBACK_FEATURE_TYPES: tuple[str, ...] = ("plaque", "adjacent_landmark")
_SYNTH_VIEW_FEATURE_TYPE: str = "view"


def _build_synthesized_opener(
    first_stop: POIBeats,
    route: Route,
    tour_input: TourInput,
    stop_idx: int,
) -> list[Sentence]:
    """Compose the Phase 7.5 SYNTHESIZED_OPENER block.

    Order:
      1. Area-anchored location line ("You're starting in the Marais.")
         from `route.spine_area`. Falls back to the POI name when
         spine_area is missing.
      2. Pronunciation, if any beat at the start POI carries one
         ("That's pronounced X.").
      3. A single physical_cue staging line. Picks the strongest cue
         (view / architectural_detail; plaque / adjacent_landmark
         fallback). "Look up at X." for view; "Notice X." otherwise.
      4. Sensory invitation if any beat has feature_type='view':
         "Take a moment to take it in."; otherwise the duration
         primer "We're going to walk for about N minutes."

    Location and walking assertions are marked with SYNTHESIZED_OPENER.
    Physical directions trace to GLUE_STAGING; the sensory invitation traces
    to GLUE_PACING because it makes no claim about the listener's surroundings.
    """
    out: list[Sentence] = []
    poi_beats = list(first_stop.beats)

    # 1. Location anchor.
    location_text = _synth_location_anchor(first_stop, route)
    out.append(
        Sentence(
            text=location_text,
            source_id=SYNTHESIZED_OPENER,
            source_type="glue",
            stop_idx=stop_idx,
        )
    )

    # 1b. Ground the walk ahead: name the first stop and how far it is. Honest —
    # the walker is still EN ROUTE (never "you're standing at {stop}"). Uses the
    # routed first-leg time, so it also sets pace/anticipation. The line arrives
    # already placed (S8.3c): head-for rides the first leg as GLUE_NAV; standing
    # variants stay stationary opener pieces.
    walk_line = _synth_first_leg_text(first_stop, route, stop_idx)
    if walk_line is not None:
        out.append(walk_line)

    # 2. Pronunciation if present.
    pronunciation_text = _synth_pronunciation(poi_beats, first_stop.poi_name)
    if pronunciation_text:
        out.append(
            Sentence(
                text=pronunciation_text,
                source_id=SYNTHESIZED_OPENER,
                source_type="glue",
                stop_idx=stop_idx,
            )
        )

    # 3. Physical staging from the strongest available cue — UNLESS the clock
    # has shut this stop's door: a cue can point at something behind it ("Look
    # up at crypt…" at the closed Panthéon directed Aiko underground), and the
    # closure line has already staged the stop ("we'll take it in from out
    # here"). The stop keeps its story; only the pointing finger is lowered.
    clock_closed = any(
        e.poi_id == first_stop.poi_id and e.kept_outside
        for e in route.clock_exclusions
    )
    chosen_cue = None if clock_closed else _synth_pick_cue(poi_beats)
    has_view_cue = any(
        (cue.feature_type or "").lower() == _SYNTH_VIEW_FEATURE_TYPE
        for beat in poi_beats
        for cue in beat.physical_cues
    )
    if chosen_cue is not None:
        feature = (chosen_cue.feature_type or "").lower()
        cue_phrase = chosen_cue.cue.strip()
        if cue_phrase:
            low = cue_phrase.lower()
            if low.startswith(("look ", "look up", "notice ")):
                # The cue already carries its own staging directive — don't double it
                # ("Look up at Look up at the dome").
                text = cue_phrase
            else:
                # Lowercase a leading "The " so it reads "Look up at the square", not
                # "...at The square" (a raw title-case field).
                if cue_phrase.startswith("The "):
                    cue_phrase = "the " + cue_phrase[4:]
                staging_verb = "Look up at" if feature == _SYNTH_VIEW_FEATURE_TYPE else "Notice"
                text = f"{staging_verb} {cue_phrase}"
            if not text.endswith((".", "!", "?")):
                text += "."
            out.append(
                Sentence(text=text, source_id=GLUE_STAGING, source_type="glue", stop_idx=stop_idx)
            )

    # 4. Sensory invitation when there's a view to take in. (The old generic
    # "we're going to walk for N minutes" primer is dropped — step 1b now grounds
    # the walk concretely with the first stop and its distance.)
    #
    # Suppressed when the cold-open already said it (2026-07-19). ``_synth_first_leg``
    # ends with this exact invitation on the you-are-already-here branch (leg < 20s), so
    # a first stop that ALSO has a view cue said it twice, four sentences apart. Seen in
    # the workbench on a real Île de la Cité preview: "You're starting right at Pont Neuf.
    # Take a moment to take it in. Look up at the Seine quais... Take a moment to take it
    # in." Both emitters now share SENSORY_INVITATION so this cannot silently re-diverge.
    already_invited = any(SENSORY_INVITATION in (s.text or "") for s in out)
    if has_view_cue and not already_invited:
        out.append(
            Sentence(
                text=SENSORY_INVITATION,
                source_id=GLUE_PACING,
                source_type="glue",
                stop_idx=stop_idx,
            )
        )

    return out


def _minute_article(minutes: int) -> str:
    """"an" before minute counts that read with a leading vowel (8, 11, 18, 80s)."""
    return "an" if minutes in (8, 11, 18) or 80 <= minutes <= 89 else "a"


def _synth_first_leg_text(
    first_stop: POIBeats, route: Route, stop_idx: int = 0
) -> Sentence | None:
    """The first-leg line, fully placed: name the first stop and how far the
    opening walk is (honest — en route).

    Uses the routed first-leg seconds when available (``route.transits[0]``);
    a very short leg reads "just ahead", a longer one "about an N-minute walk".
    Returns None when there is no stop name.

    Phase 8 S8.3c (W8.2 R2; Paulo's Finding 14): the HEAD-FOR line is
    navigation, so it returns labelled ``GLUE_NAV`` — the frozen playback rule
    (``is_walk_concurrent``, untouched) then books it onto the first LEG, and a
    queued first stop no longer auto-plays a walking instruction to someone
    standing still. The two branches with nothing to walk — already standing at
    the stop, or a route with no transit to ride — stay a stationary
    ``SYNTHESIZED_OPENER`` piece, so the playback partition never points at an
    absent leg. The line's minutes are the routed leg's own number, the same
    figure the validation floor checks nav lines against.
    """
    name = (first_stop.poi_name or "").strip()
    if not name:
        return None

    def _line(text: str, source_id: str) -> Sentence:
        return Sentence(text=text, source_id=source_id, source_type="glue", stop_idx=stop_idx)

    # Lowercase a leading "The " so the name reads naturally mid-sentence
    # ("head for the Sorbonne", not "...for The Sorbonne" — a raw title-case field).
    if name.startswith("The "):
        name = "the " + name[4:]
    if not route.transits:
        return _line(
            f"When you're ready, head for {name} — it's just ahead.", SYNTHESIZED_OPENER
        )
    leg = route.transits[0]
    secs = leg.leg_seconds or leg.walk_seconds or 0
    if secs < 20:
        # You're already standing at the first stop — don't tell the walker to
        # "head for" a place they're on top of (the Tuileries/Concorde complaint).
        return _line(f"You're starting right at {name}. {SENSORY_INVITATION}", SYNTHESIZED_OPENER)
    minutes = round(secs / 60)
    if minutes <= 1:
        return _line(f"When you're ready, head for {name} — it's just ahead.", GLUE_NAV)
    article = _minute_article(minutes)
    return _line(
        f"When you're ready, head for {name} — about {article} {minutes}-minute walk from here.",
        GLUE_NAV,
    )


def _synth_location_anchor(first_stop: POIBeats, route: Route) -> str:
    """Pick the Area-name line for the synthesized opener.

    Prefers `route.spine_area`. Falls back to the POI name when no
    spine has been computed (test fixtures without an Area).
    """
    spine = (route.spine_area or "").strip()
    if spine:
        article = _area_article(spine)
        return f"You're starting in {article}{spine}."
    return f"You're standing at {first_stop.poi_name}."


def _area_article(spine: str) -> str:
    """A small, deterministic article-prefix table for Paris Areas.

    "the Marais" / "the Île de la Cité" feel native; arrondissement
    numbers ("the 4th Arrondissement") want "the" too. Bare proper
    nouns ("Montmartre", "Saint-Germain-des-Prés") take no article.
    """
    s = spine.lower()
    if s.startswith("île ") or s.startswith("ile "):
        return "the "
    if "arrondissement" in s:
        return "the "
    if s.startswith("le ") or s.startswith("la ") or s.startswith("les "):
        return ""  # already carries the article
    # English descriptive areas read natively with "the" ("the Latin Quarter"),
    # unlike French place-names ("Montmartre", "Saint-Germain-des-Prés").
    if s in _AREA_TAKES_THE or s.endswith(" quarter") or s.endswith(" bank"):
        return "the "
    return ""


# Areas that read natively with a leading "the".
_AREA_TAKES_THE: frozenset[str] = frozenset(
    {"marais", "latin quarter", "left bank", "right bank", "grands boulevards", "city"}
)


def _synth_pronunciation(beats: list[BeatRef], poi_name: str) -> str | None:
    """Return a pronunciation glue line if any beat carries the field.

    Picks the longest non-empty pronunciation (richer phonetic detail
    is more useful aloud) and prefixes it with the POI-name target.
    """
    pronunciations = [
        (b.pronunciation or "").strip() for b in beats if (b.pronunciation or "").strip()
    ]
    if not pronunciations:
        return None
    chosen = max(pronunciations, key=len)
    # If the pronunciation already names the target, just emit it.
    if chosen.lower().startswith("that's pronounced"):
        if not chosen.endswith("."):
            chosen = chosen + "."
        return chosen
    return f"That's pronounced {chosen}."


def _synth_pick_cue(beats: list[BeatRef]):
    """Return the strongest physical_cue from a stop's beats, or None.

    Preference: view ≻ architectural_detail ≻ plaque ≻ adjacent_landmark.
    Within a category, the first cue encountered wins (stable order
    follows beat order, which is already deterministic).
    """
    for tier in (_SYNTH_PRIMARY_FEATURE_TYPES, _SYNTH_FALLBACK_FEATURE_TYPES):
        for ftype in tier:
            for beat in beats:
                for cue in beat.physical_cues:
                    if (cue.feature_type or "").lower() == ftype and cue.cue.strip():
                        return cue
    return None


def _find_orientation_beat(stop: POIBeats) -> BeatRef | None:
    """Return the first beat keyed as ``beat_type='stop_orientation'``.

    Phase 1 §1.4 inventoried 13 such beats in the live corpus and
    Phase 3.5 confirmed them: the field is ``beat_type`` (not
    ``narrative_function``). The lookup checks both for resilience —
    older corpus dumps used ``narrative_function`` for the same role.
    """
    for beat in stop.beats:
        if (beat.beat_type or "").lower() == "stop_orientation":
            return beat
        if (beat.narrative_function or "").lower() == "stop_orientation":
            return beat
    return None


# ---------------------------------------------------------------------------
# Stages 2-3 — anchor essay + circumnavigation
# ---------------------------------------------------------------------------


_TRANSIT_NARRATIVE_FUNCTIONS: frozenset[str] = frozenset({"transition", "transit", "navigation"})


def _build_anchor_block(
    stop: POIBeats,
    stop_idx: int,
    *,
    skip_beat_ids: set[str] | None = None,
) -> list[Sentence]:
    """Stream the pre-ordered beats; skip cold-open and transit-class beats.

    Phase 7: transit-class beats are routed only through ``_build_transit``
    after a direction check. When the check rejects a beat, it must NOT
    leak back into the anchor block (where the listener would hear an
    out-of-place navigation sentence). Always filter transit-class beats
    at the anchor stage; the transit stage is the only legitimate place
    for them.
    """
    skip = skip_beat_ids or set()
    out: list[Sentence] = []
    for beat in stop.beats:
        if beat.id in skip:
            continue
        if (beat.narrative_function or "").lower() in _TRANSIT_NARRATIVE_FUNCTIONS:
            continue
        out.extend(_beat_to_sentences(beat, stop_idx))
    return out


# ---------------------------------------------------------------------------
# Stage 4 — transit
# ---------------------------------------------------------------------------


# The fixed-end sentinel id prefix (selection._materialize_fixed_end_b): a pinned
# endpoint with no POI of its own is materialized as "__end_b__<lat>_<lng>". ONE
# definition, in the contract (W4.12); this name is kept for its importers.
_END_B_SENTINEL_PREFIX: str = END_B_SENTINEL_PREFIX

# Varied deterministic connective-tissue templates, cycled by leg so the tour
# never reads the identical nav line twice in a row (the "Walk to the next stop."
# ten-times complaint). No em-dashes (TTS-clean).
_NAV_TEMPLATES: tuple[str, ...] = (
    "From {prev}, make your way on to {cur}{dist}.",
    "Leaving {prev} behind, head for {cur}{dist}.",
    "From {prev}, carry on to {cur}{dist}.",
)


def _nav_walk_minutes(leg_seconds: int) -> int:
    """Whole-minute walking estimate from the engine's own leg-seconds budget.

    Not a separate display-pace constant — the same leg-seconds the engine
    budgets everywhere else (routed Valhalla seconds when present, else the
    pace-corrected haversine fallback; see ``_segment_leg_seconds``). The
    underlying tourist pace itself stays a tracked open question (the planner
    diagnostic that tracks it lives in git history — its spec tree is retired).
    """
    return round(leg_seconds / 60) if leg_seconds and leg_seconds > 0 else 0


def _template_nav(
    previous_name: str,
    current_name: str,
    leg_seconds: int,
    stop_idx: int,
    *,
    is_final_destination: bool = False,
) -> str:
    """Deterministic connective-tissue nav sentence for a leg (no LLM).

    Names the destination and gives a sense of the walk, varied by leg — the
    connective tissue between points, replacing the old flat "Walk to the next
    stop." For a pinned beatless endpoint, a graceful arrival (never routes the
    walker to a placeholder literally named "Destination"). ``leg_seconds`` is
    the same figure the engine budgets this leg at elsewhere (options.py's
    eta_seconds, reflection.py, audit.py) so the announced walk time and the
    displayed tour total never disagree.
    """
    if is_final_destination:
        return "From here, make your way to your final destination."
    minutes = _nav_walk_minutes(leg_seconds)
    if minutes >= 2:
        dist = f", about {_minute_article(minutes)} {minutes}-minute walk away"
    else:
        dist = ", just ahead"
    template = _NAV_TEMPLATES[stop_idx % len(_NAV_TEMPLATES)]
    return template.format(prev=previous_name, cur=current_name, dist=dist)


def _build_transit(
    previous: POIBeats,
    current: POIBeats,
    route: Route,
    client: GlueClient,
    *,
    stop_idx: int,
    consumed_beat_ids: set[str] | None = None,
    vignette_beats: tuple[BeatRef, ...] = (),
) -> list[Sentence]:
    """Insert a corpus transit beat when present; otherwise a single glue nav.

    Phase 7 (2026-04-29) adds direction-awareness. Pre-existing transit
    beats encode an origin → destination direction inferred from their
    ``trigger_address`` + opening prose. Reusing them on routes that run
    the opposite direction (or start at unrelated POIs) produced
    geographically-broken openings in phase-6-rerun:

    - Tour 4 stop 3 opened "Starting at Invalides Metro station…" when
      the user was arriving from Pont de la Concorde.
    - Tour 3 stop 3 opened "Leave the Sacré-Cœur…" before the user had
      visited Sacré-Cœur.

    A corpus transit beat is accepted only when the pick can VERIFY its
    direction (``_find_directional_transit_beat``): the previous stop as
    an origin the beat names in its ``trigger_address`` or opening origin
    clause, or the destination named by a beat at the departure stop in
    its ``trigger_address`` or opening sentence. When no verified match
    exists, falls through to GLUE_NAV with explicit ``from X, walk to Y,
    distance approx Nm`` context so the runtime can synthesize a coherent
    navigation instruction without inventing facts.

    ``consumed_beat_ids`` filters transit candidates so a beat already
    emitted earlier in the script (e.g. by `current`'s prior anchor
    block, or by a previous transit) isn't picked again. Without this,
    multi-anchor tours emitted the same transit beat 2-3 times.

    Track B (Step B.4): ``vignette_beats`` — the chosen walk-past beats for
    this leg (``BeatSequence.vignette_beats[stop_idx]``) — are voiced AFTER
    the transit beat/glue, one beat-cited one-liner each (the FIRST sentence
    of the beat's ``script_body``; corpus text, so no invention issue).
    Vignette beats are not POIBeats entries, so anchor blocks never see them.
    """
    consumed = consumed_beat_ids or set()
    # Prefer a transit beat at `current` whose origin matches `previous`
    # (the most common shape — transit beats describe arriving at their
    # POI from a named origin). Fall back to one at `previous` whose
    # destination matches `current` (less common; some guidebooks
    # attach the transit at the leaving POI).
    transit_beat = _find_directional_transit_beat(
        current, prev_name=previous.poi_name, consumed=consumed
    )
    if transit_beat is None:
        transit_beat = _find_directional_transit_beat(
            previous, dest_name=current.poi_name, consumed=consumed
        )
    if transit_beat is not None and transit_beat.script_body:
        out_sentences = _beat_to_sentences(transit_beat, stop_idx)
    else:
        distance_m = _segment_distance_m(route, stop_idx)
        leg_seconds = _segment_leg_seconds(route, stop_idx)
        is_final_dest = current.poi_id.startswith(_END_B_SENTINEL_PREFIX)
        template_nav = _template_nav(
            previous.poi_name, current.poi_name, leg_seconds, stop_idx,
            is_final_destination=is_final_dest,
        )
        if is_final_dest:
            # A pinned endpoint with no content: voice a graceful arrival, never
            # "walk to" a placeholder named "Destination". Skip the glue client.
            text = template_nav
        else:
            # Phase 8 S8.3a (W8.2 R1, 11/11): the nav glue KNOWS ITS LEG — the
            # request hands Haiku the routed minutes (the same number the tour
            # was budgeted with) and the leg-voice rules, so the line cannot
            # guess a bearing or a length. The coercion guard below is the
            # second line of defence; validation's floor is the third.
            minutes = _nav_walk_minutes(leg_seconds)
            distance_clause = f", distance approx {round(distance_m)}m" if distance_m else ""
            length_clause = (
                f" The walk is about {minutes} minutes; if you say its length, use "
                f"exactly that number."
                if minutes >= 2
                else " The walk is under two minutes; say 'just ahead' rather than a number."
            )
            request = (
                f"From {previous.poi_name}, walk to {current.poi_name}{distance_clause}. "
                f"Use only navigation language, no facts, no names, no dates."
                f"{length_clause} Name direction only as left, right or straight ahead, "
                f"or by a visible landmark from the context — never compass points."
            )
            context = _format_glue_context(previous, current)
            out = client.stitch(GLUE_NAV, context, request)
            # minutes==0 means the leg carries no budget to check against (the
            # `or None` below); any other count must be spoken exactly.
            text = _coerce_glue_output(out, default=template_nav, leg_minutes=minutes or None)
        out_sentences = [
            Sentence(text=text, source_id=GLUE_NAV, source_type="glue", stop_idx=stop_idx)
        ]
    # Vignette beats belong to WALK-PAST POIs (route.vignettes), not the seated
    # route.pois; the one-liner cap's name guard needs those names.
    poi_names = {p.id: p.name for p in route.pois}
    for _pois in route.vignettes.values():
        for _p in _pois:
            poi_names.setdefault(_p.id, _p.name)
    out_sentences.extend(_vignette_one_liners(vignette_beats, stop_idx, poi_names))
    return out_sentences


def _vignette_one_liners(
    vignette_beats: tuple[BeatRef, ...],
    stop_idx: int,
    poi_names: dict[str, str] | None = None,
) -> list[Sentence]:
    """One beat-cited sentence per walk-past vignette beat (Track B B.4).

    The beat's first sentence, clause-capped by ``vignette_one_liner_text`` when it
    is a long run-on, attributed to the beat's own id at the leg's ``stop_idx``.
    Beats with no body contribute nothing. ``select_vignette_beats`` prefers a
    SELF-NAMING beat, so the one-liner reads as unmistakably about the walk-past POI;
    ``poi_names`` (poi_id -> name) lets the cap keep that name in the shortened line.
    """
    names = poi_names or {}
    out: list[Sentence] = []
    for beat in vignette_beats:
        text = vignette_one_liner_text(beat.script_body, names.get(beat.poi_id, ""))
        if not text:
            continue
        out.append(
            Sentence(
                text=text,
                source_id=beat.id,
                source_type="beat",
                stop_idx=stop_idx,
            )
        )
    return out


def _segment_distance_m(route: Route, stop_idx: int) -> float:
    """Walking-segment distance (m) for the transit into stop ``stop_idx``.

    The Route carries a TransitSegment for every leg; the segment
    arriving at stop ``stop_idx`` is at index ``stop_idx`` because the
    first transit (idx 0) is start → first stop.
    """
    if 0 <= stop_idx < len(route.transits):
        return float(route.transits[stop_idx].distance_m)
    return 0.0


def _segment_leg_seconds(route: Route, stop_idx: int) -> int:
    """Walking-segment seconds for the transit into stop ``stop_idx``.

    Delegates to ``routing.leg_walk_seconds`` — the one per-leg expression the
    route total, the served clock and reflection all use — so the leg this
    announces to the walker is the leg the tour was budgeted with. It used to
    restate the rule here (and so did the other three), and the restatements
    read nullness where the total read provenance. (The fourth copy it named,
    audit.py's ``route_eta_seconds``, was dead and is gone.)
    """
    if 0 <= stop_idx < len(route.transits):
        return leg_walk_seconds(route.transits[stop_idx])
    return 0


def _find_directional_transit_beat(
    stop: POIBeats,
    *,
    prev_name: str | None = None,
    dest_name: str | None = None,
    consumed: set[str] | None = None,
) -> BeatRef | None:
    """Return the first transit-class beat whose direction matches the segment.

    ``prev_name`` (a beat at the ARRIVAL stop) vouches only as a verified
    ORIGIN: named in the beat's ``trigger_address``, or named by the body's
    opening origin clause (``_ORIGIN_CLAUSE_RE`` — "From X…", "Leave X…"). A
    name that merely appears somewhere in the body is where the beat is GOING,
    and a destination-street match once picked Frommer's Châtelet approach for
    a walker arriving along the very street the beat turns into.
    ``dest_name`` (a beat at the DEPARTURE stop) vouches only as a verified
    DESTINATION: named in the beat's ``trigger_address`` or its OPENING
    sentence — a departure beat says where it is going up front ("Leave X and
    walk to Y…"). A destination mentioned only in passing later ("you will
    pass Y on your right") is scenery on the way to somewhere else, and a
    substring over the whole body once let a walk-to-Les-Halles beat narrate
    the leg to the tower it merely passes.

    Three refusals, each because a direction this code cannot verify is a
    direction it must not speak. A beat that speaks COMPASS carries the bearing
    of the walk its guidebook took. A beat that speaks the guidebook's OWN
    ITINERARY ("where the walk ends") promises a route this day never takes.
    And a call with no names to check against has nothing to vouch for the beat
    at all. In every case the caller falls through to the deterministic nav
    template, which names both real ends, speaks the routed minutes and cannot
    say a compass point.
    """
    skip = consumed or set()
    if not prev_name and not dest_name:
        return None
    for beat in stop.beats:
        if beat.id in skip:
            continue
        if (beat.narrative_function or "").lower() not in TRANSIT_NARRATIVE_FUNCTIONS:
            continue
        body = beat.script_body or ""
        haystack = (f"{beat.trigger_address or ''} {body}").lower()
        if _COMPASS_RE.search(haystack) or _ITINERARY_RE.search(haystack):
            continue
        if prev_name and _is_verified_origin(prev_name, beat):
            return beat
        if dest_name and _is_verified_destination(dest_name, beat):
            return beat
    return None


def _is_verified_destination(dest_name: str, beat: BeatRef) -> bool:
    """Does ``beat`` verifiably describe walking TO ``dest_name``?

    Yes only when the name is in ``trigger_address``, or in the body's FIRST
    sentence — a departure beat names its destination up front.
    """
    dest = dest_name.lower()
    if dest in (beat.trigger_address or "").lower():
        return True
    sents = split_sentences(beat.script_body or "")
    return bool(sents) and dest in sents[0].lower()


def _is_verified_origin(prev_name: str, beat: BeatRef) -> bool:
    """Does ``beat`` verifiably describe arriving FROM ``prev_name``?

    Yes only when the name is in ``trigger_address``, or when the body's first
    sentence is an origin clause (``_ORIGIN_CLAUSE_RE``) that names it.
    """
    prev = prev_name.lower()
    if prev in (beat.trigger_address or "").lower():
        return True
    sents = split_sentences(beat.script_body or "")
    if not sents:
        return False
    opening = sents[0]
    return bool(_ORIGIN_CLAUSE_RE.match(opening)) and prev in opening.lower()


# ---------------------------------------------------------------------------
# Stage 5 — closing
# ---------------------------------------------------------------------------


#: The one-line fallback close for a REST (a bench seated by the party or the dial —
#: it has no story to land): nonpropositional, names the next stop so two rests on
#: one day never repeat a sentence (the rubric's C5). Rosemary (05, step 3): "The
#: bench is a stop. It has a location, a duration, and content."
REST_CLOSE_TEMPLATE: str = "Sit as long as you like — {next} can wait."
REST_CLOSE_LAST_TEMPLATE: str = "Sit as long as you like — that's the walk."
#: The one-line fallback close for a story stop that is not the day's last.
STOP_CLOSE_TEMPLATE: str = "And that's {name}."


def fallback_close_text(
    tour_input: TourInput,
    *,
    poi_name: str,
    is_rest: bool,
    is_last: bool,
    n_stops: int,
    next_name: str | None,
) -> str:
    """THE fallback close for one stretch — the stitch's line, and the sentence the
    finalizer compares a composed close against to know whether the writer wrote one
    (a template close is never counted as authored; Phase 6 S6.4). Deterministic and
    name-only: the stop's own name is the one vocabulary glue is licensed to use."""
    if is_last:
        if is_rest:
            return REST_CLOSE_LAST_TEMPLATE
        if tour_input.round_trip and n_stops == 1:
            return f"And that completes our circle of {poi_name}."
        if tour_input.round_trip:
            return "And that closes the loop, back where we started."
        return GENERIC_OPEN_TOUR_CLOSING
    if is_rest:
        return REST_CLOSE_TEMPLATE.format(next=next_name or "the rest of the walk")
    return STOP_CLOSE_TEMPLATE.format(name=poi_name)


def is_fallback_close(text: str, tour_input: TourInput, *, poi_name: str, n_stops: int) -> bool:
    """Is ``text`` one of the stitch's own closing templates for this stop (in ANY of its
    positions)? The finalizer uses it: a composed close byte-identical to a template was
    not authored. Whitespace-insensitive, never fuzzy."""
    norm = " ".join(text.split())
    candidates = {
        fallback_close_text(
            tour_input, poi_name=poi_name, is_rest=is_rest, is_last=is_last,
            n_stops=n_stops, next_name=None,
        )
        for is_rest in (False, True)
        for is_last in (False, True)
    }
    if norm in {" ".join(c.split()) for c in candidates}:
        return True
    # A rest close names the NEXT stop; match the shape without knowing it.
    head = REST_CLOSE_TEMPLATE.split("{next}")[0].strip()
    return norm.startswith(head) and norm.endswith("can wait.")


def _build_stop_close(
    beat_sequence: BeatSequence,
    tour_input: TourInput,
    route: Route,
    *,
    stop_idx: int,
    n_stops: int,
) -> Sentence:
    """One GLUE_CLOSING line at the END of stop ``stop_idx`` — the fallback close.

    Until Phase 6 S6.4 only the final stop closed, with two generic lines (a physical
    closure and a thank-you-and-keep-exploring sign-off). The sign-off is gone (W6.2 R2,
    11/11: "keep exploring on your own" / the thank-you are not a close's job), and every
    stop now ends on one line. Phase 7 (2026-04-29) ``reorder_final_stop_for_closing``
    still lifts the closing-friendly beat to be the LAST beat at the final stop; this
    line follows it. No thematic summary: the authored close (the one writer's) lands
    the point; this template only names the place.
    """
    plan = beat_sequence.poi_beats[stop_idx]
    poi = route.pois[stop_idx] if stop_idx < len(route.pois) else None
    is_rest = bool(poi is not None and poi.poi_role == "body")
    next_plan = beat_sequence.poi_beats[stop_idx + 1] if stop_idx + 1 < n_stops else None
    text = fallback_close_text(
        tour_input,
        poi_name=plan.poi_name,
        is_rest=is_rest,
        is_last=stop_idx == n_stops - 1,
        n_stops=n_stops,
        next_name=next_plan.poi_name if next_plan is not None else None,
    )
    return Sentence(text=text, source_id=GLUE_CLOSING, source_type="glue", stop_idx=stop_idx)


#: The spoken acknowledgment at a clock-closed stop — two branches, because
#: two different things are true (`ClockExclusion.all_day` picks): a place
#: shut for the whole day must say so, or Aiko plans a wasted return trip;
#: "at the moment" stays for a partial closure, true whether the door opens
#: later or already shut. No weekday, no clock time — the glue invention scan
#: licenses neither, and the schedule detail stays on the day's notes channel.
CLOSED_STOP_LINE_TEMPLATE: str = (
    "{name} is closed at the moment, so we'll take it in from out here."
)
CLOSED_ALL_DAY_LINE_TEMPLATE: str = (
    "{name} is closed today, so we'll take it in from out here."
)
#: The closed START, when the shut place is NOT in the day at all
#: (``ClockExclusion.at_start`` with an off-route poi_id): the walker stands
#: beside a door the plan removed, and setting off past it wordlessly is a
#: lie by omission — they can SEE it. Same today/at-the-moment honesty split
#: as the stop templates, same no-weekday/no-clock-time discipline.
CLOSED_START_LINE_TEMPLATE: str = (
    "{name}, right here at the start, is closed at the moment, "
    "so the walk goes on without it."
)
CLOSED_START_ALL_DAY_LINE_TEMPLATE: str = (
    "{name}, right here at the start, is closed today, "
    "so the walk goes on without it."
)
#: The KEPT closed start, seated later than stop 0: the walker is standing at
#: a shut door the day still visits from the outside. Spoken as a bare FACT —
#: never "we'll come back" (W6.2 R4: the session may trade the stop away, and
#: a promise to a place the walker never reaches is a small shut door). Its
#: own stop speaks the full acknowledgment on arrival.
CLOSED_START_KEPT_LINE_TEMPLATE: str = (
    "{name}, right here at the start, is closed at the moment."
)
CLOSED_START_KEPT_ALL_DAY_LINE_TEMPLATE: str = (
    "{name}, right here at the start, is closed today."
)


def _closure_opening_lines(route: Route) -> dict[int, Sentence]:
    """stop_idx → the closure acknowledgment, for every on-route stop whose
    door the CLOCK voided (``ClockExclusion.kept_outside`` — never the no-time
    or dusk notes, which describe places that are not closed and stay on the
    screen channel). Owner ruling: interior description at a closed stop is
    welcome — a gift to the visitor who will come back — once the closure is
    said, once, FIRST. The caller places this as the stop's first stationary
    sentence; the premium writer receives it in the stitch and may rewrite it
    (its DOOR block already licenses "closed today")."""
    closed = {e.poi_id: e for e in route.clock_exclusions if e.kept_outside}
    if not closed:
        return {}
    return {
        idx: Sentence(
            text=(
                CLOSED_ALL_DAY_LINE_TEMPLATE
                if closed[poi.id].all_day
                else CLOSED_STOP_LINE_TEMPLATE
            ).format(name=poi.name),
            source_id=GLUE_STAGING,
            source_type="glue",
            stop_idx=idx,
        )
        for idx, poi in enumerate(route.pois)
        if poi.id in closed
    }


def _closed_start_line(route: Route) -> Sentence | None:
    """The acknowledgment for a shut place AT the walk's start
    (``ClockExclusion.at_start``, set by selection from coordinates). Three
    cases: a place NOT in the day says the walk goes on without it; a KEPT
    place seated later than stop 0 is named as a bare fact — the walker is
    looking at that door NOW, and its own stop speaks the full line on
    arrival; a place that IS stop 0 adds nothing, because its own
    acknowledgment already holds the first-stationary slot. The caller
    speaks this ahead of a closed stop 0's own line when both are true: two
    shut doors are two facts, each said once."""
    on_route = {poi.id: idx for idx, poi in enumerate(route.pois)}
    kept_later: ClockExclusion | None = None
    for excl in route.clock_exclusions:
        if not excl.at_start:
            continue
        idx = on_route.get(excl.poi_id)
        if idx == 0:
            continue
        if idx is None:
            # The dropped door outranks a kept one, whatever order the
            # planner appended: this line is the ONLY place that door is
            # ever named, while a kept door's own stop still speaks the
            # full acknowledgment on arrival.
            return Sentence(
                text=(
                    CLOSED_START_ALL_DAY_LINE_TEMPLATE
                    if excl.all_day
                    else CLOSED_START_LINE_TEMPLATE
                ).format(name=excl.name),
                source_id=GLUE_STAGING,
                source_type="glue",
                stop_idx=0,
            )
        if kept_later is None:
            kept_later = excl
    if kept_later is not None:
        return Sentence(
            text=(
                CLOSED_START_KEPT_ALL_DAY_LINE_TEMPLATE
                if kept_later.all_day
                else CLOSED_START_KEPT_LINE_TEMPLATE
            ).format(name=kept_later.name),
            source_id=GLUE_STAGING,
            source_type="glue",
            stop_idx=0,
        )
    return None


def _drop_door_lines_at_doorless_stops(
    sentences: list[Sentence],
    route: Route,
    *,
    vignette_beat_ids: frozenset[str] = frozenset(),
    transit_beat_ids: frozenset[str] = frozenset(),
) -> list[Sentence]:
    """Remove through-the-door units at stops whose visit stays OUTSIDE.

    The door vocabulary is validation's ``_DOOR_LINE_RE`` — THE one definition
    the placement floor refuses these lines by — so the stitch drops exactly
    what the floor would flag. Keyed on EXPLICIT ``False`` in
    ``Route.visit_goes_inside``: an empty map (a dateless or legacy route)
    means nobody priced a door, and nothing is touched — the identity default.
    Walk-concurrent sentences (nav, reflections, vignette one-liners) play on
    a leg, not at the stop, and are exempt exactly as the floor exempts them.
    A sentence that was ONLY the invitation drops whole; a mixed one keeps its
    other units, so a fact is never lost to the filter.
    """
    from .validation import _DOOR_LINE_RE  # import here: validation imports this module

    doorless = {
        idx
        for idx, poi in enumerate(route.pois)
        if route.visit_goes_inside.get(poi.id) is False
    }
    if not doorless:
        return sentences
    out: list[Sentence] = []
    for sentence in sentences:
        if sentence.stop_idx not in doorless or is_walk_concurrent(
            sentence, vignette_beat_ids, transit_beat_ids
        ):
            out.append(sentence)
            continue
        units = split_sentences(sentence.text) or [sentence.text]
        kept = [unit for unit in units if not _DOOR_LINE_RE.search(unit)]
        if len(kept) == len(units):
            out.append(sentence)
        elif kept:
            out.append(sentence.model_copy(update={"text": " ".join(kept)}))
    return out


# ---------------------------------------------------------------------------
# Beat → sentences
# ---------------------------------------------------------------------------


def _beat_to_sentences(beat: BeatRef, stop_idx: int) -> list[Sentence]:
    if not beat.script_body:
        return []
    return [
        Sentence(text=s, source_id=beat.id, source_type="beat", stop_idx=stop_idx)
        for s in split_sentences(beat.script_body)
    ]


# ---------------------------------------------------------------------------
# Glue helpers
# ---------------------------------------------------------------------------


def _format_glue_context(previous: POIBeats, current: POIBeats) -> str:
    """A short context window for Haiku: last sentence of previous + first of next."""
    pieces: list[str] = []
    last_text = _tail_sentence(previous)
    if last_text:
        pieces.append(f"PREVIOUS STOP — {previous.poi_name}: {last_text}")
    head_text = _head_sentence(current)
    if head_text:
        pieces.append(f"NEXT STOP — {current.poi_name}: {head_text}")
    return "\n".join(pieces)


def _tail_sentence(stop: POIBeats) -> str:
    for beat in reversed(stop.beats):
        if beat.script_body:
            sents = split_sentences(beat.script_body)
            if sents:
                return sents[-1]
    return ""


def _head_sentence(stop: POIBeats) -> str:
    for beat in stop.beats:
        if beat.script_body:
            sents = split_sentences(beat.script_body)
            if sents:
                return sents[0]
    return ""


def _coerce_glue_output(raw: str, *, default: str, leg_minutes: int | None = None) -> str:
    """Defang the LLM output: empty / sentinel → default; trim quotes.

    Phase 8 S8.3a (W8.2 R1): a nav line that speaks COMPASS, or a minute count
    that is not the routed leg's own number (``leg_minutes``; None = no budget
    to check), falls back to the deterministic template — which is compass-free
    and speaks the routed number by construction. The guard sits behind the
    prompt and in front of validation's floor, so a bad roll costs one nicety,
    never a refusal.
    """
    if not raw:
        return default
    text = raw.strip().strip('"').strip("'").strip()
    if not text or NO_GLUE_SENTINEL in text:
        return default
    # Take only the first sentence in case Haiku emitted multiple.
    parts = split_sentences(text)
    candidate = parts[0] if parts else text
    if any(p in candidate.lower() for p in FORBIDDEN_PHRASES):
        return default
    if _COMPASS_RE.search(candidate):
        return default
    if leg_minutes is not None and any(
        count != leg_minutes for count in spoken_minute_counts(candidate)
    ):
        return default
    return candidate


# ---------------------------------------------------------------------------
# Output rollup
# ---------------------------------------------------------------------------


def _flatten_pois(
    beat_sequence: BeatSequence,
    route: Route,
    voiced_beat_ids: set[str] | None = None,
) -> tuple[ScriptPOI, ...]:
    """Build the selected_pois roster aligning Route POIs with their beats.

    ``voiced_beat_ids`` is the set of beat ids that ACTUALLY emitted at least one
    sentence into the final stitch (post claim-dedup). When supplied, a stop's
    ``beat_ids`` and its ``planned_audio_seconds`` dwell sum are restricted to
    those beats. This prevents a transit-class beat that the direction check
    dropped from _build_transit (and that the anchor block filters out) from
    being reported as voiced: leaving it in ``beat_ids`` would both over-report
    dwell by its spoken seconds AND wrongly mark it "voiced" so keep-exploring
    (``extra_beat_ids``) excludes its unreachable content. When ``None`` (legacy
    callers/tests), every planned beat is counted as before.
    """
    by_id = {pb.poi_id: pb for pb in beat_sequence.poi_beats}
    out: list[ScriptPOI] = []
    for poi in route.pois:
        plan = by_id.get(poi.id)
        if plan is None:
            plan_beats: tuple[BeatRef, ...] = ()
        elif voiced_beat_ids is None:
            plan_beats = plan.beats
        else:
            plan_beats = tuple(b for b in plan.beats if b.id in voiced_beat_ids)
        beat_ids = tuple(b.id for b in plan_beats)
        out.append(
            ScriptPOI(
                id=poi.id,
                name=poi.name,
                tier=poi.tier,
                lat=poi.lat,
                lng=poi.lng,
                area=route.spine_area
                if route.spine_area in poi.areas
                else (poi.areas[0] if poi.areas else None),
                # Time AT the stop, priced once in selection against this
                # visitor's interest and this place's capacity. The combining
                # rule is IMPORTED, not spelled here: the planner's gate applies
                # the same `stop_seconds` to the same capped plans, so certified
                # and served are the same quantity rather than two `max(...)`
                # calls that agree until someone edits one of them.
                dwell_seconds=stop_seconds(
                    route.planned_visit_seconds.get(poi.id, 0),
                    planned_audio_seconds(plan_beats),
                ),
                beat_ids=beat_ids,
                # C9g: the governor's trimmed-off beats, surfaced for
                # keep-exploring (never silently dropped). Empty unless the cap
                # fired on this stop.
                overflow_beat_ids=beat_sequence.overflow_by_poi.get(poi.id, ()),
            )
        )
    return tuple(out)


def _lens_coverage(beat_sequence: BeatSequence) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for plan in beat_sequence.poi_beats:
        for beat in plan.beats:
            for lens in beat.lenses:
                counter[lens] += 1
    return dict(counter)


def _word_count(text: str) -> int:
    return len(text.split())


def _sum_audio(sentences: Iterable[Sentence], beat_sequence: BeatSequence) -> int:
    """Spoken seconds of the tour: the WORDS ACTUALLY VOICED, at ``SPOKEN_WPM``.

    REWRITTEN 2026-07-19 because the previous model could not measure the thing it
    was named for, and an entire day of tuning was spent optimising it before that
    was noticed. It had two defects, either one disqualifying:

    1. **A flat 4 s per glue sentence**, regardless of length. Every reflection,
       every navigation line, every vignette one-liner counted as 4 s. A 60-word
       reflection takes ~24 s to speak and was credited 4 s. That made leg content
       invisible to the tour's own audio clock: the engine would write narration
       for a walking leg, refuse to count it, conclude it was short on audio, and
       respond by seating ANOTHER STOP — creating more walking, hence more silence.

    2. **A hard cap at the CORPUS estimate** — ``beat_secs * min(1.0, voiced/body)``.
       The ratio is capped at 1.0, so richer composed prose could never raise the
       number; only dedup could lower it. "How much audio does this tour deliver?"
       was therefore answerable only downward. Measured by an adversarial review:
       1 862 composed words (~745 s of speech) were credited as 696 s.

    Together these meant ``total_audio_seconds`` measured SEATING VOLUME, not audio.
    Every figure derived from it — the 0.8 C3 floor comparisons, the fill pass's
    sense of a deficit — was reading an instrument pointed at the wrong quantity.

    THE NEW MODEL. Sum the words of every voiced sentence and divide by one
    documented rate. That is all. It needs no per-source special cases: a vignette
    voices one sentence and is credited that sentence's words; a merged beat is
    credited exactly the words the merge kept; a composed stop that genuinely says
    more is credited more. The dedup honesty the old ratio was reaching for falls
    out for free, because suppressed sentences contribute no words.

    ``SPOKEN_WPM`` is 150 — NOT a new judgement call. It is already this engine's
    rate: ``routing.beat_spoken_seconds`` falls back to ``word_count / 150 * 60``,
    density's ``word_count / 2.5`` is the same figure, and the live Paris corpus's
    own populated ``est_spoken_seconds`` implies it tightly (486 beats: p10 147,
    median 150, p90 153). The standard's cited external range is 130-150 wpm
    (Nubart, Musa Guide); 150 is the fast end of it and the end this corpus was
    built at, so keeping it preserves continuity with every existing estimate.

    ``beat_sequence`` is retained in the signature: callers pass it, and the
    per-beat identity it carries is still what the CALLERS use for their own
    accounting. This function no longer needs it, which is itself the point — the
    audio clock is now a property of the SCRIPT, not of the seating plan.
    """
    voiced_words = sum(_word_count(s.text) for s in sentences)
    return round(voiced_words / SPOKEN_WPM * 60)


__all__ = [
    "ARITH",
    "FORBIDDEN_PHRASES",
    "GLUE_CALLBACK",
    "GLUE_CLOSING",
    "GLUE_LABELS",
    "GLUE_NAV",
    "GLUE_PACING",
    "GLUE_REFLECTION",
    "GLUE_STAGING",
    "SYNTHESIZED_OPENER",
    "generate",
    "is_walk_concurrent",
    "split_sentences",
    "spoken_minute_counts",
    "transit_class_beat_ids",
]
