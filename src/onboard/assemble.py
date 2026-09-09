"""Step-4 CORE: merge cross-source candidates into a writable ``data/{slug}/``.

Two surfaces, one wall between them:

- ``assemble`` is PURE (no I/O): it merges the ``PoiCandidate`` hits from every
  Step-3 ``ConnectorResult`` into deduplicated POI dicts, geofences them to the
  city bbox, and forces a healthy gravity-tier distribution — returning an
  ``AssembledCity``. It reads ONLY ``result.candidates``; it NEVER touches
  ``result.pointers`` or ``result.documents``, so a shadow-library
  ``DiscoveryPointer`` has no path into the assembled city (grey-zone wall 3).
- ``write_city`` is the ONLY disk/registry touch: it atomically writes the exact
  ``data/{slug}/`` corpus the Step-8 uploader consumes and registers the city.

The merge/dedup + forced distribution are designed against the silent CI guards
that turn RED the instant ``data/{slug}/`` exists:

- ``tests/test_data_integrity.py`` dedups on accent-strip + casefold (``_norm``,
  copied byte-for-byte below) and forbids name/variation collisions, so the merge
  key MIRRORS ``_norm`` (not ``canonical_name_key``, which keeps accents) and a
  post-merge sweep drops any colliding variation.

ZERO-FALSE-MERGE is GEO-AWARE, not name-only: two candidates that normalize to
the same name merge ONLY when they are co-located (within ``_SAME_PLACE_METERS``);
distinct places that merely share a name (e.g. two "St Mary's Church" in different
boroughs) are split into separate POIs and deterministically disambiguated
("Name", "Name (2)", ...), so no place is ever silently dropped by a name clash.
The fuzzy near-dup pass adds a second, tighter geo gate for near-identical names.
- ``tests/test_gravity_distribution.py`` requires a truthy
  ``_pipeline.gravity_audit`` on every POI plus a healthy tier SHAPE (T5 ∈
  [7,20]%, T3 ≤ 35%, T1+T2 ≥ T4+T5); the rank-quantile forced distribution below
  emits exactly that shape.
- ``scripts/upload_paris.py`` silently drops out-of-bbox POIs, so ``assemble``
  drops them here (never clips) — keeping the centroid inside the registry bbox.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from rapidfuzz import fuzz

from scripts.beat_builder import slugify
from src import city_registry
from src.onboard.models import CityContext, ConnectorResult, PoiCandidate

logger = logging.getLogger(__name__)

# The minimum POI count a real city must carry — never ship a thin city.
MIN_POIS = 30

# NOTABILITY CAP: the number of real landmarks a live city keeps out of its raw
# multi-source firehose. A live city can surface ~55K coordinate-bearing
# candidates (every Wikidata item + OSM feature in the bbox); only a few dozen
# are genuine tour landmarks. ``assemble`` keeps the top ``TARGET_POIS`` by a
# composite notability score. Near ``MIN_POIS``; override via the
# ``ONBOARD_TARGET_POIS`` env var or the ``target_pois`` param to ``assemble``.
TARGET_POIS = int(os.getenv("ONBOARD_TARGET_POIS", "48"))

# ---------------------------------------------------------------------------
# STAGE A — per-candidate hard blocklist, applied to the RAW candidates BEFORE
# the bucket/geo-split/fuzzy merge. This is the PERFORMANCE fix as much as a
# quality one: the fuzzy near-dup union is O(n^2) (~16 min at 55K candidates),
# so culling the obvious non-landmarks FIRST keeps the merge over a far smaller
# set. Every rule below is name/meta-local (no cross-candidate state), so it is
# a single cheap linear pass.
# ---------------------------------------------------------------------------
# OSM ``tourism`` values that are lodging / transient / info noise, never a stop.
_OSM_LODGING_TOURISM = frozenset(
    {
        "hotel",
        "hostel",
        "guest_house",
        "apartment",
        "motel",
        "camp_site",
        "caravan_site",
        "information",
        "picnic_site",
    }
)
# OSM ``historic`` values that are graves / boundary markers, never a stop.
_OSM_HISTORIC_JUNK = frozenset(
    {
        "tomb",
        "tumulus",
        "grave",
        "boundary_stone",
        "milestone",
        "wayside_cross",
        "wayside_shrine",
    }
)
# A ``PoiCandidate.name`` that is an Olympics-event article ("Athletics at the
# 2012 Summer Olympics") is an EVENT, not a place.
_OLYMPICS_EVENT_RE = re.compile(r" at the \d{4} (?:Summer|Winter) Olympics")
# Wikidata ``P31`` (instance-of) QIDs for event / occurrence classes: an item
# that IS an event must never become a POI.
_EVENT_P31_CLASSES = frozenset(
    {
        "Q1656682",   # event
        "Q1190554",   # occurrence
        "Q16510064",  # sporting event
        "Q13406554",  # sports competition
        "Q18608583",  # recurring event
        "Q1079023",   # ceremony
    }
)
# Wikidata ``P31`` QIDs for administrative / settlement classes: the CITY node
# itself and its boroughs/districts are places you tour WITHIN, never a walkable
# STOP. The live London onboard proved the hole — the "London" city node (P31
# includes Q515) resolved to the London article, drafted beats, and surfaced as a
# nonsensical stop ("head for London — a 3-minute walk from here"). Q515 and
# Q211690 are VERIFIED against the live entities (London Q84, City of Westminster
# Q179351, London Borough of Lewisham Q215030); the settlement/country classes
# are forward-looking robustness for the next city. A tour LANDMARK (museum
# Q33506, square Q174782, park Q22698, monument, church, building) carries none
# of these, so no real stop is dropped.
_ADMIN_P31_CLASSES = frozenset(
    {
        "Q515",       # city (London Q84, City of Westminster Q179351 — verified)
        "Q211690",    # London borough (Westminster, Lewisham — verified)
        "Q5119",      # capital
        "Q486972",    # human settlement
        "Q3957",      # town
        "Q532",       # village
        "Q6256",      # country
        "Q3624078",   # sovereign state
    }
)
# A candidate farther than this from the bbox centre is an outer-suburb outlier
# (airports, park-and-rides) that would break a walkable tour — dropped. Keeps
# Heathrow (~25 km out) out while keeping the whole walkable core in.
_MAX_CENTRE_METERS = 12_000.0
# A hotel-class candidate re-enters STAGE A only if it is itself very notable
# (a landmark hotel like the Savoy) — measured by its own ``sitelinks``.
_HOTEL_REENTRY_SITELINKS = 15

# ---------------------------------------------------------------------------
# Notability scoring (post-merge). OSM classes strong enough to add a small
# bonus to the composite score (never enough to KEEP a POI on their own).
# ---------------------------------------------------------------------------
_OSM_STRONG_TOURISM = frozenset(
    {"museum", "gallery", "attraction", "viewpoint", "artwork", "theme_park", "zoo", "aquarium"}
)
_OSM_STRONG_HISTORIC = frozenset(
    {
        "castle",
        "monument",
        "memorial",
        "monastery",
        "ruins",
        "archaeological_site",
        "fort",
        "city_gate",
        "palace",
        "manor",
    }
)
# Wikivoyage ``listing_type`` values that mark a curated sightseeing pick.
_WIKIVOYAGE_CURATED = frozenset({"see", "do"})

# NAME-source precedence: which source's spelling wins as the display name.
_NAME_PRIORITY: dict[str, int] = {
    "wikidata": 0,
    "wikipedia": 1,
    "osm": 2,
    "wikivoyage": 3,
}

# COORD-source precedence: the first source (in this order) that carries a
# non-null lat/lon wins the merged POI's coordinates.
_COORD_PRECEDENCE: tuple[str, ...] = ("wikidata", "osm", "wikipedia", "wikivoyage")

# Conservative near-dup thresholds (poi-dedup's ZERO-FALSE-MERGE rule): a pair
# auto-merges ONLY when the names are near-identical AND essentially co-located.
_FUZZ_MIN_RATIO = 94
_FUZZ_MAX_METERS = 75.0

# GEO gate for the EXACT-norm bucket path: two candidates that normalize to the
# SAME name are the SAME place only if within this distance; farther apart they
# are DISTINCT places sharing a name (e.g. two "St Mary's Church" in different
# boroughs) and must NOT be silently collapsed. Wider than the fuzzy gate above
# because an exact-name match is stronger evidence of co-reference than a fuzzy one.
_SAME_PLACE_METERS = 150.0


# ---------------------------------------------------------------------------
# Normalization — copied BYTE-FOR-BYTE from tests/test_data_integrity.py:23-30
# so the merge key matches the dedup guard exactly (accent-strip + casefold +
# strip). Do NOT import the test; this is the load-bearing duplicate.
# ---------------------------------------------------------------------------
def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn"
    )


def _norm(s: str) -> str:
    return _strip_accents(s).lower().strip()


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in metres."""
    r = 6371000.0  # mean Earth radius (m)
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


