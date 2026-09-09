"""Static regression tests for the data integrity bugs found in the
2026-04-08 audit. Runs against poi-raw.json and beats.json only — no DB.

Catches:
  - B1: Accent-duplicate POIs (e.g. 'Café de Flore' + 'Cafe de Flore')
  - B6: Ghost beat stubs (missing beat_id or script_body)
  - Beat→POI orphans in source files
  - POI name / name_variations collisions
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = REPO_ROOT / "data"


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn"
    )


def _norm(s: str) -> str:
    return _strip_accents(s).lower().strip()


def _city_dirs() -> list[Path]:
    if not DATA_ROOT.exists():
        return []
    return [d for d in sorted(DATA_ROOT.iterdir()) if d.is_dir() and (d / "poi-raw.json").exists()]


@pytest.mark.parametrize("city_dir", _city_dirs(), ids=lambda d: d.name)
def test_every_data_city_is_fully_registered(city_dir: Path) -> None:
    """Onboarding fail-fast: any city with data/ must ALSO be registered in every
    surface the engine reads, or it silently misbehaves — upload rejects POIs
    outside CITY_BBOX, and the API 422s a city absent from SUPPORTED_CITIES. This
    catches a half-onboarded city at CI time instead of at deploy/request time, so
    the tour algorithm truly generalizes to 'any and all cities' we add."""
    from scripts.upload_paris import CITY_BBOX
    from src.city_registry import load_registry
    from src.tour.contract import SUPPORTED_CITIES

    city = city_dir.name
    problems: list[str] = []
    if city not in CITY_BBOX:
        problems.append("missing from CITY_BBOX (upload_paris.py) — every POI skipped on upload")
    if city not in SUPPORTED_CITIES:
        problems.append("missing from SUPPORTED_CITIES (contract.py) — the API 422s every request")
    if not (city_dir / "beats.json").exists():
        problems.append("has poi-raw.json but no beats.json — POIs would seat with zero narration")

    # Registry entry must exist and carry a valid 4-element bbox that actually
    # CONTAINS the city's POI centroid — a mis-entered bbox (wrong sign, swapped
    # lat/lon, a typo'd degree) would silently drop most POIs on upload, so pin
    # the geofence to the data it must admit.
    registry = load_registry()
    entry = registry.get(city)
    if entry is None:
        problems.append("missing from the city registry (src/cities.json)")
    else:
        bbox = entry.get("bbox")
        if not (isinstance(bbox, list) and len(bbox) == 4):
            problems.append("registry bbox is not a 4-element [min_lat, max_lat, min_lon, max_lon]")
        else:
            pois = json.loads((city_dir / "poi-raw.json").read_text())
            coords = [
                (float(p["latitude"]), float(p["longitude"]))
                for p in pois
                if p.get("latitude") is not None and p.get("longitude") is not None
            ]
            if coords:
                mean_lat = sum(c[0] for c in coords) / len(coords)
                mean_lon = sum(c[1] for c in coords) / len(coords)
                min_lat, max_lat, min_lon, max_lon = bbox
                if not (min_lat <= mean_lat <= max_lat and min_lon <= mean_lon <= max_lon):
                    problems.append(
                        f"registry bbox {bbox} does not contain the POI centroid "
                        f"({mean_lat:.5f}, {mean_lon:.5f}) — likely a mis-entered bbox"
                    )

    assert not problems, f"city '{city}' is not fully onboarded:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("city_dir", _city_dirs(), ids=lambda d: d.name)
def test_no_accent_or_case_duplicate_pois(city_dir: Path) -> None:
    """No two POIs should collide after accent stripping + case folding.
    Would have caught the 'Café de Flore' vs 'Cafe de Flore' duplicate bug."""
    pois = json.loads((city_dir / "poi-raw.json").read_text())
    seen: dict[str, str] = {}
    dupes: list[str] = []
    for p in pois:
        key = _norm(p["name"])
        if key in seen:
            dupes.append(f"'{seen[key]}' ↔ '{p['name']}'")
        else:
            seen[key] = p["name"]
    if dupes:
        pytest.fail(
            f"{city_dir.name}: {len(dupes)} POI pair(s) collide after "
            f"Unicode/case normalization:\n  " + "\n  ".join(dupes)
        )


@pytest.mark.parametrize("city_dir", _city_dirs(), ids=lambda d: d.name)
def test_no_ghost_beat_stubs(city_dir: Path) -> None:
    """Every beat in beats.json must have a beat_id and non-empty script_body.
    Would have caught the 19 metadata-only stubs in the 2026-04-08 audit."""
    beats = json.loads((city_dir / "beats.json").read_text())
    missing_id = [i for i, b in enumerate(beats) if not b.get("beat_id")]
    missing_body = [i for i, b in enumerate(beats) if not (b.get("script_body") or "").strip()]
    problems = []
    if missing_id:
        problems.append(f"{len(missing_id)} beat(s) missing beat_id")
    if missing_body:
        problems.append(f"{len(missing_body)} beat(s) with empty script_body")
    if problems:
        pytest.fail(f"{city_dir.name}: " + "; ".join(problems))


@pytest.mark.parametrize("city_dir", _city_dirs(), ids=lambda d: d.name)
def test_no_tiny_fragment_beat_bodies(city_dir: Path) -> None:
    """Cheap universal floor: no beat body under 4 words.

    Catches the most extreme splitter fragments ('S.', 'W.', 'Giles and Soho.')
    for ALL beats, including book beats that have no wikipedia chunk to check
    against. Real corpus sentences run 6+ words (measured floors: Paris 6, New York
    11), so a sub-4-word body is a truncation artifact, not narration.
    This is a FLOOR only — the precise guard against longer truncations (7-20 words,
    which this misses) is ``test_wikipedia_beat_bodies_are_whole_sentences`` below.
    (If a genuinely terse beat is ever authored, lower the floor and note why.)"""
    beats = json.loads((city_dir / "beats.json").read_text())
    frags = [
        (b.get("beat_id", "?"), body)
        for b in beats
        if 0 < len((body := (b.get("script_body") or "").strip()).split()) < 4
    ]
    if frags:
        pytest.fail(
            f"{city_dir.name}: {len(frags)} beat(s) with a tiny fragment body "
            f"(<4 words — likely a splitter truncation):\n  "
            + "\n  ".join(f"{bid}: {body!r}" for bid, body in frags[:20])
        )


# Bodies the whole-sentence proxy flags that are, on inspection, COMPLETE grounded
# sentences: the CURRENT splitter mis-segments the surrounding CHUNK (not the beat),
# so the body is a prefix/suffix of a mis-joined chunk "sentence". Each verified by
# hand. Keyed by beat_id -> reason. (NOT fragments — do not "fix" the beat.)
_WHOLE_SENTENCE_ALLOWLIST: dict[str, str] = {
    "new_york_plaza_hotel_music_heritage_wikipedia_beatles_first_visit":
        "complete; an intentional edit dropped the discourse marker 'In addition,' "
        "from the chunk sentence",
}


@pytest.mark.parametrize("city_dir", _city_dirs(), ids=lambda d: d.name)
def test_wikipedia_beat_bodies_are_whole_sentences(city_dir: Path) -> None:
    """PRECISE splitter-fragment guard: a wikipedia beat body must be a WHOLE
    sentence (or run of whole sentences) of its source chunk, per the CURRENT
    ``split_sentences`` — never a truncated HEAD ('They depict the Battle of Cape
    St.') or an orphaned TAIL ('Woodington, John Ternouth and…') left by the old
    splitter that broke on name-initials / 'St.'. Word-count alone missed these
    (they are 7-20 words); this catches any body that is a strict prefix/suffix of a
    chunk sentence. The allowlist above holds the handful of complete sentences the
    proxy flags because the current splitter mis-segments the CHUNK, not the beat."""
    from src.tour.generation import split_sentences

    def _n(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip()).casefold()

    beats = json.loads((city_dir / "beats.json").read_text())
    wiki_dir = city_dir / "wikipedia"
    frags: list[str] = []
    for b in beats:
        body = (b.get("script_body") or "").strip()
        if not body or b.get("book_slug") != "wikipedia":
            continue
        if b.get("beat_id") in _WHOLE_SENTENCE_ALLOWLIST:
            continue
        chunk = wiki_dir / f"{b.get('source_chunk_slug')}.txt"
        if not chunk.exists():
            continue
        sents = [_n(s) for s in split_sentences(chunk.read_text())]
        bn = _n(body)
        if bn in sents:
            continue  # whole sentence -> clean
        # a strict, materially-shorter prefix/suffix of a chunk sentence = a fragment
        if any(
            (s.startswith(bn) or s.endswith(bn)) and len(bn) < len(s) - 1 for s in sents
        ):
            frags.append(f"{b.get('beat_id')}: {body[:70]!r}")
    if frags:
        pytest.fail(
            f"{city_dir.name}: {len(frags)} wikipedia beat(s) whose body is a "
            f"splitter FRAGMENT (truncated head or orphaned tail), not a whole "
            f"chunk sentence:\n  " + "\n  ".join(frags[:25])
        )


@pytest.mark.parametrize("city_dir", _city_dirs(), ids=lambda d: d.name)
def test_every_beat_references_known_poi(city_dir: Path) -> None:
    """Every beat.poi_name must match a POI in poi-raw.json (case-insensitive).
    Catches orphan beats in the source files before they reach the DB."""
    pois = json.loads((city_dir / "poi-raw.json").read_text())
    beats = json.loads((city_dir / "beats.json").read_text())
    poi_keys = {_norm(p["name"]) for p in pois}
    # Also include name_variations
    for p in pois:
        for v in p.get("name_variations", []) or []:
            poi_keys.add(_norm(v))
    orphans: list[str] = []
    for b in beats:
        pn = b.get("poi_name")
        if pn and _norm(pn) not in poi_keys:
            orphans.append(f"'{pn}' (beat {b.get('beat_id', '?')})")
    if orphans:
        pytest.fail(
            f"{city_dir.name}: {len(orphans)} beat(s) reference unknown POIs:\n  "
            + "\n  ".join(orphans[:20])
        )


@pytest.mark.parametrize("city_dir", _city_dirs(), ids=lambda d: d.name)
def test_no_poi_name_variation_collisions(city_dir: Path) -> None:
    """A POI's name_variations must not collide with another POI's name or
    variations. Prevents confusion during POI matching in upload/upsert."""
    pois = json.loads((city_dir / "poi-raw.json").read_text())
    reverse_index: dict[str, str] = {}  # norm → canonical poi name
    collisions: list[str] = []
    for p in pois:
        keys = {_norm(p["name"])}
        for v in p.get("name_variations", []) or []:
            keys.add(_norm(v))
        for k in keys:
            if not k:
                continue
            owner = reverse_index.get(k)
            if owner and owner != p["name"]:
                collisions.append(f"'{k}' owned by both '{owner}' and '{p['name']}'")
            reverse_index[k] = p["name"]
    if collisions:
        pytest.fail(
            f"{city_dir.name}: {len(collisions)} name/variation collision(s):\n  "
            + "\n  ".join(collisions[:20])
        )
