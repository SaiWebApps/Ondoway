"""The committed dependency lock must resolve against public PyPI only.

The Apple-internal mirror (pypi.apple.com) is unreachable off the corporate
network, so a lock pinned to it breaks every fresh install. Measured
2026-07-30: uv.lock carried 1169 Apple URLs for 36 commits (contaminated in
6805c50c, 2026-07-20) and nobody noticed because the main checkout's .venv
was already populated — a fresh worktree could not sync at all. The rule:
the committed lock carries only public PyPI URLs, never the Apple mirror, and
any re-lock runs where public PyPI is reachable. This test is the guard, so
recontamination (e.g. an interrupted `make sync-apple`, which restores the
public lock only at its end) goes RED in
`make test` instead of waiting for the next fresh-clone accident.
"""

from pathlib import Path

LOCK = Path(__file__).resolve().parent.parent / "uv.lock"


def test_lock_pins_only_public_pypi() -> None:
    content = LOCK.read_bytes()
    assert b"pypi.apple.com" not in content, (
        "uv.lock is pinned to the Apple-internal mirror and cannot install "
        "off the corporate network; re-lock where public PyPI is reachable"
    )
    assert b"files.pythonhosted.org" in content, (
        "uv.lock carries no public PyPI artifact URLs at all - wrong index"
    )
