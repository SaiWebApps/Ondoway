"""Markdown renderer for generated tours.

Reproduces the user-readable format of the empirical walks at
``fixtures/reference-tours/01-place-des-vosges.md`` and
``02-ile-de-la-cite-notre-dame.md``: header → per-stop sections →
sentence-per-line with bold ``[SOURCE]`` attributions → footer with
validation summary + "what to look for" hints.

Pure function on a Script; no Neo4j, no LLM. Used by the
``scripts/tour_build.py`` harness and the ``/tour-build`` skill.
"""

from __future__ import annotations

from collections.abc import Callable

from .contract import Anchor, BeatSequence, Script, Sentence
from .density import (
    EMPIRICAL_FILL_BAR,
    GREEN_ANCHOR_CANDIDATES_MIN,
    GREEN_CLUSTER_COMPACTNESS_MAX,
    GREEN_FILL_RATIO_MIN,
)
from .generation import is_walk_concurrent


def render_markdown(
    script: Script,
    *,
    cost_usd: float | None = None,
    haiku_input_tokens: int | None = None,
    haiku_output_tokens: int | None = None,
    wall_clock_seconds: float | None = None,
    beat_sequence: BeatSequence | None = None,
    tourability=None,
) -> str:
    """Render a Script + telemetry into the empirical-walks markdown format.

    The optional ``tourability`` arg: when a YELLOW
    assessment is passed, a warning banner is prepended at the top so
    the user sees the thin-tour caveat before the script.
    """
    sub_lookup = _build_sub_lookup(beat_sequence)

    out: list[str] = []
    out.append(
        _header(script, cost_usd, haiku_input_tokens, haiku_output_tokens, wall_clock_seconds)
    )
    if tourability is not None and tourability.status == "YELLOW":
        out.append("")
        out.append(_yellow_banner(tourability, script.inputs.lenses))
    out.append("")
    out.append("---")
    out.append("")

    grouped = _group_by_stop(script.script)
    for stop_idx, sentences in grouped.items():
        if stop_idx >= len(script.selected_pois):
            heading = "## CLOSE"
        else:
            poi = script.selected_pois[stop_idx]
            heading = f"## STOP {stop_idx + 1} — {poi.name}"
        out.append(heading)
        out.append("")
        out.extend(_render_stop_block(sentences, sub_lookup))
        out.append("")

    out.append("---")
    out.append("")
    out.append(_footer(script, sub_lookup))
    return "\n".join(out).rstrip() + "\n"


def _header(
    script: Script,
    cost_usd: float | None,
    haiku_in: int | None,
    haiku_out: int | None,
    wall_clock_s: float | None,
) -> str:
    inp = script.inputs
    lat, lng = inp.start
    parts = [
        f"# Generated Tour — {inp.start_label or 'unnamed start'}",
        "",
        f"**Generated at:** {script.generated_at}",
        f"**City:** {inp.city_slug}",
        f"**Start:** ({lat:.5f}, {lng:.5f}){' — ' + inp.start_label if inp.start_label else ''}",
        f"**Duration target:** {inp.duration_min} min · **Round-trip:** {inp.round_trip}",
    ]
    if inp.lenses:
        parts.append(f"**Lens bias:** {', '.join(inp.lenses)}")
    if inp.theme_hint:
        parts.append(f"**Theme hint:** {inp.theme_hint}")
    parts.extend(
        [
            "",
            f"**Total audio time:** {script.total_audio_seconds // 60} min "
            f"({script.total_audio_seconds} s)",
            f"**Total walk time:** {script.total_walking_seconds // 60} min "
            f"({script.total_walking_seconds} s)",
            f"**Total walk distance:** {script.total_walk_distance_m} m",
            f"**Planned total:** {script.total_planned_seconds // 60} min "
            f"(err-short of stated {inp.duration_min} min)",
        ]
    )
    if cost_usd is not None:
        parts.append(f"**Haiku cost (this tour):** ${cost_usd:.5f}")
    if haiku_in is not None and haiku_out is not None:
        parts.append(f"**Haiku tokens:** {haiku_in} in + {haiku_out} out")
    if wall_clock_s is not None:
        parts.append(f"**Wall-clock:** {wall_clock_s:.1f} s")
    parts.append("")
    parts.append("**Selected POIs (in walk order):**")
    parts.append("")
    for i, poi in enumerate(script.selected_pois, 1):
        parts.append(
            f"  {i}. {poi.name} - tier {poi.tier} "
            f"- {len(poi.beat_ids)} beats - area={poi.area or '-'}"
        )
    return "\n".join(parts)


