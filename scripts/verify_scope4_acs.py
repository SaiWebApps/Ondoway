"""Verify the beats and skill source of the unified-beat-extract skill.

Usage:
    .venv/bin/python scripts/verify_scope4_acs.py --beats <path> [--city <slug>]

Example after running the skill against the fixture chunk:
    .venv/bin/python scripts/verify_scope4_acs.py \\
        --beats data/test_city_xx/beats.json --city test_city_xx

Verifies:
- every beat has all required fields populated; passes Pydantic validation
- sensory_anchor=true implies non-empty, structured physical_cues
- no city name is hardcoded in the skill source file
- Within-run beat_id uniqueness
- Beat_id prefix matches --city
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REQUIRED_FIELDS = {
    "beat_id",
    "city_name",
    "poi_name",
    "lens",
    "topic_slug",
    "script_body",
    "duration_sec",
    "entities",
    "sensory_anchor",
    "narrative_function",
    "beat_type",
    "emotional_register",
    "subject_tag",
    "physical_cues",
    "source_passage",
}

SKILL_PATH = Path(".claude/commands/unified-beat-extract.md")

# Cities that must NOT appear hardcoded in skill logic
FORBIDDEN_CITIES = [
    "paris", "london", "boston", "rome", "tokyo", "reims",
    "lyon", "marseille", "new_york",
]

# Lines matching these patterns are allowed to contain city names
# (examples, fixture paths, parameter defaults, comments)
ALLOWED_CONTEXT_PATTERNS = [
    r"^\s*#",              # markdown heading
    r"^\s*>",              # blockquote
    r"ARGUMENTS",          # parameter placeholder
    r"example",            # examples
    r"default:",           # parameter defaults
    r"around_and_about_paris",  # book slug (not city)
    r"Books/paris/",       # fixture path
    r"data/paris/",        # fixture path
    r"data/\{city",        # templated
    r"`paris`",            # example
    r"Paris\)",            # prose "(e.g., Paris)"
    r"\"paris\"",          # JSON example
    r"book_title",         # book-title field references (e.g. "Around and About Paris")
    r"Around and About",   # book title series
    r"snake_case",         # slug-format examples enumerating valid city slugs
    r"/poi-geocode",       # skill-invocation example in follow-up instructions
    r"/poi-dedup",         # skill-invocation example in follow-up instructions
    r"/unified-beat-extract",  # self-reference in skill file
]


def check_ac4(beats: list[dict]) -> tuple[bool, list[str]]:
    """Every beat has all required fields + passes Pydantic validation."""
    from src.api.models.nodes import NarrativeBeatCreate

    errors = []
    for i, b in enumerate(beats):
        # Required fields present
        missing = REQUIRED_FIELDS - set(b.keys())
        if missing:
            errors.append(f"beat[{i}] ({b.get('beat_id','?')}): missing fields {missing}")
            continue

        # Pydantic validation
        try:
            NarrativeBeatCreate(**{k: v for k, v in b.items() if k in NarrativeBeatCreate.model_fields})
        except Exception as e:
            errors.append(f"beat[{i}] ({b.get('beat_id','?')}): Pydantic validation failed: {e}")

        # subject_tag must be non-empty and 1-3 words
        st = b.get("subject_tag", "")
        if not st or not (1 <= len(st.split()) <= 3):
            errors.append(f"beat[{i}] ({b.get('beat_id','?')}): subject_tag invalid ({st!r})")

    return (not errors, errors)


def check_ac5(beats: list[dict]) -> tuple[bool, list[str]]:
    """sensory_anchor=true implies non-empty structured physical_cues."""
    errors = []
    for i, b in enumerate(beats):
        if not b.get("sensory_anchor"):
            continue
        cues = b.get("physical_cues") or []
        if not cues:
            errors.append(
                f"beat[{i}] ({b.get('beat_id','?')}): sensory_anchor=true but zero physical_cues"
            )
            continue
        for j, c in enumerate(cues):
            for field in ("cue", "direction", "feature_type"):
                if field not in c:
                    errors.append(
                        f"beat[{i}] ({b.get('beat_id','?')}) cue[{j}]: missing {field}"
                    )
    return (not errors, errors)


def check_ac8_code(skill_path: Path) -> tuple[bool, list[str]]:
    """No city name is hardcoded in skill logic."""
    if not skill_path.exists():
        return (False, [f"skill file not found: {skill_path}"])

    errors = []
    pattern = re.compile(r"\b(" + "|".join(FORBIDDEN_CITIES) + r")\b", re.IGNORECASE)

    for lineno, line in enumerate(skill_path.read_text().splitlines(), 1):
        if not pattern.search(line):
            continue
        # Check if this line matches an allowed context
        allowed = any(re.search(p, line) for p in ALLOWED_CONTEXT_PATTERNS)
        if not allowed:
            errors.append(f"{skill_path}:{lineno}: potential city hardcode: {line.strip()}")

    return (not errors, errors)


def check_uniqueness(beats: list[dict]) -> tuple[bool, list[str]]:
    """beat_ids are unique within the run."""
    seen = {}
    errors = []
    for i, b in enumerate(beats):
        bid = b.get("beat_id")
        if not bid:
            errors.append(f"beat[{i}]: missing beat_id")
            continue
        if bid in seen:
            errors.append(f"beat_id collision: {bid} at indices {seen[bid]} and {i}")
        else:
            seen[bid] = i
    return (not errors, errors)


def check_city_prefix(beats: list[dict], city: str) -> tuple[bool, list[str]]:
    """Every beat_id starts with city_."""
    errors = []
    prefix = f"{city}_"
    for i, b in enumerate(beats):
        bid = b.get("beat_id", "")
        if not bid.startswith(prefix):
            errors.append(f"beat[{i}] ({bid}): does not start with '{prefix}'")
    return (not errors, errors)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--beats",
        required=False,
        help="path to beats.json from a unified-beat-extract run",
    )
    parser.add_argument(
        "--city",
        required=False,
        help="expected city slug for beat_id prefix check",
    )
    parser.add_argument(
        "--skill",
        default=str(SKILL_PATH),
        help="path to the unified-beat-extract skill source",
    )
    args = parser.parse_args()

    all_pass = True

    # The skill-source city check always runs
    print("=== AC-8 (code half): no city hardcoding in skill source ===")
    ok, errors = check_ac8_code(Path(args.skill))
    if ok:
        print("  PASS")
    else:
        all_pass = False
        print("  FAIL")
        for e in errors:
            print(f"    {e}")

    # The rest require beats data
    if args.beats:
        beats_path = Path(args.beats)
        if not beats_path.exists():
            print(f"\n[ERROR] --beats path not found: {beats_path}")
            return 2
        beats = json.loads(beats_path.read_text())
        print(f"\n=== Beats loaded: {len(beats)} from {beats_path} ===\n")

        print("=== AC-4: all required fields, Pydantic-valid ===")
        ok, errors = check_ac4(beats)
        if ok:
            print(f"  PASS ({len(beats)} beats)")
        else:
            all_pass = False
            print("  FAIL")
            for e in errors:
                print(f"    {e}")

        print("\n=== AC-5: sensory_anchor → structured physical_cues ===")
        ok, errors = check_ac5(beats)
        if ok:
            sa_count = sum(1 for b in beats if b.get("sensory_anchor"))
            print(f"  PASS ({sa_count} sensory-anchored beats)")
        else:
            all_pass = False
            print("  FAIL")
            for e in errors:
                print(f"    {e}")

        print("\n=== Within-run beat_id uniqueness ===")
        ok, errors = check_uniqueness(beats)
        if ok:
            print("  PASS")
        else:
            all_pass = False
            print("  FAIL")
            for e in errors:
                print(f"    {e}")

        if args.city:
            print(f"\n=== AC-8 (fixture half): beat_id prefix '{args.city}_' ===")
            ok, errors = check_city_prefix(beats, args.city)
            if ok:
                print(f"  PASS (all {len(beats)} beats prefixed)")
            else:
                all_pass = False
                print("  FAIL")
                for e in errors:
                    print(f"    {e}")
    else:
        print("\n[INFO] --beats not supplied; AC-4/AC-5/AC-8-fixture skipped.")
        print("       Run the unified-beat-extract skill against tests/fixtures/mini_chunk.txt")
        print("       with city=test_city_xx, then re-run this script with --beats <path>.")

    print("\n" + ("ALL CHECKS PASSED" if all_pass else "FAILURES FOUND"))
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
