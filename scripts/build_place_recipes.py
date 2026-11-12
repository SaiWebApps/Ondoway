"""Build stored place recipes for one city: ``make recipes-build CITY=new_york``.

Writes each place under each of its lenses through the tour writer, checks it with
the tour gates, voices each piece once and saves it to the local graph. A recipe the
graph already holds for the same beats, rules and voice is skipped at no cost.

  --limit-places N   build only the N most important places
  --poi-id ID        build only this place (repeatable)
  --lens LENS        build only this lens; "none" builds the no-lens telling
  --max-recipes N    stop after N recipes have been written or refused (default 10)
  --dry-run          plan only: report what would be built, spend nothing
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from dataclasses import asdict

from src.audio.pipeline import generate_stop_audio
from src.connection import create_driver, get_database
from src.tour.premium_tour import (
    AnthropicPremiumExecutor,
    exact_snapshot_sha256,
    resolve_build_identity,
)
from src.tour.recipe_build import (
    RecipeOutcome,
    build_recipes,
    pinned_voice_identity,
    places_to_build,
)
from src.tour.selection import load_paris_corpus
from src.tour.verify import HaikuFaithfulnessChecker


def voice_identity() -> str:
    """The voice every piece is stored in. Refuses a fallback voice, because a
    recipe whose pieces could be spoken by two different voices is not one telling."""

    if os.getenv("TTS_FALLBACK"):
        raise SystemExit("TTS_FALLBACK must be empty: a recipe is stored in one voice")
    identity = pinned_voice_identity()
    if identity is None:
        raise SystemExit("TTS_PROVIDER must name the voice provider")
    return identity


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--city", required=True, help="City slug, e.g. new_york")
    parser.add_argument("--limit-places", type=int, default=None)
    parser.add_argument("--poi-id", action="append", default=None)
    parser.add_argument("--lens", default=None)
    parser.add_argument("--max-recipes", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def run(
    args: argparse.Namespace,
    *,
    stop_when: Callable[[RecipeOutcome], bool] | None = None,
) -> list[RecipeOutcome]:
    """Run the build job and print one line per recipe. ``stop_when`` lets a caller
    that measures spend stop the job between recipes."""

    voice_id = voice_identity()
    driver = create_driver()
    try:
        snapshot = load_paris_corpus(driver, city_slug=args.city)
        places = [
            poi for poi in places_to_build(snapshot) if args.poi_id is None or poi.id in args.poi_id
        ][: args.limit_places]
        paid = 0

        def should_continue(outcome: RecipeOutcome) -> bool:
            nonlocal paid
            paid += 1
            if stop_when is not None and stop_when(outcome):
                return False
            return paid < args.max_recipes

        outcomes = []
        with driver.session(database=get_database()) as session:
            for outcome in build_recipes(
                session,
                snapshot,
                places,
                city_slug=args.city,
                snapshot_sha256=exact_snapshot_sha256(snapshot),
                executor=AnthropicPremiumExecutor(),
                faithfulness_checker=HaikuFaithfulnessChecker(),
                voice=generate_stop_audio,
                voice_identity=voice_id,
                build_identity=resolve_build_identity(),
                dry_run=args.dry_run,
                should_continue=should_continue,
                only_lens=None if args.lens is None else (_lens(args.lens),),
            ):
                outcomes.append(outcome)
                print(_line(outcome), flush=True)
        _summary(outcomes)
        return outcomes
    finally:
        driver.close()


def _lens(value: str) -> str | None:
    return None if value == "none" else value


def _line(outcome: RecipeOutcome) -> str:
    fields = asdict(outcome)
    lens = fields.pop("lens") or "no lens"
    head = f"{fields.pop('status'):8} {fields.pop('poi_name')} / {lens}"
    detail = (
        f"beats {outcome.seated_beats} told, {outcome.overflow_beats} left for later"
        if outcome.status in ("planned", "current")
        else f"attempts {outcome.attempts}, output tokens {outcome.output_tokens}, "
        f"pieces {outcome.pieces}, audio {outcome.audio_seconds}s"
    )
    reason = f" — {outcome.reason[:300]}" if outcome.reason else ""
    return f"{head}: {detail}{reason}"


def _summary(outcomes: list[RecipeOutcome]) -> None:
    counts: dict[str, int] = {}
    for outcome in outcomes:
        counts[outcome.status] = counts.get(outcome.status, 0) + 1
    tokens = sum(o.output_tokens for o in outcomes)
    print(f"\n{len(outcomes)} recipes: {counts}; writer output tokens {tokens}", flush=True)


def main(argv: list[str] | None = None) -> int:
    outcomes = run(parse_args(argv))
    return 1 if any(o.status == "refused" for o in outcomes) else 0


if __name__ == "__main__":
    sys.exit(main())