def _group_by_stop(sentences: tuple[Sentence, ...]) -> dict[int, list[Sentence]]:
    groups: dict[int, list[Sentence]] = {}
    for s in sentences:
        groups.setdefault(s.stop_idx, []).append(s)
    return groups


def _vignette_beat_ids(script: Script) -> frozenset[str]:
    """The beat ids a script voices that no stop SEATS — the walk-past vignettes,
    heard on the leg. Derived the way the blueprint's playback freeze tells them
    apart (`artifact.derive_playback_assignments`: seated ids come off
    ``selected_pois[].beat_ids``); never a second rule."""
    seated = {beat_id for poi in script.selected_pois for beat_id in poi.beat_ids}
    return frozenset(
        s.source_id for s in script.script if s.source_type == "beat" and s.source_id not in seated
    )


def _partition_by_stop(script: Script) -> dict[int, tuple[list[Sentence], list[Sentence]]]:
    """Per stop: (the LEG sentences — spoken on the walk INTO the stop; the STOP
    sentences — spoken standing there), split by THE one leg-vs-stop rule
    (`generation.is_walk_concurrent` — the same rule the workbench's leg cards and the
    blueprint's playback assignments read), in order."""
    vignettes = _vignette_beat_ids(script)
    out: dict[int, tuple[list[Sentence], list[Sentence]]] = {}
    for sentence in script.script:
        legs, stops = out.setdefault(sentence.stop_idx, ([], []))
        (legs if is_walk_concurrent(sentence, vignettes) else stops).append(sentence)
    return out


AnchorOf = Callable[[Sentence], "Anchor | None"]


def _is_close(sentence: Sentence) -> bool:
    """The stop's GOODBYE — the one line `stop_close_text` travels beside."""
    return sentence.source_id == "GLUE_CLOSING" and sentence.source_type == "glue"


def _anchored_stops(script: Script, anchor_of: AnchorOf | None) -> set[int]:
    """The stop indices whose telling is CUT INTO CHAPTERS on this day."""
    if anchor_of is None:
        return set()
    return {s.stop_idx for s in script.script if anchor_of(s) is not None}


def stop_narration_text(script: Script, *, anchor_of: AnchorOf | None = None) -> dict[int, str]:
    """Per-stop spoken STORY: each stop's stationary sentences joined in order —
    the cold-open, the beat sentences and (unless the stop is chaptered) the close;
    NOT the walking line into it, and NOT the chapters cut out to a reviewed anchor.

    The text that becomes the stop's own audio file, placed at the footprint. The leg is
    not in it: the piece used to OPEN with the walk the person had already walked. The
    leg's sentences are `stop_leg_text`. ``anchor_of`` names
    the reviewed anchor a sentence is told AT, if any — built by THE placement rule
    (`placement.anchor_of`) — and such a sentence belongs to `stop_segment_text`, never
    here.

    A CHAPTERED stop's story does not carry its GOODBYE. The close used to end the story
    piece, so a marquee said farewell on arrival and then spoke again at the chapter. The
    goodbye is the last thing said at the last place, or it is not said.
    The close is already its own hash-guarded artifact (`stop_close_text`), and the phone
    plays it when the last chapter has been told. A stop with NO chapters is untouched.

    Pure: no DB, no audio. A stop left with no story sentence is absent; empty → ``{}``.
    """
    chaptered = _anchored_stops(script, anchor_of)
    out: dict[int, str] = {}
    for idx, (_legs, stops) in _partition_by_stop(script).items():
        text = " ".join(
            s.text
            for s in stops
            if (anchor_of is None or anchor_of(s) is None)
            and not (idx in chaptered and _is_close(s))
        )
        if text:
            out[idx] = text
    return out


