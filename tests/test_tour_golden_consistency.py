"""Internal consistency of the golden fixtures — no database, no provider, no cost.

The two golden fixtures used to pin their expected beats as the per-database UUIDs
Neo4j mints at upload (``randomUUID()``). Those ids are ephemeral: none of them exists
in ``data/paris/beats.json``, whose durable identity is the slug (``beat_id`` in the
corpus file, ``stable_beat_id`` on a ``BeatRef``). That forced ``beat_overlap`` to 0.0
no matter what the engine did, and it is why re-recording fresh UUIDs is laundering
rather than repair.

This module is the cheap guard that the fixtures stay keyed to durable ids and stay
internally coherent. It reads the two source documents the fixtures were hand-composed
from, so "the fixture covers the tour" is checked against the tour, not against the
fixture's own bookkeeping.

Tag inventory parsing is deliberately structural (quoted lines, a bold ``**[...]``
block, ``+``-joined tags, a trailing ``— note`` stripped). No regular expressions: a
pattern written against one document silently returned zero matches on the other.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "fixtures" / "tour_golden"
CORPUS_PATH = REPO_ROOT / "data" / "paris" / "beats.json"

# Each fixture and the hand-composed empirical tour document it was written from.
GOLDEN_DOCUMENTS = {
    "ile_oneway_90min": "fixtures/reference-tours/02-ile-de-la-cite-notre-dame.md",
    "pdv_round_trip_60min": "fixtures/reference-tours/01-place-des-vosges.md",
}

_HEX = set("0123456789abcdefABCDEF")

# RESOLUTION RATCHET — may only ever go UP.
#
# The partition invariant below (every document tag is either resolved or queued)
# is satisfiable by queueing EVERYTHING: a fixture that resolves zero tags still
# partitions cleanly. Measured 2026-07-30, that hole was live — the gate passed on
# an Ile fixture with `expected_stable_beat_ids: []` and every tag queued. These
# floors are the actual coverage achieved at repair time, so a later change cannot
# quietly retreat into the queue. Raise them when resolution improves; never lower
# them to make a regression pass.
MIN_RESOLVED_TAGS = {
    "ile_oneway_90min": 51,  # of 70 unique document tags
    "pdv_round_trip_60min": 24,  # of 25
}


def looks_like_a_uuid(value: str) -> bool:
    """8-4-4-4-12 hex with dashes — the shape Neo4j's ``randomUUID()`` emits."""
    if len(value) != 36:
        return False
    if [i for i, ch in enumerate(value) if ch == "-"] != [8, 13, 18, 23]:
        return False
    return all(ch in _HEX for ch in value if ch != "-")


def _split_tag_block(block: str) -> list[str]:
    tags = []
    for piece in block.split("+"):
        tag = piece.strip()
        if " — " in tag:  # e.g. "V:palais_de_justice_marcel_revolt — anchor"
            tag = tag.split(" — ", 1)[0].strip()
        if tag:
            tags.append(tag)
    return tags


def document_tags(doc_path: Path) -> list[str]:
    """Every beat tag the document attributes narration to, in document order."""
    tags: list[str] = []
    for raw in doc_path.read_text().splitlines():
        line = raw.strip()
        if line.startswith(">") and "**[" in line:
            tail = line.partition("**[")[2]
            assert "]" in tail, f"unterminated tag block: {line}"
            tags.extend(_split_tag_block(tail.split("]", 1)[0]))
        elif line.startswith("`[GLUE:") and line.count("[") > 1:
            after_glue = line.split("]", 1)[1]
            if "[" in after_glue:
                tags.extend(_split_tag_block(after_glue.split("[", 1)[1].split("]", 1)[0]))
    assert tags, f"parsed zero tags out of {doc_path}"
    return tags


def unreachable_tag_set(structurally_unreachable: dict) -> tuple[set[str], list[str]]:
    """Tags the algorithm provably cannot emit today, minus the RECOVERED groups.

    Returns the tag set plus a list of malformed group labels: every group must be
    ``{"tags": [...], "reason": ...}``, never a bare per-tag entry carrying a UUID.
    """
    out: set[str] = set()
    malformed: list[str] = []
    for label, group in structurally_unreachable.items():
        if group.get("_status") == "RECOVERED":
            continue
        tags = group.get("tags")
        if not isinstance(tags, list) or not tags:
            malformed.append(label)
            continue
        out |= set(tags)
    return out, malformed


