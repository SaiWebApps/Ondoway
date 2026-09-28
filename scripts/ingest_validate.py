"""Validate a new-shape beats file with the P7 validator — `make ingest-validate FILE=`. $0.

Read-only: runs `model.validate` over the file with its spans grounded in
`Books/{city}`, and prints the error count, a fingerprint of the sorted error
set and every error. Two runs agree exactly when their fingerprints do, which
is how a change that must leave every existing narration's hash untouched is
proven on the real corpus (ADR-0004).

    make ingest-validate FILE=data-ingest/new_york/beats.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ingest import model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", type=Path, required=True, help="a new-shape beats.json")
    parser.add_argument("--books-root", type=Path, default=ROOT / "Books")
    args = parser.parse_args(argv)

    records = json.loads(args.file.read_text(encoding="utf-8"))
    city = records[0]["city_name"] if records else ""
    errors = sorted(model.validate(records, chunks_root=args.books_root / city))
    fingerprint = hashlib.sha256("\n".join(errors).encode("utf-8")).hexdigest()[:16]
    print(f"records={len(records)} errors={len(errors)} fingerprint={fingerprint}")
    for error in errors:
        print(f"  {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
