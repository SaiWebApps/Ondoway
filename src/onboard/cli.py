"""Step-4 onboarding CLI — assemble a city + draft its beats, end to end.

Runs the same Step-3 → Step-4 flow the live onboarding API will drive, but in
FIXTURE mode: the connectors' network door (``src.onboard.fetch``) defaults to
``fixture`` and raises on any real GET, so this CLI never touches the network.
Instead it loads each connector's committed run fixture from
``tests/fixtures/onboard/{city}/`` and calls the connector's PURE ``parse``
directly — emitting the same ``source_consult`` / ``candidate_batch`` events to a
``JobStore`` that ``base.run_connector`` emits live, so the flow mirrors the real
run.

The pipeline:

1. Geocode (fixture mode = ``run/city.json``) → ``CityContext``.
2. Parse each license-clean connector over its fixture → ``ConnectorResult`` list.
3. Build pinned ``WikiExtract``s from ``run/wikipedia_extracts.json``.
4. ``assemble`` → geofenced, gravity-tiered POIs (pure, no I/O).
5. ``beat_draft.draft_all`` → beats grounded in the pinned Wikipedia revisions
   (mock provider by default → free; a live provider needs ``--confirm-cost``).
6. ``write_city`` → the ``data/{slug}/`` corpus + registry entry (honouring
   ``ONBOARD_DATA_ROOT`` / ``ONBOARD_REGISTRY_PATH`` so the bar writes to a tmp
   root, never the committed ``data/`` or ``src/cities.json``).
7. Write ``beats.json`` beside the corpus and gate it through
   ``validate_beats.validate`` — non-zero exit on any error.

``--dry-run`` is an ESTIMATE-ONLY short-circuit: it runs steps 1-4 (geocode →
consult each source → ``assemble`` with EMPTY extracts) and then PRINTS the POI
count, per-POI summary, tier histogram, and the ESTIMATED beat-drafting cost —
then exits 0 WITHOUT drafting any beats (zero LLM spend) or writing anything to
disk (no corpus, no registry). It honours ``ONDOWAY_ONBOARD_HTTP``, so
``ONDOWAY_ONBOARD_HTTP=live`` fetches REAL source data while still spending and
writing nothing. It deliberately skips ``build_extracts`` (a dry run needs no
Wikipedia extracts), so a live dry run never has to walk the extract-fetch path.

Usage:
    python -m src.onboard.cli --city london --modes license_clean [--confirm-cost]
    python -m src.onboard.cli --city london --modes license_clean --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

from scripts import validate_beats
from src.onboard.assemble import assemble, write_city
from src.onboard.flow import (
    FIXTURES_ROOT,
    OnboardError,
    build_extract_index,
    consult_source,
    resolve_connectors,
)
from src.onboard.jobs import get_store
from src.onboard.models import CityContext

# The drafter emits ~3 beats per POI (each POI's Wikipedia lead is split into
# several verbatim spans). A --dry-run deliberately skips extract fetching, so it
# cannot count the exact spans; it estimates over this per-POI multiplier as an
# UPPER BOUND ("up to ~3 beats/POI") so the printed cost is never ~3x low.
_DRY_RUN_BEATS_PER_POI = 3


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (tmp sibling + ``os.replace``)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _geocode(city: str) -> CityContext:
    """Fixture-mode geocode: read ``run/city.json`` into a ``CityContext``.

    (Live geocoding is a later step; here the city frame comes from the committed
    fixture, so the run is deterministic and network-free.)
    """
    city_json = FIXTURES_ROOT / city / "run" / "city.json"
    if not city_json.exists():
        raise OnboardError(f"no city fixture for {city!r}: {city_json} not found")
    data = _load_json(city_json)
    return CityContext(
        slug=data["slug"],
        display_name=data["display_name"],
        bbox=tuple(data["bbox"]),
        # The country the city's opening hours are read against (Docs/adr/0006).
        # Absent from an older fixture, which `write_city` then refuses by name.
        country=data.get("country"),
    )


def _load_beat_drafter():
    """Import the parallel-built beat drafter, or exit with a clear message.

    ``src/onboard/beat_draft.py`` is built alongside this CLI; if it has not
    landed yet the run stops with a distinct exit code so the caller re-runs once
    it does, rather than failing obscurely mid-flow.
    """
    try:
        from src.onboard.beat_draft import CostNotConfirmed, draft_all
    except ImportError as exc:
        print(
            "src/onboard/beat_draft.py is not importable yet "
            f"(sibling drafter still building?): {exc}",
            file=sys.stderr,
        )
        raise SystemExit(3) from exc
    return draft_all, CostNotConfirmed


def _env_path(name: str) -> Path | None:
    """The ``Path`` in env var ``name``, or ``None`` if unset/empty."""
    value = os.getenv(name)
    return Path(value) if value else None


def run(city: str, modes: list[str], *, confirm_cost: bool, dry_run: bool = False) -> int:
    """Run the full onboarding flow for ``city``. Returns a process exit code.

    ``dry_run=True`` short-circuits to the estimate-only path: fetch + assemble,
    print the POI count + beat-drafting cost estimate, then exit 0 — no beats
    drafted (zero LLM spend), nothing written to disk or the registry.
    """
    if dry_run:
        return _run_dry_run(city, modes)

    connector_slugs = resolve_connectors(modes)
    ctx = _geocode(city)

    store = get_store()
    job = store.create(ctx.slug, modes)
    store.set_status(job.id, "running")

    results = [consult_source(slug, ctx, store, job.id) for slug in connector_slugs]
    # Assemble FIRST so the merged/filtered POI names are final, THEN fetch each
    # POI's Wikipedia extract keyed to those names (a live run must ground against
    # the real POIs, not the raw candidates). Fixture mode ignores the POIs arg and
    # reads the committed extracts fixture — same result as the prior ordering.
    assembled = assemble(results, ctx, [])
    assembled.wiki_extracts = build_extract_index(city, assembled.pois)
    store.set_status(job.id, "assembled")

    draft_all, cost_not_confirmed = _load_beat_drafter()
    store.set_status(job.id, "drafting")
    try:
        beats = draft_all(assembled.pois, assembled.wiki_extracts, confirm=confirm_cost)
    except cost_not_confirmed as exc:
        print(
            f"beat drafting needs cost confirmation — re-run with --confirm-cost: {exc}",
            file=sys.stderr,
        )
        return 4

    # write_city honours ONBOARD_DATA_ROOT for the corpus root; it does NOT read
    # ONBOARD_REGISTRY_PATH, so the CLI resolves that env itself and passes it in
    # — without it, register_city would touch the real src/cities.json.
    city_dir = write_city(
        assembled,
        ctx,
        data_root=_env_path("ONBOARD_DATA_ROOT"),
        registry_path=_env_path("ONBOARD_REGISTRY_PATH"),
    )

    beats_path = city_dir / "beats.json"
    _atomic_write_text(beats_path, json.dumps(beats, indent=2, ensure_ascii=False) + "\n")

    errors = validate_beats.validate(beats_path)
    registry_path = _env_path("ONBOARD_REGISTRY_PATH")

    _print_summary(ctx, assembled, beats, city_dir, registry_path, errors)
    return 1 if errors else 0


def _print_summary(
    ctx: CityContext,
    assembled,
    beats: list[dict],
    city_dir: Path,
    registry_path: Path | None,
    errors: list[str],
) -> None:
    """Print the run summary: POI count, tier histogram, beats, registry, gate."""
    hist = Counter(p["importance_tier"] for p in assembled.pois)
    tier_line = "  ".join(f"T{tier}={hist[tier]}" for tier in sorted(hist, reverse=True))

    print("─" * 60)
    print(f"onboard-city: {ctx.display_name} ({ctx.slug})")
    print(f"  POIs:        {len(assembled.pois)}")
    print(f"  tiers:       {tier_line}")
    print(f"  extracts:    {len(assembled.wiki_extracts)} pinned Wikipedia revisions")
    print(f"  beats:       {len(beats)}")
    print(f"  city dir:    {city_dir}")
    print(f"  registry:    {registry_path or '(default src/cities.json)'}")
    if errors:
        print(f"  validate:    FAIL ({len(errors)} issue(s))")
        for line in errors:
            print(f"    - {line}")
    else:
        print("  validate:    PASS")
    print("─" * 60)


def _run_dry_run(city: str, modes: list[str]) -> int:
    """ESTIMATE-ONLY path: fetch + assemble, then PRINT the POI count, per-POI
    summary, tier histogram, and the ESTIMATED beat-drafting cost — WITHOUT
    drafting any beats (zero LLM spend) or writing anything to disk/registry.

    Honours ``ONDOWAY_ONBOARD_HTTP`` via ``consult_source`` (``=live`` fetches
    real data). ``assemble`` is passed EMPTY extracts on purpose: a dry run needs
    no Wikipedia extracts, and ``build_extracts`` is deliberately NOT called, so a
    live dry run never has to walk the extract-fetch path. Returns 0.
    """
    # estimate_cost is pure (no anthropic import at module load); the drafter it
    # sizes is NEVER constructed here — that is the whole point of the dry run.
    from src.onboard.beat_draft import estimate_cost

    connector_slugs = resolve_connectors(modes)
    ctx = _geocode(city)

    store = get_store()
    job = store.create(ctx.slug, modes)
    store.set_status(job.id, "running")

    results = [consult_source(slug, ctx, store, job.id) for slug in connector_slugs]
    assembled = assemble(results, ctx, [])  # EMPTY extracts — no build_extracts call
    store.set_status(job.id, "assembled")

    # No extracts were fetched, so size the estimate over the UPPER BOUND of
    # ~3 beats/POI — never one-beat-per-POI, which under-reports the true cost ~3x.
    poi_count = len(assembled.pois)
    estimate = estimate_cost(poi_count * _DRY_RUN_BEATS_PER_POI)
    _print_dry_run(ctx, assembled, estimate, poi_count)
    return 0


def _print_dry_run(ctx: CityContext, assembled, estimate: dict, poi_count: int) -> None:
    """Print the dry-run report: POI count, per-POI summary (by tier desc then
    name), tier histogram, and the ESTIMATED (not-yet-spent) drafting cost.

    The beat count is an UPPER-BOUND estimate (``up to ~3 beats/POI``): a dry run
    never fetches the Wikipedia extracts, so it cannot count the exact spans."""
    pois = assembled.pois
    hist = Counter(p["importance_tier"] for p in pois)
    tier_line = "  ".join(f"T{tier}={hist[tier]}" for tier in sorted(hist, reverse=True))

    print("─" * 60)
    print("onboard-city DRY RUN — estimate only (no beats drafted, nothing written)")
    print(f"  city:        {ctx.display_name} ({ctx.slug})")
    print(f"  POIs:        {len(pois)}")
    print(f"  tiers:       {tier_line}")
    print("─" * 60)
    print("  POIs (by importance tier, then name):")
    for poi in sorted(pois, key=lambda p: (-p["importance_tier"], p["name"])):
        print(
            f"    T{poi['importance_tier']}  {poi['name']}  "
            f"({poi['latitude']:.5f}, {poi['longitude']:.5f})"
        )
    print("─" * 60)
    print("  ESTIMATED beat-drafting cost (NOT yet spent):")
    print(f"    model:              {estimate['model']}")
    print(f"    POIs:               {poi_count}")
    print(
        f"    beats (estimated):  {estimate['beats']}  "
        f"(up to ~{_DRY_RUN_BEATS_PER_POI} beats/POI — exact count known after extract fetch)"
    )
    print(f"    est_input_tokens:   {estimate['est_input_tokens']}")
    print(f"    est_output_tokens:  {estimate['est_output_tokens']}")
    print(f"    est_usd:            ${estimate['est_usd']}")
    print("─" * 60)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.onboard.cli",
        description="Onboard a city end to end (fixture + mock in the bar).",
    )
    parser.add_argument("--city", required=True, help="city slug, e.g. london")
    parser.add_argument(
        "--modes",
        required=True,
        help="comma-separated onboarding modes (Step 4 handles: license_clean)",
    )
    parser.add_argument(
        "--confirm-cost",
        action="store_true",
        help="confirm paid beat-drafting (needed only for a live ONBOARD_PROVIDER)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "ESTIMATE ONLY: fetch + assemble, print the POI count + beat-drafting "
            "cost estimate, then exit — draft NO beats (zero LLM spend) and write "
            "NOTHING to disk or the registry"
        ),
    )
    args = parser.parse_args(argv)
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    if not modes:
        parser.error("--modes must name at least one mode")

    try:
        return run(args.city, modes, confirm_cost=args.confirm_cost, dry_run=args.dry_run)
    except OnboardError as exc:
        print(f"onboard-city: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