def generated_stable_beat_ids(script, sequence) -> tuple[set[str], list[str]]:
    """Translate a Script's graph-UUID ``source_id``s into durable corpus slugs.

    ``BeatRef`` carries both identities side by side (``id`` is the per-database UUID
    the runtime uses, ``stable_beat_id`` is the corpus slug), so a slug-keyed fixture
    can be compared against real output with no production change at all.

    Returns the translated ids plus any beat ids the sequence could not translate —
    an untranslatable id is a real hole, not something to quietly drop.
    """
    stable_for: dict[str, str] = {}
    for poi in sequence.poi_beats:
        for beat in poi.beats:
            if beat.stable_beat_id:
                stable_for[beat.id] = beat.stable_beat_id
    for beats in sequence.vignette_beats.values():
        for beat in beats:
            if beat.stable_beat_id:
                stable_for[beat.id] = beat.stable_beat_id

    emitted = [s.source_id for s in script.script if s.source_type == "beat"]
    translated = {stable_for[bid] for bid in emitted if bid in stable_for}
    untranslated = sorted({bid for bid in emitted if bid not in stable_for})
    return translated, untranslated


def iter_strings(node: object):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from iter_strings(value)
    elif isinstance(node, list | tuple):
        for value in node:
            yield from iter_strings(value)


@pytest.fixture(scope="module")
def corpus_beat_ids() -> set[str]:
    return {beat["beat_id"] for beat in json.loads(CORPUS_PATH.read_text())}


@pytest.fixture(scope="module")
def goldens() -> dict[str, dict]:
    return {
        name: json.loads((FIXTURE_DIR / f"{name}.json").read_text()) for name in GOLDEN_DOCUMENTS
    }


# PINNED SET SIZES — the counterweight to MIN_RESOLVED_TAGS.
#
# The ratchet above rewards resolving more tags and has no ceiling, while the overlap
# floors in test_tour_golden_{ile,pdv}.py are absolute hit counts. Growing the pinned set
# therefore weakens those floors as a FRACTION with nobody deciding to: a hostile review
# proved that resolving Ile's 19 queued tags onto beats the engine already emits (there
# are 23 such extras) passes every invariant here, lifts overlap toward 41/50, and leaves
# the floor at 15 — restoring the very silent drift these pinned sizes exist to kill.
# Nothing in this module checks that a resolution is the SEMANTICALLY right beat, only
# that it exists in the corpus, so growth is cheap and unpoliced.
#
# So the sizes are pinned. Growing a pinned set is allowed, but not silently: it turns
# this RED and the same change must re-derive the overlap floor from a fresh measurement.
PINNED_SET_SIZES = {"ile_oneway_90min": 17, "pdv_round_trip_60min": 21}


def test_pinned_set_growth_forces_the_overlap_floor_to_be_rederived():
    """A bigger expectation must not quietly become a smaller bar.

    See PINNED_SET_SIZES. If you resolved more tags: good — update this count AND
    re-derive OVERLAP_MIN_HITS in the two golden modules from a fresh `make
    golden-probe`, in the same commit.
    """
    actual = {}
    for name in GOLDEN_DOCUMENTS:
        fixture = json.loads((FIXTURE_DIR / f"{name}.json").read_text())
        actual[name] = len(fixture["expected_stable_beat_ids"])
    assert actual == PINNED_SET_SIZES, (
        f"pinned-set size changed: {actual} vs the pinned {PINNED_SET_SIZES}. The overlap "
        "floors are ABSOLUTE hit counts, so changing these sizes changes the effective "
        "bar. Re-derive OVERLAP_MIN_HITS from a fresh measurement in the same change."
    )


def test_golden_diff_cli_reads_the_durable_key():
    """`make golden-diff` must not rot when the fixtures are re-keyed.

    scripts/tour_golden_diff.py cannot import this module (scripts/ ships in the
    Docker image, tests/ is .dockerignore'd), so it repeats the UUID->slug mapping
    and nothing links the two copies. Measured 2026-07-30: after the re-key the CLI
    still read the deleted ``expected_beat_ids`` and crashed with a bare KeyError,
    AFTER `make golden-diff` had already started the shared dev graph and Valhalla.
    Neither lint (ruff cannot see a wrong dict key, and the file is outside the lint
    list) nor any test covered it. This is that cover.
    """
    # Import it for real: a source-text check would pass on a module that raises on
    # import, which is the failure mode that matters for a CLI.
    import importlib

    module = importlib.import_module("scripts.tour_golden_diff")
    assert hasattr(module, "main"), "the golden-diff CLI lost its main() entry point"

    source = (REPO_ROOT / "scripts" / "tour_golden_diff.py").read_text()
    assert '"expected_stable_beat_ids"' in source, (
        "the golden-diff CLI does not read expected_stable_beat_ids — it will KeyError"
    )
    assert '"expected_beat_ids"' not in source, (
        "the golden-diff CLI still reads the deleted ephemeral-UUID key"
    )
    assert "stable_beat_id" in source, (
        "the CLI compares untranslated source_ids, so every overlap would read 0%"
    )
    # And the key it reads is actually present in both shipped fixtures.
    for fixture_name in GOLDEN_DOCUMENTS:
        fixture = json.loads((FIXTURE_DIR / f"{fixture_name}.json").read_text())
        assert fixture["expected_stable_beat_ids"], fixture_name


