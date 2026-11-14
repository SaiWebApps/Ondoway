"""Adversarial tests for trusted pre-selection place materialization."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest
from pydantic import ValidationError

from src.tour.contract import POI, BeatRef, Route, TourInput
from src.tour.corpus_places import (
    CanonicalPlaceIdentity,
    CoordinateProvenance,
    CorpusBeatPlacePlan,
    CorpusPlaceManifest,
    CorpusPlaceReference,
    CorpusSentencePlaceAssessment,
    MaterializationDecision,
    StableBeatIdentity,
    text_sha256,
)
from src.tour.density import TourabilityRefusedError, assess, assess_snapshot
from src.tour.generation import split_sentences
from src.tour.place_materialization import (
    materialize_corpus_snapshot,
    selection_payload_sha256,
)
from src.tour.selection import (
    CorpusSnapshot,
    build_poi_extra_narration,
    select_route,
    select_vignettes,
)


def _place(
    canonical_id: str,
    *,
    name: str | None = None,
    lat: float = 48.85,
    lng: float = 2.35,
    source_id: str | None = None,
    resolution_state: str = "resolved",
    aliases: tuple[str, ...] = (),
) -> CanonicalPlaceIdentity:
    source_id = source_id or f"source:{canonical_id}"
    provenance = None
    if resolution_state == "resolved":
        provenance = CoordinateProvenance.build(
            authority_kind="corpus_record",
            authority_id="fixture-authority",
            source_record_id=source_id,
            coordinates=(lat, lng),
            source_payload={"fixture": canonical_id},
        )
    return CanonicalPlaceIdentity(
        canonical_place_id=canonical_id,
        display_name=name or canonical_id,
        aliases=tuple(sorted(aliases, key=lambda value: (value.casefold(), value))),
        source_identity_ids=(source_id,),
        resolution_state=resolution_state,
        coordinate_provenance=provenance,
    )


def _poi(
    raw_id: str,
    place: CanonicalPlaceIdentity,
    *,
    name: str | None = None,
    tier: int = 5,
    areas: tuple[str, ...] = ("Test Area",),
    aliases: tuple[str, ...] = (),
) -> POI:
    assert place.coordinates is not None
    return POI(
        id=raw_id,
        canonical_place_id=place.canonical_place_id,
        name=name or place.display_name,
        aliases=aliases,
        coordinate_provenance=place.coordinate_provenance,
        tier=tier,
        poi_role="stop",
        lat=place.coordinates[0],
        lng=place.coordinates[1],
        areas=areas,
    )


def _reference(
    reference_id: str,
    *,
    kind: str = "experiential",
    relation: str | None = "primary",
    canonical_id: str | None = None,
    feature_of: str | None = None,
    resolution_state: str = "resolved",
) -> CorpusPlaceReference:
    return CorpusPlaceReference(
        reference_id=reference_id,
        reference_kind=kind,
        relation=relation,
        canonical_place_id=canonical_id,
        feature_of_canonical_place_id=feature_of,
        resolution_state=resolution_state,
        perceivability_state=(
            "not_applicable"
            if kind == "contextual_historical"
            else "perceivable"
            if resolution_state == "resolved"
            else "unresolved"
        ),
    )


def _beat(
    raw_id: str,
    stable_id: str,
    poi_id: str,
    script: str,
    *,
    plan_id: str | None = None,
    lenses: tuple[str, ...] = (),
) -> BeatRef:
    return BeatRef(
        id=raw_id,
        stable_beat_id=stable_id,
        place_plan_id=plan_id or f"plan:{stable_id}",
        poi_id=poi_id,
        script_body=script,
        source_passage=script,
        source_chunk_slug=f"chunk:{stable_id}",
        active_status="active",
        est_spoken_seconds=60,
        lenses=lenses,
    )


def _plan(
    beat: BeatRef,
    host_canonical_id: str,
    references_by_sentence: tuple[tuple[CorpusPlaceReference, ...], ...],
    *,
    resolution_state: str = "resolved",
    sentence_hashes: tuple[str, ...] | None = None,
    assessment_kind: str | None = None,
) -> CorpusBeatPlacePlan:
    assert beat.stable_beat_id is not None
    sentences = split_sentences(beat.script_body or "")
    if sentence_hashes is None:
        sentence_hashes = tuple(text_sha256(sentence) for sentence in sentences)
    assessments = []
    for index, references in enumerate(references_by_sentence):
        references = tuple(sorted(references, key=lambda item: item.reference_id))
        kind = assessment_kind or (
            "place_references" if references else "no_material_place_claim"
        )
        assessment_resolution = (
            "unresolved" if kind == "unreviewed" else "resolved"
        )
        assessments.append(
            CorpusSentencePlaceAssessment(
                stable_beat_id=beat.stable_beat_id,
                sentence_index=index,
                sentence_sha256=sentence_hashes[index],
                assessment=kind,
                references=references,
                resolution_state=assessment_resolution,
                provenance_record_id=f"review:{beat.stable_beat_id}:{index}",
            )
        )
    return CorpusBeatPlacePlan.build(
        place_plan_id=beat.place_plan_id,
        stable_beat_id=beat.stable_beat_id,
        host_canonical_place_id=host_canonical_id,
        sentence_assessments=tuple(assessments),
        resolution_state=resolution_state,
        provenance_record_id=f"review:{beat.stable_beat_id}",
    )


def _identity(
    beat: BeatRef, *, resolution_state: str = "resolved", span_hash: str | None = None
) -> StableBeatIdentity:
    assert beat.stable_beat_id is not None
    return StableBeatIdentity(
        stable_beat_id=beat.stable_beat_id,
        source_document_id=f"doc:{beat.stable_beat_id}",
        source_revision="1",
        source_span_sha256=span_hash or text_sha256(beat.source_passage or ""),
        resolution_state=resolution_state,
    )


def _source_snapshot(
    pois: tuple[POI, ...], beats: tuple[BeatRef, ...]
) -> CorpusSnapshot:
    by_poi: dict[str, list[BeatRef]] = {}
    for beat in beats:
        by_poi.setdefault(beat.poi_id, []).append(beat)
    return CorpusSnapshot(
        pois=pois,
        beats_by_poi={key: tuple(value) for key, value in by_poi.items()},
        area_types={"Test Area": "neighborhood"},
        adjacent_areas={"Test Area": frozenset({"Other"})},
        lens_neighbors={"history": frozenset({"architecture"})},
    )


def _attach_manifest(
    source: CorpusSnapshot,
    *,
    places: tuple[CanonicalPlaceIdentity, ...],
    plans: tuple[CorpusBeatPlacePlan, ...],
    identities: tuple[StableBeatIdentity, ...] | None = None,
) -> CorpusSnapshot:
    beats = tuple(beat for values in source.beats_by_poi.values() for beat in values)
    identities = identities or tuple(_identity(beat) for beat in beats)
    unresolved = any(
        item.resolution_state != "resolved"
        for item in (*places, *plans, *identities)
    )
    manifest = CorpusPlaceManifest.build(
        manifest_id="fixture-manifest",
        authority_id="fixture-authority",
        selection_payload_sha256=selection_payload_sha256(source),
        resolution_state="contains_unresolved" if unresolved else "resolved",
        places=places,
        beat_identities=identities,
        beat_place_plans=plans,
    )
    return replace(source, place_manifest=manifest)


def _empty_route() -> Route:
    return Route(
        pois=(),
        transits=(),
        total_walk_distance_m=0,
        total_walk_seconds=0,
    )


def test_manifest_rejects_shared_source_identity_and_tampered_plan_hash():
    first = _place("place:a", source_id="same-source")
    second = _place("place:b", source_id="same-source", lat=48.851)
    with pytest.raises(ValidationError, match="multiple canonical places"):
        CorpusPlaceManifest.build(
            manifest_id="bad",
            authority_id="fixture-authority",
            selection_payload_sha256="0" * 64,
            resolution_state="resolved",
            places=(first, second),
            beat_identities=(),
            beat_place_plans=(),
        )

    poi = _poi("raw-p", first)
    beat = _beat("raw-b", "beat:1", poi.id, "A sentence.")
    plan = _plan(
        beat,
        first.canonical_place_id,
        ((_reference("r1", canonical_id=first.canonical_place_id),),),
    )
    tampered = plan.model_dump(mode="python")
    tampered["plan_sha256"] = "f" * 64
    with pytest.raises(ValidationError, match="plan hash"):
        CorpusBeatPlacePlan.model_validate(tampered)

    with pytest.raises(ValidationError, match="host success reason"):
        MaterializationDecision(
            stable_beat_id="beat:1",
            place_plan_id="plan:beat:1",
            source_poi_id="stable-source",
            source_canonical_place_id="place:a",
            disposition="host",
            destination_poi_id="stable-source",
            destination_canonical_place_id="place:a",
            reason="off_site_resolved",
        )


def test_unreviewed_is_explicit_and_never_masquerades_as_no_place_claim():
    with pytest.raises(ValidationError, match="resolved review decision"):
        CorpusSentencePlaceAssessment(
            stable_beat_id="beat:1",
            sentence_index=0,
            sentence_sha256="0" * 64,
            assessment="no_material_place_claim",
            resolution_state="unresolved",
            provenance_record_id="pending",
        )
    pending = CorpusSentencePlaceAssessment(
        stable_beat_id="beat:1",
        sentence_index=0,
        sentence_sha256="0" * 64,
        assessment="unreviewed",
        resolution_state="unresolved",
        provenance_record_id="pending",
    )
    assert pending.references == ()


def test_off_site_relocates_to_neutral_dwell_only_target_and_is_idempotent():
    host = _place("place:host", name="Host")
    destination = _place("place:destination", name="Destination", lat=48.851, lng=2.351)
    host_poi = _poi("graph-poi-host", host, tier=5)
    beat = _beat(
        "graph-beat-uuid",
        "stable-beat",
        host_poi.id,
        "Walk through the destination courtyard.",
    )
    plan = _plan(
        beat,
        host.canonical_place_id,
        ((_reference("off", relation="off_site", canonical_id=destination.canonical_place_id),),),
    )
    typed = _attach_manifest(
        _source_snapshot((host_poi,), (beat,)),
        places=(destination, host),
        plans=(plan,),
    )
    with pytest.raises(ValueError, match="materialized before extra narration"):
        build_poi_extra_narration(
            {host_poi.id: (beat.id,)},
            typed,
        )

    result = materialize_corpus_snapshot(typed)
    again = materialize_corpus_snapshot(result.snapshot)
    assert again.snapshot is result.snapshot
    assert again.plan is result.plan

    host_out = next(p for p in result.snapshot.pois if p.canonical_place_id == "place:host")
    target = next(
        p for p in result.snapshot.pois if p.canonical_place_id == "place:destination"
    )
    assert host_out.beat_count == 0
    assert target.tier == 1  # no source-host gravity inheritance
    assert target.areas == ()
    assert target.requires_dwell is True
    assert target.vignette_eligible is False
    moved = result.snapshot.beats_by_poi[target.id][0]
    assert moved.id == moved.stable_beat_id == "stable-beat"
    assert moved.poi_id == target.id
    assert moved.requires_dwell is True and moved.vignette_eligible is False
    assert "graph-" not in result.plan.model_dump_json()
    assert build_poi_extra_narration(
        {host_out.id: (moved.id,)}, result.snapshot
    ) == {}
    assert build_poi_extra_narration(
        {target.id: (moved.id,)}, result.snapshot
    ) == {target.id: "Walk through the destination courtyard."}

    with pytest.raises(TypeError):
        result.snapshot.beats_by_poi[target.id] = ()  # type: ignore[index]

    out_of_budget = materialize_corpus_snapshot(
        typed, admitted_canonical_place_ids={host.canonical_place_id}
    )
    decision = out_of_budget.plan.decisions[0]
    assert decision.disposition == "omitted"
    assert decision.reason == "destination_out_of_budget"
    assert all(
        p.canonical_place_id != destination.canonical_place_id
        for p in out_of_budget.snapshot.pois
    )
    typed.beats_by_poi.clear()  # caller-owned source mapping remains mutable
    assert result.snapshot.beats_by_poi[target.id] == (moved,)


def test_uuid_row_manifest_and_lens_permutations_are_byte_stable():
    shared = _place("place:shared", name="Shared")
    distinct = _place("place:distinct", name="Shared", lat=48.8501, lng=2.3501)

    def build(raw_suffix: str, reverse: bool) -> CorpusSnapshot:
        alias_a = _poi(
            f"raw-a-{raw_suffix}", shared, name="ALIAS", aliases=("alpha", "ALPHA")
        )
        alias_b = _poi(f"raw-b-{raw_suffix}", shared, name="alias")
        nearby = _poi(f"raw-c-{raw_suffix}", distinct, name="Shared")
        beat_a = _beat(
            f"uuid-a-{raw_suffix}",
            "beat:a",
            alias_a.id,
            "Host fact A.",
            lenses=("history", "architecture") if not reverse else ("architecture", "history"),
        )
        beat_b = _beat(f"uuid-b-{raw_suffix}", "beat:b", alias_b.id, "Host fact B.")
        beat_c = _beat(f"uuid-c-{raw_suffix}", "beat:c", nearby.id, "Nearby fact.")
        plans = (
            _plan(beat_a, "place:shared", ((_reference("a", canonical_id="place:shared"),),)),
            _plan(beat_b, "place:shared", ((_reference("b", canonical_id="place:shared"),),)),
            _plan(beat_c, "place:distinct", ((_reference("c", canonical_id="place:distinct"),),)),
        )
        pois = (nearby, alias_b, alias_a) if reverse else (alias_a, alias_b, nearby)
        beats = (beat_c, beat_b, beat_a) if reverse else (beat_a, beat_b, beat_c)
        source = _source_snapshot(pois, beats)
        return _attach_manifest(
            source,
            places=(distinct, shared) if reverse else (shared, distinct),
            plans=tuple(reversed(plans)) if reverse else plans,
            identities=tuple(
                reversed(tuple(_identity(beat) for beat in beats))
            ) if reverse else tuple(_identity(beat) for beat in beats),
        )

    first = build("one", False)
    second = build("two", True)
    assert selection_payload_sha256(first) == selection_payload_sha256(second)
    assert first.place_manifest == second.place_manifest

    one = materialize_corpus_snapshot(first)
    two = materialize_corpus_snapshot(second)
    assert one.plan == two.plan
    assert one.snapshot.snapshot_sha256 == two.snapshot.snapshot_sha256
    assert one.snapshot.pois == two.snapshot.pois
    assert dict(one.snapshot.beats_by_poi) == dict(two.snapshot.beats_by_poi)
    assert len(one.snapshot.pois) == 2  # aliases collapse; nearby distinct survives
    shared_out = next(p for p in one.snapshot.pois if p.canonical_place_id == "place:shared")
    assert shared_out.beat_count == 2
    assert len({alias.casefold() for alias in shared_out.aliases}) == len(shared_out.aliases)


def test_conflicting_alias_selection_metadata_fails_closed():
    place = _place("place:one")
    first = _poi("raw-1", place, tier=5)
    second = _poi("raw-2", place, tier=4)
    beat_a = _beat("uuid-1", "beat:1", first.id, "First.")
    beat_b = _beat("uuid-2", "beat:2", second.id, "Second.")
    plans = (
        _plan(
            beat_a,
            place.canonical_place_id,
            ((_reference("r1", canonical_id=place.canonical_place_id),),),
        ),
        _plan(
            beat_b,
            place.canonical_place_id,
            ((_reference("r2", canonical_id=place.canonical_place_id),),),
        ),
    )
    typed = _attach_manifest(
        _source_snapshot((first, second), (beat_a, beat_b)),
        places=(place,),
        plans=plans,
    )
    with pytest.raises(ValueError, match="alias rows disagree"):
        materialize_corpus_snapshot(typed)


@pytest.mark.parametrize("failure", ["coverage", "sentence_hash", "source_hash", "host"])
def test_exact_source_and_host_bindings_reject_corruption(failure: str):
    host = _place("place:host")
    other = _place("place:other", lat=48.851)
    poi = _poi("raw-host", host)
    script = "First sentence. Second sentence."
    beat = _beat("raw-beat", "beat:bound", poi.id, script)
    references = (
        (_reference("r1", canonical_id=host.canonical_place_id),),
        (_reference("r2", canonical_id=host.canonical_place_id),),
    )
    hashes = None
    plan_refs = references
    plan_host = host.canonical_place_id
    identities = None
    if failure == "coverage":
        plan_refs = references[:1]
    elif failure == "sentence_hash":
        hashes = ("f" * 64, text_sha256("Second sentence."))
    elif failure == "source_hash":
        identities = (_identity(beat, span_hash="e" * 64),)
    elif failure == "host":
        plan_host = other.canonical_place_id
    plan = _plan(beat, plan_host, plan_refs, sentence_hashes=hashes)
    typed = _attach_manifest(
        _source_snapshot((poi,), (beat,)),
        places=(host, other),
        plans=(plan,),
        identities=identities,
    )
    with pytest.raises(ValueError):
        materialize_corpus_snapshot(typed)


def test_manifest_content_swap_is_rejected_before_semantic_processing():
    host = _place("place:host")
    poi = _poi("raw-host", host)
    beat = _beat("raw-beat", "beat:1", poi.id, "Original sentence.")
    plan = _plan(
        beat,
        host.canonical_place_id,
        ((_reference("r1", canonical_id=host.canonical_place_id),),),
    )
    typed = _attach_manifest(
        _source_snapshot((poi,), (beat,)), places=(host,), plans=(plan,)
    )
    swapped_beat = beat.model_copy(update={"script_body": "Swapped sentence."})
    swapped = replace(typed, beats_by_poi={poi.id: (swapped_beat,)})
    with pytest.raises(ValueError, match="different selection source data"):
        materialize_corpus_snapshot(swapped)

    wrong_splitter = typed.place_manifest.model_copy(
        update={"sentence_splitter_version": "obsolete-splitter"}
    )
    with pytest.raises(ValueError, match="different sentence splitter"):
        materialize_corpus_snapshot(replace(typed, place_manifest=wrong_splitter))


def test_context_stays_host_while_mixed_and_unreviewed_contribute_zero():
    host = _place("place:host")
    remote = _place("place:remote", lat=48.9, lng=2.4)
    poi = _poi("raw-host", host)
    context = _beat("uuid-context", "beat:context", poi.id, "He lived in exile elsewhere.")
    mixed = _beat("uuid-mixed", "beat:mixed", poi.id, "Look here, then enter elsewhere.")
    pending = _beat("uuid-pending", "beat:pending", poi.id, "Unreviewed place claim.")
    context_plan = _plan(
        context,
        host.canonical_place_id,
        ((_reference(
            "historical",
            kind="contextual_historical",
            relation=None,
            canonical_id=remote.canonical_place_id,
        ),),),
    )
    mixed_plan = _plan(
        mixed,
        host.canonical_place_id,
        ((
            _reference("primary", canonical_id=host.canonical_place_id),
            _reference("off", relation="off_site", canonical_id=remote.canonical_place_id),
        ),),
        resolution_state="unresolved",
    )
    pending_plan = _plan(
        pending,
        host.canonical_place_id,
        ((),),
        resolution_state="unresolved",
        assessment_kind="unreviewed",
    )
    typed = _attach_manifest(
        _source_snapshot((poi,), (context, mixed, pending)),
        places=(host, remote),
        plans=(context_plan, mixed_plan, pending_plan),
    )
    result = materialize_corpus_snapshot(typed)
    host_out = next(
        p
        for p in result.snapshot.pois
        if p.canonical_place_id == host.canonical_place_id
    )
    assert host_out.beat_count == 1
    assert result.snapshot.beats_by_poi[host_out.id][0].stable_beat_id == "beat:context"
    assert all(p.canonical_place_id != remote.canonical_place_id for p in result.snapshot.pois)
    reasons = {decision.stable_beat_id: decision.reason for decision in result.plan.decisions}
    assert reasons == {
        "beat:context": "context_only",
        "beat:mixed": "mixed_host_and_off_site",
        "beat:pending": "unresolved_plan",
    }


def test_seven_off_site_beats_move_before_density_instead_of_inflating_host(
    monkeypatch,
):
    host = _place("place:washington", lat=48.85, lng=2.35)
    poi = _poi("raw-washington", host)
    targets = tuple(
        _place(
            f"place:target:{index}",
            lat=48.95 + index * 0.001,
            lng=2.45,
        )
        for index in range(7)
    )
    beats = tuple(
        _beat(
            f"uuid:{index}",
            f"beat:{index}",
            poi.id,
            f"Enter remote address {index}.",
        )
        for index in range(7)
    )
    plans = tuple(
        _plan(
            beat,
            host.canonical_place_id,
            ((_reference(
                f"off:{index}",
                relation="off_site",
                canonical_id=targets[index].canonical_place_id,
            ),),),
        )
        for index, beat in enumerate(beats)
    )
    raw = _source_snapshot((poi,), beats)
    typed = _attach_manifest(raw, places=(host, *targets), plans=plans)
    tour_input = TourInput(
        start=(host.coordinates[0], host.coordinates[1]),
        duration_min=10,
        city_slug="paris",
    )
    assert assess(tour_input, raw.pois, raw.beats_by_poi).reachable_beat_count == 7
    with pytest.raises(ValueError, match="materialized before density"):
        assess_snapshot(tour_input, typed)
    with pytest.raises(ValueError, match="before density"):
        select_route(tour_input, typed)

    places_property = CorpusPlaceManifest.places_by_id
    plans_property = CorpusPlaceManifest.plans_by_id
    property_calls = {"places": 0, "plans": 0}

    def counted_places(manifest):
        property_calls["places"] += 1
        return places_property.fget(manifest)

    def counted_plans(manifest):
        property_calls["plans"] += 1
        return plans_property.fget(manifest)

    monkeypatch.setattr(
        CorpusPlaceManifest, "places_by_id", property(counted_places)
    )
    monkeypatch.setattr(
        CorpusPlaceManifest, "plans_by_id", property(counted_plans)
    )
    materialized = materialize_corpus_snapshot(typed).snapshot
    assert property_calls == {"places": 2, "plans": 2}
    assessment = assess_snapshot(tour_input, materialized)
    assert assessment.reachable_beat_count == 0
    materialized_host = next(
        p for p in materialized.pois if p.canonical_place_id == host.canonical_place_id
    )
    assert materialized_host.beat_count == 0
    assert all(
        p.tier == 1
        for p in materialized.pois
        if p.canonical_place_id != host.canonical_place_id
    )


def test_pending_and_forged_typed_snapshots_cannot_bypass_vignette_guard():
    host = _place("place:host")
    poi = _poi("raw-host", host)
    beat = _beat("raw-beat", "beat:1", poi.id, "Host sentence.")
    plan = _plan(
        beat,
        host.canonical_place_id,
        ((_reference("r1", canonical_id=host.canonical_place_id),),),
    )
    typed = _attach_manifest(
        _source_snapshot((poi,), (beat,)), places=(host,), plans=(plan,)
    )
    with pytest.raises(ValueError, match="materialized before vignette"):
        select_vignettes(_empty_route(), typed)

    valid = materialize_corpus_snapshot(typed).snapshot
    with pytest.raises(ValueError, match="only the validated materializer"):
        replace(valid, snapshot_sha256="0" * 64)

    poi_id, beats = next(iter(valid.beats_by_poi.items()))
    wrong_plan_beat = beats[0].model_copy(update={"place_plan_id": "wrong-plan"})
    from src.tour.selection import _MATERIALIZED_SNAPSHOT_TOKEN

    wrong_plan_snapshot = replace(
        valid,
        beats_by_poi=MappingProxyType({poi_id: (wrong_plan_beat,)}),
        _validation_token=_MATERIALIZED_SNAPSHOT_TOKEN,
    )
    with pytest.raises(ValueError, match="differs from its decision"):
        select_vignettes(_empty_route(), wrong_plan_snapshot)


def test_select_route_performs_one_full_validation_not_one_per_beat_lookup(monkeypatch):
    host = _place("place:host")
    poi = _poi("raw-host", host)
    beat = _beat("raw-beat", "beat:1", poi.id, "Host sentence.")
    plan = _plan(
        beat,
        host.canonical_place_id,
        ((_reference("r1", canonical_id=host.canonical_place_id),),),
    )
    typed = _attach_manifest(
        _source_snapshot((poi,), (beat,)), places=(host,), plans=(plan,)
    )
    materialized = materialize_corpus_snapshot(typed).snapshot

    import src.tour.place_materialization as place_materialization

    original = place_materialization.validate_materialized_corpus_snapshot
    calls = 0

    def counted(snapshot):
        nonlocal calls
        calls += 1
        return original(snapshot)

    monkeypatch.setattr(
        place_materialization, "validate_materialized_corpus_snapshot", counted
    )
    tour_input = TourInput(
        start=host.coordinates,
        duration_min=10,
        city_slug="paris",
    )
    with pytest.raises(TourabilityRefusedError):
        select_route(tour_input, materialized)
    assert calls == 1