# ---------------------------------------------------------------------------
# Frozen interface consumed by Step-4 beat_draft / cli (Devs B & C).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class WikiExtract:
    """One pinned Wikipedia revision's plain text, keyed to a POI.

    ``write_city`` saves each as ``wikipedia/{slugify(poi_name)}-rev-{revid}.txt``
    VERBATIM — the same pinned-revision layout ``scripts/wiki_fetch.py`` produces,
    which ``validate_beats.py`` later checks beats' ``source_passage`` against.
    """

    poi_name: str
    revid: str
    text: str
    article_title: str
    url: str


@dataclass
class AssembledCity:
    """The pure result of ``assemble`` — everything ``write_city`` needs and
    NOTHING that could carry shadow-library text.

    ``pois`` are finished POI dicts (poi-raw.json shape); ``wiki_extracts`` maps
    ``slugify(poi_name) -> WikiExtract``. ``cloud_deployed`` is threaded from
    ``assemble`` to the registry entry ``write_city`` writes (a new city defaults
    to local-only, ``False``, until deployed to Aura). There is deliberately NO
    ``pointers`` field: ``DiscoveryPointer`` never reaches disk (wall 3).
    """

    slug: str
    pois: list[dict]
    wiki_extracts: dict[str, WikiExtract] = field(default_factory=dict)
    cloud_deployed: bool = False


