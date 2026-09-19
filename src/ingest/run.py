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
  projection; `cap_bound_usd` is the true limit — is the job's first
  event; it arms the mock and the live
  client refuses to run without it.
- P1 decompose, P2 group per unit.
- P3 judge claims per story, then the unit's omission check; an
  omission finding re-asks P1 ONCE for that unit (`decompose(...,
  omitted=, existing=)`) for the omitted facts ONLY, whose new claims
  are grouped and judged on their own and appended to the first pass
  (owner ruling A, slice 10) — with no second omission check. Exactly
  one re-ask per phase per item: the phases enforce their own budgets;
  the runner never adds a retry.
- P4 narrate, P5 judge narration per story, `publishers=` passed.
- P6 merge per story against the beats already at its place in the
  city's file: no beat there → the story is new without a merge call
  (logged `merge_skipped`); a held outcome → a D13 queue item, nothing
  applied; otherwise `merge.apply`, then P4/P5 run again for every id in
  `MergeOutcome.rerun` before P7 (every beat a claim folded into counts as
  changed). A `new` story's record is assembled here:
  `beat_id = city/slug(place)/story_slug`, the story's enrichment fields,
  P5's narration, its duration and review. When some of its claims folded
  into existing beats, the record holds only the unmatched ones and is
  narrated again (P4/P5); left below its arc minimum it is a merge queue
  item instead, and with nothing left it writes no beat (`merge_absorbed`). A story at a new place
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
from src.ingest.decompose import P1_MAX_TOKENS, P1_PLAN, ClaimDraft, UnitHeld, decompose
from src.ingest.group import P2_MAX_TOKENS, P2_PLAN, Enrichment, Story, group
from src.ingest.jobs import IngestJob, IngestJobStore
from src.ingest.judge_claims import (
    OMISSIONS_PLAN,
    P3_MAX_TOKENS,
    P3_OMISSIONS_MAX_TOKENS,
    P3_PLAN,
    P3_RESTATE_MAX_TOKENS,
    JudgedClaim,
)
from src.ingest.judge_narration import P5_MAX_TOKENS, P5_PLAN, JudgedNarration
from src.ingest.merge import P6_MAX_TOKENS, P6_PLAN
from src.ingest.narrate import P4_MAX_TOKENS, P4_PLAN, BeatHeld, NarrationDraft
from src.ingest.unit import Unit, load_unit

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = REPO_ROOT / "data"

#: Every phase's plan with the FAN-OUT its rows are priced by (the phase
#: modules document it: P3 per claim, P4/P6 per story, P5 per sentence,
#: P1/P2/omissions per unit). Slice 9's judge caught the runner pricing
#: every row as one call per unit, which made the printed figure a floor.
PLAN_ROWS: tuple[tuple[llm.PhaseCall, str], ...] = (
    *((row, "unit") for row in P1_PLAN),
    *((row, "unit") for row in P2_PLAN),
    *((row, "claims") for row in P3_PLAN),
    *((row, "unit") for row in OMISSIONS_PLAN),
    *((row, "stories") for row in P4_PLAN),
    *((row, "sentences") for row in P5_PLAN),
    *((row, "stories") for row in P6_PLAN),
)

#: The flat plan, for callers that only need the rows.
PLAN: tuple[llm.PhaseCall, ...] = tuple(row for row, _kind in PLAN_ROWS)

#: The max_tokens each plan row's call actually asks for, aligned with
#: PLAN/PLAN_ROWS by index. A row prices its EXPECTED output (what a run
#: is projected to produce); the cap is the headroom the call reserves
#: for thinking plus the answer, and it is what bounds spend.
CAPS: tuple[int, ...] = (
    *(P1_MAX_TOKENS for _row in P1_PLAN),
    *(P2_MAX_TOKENS for _row in P2_PLAN),
    P3_MAX_TOKENS,
    P3_RESTATE_MAX_TOKENS,
    P3_MAX_TOKENS,
    *(P3_OMISSIONS_MAX_TOKENS for _row in OMISSIONS_PLAN),
    *(P4_MAX_TOKENS for _row in P4_PLAN),
    P5_MAX_TOKENS,
    P4_MAX_TOKENS,
    P5_MAX_TOKENS,
    *(P6_MAX_TOKENS for _row in P6_PLAN),
)

