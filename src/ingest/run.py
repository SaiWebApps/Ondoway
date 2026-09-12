"""The job runner: one source through P0 intake → P7 commit.

Docs/ingestion/rebuild-spec.md §3 (the phase table), §5 (the front door),
§1 D12/D13 and CONTEXT.md pin the shapes below. The phases themselves are
the slice 3-6 modules (`decompose`, `group`, `judge_claims`, `narrate`,
`judge_narration`, `merge`); this module only sequences them, persists
each phase's output to the job before the next starts, and assembles
what those modules deliberately left to the runner.

`run_job(job_id, store, client)`:

- P0 intake — a book source is a chunk dir with a manifest (D12): every
  chunk the manifest names becomes one `Unit` carrying the job's as_of and
  rights basis; the manifest's `publisher` (every one has it) feeds the
  leak gate through `narrate.publishers_from_manifest`. A url source is
  refused here with a clear reason: no pinned-revision reader exists yet.
  The city's beats file must be new-shape (or absent): a legacy file
  cannot take a new-shape record, and the validator would refuse the
  mixed file at P7 anyway, so the job stops before any call. Before P1
  the cost estimate — `P1_PLAN`…`P6_PLAN` over the unit texts, a
  ceiling — is the job's first event; it arms the mock and the live
  client refuses to run without it.
- P1 decompose, P2 group per unit.
- P3 judge claims per story, then the unit's omission check; an
  omission finding re-asks P1 ONCE for that unit (`decompose(...,
  omitted=)`), and P2/P3 run again over the re-asked claims — with no
  second omission check. Exactly one re-ask per phase per item: the
  phases enforce their own budgets; the runner never adds a retry.
- P4 narrate, P5 judge narration per story, `publishers=` passed.
- P6 merge per story against the beats already at its place in the
  city's file: no beat there → the story is new without a merge call
  (logged `merge_skipped`); a held outcome → a D13 queue item, nothing
  applied; otherwise `merge.apply`, then P4/P5 run again for every id in
  `MergeOutcome.rerun` before P7. A `new` story's record is assembled
  here: `beat_id = city/slug(place)/story_slug`, the story's enrichment
  fields, P5's narration, its duration and review. A story at a new place
  (`story.new_poi`) and a narration P5 held both become queue items too.
- P7 commit through the slice-1 validator with the chunks root (the
  chunk dir's parent), via `scripts.beats_io.commit`; nothing reaches
  disk otherwise, and the book log records the chunk.

A `UnitHeld` from any phase drops that unit (or that story) with the
phase's own `unit_held`/`beat_held` event as the log line; every other
exception ends the job in `error` with its message, the phases that
landed still on the job so `run_job` can be called again to resume.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from scripts import beats_io
from src.ingest import judge_claims, judge_narration, llm, merge, model, narrate
from src.ingest.decompose import P1_PLAN, ClaimDraft, UnitHeld, decompose
from src.ingest.group import P2_PLAN, Enrichment, Story, group
from src.ingest.jobs import IngestJob, IngestJobStore
from src.ingest.judge_claims import OMISSIONS_PLAN, P3_PLAN, JudgedClaim
from src.ingest.judge_narration import P5_PLAN, JudgedNarration
from src.ingest.merge import P6_PLAN
from src.ingest.narrate import P4_PLAN, BeatHeld, NarrationDraft
from src.ingest.unit import Unit, load_unit

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = REPO_ROOT / "data"

#: The whole job priced over the unit texts before P1 — every phase's
#: plan, re-ask rows included: a ceiling, printed as the first event.
PLAN: tuple[llm.PhaseCall, ...] = (
    *P1_PLAN, *P2_PLAN, *P3_PLAN, *OMISSIONS_PLAN, *P4_PLAN, *P5_PLAN, *P6_PLAN
)

#: Manifest fields P0 requires (D12); as_of and rights basis come from the job.
MANIFEST_FIELDS: tuple[str, ...] = ("chunks", "publisher")


class JobRefused(Exception):  # noqa: N818 — a refusal, not an error class
    """P0 refused the job before any model call; the message says why."""


def data_root_from_env() -> Path:
    """`INGEST_DATA_ROOT` (a per-test or per-proof root) or the repo's data/."""
    value = os.getenv("INGEST_DATA_ROOT")
    return Path(value) if value else DEFAULT_DATA_ROOT


