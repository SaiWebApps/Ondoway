"""Data-driven city registry — the single writable surface for city registration.

Historically a city was registered by editing hardcoded Python literals
(``CITY_BBOX`` in ``scripts/upload_paris.py``, ``SUPPORTED_CITIES`` in
``src/tour/contract.py``) plus dropping a ``data/{slug}/`` corpus. The new-city
onboarding panel registers a city at RUNTIME, so those literals now derive from
this module, which is backed by the writable ``src/cities.json``.

Why ``src/cities.json`` (not ``data/``): the production Dockerfile ships ``src/``
and ``frontend/`` only — never ``data/`` — so the registry file must live under
``src/`` to be importable in prod.

Prod cloud-filter: a city can be onboarded + uploaded LOCALLY (visible to the
local workbench) before it is deployed to Aura. Such a city must NOT be servable
by the public prod ``/trips`` API. ``servable_cities()`` enforces this — it
returns every registered slug in local/workbench mode, but only ``cloud_deployed``
slugs when the workbench is disabled (the prod signal
``WORKBENCH_API_ENABLED=false``). ``supported_cities()`` stays "all registered"
so the onboarding data-integrity guard and the local workbench keep seeing every
registered city.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

# A registry slug is the canonical city key: it also names the ``data/{slug}/``
# corpus dir and is what ``_validate_city_slug`` normalizes a request to, so a key
# with stray case/whitespace/punctuation would be un-servable and silently wrong.
_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Registry file lives next to this module (ships in the prod image under src/).
_REGISTRY_PATH = Path(__file__).resolve().parent / "cities.json"

# Repo root: src/city_registry.py -> src -> repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent


def onboard_data_root() -> Path:
    """The hermetic-aware corpus root: ``$ONBOARD_DATA_ROOT`` when set (non-empty),
    else the committed ``<repo>/data``.

    ONE definition shared by the onboard WRITE path (``assemble.write_city``) and
    the deploy READ chain (``scripts.upload_paris`` / ``upload_areas`` /
    ``db_parity``) so a hermetic write and the deploy that consumes it resolve the
    SAME root. BACKWARD-COMPATIBLE: when the env is unset the result is byte-
    identical to the old hardcoded ``REPO_ROOT/'data'`` — real paris/new_york
    deploys are unaffected. Mirrors ``write_city``'s prior inline resolution."""
    env = os.environ.get("ONBOARD_DATA_ROOT")
    return Path(env) if env else _REPO_ROOT / "data"


def _effective_registry_path() -> Path:
    """The registry file every read/write in this module resolves to.

    ``$ONBOARD_REGISTRY_PATH`` when set to an EXISTING file, else the module-global
    ``_REGISTRY_PATH`` (the committed ``src/cities.json``, or whatever
    ``assemble._register`` has swapped in under try/finally).

    The existence guard preserves two invariants:
      * env UNSET → byte-identical to today: the module global (real
        ``src/cities.json``), so real paris/new_york reads/writes are unchanged and
        ``_register``'s global-swap keeps working exactly as before.
      * env SET but the hermetic registry not yet materialized (the onboard API
        calls ``supported_cities()`` before ``write_city`` seeds the tmp file) →
        the committed registry, never a ``FileNotFoundError``.
    Once ``write_city`` has materialized the hermetic file — and in the DEPLOY
    subprocess, where it already exists — the env path WINS (the whole point: the
    deploy read chain sees the hermetically-onboarded city).

    CALLER INVARIANT (do not break): a caller that sets ``$ONBOARD_REGISTRY_PATH``
    MUST also pass ``registry_path=Path($ONBOARD_REGISTRY_PATH)`` to ``write_city``
    so ``assemble._register`` SEEDS/materializes that file before the write. If the
    env is set but the file is never materialized and ``register_city`` is called
    directly (no ``registry_path``), this resolver falls back to the committed
    ``src/cities.json`` and the onboarded city would land in the real registry (the
    Step-5 clobber). Both current callers (routes/onboard.py, cli.py) honor this."""
    env = os.environ.get("ONBOARD_REGISTRY_PATH")
    if env:
        path = Path(env)
        if path.exists():
            return path
    return _REGISTRY_PATH