def test_fixture_ids_are_durable_slugs_and_internally_consistent(goldens, corpus_beat_ids):
    """Every pinned id is a durable corpus slug, and the fixture agrees with itself.

    Six invariants per fixture:
      1. no ephemeral UUID survives anywhere in the file;
      2. every resolved tag points at a beat that exists in the corpus;
      3. ``expected_stable_beat_ids`` is unique, sorted and all in the corpus;
      4. ``expected_beat_count`` is that list's length — not an unrelated number;
      5. the list is exactly the reachable resolutions (structurally unreachable tags
         are excluded, RECOVERED groups are not);
      6. resolved and unresolved tags partition the document's tag inventory, so a tag
         can never be silently dropped — it is either pinned or queued for review.
    """
    problems: list[str] = []

    for name, fixture in sorted(goldens.items()):
        doc_tags = document_tags(REPO_ROOT / GOLDEN_DOCUMENTS[name])
        resolution: dict[str, str] = fixture.get("expected_tag_resolution", {})
        unresolved: dict[str, str] = fixture.get("unresolved_tags", {})
        pinned: list[str] = fixture.get("expected_stable_beat_ids", [])

        # 1. no ephemeral UUID survives.
        uuids = sorted({s for s in iter_strings(fixture) if looks_like_a_uuid(s)})
        if uuids:
            problems.append(f"{name}: {len(uuids)} UUID-shaped ids remain, e.g. {uuids[:3]}")

        # 2. resolved tags point at real corpus beats.
        if "expected_stable_beat_ids" not in fixture:
            problems.append(f"{name}: no expected_stable_beat_ids — fixture not re-keyed")
        unknown = sorted({b for b in resolution.values() if b not in corpus_beat_ids})
        if unknown:
            problems.append(
                f"{name}: {len(unknown)} resolved ids absent from {CORPUS_PATH.name}, "
                f"e.g. {unknown[:3]}"
            )

        # 3-4. the pinned list is unique, sorted, real, and counted.
        if len(set(pinned)) != len(pinned):
            dupes = sorted({b for b in pinned if pinned.count(b) > 1})
            problems.append(f"{name}: expected_stable_beat_ids has duplicates: {dupes}")
        if pinned != sorted(pinned):
            problems.append(f"{name}: expected_stable_beat_ids is not sorted")
        missing = sorted(set(pinned) - corpus_beat_ids)
        if missing:
            problems.append(f"{name}: pinned ids absent from the corpus: {missing[:3]}")
        if fixture.get("expected_beat_count") != len(pinned):
            problems.append(
                f"{name}: expected_beat_count={fixture.get('expected_beat_count')} but "
                f"{len(pinned)} ids are pinned"
            )

        # 5. the pinned list is exactly the reachable resolutions.
        unreachable, malformed = unreachable_tag_set(fixture["structurally_unreachable"])
        if malformed:
            problems.append(
                f"{name}: structurally_unreachable groups without a 'tags' list: {malformed}"
            )
        stray = sorted(unreachable - set(doc_tags))
        if stray:
            problems.append(
                f"{name}: structurally_unreachable names tags absent from the doc: {stray}"
            )
        reachable = sorted({b for tag, b in resolution.items() if tag not in unreachable})
        if reachable != sorted(pinned):
            problems.append(
                f"{name}: expected_stable_beat_ids != reachable resolutions "
                f"(+{sorted(set(pinned) - set(reachable))[:3]} "
                f"-{sorted(set(reachable) - set(pinned))[:3]})"
            )

        # 5b. the resolution ratchet — the partition in 6 is vacuous without it.
        floor = MIN_RESOLVED_TAGS[name]
        if len(resolution) < floor:
            problems.append(
                f"{name}: only {len(resolution)} tags resolved, ratchet floor is {floor} "
                f"— tags may not be retreated into the unresolved queue"
            )

        # 6. resolved + unresolved partition the document's tags.
        both = sorted(set(resolution) & set(unresolved))
        if both:
            problems.append(f"{name}: tags both resolved and unresolved: {both}")
        accounted = set(resolution) | set(unresolved)
        unaccounted = sorted(set(doc_tags) - accounted)
        invented = sorted(accounted - set(doc_tags))
        if unaccounted:
            problems.append(
                f"{name}: {len(unaccounted)} document tags neither pinned nor queued: "
                f"{unaccounted[:5]}"
            )
        if invented:
            problems.append(
                f"{name}: {len(invented)} fixture tags do not exist in the document: "
                f"{invented[:5]}"
            )

    assert not problems, "golden fixtures are not internally consistent:\n  " + "\n  ".join(
        problems
    )
