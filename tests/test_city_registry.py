"""Behavior + regression tests for the data-driven city registry.

The registry (``src/city_registry.py`` + ``src/cities.json``) replaced three
hardcoded literals. These tests:
  - pin paris/new_york bbox VALUES byte-equal to the old literals (the refactor's
    regression anchor);
  - exercise the prod cloud-filter (``servable_cities`` in local vs prod mode);
  - round-trip ``register_city`` + ``mark_cloud_deployed`` against a TMP copy of
    cities.json so the committed ``src/cities.json`` is never mutated.

Pure (no Neo4j) → run with ``make test-file FILE=tests/test_city_registry.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import city_registry


@pytest.fixture(autouse=True)
def _clean_registry_cache():
    """Keep the lru_cache clean around every test so a test that monkeypatches the
    loader or the registry path can never leak cached state into another."""
    clear = getattr(city_registry.load_registry, "cache_clear", None)
    if clear:
        clear()
    yield
    clear = getattr(city_registry.load_registry, "cache_clear", None)
    if clear:
        clear()


def test_bbox_map_byte_equal_to_old_literals() -> None:
    """The refactor MUST preserve the exact bbox tuples the CITY_BBOX literal held."""
    bm = city_registry.bbox_map()
    assert bm["paris"] == (48.70, 49.00, 2.10, 2.60)
    assert bm["new_york"] == (40.45, 40.93, -74.28, -73.68)


def test_supported_cities_today() -> None:
    """The launch cities (paris, new_york) remain registered. Asserted as a SUBSET,
    not exact equality: the registry now GROWS as cities are onboarded (london, etc.),
    so an exact ``frozenset({'paris', 'new_york'})`` pin would RED the moment a new
    city is committed. The invariant that matters is that the refactor never DROPPED
    a launch city — not that the set is frozen at two."""
    assert {"paris", "new_york"} <= city_registry.supported_cities()


def _fake_registry() -> dict[str, dict]:
    return {
        "paris": {
            "display_name": "Paris",
            "bbox": [48.70, 49.00, 2.10, 2.60],
            "cloud_deployed": True,
            "country": "FR",
        },
        "testburg": {
            "display_name": "Testburg",
            "bbox": [0.0, 1.0, 0.0, 1.0],
            "cloud_deployed": False,
        },
    }


def test_every_launch_city_carries_its_country_and_the_accessor_refuses_a_city_without_one(
    monkeypatch,
) -> None:
    """Opening hours are read in the city's own country (Docs/adr/0006): ``PH``
    in a place's hours means THAT country's public holidays. The committed
    registry carries a two-letter country for every city, the accessor hands it
    back, and a city with none is refused in plain words — never defaulted,
    because a default country reads every holiday as an open day."""
    assert city_registry.country_code("paris") == "FR"
    assert city_registry.country_code("new_york") == "US"
    assert city_registry.country_code("london") == "GB"

    monkeypatch.setattr(city_registry, "load_registry", _fake_registry)
    assert city_registry.country_code("paris") == "FR"
    with pytest.raises(ValueError, match=r"testburg.*country"):
        city_registry.country_code("testburg")
    with pytest.raises(KeyError):
        city_registry.country_code("nowhere")


def test_validate_entry_refuses_a_malformed_country() -> None:
    """A country, when given, is an upper-case two-letter code — the shape the
    hours library keys its holiday calendars on."""
    good = {"display_name": "X", "bbox": [0.0, 1.0, 0.0, 1.0], "cloud_deployed": False}
    city_registry._validate_entry({**good, "country": "FR"})
    city_registry._validate_entry(good)  # a city not yet placed in a country
    for bad in ("fr", "FRA", "", 33, None):
        with pytest.raises(ValueError, match="country"):
            city_registry._validate_entry({**good, "country": bad})


def test_servable_cities_local_includes_non_cloud_deployed(monkeypatch) -> None:
    """LOCAL mode (WORKBENCH_API_ENABLED unset/true): every registered city is
    servable, including a not-yet-cloud-deployed one."""
    monkeypatch.setattr(city_registry, "load_registry", _fake_registry)
    monkeypatch.delenv("WORKBENCH_API_ENABLED", raising=False)
    assert city_registry.servable_cities() == frozenset({"paris", "testburg"})
    # Explicit truthy values stay local too.
    for local_value in ("true", "1", "on", "yes", "anything-else"):
        monkeypatch.setenv("WORKBENCH_API_ENABLED", local_value)
        assert city_registry.servable_cities() == frozenset({"paris", "testburg"}), local_value


def test_servable_cities_prod_excludes_non_cloud_deployed(monkeypatch) -> None:
    """PROD mode (WORKBENCH_API_ENABLED falsey): a non-cloud_deployed city is
    excluded from what the public /trips API may serve — mirrors the same falsey
    parsing as src/api/app.py:_workbench_api_enabled."""
    monkeypatch.setattr(city_registry, "load_registry", _fake_registry)
    for prod_value in ("false", "0", "no", "off", "FALSE", "Off", "  no  "):
        monkeypatch.setenv("WORKBENCH_API_ENABLED", prod_value)
        assert city_registry.servable_cities() == frozenset({"paris"}), prod_value


def test_register_and_mark_round_trip(tmp_path, monkeypatch) -> None:
    """register_city + mark_cloud_deployed round-trip against a TMP cities.json,
    proving atomic write, sorted-key stable diff, and lru_cache invalidation —
    without ever touching the committed src/cities.json."""
    real = Path(city_registry.__file__).resolve().parent / "cities.json"
    tmp_registry = tmp_path / "cities.json"
    tmp_registry.write_text(real.read_text())
    monkeypatch.setattr(city_registry, "_REGISTRY_PATH", tmp_registry)
    city_registry.load_registry.cache_clear()

    entry = {"display_name": "London", "bbox": [51.28, 51.70, -0.52, 0.33], "cloud_deployed": False}
    city_registry.register_city("london", entry)

    # Cache was invalidated: a fresh load sees london.
    reg = city_registry.load_registry()
    assert reg["london"] == entry
    assert city_registry.bbox_map()["london"] == (51.28, 51.70, -0.52, 0.33)

    # The file on disk actually changed, and top-level keys are sorted (stable diff).
    on_disk = json.loads(tmp_registry.read_text())
    assert "london" in on_disk
    assert list(on_disk.keys()) == sorted(on_disk.keys())

    # london starts local-only → excluded from prod servable set.
    monkeypatch.setenv("WORKBENCH_API_ENABLED", "false")
    assert "london" not in city_registry.servable_cities()
    assert "london" in city_registry.supported_cities()

    # mark_cloud_deployed flips the flag AND invalidates the cache.
    city_registry.mark_cloud_deployed("london")
    assert city_registry.load_registry()["london"]["cloud_deployed"] is True
    assert "london" in city_registry.servable_cities()


def test_register_city_validates_entry(tmp_path, monkeypatch) -> None:
    """A malformed entry (missing/short bbox, missing required keys) is refused
    before it can be written."""
    real = Path(city_registry.__file__).resolve().parent / "cities.json"
    tmp_registry = tmp_path / "cities.json"
    tmp_registry.write_text(real.read_text())
    monkeypatch.setattr(city_registry, "_REGISTRY_PATH", tmp_registry)
    city_registry.load_registry.cache_clear()

    with pytest.raises(ValueError):  # no bbox
        city_registry.register_city("bad", {"display_name": "Bad", "cloud_deployed": True})
    with pytest.raises(ValueError):  # bbox wrong length
        city_registry.register_city(
            "bad", {"display_name": "Bad", "bbox": [1, 2, 3], "cloud_deployed": True}
        )
    with pytest.raises(ValueError):  # missing cloud_deployed
        city_registry.register_city("bad", {"display_name": "Bad", "bbox": [1, 2, 3, 4]})

    # None of the rejected writes reached disk.
    assert "bad" not in json.loads(tmp_registry.read_text())


def test_stringly_typed_cloud_deployed_is_rejected_and_never_served(monkeypatch) -> None:
    """The prod cloud-filter's core safety property: a truthy NON-bool
    ``cloud_deployed`` (e.g. the JSON/form string ``"false"``) must NOT leak a
    local-only city into prod. Two independent walls:
      1. ``_validate_entry`` REFUSES a non-bool value at write time.
      2. ``servable_cities()`` uses ``is True`` (not truthiness), so even a
         hand-edited cities.json carrying ``"false"`` is excluded in prod."""
    # Wall 1 — write-time rejection.
    with pytest.raises(ValueError, match="cloud_deployed"):
        city_registry._validate_entry(
            {"display_name": "Bad", "bbox": [51.0, 52.0, -1.0, 1.0], "cloud_deployed": "false"}
        )
    with pytest.raises(ValueError, match="cloud_deployed"):
        city_registry._validate_entry(
            {"display_name": "Bad", "bbox": [51.0, 52.0, -1.0, 1.0], "cloud_deployed": 1}
        )
    # Wall 2 — serve-time defense against a bad value that bypassed wall 1.
    bad = {
        "paris": {"display_name": "Paris", "bbox": [48.7, 49.0, 2.1, 2.6], "cloud_deployed": True},
        "sneaky": {
            "display_name": "Sneaky",
            "bbox": [0.0, 1.0, 0.0, 1.0],
            "cloud_deployed": "false",
        },
    }
    monkeypatch.setattr(city_registry, "load_registry", lambda: bad)
    monkeypatch.setenv("WORKBENCH_API_ENABLED", "false")  # prod
    assert city_registry.servable_cities() == frozenset({"paris"})


def test_validate_entry_rejects_non_numeric_bbox() -> None:
    with pytest.raises(ValueError, match="bbox"):
        city_registry._validate_entry(
            {"display_name": "Bad", "bbox": ["a", "b", "c", "d"], "cloud_deployed": True}
        )
    # A bool is not a valid coordinate (isinstance(True, int) is True in Python).
    with pytest.raises(ValueError, match="bbox"):
        city_registry._validate_entry(
            {"display_name": "Bad", "bbox": [True, 1.0, 2.0, 3.0], "cloud_deployed": True}
        )


def test_register_city_normalizes_and_validates_slug(tmp_path, monkeypatch) -> None:
    """A slug is canonicalized (trim + lowercase) so a stray-cased key can't become
    silently un-servable; a slug with illegal chars is refused outright."""
    real = Path(city_registry.__file__).resolve().parent / "cities.json"
    tmp_registry = tmp_path / "cities.json"
    tmp_registry.write_text(real.read_text())
    monkeypatch.setattr(city_registry, "_REGISTRY_PATH", tmp_registry)
    city_registry.load_registry.cache_clear()

    entry = {"display_name": "London", "bbox": [51.28, 51.70, -0.52, 0.33], "cloud_deployed": False}
    returned = city_registry.register_city("  London ", entry)
    assert returned == "london"
    assert "london" in city_registry.load_registry()
    assert "  London " not in city_registry.load_registry()

    for bad_slug in ("New York", "sao-paulo", "123city", "", "Città"):
        with pytest.raises(ValueError, match="invalid city slug"):
            city_registry.register_city(bad_slug, entry)


def test_mark_unknown_city_raises(tmp_path, monkeypatch) -> None:
    real = Path(city_registry.__file__).resolve().parent / "cities.json"
    tmp_registry = tmp_path / "cities.json"
    tmp_registry.write_text(real.read_text())
    monkeypatch.setattr(city_registry, "_REGISTRY_PATH", tmp_registry)
    city_registry.load_registry.cache_clear()

    with pytest.raises(KeyError):
        city_registry.mark_cloud_deployed("nowhere")


# ---------------------------------------------------------------------------
# HERMETIC ENV: onboard_data_root() + ONBOARD_REGISTRY_PATH honoring. All
# env-gated with the default (unset) behavior preserved byte-for-byte, so real
# paris/new_york reads are unchanged.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(city_registry.__file__).resolve().parent.parent


def test_onboard_data_root_defaults_to_repo_data_when_unset(monkeypatch) -> None:
    """Unset (or empty) ONBOARD_DATA_ROOT → <repo>/data — the old hardcoded default.
    [undo: hardcode env-only → RED on the unset/empty cases]"""
    monkeypatch.delenv("ONBOARD_DATA_ROOT", raising=False)
    assert city_registry.onboard_data_root() == _REPO_ROOT / "data"
    monkeypatch.setenv("ONBOARD_DATA_ROOT", "")  # empty string is treated as unset
    assert city_registry.onboard_data_root() == _REPO_ROOT / "data"


def test_onboard_data_root_honors_env_when_set(monkeypatch, tmp_path) -> None:
    """ONBOARD_DATA_ROOT set (non-empty) → that path.
    [undo: ignore the env / hardcode <repo>/data → RED when set]"""
    monkeypatch.setenv("ONBOARD_DATA_ROOT", str(tmp_path))
    assert city_registry.onboard_data_root() == tmp_path


def test_registry_path_honors_existing_env_file(tmp_path, monkeypatch) -> None:
    """ONBOARD_REGISTRY_PATH → an EXISTING tmp registry wins: supported_cities()
    and bbox_map() read IT (the injected marker visible); unset → the committed
    src/cities.json (paris, new_york; NOT the injected marker).
    [undo: make _effective_registry_path ignore the env → the marker never appears → RED]

    The marker is a NEVER-committed slug ('gotham'), not a real onboarded city, so the
    "unset does not see the injected city" property holds regardless of which real
    cities (london, …) already live in the committed registry."""
    real = Path(city_registry.__file__).resolve().parent / "cities.json"
    reg = dict(json.loads(real.read_text()))
    reg["gotham"] = {
        "display_name": "Gotham",
        "bbox": [51.28, 51.7, -0.51, 0.33],
        "cloud_deployed": False,
    }
    hermetic = tmp_path / "cities.json"
    hermetic.write_text(json.dumps(reg, indent=2, sort_keys=True) + "\n")

    # UNSET → the real registry (paris, new_york; NOT the injected marker).
    monkeypatch.delenv("ONBOARD_REGISTRY_PATH", raising=False)
    city_registry.load_registry.cache_clear()
    assert {"paris", "new_york"} <= city_registry.supported_cities()
    assert "gotham" not in city_registry.supported_cities()
    assert "gotham" not in city_registry.bbox_map()

    # SET to the existing hermetic file → the marker is visible in BOTH accessors.
    monkeypatch.setenv("ONBOARD_REGISTRY_PATH", str(hermetic))
    city_registry.load_registry.cache_clear()
    assert "gotham" in city_registry.supported_cities()
    assert city_registry.bbox_map()["gotham"] == (51.28, 51.7, -0.51, 0.33)
    assert {"paris", "new_york", "gotham"} <= set(city_registry.bbox_map())


def test_registry_env_missing_file_falls_back_to_global(tmp_path, monkeypatch) -> None:
    """ONBOARD_REGISTRY_PATH set but the file not yet MATERIALIZED → the read chain
    falls back to the committed registry (never FileNotFoundError). This keeps the
    onboard API's pre-write ``supported_cities()`` call (before ``write_city`` seeds
    the tmp file) safe.
    [undo: drop the .exists() guard → load_registry opens a missing file → RED]"""
    monkeypatch.setenv("ONBOARD_REGISTRY_PATH", str(tmp_path / "not-yet-written.json"))
    city_registry.load_registry.cache_clear()
    assert {"paris", "new_york"} <= city_registry.supported_cities()
    # A never-committed marker stays absent: fallback yields exactly the committed
    # registry, conjuring no phantom city. (Was 'london'; that inverts once london is
    # a real committed city.)
    assert "gotham" not in city_registry.supported_cities()


def test_registry_env_unset_preserves_global_swap(tmp_path, monkeypatch) -> None:
    """With ONBOARD_REGISTRY_PATH UNSET, the module-global swap (assemble._register's
    mechanism) still drives the effective path exactly as before — a monkeypatched
    _REGISTRY_PATH is what load_registry reads.
    [undo: n/a — this guards that env-honoring did not break the swap contract]"""
    monkeypatch.delenv("ONBOARD_REGISTRY_PATH", raising=False)
    swapped = tmp_path / "swapped.json"
    swapped.write_text(
        json.dumps(
            {"gotham": {"display_name": "Gotham", "bbox": [0.0, 1.0, 0.0, 1.0],
                        "cloud_deployed": False}},
            indent=2, sort_keys=True,
        )
        + "\n"
    )
    monkeypatch.setattr(city_registry, "_REGISTRY_PATH", swapped)
    city_registry.load_registry.cache_clear()
    assert city_registry.supported_cities() == frozenset({"gotham"})