#: How often each plan row's call actually fires, aligned with PLAN:
#: first asks always; re-ask rows at the rate MEASURED on the first real
#: job (2026-09-12, LP Upper East Side): the omission re-ask and the P2
#: redo fired on the one unit (1.0); 9 of ~100 claims were refused once
#: (0.09, restate + re-judge); 5 of 71 narration sentences (0.07, revise +
#: re-judge); no narration failed its gate (P4 redo 0.0); the P6 re-ask
#: is a stated projection (0.1) until a merge runs.
REASK_RATES: tuple[float, ...] = (
    1.0, 1.0,  # P1 first ask, omission re-ask
    1.0, 1.0,  # P2 group, redo
    1.0, 0.09, 0.09,  # P3 judge, restate, re-judge
    1.0,  # omissions
    1.0, 0.0,  # P4 narrate, redo
    1.0, 0.07, 0.07,  # P5 judge, revise, re-judge
    1.0, 0.1,  # P6 merge, re-ask
)


def expected_usd(estimate: llm.CostEstimate) -> float:
    """What a run is expected to cost: every row at its expected output,
    weighted by how often its call fires (REASK_RATES)."""
    return sum(
        row.usd * rate for row, rate in zip(estimate.rows, REASK_RATES, strict=True)
    )



def _price_rows(rows: Sequence[llm.PhaseCost], output_tokens_of) -> float:
    """Price rows at the estimate's own table and batch discount, with the
    output tokens `output_tokens_of(row)` says."""
    total = 0.0
    for row in rows:
        price_in, price_out = llm.PRICES_USD_PER_MTOK[row.model_id]
        usd = (row.input_tokens / 1_000_000) * price_in
        usd += (output_tokens_of(row) / 1_000_000) * price_out
        if row.batch:
            usd *= llm.BATCH_DISCOUNT
        total += usd
    return total


def projection_usd(estimate: llm.CostEstimate) -> float:
    """What the job is projected to cost: every row at its expected output."""
    return _price_rows(estimate.rows, lambda row: row.output_tokens)


def cap_bound_usd(estimate: llm.CostEstimate) -> float:
    """The true upper limit: every row at the max_tokens its call asks
    for. A run can legitimately bill past the projection; never past this."""
    caps = dict(zip((id(row) for row in estimate.rows), CAPS, strict=True))
    return _price_rows(estimate.rows, lambda row: row.calls * caps[id(row)])

#: The input each plan row's call actually carries, aligned with PLAN_ROWS:
#: only the P1 and P3 prompts embed the passage (`unit`); P2 sees every
#: claim of the unit (`claims_unit`); P4, P5 and P6 see one story's claims
#: (`claims_story`). Measured on slice 9's first real job (2026-09-12): a
#: P3 request carried 6,410 input tokens, a P5 request 418.
INPUT_BASIS: tuple[str, ...] = (
    *("unit" for _row in P1_PLAN),
    *("claims_unit" for _row in P2_PLAN),
    "unit",  # P3 judge
    "unit",  # P3 restate (the author sees the passage)
    "unit",  # P3 re-judge
    *("unit" for _row in OMISSIONS_PLAN),
    *("claims_story" for _row in P4_PLAN),
    *("claims_story" for _row in P5_PLAN),
    *("claims_story" for _row in P6_PLAN),
)

#: Measured 2026-09-12 on the Lonely Planet Upper East Side chunk: 82
#: claims from 130 source sentences (0.63 per sentence), 55 committed
#: claims in 20 stories (~3 per story, stories = claims // 4), 71
#: narration sentences judged for 55 claims (1.3 per claim), ~70 input
#: tokens per claim as P2/P4/P5 see it (a P5 request was 418 tokens with
#: its prompt).
CLAIMS_PER_SENTENCE: float = 0.63
CLAIMS_PER_STORY: int = 3
NARRATION_SENTENCES_PER_CLAIM: float = 1.3
CLAIM_TOKENS: int = 70


#: Which rows of PLAN (by index) are a phase's FIRST ask, as opposed to
#: the re-ask rows every plan prices as if each item were refused once.
#: The first-pass sum is the expected spend when nothing is refused; the
#: whole plan is the projection with every re-ask; CAPS bound it.
FIRST_PASS: tuple[bool, ...] = tuple(
    index == 0
    for plan in (P1_PLAN, P2_PLAN, P3_PLAN, OMISSIONS_PLAN, P4_PLAN, P5_PLAN, P6_PLAN)
    for index in range(len(plan))
)


