"""City-wide claim conflict report — `make claim-conflicts CITY=` (spec slice 6).

P6 (src/ingest/merge.py) sees a conflict only within one story at one place.
This pass reads `data/{city}/beats.json` and finds the same fact stated with
different values ACROSS beats — at another place, in another story — by the
signature test P6's own hint uses (`src.tour.claim_dedup._signature`, the
tour engine's "same fact" rule) and the number tokens (years, counts, prices)
each claim text states: two resolved claims in different beats that share no
source, state the same fact, and whose numbers neither contain the other's.

    make claim-conflicts CITY=new_york               # report only, exit 0
    make claim-conflicts CITY=new_york ARGS=--apply  # both claims -> contested

With `--apply` both claims become `contested`, each gaining the other's
sources with the value they state (the shape the validator's
CONTESTED_NEEDS_DIFFERING_VALUES demands), their verdicts re-bound over
their sources with judge and entailment untouched. The file is rewritten
only if the whole city still validates against its chunks — the one error
accepted is NARRATION_HASH_STALE on exactly the beats that changed, whose
narration must be re-derived (P4/P5, the job runner's) now that a resolved
claim left their resolved set. Legacy-shape records are counted, skipped
and written back unchanged (slice 10 re-extracts them).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ingest import model
from src.ingest.merge import SIGNATURE_MATCH_MIN, SIGNATURE_MIN_SHARED
from src.tour.claim_dedup import _overlap, _signature

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")


@dataclass(frozen=True)
class Conflict:
    """The same fact stated with different values in two beats."""

    beat_a: str
    claim_a: str
    values_a: str
    text_a: str
    beat_b: str
    claim_b: str
    values_b: str
    text_b: str


def stated_numbers(text: str) -> frozenset[str]:
    """Every number token a claim text states, as written."""
    return frozenset(_NUMBER_RE.findall(text))


def _value(numbers: frozenset[str]) -> str:
    return ", ".join(sorted(numbers, key=lambda n: (len(n), n)))


def is_new_shape(record: object) -> bool:
    """The slice-1 shape rule: both `claims` and `narration` present."""
    return isinstance(record, Mapping) and "claims" in record and "narration" in record


def _candidates(records: Sequence[Mapping]) -> Iterator[tuple[str, dict]]:
    for record in records:
        if not is_new_shape(record):
            continue
        for claim in record["claims"]:
            if claim["status"] == "resolved":
                yield record["beat_id"], claim


def _same_fact(a: str, b: str) -> bool:
    sig_a, sig_b = _signature(a), _signature(b)
    return (
        _overlap(sig_a, sig_b) >= SIGNATURE_MATCH_MIN
        and len(sig_a & sig_b) >= SIGNATURE_MIN_SHARED
    )


def _values_differ(a: frozenset[str], b: frozenset[str]) -> bool:
    return bool(a) and bool(b) and not a <= b and not b <= a


def _share_a_source(a: Mapping, b: Mapping) -> bool:
    return bool({s["source_id"] for s in a["sources"]} & {s["source_id"] for s in b["sources"]})


def find_conflicts(records: Sequence[Mapping]) -> list[Conflict]:
    """Every pair of resolved claims in different beats that share no
    source, state the same fact, and state different numbers. File order,
    each pair once."""
    items = list(_candidates(records))
    found: list[Conflict] = []
    for i, (beat_a, claim_a) in enumerate(items):
        for beat_b, claim_b in items[i + 1 :]:
            if beat_a == beat_b or _share_a_source(claim_a, claim_b):
                continue
            numbers_a = stated_numbers(claim_a["text"])
            numbers_b = stated_numbers(claim_b["text"])
            if not _values_differ(numbers_a, numbers_b):
                continue
            if not _same_fact(claim_a["text"], claim_b["text"]):
                continue
            found.append(
                Conflict(
                    beat_a,
                    claim_a["claim_id"],
                    _value(numbers_a),
                    claim_a["text"],
                    beat_b,
                    claim_b["claim_id"],
                    _value(numbers_b),
                    claim_b["text"],
                )
            )
    return found


def format_report(conflicts: Sequence[Conflict]) -> str:
    n = len(conflicts)
    plural = "s" if n != 1 else ""
    lines = [f"{n} conflict{plural} found (same fact, different values, across beats)"]
    for c in conflicts:
        lines.append(f"- {c.beat_a} {c.claim_a} states {c.values_a}: {c.text_a}")
        lines.append(f"  {c.beat_b} {c.claim_b} states {c.values_b}: {c.text_b}")
    return "\n".join(lines)


def _contest(claim: dict, value: str, other_sources: Sequence[Mapping], other_value: str) -> None:
    for source in claim["sources"]:
        if source["stated_value"] is None:
            source["stated_value"] = value
    own = {(s["source_id"], s["chunk"], s["span"]) for s in claim["sources"]}
    for source in other_sources:
        if (source["source_id"], source["chunk"], source["span"]) in own:
            continue
        stated = source["stated_value"] if source["stated_value"] is not None else other_value
        claim["sources"].append({**deepcopy(source), "stated_value": stated})
    claim["status"] = "contested"
    claim["resolved_value"] = None
    claim["resolution"] = None
    claim["verdict"]["bound_to"] = model.bind(
        claim["text"], "\n".join(s["span"] for s in claim["sources"])
    )


def apply_conflicts(
    records: Sequence[Mapping], conflicts: Sequence[Conflict]
) -> tuple[list[dict], list[str]]:
    """Both claims of every conflict contested (see the module docstring).
    Returns the new records — the caller's are never mutated — and the
    changed beat ids in file order, whose narrations are now stale."""
    out = [deepcopy(record) for record in records]
    index = {
        (record["beat_id"], claim["claim_id"]): claim
        for record in out
        if is_new_shape(record)
        for claim in record["claims"]
    }
    changed: set[str] = set()
    for conflict in conflicts:
        a = index[(conflict.beat_a, conflict.claim_a)]
        b = index[(conflict.beat_b, conflict.claim_b)]
        a_sources = deepcopy(a["sources"])
        b_sources = deepcopy(b["sources"])
        _contest(a, conflict.values_a, b_sources, conflict.values_b)
        _contest(b, conflict.values_b, a_sources, conflict.values_a)
        changed.update((conflict.beat_a, conflict.beat_b))
    rerun = [r["beat_id"] for r in out if is_new_shape(r) and r["beat_id"] in changed]
    return out, rerun


def _stale_hash_line(beat_id: str) -> str:
    return (
        f"NARRATION_HASH_STALE {beat_id}: narration.claims_hash does not match "
        "a fresh recomputation over this beat's claims"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--city", required=True)
    parser.add_argument("--data-root", type=Path, default=ROOT / "data")
    parser.add_argument("--books-root", type=Path, default=ROOT / "Books")
    parser.add_argument(
        "--apply", action="store_true", help="mark both claims of every conflict contested"
    )
    args = parser.parse_args(argv)

    beats_path = args.data_root / args.city / "beats.json"
    records = json.loads(beats_path.read_text(encoding="utf-8"))
    new_shape = [r for r in records if is_new_shape(r)]
    legacy = len(records) - len(new_shape)

    conflicts = find_conflicts(new_shape)
    print(format_report(conflicts))
    plural = "s" if legacy != 1 else ""
    print(f"{legacy} legacy-shape record{plural} skipped (slice 10 re-extracts them)")
    if not args.apply:
        return 0
    if not conflicts:
        print("nothing to apply")
        return 0

    applied, rerun = apply_conflicts(records, conflicts)
    errors = model.validate(
        [r for r in applied if is_new_shape(r)], chunks_root=args.books_root / args.city
    )
    expected = {_stale_hash_line(beat_id) for beat_id in rerun}
    unexpected = [error for error in errors if error not in expected]
    if unexpected:
        print(f"refusing to write {beats_path}: the city would not validate")
        for error in unexpected:
            print(f"  {error}")
        return 1
    beats_path.write_text(
        json.dumps(applied, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"wrote {beats_path}: {len(conflicts)} conflict{'s' if len(conflicts) != 1 else ''} "
        f"applied; rerun P4/P5: {', '.join(rerun)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
