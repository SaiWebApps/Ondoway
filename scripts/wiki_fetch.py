#!/usr/bin/env python3
"""Deterministically fetch + pin a Wikipedia article's raw plain text.

Single approved entrypoint (via `make wiki-fetch`) for the network + file-write
plumbing of `/beat-from-wikipedia`. It pulls the CURRENT revision's raw plain
text straight from the MediaWiki API, pins the revision id, and saves the text
to `data/{city}/wikipedia/{poi_slug}-rev-{revid}.txt`.

The model MUST extract beats only from the saved file this prints — never from
a WebFetch summary or memory. (WebFetch returns LLM-summarised page content,
so beats drawn from it drift from their pinned source.)

Prints a JSON summary to stdout. It reports facts (disambiguation, prior
revisions, whether this exact revision was already logged) but does not make
the HARD REFUSE / update-pass decision — that judgment stays with the model.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.beat_builder import slugify
from scripts.wipe_beats import slugify_title

API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "Ondoway-pipeline/1.0 (serblowa@gmail.com)"


def fetch_article(title: str) -> dict:
    params = {
        "action": "query",
        "format": "json",
        "redirects": "1",
        "titles": title,
        "prop": "revisions|pageprops|extracts",
        "rvprop": "ids|timestamp",
        "explaintext": "1",
        "exsectionformat": "plain",
    }
    url = f"{API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def scan_log(log_path: Path, poi_slug: str, revid: str) -> dict:
    """Scan EVERY 'Wikipedia' book entry's chunks (a known duplicate-entry
    failure mode means we cannot trust just the first one)."""
    result = {"already_processed": False, "prior_revisions": [], "wikipedia_entries": 0}
    if not log_path.exists():
        return result
    log = json.loads(log_path.read_text())
    target_chunk = f"{poi_slug}-rev-{revid}"
    for book in log.get("books_processed", []):
        if slugify_title(book.get("book_title", "")) != "wikipedia":
            continue
        result["wikipedia_entries"] += 1
        for entry in book.get("chunks_processed", []):
            chunk = entry.get("chunk") if isinstance(entry, dict) else entry
            if chunk == target_chunk:
                result["already_processed"] = True
            elif isinstance(chunk, str) and chunk.startswith(f"{poi_slug}-rev-"):
                result["prior_revisions"].append(chunk)
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="wiki_fetch.py")
    parser.add_argument("--city", required=True, help="city slug, e.g. paris")
    parser.add_argument("--name", required=True, help="canonical POI name (used for the file slug)")
    parser.add_argument("--title", help="Wikipedia article title (defaults to --name)")
    args = parser.parse_args(argv)

    # Sanitize city before it touches the filesystem path — never trust it raw
    # (a value like "../../x" would otherwise write outside the data tree).
    city = args.city.lower()
    if not re.fullmatch(r"[a-z0-9_-]+", city):
        print(json.dumps(
            {"status": "invalid_city", "requested_city": args.city,
             "note": "city must be a slug: lowercase letters, digits, hyphen, underscore."},
            indent=2,
        ))
        return 0

    poi_slug = slugify(args.name)
    title = args.title or args.name
    data = fetch_article(title)

    pages = data.get("query", {}).get("pages", {})
    page = next(iter(pages.values()), {})
    if "missing" in page or not page.get("revisions"):
        print(json.dumps({"status": "not_found", "requested_title": title}, indent=2))
        return 0
    if "disambiguation" in page.get("pageprops", {}):
        print(json.dumps(
            {"status": "disambiguation", "resolved_title": page.get("title"),
             "note": "Pass an explicit --title; never guess past a disambiguation page."},
            indent=2,
        ))
        return 0

    resolved_title = page["title"]
    rev = page["revisions"][0]
    revid = str(rev["revid"])
    extract = page.get("extract", "")

    wiki_dir = Path(f"data/{city}/wikipedia")
    wiki_dir.mkdir(parents=True, exist_ok=True)
    saved_path = wiki_dir / f"{poi_slug}-rev-{revid}.txt"
    saved_path.write_text(extract)

    log_scan = scan_log(Path(f"data/{city}/book-log.json"), poi_slug, revid)

    url_title = urllib.parse.quote(resolved_title.replace(" ", "_"))
    summary = {
        "status": "ok",
        "resolved_title": resolved_title,
        "redirected_from": [r["from"] for r in data.get("query", {}).get("redirects", [])],
        "poi_slug": poi_slug,
        "revid": revid,
        "source_chunk_slug": f"{poi_slug}-rev-{revid}",
        "rev_timestamp": rev["timestamp"],
        "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "oldid_url": f"https://en.wikipedia.org/wiki/{url_title}?oldid={revid}",
        "saved_path": str(saved_path),
        "char_count": len(extract),
        **log_scan,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