@lru_cache(maxsize=1)
def load_registry() -> dict[str, dict]:
    """Read + parse the effective ``cities.json`` (cached).

    The effective path is ``$ONBOARD_REGISTRY_PATH`` (when set to an existing file)
    else the module-global ``_REGISTRY_PATH`` — see ``_effective_registry_path``.
    The cache is invalidated by ``register_city`` / ``mark_cloud_deployed`` via
    ``load_registry.cache_clear()``; anything that writes the file directly, or
    changes ``$ONBOARD_REGISTRY_PATH`` / the global mid-process, must clear it too.
    """
    with open(_effective_registry_path()) as f:
        return json.load(f)


def bbox_map() -> dict[str, tuple[float, float, float, float]]:
    """``{slug: (min_lat, max_lat, min_lon, max_lon)}`` — replaces the old
    ``CITY_BBOX`` literal. bbox order matches ``scripts/upload_paris.py``."""
    return {slug: tuple(entry["bbox"]) for slug, entry in load_registry().items()}


#: A country is the upper-case two-letter code the opening-hours library keys
#: its public-holiday calendars on (``FR``, ``US``, ``GB``).
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")


def country_code(slug: str) -> str:
    """The two-letter country a city's opening hours are read in.

    ``PH`` in a place's hours means the public holidays of the city's own
    country, so the planner reads hours WITH this code (Docs/adr/0006). There
    is no default: a registry entry without a country is refused in plain
    words, because a country guessed wrong reads every holiday as an open day.
    An unknown slug raises ``KeyError``.
    """
    entry = load_registry()[slug]
    country = entry.get("country")
    if not isinstance(country, str) or not _COUNTRY_RE.match(country):
        raise ValueError(
            f"city {slug!r} has no country in the registry; add a two-letter "
            "'country' to its src/cities.json entry before planning a dated day there"
        )
    return country


def supported_cities() -> frozenset[str]:
    """ALL registered slugs. Backs ``SUPPORTED_CITIES`` + the onboarding
    data-integrity guard; the local workbench serves every registered city."""
    return frozenset(load_registry())


def _prod_cloud_filter_active() -> bool:
    """True when the public prod cloud-filter should apply — i.e. the workbench is
    DISABLED. Mirrors ``src/api/app.py:_workbench_api_enabled`` falsey parsing
    exactly, so ``false``/``0``/``no``/``off`` (any case) all count as prod.

    INTENTIONAL ASYMMETRY (do NOT "unify" with app.py): this controls the
    ``servable_cities()`` /trips filter, NOT router mounting. app.py flipped to
    fail-CLOSED (unset ⇒ routers OFF); this stays fail-OPEN (unset ⇒ cloud-filter
    OFF ⇒ local/all-servable). On an UNSET value they diverge, but harmlessly:
    prod pins render.yaml ``"false"`` (both agree ⇒ routers off + cloud-filter on)
    and the tests set it ``"true"`` (both agree ⇒ routers on + cloud-filter off).
    Flipping this to fail-closed would instead make an unset local dev env serve
    ONLY cloud-deployed cities — the opposite of the local-workbench intent."""
    return os.getenv("WORKBENCH_API_ENABLED", "true").strip().lower() in (
        "false",
        "0",
        "no",
        "off",
    )


def servable_cities() -> frozenset[str]:
    """Slugs the API may serve for a /trips request.

    ALL registered slugs normally; ONLY ``cloud_deployed`` slugs when the
    workbench is disabled (prod), so a locally-onboarded but not-yet-deployed city
    is never served by the public prod /trips API.
    """
    registry = load_registry()
    if _prod_cloud_filter_active():
        # `is True` (not truthiness): a stringly-typed ``"cloud_deployed": "false"``
        # is truthy in Python and would otherwise leak a local-only city into prod.
        # ``_validate_entry`` also rejects non-bool values at write time (belt +
        # suspenders — a hand-edited cities.json can still carry a bad value).
        return frozenset(
            slug for slug, entry in registry.items() if entry.get("cloud_deployed") is True
        )
    return frozenset(registry)