# ---------------------------------------------------------------------------
# Merge helpers (all pure).
# ---------------------------------------------------------------------------
def _name_priority(source: str) -> int:
    return _NAME_PRIORITY.get(source, 99)


def _display_name(cands: list[PoiCandidate]) -> str:
    """The display name for a merged group: the highest-priority NAME source's
    spelling; ties broken by ``_norm`` then raw name for determinism."""
    return min(cands, key=lambda c: (_name_priority(c.source), _norm(c.name), c.name)).name


def _merged_coords(cands: list[PoiCandidate]) -> tuple[float | None, float | None]:
    """First non-null (lat, lon) walking ``_COORD_PRECEDENCE``; a source not in
    the precedence list is only used as a last resort."""
    for src in _COORD_PRECEDENCE:
        for c in cands:
            if c.source == src and c.latitude is not None and c.longitude is not None:
                return c.latitude, c.longitude
    for c in cands:
        if c.latitude is not None and c.longitude is not None:
            return c.latitude, c.longitude
    return None, None


class _UnionFind:
    """Tiny union-find so a chain of near-dups (A~B, B~C) collapses to one POI."""

    def __init__(self, keys: list[str]) -> None:
        self._parent = {k: k for k in keys}

    def find(self, x: str) -> str:
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        # Path-compress.
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra


def _short_description(cands: list[PoiCandidate]) -> str:
    """The best (longest) non-empty candidate ``short_description``, else ""."""
    descs = [c.short_description for c in cands if (c.short_description or "").strip()]
    return max(descs, key=len) if descs else ""


def _bucket_candidates(results: list[ConnectorResult]) -> dict[str, list[PoiCandidate]]:
    """Bucket every candidate across every result by ``_norm(name)`` (the exact
    accent+case dedup key). This is the first pass; ``_geo_split_bucket`` then
    splits each bucket by proximity so a shared exact name never merges two
    genuinely DISTINCT places (see ZERO-FALSE-MERGE in the module docstring)."""
    buckets: dict[str, list[PoiCandidate]] = {}
    for result in results:
        for cand in result.candidates:
            buckets.setdefault(_norm(cand.name), []).append(cand)
    return buckets


def _geo_split_bucket(cands: list[PoiCandidate]) -> list[list[PoiCandidate]]:
    """Split ONE exact-``_norm`` bucket into same-place clusters by proximity.

    The common case is UNCHANGED from the pre-fix single-group merge: when a bucket
    has 0 or 1 placeable point, or ALL its placeable points co-locate (within
    ``_SAME_PLACE_METERS``), the whole bucket — coord AND no-coord candidates, in
    original order — is returned as ONE cluster. (A wholly-coordless bucket is that
    one cluster too; ``_merged_coords`` returns ``None`` and the caller drops it, as
    before.) This is what keeps an already-correct city byte-for-byte identical.

    The fix bites ONLY when the placeable points fall into 2+ proximity clusters:
    genuinely DISTINCT places share a name (e.g. two "St Mary's Church" 17 km apart).
    Then each cluster is a separate place (original relative order preserved); any
    no-coord candidate can't be assigned to one place, so it forms its own coordless
    cluster the caller drops (never mis-attributed). Greedy union-find over pairwise
    distance, so a chain of near points stays one cluster. Never crashes on missing
    coords."""
    coord_idx = [
        i for i, c in enumerate(cands) if c.latitude is not None and c.longitude is not None
    ]
    if len(coord_idx) <= 1:
        return [list(cands)]  # 0 or 1 placeable point -> one place (as before)

    keys = [str(k) for k in range(len(coord_idx))]
    uf = _UnionFind(keys)
    for a in range(len(coord_idx)):
        for b in range(a + 1, len(coord_idx)):
            ca, cb = cands[coord_idx[a]], cands[coord_idx[b]]
            if (
                _haversine_m(ca.latitude, ca.longitude, cb.latitude, cb.longitude)
                <= _SAME_PLACE_METERS
            ):
                uf.union(keys[a], keys[b])

    root_per_pos = [uf.find(keys[a]) for a in range(len(coord_idx))]
    roots_in_order: list[str] = []
    for r in root_per_pos:
        if r not in roots_in_order:
            roots_in_order.append(r)
    if len(roots_in_order) == 1:
        return [list(cands)]  # all placeable points co-locate -> one place (as before)

    # GENUINE SPLIT: distinct places share this name.
    clusters_by_root: dict[str, list[PoiCandidate]] = {r: [] for r in roots_in_order}
    for a, i in enumerate(coord_idx):
        clusters_by_root[root_per_pos[a]].append(cands[i])
    clusters = [clusters_by_root[r] for r in roots_in_order]
    no_coord = [c for c in cands if c.latitude is None or c.longitude is None]
    if no_coord:
        clusters.append(no_coord)
    return clusters