def stop_segment_text(
    script: Script, anchor_of: AnchorOf
) -> dict[int, tuple[tuple[Anchor, str], ...]]:
    """Per stop, the CHAPTERS: one entry per reviewed anchor a sentence is told at, in
    first-appearance order, each the anchor and its sentences joined in SCRIPT order.
    A stop with no anchored sentence is absent — its story stays whole.

    Every sentence of the stop is considered, not only the stationary ones: an anchored
    sentence belongs AT its anchor wherever the leg-vs-stop rule would otherwise put it,
    so the walk this reads and the leg `stop_leg_text` builds partition the stop's
    sentences between them with nothing lost and nothing said twice."""
    out: dict[int, dict[str, tuple[Anchor, list[str]]]] = {}
    for sentence in script.script:
        anchor = anchor_of(sentence)
        if anchor is None:
            continue
        chapters = out.setdefault(sentence.stop_idx, {})
        chapters.setdefault(anchor.label, (anchor, []))[1].append(sentence.text)
    return {
        idx: tuple((anchor, " ".join(texts)) for anchor, texts in chapters.values())
        for idx, chapters in out.items()
    }


def stop_leg_text(script: Script, *, anchor_of: AnchorOf | None = None) -> dict[int, str]:
    """Per-stop spoken LEG: the walk-concurrent sentences INTO the stop — its nav line,
    a reflection placed on that leg, the walk-past one-liners — joined in order: the
    stop's LEG piece, voiced as its own file and played on the leg.
    A stop whose leg carries nothing is absent (silence, never an empty piece).

    ``anchor_of``: a sentence belonging to a reviewed anchor is told AT that anchor and
    never on the walk toward it. On the leg it points at something the walker cannot see
    yet, and it spends the reveal early. Such a sentence rides `stop_segment_text`
    instead."""
    return {
        idx: text
        for idx, (legs, _stops) in _partition_by_stop(script).items()
        if (
            text := " ".join(
                s.text for s in legs if anchor_of is None or anchor_of(s) is None
            )
        )
    }


def stop_close_text(script: Script) -> dict[int, str]:
    """Per-stop CLOSE: the text of the stop's GLUE_CLOSING sentence — the
    one line [Head back now] plays and shows for THIS stretch, travelling
    beside the narration it ends (`stop_narration_text`) rather than being parsed out of
    it. A stop with no close (a legacy script) is absent from the map."""
    out: dict[int, str] = {}
    for sentence in script.script:
        if sentence.source_id == "GLUE_CLOSING" and sentence.source_type == "glue":
            out[sentence.stop_idx] = sentence.text
    return out


def _render_stop_block(
    sentences: list[Sentence],
    sub_lookup: dict[str, tuple[str | None, str | None]],
) -> list[str]:
    """One sentence per line; sentences with the same sub-bucket share a sub-heading.

    The sub-bucket comes from the sub_location or trigger_address of the
    cited beat. Glue sentences attach to the active sub-bucket.
    """
    out: list[str] = []
    current_sub: tuple[str | None, str | None] | None = None

    for sent in sentences:
        sub_key: tuple[str | None, str | None] = (None, None)
        if sent.source_type == "beat":
            sub_key = sub_lookup.get(sent.source_id, (None, None))

        # Sub-headings only emitted for non-empty buckets, when changed,
        # and only for beat-typed sentences (glue inherits the active bucket).
        if sent.source_type == "beat" and sub_key != current_sub and any(sub_key):
            current_sub = sub_key
            label = _format_sub_label(sub_key)
            out.append(f"### {label}")
            out.append("")
        elif sent.source_type == "beat" and sub_key != current_sub and not any(sub_key):
            # Reset to "no sub-bucket" but don't emit a heading.
            current_sub = sub_key

        attribution = _format_attribution(sent)
        out.append(f"{sent.text} **{attribution}**")
        out.append("")
        if sent.source_id == "SYNTHESIZED_OPENER":
            out.append(
                "> **Note:** this tour stop has no canonical "
                "`stop_orientation` beat in the corpus; opener was "
                "synthesized from physical_cues + pronunciation. Quality "
                "gradient."
            )
            out.append("")
    return out


