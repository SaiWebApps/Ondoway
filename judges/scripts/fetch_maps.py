#!/usr/bin/env python3
"""Fetch the street map of every city in the judges' city list and prove each one.

A map is proved when its MD5 checksum equals both the checksum its publisher
serves beside it and the checksum pinned in the city list. A proved map on disk
is never downloaded again. Any mismatch is an error and the exit code is 1.

Run it from anywhere: python3 judges/scripts/fetch_maps.py
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

JUDGES_DIR = Path(__file__).resolve().parent.parent
CITIES_FILE = JUDGES_DIR / "cities.json"
MAPS_DIR = JUDGES_DIR / "data" / "maps"

# The publisher serves each map's checksum at the map's address plus this suffix.
PUBLISHED_MD5_SUFFIX = ".md5"
# A download in progress carries this suffix, so a half-written map never
# passes for a whole one.
PARTIAL_SUFFIX = ".partial"
# Every request names a generic client and carries no personal detail.
USER_AGENT = "map-fetch/1.0"
TIMEOUT_SECONDS = 60
CHUNK_BYTES = 1 << 20

MD5_PATTERN = re.compile(r"[0-9a-f]{32}")
# A map file name is a bare name, so every map lands inside the maps folder.
FILE_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class MapError(Exception):
    """A map or its checksum is not what the city list pins."""


@dataclass(frozen=True)
class City:
    key: str
    map_url: str
    map_file: str
    map_md5: str


def main() -> int:
    MAPS_DIR.mkdir(parents=True, exist_ok=True)
    failed = False
    for city in load_cities():
        try:
            print(f"{city.key}: {ensure_map(city)}", flush=True)
        except (MapError, OSError) as error:
            failed = True
            print(f"{city.key}: ERROR: {error}", file=sys.stderr, flush=True)
    return 1 if failed else 0


def load_cities() -> list[City]:
    """Every city in the city list, with its entry checked for shape."""
    entries = json.loads(CITIES_FILE.read_text(encoding="utf-8"))
    cities = []
    for key, entry in entries.items():
        city = City(key, entry["map_url"], entry["map_file"], entry["map_md5"])
        if not FILE_NAME_PATTERN.fullmatch(city.map_file):
            raise SystemExit(f"{CITIES_FILE}: {key}: map_file must be a bare file name")
        if not MD5_PATTERN.fullmatch(city.map_md5):
            raise SystemExit(f"{CITIES_FILE}: {key}: map_md5 must be 32 lowercase hex digits")
        cities.append(city)
    return cities


def ensure_map(city: City) -> str:
    """Put one city's proved map on disk and say in one line what happened."""
    target = MAPS_DIR / city.map_file
    published = published_md5(city)
    if published != city.map_md5:
        raise MapError(
            f"the city list pins MD5 {city.map_md5}, but the publisher lists {published}"
        )
    if target.exists():
        require_md5(target, city.map_md5)
        action = "already on disk"
    else:
        partial = target.with_name(target.name + PARTIAL_SUFFIX)
        print(f"{city.key}: downloading {city.map_url}", flush=True)
        download(city.map_url, partial)
        try:
            require_md5(partial, city.map_md5)
        except MapError:
            # A whole download with the wrong content is useless to continue from.
            partial.unlink()
            raise
        partial.rename(target)
        action = "downloaded"
    return (
        f"{city.map_file} {action}; MD5 {city.map_md5} matches "
        "the publisher's checksum and the city list's checksum"
    )


def published_md5(city: City) -> str:
    """The checksum the publisher serves beside the map.

    The publisher's checksum file is kept beside the map on disk, so proving a
    map that is already on disk needs no network.
    """
    saved = MAPS_DIR / (city.map_file + PUBLISHED_MD5_SUFFIX)
    if not saved.exists():
        with open_url(city.map_url + PUBLISHED_MD5_SUFFIX) as response:
            saved.write_bytes(response.read())
    words = saved.read_text(encoding="utf-8").split()
    if not words or not MD5_PATTERN.fullmatch(words[0]):
        raise MapError(f"{saved.name} does not start with an MD5 checksum")
    return words[0]


def require_md5(path: Path, expected: str) -> None:
    actual = md5_of(path)
    if actual != expected:
        raise MapError(f"{path.name} has MD5 {actual}, expected {expected}")


def md5_of(path: Path) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, partial: Path) -> None:
    """Download url into partial, continuing from the bytes already there."""
    have = partial.stat().st_size if partial.exists() else 0
    try:
        response = open_url(url, first_byte=have)
    except HTTPError as error:
        # The server answers 416 when the bytes on disk already reach the end
        # of the file. The checksum then decides whether they are the map.
        if have and error.code == 416:
            return
        raise
    with response:
        continued = response.status == 206
        expected = (have if continued else 0) + int(response.headers["Content-Length"])
        with open(partial, "ab" if continued else "wb") as out:
            shutil.copyfileobj(response, out, CHUNK_BYTES)
    size = partial.stat().st_size
    if size != expected:
        raise MapError(
            f"the download stopped at {size} of {expected} bytes; run again to continue it"
        )


def open_url(url: str, first_byte: int = 0):
    headers = {"User-Agent": USER_AGENT}
    if first_byte:
        headers["Range"] = f"bytes={first_byte}-"
    return urlopen(Request(url, headers=headers), timeout=TIMEOUT_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