def _disambiguate_same_name(pois: list[dict], ctx: CityContext) -> None:
    """DE-COLLIDE canonical names of DISTINCT places (in place).

    A bucket that geo-split into 2+ surviving places leaves POIs sharing one
    ``_norm`` name — which would (a) trip the CI accent/case dedup guard and (b)
    make two places look like one. Keep the first (by DESCENDING cross-source
    agreement, then coords for determinism) as the plain name and append
    ``' (2)'``, ``' (3)'``, ... to the rest, LOGGING each split so the divergence is
    never silent. (Distinct ``_norm`` names can only arise within one exact-norm
    bucket, so this touches nothing the fuzzy pass legitimately merged.)"""
    by_norm: dict[str, list[dict]] = {}
    for poi in pois:
        by_norm.setdefault(_norm(poi["name"]), []).append(poi)
    for group in by_norm.values():
        if len(group) < 2:
            continue
        ordered = sorted(
            group,
            key=lambda p: (
                -p["_pipeline"]["gravity_audit"]["cross_source_agreement"],
                p["latitude"],
                p["longitude"],
            ),
        )
        base = ordered[0]["name"]
        logger.info(
            "assemble(%s): same-name split — %r kept as %d distinct places at %s",
            ctx.slug,
            base,
            len(ordered),
            [(round(p["latitude"], 5), round(p["longitude"], 5)) for p in ordered],
        )
        for n, poi in enumerate(ordered[1:], start=2):
            poi["name"] = f"{base} ({n})"


def _fuzzy_union(buckets: dict[str, list[PoiCandidate]]) -> _UnionFind:
    """Conservative rapidfuzz near-dup pass over the exact-norm buckets: union a
    pair ONLY when ``token_sort_ratio >= 94`` AND the two representative points
    are within 75 m. Bucket order is fixed (dict insertion) for determinism."""
    keys = list(buckets)
    names = {k: _display_name(buckets[k]) for k in keys}
    coords = {k: _merged_coords(buckets[k]) for k in keys}
    uf = _UnionFind(keys)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = keys[i], keys[j]
            if fuzz.token_sort_ratio(names[a], names[b]) < _FUZZ_MIN_RATIO:
                continue
            (la, oa), (lb, ob) = coords[a], coords[b]
            if la is None or lb is None:
                continue
            if _haversine_m(la, oa, lb, ob) <= _FUZZ_MAX_METERS:
                uf.union(a, b)
    return uf


def _variations(group: list[PoiCandidate], canonical: str) -> list[str]:
    """Distinct display strings in a merged group (by ``_norm``) other than the
    canonical name — includes each candidate's own ``name_variations``."""
    seen = {_norm(canonical)}
    out: list[str] = []
    for cand in group:
        for name in (cand.name, *cand.name_variations):
            key = _norm(name)
            if key and key not in seen:
                seen.add(key)
                out.append(name)
    return out


def _sweep_collisions(pois: list[dict]) -> None:
    """POST-MERGE COLLISION SWEEP (in place): drop any ``name_variation`` whose
    ``_norm`` collides with ANOTHER POI's name or an already-claimed key, so
    ``test_data_integrity`` :92 (accent/case dup) and :151 (variation collision)
    stay green. Canonical names are collision-free by construction (one per
    surviving ``_norm`` bucket); only variations can collide."""
    reverse: dict[str, str] = {}
    # Claim every canonical name first — canonical names always win a collision.
    for poi in pois:
        reverse[_norm(poi["name"])] = poi["name"]
    for poi in pois:
        own = _norm(poi["name"])
        kept: list[str] = []
        for var in poi["name_variations"]:
            key = _norm(var)
            if not key or key == own:
                continue  # empty, or a restatement of the POI's own name
            owner = reverse.get(key)
            if owner is not None and owner != poi["name"]:
                continue  # collides with another POI's name/variation — drop it
            reverse[key] = poi["name"]
            kept.append(var)
        poi["name_variations"] = kept


def _assign_tiers(pois: list[dict]) -> None:
    """FORCED GRAVITY DISTRIBUTION (in place): deterministic rank-quantile.

    Rank by ``(-notability_score, _norm(name))`` then slice at boundaries
    [10, 25, 50, 75]% into tiers 5/4/3/2/1. Ranking by the composite NOTABILITY
    SCORE (not raw agreement) means the most notable landmarks land in the top
    tiers instead of post-filter agreement ties being tiered alphabetically. The
    quantile slicing is unchanged, so the emitted SHAPE (T5 ≈ 10%, T3 ≈ 25%,
    bottom-heavy) still satisfies ``test_gravity_distribution`` for n in 30..60.
    """
    n = len(pois)

    def _rank_key(poi: dict) -> tuple[float, str]:
        audit = poi["_pipeline"]["gravity_audit"]
        return (-audit["notability_score"], _norm(poi["name"]))

    ranked = sorted(pois, key=_rank_key)
    b = [int(0.10 * n), int(0.25 * n), int(0.50 * n), int(0.75 * n)]
    scored_at = date.today().isoformat()
    for idx, poi in enumerate(ranked):
        if idx < b[0]:
            tier = 5
        elif idx < b[1]:
            tier = 4
        elif idx < b[2]:
            tier = 3
        elif idx < b[3]:
            tier = 2
        else:
            tier = 1
        poi["importance_tier"] = tier
        audit = poi["_pipeline"]["gravity_audit"]
        audit["assigned_gravity"] = tier
        audit["rank"] = idx
        audit["of"] = n
        audit["scored_at"] = scored_at


