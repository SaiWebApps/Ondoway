"""Structural guard on per-POI place categories in `data/{city}/poi-raw.json`.

Every POI carries `place_category`, one value from a CLOSED twelve-word
vocabulary, derived deterministically at $0 by `scripts/poi_place_category.py`.
The category-diverse replacement is the consumer — a repair must never offer a
visitor a third gallery before 10:30, which only works if the classes are
assigned and the vocabulary cannot silently widen.

Same split as the sibling guards (`tests/test_poi_visit_duration.py`,
`tests/test_poi_opening_hours.py`): the pure derivation is pinned here with
always-on tests; the corpus-wide presence checks run per city on an explicit
allow-list a pass run deliberately extends.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.poi_place_category import PLACE_CATEGORIES, categorise

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = REPO_ROOT / "data"

# Cities whose corpus has been through the categoriser and must satisfy the
# data checks below. Same contract as CITIES_WITH_VISIT_CAPACITY: adding a slug
# is the deliberate act of declaring the pass ran; removing one to reach green
# is forbidden.
CITIES_WITH_PLACE_CATEGORY: tuple[str, ...] = ("paris",)


def test_the_vocabulary_is_closed_and_exactly_the_plan_s_twelve() -> None:
    """The vocabulary is fixed at twelve values; a silently widened one gives
    the category-diverse replacement classes nobody reviewed. Order-insensitive on purpose:
    the tuple's ORDER is presentation, its SET is the contract."""
    assert set(PLACE_CATEGORIES) == {
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
    }


@pytest.mark.parametrize(
    ("name", "description", "expected"),
    [
        # The three pinned precedents, with their real corpus descriptions.
        (
            "Place des Vosges",
            "Oldest planned square in Paris built in 1612 with 36 uniform "
            "red-brick houses and Victor Hugo's former home at No. 6",
            "square",
        ),
        (
            "Sainte-Chapelle",
            "13th-century Gothic royal chapel with 15 stained-glass windows",
            "church",
        ),
        (
            "Galerie Vivienne",
            "Elegant 1823 covered arcade with mosaic floors and neoclassical "
            "decor, one of the most beautiful passages in Paris",
            "arcade",
        ),
        # The French-toponymy reading the script documents: "Square X" is a
        # small garden, not a plaza.
        ("Square du Vert-Galant", "Riverside garden at the tip of the island", "garden"),
        ("Pont Neuf", "Oldest standing bridge across the Seine", "bridge"),
        ("Rue de la Paix", "Elegant shopping street", "street"),
    ],
)
def test_pinned_precedents(name: str, description: str, expected: str) -> None:
    assert categorise(name, description, "stop") == expected


def test_every_poi_is_categorised_with_a_closed_vocabulary_value() -> None:
    """Every POI in a declared city carries `place_category`, and every value
    is in the closed vocabulary — `other` is legal, absence is not."""
    for city in CITIES_WITH_PLACE_CATEGORY:
        pois = json.loads((DATA_ROOT / city / "poi-raw.json").read_text())
        offenders = [
            f"{p.get('name', '<unnamed>')}: place_category={p.get('place_category')!r}"
            for p in pois
            if p.get("place_category") not in PLACE_CATEGORIES
        ]
        if offenders:
            shown = "\n".join(f"  {line}" for line in offenders[:20])
            pytest.fail(
                f"{city}: POI(s) without a valid place_category "
                f"({len(offenders)} of them)\n{shown}\n"
                "Run the categoriser: `make poi-place-category SLUG=<city>`."
            )
