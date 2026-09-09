"""Step-4 CORE tests for ``src/onboard/assemble.py`` (pure ``assemble`` +
disk/registry ``write_city``).

Pure — no Neo4j, no network. Candidates are built from the committed
``tests/fixtures/onboard/london/`` fixtures via the real Step-3 connectors
(``run/`` for the fuller geosearch + extracts, the flat wikidata/osm/wikivoyage
files for cross-source signal), then merged/written into ``tmp_path``. Each
behavior below has a mutation/undo target documented on the test so a reverted
fix goes RED. Run with:
    make test-file FILE=tests/test_onboard_assemble.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from src import city_registry
from src.onboard.assemble import (
    TARGET_POIS,
    AssembledCity,
    WikiExtract,
    _stage_a_filter,
    assemble,
    write_city,
)
from src.onboard.assemble import _norm as _assemble_norm
from src.onboard.models import (
    CityContext,
    ConnectorResult,
    DiscoveryPointer,
    PoiCandidate,
)
from src.onboard.sources.osm import OsmSource
from src.onboard.sources.wikidata import WikidataConnector
from src.onboard.sources.wikipedia import WikipediaConnector
from src.onboard.sources.wikivoyage import WikivoyageConnector
from tests.test_data_integrity import _norm  # the INDEPENDENT CI guard's normalizer
from tests.test_gravity_distribution import _distribution_failures

FIXTURES = Path(__file__).parent / "fixtures" / "onboard" / "london"


# ---------------------------------------------------------------------------
# Fixture loaders / builders.
# ---------------------------------------------------------------------------
def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _ctx() -> CityContext:
    city = _load("run/city.json")
    return CityContext(
        slug=city["slug"],
        display_name=city["display_name"],
        bbox=tuple(city["bbox"]),
        country=city.get("country"),
    )


def _base_results(ctx: CityContext) -> list[ConnectorResult]:
    """The four real connectors parsed over the committed fixtures: the fuller
    ``run/`` geosearch + the flat wikidata/osm/wikivoyage for cross-source signal."""
    return [
        WikipediaConnector().parse(_load("run/wikipedia_geosearch.json"), ctx),
        WikidataConnector().parse(_load("wikidata_sparql.json"), ctx),
        OsmSource().parse(_load("osm_overpass.json"), ctx),
        WikivoyageConnector().parse(_load("wikivoyage_page.json"), ctx),
    ]


def _extracts() -> list[WikiExtract]:
    raw = _load("run/wikipedia_extracts.json")
    return [
        WikiExtract(
            poi_name=title,
            revid=str(d["revid"]),
            text=d["extract"],
            article_title=title,
            url=f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
        )
        for title, d in raw.items()
    ]


# Collision checks copied from tests/test_data_integrity.py (:92 accent/case dup,
# :151 name/variation collision) so we assert them directly on assemble's output.
def _accent_dup_collisions(pois: list[dict]) -> list[str]:
    seen: dict[str, str] = {}
    dupes: list[str] = []
    for p in pois:
        key = _norm(p["name"])
        if key in seen:
            dupes.append(f"{seen[key]!r} <-> {p['name']!r}")
        else:
            seen[key] = p["name"]
    return dupes


def _variation_collisions(pois: list[dict]) -> list[str]:
    reverse: dict[str, str] = {}
    collisions: list[str] = []
    for p in pois:
        keys = {_norm(p["name"])}
        for v in p.get("name_variations", []) or []:
            keys.add(_norm(v))
        for k in keys:
            if not k:
                continue
            owner = reverse.get(k)
            if owner and owner != p["name"]:
                collisions.append(f"{k!r} owned by {owner!r} and {p['name']!r}")
            reverse[k] = p["name"]
    return collisions


# ---------------------------------------------------------------------------
# Behaviors.
# ---------------------------------------------------------------------------
def test_assemble_norm_matches_data_integrity_guard() -> None:
    """assemble's merge key MUST be the byte-for-byte copy of the CI guard's
    ``_norm`` (accent-strip + casefold + strip) — if they ever drift, a merge
    that assemble thinks is unique collides in test_data_integrity."""
    for sample in ["Café de Paris", "CAFE  de  paris", "  Notre-Dame  ", "Big Ben", "Æthel"]:
        assert _assemble_norm(sample) == _norm(sample)


def test_at_least_30_pois_all_geofenced_and_audited() -> None:
    """>=30 POIs; every POI carries lat/lon and a truthy gravity_audit.
    UNDO: drop the ``_assign_tiers`` audit stamp (or the coord walk) -> RED."""
    ctx = _ctx()
    city = assemble(_base_results(ctx), ctx, _extracts())

    assert len(city.pois) >= 30, f"expected >=30 POIs, got {len(city.pois)}"
    for p in city.pois:
        assert p["latitude"] is not None and p["longitude"] is not None, p["name"]
        assert p["_pipeline"]["gravity_audit"], f"{p['name']} has no gravity_audit"
        audit = p["_pipeline"]["gravity_audit"]
        assert audit["signal_source"] == "onboard_auto_v1"
        assert audit["assigned_gravity"] == p["importance_tier"]
        assert audit["of"] == len(city.pois)


def test_forced_distribution_satisfies_shape_guard() -> None:
    """The forced rank-quantile tiers pass test_gravity_distribution's shape rule.
    UNDO: change the boundaries to a T3-heavy split (e.g. all mid) -> RED."""
    ctx = _ctx()
    city = assemble(_base_results(ctx), ctx, _extracts())

    counts = Counter(p["importance_tier"] for p in city.pois)
    failures = _distribution_failures(counts, len(city.pois))
    assert not failures, "unhealthy tier shape:\n" + "\n".join(failures)


def test_accent_and_variation_collision_free() -> None:
    """Accent/case dups and fuzzy near-dups merge; no name/variation collides.
    UNDO: bucket by ``canonical_name_key`` (keeps accents) -> the accented pair
    stays two POIs that collide under ``_norm`` -> RED."""
    ctx = _ctx()
    # Accent dup >75 m apart: exact-_norm merge must still collapse it (a wrong,
    # accent-KEEPING key would leave two POIs the fuzzy pass can't rejoin at range).
    accent_pair = ConnectorResult(
        candidates=[
            PoiCandidate(
                name="Café de Paris",
                source="wikivoyage",
                source_url="https://en.wikivoyage.org/wiki/London",
                latitude=51.510,
                longitude=-0.130,
            ),
            PoiCandidate(
                name="Cafe de Paris",
                source="osm",
                source_url="https://www.openstreetmap.org/node/999001",
                latitude=51.512,
                longitude=-0.135,
            ),
        ]
    )
    # Fuzzy near-dup at the same coords as the geosearch St Paul's -> one POI with
    # the alternate spelling demoted to a variation.
    fuzzy_dup = ConnectorResult(
        candidates=[
            PoiCandidate(
                name="St. Paul's Cathedral",
                source="wikivoyage",
                source_url="https://en.wikivoyage.org/wiki/London",
                latitude=51.5138,
                longitude=-0.0984,
            )
        ]
    )
    city = assemble([*_base_results(ctx), accent_pair, fuzzy_dup], ctx, _extracts())

    assert _accent_dup_collisions(city.pois) == []
    assert _variation_collisions(city.pois) == []

    # The accented pair collapsed to exactly one POI.
    cafe = [p for p in city.pois if _norm(p["name"]) == "cafe de paris"]
    assert len(cafe) == 1, f"accent dup did not merge: {[p['name'] for p in cafe]}"

    # The fuzzy near-dup became a name_variation on the canonical St Paul's POI.
    stpauls = next(p for p in city.pois if p["name"] == "St Paul's Cathedral")
    assert "St. Paul's Cathedral" in stpauls["name_variations"]


def test_out_of_bbox_candidate_dropped_and_centroid_in_bbox() -> None:
    """A candidate whose coords fall outside the bbox is DROPPED (never clipped),
    and the surviving centroid stays inside the bbox.
    UNDO: remove the bbox filter -> the sentinel is present -> RED."""
    ctx = _ctx()
    min_lat, max_lat, min_lon, max_lon = ctx.bbox
    sentinel = ConnectorResult(
        candidates=[
            PoiCandidate(
                name="Out Of Bbox Sentinel",
                source="wikidata",
                source_url="http://www.wikidata.org/entity/Q99999999",
                latitude=10.0,  # far outside London
                longitude=40.0,
            )
        ]
    )
    city = assemble([*_base_results(ctx), sentinel], ctx, _extracts())

    names = {p["name"] for p in city.pois}
    assert "Out Of Bbox Sentinel" not in names

    mean_lat = sum(p["latitude"] for p in city.pois) / len(city.pois)
    mean_lon = sum(p["longitude"] for p in city.pois) / len(city.pois)
    assert min_lat <= mean_lat <= max_lat
    assert min_lon <= mean_lon <= max_lon


def test_wall3_discovery_pointer_never_reaches_disk(tmp_path: Path) -> None:
    """WALL 3: a ConnectorResult carrying a shadow-library DiscoveryPointer must
    leave NO trace — not a filename, not a byte, not a POI name/variation.
    UNDO: give AssembledCity a ``pointers`` field that write_city dumps -> RED."""
    ctx = _ctx()
    pointer_result = ConnectorResult(
        pointers=[
            DiscoveryPointer(
                source="annas_archive",
                title="SHADOWMARKER",
                source_url="https://annas-archive.org/md5/x",
            )
        ]
    )
    city = assemble([*_base_results(ctx), pointer_result], ctx, _extracts())
    city_dir = write_city(
        city, ctx, data_root=tmp_path, registry_path=tmp_path / "cities.json"
    )

    # (a) Only the whitelisted files exist — nothing derived from the pointer.
    expected = {
        Path("poi-raw.json"),
        Path("areas.json"),
        Path("within_edges.json"),
        Path("book-log.json"),
    }
    for slug, ext in city.wiki_extracts.items():
        expected.add(Path("wikipedia") / f"{slug}-rev-{ext.revid}.txt")
    written = {p.relative_to(city_dir) for p in city_dir.rglob("*") if p.is_file()}
    assert written == expected, f"unexpected/missing files: {written ^ expected}"

    # (b) No written byte contains the pointer's title / host / url.
    forbidden = ("SHADOWMARKER", "annas-archive", "annas_archive", "md5/x")
    for path in city_dir.rglob("*"):
        if path.is_file():
            blob = path.read_text()
            for needle in forbidden:
                assert needle not in blob, f"{needle!r} leaked into {path.name}"

    # (c) No POI name or variation derives from the pointer.
    for p in city.pois:
        assert "SHADOWMARKER" not in p["name"]
        assert all("SHADOWMARKER" not in v for v in p["name_variations"])


def test_registry_entry_written_without_centre(tmp_path: Path) -> None:
    """write_city registers london with display_name/bbox/country/cloud_deployed
    and NO ``centre`` (register_city validates only those three fields).
    UNDO: remove the register_city call -> london absent from cities.json -> RED."""
    ctx = _ctx()
    city = assemble(_base_results(ctx), ctx, _extracts())
    registry_path = tmp_path / "cities.json"
    write_city(city, ctx, data_root=tmp_path, registry_path=registry_path)

    registry = json.loads(registry_path.read_text())
    assert "london" in registry
    entry = registry["london"]
    assert entry["display_name"] == "London"
    assert entry["bbox"] == [51.28, 51.7, -0.51, 0.33]
    assert entry["cloud_deployed"] is False
    assert "centre" not in entry
    # M10 (Docs/adr/0006): the country its opening hours are read against.
    assert entry["country"] == "GB"


def test_a_city_with_no_country_is_refused_before_it_is_registered(tmp_path: Path) -> None:
    """M10: public holidays are a country's, so a door tagged ``PH off`` is open
    or shut by that and nothing else — and the planner refuses a dated day in a
    city that has none. A city onboarded without one would look finished and then
    fail at its first walker's first dated request, months later and far from
    here. So it is refused where the city is registered and someone is watching.

    UNDO: drop the country guard from ``_register`` -> a countryless city
    registers happily and breaks at planning time instead -> RED.
    """
    ctx = _ctx().model_copy(update={"country": None})
    city = assemble(_base_results(ctx), ctx, _extracts())
    with pytest.raises(ValueError, match="country"):
        write_city(city, ctx, data_root=tmp_path, registry_path=tmp_path / "cities.json")

    assert not (tmp_path / "cities.json").exists(), "nothing was registered"


def test_a_country_that_is_not_two_letters_is_refused_at_construction() -> None:
    """The opening-hours library keys holidays by a two-letter code, so anything
    else is a country nobody can read hours against — refused where the frame is
    built rather than carried to the registry.
    UNDO: drop the country validator -> 'France' reaches the registry -> RED."""
    for bad in ("France", "F", "", "F1"):
        with pytest.raises(Exception, match=r"country|two-letter"):
            CityContext(
                slug="paris", display_name="Paris", bbox=(48.8, 48.9, 2.2, 2.4), country=bad
            )
    # Case and padding are normalised rather than refused.
    assert CityContext(
        slug="paris", display_name="Paris", bbox=(48.8, 48.9, 2.2, 2.4), country=" fr "
    ).country == "FR"


def test_registry_global_not_leaked(tmp_path: Path) -> None:
    """The redirected registry global is restored (try/finally), so the real
    src/cities.json is untouched and no sibling test sees a leaked path."""
    original = city_registry._REGISTRY_PATH
    committed_before = original.read_text()  # snapshot the real cities.json bytes
    ctx = _ctx()
    city = assemble(_base_results(ctx), ctx, _extracts())
    write_city(city, ctx, data_root=tmp_path, registry_path=tmp_path / "cities.json")

    assert original == city_registry._REGISTRY_PATH
    # The committed registry FILE is byte-unchanged by the tmp-redirected write.
    # (Was ``"london" not in load_registry()``; that inverts once london is a real
    # committed city — an unchanged-bytes snapshot proves non-mutation regardless.)
    assert original.read_text() == committed_before


def test_thin_city_raises_valueerror_naming_count(tmp_path: Path) -> None:
    """write_city refuses a thin city and names the count; nothing is written."""
    ctx = _ctx()
    tiny = AssembledCity(
        slug="london",
        pois=[{"name": f"P{i}", "latitude": 51.5, "longitude": -0.1} for i in range(5)],
        wiki_extracts={},
    )
    with pytest.raises(ValueError, match=r"\b5\b"):
        write_city(tiny, ctx, data_root=tmp_path, registry_path=tmp_path / "cities.json")

    assert not (tmp_path / "london").exists()


def test_write_city_rejects_traversal_slug_and_writes_nothing(tmp_path: Path) -> None:
    """write_city validates ``city.slug`` at the VERY TOP — before any mkdir/write
    — so a path-traversal slug ('../evil') raises ValueError and creates NO file:
    not inside the data root, and NOT at the escaped target outside it. (>=30 POIs
    so the slug guard, not the thin-city guard, is what fires.)

    UNDO: remove the slug guard at the top of write_city -> it mkdirs
    tmp_path/../evil and writes poi-raw.json OUTSIDE the data root (register_city's
    late slug check raises only AFTER the files are on disk) -> the escaped target
    exists -> RED."""
    ctx = _ctx()  # a valid ctx; write_city keys off city.slug, not ctx.slug
    evil = AssembledCity(
        slug="../evil",
        pois=[{"name": f"P{i}", "latitude": 51.5, "longitude": -0.1} for i in range(30)],
        wiki_extracts={},
    )
    escaped = tmp_path.parent / "evil"
    assert not escaped.exists(), "precondition: escaped target must not pre-exist"

    with pytest.raises(ValueError, match=r"slug"):
        write_city(evil, ctx, data_root=tmp_path, registry_path=tmp_path / "cities.json")

    assert not escaped.exists(), f"traversal target was written OUTSIDE the data root: {escaped}"
    assert not (tmp_path / "..evil").exists()
    written = list(tmp_path.iterdir())
    assert written == [], f"files written into the data root: {written}"


# ---------------------------------------------------------------------------
# FIX 1 — every write_text must pass encoding="utf-8" (accented cities round-trip
# on a non-utf-8-locale runner; validate_beats READS with encoding="utf-8").
# ---------------------------------------------------------------------------
def test_write_city_writes_utf8_and_roundtrips_nonascii(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every corpus file write_city produces passes encoding='utf-8', so an
    accented POI/extract (Café/Zürich) round-trips byte-exact — matching
    validate_beats' utf-8 read.
    UNDO: drop encoding='utf-8' from assemble._atomic_write_text -> a corpus file
    is written with encoding=None -> RED."""
    captured: list[tuple[Path, object]] = []
    real_write_text = Path.write_text

    def _spy(self, data, *args, **kwargs):
        captured.append((self, kwargs.get("encoding")))
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _spy)

    ctx = _ctx()
    city = assemble(_base_results(ctx), ctx, _extracts())
    city.pois[0]["name"] = "Café de Flore"
    city.wiki_extracts["cafe-de-flore-nonascii"] = WikiExtract(
        poi_name="Café de Flore",
        revid="1",
        text="Zürich, naïve façade — Café crème.",
        article_title="Café de Flore",
        url="https://en.wikipedia.org/wiki/Cafe_de_Flore",
    )
    city_dir = write_city(city, ctx, data_root=tmp_path, registry_path=tmp_path / "cities.json")

    # (a) EVERY corpus-file write under the city dir passed encoding='utf-8'.
    corpus_writes = [(p, enc) for p, enc in captured if city_dir in p.parents]
    assert corpus_writes, "no corpus files were written"
    for p, enc in corpus_writes:
        assert enc == "utf-8", f"{p.name} written with encoding={enc!r}, not 'utf-8'"

    # (b) Non-ASCII POI name + extract text round-trip byte-exact under a utf-8 read.
    pois = json.loads((city_dir / "poi-raw.json").read_text(encoding="utf-8"))
    assert any(p["name"] == "Café de Flore" for p in pois)
    extract_txt = (city_dir / "wikipedia" / "cafe-de-flore-nonascii-rev-1.txt").read_text(
        encoding="utf-8"
    )
    assert extract_txt == "Zürich, naïve façade — Café crème."