def _stage_a_filter(
    results: list[ConnectorResult], ctx: CityContext
) -> tuple[list[ConnectorResult], Counter]:
    """STAGE A: drop obvious non-landmark candidates BEFORE the O(n^2) merge.

    Returns the results with their candidate lists filtered (documents/pointers
    untouched — ``assemble`` never reads them, but a copy keeps the model valid)
    plus a per-rule drop-count ``Counter`` for the audit log and the thin-city
    error. See the module ``STAGE A`` block for why this runs first.
    """
    centre_lat, centre_lon = ctx.centre
    # The city's own identity — a candidate that IS the city (or a borough sharing
    # its name) is administrative, never a stop. Normalized once here.
    city_names = {_norm(ctx.display_name), _norm(ctx.slug)}
    drops: Counter = Counter()
    filtered: list[ConnectorResult] = []
    for result in results:
        kept = [
            c
            for c in result.candidates
            if _keep_candidate(c, centre_lat, centre_lon, city_names, drops)
        ]
        filtered.append(result.model_copy(update={"candidates": kept}))
    return filtered, drops


def _keep_candidate(
    cand: PoiCandidate,
    centre_lat: float,
    centre_lon: float,
    city_names: set[str],
    drops: Counter,
) -> bool:
    """One candidate's STAGE-A verdict; ``drops`` is incremented per fired rule.

    ``meta`` is read with ``.get`` + safe defaults throughout — an OLDER fixture
    whose connectors did not yet attach ``tourism``/``historic``/``p31``/
    ``sitelinks`` simply doesn't trip the meta rules (its real landmarks survive).
    ``city_names`` is the normalized {display_name, slug} — a candidate whose own
    name matches is the city/borough node, dropped even without a ``p31``.
    """
    meta = cand.meta or {}
    # 1. EMPTY SLUG: a non-Latin-only name slugifies to "" — unroutable as a POI
    #    slug (write_city keys the corpus files on ``slugify(name)``). Reject.
    if slugify(cand.name) == "":
        drops["empty_slug"] += 1
        return False
    # 2. OSM lodging / transient / info classes — not a tour stop. A hotel-class
    #    candidate re-enters ONLY if it is itself very notable (a landmark hotel).
    tourism = meta.get("tourism")
    if (
        tourism in _OSM_LODGING_TOURISM
        and int(meta.get("sitelinks") or 0) < _HOTEL_REENTRY_SITELINKS
    ):
        drops["osm_lodging"] += 1
        return False
    # 3. OSM ``tourism=artwork`` with no wiki linkage — street furniture.
    if tourism == "artwork" and not (meta.get("osm_wikidata") or meta.get("osm_wikipedia")):
        drops["osm_artwork_no_wiki"] += 1
        return False
    # 4. OSM historic graves / boundary markers.
    if meta.get("historic") in _OSM_HISTORIC_JUNK:
        drops["osm_historic_junk"] += 1
        return False
    # 5. EVENT (not a place): an Olympics-event name, or a Wikidata event ``P31``.
    if _OLYMPICS_EVENT_RE.search(cand.name):
        drops["event"] += 1
        return False
    p31 = meta.get("p31") or ""
    if p31 and _EVENT_P31_CLASSES.intersection(p31.split("|")):
        drops["event"] += 1
        return False
    # 5b. ADMINISTRATIVE AREA (not a place you STOP at): the city node itself or a
    #     borough/district — by Wikidata ``P31`` (a landmark carries none of these)
    #     OR by name (an OSM-only city node with no ``p31`` still matches the city's
    #     own name). Either kills the "London is a tour stop" bug at the root.
    if p31 and _ADMIN_P31_CLASSES.intersection(p31.split("|")):
        drops["admin_area"] += 1
        return False
    if _norm(cand.name) in city_names:
        drops["admin_area"] += 1
        return False
    # 6. FAR OUTLIER: > ~12 km from the bbox centre (unwalkable; kills airports /
    #    outer park-and-rides while keeping the whole walkable core).
    if (
        cand.latitude is not None
        and cand.longitude is not None
        and _haversine_m(centre_lat, centre_lon, cand.latitude, cand.longitude)
        > _MAX_CENTRE_METERS
    ):
        drops["far_from_centre"] += 1
        return False
    return True


