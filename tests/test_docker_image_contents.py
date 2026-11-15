"""Regression test: every first-party package that src/ imports must be COPY'd
into the Docker image.

2026-07-18 deploy dep-d9e0ls3bc2fs73fo4gh0 crashed at import time with
`ModuleNotFoundError: No module named 'scripts'`: src/onboard/assemble.py (and
flow/extract/beat_draft) import `scripts.beat_builder`, but the Dockerfile only
copied src/ and the public web pages. This walks every module under src/, collects the
top-level packages it imports (module-level AND lazy, since both must resolve
inside the container), keeps the first-party ones (those that exist in the repo
root), and asserts each is both COPY'd by the Dockerfile and not excluded by
.dockerignore.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
SRC = REPO_ROOT / "src"


def _copied_roots() -> set[str]:
    """Top-level names the Dockerfile COPYs into the image (`COPY src/ src/`
    -> `src`)."""
    roots: set[str] = set()
    for line in DOCKERFILE.read_text().splitlines():
        parts = line.strip().split()
        if len(parts) >= 3 and parts[0] == "COPY":
            for source in parts[1:-1]:
                roots.add(source.strip("./").rstrip("/").split("/")[0])
    return roots


def _dockerignored(name: str) -> bool:
    """True when .dockerignore excludes the top-level directory `name` (plain
    `name/` entries only — good enough for whole-package exclusions) without a
    later `!name/**` re-include."""
    excluded = False
    for line in DOCKERIGNORE.read_text().splitlines():
        entry = line.strip()
        if entry in (f"{name}/", name):
            excluded = True
        elif entry.startswith(f"!{name}/") or entry == f"!{name}":
            excluded = False
    return excluded


def _first_party_imports() -> dict[str, set[str]]:
    """Map of first-party top-level package -> src files importing it. A
    package is first-party when it exists as a directory or module in the repo
    root; everything else is stdlib or pip-installed."""
    imports: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module.split(".")[0]]
            else:
                continue
            for name in names:
                if name in sys.stdlib_module_names:
                    continue
                if (REPO_ROOT / name).is_dir() or (REPO_ROOT / f"{name}.py").is_file():
                    imports.setdefault(name, set()).add(str(path.relative_to(REPO_ROOT)))
    return imports


def test_src_first_party_imports_are_shipped_in_image() -> None:
    copied = _copied_roots()
    missing = {
        pkg: sorted(files)
        for pkg, files in _first_party_imports().items()
        if pkg not in copied or _dockerignored(pkg)
    }
    assert not missing, (
        "src/ imports first-party packages the Docker image does not ship "
        "(add `COPY <pkg>/ <pkg>/` to the Dockerfile or drop the dependency): "
        f"{missing}"
    )


def test_guard_actually_sees_the_known_dependencies() -> None:
    """Self-check: the scanner must at minimum see src->src and src->scripts,
    otherwise a refactor of the scanner could silently pass on nothing."""
    found = _first_party_imports()
    assert "scripts" in found, "scanner no longer sees src/onboard's scripts imports"
    assert "src" in found, "scanner no longer sees src's own absolute imports"


def test_image_ships_only_the_public_web_pages() -> None:
    """The workbench and the internal business pages never reach production.

    The workbench edits the story database without a sign-in, and the internal pages
    hold prices and costs. So the image copies the public site and nothing else
    from web/, and leaves out the business cost tools under scripts/.
    """
    web_sources = [
        source.strip("./").rstrip("/")
        for line in DOCKERFILE.read_text().splitlines()
        if (parts := line.strip().split()) and parts[0] == "COPY"
        for source in parts[1:-1]
        if source.strip("./").startswith("web")
    ]
    assert web_sources == ["web/site/public"], web_sources
    ignored = {line.strip() for line in DOCKERIGNORE.read_text().splitlines()}
    assert "scripts/business/" in ignored, "the business cost tools would ship in the image"
