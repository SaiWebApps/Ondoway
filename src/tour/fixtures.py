"""Named ordering tables for tier-5 anchors with hand-curated walks.

Each entry maps a POI's `name` (city-scoped) to an ordered list of
sub_location slugs as they appear in the empirical walk-throughs. The
order encodes the spatial walk path through the building or square.

Beat ordering for these POIs uses these tables when sub_location is
populated. Beats whose `sub_location` is missing from the table sort
last, in stable name order.

Adding a new POI: follow the empirical-tour walk path. Don't infer
ordering from corpus-cluster heuristics — get it from a hand-write or
the source guidebook.
"""

from __future__ import annotations

# Notre-Dame Cathedral, from empirical Tour 2 (2026-04-26).
# Path: parvis approach → kilometre-zero medallion → west façade reading
# (gallery of kings → portals) → bell-tower vestibule → interior nave →
# choir → treasury → towers (climb-out) → exterior east end (square Jean XXIII).
NOTRE_DAME_SUB_LOCATION_ORDER: tuple[str, ...] = (
    "parvis",
    "kilometre-zero",
    "facade",
    "facade-gallery-of-kings",
    "gallery-of-kings",
    "central-portal",
    "right-portal",
    "side-portals",
    "facade-side-portals",
    "left-tower",
    "bell-tower-vestibule",
    "interior-bell-tower-vestibule",
    "interior-nave",
    "nave",
    "choir",
    "interior-treasury",
    "treasury",
    "towers",
    "exterior-east-end-square-jean-xxiii",
)

# Conciergerie, from the Paris corpus (six populated sub_locations).
# Path: tour-bonbec entry → salle-des-gens-darmes (vaulted hall) →
# salle-de-toilette (Marie-Antoinette prep room) → marie-antoinette-cell
# → marie-antoinette-cell-mockup → rue-de-paris-cells (the cheap-seats
# cellblock).
CONCIERGERIE_SUB_LOCATION_ORDER: tuple[str, ...] = (
    "tour-bonbec",
    "salle-des-gens-darmes",
    "salle-de-toilette",
    "marie-antoinette-cell",
    "marie-antoinette-cell-mockup",
    "rue-de-paris-cells",
)

# Master table. Key by exact POI.name as it appears in the corpus.
SUB_LOCATION_ORDER: dict[str, tuple[str, ...]] = {
    "Notre-Dame Cathedral": NOTRE_DAME_SUB_LOCATION_ORDER,
    "Conciergerie": CONCIERGERIE_SUB_LOCATION_ORDER,
}


def sub_location_order_for(poi_name: str) -> tuple[str, ...] | None:
    """Return the curated sub_location order for a POI, or None if absent."""
    return SUB_LOCATION_ORDER.get(poi_name)