def _format_attribution(sent: Sentence) -> str:
    if sent.source_type == "beat":
        return f"[BEAT:{sent.source_id[:8]}]"
    # glue / arith / synthesized — emit the label verbatim
    return f"[{sent.source_id}]"


def _format_sub_label(sub_key: tuple[str | None, str | None]) -> str:
    sub_loc, trig = sub_key
    if sub_loc and trig:
        return f"{sub_loc} · {trig}"
    return sub_loc or trig or ""


def _build_sub_lookup(
    beat_sequence: BeatSequence | None,
) -> dict[str, tuple[str | None, str | None]]:
    """beat_id → (sub_location, trigger_address) so render can group sentences."""
    out: dict[str, tuple[str | None, str | None]] = {}
    if beat_sequence is None:
        return out
    for plan in beat_sequence.poi_beats:
        for beat in plan.beats:
            out[beat.id] = (beat.sub_location, beat.trigger_address)
    return out


def _yellow_banner(tourability, lenses: list[str] | None = None) -> str:
    """Top-of-tour warning when density.assess returned YELLOW.

    YELLOW fires for several distinct reasons (density.py's
    ``_status`` — low fill, thin anchor pool, or a spread-out cluster with
    otherwise-surplus fill). The banner used to unconditionally print the
    "below the 70-80% audio-fill bar" sentence, which is a lie when fill is
    actually at/above target (e.g. a 100%-fill tour that missed GREEN only
    on anchor count or compactness) — self-contradictory in the field
    (Louvre 45-min round-trip: "100% audio fill" + "ran below ... bar").
    Gate that sentence on the SAME fill threshold density.py's GREEN
    classification uses, and otherwise disclose the true limiting factor.
    """
    fill_pct = round(tourability.fill_ratio * 100)
    lines = [f"> **⚠ THIN TOUR (YELLOW) — {fill_pct}% audio fill.**"]
    if tourability.fill_ratio < EMPIRICAL_FILL_BAR:
        lines.append(
            "> This tour ran below the empirical 70-80% audio-fill bar; expect "
            "long stretches of silence between stops."
        )
    elif tourability.fill_ratio < GREEN_FILL_RATIO_MIN:
        # Above the empirical bar but short of the GREEN full-fill target —
        # claiming "below the 70-80% bar" here would be false (e.g. 99% fill).
        lines.append(
            f"> Audio fills {fill_pct}% of the full-tour target — close, but "
            "expect some quiet stretches between stops."
        )
    else:
        lines.append(f"> {_yellow_non_fill_reason(tourability)}")
    # Lens-specific disclosure: two tourists at the same start see the SAME
    # overall fill, so tell each how much of it matches THEIR interest.
    if lenses and tourability.on_lens_fill_ratio is not None:
        on_lens_pct = round(tourability.on_lens_fill_ratio * 100)
        lens_label = "/".join(lenses)
        lines.append(
            f"> For your interest ({lens_label}), only {on_lens_pct}% of the reachable "
            "audio is on-theme — the rest is other subjects."
        )
    if tourability.max_supportable_duration_min:
        lines.append(
            f"> Consider {tourability.max_supportable_duration_min}-min instead "
            "for richer content at this start point."
        )
    if tourability.round_trip and tourability.one_way_alternative_destination:
        lines.append(
            f"> Or try a one-way ending at {tourability.one_way_alternative_destination!r}."
        )
    return "\n".join(lines)