def fanout(unit: Unit) -> dict[str, int]:
    """The fan-out a unit is priced by, from its own sentence count and the
    ratios measured on the first real job (CLAIMS_PER_SENTENCE,
    NARRATION_SENTENCES_PER_CLAIM, stories = claims // 4). Named in the
    cost_estimate event, measured again by every run's summary — a
    projection, not a bound."""
    n = max(1, len(judge_narration.sentences(unit.text)))
    claims = max(1, round(n * CLAIMS_PER_SENTENCE))
    return {
        "claims": claims,
        "stories": max(1, claims // 4),
        "sentences": max(1, round(claims * NARRATION_SENTENCES_PER_CLAIM)),
    }


def _scaled_plan(fan: dict[str, int]) -> list[llm.PhaseCall]:
    return [
        llm.PhaseCall(
            phase=row.phase,
            role=row.role,
            calls_per_unit=row.calls_per_unit * (1 if kind == "unit" else fan[kind]),
            overhead_tokens=row.overhead_tokens,
            expected_output_tokens=row.expected_output_tokens,
        )
        for row, kind in PLAN_ROWS
    ]


def _sum_estimates(parts: Sequence[llm.CostEstimate]) -> llm.CostEstimate:
    """Row-wise sum of per-unit estimates (every part prices the same plan,
    so rows align by index)."""
    rows: list[llm.PhaseCost] = []
    for index in range(len(parts[0].rows)):
        same = [part.rows[index] for part in parts]
        first = same[0]
        rows.append(
            llm.PhaseCost(
                phase=first.phase,
                role=first.role,
                model_id=first.model_id,
                batch=first.batch,
                calls=sum(r.calls for r in same),
                input_tokens=sum(r.input_tokens for r in same),
                output_tokens=sum(r.output_tokens for r in same),
                usd=sum(r.usd for r in same),
            )
        )
    return llm.CostEstimate(
        units=sum(p.units for p in parts),
        rows=rows,
        total_input_tokens=sum(p.total_input_tokens for p in parts),
        total_output_tokens=sum(p.total_output_tokens for p in parts),
        total_usd=sum(p.total_usd for p in parts),
        prices_cached_on=parts[0].prices_cached_on,
    )


def estimate_job(client: llm.ModelClient, units: Sequence[Unit]) -> llm.CostEstimate:
    """Price the whole job: each unit under its own fan-out, every row on
    the input its call actually carries (INPUT_BASIS), summed. The
    `unit`-basis rows are priced by the client over the unit's real token
    count (which also arms the client's estimate gate, one `estimate` per
    unit); the claims-basis rows are priced here over the measured claim
    sizes, at the same table and batch discount."""
    if not units:
        return client.estimate([], list(PLAN))
    parts = []
    for unit in units:
        fan = fanout(unit)
        priced = client.estimate([unit.text], _scaled_plan(fan))
        rows = []
        for row, call, basis in zip(priced.rows, PLAN, INPUT_BASIS, strict=True):
            if basis == "unit":
                rows.append(row)
                continue
            basis_tokens = (
                fan["claims"] * CLAIM_TOKENS
                if basis == "claims_unit"
                else CLAIMS_PER_STORY * CLAIM_TOKENS
            )
            input_tokens = row.calls * (basis_tokens + call.overhead_tokens)
            price_in, price_out = llm.PRICES_USD_PER_MTOK[row.model_id]
            usd = (input_tokens / 1_000_000) * price_in
            usd += (row.output_tokens / 1_000_000) * price_out
            if row.batch:
                usd *= llm.BATCH_DISCOUNT
            rows.append(
                llm.PhaseCost(
                    phase=row.phase, role=row.role, model_id=row.model_id, batch=row.batch,
                    calls=row.calls, input_tokens=input_tokens,
                    output_tokens=row.output_tokens, usd=usd,
                )
            )
        parts.append(
            llm.CostEstimate(
                units=1,
                rows=rows,
                total_input_tokens=sum(r.input_tokens for r in rows),
                total_output_tokens=sum(r.output_tokens for r in rows),
                total_usd=sum(r.usd for r in rows),
                prices_cached_on=priced.prices_cached_on,
            )
        )
    return _sum_estimates(parts)

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
    filenames: list[str] = []
    for entry in manifest["chunks"]:
        filename = entry.get("filename") if isinstance(entry, dict) else None
        if not filename:
            raise JobRefused(f"manifest {manifest_path} names a chunk without a filename")
        filenames.append(filename)
    if job.source.chunks is not None:
        wanted = {Path(name).stem for name in job.source.chunks}
        known = {Path(name).stem for name in filenames}
        unknown = sorted(wanted - known)
        if unknown:
            raise JobRefused(
                f"manifest {manifest_path} does not name chunk(s) {unknown}; "
                f"it names {sorted(known)}"
            )
        filenames = [name for name in filenames if Path(name).stem in wanted]
    units: list[Unit] = []
    for filename in filenames:
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


def _unique_slug(story: Story, taken: set[str]) -> Story:
    """`story`, its slug suffixed `-2`, `-3`, ... if the unit already has it
    (an omission re-ask's story grouped apart from the first pass); `taken`
    gains the slug used."""
    slug, n = story.story_slug, 2
    while slug in taken:
        slug, n = f"{story.story_slug}-{n}", n + 1
    taken.add(slug)
    return story if slug == story.story_slug else story.model_copy(update={"story_slug": slug})


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
        """Print the job's cost estimate — the projection over every phase's
        plan — as a job event, arming the client's estimate gate."""
        estimate = estimate_job(self.client, units)
        self.emit(
            "cost_estimate",
            {**estimate.as_dict(), "fanout": {u.key: fanout(u) for u in units}},
        )

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
                # One P1 re-ask for the unit, for the omitted facts ONLY
                # (owner ruling A, slice 10): the new claims are grouped and
                # judged on their own and appended; the first pass is never
                # re-run, and a re-ask that fails keeps it. No second
                # omission check (one re-ask per phase per item).
                reasked.append(unit.key)
                try:
                    added = decompose(
                        unit, self.client, events=self.emit, omitted=found, existing=unit_claims
                    )
                    added_stories = (
                        group(added, unit, pois, self.client, events=self.emit) if added else []
                    )
                except UnitHeld:
                    added, added_stories = [], []
                taken = {story.story_slug for story in unit_stories}
                added_stories = [_unique_slug(story, taken) for story in added_stories]
                if added_stories:
                    unit_judged = {
                        **unit_judged,
                        **self._judge_stories(unit, added, added_stories),
                    }
                    unit_claims = [*unit_claims, *added]
                    unit_stories = [*unit_stories, *added_stories]
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
                try:
                    narrations[key] = judge_narration.judge_narration(
                        story, drafts[key], claims, self.client, events=self.emit,
                        publishers=publishers,
                    )
                except BeatHeld as held:
                    # One story the judge cannot answer is held, never the job
                    # (slice 9 job 2 attempt 3 died on an uncaught hold in P6).
                    self._queue_held_narration(story, claims, held.reason, phase="P5")
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
            # Whole, so a held story can be re-narrated without paying for
            # P1-P3 again (slice 9 job 1 run 4 lost four stories).
            "judged_claims": [_dump(c) for c in claims],
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

    def _queue_unmerged(
        self, story: Story, claims: Sequence[JudgedClaim], reason: str,
        narration: JudgedNarration,
    ) -> None:
        """A story P6 could not merge at all (the judge's answer never met the
        contract, or its call failed): held as a merge item, nothing applied."""
        shown = {
            "story": _dump(story),
            "claims": [c.draft.text for c in claims],
            "judged_claims": [_dump(c) for c in claims],
            "narration": narration.narration.text,
            "reason": reason,
        }
        self.store.queue(
            job_id=self.job.id, city=self.job.city, kind="merge_held",
            story_slug=story.story_slug, place=story.place, reason=reason, shown=shown,
            held_back={"claims": len(claims), "duration_sec": narration.duration_sec},
        )

    def _queue_held_merge(
        self, story: Story, claims: Sequence[JudgedClaim], outcome: merge.MergeOutcome,
        narration: JudgedNarration,
    ) -> None:
        shown = {
            "story": _dump(story),
            "claims": [c.draft.text for c in claims],
            "judged_claims": [_dump(c) for c in claims],
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
    ) -> tuple[model.Beat | None, str]:
        """P4/P5 again for a beat whose resolved texts changed. `(None,
        reason)` when no judged narration comes back (held at P4 or P5): the
        caller keeps the beat as it was on disk — a merged beat carrying its
        old narration has a stale claims_hash and P7's validator would refuse
        the whole file — and queues the stories whose merges it dropped."""
        story = story_of_beat(beat)
        claims = judged_of_beat(beat)
        try:
            draft = narrate.narrate(story, claims, self.client, events=self.emit,
                                    publishers=publishers)
        except BeatHeld as held:
            return None, f"P4: {held.reason}"
        try:
            judged = judge_narration.judge_narration(
                story, draft, claims, self.client, events=self.emit, publishers=publishers
            )
        except BeatHeld as held:
            return None, f"P5: {held.reason}"
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
        ), ""

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
        # Who touched which place, per unit, and which stories merged into
        # which beat — so a merge dropped by a failed rerun counts as nothing.
        touched_by: dict[str, dict[str, set[str]]] = {}
        merged_into: dict[str, list[tuple[str, Story, list[JudgedClaim], JudgedNarration]]] = {}
        for unit in units:
            touched = touched_by.setdefault(unit.key, {})
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
                    touched.setdefault(story.place, set()).add(story.story_slug)
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
                    try:
                        outcome = merge.merge(
                            story, claims, candidates, self.client, events=self.emit
                        )
                    except BeatHeld as held:
                        # Slice 9 job 2 attempt 3 (2026-09-15): an invalid merge
                        # answer, twice, raised here and ended a ten-hour job.
                        self._queue_unmerged(story, claims, held.reason, narration)
                        continue
                    if outcome.held:
                        self._queue_held_merge(story, claims, outcome, narration)
                        continue
                folds = (
                    [o for o in outcome.claims if o.existing_beat_id is not None]
                    if outcome is not None else []
                )
                if outcome is not None and (outcome.story != "new" or folds):
                    existing = merge.apply(outcome, existing)
                    applied.append(story.story_slug)
                    targets = {o.existing_beat_id for o in folds}
                    if outcome.beat_id:
                        targets.add(outcome.beat_id)
                    for target in sorted(targets):
                        changed_ids.add(target)
                        merged_into.setdefault(target, []).append(
                            (unit.key, story, list(outcome.judged or claims), narration)
                        )
                    rerun.extend(i for i in outcome.rerun if i not in rerun)
                if outcome is None or outcome.story == "new":
                    new_story, kept = story, list(claims)
                    if folds:
                        # Its matched claims now live in the beats that hold
                        # them; its own beat is the rest, narrated again.
                        unmatched = {o.claim_id for o in outcome.claims if o.outcome == "new"}
                        kept = [c for c in claims if c.draft.claim_id in unmatched]
                        new_story = story.model_copy(
                            update={"claim_ids": [c.draft.claim_id for c in kept]}
                        )
                        if not kept:
                            self.emit(
                                "merge_absorbed",
                                {"story_slug": story.story_slug, "place": story.place},
                            )
                            touched.setdefault(story.place, set()).add(story.story_slug)
                            continue
                        if model.arc_too_short(story.beat_type, len(kept)):
                            # Written, it would make P7 refuse the whole file.
                            self._queue_unmerged(
                                new_story, kept,
                                f"after folding, {len(kept)} claim is below the arc "
                                f"minimum for beat_type {story.beat_type!r}",
                                narration,
                            )
                            continue
                    beat = assemble_record(self.job.city, new_story, kept, narration)
                    if folds:
                        renarrated, reason = self._rerun(beat, publishers)
                        if renarrated is None:
                            self._queue_unmerged(
                                new_story, kept,
                                f"its unmatched claims could not be re-narrated ({reason})",
                                narration,
                            )
                            continue
                        beat = renarrated
                    if any(b.beat_id == beat.beat_id for b in [*existing, *new_records]):
                        self.emit(
                            "beat_id_collision",
                            {"story_slug": story.story_slug, "beat_id": beat.beat_id},
                        )
                        continue
                    new_records.append(beat)
                    extracted += 1
                touched.setdefault(story.place, set()).add(story.story_slug)
            per_unit[unit.key] = {"chunk": unit.chunk, "beats_extracted": extracted}
        reverted: set[str] = set()
        if rerun:
            self.emit("rerun", {"beat_ids": list(rerun)})
            kept: list[model.Beat] = []
            for b in existing:
                if b.beat_id not in rerun:
                    kept.append(b)
                    continue
                renarrated, reason = self._rerun(b, publishers)
                if renarrated is not None:
                    kept.append(renarrated)
                    continue
                reverted.add(b.beat_id)
                kept.append(model.Beat.model_validate(raw_by_id[b.beat_id]))
                for unit_key, story, story_claims, narration in merged_into.get(b.beat_id, []):
                    self._queue_unmerged(
                        story, story_claims,
                        f"merge into {b.beat_id} dropped: its re-narration was held ({reason})",
                        narration,
                    )
                    if story.story_slug in applied:
                        applied.remove(story.story_slug)
                    touched_by[unit_key].get(story.place, set()).discard(story.story_slug)
            existing = kept
            if reverted:
                self.emit("rerun_reverted", {"beat_ids": sorted(reverted)})
        for unit_key, entry in per_unit.items():
            entry["pois_touched"] = sorted(
                place for place, slugs in touched_by.get(unit_key, {}).items() if slugs
            )
        changed_ids.update(rerun)
        changed_ids -= reverted
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
                "changed": bool(new_records or applied or set(rerun) - reverted),
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