def _notability_signals(group: list[PoiCandidate]) -> dict:
    """The notability SIGNALS of a merged group, from ``PoiCandidate.meta`` only.

    All reads use ``.get`` + safe defaults so an older fixture (sparse meta)
    degrades gracefully rather than crashing. ``sitelinks_max`` defaults to 0;
    ``has_enwiki`` is true when the group has its OWN English Wikipedia article
    (a wikipedia-source candidate, a Wikidata ``enwiki`` sitelink, or an OSM
    ``wikipedia`` tag).
    """
    sitelinks_max = max((int((c.meta or {}).get("sitelinks") or 0) for c in group), default=0)
    has_enwiki = (
        any(c.source == "wikipedia" for c in group)
        or any(c.source == "wikidata" and (c.meta or {}).get("enwiki") for c in group)
        or any((c.meta or {}).get("osm_wikipedia") for c in group)
    )
    agreement = len({c.source for c in group})
    wikivoyage_curated = any(
        c.source == "wikivoyage" and (c.meta or {}).get("listing_type") in _WIKIVOYAGE_CURATED
        for c in group
    )
    osm_strong = any(
        c.source == "osm"
        and (
            (c.meta or {}).get("tourism") in _OSM_STRONG_TOURISM
            or (c.meta or {}).get("historic") in _OSM_STRONG_HISTORIC
            or (c.meta or {}).get("osm_wikidata")
            or (c.meta or {}).get("osm_wikipedia")
        )
        for c in group
    )
    score = (
        2.0 * math.log10(1 + sitelinks_max)
        + 1.0 * (agreement - 1)
        + 1.5 * float(has_enwiki)
        + 1.0 * float(wikivoyage_curated)
        + 0.5 * float(osm_strong)
    )
    return {
        "sitelinks_max": sitelinks_max,
        "has_enwiki": has_enwiki,
        "agreement": agreement,
        "wikivoyage_curated": wikivoyage_curated,
        "osm_strong_class": osm_strong,
        "score": round(score, 6),
    }


def _is_notable(sig: dict) -> bool:
    """STAGE B: the post-merge notability KEEP predicate (the firehose filter).

    A place with its OWN English Wikipedia article near the city centre is a
    landmark by definition, so ``has_enwiki`` keeps it — this is the graceful
    default that keeps real landmarks from an OLDER fixture whose connectors did
    not yet attach ``sitelinks``/``enwiki`` scalars (the spec keep-clauses
    ``has_enwiki AND sitelinks_max>=3`` and ``has_enwiki AND agreement>=2`` are
    the stronger, corroborated cases of this same signal). With NO English
    article we demand a strong independent signal: heavy cross-wiki linkage
    (a foreign-notable global landmark) or a corroborated curated Wikivoyage
    pick. Everything below this bar is the 55K firehose tail.
    """
    if sig["has_enwiki"]:
        return True
    if sig["sitelinks_max"] >= 10:
        return True
    # No English article: a corroborated curated Wikivoyage see/do pick keeps it.
    return sig["wikivoyage_curated"] and sig["agreement"] >= 2


def _drop_report(drops: Counter) -> str:
    """A stable, human-readable ``rule=count`` summary of every filter drop."""
    return ", ".join(f"{rule}={drops[rule]}" for rule in sorted(drops)) or "(none)"