def _yellow_non_fill_reason(tourability) -> str:
    """Honest reason for a YELLOW tour whose fill is NOT the limiting factor.

    Only called when ``fill_ratio >= GREEN_FILL_RATIO_MIN`` — there's enough
    audio capacity, so the bottleneck that kept the tour out of GREEN is the
    walkable area's layout: too few substantial ("anchor") stops within
    reach, or the ones that exist are spread out rather than clustered.
    Phrased for a tourist as a capacity-vs-layout distinction, not the
    engine's internal thresholds.
    """
    thin_anchors = tourability.anchor_candidate_count < GREEN_ANCHOR_CANDIDATES_MIN
    spread_out = tourability.cluster_compactness > GREEN_CLUSTER_COMPACTNESS_MAX
    stops_phrase = (
        f"only {tourability.anchor_candidate_count} substantial stop"
        f"{'s' if tourability.anchor_candidate_count != 1 else ''} within reach"
    )
    if thin_anchors and spread_out:
        return (
            "This tour has plenty of audio content, but there are "
            f"{stops_phrase} and they're spread out rather than clustered — "
            "expect more walking between stops than usual."
        )
    if thin_anchors:
        return (
            "This tour has plenty of audio content, but there are "
            f"{stops_phrase} at this start point — a capacity issue with "
            "nearby stops, not the audio itself."
        )
    if spread_out:
        return (
            "This tour has plenty of audio content, but the substantial stops "
            "here are spread out across the walk area rather than clustered "
            "together — expect more walking between stops than usual."
        )
    return (
        "This tour has plenty of audio content, but the layout of stops at "
        "this start point falls just short of the usual density bar."
    )


def _footer(
    script: Script,
    sub_lookup: dict[str, tuple[str | None, str | None]],
) -> str:
    rep = script.validation
    lines = [
        "## Validation summary",
        "",
        f"- Untraceable sentences: **{len(rep.untraceable_sentences)}** (gate: 0)",
        f"- Forbidden phrase hits: **{len(rep.forbidden_phrase_hits)}** (gate: 0)",
        f"- Validation passed: **{rep.passed}**",
    ]
    if rep.untraceable_sentences:
        lines.append("")
        lines.append("**Untraceable sentences (audit):**")
        for s in rep.untraceable_sentences[:10]:
            lines.append(f"  - {s.text!r} (source_id={s.source_id})")
    if rep.forbidden_phrase_hits:
        lines.append("")
        lines.append("**Forbidden phrase hits:**")
        for s, label in rep.forbidden_phrase_hits[:10]:
            lines.append(f"  - {label}: {s.text!r}")

    quality = _quality_hints(script, sub_lookup)
    if quality:
        lines.append("")
        lines.append("## What to look for when auditing")
        lines.append("")
        lines.extend(f"- {h}" for h in quality)

    if script.lens_coverage:
        lines.append("")
        lines.append("## Lens coverage")
        lines.append("")
        for lens, count in sorted(script.lens_coverage.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"  - {lens}: {count}")

    return "\n".join(lines)


def _quality_hints(
    script: Script,
    sub_lookup: dict[str, tuple[str | None, str | None]],
) -> list[str]:
    hints: list[str] = []

    # Sparse anchors: tier-5 with <3 beats, tier-4 with <2.
    for poi in script.selected_pois:
        if poi.tier == 5 and len(poi.beat_ids) < 3:
            hints.append(
                f"{poi.name} is tier-5 but the tour landed only "
                f"{len(poi.beat_ids)} beats here — content density is thin."
            )
        elif poi.tier in (3, 4) and len(poi.beat_ids) < 2:
            hints.append(
                f"{poi.name} (tier {poi.tier}) carries only "
                f"{len(poi.beat_ids)} beats in this tour — likely a sparse anchor."
            )

    # SYNTHESIZED_OPENER fired
    if any(s.source_id == "SYNTHESIZED_OPENER" for s in script.script):
        hints.append(
            "Cold open used SYNTHESIZED_OPENER — the first stop has no "
            "canonical `stop_orientation` beat in the corpus. Expect a "
            "flatter cold-open feel than the Pariswalks gold standard."
        )

    # Forbidden / untraceable already surfaced in the validation summary;
    # only mention as an audit hint if non-zero.
    if script.validation.untraceable_sentences:
        hints.append(
            "Untraceable sentences present — generation produced text "
            "that does not trace to a known beat or glue label. This "
            "should be 0 for a launch-ready tour."
        )

    if not script.selected_pois:
        hints.append(
            "No POIs selected — the start point may be outside the "
            "Paris corpus envelope, or the duration is too short to "
            "reach any reachable POI."
        )

    return hints


__all__ = [
    "render_markdown",
    "stop_close_text",
    "stop_leg_text",
    "stop_narration_text",
    "stop_segment_text",
]