def client_from_env(events: llm.EventSink) -> llm.ModelClient:
    """The model client the front door runs jobs with, from `INGEST_PROVIDER`.

    `mock` needs `INGEST_MOCK_SCRIPT`, a JSON file of scripted answers
    (the mock never fabricates); `anthropic` is the live client. Unset or
    anything else fails closed — a job must never spend by default.
    """
    provider = os.getenv("INGEST_PROVIDER", "").strip().lower()
    if provider == "mock":
        script_path = os.getenv("INGEST_MOCK_SCRIPT", "")
        if not script_path:
            raise ValueError(
                "INGEST_PROVIDER=mock needs INGEST_MOCK_SCRIPT (a scripted answers file)"
            )
        return mock_client_from_script(Path(script_path), events)
    if provider == "anthropic":
        return llm.AnthropicClient(events)
    raise ValueError(
        f"INGEST_PROVIDER must be 'mock' or 'anthropic', got {provider!r}; "
        "an ingest job never spends by default"
    )


def mock_client_from_script(path: Path, events: llm.EventSink) -> llm.MockClient:
    """A MockClient scripted from a JSON file: `{"answers": {role: [...]},
    "batch_answers": {custom_id: {...}}}`, each answer `{text, model_id,
    stop_reason?}`."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))

    def answer(item: dict[str, Any]) -> llm.MockAnswer:
        return llm.MockAnswer(
            text=item["text"],
            model_id=item["model_id"],
            stop_reason=item.get("stop_reason", "end_turn"),
        )

    return llm.MockClient(
        events,
        answers={
            role: [answer(a) for a in items] for role, items in raw.get("answers", {}).items()
        },
        batch_answers={cid: answer(a) for cid, a in raw.get("batch_answers", {}).items()},
    )


# ── P0 intake ───────────────────────────────────────────────────────────────


def intake(job: IngestJob) -> tuple[list[Unit], dict[str, Any]]:
    """The job's units and its manifest (P0). Raises JobRefused."""
    if job.source.kind != "book":
        raise JobRefused(
            "url sources are not ingested yet: no pinned-revision reader exists; "
            "chunk the page as a book source for now"
        )
    chunk_dir = Path(job.source.chunk_dir or "")
    manifest_path = chunk_dir / "manifest.json"
    if not manifest_path.is_file():
        raise JobRefused(f"chunk dir {chunk_dir} has no manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    missing = [f for f in MANIFEST_FIELDS if not manifest.get(f)]
    if missing:
        raise JobRefused(f"manifest {manifest_path} lacks {missing}")
    units: list[Unit] = []
    for entry in manifest["chunks"]:
        filename = entry.get("filename") if isinstance(entry, dict) else None
        if not filename:
            raise JobRefused(f"manifest {manifest_path} names a chunk without a filename")
        units.append(
            load_unit(
                chunk_dir.parent,
                city=job.city,
                source_id=chunk_dir.name,
                chunk=Path(filename).stem,
                as_of=job.as_of,
                rights_basis=job.rights_basis,
            )
        )
    if not units:
        raise JobRefused(f"manifest {manifest_path} names no chunks")
    return units, manifest


def load_beats(beats_path: Path) -> tuple[list[dict[str, Any]], list[model.Beat]]:
    """The city's new-shape records as read, and as beats; both [] when
    the file does not exist. A file holding any legacy-shape record
    refuses the job (JobRefused)."""
    if not beats_path.is_file():
        return [], []
    records = json.loads(beats_path.read_text(encoding="utf-8"))
    legacy = [r for r in records if not ("claims" in r and "narration" in r)]
    if legacy:
        raise JobRefused(
            f"{beats_path} holds {len(legacy)} legacy-shape record(s); a new-shape record "
            "cannot join a legacy file and the validator would refuse the mixed file "
            "(the slice-10 swap re-homes the city first)"
        )
    return records, [model.Beat.model_validate(r) for r in records]


def load_pois(data_root: Path, city: str) -> list[dict[str, Any]]:
    path = data_root / city / "poi-raw.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


# ── Record assembly ─────────────────────────────────────────────────────────


def claim_record(judged: JudgedClaim) -> dict[str, Any]:
    return {
        "claim_id": judged.draft.claim_id,
        "text": judged.draft.text,
        "kind": judged.draft.kind,
        "status": "resolved",
        "sources": [judged.draft.source.model_dump(mode="json")],
        "resolved_value": None,
        "resolution": None,
        "verdict": judged.verdict.model_dump(mode="json"),
    }


def assemble_record(
    city: str,
    story: Story,
    judged: Sequence[JudgedClaim],
    narration: JudgedNarration,
    *,
    review: model.Review | None = None,
) -> model.Beat:
    """A `new` story's record: P6 only reports `new`; the runner owns the
    beat (beat_id = city/slug(place)/story_slug, Enrichment → Beat fields,
    P5's narration, its duration, its review unless one is given)."""
    return model.Beat(
        beat_id=f"{city}/{model.slug(story.place)}/{story.story_slug}",
        city_name=city,
        poi_name=story.place,
        story_slug=story.story_slug,
        title=story.title,
        beat_type=story.beat_type,
        lenses=list(story.lenses),
        claims=[claim_record(j) for j in judged],
        narration=narration.narration,
        duration_sec=narration.duration_sec,
        review=review if review is not None else narration.review,
        **story.enrichment.model_dump(),
    )


def story_of_beat(beat: model.Beat) -> Story:
    """The Story a committed beat corresponds to, for a P4/P5 rerun."""
    return Story(
        title=beat.title,
        story_slug=beat.story_slug,
        place=beat.poi_name,
        new_poi=False,
        lenses=list(beat.lenses),
        beat_type=beat.beat_type,
        enrichment=Enrichment(),
        claim_ids=[c.claim_id for c in beat.claims if c.status == "resolved"],
    )


def judged_of_beat(beat: model.Beat) -> list[JudgedClaim]:
    """A beat's RESOLVED claims as the JudgedClaims P4/P5 read (texts and
    spans). A claim with several sources carries every span, newline-
    joined, so the lift gate sees all of them."""
    out: list[JudgedClaim] = []
    for claim in beat.claims:
        if claim.status != "resolved":
            continue
        spans = "\n".join(s.span for s in claim.sources)
        source = claim.sources[0].model_copy(update={"span": spans})
        out.append(
            JudgedClaim(
                draft=ClaimDraft(
                    claim_id=claim.claim_id, text=claim.text, kind=claim.kind, source=source
                ),
                verdict=claim.verdict,
            )
        )
    return out


# ── Serialization helpers (phase outputs are JSON-safe dicts) ───────────────


def _dump(obj: Any) -> Any:
    return obj.model_dump(mode="json")


def _story_key(unit: Unit, story: Story) -> str:
    return f"{unit.key}:{story.story_slug}"


# ── The runner ──────────────────────────────────────────────────────────────


class _Run:
    """One `run_job` invocation's working state."""

    def __init__(
        self, job: IngestJob, store: IngestJobStore, client: llm.ModelClient, data_root: Path
    ) -> None:
        self.job = job
        self.store = store
        self.client = client
        self.data_root = Path(data_root)
        self.city_dir = self.data_root / job.city
        self.beats_path = self.city_dir / "beats.json"
        self.log_path = self.city_dir / "book-log.json"

    # -- events --
    def emit(self, kind: str, payload: dict[str, Any]) -> None:
        self.store.append_event(self.job.id, "info", kind, data=payload)

    def done(self, phase: str, output: dict[str, Any]) -> None:
        self.store.set_phase(self.job.id, phase, output)

    def output(self, phase: str) -> dict[str, Any] | None:
        return self.store.phase_output(self.job.id, phase)

    def arm(self, units: Sequence[Unit]) -> None:
        """Print the job's cost estimate — the ceiling over every phase's
        plan — as a job event, arming the client's estimate gate."""
        estimate = self.client.estimate([u.text for u in units], list(PLAN))
        self.emit("cost_estimate", estimate.as_dict())

    # -- P0 --
    def p0(self) -> tuple[list[Unit], list[str], Path]:
        saved = self.output("P0")
        if saved is None:
            units, manifest = intake(self.job)
            load_beats(self.beats_path)  # the shape gate, before any call
            publishers = narrate.publishers_from_manifest(manifest)
            chunks_root = Path(self.job.source.chunk_dir or "").parent
            self.arm(units)
            self.done(
                "P0",
                {
                    "units": [_dump(u) for u in units],
                    "publishers": publishers,
                    "chunks_root": str(chunks_root),
                    "book_title": manifest.get("book_title", ""),
                    "author": manifest.get("author", ""),
                },
            )
            return units, publishers, chunks_root
        units = [Unit.model_validate(u) for u in saved["units"]]
        # A resumed run has a fresh client: arm it again ($0 on the mock,
        # a free count on the live client); the estimate is logged again.
        self.arm(units)
        return units, list(saved["publishers"]), Path(saved["chunks_root"])

    # -- P1 --
    def p1(self, units: Sequence[Unit]) -> dict[str, list[ClaimDraft]]:
        saved = self.output("P1")
        if saved is not None:
            return {
                key: [ClaimDraft.model_validate(c) for c in claims]
                for key, claims in saved["claims"].items()
            }
        claims: dict[str, list[ClaimDraft]] = {}
        for unit in units:
            try:
                claims[unit.key] = decompose(unit, self.client, events=self.emit)
            except UnitHeld:
                continue  # the phase logged unit_held; D13: a log line
        self.done("P1", {"claims": {k: [_dump(c) for c in v] for k, v in claims.items()}})
        return claims

    # -- P2 --
    def p2(
        self, units: Sequence[Unit], claims: dict[str, list[ClaimDraft]], pois: list[dict]
    ) -> dict[str, list[Story]]:
        saved = self.output("P2")
        if saved is not None:
            return {
                key: [Story.model_validate(s) for s in stories]
                for key, stories in saved["stories"].items()
            }
        stories: dict[str, list[Story]] = {}
        for unit in units:
            if unit.key not in claims:
                continue
            try:
                stories[unit.key] = group(
                    claims[unit.key], unit, pois, self.client, events=self.emit
                )
            except UnitHeld:
                continue
        self.done("P2", {"stories": {k: [_dump(s) for s in v] for k, v in stories.items()}})
        return stories

    # -- P3 --
    def _judge_stories(
        self, unit: Unit, claims: Sequence[ClaimDraft], stories: Sequence[Story]
    ) -> dict[str, list[JudgedClaim]]:
        by_id = {c.claim_id: c for c in claims}
        judged: dict[str, list[JudgedClaim]] = {}
        for story in stories:
            drafts = [by_id[cid] for cid in story.claim_ids if cid in by_id]
            try:
                judged[story.story_slug] = judge_claims.judge_claims(
                    story, drafts, unit, self.client, events=self.emit
                )
            except UnitHeld:
                continue
        return judged

    def p3(
        self,
        units: Sequence[Unit],
        claims: dict[str, list[ClaimDraft]],
        stories: dict[str, list[Story]],
        pois: list[dict],
    ) -> tuple[dict[str, list[Story]], dict[str, dict[str, list[JudgedClaim]]]]:
        saved = self.output("P3")
        if saved is not None:
            final_stories = {
                key: [Story.model_validate(s) for s in items]
                for key, items in saved["stories"].items()
            }
            judged = {
                key: {slug: [JudgedClaim.model_validate(j) for j in items]
                      for slug, items in per_story.items()}
                for key, per_story in saved["judged"].items()
            }
            return final_stories, judged
        final_stories: dict[str, list[Story]] = {}
        judged: dict[str, dict[str, list[JudgedClaim]]] = {}
        reasked: list[str] = []
        for unit in units:
            if unit.key not in stories:
                continue
            unit_claims = claims[unit.key]
            unit_stories = stories[unit.key]
            unit_judged = self._judge_stories(unit, unit_claims, unit_stories)
            try:
                found = judge_claims.omissions(unit, unit_claims, self.client, events=self.emit)
            except UnitHeld:
                found = []
            if found:
                # One P1 re-ask for the unit with the omissions quoted back,
                # then P2 and P3 again over the re-asked claims; no second
                # omission check (one re-ask per phase per item).
                reasked.append(unit.key)
                try:
                    unit_claims = decompose(unit, self.client, events=self.emit, omitted=found)
                    unit_stories = group(unit_claims, unit, pois, self.client, events=self.emit)
                except UnitHeld:
                    continue
                unit_judged = self._judge_stories(unit, unit_claims, unit_stories)
            final_stories[unit.key] = unit_stories
            judged[unit.key] = unit_judged
        self.done(
            "P3",
            {
                "stories": {k: [_dump(s) for s in v] for k, v in final_stories.items()},
                "judged": {
                    k: {slug: [_dump(j) for j in items] for slug, items in per.items()}
                    for k, per in judged.items()
                },
                "reasked_units": reasked,
            },
        )
        return final_stories, judged

    # -- P4 --
    def p4(
        self,
        units: Sequence[Unit],
        stories: dict[str, list[Story]],
        judged: dict[str, dict[str, list[JudgedClaim]]],
        publishers: Sequence[str],
    ) -> dict[str, NarrationDraft]:
        saved = self.output("P4")
        if saved is not None:
            return {k: NarrationDraft.model_validate(d) for k, d in saved["drafts"].items()}
        drafts: dict[str, NarrationDraft] = {}
        for unit in units:
            for story in stories.get(unit.key, []):
                claims = judged.get(unit.key, {}).get(story.story_slug)
                if not claims:
                    continue
                try:
                    drafts[_story_key(unit, story)] = narrate.narrate(
                        story, claims, self.client, events=self.emit, publishers=publishers
                    )
                except BeatHeld as held:
                    self._queue_held_narration(story, claims, held.reason, phase="P4")
        self.done("P4", {"drafts": {k: _dump(d) for k, d in drafts.items()}})
        return drafts

    # -- P5 --
    def p5(
        self,
        units: Sequence[Unit],
        stories: dict[str, list[Story]],
        judged: dict[str, dict[str, list[JudgedClaim]]],
        drafts: dict[str, NarrationDraft],
        publishers: Sequence[str],
    ) -> dict[str, JudgedNarration]:
        saved = self.output("P5")
        if saved is not None:
            return {k: JudgedNarration.model_validate(n) for k, n in saved["narrations"].items()}
        narrations: dict[str, JudgedNarration] = {}
        for unit in units:
            for story in stories.get(unit.key, []):
                key = _story_key(unit, story)
                if key not in drafts:
                    continue
                claims = judged[unit.key][story.story_slug]
                narrations[key] = judge_narration.judge_narration(
                    story, drafts[key], claims, self.client, events=self.emit,
                    publishers=publishers,
                )
        self.done("P5", {"narrations": {k: _dump(n) for k, n in narrations.items()}})
        return narrations

    # -- queue items --
    def _queue_held_narration(
        self, story: Story, claims: Sequence[JudgedClaim], reason: str, *, phase: str,
        narration: str | None = None, duration_sec: int = 0,
    ) -> None:
        shown = {
            "story": _dump(story),
            "claims": [c.draft.text for c in claims],
            "narration": narration,
            "phase": phase,
            "reason": reason,
        }
        self.store.queue(
            job_id=self.job.id, city=self.job.city, kind="narration_held",
            story_slug=story.story_slug, place=story.place, reason=reason, shown=shown,
            held_back={"claims": len(claims), "duration_sec": duration_sec},
        )

    def _queue_new_place(self, beat: model.Beat, story: Story) -> None:
        shown = {"beat": _dump(beat), "reason": f"new_poi: {story.place}"}
        self.store.queue(
            job_id=self.job.id, city=self.job.city, kind="new_place",
            story_slug=story.story_slug, place=story.place, reason=shown["reason"],
            shown=shown, held_back={"claims": len(beat.claims), "duration_sec": beat.duration_sec},
        )

    def _queue_held_merge(
        self, story: Story, claims: Sequence[JudgedClaim], outcome: merge.MergeOutcome,
        narration: JudgedNarration,
    ) -> None:
        shown = {
            "story": _dump(story),
            "claims": [c.draft.text for c in claims],
            "narration": narration.narration.text,
            "judge_story": outcome.judge_story,
            "reason": outcome.reason,
        }
        self.store.queue(
            job_id=self.job.id, city=self.job.city, kind="merge_held",
            story_slug=story.story_slug, place=story.place, reason=outcome.reason or "",
            shown=shown,
            held_back={"claims": len(claims), "duration_sec": narration.duration_sec},
        )

    # -- P6 --
    def _rerun(
        self, beat: model.Beat, publishers: Sequence[str]
    ) -> model.Beat:
        """P4/P5 again for a beat whose resolved texts changed."""
        story = story_of_beat(beat)
        claims = judged_of_beat(beat)
        try:
            draft = narrate.narrate(story, claims, self.client, events=self.emit,
                                    publishers=publishers)
        except BeatHeld as held:
            self._queue_held_narration(story, claims, held.reason, phase="P4")
            return beat.model_copy(
                update={"review": model.Review(held=True, reason=f"P4: {held.reason}")}
            )
        judged = judge_narration.judge_narration(
            story, draft, claims, self.client, events=self.emit, publishers=publishers
        )
        if judged.review.held:
            self._queue_held_narration(
                story, claims, judged.review.reason or "", phase="P5",
                narration=judged.narration.text, duration_sec=judged.duration_sec,
            )
        return beat.model_copy(
            update={
                "narration": judged.narration,
                "duration_sec": judged.duration_sec,
                "review": judged.review,
            }
        )

    def p6(
        self,
        units: Sequence[Unit],
        stories: dict[str, list[Story]],
        judged: dict[str, dict[str, list[JudgedClaim]]],
        narrations: dict[str, JudgedNarration],
        publishers: Sequence[str],
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        """Returns the whole file's records after the merge — an untouched
        beat exactly as it was read, a changed or new one dumped — plus
        per-unit counts for the book log."""
        saved = self.output("P6")
        if saved is not None:
            return saved["records"], saved["per_unit"]
        raw, existing = load_beats(self.beats_path)
        raw_by_id = {r["beat_id"]: r for r in raw}
        new_records: list[model.Beat] = []
        rerun: list[str] = []
        applied: list[str] = []
        changed_ids: set[str] = set()
        per_unit: dict[str, dict[str, Any]] = {}
        for unit in units:
            touched: set[str] = set()
            extracted = 0
            for story in stories.get(unit.key, []):
                key = _story_key(unit, story)
                if key not in narrations:
                    continue
                claims = judged[unit.key][story.story_slug]
                narration = narrations[key]
                if narration.review.held:
                    self._queue_held_narration(
                        story, claims, narration.review.reason or "", phase="P5",
                        narration=narration.narration.text,
                        duration_sec=narration.duration_sec,
                    )
                if story.new_poi:
                    beat = assemble_record(
                        self.job.city, story, claims, narration,
                        review=model.Review(held=True, reason=f"new_poi: {story.place}"),
                    )
                    self._queue_new_place(beat, story)
                    new_records.append(beat)
                    extracted += 1
                    touched.add(story.place)
                    continue
                candidates = [b for b in existing if b.poi_name == story.place]
                if not candidates:
                    self.emit(
                        "merge_skipped",
                        {"story_slug": story.story_slug, "place": story.place,
                         "reason": "no beat at the place; the story is new"},
                    )
                    outcome = None
                else:
                    outcome = merge.merge(story, claims, candidates, self.client, events=self.emit)
                    if outcome.held:
                        self._queue_held_merge(story, claims, outcome, narration)
                        continue
                if outcome is None or outcome.story == "new":
                    beat = assemble_record(self.job.city, story, claims, narration)
                    if any(b.beat_id == beat.beat_id for b in [*existing, *new_records]):
                        self.emit(
                            "beat_id_collision",
                            {"story_slug": story.story_slug, "beat_id": beat.beat_id},
                        )
                        continue
                    new_records.append(beat)
                    extracted += 1
                else:
                    existing = merge.apply(outcome, existing)
                    applied.append(story.story_slug)
                    if outcome.beat_id:
                        changed_ids.add(outcome.beat_id)
                    rerun.extend(i for i in outcome.rerun if i not in rerun)
                touched.add(story.place)
            per_unit[unit.key] = {
                "chunk": unit.chunk,
                "beats_extracted": extracted,
                "pois_touched": sorted(touched),
            }
        if rerun:
            self.emit("rerun", {"beat_ids": list(rerun)})
            existing = [
                self._rerun(b, publishers) if b.beat_id in rerun else b for b in existing
            ]
        changed_ids.update(rerun)
        records = [
            *(_dump(b) if b.beat_id in changed_ids else raw_by_id[b.beat_id] for b in existing),
            *(_dump(b) for b in new_records),
        ]
        self.done(
            "P6",
            {
                "records": records,
                "per_unit": per_unit,
                "new": [b.beat_id for b in new_records],
                "applied": applied,
                "rerun": rerun,
                "changed": bool(new_records or applied or rerun),
            },
        )
        return records, per_unit

    # -- P7 --
    def p7(
        self, records: list[dict[str, Any]], per_unit: dict[str, dict[str, Any]],
        chunks_root: Path,
    ) -> None:
        saved = self.output("P7")
        if saved is not None:
            return
        raw = records
        p6 = self.output("P6") or {}
        if not p6.get("changed"):
            self.emit("commit_skipped", {"reason": "nothing new or changed"})
            self.done("P7", {"beats_path": str(self.beats_path), "written": 0})
            return
        errors = model.validate(raw, chunks_root=chunks_root)
        if errors:
            raise RuntimeError(
                f"the validator refused the file ({len(errors)} error(s)); nothing reached "
                f"disk: " + "; ".join(errors[:5])
            )
        self.city_dir.mkdir(parents=True, exist_ok=True)
        log = (
            json.loads(self.log_path.read_text(encoding="utf-8"))
            if self.log_path.is_file()
            else {"city": self.job.city, "books_processed": []}
        )
        p0 = self.output("P0") or {}
        log["books_processed"].append(
            {
                "book_title": p0.get("book_title", ""),
                "author": p0.get("author", ""),
                "book_slug": Path(self.job.source.chunk_dir or "").name,
                "job_id": self.job.id,
                "as_of": self.job.as_of,
                "rights_basis": self.job.rights_basis,
                "chunks_processed": list(per_unit.values()),
            }
        )
        beats_io.commit(
            raw, log, beats_path=self.beats_path, log_path=self.log_path,
            chunks_root=chunks_root,
        )
        self.done("P7", {"beats_path": str(self.beats_path), "written": len(raw)})

    def run(self) -> None:
        units, publishers, chunks_root = self.p0()
        pois = load_pois(self.data_root, self.job.city)
        claims = self.p1(units)
        stories = self.p2(units, claims, pois)
        stories, judged = self.p3(units, claims, stories, pois)
        drafts = self.p4(units, stories, judged, publishers)
        narrations = self.p5(units, stories, judged, drafts, publishers)
        records, per_unit = self.p6(units, stories, judged, narrations, publishers)
        self.p7(records, per_unit, chunks_root)


def run_job(
    job_id: str,
    store: IngestJobStore,
    client: llm.ModelClient,
    *,
    data_root: Path | str | None = None,
) -> None:
    """Run (or resume) the job; see the module docstring. Never raises: a
    failure ends the job in `error` with the message on the job and an
    `error` event, the completed phases kept for a later resume."""
    job = store.get(job_id)
    if job is None:
        raise KeyError(job_id)
    root = Path(data_root) if data_root is not None else data_root_from_env()
    store.set_status(job_id, "running")
    try:
        _Run(job, store, client, root).run()
    except Exception as exc:  # any failure MUST end the job cleanly
        store.append_event(job_id, "error", str(exc))
        store.set_status(job_id, "error", error=str(exc))
        return
    store.set_status(job_id, "committed")
