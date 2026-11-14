"""Assign each POI a place category — the script behind /poi-place-category.

WHY A THIRD SCRIPT. One script answers one question (the header rule in
`scripts/poi_visit_duration.py`): the capacity pass prices time, the hours pass
prices the calendar, this one names WHAT KIND OF PLACE each POI is. It is
deliberately NOT folded into the opening-hours pass, and deliberately NOT a
model call: it is a cheap derivation — deterministic, $0,
re-runnable at will — from name tokens, the description, and `poi_role`.
The write path (`load_pois` / `dump_pois`, the refuse-to-write-on-reformat
guard) is imported from the capacity pass, never copied.

THE VOCABULARY IS CLOSED. Twelve values, fixed:

    gallery | museum | church | square | arcade | market | park | garden |
    bridge | street | monument | other

`other` is a real answer, not a dumping ground — the consumer
(category-diverse replacement: prefers a set of stops that is not mostly one kind)
only needs the big classes to be right, and the human review reads the
full assignment table.

FRENCH TOPONYMY, two deliberate readings: a name starting "Place " is a
plaza (`square`), while a name starting "Square " is — in Paris usage — a
small fenced garden (`garden`): Square du Vert-Galant is a riverside garden,
not a plaza. And "Galerie" alone is ambiguous (Galerie Vivienne is a covered
arcade; the Galerie nationale du Jeu de Paume is an art venue), so `galerie`
resolves to `arcade` only when the description says covered/passage/arcade.

The harness corridor printout surfaces the labels so the owner can see them.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

from scripts.poi_visit_duration import dump_pois, load_pois

ROOT = Path(__file__).resolve().parent.parent

#: THE closed vocabulary. The structural test asserts every
#: written value is one of these and that this tuple never silently widens.
PLACE_CATEGORIES: tuple[str, ...] = (
    "gallery",
    "museum",
    "church",
    "square",
    "arcade",
    "market",
    "park",
    "garden",
    "bridge",
    "street",
    "monument",
    "other",
)


def _norm(text: str) -> str:
    """Casefolded, accent-stripped, single-spaced — the matching form."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    return " ".join(re.sub(r"[^a-z0-9-]+", " ", ascii_only.casefold()).split())


_CHURCH_TOKENS = ("eglise", "chapelle", "cathedrale", "basilique", "abbaye", "couvent")
_MONUMENT_TOKENS = (
    "fontaine",
    "fountain",
    "statue",
    "colonne",
    "obelisque",
    "monument",
    "arc de triomphe",
    "memorial",
)
_STREET_PREFIXES = ("rue ", "avenue ", "boulevard ", "quai ", "esplanade ", "allee ")

#: Description keywords, tried only after every name rule missed. Narrow on
#: purpose: a description mentioning "the square nearby" must not relabel a
#: museum, so each keyword names the place's own kind.
_DESC_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("arcade", ("covered arcade", "covered passage", "passage couvert")),
    ("museum", ("museum",)),
    ("church", ("church", "chapel", "cathedral", "basilica", "abbey")),
    ("bridge", ("bridge",)),
    ("market", ("market",)),
    ("garden", ("garden",)),
    ("park", ("public park",)),
    ("square", ("square", "plaza")),
    ("street", ("street", "promenade")),
    ("monument", ("statue", "fountain", "column", "obelisk", "monument", "memorial")),
    ("gallery", ("gallery",)),
)


def categorise(name: str, description: str, poi_role: str | None) -> str:
    """THE one derivation of a POI's place category. Pure, $0.

    Order matters and is part of the contract: French toponyms are decided by
    the name's leading word first (Place/Square/Rue/Pont/Jardin/Parc/Marche),
    then contained tokens (musee, chapelle, passage...), then the narrow
    description keywords, then `other`.
    """
    n = _norm(name)
    d = _norm(description)

    if n.startswith("place "):
        return "square"
    if n.startswith("square "):
        return "garden"  # French "square" = a small public garden
    if n.startswith(_STREET_PREFIXES):
        return "street"
    if n.startswith(("pont ", "passerelle ")):
        return "bridge"
    if n.startswith("jardin"):
        return "garden"
    if n.startswith("parc"):
        return "park"
    if n.startswith("marche"):
        return "market"
    if n.startswith("tour "):
        return "monument"
    if "musee" in n or "museum" in n:
        return "museum"
    if any(token in n for token in _CHURCH_TOKENS) or n.startswith(("notre-dame", "sacre-")):
        return "church"
    if any(token in n for token in _MONUMENT_TOKENS):
        return "monument"
    if "passage" in n:
        return "arcade"
    if "galerie" in n:
        arcade_words = ("arcade", "passage", "couvert")
        return "arcade" if any(w in d for w in arcade_words) else "gallery"
    for category, keywords in _DESC_RULES:
        if any(keyword in d for keyword in keywords):
            return category
    return "other"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slug", default="paris", help="City slug (default: paris)")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the table and write nothing."
    )
    args = parser.parse_args(argv)

    path = ROOT / "data" / args.slug / "poi-raw.json"
    if not path.exists():
        raise SystemExit(f"✗ no POI file at {path}")

    pois, original = load_pois(path)
    counts: dict[str, int] = dict.fromkeys(PLACE_CATEGORIES, 0)
    rows: list[tuple[str, str]] = []
    for poi in pois:
        category = categorise(
            poi.get("name", ""), poi.get("short_description", ""), poi.get("poi_role")
        )
        assert category in PLACE_CATEGORIES  # the vocabulary is closed
        poi["place_category"] = category
        counts[category] += 1
        rows.append((poi.get("name", ""), category))

    print(f"{len(pois)} POIs in {path.relative_to(ROOT)}; every one categorised.")
    for category in PLACE_CATEGORIES:
        print(f"  {category:9s} {counts[category]:4d}")
    if args.dry_run:
        print()
        for name, category in rows:
            print(f"  {category:9s} {name}")
        print("\nDry run. Nothing written.")
        return 0

    dump_pois(path, pois, original)
    print(f"✓ wrote place_category for all {len(pois)} POIs to {path.relative_to(ROOT)}")
    print(f"  NEXT, AND MANDATORY: make sync-poi-exports SLUG={args.slug}, exactly")
    print("  as the opening-hours and visit-duration passes require.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
