"""THE TOUR QUALITY RUBRIC — the mechanical floor that runs on every tour.

Every threshold below states its kind: MEASURED, INHERITED from the engine,
CITED to professional audio-guide practice, or JUDGEMENT.

Two layers:

* **FLOOR (this module)** — deterministic, $0, runs on every tour. A BLOCKER result
  means the tour is not fit to serve and the caller regenerates.
* **GATE (elsewhere)** — semantic, model-judged. Meaning-level properties a counter
  can never see. Word-matching for MEANING is a
  banned shortcut in this project: narrative quality is judged semantically or not at
  all. This module therefore checks only STRUCTURE — counts, ratios, budgets — and
  deliberately makes no claim about whether the prose is good.

The two failures this exists to catch, both measured on the live Paris graph:

* **STARVATION** — Sainte-Chapelle (tier 5, 12 beats in corpus) rendered as 9 words.
* **GORGING** — Notre-Dame rendered 1022 words in one stop, over even the walking-tour
  adaptation of the museum sector's 250-words-per-station ceiling.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypedDict

from src.tour.contract import (
    END_B_SENTINEL_PREFIX,
    BeatRef,
    BeatSequence,
    Route,
    Script,
    Sentence,
)
from src.tour.generation import (
    _word_count,
    is_walk_concurrent,
    transit_class_beat_ids,
)
from src.tour.narration_quality import score_narration
from src.tour.routing import (
    SPOKEN_WPM,
    haversine_m,
    spoken_seconds_for_words,
    total_walk_seconds,
)
from src.tour.visit_time import served_elapsed_seconds

# Thresholds.
# Each value's comment states its KIND and the data behind it:
#   MEASURED  — derived from a real $0 engine run on the live Paris graph
#   INHERITED — already a constant elsewhere in the engine; not a new number
#   CITED     — professional audio-guide practice
#   JUDGEMENT — chosen by us; the comment says so explicitly and shows the data

#: JUDGEMENT. A POI with at least this many corpus beats is "rich" — starving it is a
#: defect rather than an editorial choice. Sainte-Chapelle had 12; the thin POIs
#: around it had 3.
STARVE_MIN_BEATS: int = 5

#: JUDGEMENT, anchored on MEASURED data. Healthy stops ran ~17 and ~40 words/beat.
#: Deliberately permissive: this catches 9-words-for-12-beats (0.75), not terseness.
STARVE_MIN_WORDS_PER_BEAT: float = 12.0

#: MEASURED. The widest stop in each of the eight authored certification tours runs
#:     661 / 662 / 664 / 673 / 679 / 757 / 761 / 808
#: words. 850 clears that ceiling (808) at 1.05x and still catches the defect it exists
#: for — a Notre-Dame wall of 1022-1038 words — at 1.20x. Museum practice caps a station
#: at 250 words; a walking-tour anchor with a long dwell is not a museum station, so that
#: figure does not apply.
#: Guarded by test_c8_clears_the_widest_stop_in_the_certification_corpus.
GORGE_MAX_WORDS_PER_STOP: int = 850

#: JUDGEMENT, anchored on a MEASUREMENT of the gold text — the hand-written reference
#: passage that ``scripts/score_gold_text.extract_gold_text`` reads — whose
#: `mean_sentence_words` is 19.11. The 10-15 word museum-copy range describes station
#: copy; the gold text and every saved tour exceed it, so it is not the anchor.
#:
#: C9 fires per stop, so one long stop flags a whole tour. Per stop the gold reads
#: shorter than 34 of 38 machine stops (min 16.9 / median 23.2 / max 34.3), so the check
#: ranks hand-written text above machine output.
#:
#: The cap is the smallest round number strictly above the gold mean of 19.11: scores
#: round to 2 dp, and a stop's mean is taken over far fewer sentences than the gold's.
#: Guarded by test_c9_cap_admits_the_owners_own_gold_text.
MAX_SENTENCE_WORDS: float = 20.0

#: JUDGEMENT. No single stop should hold more than this share of the tour's words.
BALANCE_MAX_SHARE: float = 0.60

#: JUDGEMENT, WARN only. It catches one building told as two stops — e.g. Palais de
#: Justice and Conciergerie seated 17 m apart, the second opening by describing the
#: spot the listener just stood on. 50 m, not 100 m: Sainte-Chapelle sits 86 m from
#: the Conciergerie and is a genuinely distinct attraction.
#:
#: No distance separates 'one building told twice' from 'two neighbours on one
#: street'. Across the real corpora (Paris 370 POIs, 48 pairs under 50 m; New York
#: 402, 46 pairs) true duplicates cluster at 0.0-1.7 m, while distinct walkable
#: landmarks start at ~8 m — Hôtel Le Meurice <-> Angelina at 8.4 m, and Le Meurice
#: and Galignani are consecutive stops of the gold text (see MAX_SENTENCE_WORDS), so a
#: blocker here would refuse the gold text's own tour. So this check never
#: blocks serving and ``select_route`` does not filter on this distance
#: (test_selection_does_not_filter_close_but_distinct_pois). Whether the same place is
#: told twice is a semantic question about repeated facts; C12 is a WARN surfaced to
#: the editor.
MIN_STOP_SEPARATION_M: float = 50.0

#: JUDGEMENT. Ceiling on the C1 starvation floor, as a share of the gorge cap, so C1
#: and C8 can never demand contradictory things for a beat-rich POI. See the comment
#: at the C1 check for the Notre-Dame arithmetic that forced this.
STARVE_FLOOR_MAX_SHARE_OF_CAP: float = 0.5

#: MEASURED. Seconds of spoken audio required per second of WALKING.
#:
#: The denominator is walking time: silence while standing is the product working,
#: silence while walking is the defect. A stop lasts as long as the place and the
#: visitor justify, so a five-hour tour can spend three and a half hours standing still;
#: a share of the requested duration would demand nearly an hour of continuous narration
#: and refuse exactly the long, stop-heavy day the planner produces.
#:
#: MEASURED on the eight authored certification tours:
#:
#:     0.738 paris-pont-neuf-notre-dame-60
#:     0.541 paris-ile-open-90
#:     0.472 nyc-village-loop-60
#:     0.451 nyc-grand-central-times-square-60
#:     0.414 paris-marais-loop-60
#:     0.387 nyc-central-park-open-90
#:     0.327 nyc-lower-manhattan-90        <- thinnest tour this project ACCEPTED
#:     ----
#:     0.059 paris-west-axis-90            <- genuinely starved: 5.0 min of audio
#:                                            across 85.3 min of walking
#:
#: The two poles sit 5.5x apart under this denominator.
#:
#: AND ONE INTERIOR-HEAVY TOUR ADDED to the derivation set, because the eight above
#: are exterior-only and a threshold calibrated on exterior-only tours alone is
#: off-distribution for what the planner produces: the 300-minute Rue Royale ->
#: Notre-Dame flagship, 22.6 min of audio across 85.8 min of walking, 7 stops —
#: **0.263**, and the worst tour that must PASS.
#:
#: 0.12 is the geometric midpoint of the starved pole (0.059) and that worst
#: must-pass tour (0.263), which is 0.1246, rounded DOWN to 0.12 so the nearest
#: observed passing tour in the wider corpus (a one-stop 45-minute artifact at
#: exactly 0.125) is not decided by a coin flip. Block margin 2.03x, pass margin
#: 2.19x.
#:
#: CROSS-CHECKED against every stored tour with both numbers — 210 files under
#: ``data/*/tours``. The distribution has a real gap, not a smear: thirteen tours sit
#: at 0.018-0.054, all of them the same failing corridor (2 stops, 1.6-4.5 min of
#: audio across 76-123 min of walking), then nothing at all until 0.125. Every bar
#: between 0.055 and 0.124 blocks the identical thirteen, so this value is not
#: balanced on a knife edge.
#:
#: It is NOT a target. Guarded from both sides by
#: test_c3_clears_the_certification_corpus_and_still_blocks_the_starved_tour.
MIN_AUDIO_FRAC_OF_WALKING: float = 0.12

#: MEASURED over every real composed tour in ``data/*/tours/`` — 195 tour
#: files: **paris 191, london 4. ``data/new_york/tours/`` DOES NOT EXIST** and
#: contributed nothing, so this is a Paris-dominated sample (paris 485 of 501
#: stop/tour samples with a nonzero tour mean), not a three-city one. No measured or
#: cited absolute "dates per 100 words" ceiling exists for C11
#: (unlike C1/C8), so C11 flags a stop RELATIVE to
#: the tour's own mean year_density rather than an invented absolute cut. The
#: multiple is anchored on the real distribution of (stop_density / tour_mean)
#: across every measured stop: median 0.97, p90 1.67, p95 2.00, max 3.11. 3.4% of
#: samples exceed a 2.0x multiple, i.e. this sits at roughly the p95 of real
#: tours — a genuine outlier cut, not noise, without being so tight it fires on the
#: routine spread every tour has. This was a one-off measurement (not a standing
#: regression test — the corpus in ``data/*/tours/`` will drift), reproducible by
#: running ``score_narration`` per stop over every ``data/{city}/tours/*.json`` and
#: taking each stop's ``per_100w["year_density"]`` against its tour's own mean.
#: Revisit if a future corpus's distribution shifts.
OUTLIER_YEAR_DENSITY_MULTIPLE: float = 2.0

#: JUDGEMENT, MEASURED on real days. POINT FIRST: the point of a stop lands inside its
#: first ~100
#: words, counted from the stop's OWN first sentence about the place, never from the nav
#: line or the recap. Measured stops opened with 24-44 s of recap ("think back over where
#: we've walked" — 60-110 words) before the place was named. A stop's story must START
#: inside this many words of glue; one placing sentence ("Stand by the horseman.") is 8-25.
POINT_FIRST_PREAMBLE_MAX_WORDS: int = 40
#: The "first hundred words" ≈ 40 s at SPOKEN_WPM — where the stop's
#: PRIMARY beat (the first of its capped plan, ScriptPOI.beat_ids[0]) must be cited by.
#: A $0 proxy: whether THAT sentence is the point is a reading, never this check.
POINT_FIRST_STORY_WORDS: int = 100


class Severity(StrEnum):
    """BLOCKER regenerates the tour. WARN surfaces to the editor."""

    BLOCKER = "blocker"
    WARN = "warn"


class FindingContext(TypedDict, total=False):
    """The numbers a check records beside its message: C1 its floor, C13 its counts."""

    floor: float
    preamble_words: int
    primary_first_cited_words: int | None


class RubricStats(TypedDict, total=False):
    """The measurements ``score_tour`` reports whatever the verdict."""

    total_words: int
    n_stops: int
    audio_minutes: float
    words_by_stop: dict[int, int]
    audio_floor_min: float
    audio_concurrent_s: int
    audio_stationary_s: int
    time_budget_actual_s: int
    time_budget_ceiling_s: int
    leg_walk_capacity_s: int
    year_density_by_stop: dict[int, float]
    year_density_outliers: dict[int, float]


@dataclass(frozen=True)
class Finding:
    check: str
    severity: Severity
    message: str
    stop_idx: int | None = None
    poi_name: str | None = None
    #: Structured numbers a check needed to derive its own verdict and that a later
    #: consumer (``compose_fixable``) needs again — e.g. C1's per-POI floor. Kept
    #: separate from ``message`` (prose for a human) so downstream code never has to
    #: parse a sentence to recover a number score_tour already computed exactly.
    context: FindingContext | None = None


@dataclass
class RubricReport:
    findings: list[Finding] = field(default_factory=list)
    stats: RubricStats = field(default_factory=RubricStats)

    @property
    def blockers(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.BLOCKER]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.WARN]

    @property
    def passed(self) -> bool:
        """False => do not serve this tour; regenerate."""
        return not self.blockers

    def summary(self) -> str:
        if self.passed and not self.warnings:
            return "PASS — no findings"
        head = "PASS" if self.passed else "FAIL"
        return (
            f"{head} — {len(self.blockers)} blocker(s), {len(self.warnings)} warning(s): "
            + "; ".join(f"{f.check}:{f.message}" for f in self.findings[:6])
        )


def c3_audio_floor_seconds(walk_seconds: int) -> float:
    """The C3 thinness floor, in seconds of spoken audio, for a route's WALKING time.

    THE QUESTION IS 'ARE THE WALKS SILENT?', not 'does this tour talk for a fifth of
    its length?'. A stop lasts as long as the place and the visitor jointly justify,
    so a five-hour tour can spend three and a half hours standing in and around
    buildings, and a fraction of total length would demand 57 minutes of continuous
    narration to approve it. That refuses a good day: about 35 minutes of
    audio across five hours, where a visitor stands twenty minutes at Concorde for four
    minutes of talking and does not feel short-changed.

    Silence while you stand somewhere looking at it is the product working. Silence
    while you walk for an hour is the defect this check exists to catch, and walking
    time is the only denominator that asks about it directly.

    NO STOP LIMIT: duration is the only bound on stop count, so the floor is linear in
    walking time with no cap. A cap at what a fixed number of stops could hold would
    contradict that; a longer tour holds proportionally more stops, so the floor never
    demands more than the stops can carry. See
    ``test_c3_floor_never_demands_more_words_than_c8_allows_at_any_offered_duration``
    for the satisfiability proof: the engine caps one stop at
    ``MAX_DWELL_AUDIO_SECONDS`` of audio, so the stops needed to meet this floor each
    carry well under ``GORGE_MAX_WORDS_PER_STOP``.
    """
    return MIN_AUDIO_FRAC_OF_WALKING * walk_seconds


def _concurrent_audio_seconds(script: Script, beat_sequence: BeatSequence | None) -> int:
    """Seconds of narration the tourist hears WHILE WALKING, not while standing.

    Audio spoken while walking is part of the tour experience and costs no elapsed
    time — it is spoken during a leg the tourist is walking anyway. C7 must therefore
    exclude it from the time budget, and C7b bounds it by the walk it rides on.

    Identified from what the engine already records, with no new Sentence field:

    * ``GLUE_NAV`` — the leg's navigation line, spoken on the move.
    * ``GLUE_REFLECTION`` — placed by ``reflection.py`` on a long WALKING leg by
      definition; that is the whole point of the placement rule.
    * vignette beat sentences — walk-past one-liners voiced inside the leg
      (``generation._vignette_one_liners``), identified via
      ``beat_sequence.vignette_beats``.
    * transit-class beat sentences — the walk's own corpus narration
      (``generation._build_transit``), identified via
      ``transit_class_beat_ids`` over the seated beats.

    Everything else — anchor beat sentences, the cold open, staging, closing — is
    spoken at a stop with the tourist standing still, and stays in the time budget.

    Without a ``beat_sequence`` the vignette ids are unknown; those sentences then
    count as stationary, which is the CONSERVATIVE direction (it can only make C7
    stricter, never wrongly pass a tour).
    """
    vignette_ids: set[str] = set()
    transit_ids: frozenset[str] = frozenset()
    if beat_sequence is not None:
        vignette_ids = {
            b.id for beats in beat_sequence.vignette_beats.values() for b in beats
        }
        transit_ids = transit_class_beat_ids(
            beat for plan in beat_sequence.poi_beats for beat in plan.beats
        )
    words = sum(
        _word_count(s.text)
        for s in script.script
        if is_walk_concurrent(s, vignette_ids, transit_ids)
    )
    return spoken_seconds_for_words(words)


def score_tour(
    script: Script,
    route: Route,
    beats_by_poi: Mapping[str, Sequence[BeatRef]],
    *,
    beat_sequence: BeatSequence | None = None,
) -> RubricReport:
    """Score a generated tour against the mechanical quality floor.

    ``beats_by_poi`` is the corpus map (``CorpusSnapshot.beats_by_poi``) — the full set
    of beats AVAILABLE for each POI, which is what makes the starvation check possible:
    it compares what was rendered against what the corpus could have supported.
    """
    report = RubricReport()

    words_by_stop: dict[int, int] = {}
    texts_by_stop: dict[int, list[str]] = {}
    for sentence in script.script:
        words_by_stop[sentence.stop_idx] = words_by_stop.get(sentence.stop_idx, 0) + _word_count(
            sentence.text
        )
        texts_by_stop.setdefault(sentence.stop_idx, []).append(sentence.text)

    total_words = sum(words_by_stop.values())
    report.stats = {
        "total_words": total_words,
        # The stop count is the ROSTER's, not the rendered sentences'. Counting
        # ``words_by_stop`` UNDER-REPORTS whenever a stop rendered nothing at all —
        # the exact tour C6 exists to catch — so the editor saw "2 stops" for a
        # 3-stop tour with one silent stop and had no signal anything was missing.
        "n_stops": len(script.selected_pois),
        "audio_minutes": round(script.total_audio_seconds / 60, 1),
        "words_by_stop": dict(sorted(words_by_stop.items())),
    }

    # C3: are the walks silent?
    # Delivered audio against the tour's own WALKING time, not against the requested
    # duration. A stop the traveller stands at for twenty
    # minutes and hears four minutes about is the product working; an hour of walking
    # with nothing said is the defect. Judging against the request confuses the two,
    # and refuses exactly the long-stop tours the planner produces, where a stop's
    # length is what the place is worth. See MIN_AUDIO_FRAC_OF_WALKING for the
    # 210-tour measurement behind the number.
    #
    # Without C3 the rubric BLESSES a starved tour, which it did on a 2-stop/9.4-min run.
    duration_min = script.inputs.duration_min
    walk_s = route.total_walk_seconds
    if walk_s > 0:
        floor_s = c3_audio_floor_seconds(walk_s)
        report.stats["audio_floor_min"] = round(floor_s / 60, 1)
        if script.total_audio_seconds < floor_s:
            report.findings.append(
                Finding(
                    check="C3-thin",
                    severity=Severity.BLOCKER,
                    message=(
                        f"{script.total_audio_seconds / 60:.1f} min of audio across "
                        f"{walk_s / 60:.0f} min of walking (floor "
                        f"{floor_s / 60:.1f} min) — the tourist walks in silence"
                    ),
                )
            )

    # C7: time-budget overrun, walk plus stationary listening against the engine's total.
    # INHERITED ceiling, nothing new computed. ``Route.err_short_total_seconds`` is a
    # POLICY-STAMPED field: ``summarise_route`` writes the planning budget's own
    # ``nominal_elapsed_seconds`` onto it, so this reads the ceiling the route was
    # actually planned to straight off ``route``. (The field name says err-short; it
    # holds the policy's nominal elapsed seconds.) Skipped when the Route carries no total
    # (the field's own unset sentinel, 0 — e.g. a Route built directly in a test).
    #
    # AUDIO SPOKEN WHILE WALKING IS FREE: narration heard while walking is part of
    # the tour experience and does not cost the tourist extra minutes. That is a
    # statement about what the product IS, and it needs no arithmetic.
    #
    # Adding ALL audio to ALL walking models a tourist who walks in silence and then
    # stands in silence to listen — which is not what the product is. Under that
    # model, filling a walk with narration APPEARS to consume the time budget even
    # though it costs the tourist no extra minutes, so leg content could never be
    # scored fairly however much of it was added.
    #
    # The correct model has two kinds of listening:
    #   * STATIONARY — the tourist stands at a stop and listens. This ADDS to
    #     elapsed time and is what C7 must count.
    #   * CONCURRENT — the tourist listens WHILE walking a leg. This costs no
    #     elapsed time at all; it is bounded by the leg's own duration (C7b below).
    #
    # Concurrent audio is identified from what the engine already records, with no
    # new field: navigation glue and reflections are spoken on the move by
    # construction (reflection.py places a reflection on a LONG WALKING LEG), and a
    # vignette is a walk-past one-liner (generation._vignette_one_liners). Anchor
    # beat sentences at a stop are stationary.
    concurrent_s = _concurrent_audio_seconds(script, beat_sequence)
    stationary_s = max(0, script.total_audio_seconds - concurrent_s)
    report.stats["audio_concurrent_s"] = concurrent_s
    report.stats["audio_stationary_s"] = stationary_s

    if route.err_short_total_seconds > 0:
        # C7 counts dwell, not only listening: a visitor can stand twenty minutes at
        # Concorde for four minutes of audio. It uses the planner's final-gate terms,
        # the route's declared walk total plus served dwell, so the rubric and the
        # planner agree on the same tour, constructed or built by `summarise_route`.
        # Stationary audio sits inside dwell and concurrent audio inside the walk, so
        # the audio stats above are reported for C7b and do not decide C7.
        actual_total_s = served_elapsed_seconds(
            route.total_walk_seconds,
            sum(sp.dwell_seconds for sp in script.selected_pois),
        )
        report.stats["time_budget_actual_s"] = actual_total_s
        report.stats["time_budget_ceiling_s"] = route.err_short_total_seconds
        # The ceiling percentage comes from the route, never from a planning constant,
        # so the number a reader is shown is the one the check measured against.
        ceiling_note = (
            f" ({route.err_short_total_seconds / (duration_min * 60):.0%} of the "
            f"requested duration)"
        )
        if actual_total_s > route.err_short_total_seconds:
            report.findings.append(
                Finding(
                    check="C7-time-budget",
                    severity=Severity.BLOCKER,
                    message=(
                        f"{actual_total_s}s of walking+standing exceeds the "
                        f"{route.err_short_total_seconds}s err-short budget "
                        f"({route.total_walk_seconds}s walk + "
                        f"{stationary_s}s stationary listening; {concurrent_s}s more is "
                        f"spoken while walking and costs no time) — over the engine's own "
                        f"err-short planning ceiling{ceiling_note}, not necessarily over "
                        f"the requested duration itself"
                    ),
                )
            )

    # C7b: leg audio may not outrun the leg.
    # Concurrent audio is free only while the tourist is still walking. Narration
    # that outlasts its leg either cuts off on arrival or holds the tourist on the
    # pavement — so the free-airtime budget is the walk itself.
    #
    # On a route ``summarise_route`` built, both sides of the min equal
    # ``routing.total_walk_seconds``. On a constructed route they differ: the timebox
    # repair's trials and the scoring harnesses set ``total_walk_seconds`` directly
    # while passing ``transits=()``. C7b is a BLOCKER about whether narration fits a
    # walk, so a declared total must never outrank the legs actually present.
    leg_seconds_total = total_walk_seconds(route.transits)
    total_leg_walk_s = (
        min(leg_seconds_total, route.total_walk_seconds)
        if route.total_walk_seconds > 0
        else leg_seconds_total
    )
    report.stats["leg_walk_capacity_s"] = total_leg_walk_s
    if concurrent_s > total_leg_walk_s > 0:
        report.findings.append(
            Finding(
                check="C7b-leg-audio-overruns-walk",
                severity=Severity.BLOCKER,
                message=(
                    f"{concurrent_s}s of walk-concurrent narration exceeds the "
                    f"{total_leg_walk_s}s the tourist actually spends walking — leg "
                    f"content would cut off on arrival or hold them on the pavement"
                ),
            )
        )

    # C6: no empty stop.
    # DRIVEN FROM THE STOP ROSTER, not from the rendered sentences. Iterating
    # ``words_by_stop`` made this check UNREACHABLE for the exact condition it
    # names: a stop that rendered NO Sentence at all has no key in that dict, so
    # the ``words == 0`` branch could only ever fire on a Sentence whose text was
    # literally empty. A tour routing the walker to a stop where they hear nothing
    # scored PASS and was served. C1 does not cover it either (it skips POIs with
    # fewer than STARVE_MIN_BEATS corpus beats).
    #
    # ALIGNMENT ASSUMPTION (shared with C1/C8 below, and stated here rather than
    # left implicit): ``stop_idx`` indexes ``script.selected_pois`` POSITIONALLY.
    # That holds because both the roster (generation._script_pois, built by walking
    # ``route.pois`` in order) and every Sentence's ``stop_idx`` are derived from
    # the same ordered Route. It is NOT keyed on beat_sequence.poi_beats, which is a
    # lookup BY POI ID inside that walk and may legitimately not cover every POI.
    #
    # The pinned-endpoint sentinel (``END_B_SENTINEL_PREFIX``) is skipped: it is a
    # materialised destination with no POI of its own and is content-free BY
    # DESIGN, so silence there is not a defect.
    #
    # A roster entry with NO SEATED PLAN (``beat_sequence.poi_beats`` has no entry
    # for it, so ``ScriptPOI.beat_ids`` is empty) is deliberately NOT skipped. It is
    # tempting to treat it as "nothing was seated, so nothing could be said", but
    # that is the WORST instance of this very defect: the tourist is still routed
    # there and still hears nothing. It also carries no false-positive risk, because
    # such a stop normally still receives its arrival/navigation glue line (attached
    # at ITS stop_idx by ``generation._build_transit``) and so never reaches zero
    # words. Only a genuinely silent stop fires.
    for stop_idx, poi in enumerate(script.selected_pois):
        if poi.id.startswith(END_B_SENTINEL_PREFIX):
            continue
        if words_by_stop.get(stop_idx, 0) == 0:
            report.findings.append(
                Finding(
                    check="C6-empty-stop",
                    severity=Severity.BLOCKER,
                    message="stop rendered zero words",
                    stop_idx=stop_idx,
                    poi_name=poi.name,
                )
            )

    # C1 starvation and C8 gorging, per anchor.
    # ScriptPOI carries the POI roster in stop order; stop_idx indexes it.
    for stop_idx, poi in enumerate(script.selected_pois):
        words = words_by_stop.get(stop_idx, 0)
        available = len(beats_by_poi.get(poi.id, ()))

        if available >= STARVE_MIN_BEATS:
            # The floor is CAPPED so C1 and C8 cannot demand contradictory things. A
            # linear floor is unsatisfiable for a beat-rich POI: Notre-Dame has 59
            # corpus beats, so 59*12 = 708 words, against a 750-word gorge cap — a
            # 42-word window, and govern_poi_beats cuts on WHOLE beats so the render
            # jumps 594 -> 709 and can land outside it. Starvation means "a rich POI
            # was reduced to a line", NOT "the stop failed to exhaust the corpus": a
            # 59-beat POI legitimately renders a SELECTION. Capping the floor at half
            # the gorge limit keeps the check meaningful where it matters (12 beats ->
            # 144-word floor still catches Sainte-Chapelle's 9 words) while never
            # contradicting C8.
            floor = min(
                STARVE_MIN_WORDS_PER_BEAT * available,
                GORGE_MAX_WORDS_PER_STOP * STARVE_FLOOR_MAX_SHARE_OF_CAP,
            )
            if words < floor:
                report.findings.append(
                    Finding(
                        check="C1-starved",
                        severity=Severity.BLOCKER,
                        message=(
                            f"{words} words rendered for a POI with {available} corpus "
                            f"beats (floor {floor:.0f}); the material exists and was not used"
                        ),
                        stop_idx=stop_idx,
                        poi_name=poi.name,
                        context={"floor": floor},
                    )
                )

        if words > GORGE_MAX_WORDS_PER_STOP:
            report.findings.append(
                Finding(
                    check="C8-gorged",
                    severity=Severity.BLOCKER,
                    message=(
                        f"{words} words in one stop (cap {GORGE_MAX_WORDS_PER_STOP}, "
                        f"~{words / SPOKEN_WPM:.1f} min of unbroken narration)"
                    ),
                    stop_idx=stop_idx,
                    poi_name=poi.name,
                )
            )

    # C2: tier inversion, a tier-5 POI reduced to a walk-past one-liner.
    anchor_ids = {p.id for p in script.selected_pois}
    vignette_pois = [v for group in route.vignettes.values() for v in group]
    anchor_tiers = [p.tier for p in script.selected_pois]
    lowest_anchor_tier = min(anchor_tiers) if anchor_tiers else 5
    for vig in vignette_pois:
        if vig.id in anchor_ids:
            continue
        available = len(beats_by_poi.get(vig.id, ()))
        if vig.tier > lowest_anchor_tier and available >= STARVE_MIN_BEATS:
            report.findings.append(
                Finding(
                    check="C2-tier-inversion",
                    severity=Severity.BLOCKER,
                    message=(
                        f"tier-{vig.tier} POI with {available} beats is a walk-past "
                        f"vignette while a tier-{lowest_anchor_tier} POI is a full stop"
                    ),
                    poi_name=vig.name,
                )
            )

    # C4: stop balance.
    if total_words and words_by_stop:
        top_idx, top_words = max(words_by_stop.items(), key=lambda kv: kv[1])
        share = top_words / total_words
        if share > BALANCE_MAX_SHARE and len(words_by_stop) > 1:
            report.findings.append(
                Finding(
                    check="C4-imbalance",
                    severity=Severity.WARN,
                    message=f"one stop holds {share:.0%} of the tour's words",
                    stop_idx=top_idx,
                )
            )

    # C12: two adjacent anchors close enough to deserve a human glance.
    # WARN, not BLOCKER — see MIN_STOP_SEPARATION_M's provenance comment above.
    # Consecutive anchors only: a tour legitimately revisits an area. Distance ALONE
    # cannot tell "the same place told twice" (17 m Palais de Justice/Conciergerie,
    # a real defect) apart from "two neighbouring, genuinely distinct landmarks"
    # (8.4 m Hôtel Le Meurice/Angelina — consecutive stops in the gold text).
    # A BLOCKER here refuses to serve the gold text's own tour, which is
    # worse than the defect this check exists to catch. It stays WARN so an editor
    # sees a short gap without the tour being refused; what actually distinguishes
    # the two cases is semantic (the same fact repeated anywhere in the tour), not
    # geometric.
    pois = list(script.selected_pois)
    for i in range(len(pois) - 1):
        a, b = pois[i], pois[i + 1]
        metres = haversine_m(a.lat, a.lng, b.lat, b.lng)
        if metres < MIN_STOP_SEPARATION_M:
            report.findings.append(
                Finding(
                    check="C12-stops-too-close",
                    severity=Severity.WARN,
                    message=(
                        f"{metres:.0f} m from the previous stop ({a.name}) — close enough "
                        f"that an editor should confirm these are two distinct stops, not "
                        f"one place told twice"
                    ),
                    stop_idx=i + 1,
                    poi_name=b.name,
                )
            )

    # C5: verbatim repetition.
    seen: dict[str, int] = {}
    for sentence in script.script:
        key = " ".join(sentence.text.lower().split())
        if len(key.split()) < 5:  # short glue lines legitimately recur
            continue
        if key in seen:
            report.findings.append(
                Finding(
                    check="C5-verbatim-repeat",
                    severity=Severity.BLOCKER,
                    message=f"sentence repeated (also at stop {seen[key]}): {sentence.text[:70]}…",
                    stop_idx=sentence.stop_idx,
                )
            )
        else:
            seen[key] = sentence.stop_idx

    # C9/C11: narration-quality reuse, per stop.
    # REUSE narration_quality.score_narration — the rubric reads those metrics
    # rather than computing its own. No new lexical logic is added below; only
    # score_narration's own numbers are read.
    stop_year_densities: dict[int, float] = {}
    for stop_idx, texts in sorted(texts_by_stop.items()):
        stop_text = " ".join(texts)
        if not stop_text.strip():
            continue  # C6 already reports the empty stop; nothing to score here
        nq = score_narration(stop_text)

        # C9: sentence length for the ear. The cap is a JUDGEMENT anchored on the
        # measured 19.11 of the hand-written gold text that MAX_SENTENCE_WORDS
        # describes; the gold fails the 10-15 museum-copy range, so that range is not
        # the anchor. WARN — a long
        # mean is a craft smell surfaced to the editor, not grounds to refuse serving.
        if nq.mean_sentence_words > MAX_SENTENCE_WORDS:
            report.findings.append(
                Finding(
                    check="C9-long-sentences",
                    severity=Severity.WARN,
                    message=(
                        f"mean sentence length {nq.mean_sentence_words:.1f} words "
                        f"(cap {MAX_SENTENCE_WORDS:.0f})"
                    ),
                    stop_idx=stop_idx,
                )
            )

        # Opener quality is not checked. Whether a stop opens by orienting the
        # listener is a meaning question; a word list of sentence-initial verbs
        # cannot answer it, and no semantic opener check exists.
        # test_openers_are_unchecked_since_c10_was_deleted pins the gap.

        stop_year_densities[stop_idx] = nq.per_100w["year_density"]

    # C11: date density for the ear. WARN, RELATIVE to the tour's own mean — see
    # OUTLIER_YEAR_DENSITY_MULTIPLE for why there is no absolute cut (no measured or
    # cited one exists). Always reports the per-stop densities either way.
    if len(stop_year_densities) > 1:
        report.stats["year_density_by_stop"] = dict(sorted(stop_year_densities.items()))
        mean_density = sum(stop_year_densities.values()) / len(stop_year_densities)
        if mean_density > 0:
            # C11 IS REPORT-ONLY. It records the outliers as a stat and emits NO
            # Finding. Decided on measurement, not taste: C11 fired on
            # 100% of the human-vetted reference tours (5 of 25 Place des Vosges
            # positions, 6 of 29 Ile) and on only 8-11% of machine tours. A check
            # that accuses the hand-written reference tours,
            # while passing the output it is meant to police, is pointing the wrong
            # way — and the multiple needed to admit the references (3.82 PdV /
            # 4.19 Ile) sits ABOVE the machine maximum ratio of 3.11, so no
            # threshold exists that clears the humans and still bites. Deleting it
            # would throw away a real signal; emitting it as a WARN spends reader
            # attention on noise. So the densities stay visible in
            # `stats["year_density_outliers"]` for anyone diagnosing a date-list
            # stop, and nothing is blocked or warned. Pinned by
            # test_c11_reports_a_stat_but_emits_no_finding.
            outlier_threshold = mean_density * OUTLIER_YEAR_DENSITY_MULTIPLE
            report.stats["year_density_outliers"] = {
                stop_idx: round(density, 1)
                for stop_idx, density in sorted(stop_year_densities.items())
                if density > outlier_threshold
            }

    # C13: point first (warn).
    # Two things a $0 check can see of "the point lands in the first minute": the
    # STORY starts early (the glue before the stop's first beat sentence — the recap,
    # the walking line — stays under POINT_FIRST_PREAMBLE_MAX_WORDS), and the stop's
    # PRIMARY beat (ScriptPOI.beat_ids[0], the first of its capped plan) is cited
    # inside the first POINT_FIRST_STORY_WORDS of the story. WARN until its pass rate
    # is measured on real days (BLOCKER then, never before).
    # Stops with no story — a rest, the pinned end sentinel — have no point to land.
    sentences_by_stop: dict[int, list[Sentence]] = {}
    for sentence in script.script:
        sentences_by_stop.setdefault(sentence.stop_idx, []).append(sentence)
    for stop_idx, poi in enumerate(script.selected_pois):
        if poi.id.startswith(END_B_SENTINEL_PREFIX) or not poi.beat_ids:
            continue
        sents = sentences_by_stop.get(stop_idx, [])
        if not any(s.source_type == "beat" for s in sents):
            continue  # C6's case, or a stop whose story never rendered — not C13's
        primary = poi.beat_ids[0]
        preamble = 0
        for s in sents:
            if s.source_type == "beat":
                break
            preamble += _word_count(s.text)
        if preamble > POINT_FIRST_PREAMBLE_MAX_WORDS:
            report.findings.append(
                Finding(
                    check="C13-point-late",
                    severity=Severity.WARN,
                    message=(
                        f"{preamble} words of glue (a recap or a walking line) before the "
                        f"story starts (cap {POINT_FIRST_PREAMBLE_MAX_WORDS}) — the point "
                        "cannot land in the first minute if the stop opens elsewhere"
                    ),
                    stop_idx=stop_idx,
                    poi_name=poi.name,
                    context={"preamble_words": preamble},
                )
            )
        story_words = 0
        landed: int | None = None
        for s in sents:
            if s.source_type != "beat":
                continue
            if primary in s.cited_beat_ids:
                landed = story_words
                break
            story_words += _word_count(s.text)
        if landed is None or landed > POINT_FIRST_STORY_WORDS:
            where = "never" if landed is None else f"{landed} words in"
            report.findings.append(
                Finding(
                    check="C13-point-late",
                    severity=Severity.WARN,
                    message=(
                        f"the stop's primary beat is first cited {where} (cap "
                        f"{POINT_FIRST_STORY_WORDS} words of story) — the point the capped "
                        "plan leads with is not in the first minute"
                    ),
                    stop_idx=stop_idx,
                    poi_name=poi.name,
                    context={"primary_first_cited_words": landed},
                )
            )

    # Spatial claim geometry is not part of this floor.
    # No check here tests whether a directional cue is geometrically true. A
    # spatial-claim checker was built, measured on real data, and removed.
    #
    # The hole it targets is real: SemanticFactChecker entails PROPOSITIONS against
    # source facts and never sees the directional instruction wrapped around one
    # ("look down X toward Y"), so a cue can be geometrically false and still pass
    # fact-check 0-unsupported. But that checker emitted FALSE ACCUSATIONS on
    # real shipped data, reproduced end-to-end against
    # unmodified `data/new_york/poi-raw.json` with its extractor:
    #
    #     "Look down Wall Street toward Trinity Church."  ->  IMPLAUSIBLE
    #
    # That cue is correct. Trinity Church stands at the head of Wall Street and is
    # its single most recognisable sightline; the check rejected it because the
    # church bears 330 deg while the street axis (derived by matching POIs whose
    # names merely END WITH "Wall Street") read 47 deg off.
    #
    # A missed cue costs nothing. A false accusation actively degrades the tour --
    # it tells an editor a true sentence is wrong, and concrete look-cues are a
    # quality a good tour has, so such a check would fight the very property we
    # are trying to increase. A spatial check needs ~100% precision on real data
    # before it is wired at ANY severity, including WARN.
    #
    # Its street-axis resolution succeeded for only 2 of 13 addressed NYC
    # candidates (one of those spurious), and `data/{city}/poi-raw.json` is not
    # shipped in the production image, so a check reading it degrades to
    # all-UNKNOWN in prod regardless.

    return report


# compose_fixable: is a BLOCKER worth another compose pass?
#
# Reading BLOCKER as "a BLOCKER regenerates the script" treats it as one
# predicate. It is two: (a) may this tour be SERVED (score_tour's
# ``passed``, unchanged, never touched by anything below), and (b) is looping the
# COMPOSE step for one stop worth the money. This module answers (b). A defect
# that is upstream of compose (selection picked the wrong POI, chose a bad route,
# or the audio-seconds formula structurally cannot move) will reproduce IDENTICALLY
# on a recompose — that is spend with zero chance of convergence, and for C1 it is
# actively worse than doing nothing (an under-seated stop pushed to "write more"
# invites fabrication that the entailment gate then rejects, floors to stitch, and
# ends up terser than before). ``compose_fixable`` exists so the caller can spend
# only where a recompose can plausibly change the outcome.


@dataclass(frozen=True)
class StopMaterial:
    """What the composer actually had to work with for ONE stop."""

    seated_body_words: int  # words in the beats SEATED for this stop
    composed_words: int  # words the composer actually rendered


def compose_fixable(finding: Finding, material: StopMaterial | None) -> bool:
    """True => a targeted re-compose of this stop could plausibly fix it.
    False => the defect is upstream (selection/route); recomposing burns
    money and cannot converge.

    This is a SEPARATE predicate from "is the tour servable" (``RubricReport.passed``
    / a finding's ``severity``). A BLOCKER always means do-not-serve; this function
    answers the different question of whether looping compose for the stop is worth
    the spend. The module-level comment above says why these must not be conflated.

    Fails CLOSED throughout: any severity other than BLOCKER, any unrecognised check
    id, or a check missing the structured data it needs (``material`` and, for C1,
    ``finding.context["floor"]``) all return False. This function never guesses.
    """
    if finding.severity is not Severity.BLOCKER:
        # Never loop on a WARN — nothing here is worth another compose pass over a
        # finding that only ever surfaces to the editor.
        return False

    check = finding.check

    if check == "C6-empty-stop":
        # The model emitted nothing for this stop. Always a compose-side failure —
        # there is no selection/route explanation for zero output.
        return True

    if check == "C5-verbatim-repeat":
        # Same-STOP exact dupes already die inside compose, in _dedup_composed
        # (compose.py:1061, via suppress_exact_repeats) before a script is ever
        # scored. A survivor reaching the rubric is therefore a CROSS-stop repeat —
        # only a recompose of the offending stop(s) removes it.
        return True

    if check == "C8-gorged":
        # Fixable ONLY if the COMPOSER is the one who inflated the stop past what
        # selection actually seated for it (composed_words > seated_body_words).
        # If composed_words <= seated_body_words, the overshoot is selection's
        # doing: beat_select.py:260-288 (govern_poi_beats) always keeps the first
        # seated beat even when it alone blows the per-stop allowance (the FIRST
        # beat is always kept, a bounded one-beat overshoot). Trimming that in
        # compose drops a claim, the coverage gate flags it,
        # repair_composed_surgical (compose_gate.py:125, spliced back in
        # compose.py's serve path around line 1170) puts the stitch back verbatim,
        # and the stop gorges again on the next attempt — a PROVEN oscillation.
        # Never loop-eligible in that branch.
        return material is not None and material.composed_words > material.seated_body_words

    if check == "C1-starved":
        # Mirror of C8. Fixable ONLY if what was SEATED for this stop already met
        # the SAME per-POI floor score_tour computed when it raised this finding
        # (carried in finding.context["floor"] — compose_fixable's signature is
        # fixed to (finding, material), so that per-POI number, which depends on
        # the corpus's full beat count for the POI, has no other channel here).
        # seated_body_words >= floor means the COMPOSER under-used material it
        # actually had — a recompose can plausibly do better with no new risk.
        # If seated_body_words < floor, the rest of the POI's beats are sitting in
        # overflow_by_poi, never seated for this stop; telling compose to "write
        # more" invites fabrication, the entailment gate rejects it, and the stop
        # floors to stitch — strictly WORSE than the terse original. Fails closed
        # (False) if the floor was never recorded, rather than guessing.
        if material is None or finding.context is None:
            return False
        floor = finding.context.get("floor")
        if floor is None:
            return False
        return material.seated_body_words >= floor

    if check == "C3-thin":
        # PROVEN unfixable, for the BODY of the stop. generation.py:1203-1207 computes
        # the beat-derived share of total_audio_seconds from SEATED beats scaled by the
        # voiced-word fraction, min(1.0, voiced/body) — more composed BODY words can
        # only push that ratio TOWARD 1.0, never past it, so a recompose cannot close a
        # C3 gap by writing more of the beat text. This is NOT literally zero extra
        # audio, though: generation.py:1208 adds ``glue_count * 4`` seconds on top of
        # the beat total — more glue sentences (navigation/transition lines) DO add a
        # few real seconds, uncapped by the voiced/body ratio. That is also a
        # reward-hacking vector worth naming: a composer under pressure to hit a C3
        # floor could pad glue count rather than write more substance, and this check
        # would not catch it. The conclusion still holds — a targeted stop recompose
        # cannot close a C3 gap by any amount that matters — so C3 stays BLOCKER for
        # the SERVING verdict; it is merely loop-INELIGIBLE. These are different
        # predicates — see the module-level comment above.
        return False

    if check in ("C2-tier-inversion", "C12-stops-too-close"):
        # Route/selection defects, kept fail-closed here regardless of severity. C2
        # needs a different tiering/selection decision. C12 is WARN (see
        # MIN_STOP_SEPARATION_M's provenance comment — distance alone cannot tell a
        # duplicate from a genuinely close, distinct landmark), so the severity guard
        # above already returns False for it in practice; even if a future caller
        # somehow raised a C12 finding at BLOCKER, fixing it needs a stop dropped or
        # merged. Neither is something a stop-level recompose can do — the POIs and
        # their positions don't change.
        return False

    if check == "C7-time-budget":
        # Selection/budget: the walk+listening total is a routing/selection
        # quantity. Recomposing a stop's TEXT does not change
        # route.total_walk_seconds, and (per C3, generation.py:1207) it barely
        # moves total_audio_seconds either — no recompose closes an over-budget gap.
        return False

    # Unknown check id: fail CLOSED. Never spend money looping on something this
    # classifier has no positive evidence about.
    return False


__all__ = [
    "Finding",
    "RubricReport",
    "Severity",
    "StopMaterial",
    "compose_fixable",
    "score_tour",
]