def test_cli_atomic_write_text_uses_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI's beats.json writer also passes encoding='utf-8', so accented beats
    round-trip on a non-utf-8-locale runner (validate_beats reads utf-8).
    UNDO: drop encoding='utf-8' from cli._atomic_write_text -> RED."""
    from src.onboard.cli import _atomic_write_text as _cli_atomic_write_text

    captured: list[object] = []
    real_write_text = Path.write_text

    def _spy(self, data, *args, **kwargs):
        captured.append(kwargs.get("encoding"))
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _spy)

    target = tmp_path / "beats.json"
    _cli_atomic_write_text(target, "Zürich café — naïve façade")

    assert captured == ["utf-8"], captured
    assert target.read_text(encoding="utf-8") == "Zürich café — naïve façade"


# ---------------------------------------------------------------------------
# FIX 2 — same-name merging must be GEO-AWARE (distinct places sharing a name are
# NOT silently collapsed; co-located duplicates still merge to one POI).
# ---------------------------------------------------------------------------
def test_same_name_distant_places_are_kept_separate() -> None:
    """Two GENUINELY DISTINCT places sharing an exact name but ~17 km apart must
    stay TWO POIs (geo-aware split), deterministically disambiguated — never
    silently collapsed to one with the other's data lost.
    UNDO: revert the geo-split (merge the whole exact-norm bucket) -> one POI -> RED."""
    ctx = _ctx()
    distant = ConnectorResult(
        candidates=[
            PoiCandidate(
                name="St Mary's Church",
                source="osm",
                source_url="https://www.openstreetmap.org/node/111",
                latitude=51.55,  # North London
                longitude=-0.10,
            ),
            PoiCandidate(
                name="St Mary's Church",
                source="wikidata",
                source_url="http://www.wikidata.org/entity/Q222",
                latitude=51.40,  # South London, ~17 km away
                longitude=-0.05,
            ),
        ]
    )
    city = assemble([*_base_results(ctx), distant], ctx, _extracts())

    marys = [p for p in city.pois if p["name"].startswith("St Mary's Church")]
    assert sorted(p["name"] for p in marys) == [
        "St Mary's Church",
        "St Mary's Church (2)",
    ], [p["name"] for p in marys]

    # Both distinct places survived — neither silently dropped.
    coords = {(round(p["latitude"], 2), round(p["longitude"], 2)) for p in marys}
    assert coords == {(51.55, -0.10), (51.40, -0.05)}, coords

    # No accent/case collision introduced by the disambiguation.
    assert _accent_dup_collisions(city.pois) == []
    assert _variation_collisions(city.pois) == []


def test_same_name_colocated_still_merges() -> None:
    """Two same-name candidates ~20 m apart are the SAME place -> exactly ONE POI:
    the geo-split must not OVER-split co-located duplicates.
    UNDO: n/a (guards the fix against over-correction; distant test guards under)."""
    ctx = _ctx()
    colocated = ConnectorResult(
        candidates=[
            PoiCandidate(
                name="St Mary's Church",
                source="osm",
                source_url="https://www.openstreetmap.org/node/1",
                latitude=51.50000,
                longitude=-0.10000,
            ),
            PoiCandidate(
                name="St Mary's Church",
                source="wikidata",
                source_url="http://www.wikidata.org/entity/Q1",
                latitude=51.50018,  # ~20 m north
                longitude=-0.10000,
            ),
        ]
    )
    city = assemble([*_base_results(ctx), colocated], ctx, _extracts())

    marys = [p for p in city.pois if p["name"].startswith("St Mary's Church")]
    assert len(marys) == 1, [p["name"] for p in marys]
    assert marys[0]["name"] == "St Mary's Church"
    # It merged the two co-located sources into one POI.
    assert marys[0]["_pipeline"]["gravity_audit"]["cross_source_agreement"] == 2


# ---------------------------------------------------------------------------
# FIX 3 — the registry redirect must restore the global even if register_city
# raises (try/finally), and never corrupt the real src/cities.json.
# ---------------------------------------------------------------------------
def test_registry_global_restored_on_register_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_register restores city_registry._REGISTRY_PATH even when register_city
    raises mid-write, so a failed onboarding leaks neither the redirected global
    to sibling tests nor a byte into the real src/cities.json.
    UNDO: replace the try/finally in _register with a plain call -> the global
    stays redirected after the raise -> RED."""
    original_path = city_registry._REGISTRY_PATH
    real_registry_bytes = original_path.read_bytes()

    def _boom(*args, **kwargs):
        raise RuntimeError("register blew up")

    monkeypatch.setattr(city_registry, "register_city", _boom)

    ctx = _ctx()
    city = assemble(_base_results(ctx), ctx, _extracts())
    with pytest.raises(RuntimeError, match="register blew up"):
        write_city(city, ctx, data_root=tmp_path, registry_path=tmp_path / "cities.json")

    # The module global was restored despite the raise ...
    assert original_path == city_registry._REGISTRY_PATH
    # ... and the real src/cities.json is byte-for-byte untouched.
    assert original_path.read_bytes() == real_registry_bytes