def assemble(
    results: list[ConnectorResult],
    ctx: CityContext,
    extracts: list[WikiExtract],
    *,
    cloud_deployed: bool = False,
    target_pois: int | None = None,
) -> AssembledCity:
    """Merge cross-source candidates into geofenced, gravity-tiered POI dicts.

    PURE — no network, no disk, no registry. Reads ONLY ``result.candidates``;
    ``result.pointers`` / ``result.documents`` are never consulted (wall 3).

    The notability pipeline turns a live city's raw firehose into ~``TARGET_POIS``
    real landmarks: STAGE A culls per-candidate junk BEFORE the O(n^2) merge (the
    perf fix), the merge is unchanged, STAGE B keeps only the notable merged POIs,
    and a composite-score CAP trims the survivors to ``target_pois``. When a
    (small / older-fixture) city yields fewer notable POIs than the target the CAP
    BACKFILLS from the best remaining POIs rather than nuking a real city to a
    handful — but a real firehose has far more notable POIs than the target, so
    the tail never gets a slot. Fewer than ``MIN_POIS`` survivors raises (never
    auto-relax).
    """
    target = TARGET_POIS if target_pois is None else target_pois
    min_lat, max_lat, min_lon, max_lon = ctx.bbox
    raw_in = sum(len(r.candidates) for r in results)

    # 0. STAGE A — per-candidate hard blocklist BEFORE the O(n^2) merge (perf fix).
    results, stage_a_drops = _stage_a_filter(results, ctx)

    # 1. MERGE/DEDUP: exact-norm buckets, GEO-SPLIT each bucket into same-place
    # clusters (distinct places sharing a name stay separate), then a conservative
    # fuzzy near-dup union-find over the clusters — now over the FAR smaller set.
    buckets = _bucket_candidates(results)
    clusters: dict[str, list[PoiCandidate]] = {}
    for bkey, bcands in buckets.items():
        for i, cluster in enumerate(_geo_split_bucket(bcands)):
            clusters[f"{bkey}\x00{i}"] = cluster
    uf = _fuzzy_union(clusters)
    groups: dict[str, list[PoiCandidate]] = {}
    for key, cands in clusters.items():
        groups.setdefault(uf.find(key), []).extend(cands)

    # 2. COORDS + 3. BBOX drop (never clip). Each surviving group also gets its
    # notability SIGNALS + composite score (stamped into the gravity_audit).
    scored: list[tuple[dict, dict]] = []  # (poi, notability signals)
    dropped_no_coords = 0
    dropped_out_of_bbox = 0
    for group in groups.values():
        canonical = _display_name(group)
        lat, lon = _merged_coords(group)
        if lat is None or lon is None:
            dropped_no_coords += 1
            continue
        if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
            dropped_out_of_bbox += 1
            continue
        sources = sorted({c.source for c in group})
        source_urls = sorted({c.source_url for c in group if c.source_url})
        sig = _notability_signals(group)
        poi = {
            "name": canonical,
            "short_description": _short_description(group),
            "name_variations": _variations(group, canonical),
            "latitude": lat,
            "longitude": lon,
            "city_name": ctx.slug,
            "trigger_radius": 30,
            "kid_friendly": "yes",
            "poi_role": "stop",
            "importance_tier": None,  # stamped by _assign_tiers
            "_pipeline": {
                "discovery_sources": sources,
                "source_urls": source_urls,
                "gravity_audit": {
                    "signal_source": "onboard_auto_v1",
                    "assigned_gravity": None,  # stamped below
                    "cross_source_agreement": len(sources),
                    "sources": sources,
                    "notability_score": sig["score"],
                    "sitelinks": sig["sitelinks_max"],
                    "rank": None,  # stamped below
                    "of": None,  # stamped below
                    "method": "rank_quantile_forced_distribution",
                    "scored_at": None,  # stamped below
                },
            },
        }
        scored.append((poi, sig))

    if dropped_out_of_bbox or dropped_no_coords:
        logger.info(
            "assemble(%s): dropped %d POI(s) outside bbox %s, %d with no coordinates",
            ctx.slug,
            dropped_out_of_bbox,
            ctx.bbox,
            dropped_no_coords,
        )

    # 3b. STAGE B (notability KEEP) + composite-score CAP. Sort each partition by
    # score desc (tiebreak _norm(name)); keep every notable POI up to the target,
    # then BACKFILL from the best non-notable ONLY to reach the target (so a small
    # real city is never nuked, while a firehose's notable set alone fills it).
    def _by_score(item: tuple[dict, dict]) -> tuple[float, str]:
        poi, sig = item
        return (-sig["score"], _norm(poi["name"]))

    notable = sorted((it for it in scored if _is_notable(it[1])), key=_by_score)
    rejected = sorted((it for it in scored if not _is_notable(it[1])), key=_by_score)
    kept = notable[:target]
    if len(kept) < target:
        kept = kept + rejected[: target - len(kept)]
    dropped_low_notability = len(rejected) - max(0, len(kept) - len(notable))
    dropped_over_cap = max(0, len(notable) - target)
    pois = [poi for poi, _ in kept]

    stage_a_drops["low_notability"] = dropped_low_notability
    stage_a_drops["over_cap"] = dropped_over_cap
    stage_a_drops["no_coords"] = dropped_no_coords
    stage_a_drops["out_of_bbox"] = dropped_out_of_bbox
    logger.info(
        "assemble(%s): notability filter kept %d/%d POIs (target=%d); drops: %s",
        ctx.slug,
        len(pois),
        len(scored),
        target,
        _drop_report(stage_a_drops),
    )
    if len(pois) < MIN_POIS:
        raise ValueError(
            f"refusing to assemble a thin city {ctx.slug!r}: only {len(pois)} POI(s) "
            f"survived the notability filter (from {raw_in} raw candidates), need at "
            f"least {MIN_POIS}. Per-rule drops: {_drop_report(stage_a_drops)}"
        )

    # 1 (cont.) DISAMBIGUATE distinct places that share a name (geo-split), then
    # POST-MERGE COLLISION SWEEP over the surviving set.
    _disambiguate_same_name(pois, ctx)
    _sweep_collisions(pois)

    # 4. FORCED GRAVITY DISTRIBUTION.
    _assign_tiers(pois)

    # 5. wiki_extracts keyed by slug.
    wiki_extracts = {slugify(e.poi_name): e for e in extracts}

    return AssembledCity(
        slug=ctx.slug,
        pois=pois,
        wiki_extracts=wiki_extracts,
        cloud_deployed=cloud_deployed,
    )


# ---------------------------------------------------------------------------
# write_city — the ONLY disk / registry touch.
# ---------------------------------------------------------------------------
def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (tmp sibling + ``os.replace``)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _book_log(city: AssembledCity, ctx: CityContext) -> dict:
    """A ``scripts/wiki_fetch.py``-format book-log: one 'Wikipedia' book whose
    ``chunks_processed`` name each pinned ``{poi_slug}-rev-{revid}`` file (the
    slug ``wiki_fetch.scan_log`` scans for)."""
    processed_at = date.today().isoformat()
    chunks = [
        {
            "chunk": f"{poi_slug}-rev-{ext.revid}",
            "processed_at": processed_at,
            "beats_extracted": 0,
            "pois_touched": [ext.poi_name],
        }
        for poi_slug, ext in city.wiki_extracts.items()
    ]
    return {
        "city": ctx.display_name,
        "books_processed": [
            {
                "book_title": "Wikipedia",
                "author": "Wikipedia contributors",
                "source": "wikipedia",
                "chunks_processed": chunks,
            }
        ],
    }