def _validate_entry(entry: dict) -> None:
    """A registry entry must carry a display_name, a numeric 4-element bbox, and a
    BOOLEAN cloud_deployed flag — refuse a half-formed entry before it is written
    (a silently-missing or wrong-typed field would surface far downstream).

    The ``cloud_deployed`` type-check is a safety property, not a nicety: an
    untrusted caller (the Step-5 onboarding API / CLI) that hands a stringly-typed
    ``"false"`` through JSON/form deserialization would, absent this check, write a
    truthy value that ``servable_cities()`` serves in prod — leaking a local-only
    city into the public API, the exact failure this registry exists to prevent."""
    if "display_name" not in entry:
        raise ValueError("city entry is missing 'display_name'")
    bbox = entry.get("bbox")
    if not isinstance(bbox, list | tuple) or len(bbox) != 4:
        raise ValueError(
            "city entry 'bbox' must be a 4-element [min_lat, max_lat, min_lon, max_lon]"
        )
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in bbox):
        raise ValueError("city entry 'bbox' values must all be numbers")
    if "cloud_deployed" not in entry:
        raise ValueError("city entry is missing 'cloud_deployed'")
    if not isinstance(entry["cloud_deployed"], bool):
        raise ValueError(
            "city entry 'cloud_deployed' must be a JSON boolean (true/false), "
            f"got {entry['cloud_deployed']!r}"
        )
    # The country is optional at registration (onboarding does not yet know
    # it) but never malformed: ``country_code`` refuses an entry without one
    # the moment a dated day is planned there.
    if "country" in entry and not (
        isinstance(entry["country"], str) and _COUNTRY_RE.match(entry["country"])
    ):
        raise ValueError(
            "city entry 'country' must be an upper-case two-letter code such as 'FR', "
            f"got {entry['country']!r}"
        )


def _atomic_write(registry: dict[str, dict]) -> None:
    """Write the effective ``cities.json`` atomically (temp file in the same dir +
    ``os.replace``), keys sorted for a stable diff, then invalidate the loader
    cache. Writes to ``_effective_registry_path()`` — the hermetic file when
    ``$ONBOARD_REGISTRY_PATH`` points at one (``_register`` seeds it before the
    first write, so it exists), else the module global (unchanged default)."""
    path = _effective_registry_path()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)
    load_registry.cache_clear()


def _normalize_slug(slug: str) -> str:
    """Canonicalize + validate a slug (lowercased, trimmed). Raises on anything
    that isn't ``[a-z][a-z0-9_]*`` so a mis-typed key can never be silently
    un-servable (``_validate_city_slug`` normalizes the same way)."""
    norm = (slug or "").strip().lower()
    if not _SLUG_RE.match(norm):
        raise ValueError(
            f"invalid city slug {slug!r}: must match {_SLUG_RE.pattern} "
            "(lowercase letters/digits/underscore, letter-initial)"
        )
    return norm


def register_city(slug: str, entry: dict) -> str:
    """Atomically add/update a city in ``cities.json`` and clear the cache.

    ``slug`` is normalized to the canonical ``[a-z][a-z0-9_]*`` form (the returned
    value); ``entry`` must have ``display_name``, a numeric 4-element ``bbox``, and
    a boolean ``cloud_deployed`` (a new city typically registers with
    ``cloud_deployed: false`` until it is deployed to Aura).
    """
    norm = _normalize_slug(slug)
    _validate_entry(entry)
    registry = dict(load_registry())
    registry[norm] = dict(entry)
    _atomic_write(registry)
    return norm


def mark_cloud_deployed(slug: str) -> None:
    """Set ``cloud_deployed=true`` for an existing slug (atomic write + cache clear)."""
    registry = dict(load_registry())
    if slug not in registry:
        raise KeyError(f"cannot mark unknown city {slug!r} cloud-deployed; register it first")
    entry = dict(registry[slug])
    entry["cloud_deployed"] = True
    registry[slug] = entry
    _atomic_write(registry)