# ---------------------------------------------------------------------------
# NOTABILITY FILTER — turn a live city's 55K-candidate firehose into ~48 real
# landmarks. STAGE A (per-candidate hard blocklist, pre-merge) + STAGE B
# (post-merge notability keep) + composite-score CAP. Candidates are built
# DIRECTLY here (not via the connectors) so each test controls the exact
# ``PoiCandidate.meta`` scalars the new contract keys off. Each behavior names
# its mutation/undo target so a reverted fix goes RED.
# ---------------------------------------------------------------------------
def _pt(i: int) -> tuple[float, float]:
    """A distinct, walkable-core coordinate inside the London bbox and within
    ~7 km of its centre (so STAGE A's far-outlier rule never fires for these)."""
    return (51.500 + (i % 20) * 0.002, -0.100 + (i // 20) * 0.002)


def _wiki(name: str, i: int, *, sitelinks: int | None = None) -> PoiCandidate:
    """A wikipedia geosearch hit (its own enwiki article -> notable)."""
    meta: dict = {"pageid": 1000 + i}
    if sitelinks is not None:
        meta["sitelinks"] = sitelinks
    lat, lon = _pt(i)
    return PoiCandidate(
        name=name,
        source="wikipedia",
        source_url=f"https://en.wikipedia.org/wiki/{name.replace(' ', '_')}",
        latitude=lat,
        longitude=lon,
        meta=meta,
    )


def _wd(
    name: str,
    i: int,
    *,
    sitelinks: int = 0,
    enwiki: str | None = None,
    p31: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> PoiCandidate:
    plat, plon = _pt(i)
    meta: dict = {"qid": f"Q{2000 + i}", "sitelinks": sitelinks}
    if enwiki is not None:
        meta["enwiki"] = enwiki
    if p31 is not None:
        meta["p31"] = p31
    return PoiCandidate(
        name=name,
        source="wikidata",
        source_url=f"http://www.wikidata.org/entity/Q{2000 + i}",
        latitude=lat if lat is not None else plat,
        longitude=lon if lon is not None else plon,
        meta=meta,
    )


def _osm(
    name: str,
    i: int,
    *,
    tourism: str | None = None,
    historic: str | None = None,
    osm_wikipedia: str | None = None,
    osm_wikidata: str | None = None,
    sitelinks: int | None = None,
) -> PoiCandidate:
    meta: dict = {"osm_type": "node", "osm_id": 3000 + i}
    if tourism is not None:
        meta["tourism"] = tourism
    if historic is not None:
        meta["historic"] = historic
    if osm_wikipedia is not None:
        meta["osm_wikipedia"] = osm_wikipedia
    if osm_wikidata is not None:
        meta["osm_wikidata"] = osm_wikidata
    if sitelinks is not None:
        meta["sitelinks"] = sitelinks
    lat, lon = _pt(i)
    return PoiCandidate(
        name=name,
        source="osm",
        source_url=f"https://www.openstreetmap.org/node/{3000 + i}",
        latitude=lat,
        longitude=lon,
        meta=meta,
    )


# 5 CJK-only names: slugify(name) == "" -> STAGE A empty-slug reject (non-Latin).
_EMPTY_SLUG_NAMES = ["北京", "東京", "京都", "大阪", "名古屋"]


def _firehose(ctx: CityContext) -> list[ConnectorResult]:
    """~200 raw candidates: 44 real landmarks + every STAGE-A junk class + a
    batch of non-notable minor nodes (survive STAGE A, dropped by STAGE B)."""
    cands: list[PoiCandidate] = []
    # 44 real landmarks (own enwiki article).
    cands += [_wiki(f"Landmark {i}", i) for i in range(44)]
    # 60 lodging/transient OSM classes.
    cands += [_osm(f"Hotel {i}", 60 + i, tourism="hotel") for i in range(40)]
    cands += [_osm(f"Hostel {i}", 100 + i, tourism="hostel") for i in range(20)]
    # 20 EVENT articles by name (Olympics regex) + 15 by Wikidata P31.
    cands += [
        _wiki(f"Sport {i} at the 2016 Summer Olympics", 120 + i) for i in range(20)
    ]
    cands += [_wd(f"Ceremony {i}", 140 + i, p31="Q5|Q1656682") for i in range(15)]
    # 20 OSM historic grave/marker junk.
    cands += [_osm(f"Grave {i}", 160 + i, historic="grave") for i in range(20)]
    # 5 non-Latin empty-slug names.
    cands += [_wd(name, 180 + k) for k, name in enumerate(_EMPTY_SLUG_NAMES)]
    # 8 far outliers (~25 km west of centre — in the bbox, but unwalkable).
    cands += [
        _wd(f"Outlier {i}", 190 + i, lat=51.47, lon=-0.45) for i in range(8)
    ]
    # 25 non-notable minor nodes (a coordinate + a bare qid, nothing else).
    cands += [_wd(f"Minor {i}", 40 + i, sitelinks=1) for i in range(25)]
    return [ConnectorResult(candidates=cands)]


def test_firehose_collapses_to_real_landmarks_only() -> None:
    """A ~200-candidate firehose with hotels/events/graves/empty-slug + 44 real
    landmarks collapses to <= TARGET real landmarks with NONE of the junk.
    UNDO: revert STAGE A (``_stage_a_filter``) or STAGE B (``_is_notable``) ->
    a hotel/event/minor node reappears, or the count blows past the cap -> RED."""
    ctx = _ctx()
    city = assemble(_firehose(ctx), ctx, [], target_pois=40)
    names = {p["name"] for p in city.pois}

    # The CAP holds and the city is still healthy.
    assert 30 <= len(city.pois) <= 40, len(city.pois)
    assert len(city.pois) <= TARGET_POIS

    # EVERY survivor is a real landmark — no junk class leaked through.
    assert all(n.startswith("Landmark ") for n in names), sorted(names)[:10]
    for bad in ("Hotel ", "Hostel ", "Grave ", "Minor ", "Outlier ", "Ceremony "):
        assert not any(n.startswith(bad) for n in names), bad
    assert not any("Olympics" in n for n in names)
    assert not (set(names) & set(_EMPTY_SLUG_NAMES)), "non-Latin empty-slug survived"


def test_admin_area_nodes_dropped_stage_a() -> None:
    """The CITY node itself + its boroughs are administrative areas, NEVER tour
    stops — the live London onboard surfaced 'London' as a nonsensical STOP ("head
    for London — a 3-minute walk from here"). STAGE A drops them by Wikidata P31
    (Q515 city / Q211690 borough, both VERIFIED against Q84 / Q179351) and, for an
    OSM-only node carrying no p31, by name-equals-city. A real landmark (museum) is
    untouched.
    UNDO: remove rule 5b (the ADMIN P31 test AND the name-equals-city test) -> the
    'London' city node + the borough survive STAGE A -> RED."""
    ctx = _ctx()  # london / London
    cands = [
        _wd("London", 0, sitelinks=400, enwiki="London", p31="Q515"),  # city node, verified P31
        _osm("London", 1),  # OSM-only city node, NO p31 -> name guard must catch it
        _wd("City of Westminster", 2, sitelinks=90, enwiki="City of Westminster", p31="Q211690"),
        _wd("British Museum", 3, sitelinks=250, enwiki="British Museum", p31="Q33506"),  # museum
    ]
    filtered, drops = _stage_a_filter([ConnectorResult(candidates=cands)], ctx)
    survivors = {c.name for r in filtered for c in r.candidates}

    assert "British Museum" in survivors, "a real landmark (museum) must survive"
    assert "London" not in survivors, "city node (by P31 AND by name) must be dropped"
    assert "City of Westminster" not in survivors, "borough (P31 Q211690) must be dropped"
    assert drops["admin_area"] >= 3, drops


def test_pinned_anchor_landmarks_always_survive_the_cap() -> None:
    """The unmissable anchors (British Museum, Tower of London, Buckingham Palace,
    Westminster Abbey, Tower Bridge) — high sitelinks + enwiki — are ALWAYS kept,
    even when the candidate set exceeds the cap and lower landmarks are trimmed.
    UNDO: drop the sitelinks term from the composite score (or the CAP sort) ->
    an anchor can fall below a filler POI and be trimmed -> RED."""
    ctx = _ctx()
    anchors = [
        "British Museum",
        "Tower of London",
        "Buckingham Palace",
        "Westminster Abbey",
        "Tower Bridge",
    ]
    cands: list[PoiCandidate] = []
    # Each anchor is a single high-sitelinks + enwiki Wikidata item — so ONLY its
    # sitelinks (via the composite score) keeps it above the fillers.
    for k, name in enumerate(anchors):
        cands.append(_wd(name, k, sitelinks=250 + k, enwiki=name))
    # 60 enwiki filler landmarks whose names sort ALPHABETICALLY BEFORE every
    # anchor, so if the sitelinks score term is removed (scores tie at the enwiki
    # bonus) the name tiebreak fills the cap with fillers and TRIMS the anchors.
    cands += [_wiki(f"Aaa Filler {i:02d}", 10 + i) for i in range(60)]

    city = assemble([ConnectorResult(candidates=cands)], ctx, [])
    names = {p["name"] for p in city.pois}

    assert len(city.pois) <= TARGET_POIS
    for anchor in anchors:
        assert anchor in names, f"pinned anchor trimmed by the cap: {anchor}"


def test_thin_after_filter_raises_with_drop_counts() -> None:
    """Fewer than MIN_POIS survivors is a hard ValueError naming the per-rule
    drop counts — never an auto-relaxed thin city.
    UNDO: remove the ``len(pois) < MIN_POIS`` raise -> a 20-POI city assembles
    silently -> RED."""
    ctx = _ctx()
    cands = [_wiki(f"Small {i}", i) for i in range(20)]
    cands += [_osm(f"Hotel {i}", 60 + i, tourism="hotel") for i in range(2)]

    with pytest.raises(ValueError) as excinfo:
        assemble([ConnectorResult(candidates=cands)], ctx, [])

    msg = str(excinfo.value)
    assert "20" in msg, msg  # the survivor count
    assert "30" in msg, msg  # MIN_POIS
    assert "osm_lodging=2" in msg, msg  # the per-rule drop counts are reported


def test_hotel_class_reentry_requires_high_sitelinks() -> None:
    """A hotel-class candidate is dropped in STAGE A UNLESS it is itself very
    notable (``sitelinks >= 15``) — a landmark hotel re-enters; a roadside motel
    does not.
    UNDO: drop the ``sitelinks >= _HOTEL_REENTRY_SITELINKS`` escape -> the
    landmark hotel is culled with the rest -> RED."""
    ctx = _ctx()
    cands = [_wiki(f"Spot {i}", i) for i in range(33)]
    cands.append(
        _osm(
            "Grand Landmark Hotel",
            100,
            tourism="hotel",
            osm_wikipedia="en:Grand Landmark Hotel",
            sitelinks=20,
        )
    )
    cands.append(_osm("Roadside Motel", 101, tourism="motel"))

    city = assemble([ConnectorResult(candidates=cands)], ctx, [])
    names = {p["name"] for p in city.pois}

    assert "Grand Landmark Hotel" in names, "notable hotel must re-enter STAGE A"
    assert "Roadside Motel" not in names, "plain lodging must stay culled"


def test_filtered_tiers_still_satisfy_shape_guard() -> None:
    """After the firehose is filtered, the re-tiered survivors still pass
    test_gravity_distribution's shape rule (T5 anchors, no T3 clustering,
    seasoning-heavy base).
    UNDO: rank _assign_tiers by a constant instead of the notability score, or
    break the quantile boundaries -> unhealthy shape -> RED."""
    ctx = _ctx()
    city = assemble(_firehose(ctx), ctx, [], target_pois=40)

    counts = Counter(p["importance_tier"] for p in city.pois)
    failures = _distribution_failures(counts, len(city.pois))
    assert not failures, "unhealthy tier shape:\n" + "\n".join(failures)


def test_notability_score_and_sitelinks_stamped_in_audit() -> None:
    """Every survivor carries the composite ``notability_score`` and its
    ``sitelinks`` in the gravity_audit (so the re-tier ranks by it and the audit
    is inspectable).
    UNDO: stop stamping ``notability_score`` -> _assign_tiers KeyErrors / the
    audit lacks the field -> RED."""
    ctx = _ctx()
    city = assemble(_base_results(ctx), ctx, _extracts())
    for p in city.pois:
        audit = p["_pipeline"]["gravity_audit"]
        assert "notability_score" in audit, p["name"]
        assert isinstance(audit["notability_score"], (int, float)), p["name"]
        assert "sitelinks" in audit, p["name"]