def _register(city: AssembledCity, ctx: CityContext, registry_path: Path | None) -> None:
    """Register the city. When ``registry_path`` is the default (or None) this
    writes the real ``src/cities.json``; otherwise it redirects the module global
    to ``registry_path`` under try/finally so the global is NEVER leaked to
    sibling tests (the target is seeded from the current registry first)."""
    # THE COUNTRY IS PART OF BEING ONBOARDED (Docs/adr/0006). Public holidays are
    # a country's, so a door tagged `PH off` is open or shut by this and nothing
    # else — and the planner refuses a dated day in a city that has none. Caught
    # HERE, where the city is registered and the person is watching, rather than
    # months later at the first walker's first dated request.
    if not ctx.country:
        raise ValueError(
            f"refusing to register {city.slug!r} with no country: opening hours are "
            "read against a country's public holidays, so a city without one cannot "
            "plan a dated day. Pass a two-letter country (FR, GB, US) when onboarding."
        )
    entry = {
        "display_name": ctx.display_name,
        "bbox": list(ctx.bbox),
        "country": ctx.country,
        "cloud_deployed": city.cloud_deployed,
    }
    default = city_registry._REGISTRY_PATH
    if registry_path is None or registry_path == default:
        city_registry.register_city(city.slug, entry)
        return

    original = city_registry._REGISTRY_PATH
    try:
        # Seed the redirected registry from the CURRENT (real) registry contents
        # so register_city merges into a full copy, not an empty file.
        current = dict(city_registry.load_registry())
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        city_registry._REGISTRY_PATH = registry_path
        city_registry.load_registry.cache_clear()
        city_registry.register_city(city.slug, entry)
    finally:
        city_registry._REGISTRY_PATH = original
        city_registry.load_registry.cache_clear()


def write_city(
    city: AssembledCity,
    ctx: CityContext,
    *,
    data_root: Path | None = None,
    registry_path: Path | None = None,
) -> Path:
    """Atomically write the ``data/{slug}/`` corpus and register the city.

    Writes EXACTLY: ``poi-raw.json``, ``areas.json`` (``[]``), ``within_edges.json``
    (well-formed empty), ``book-log.json``, and one verbatim
    ``wikipedia/{poi_slug}-rev-{revid}.txt`` per ``WikiExtract`` — nothing else.
    Refuses to write a thin city (< 30 POIs). Returns the city directory.
    """
    # SLUG WALL (defense in depth): validate BEFORE any mkdir/write, so a
    # path-traversal slug ('../evil') can never escape the data root. The registry
    # (``register_city``) also validates the slug, but that runs LAST — after the
    # corpus is already on disk — so it cannot protect the filesystem. Reuse
    # city_registry's canonical allowlist. Protects the CLI + any direct caller.
    if not city_registry._SLUG_RE.match(city.slug or ""):
        raise ValueError(
            f"refusing to write city with invalid slug {city.slug!r}: must match "
            f"{city_registry._SLUG_RE.pattern} (lowercase letters/digits/underscore, "
            "letter-initial)"
        )

    if len(city.pois) < MIN_POIS:
        raise ValueError(
            f"refusing to write a thin city {city.slug!r}: only {len(city.pois)} POI(s), "
            f"need at least {MIN_POIS}"
        )

    # Explicit ``data_root`` wins; otherwise the ONE shared resolver
    # (``$ONBOARD_DATA_ROOT`` when set, else ``<repo>/data``) — same result as the
    # prior inline resolution, now defined once so scripts + assemble never drift.
    root = data_root or city_registry.onboard_data_root()
    city_dir = root / city.slug
    wiki_dir = city_dir / "wikipedia"
    wiki_dir.mkdir(parents=True, exist_ok=True)

    _atomic_write_text(
        city_dir / "poi-raw.json",
        json.dumps(city.pois, indent=2, ensure_ascii=False) + "\n",
    )
    _atomic_write_text(city_dir / "areas.json", json.dumps([], indent=2) + "\n")
    _atomic_write_text(
        city_dir / "within_edges.json",
        json.dumps(
            {
                "_meta": {
                    "generated_by": "src/onboard/assemble.py",
                    "city_name": city.slug,
                    "total_pois": len(city.pois),
                    "total_areas": 0,
                    "poi_to_area_count": 0,
                    "area_to_area_count": 0,
                    "orphans": [],
                },
                "poi_to_area": [],
                "area_to_area": [],
            },
            indent=2,
        )
        + "\n",
    )
    _atomic_write_text(
        city_dir / "book-log.json",
        json.dumps(_book_log(city, ctx), indent=2, ensure_ascii=False) + "\n",
    )
    for poi_slug, ext in city.wiki_extracts.items():
        _atomic_write_text(wiki_dir / f"{poi_slug}-rev-{ext.revid}.txt", ext.text)

    _register(city, ctx, registry_path)
    return city_dir
