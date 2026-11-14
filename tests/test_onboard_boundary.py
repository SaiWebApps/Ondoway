"""Hard-boundary tests: network allowlist, metadata-only pointer type, clean repo data.

These lock the STRUCTURAL guarantees that keep shadow-library (Anna's Archive /
WeLib) copyrighted text out of the pipeline:

- Network: src/onboard/fetch.py's allowlist rejects every non license-clean
  host, including lookalikes.
- Type:    DiscoveryPointer has no text field and forbids extras.
- Repo:    no committed data/*/beats.json attribution references a
  shadow-library domain.

Pure — no Neo4j, no network. Run with:
    make test-file FILE=tests/test_onboard_boundary.py
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from src.onboard import fetch
from src.onboard.fetch import (
    INGEST_DOMAIN_ALLOWLIST,
    IngestDomainError,
    assert_ingestable,
    is_ingest_host_allowed,
)
from src.onboard.models import DiscoveryPointer

# ---------------------------------------------------------------------------
# Network allowlist
# ---------------------------------------------------------------------------

BLOCKED_URLS = [
    "https://annas-archive.org/md5/abc123",
    "https://welib.org/book/42",
    "https://welib.se/read/99",
    "http://libgen.is/index.php?req=paris",
    "https://example.com/x",
    # Lookalike: a shadow host that merely SUFFIXES an allowed brand must fail.
    "https://wikipedia.org.evil.com/paris",
]

ALLOWED_URLS = [
    "https://en.wikipedia.org/wiki/Paris",
    "https://en.wikivoyage.org/wiki/Paris",
    "https://query.wikidata.org/sparql?query=SELECT",
    "https://overpass-api.de/api/interpreter",
    "https://www.gutenberg.org/ebooks/1",
    "https://archive.org/advancedsearch.php",
    "https://gutendex.com/books",
]


@pytest.mark.parametrize("url", BLOCKED_URLS)
def test_assert_ingestable_rejects_non_allowlisted(url: str) -> None:
    assert is_ingest_host_allowed(url) is False
    with pytest.raises(IngestDomainError):
        assert_ingestable(url)


@pytest.mark.parametrize("url", ALLOWED_URLS)
def test_assert_ingestable_allows_license_clean(url: str) -> None:
    assert is_ingest_host_allowed(url) is True
    assert_ingestable(url)  # must NOT raise


def test_lookalike_suffix_is_rejected() -> None:
    """Two distinct lookalike shapes must both be rejected — one genuinely
    discriminates between the naive and the correct impl:

    - ``notwikipedia.org`` ends with the bare brand ``wikipedia.org``, so a naive
      ``host.endswith(d)`` would WRONGLY allow it; the dotted-suffix rule
      (``host == d or host.endswith("." + d)``) correctly rejects it. This case
      is RED under the naive bug and GREEN under the fix — it is the real test.
    - ``wikipedia.org.evil.com`` is a different registrable domain that only
      prefixes the brand; it is rejected under BOTH impls (``endswith(d)`` is
      False), so it does not discriminate — kept as an extra guard."""
    # The discriminator: naive endswith(d) allows it, dotted-suffix rejects it.
    assert is_ingest_host_allowed("https://notwikipedia.org/x") is False
    with pytest.raises(IngestDomainError):
        assert_ingestable("https://notwikipedia.org/x")
    # Non-discriminating guard: a different-registrable-domain lookalike.
    assert is_ingest_host_allowed("https://wikipedia.org.evil.com/x") is False
    with pytest.raises(IngestDomainError):
        assert_ingestable("https://wikipedia.org.evil.com/x")


def test_shadow_domains_are_not_on_the_allowlist() -> None:
    for shadow in ("annas-archive.org", "welib.org", "welib.se", "libgen.is"):
        assert shadow not in INGEST_DOMAIN_ALLOWLIST


def test_missing_host_is_rejected() -> None:
    assert is_ingest_host_allowed("not-a-url") is False
    assert is_ingest_host_allowed("") is False


def test_allowlisted_host_with_port_is_allowed() -> None:
    assert is_ingest_host_allowed("https://en.wikipedia.org:443/wiki/Paris") is True


# ---------------------------------------------------------------------------
# DiscoveryPointer is metadata-only (no text field, extras forbidden)
# ---------------------------------------------------------------------------

TEXT_BEARING_FIELDS = ["text", "content", "body", "full_text"]


@pytest.mark.parametrize("field", TEXT_BEARING_FIELDS)
def test_discovery_pointer_has_no_text_field(field: str) -> None:
    assert field not in DiscoveryPointer.model_fields


@pytest.mark.parametrize("field", TEXT_BEARING_FIELDS)
def test_discovery_pointer_rejects_text_via_kwargs(field: str) -> None:
    with pytest.raises(ValidationError):
        DiscoveryPointer(
            source="annas_archive",
            title="Some Book",
            source_url="https://annas-archive.org/md5/abc",
            **{field: "copyrighted body text"},
        )


def test_discovery_pointer_valid_metadata_constructs() -> None:
    p = DiscoveryPointer(
        source="annas_archive",
        title="Around and About London",
        author="Someone",
        year=1901,
        format="epub",
        source_url="https://annas-archive.org/md5/abc",
    )
    assert p.title == "Around and About London"
    # Sanity: the instance genuinely has no text-bearing attribute.
    for field in TEXT_BEARING_FIELDS:
        assert not hasattr(p, field)


# ---------------------------------------------------------------------------
# No committed beats.json attribution references a shadow library
# ---------------------------------------------------------------------------

# Shadow-library brand tokens. Matched only as word-bounded / domain-ish tokens
# (\b on both sides), NOT as bare substrings: `welib` matches `welib.se` and
# `www.welib.com` but NOT the `welib` inside `jewelibrary` (there the leading `w`
# is preceded by a word char, so there is no boundary and no match). This keeps
# the all-leaves scan from false-positiving on ordinary long narration text.
_SHADOW_BRANDS = ("annas-archive", "libgen", "z-lib", "zlibrary", "welib")
_SHADOW_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(b) for b in _SHADOW_BRANDS) + r")\b",
    re.IGNORECASE,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _shadow_hits_in_beat(beat: dict) -> list[str]:
    """Every string leaf anywhere in `beat` (ALL keys, ALL nesting) that
    references a shadow-library brand as a word-bounded / domain-ish token.

    Walking every leaf — not a hand-picked subset of attribution keys — is what
    makes the scan non-evadable: a shadow ref hidden under `source_passage`,
    `_meta`, `fact_check`, `provenance_note`, or any other key is still caught."""
    hits: list[str] = []

    def walk(obj: object) -> None:
        if isinstance(obj, dict):
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)
        elif isinstance(obj, str) and _SHADOW_RE.search(obj):
            hits.append(obj)

    walk(beat)
    return hits


def test_scan_catches_shadow_ref_in_any_key() -> None:
    """A shadow ref hidden under ANY real key (not just source_url/*url*) must be
    caught: the scan walks every string leaf, so it is not evadable by relocating
    the ref to an unexpected key."""
    beat = {
        "beat_id": "b1",
        "source_chunk_slug": "libgen-md5-abc",
        "source_passage": "see annas-archive.org for the scan",
        "_meta": {"ingest_source": "welib.se"},
        "fact_check": {"notes": "cross-checked against a libgen scan"},
        "provenance_note": "acquired via annas-archive",
    }
    hits = _shadow_hits_in_beat(beat)
    assert len(hits) == 5, hits
    joined = " ".join(hits).lower()
    for token in ("libgen-md5-abc", "annas-archive.org", "welib.se"):
        assert token in joined


def test_scan_does_not_false_positive_on_ordinary_words() -> None:
    """Ordinary words that merely CONTAIN a brand as a substring (e.g.
    'jewelibrary') must NOT be flagged — the match is word-bounded / domain-ish."""
    beat = {
        "beat_id": "b2",
        "title": "a jewelibrary in Paris",
        "body": "the public library",
        "note": "calibrate the genre",
    }
    assert _shadow_hits_in_beat(beat) == []


def _beats_files() -> list[Path]:
    return sorted((_REPO_ROOT / "data").glob("*/beats.json"))


def test_beats_files_exist() -> None:
    # Guards against a silently-empty scan (glob matching nothing → vacuous pass).
    assert _beats_files(), "no data/*/beats.json found — scan would be vacuous"


@pytest.mark.parametrize("beats_path", _beats_files(), ids=lambda p: p.parent.name)
def test_no_beat_attribution_references_shadow_library(beats_path: Path) -> None:
    beats = json.loads(beats_path.read_text())
    offenders: list[str] = []
    for beat in beats:
        for value in _shadow_hits_in_beat(beat):
            offenders.append(f"{beat.get('beat_id', '?')}: {value!r}")
    assert not offenders, (
        f"{beats_path} has beats attributing a shadow-library source: {offenders[:5]}"
    )


# ---------------------------------------------------------------------------
# Connector isolation: shadow_discovery.py can never reach the network
# ---------------------------------------------------------------------------

_SHADOW_DISCOVERY_SRC = _REPO_ROOT / "src" / "onboard" / "sources" / "shadow_discovery.py"

# Any of these would give the discovery connector a path to the network. If NONE
# is imported, the connector is STRUCTURALLY discovery-only: it holds no code
# that can fetch, so it can only ever surface metadata pointers. The set is
# broad on purpose: a future editor reaching for a DIFFERENT network client
# (aiohttp / urllib3) or dropping to raw sockets / a low-level HTTP connection
# (socket / http.client) is just as much a network path as httpx — each must be
# flagged, so each has its OWN entry (a prefix of one does NOT imply another:
# ``urllib3`` is not under ``urllib``, ``http.client`` is not under ``httpx``).
_FORBIDDEN_IMPORTS = frozenset(
    {
        "src.onboard.fetch",  # the network door (allowlisted GET)
        "src.onboard.sources.base",  # run_connector — the ONE thing that fetches
        "httpx",
        "requests",
        "urllib.request",
        "urllib",  # bare urllib (urllib.request lives under it)
        "urllib3",  # separate 3rd-party client — NOT under stdlib urllib
        "aiohttp",  # async HTTP client
        "socket",  # raw sockets — the lowest-level network path
        "http.client",  # stdlib low-level HTTP (HTTPConnection/HTTPSConnection)
        "http",  # bare http (http.client / http.server live under it)
    }
)


def _module_is_forbidden(name: str) -> bool:
    """True if ``name`` IS a forbidden module or lives UNDER one (dotted-prefix),
    so ``http.client.HTTPSConnection`` and ``urllib.request`` both match. The
    dotted boundary is deliberate: ``httptools`` does NOT match ``http`` and
    ``urllib3`` does NOT match ``urllib`` — those network-capable siblings need
    their OWN entries in the forbidden set, they are not covered by a prefix."""
    return any(name == f or name.startswith(f + ".") for f in _FORBIDDEN_IMPORTS)


def _imported_modules(source: str) -> set[str]:
    """Every module named by an import in ``source``, PLUS each ``from X import Y``
    target as the combined dotted path ``X.Y``.

    Recording ``X.Y`` is what makes ``from src.onboard import fetch`` detectable,
    not only ``import src.onboard.fetch``: the former's ``ast.ImportFrom`` carries
    ``module="src.onboard"`` with name ``fetch``, so only the combined
    ``src.onboard.fetch`` reveals the forbidden target."""
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
            for alias in node.names:
                modules.add(f"{node.module}.{alias.name}")
    return modules


def _forbidden_imports_in_source(src: str) -> list[str]:
    """Every forbidden network-capable module referenced in ``src`` — by STATIC
    import (``import X`` / ``from X import Y``) OR by a DYNAMIC string-literal
    import (``__import__("X")`` / ``importlib.import_module("X")``).

    Refactored out of the test body so the guard is unit-testable against
    adversarial source strings, proving it catches an evasion WITHOUT mutating
    the real connector on disk.

    HONEST SCOPE LIMIT — this is a SINGLE-FILE check. It proves the connector
    FILE itself contains no network-capable import, static or string-literal
    dynamic. It deliberately does NOT follow TRANSITIVE imports: if
    shadow_discovery imported a LOCAL module that itself imported httpx, that
    indirection is invisible to a single-file AST walk. A full import-graph /
    dependency-closure check (resolve + parse every reachable module) is
    deliberately OUT OF SCOPE — over-scoping it here would trade a crisp,
    fast, deterministic file-level guarantee for a fragile whole-graph one.
    The contract is precisely "this connector FILE is network-free" (and the
    connector is kept tiny so that file-level guarantee is meaningful). Also
    not detectable statically: a COMPUTED module name (``__import__(var)`` where
    the string is built at runtime) — only string-LITERAL dynamic imports."""
    tree = ast.parse(src)
    hits: set[str] = set()

    # (1) Static imports — reuse _imported_modules' `import X` + `from X import Y`
    #     (recorded as the combined `X.Y`) coverage.
    for module in _imported_modules(src):
        if _module_is_forbidden(module):
            hits.add(module)

    # (2) Dynamic string-literal imports: `__import__("X")` and
    #     `importlib.import_module("X")` (or a bare `import_module("X")` when it
    #     was `from importlib import import_module`) are ast.Call nodes, NOT
    #     ast.Import — the static walk above cannot see them. Flag any such call
    #     whose first positional arg is a string literal naming a forbidden
    #     module. (A NON-literal arg, e.g. a variable, is intentionally uncaught:
    #     it is not statically resolvable — see the SCOPE LIMIT above.)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        func = node.func
        is_builtin_import = isinstance(func, ast.Name) and func.id == "__import__"
        is_importlib_call = (
            isinstance(func, ast.Attribute)
            and func.attr == "import_module"
            and isinstance(func.value, ast.Name)
            and func.value.id == "importlib"
        ) or (isinstance(func, ast.Name) and func.id == "import_module")
        if (is_builtin_import or is_importlib_call) and _module_is_forbidden(first.value):
            hits.add(first.value)

    return sorted(hits)


@pytest.mark.parametrize(
    "adversarial_src",
    [
        "import socket",
        "import aiohttp",
        "import urllib3",
        "from http.client import HTTPSConnection",
        '__import__("httpx")',
        'importlib.import_module("requests")',
        'import importlib\nimportlib.import_module("requests")',
        'from importlib import import_module\nimport_module("socket")',
    ],
)
def test_forbidden_import_check_catches_evasions(adversarial_src: str) -> None:
    """The guard must catch evasions the naive Import-only + fixed-set walk
    MISSED: network-capable modules outside the original set (socket / aiohttp /
    urllib3 / http.client) AND dynamic string-literal imports (``__import__`` /
    ``importlib.import_module``). Each is RED against the old guard, GREEN after
    strengthening."""
    assert _forbidden_imports_in_source(adversarial_src), (
        f"guard failed to flag a network-capable evasion: {adversarial_src!r}"
    )


def test_forbidden_import_check_passes_clean_source() -> None:
    """A discovery-only source that imports ONLY models is not flagged — the
    guard does not false-positive on legitimate metadata-only code."""
    clean = (
        "from __future__ import annotations\n"
        "from src.onboard.models import CityContext, DiscoveryPointer\n"
    )
    assert _forbidden_imports_in_source(clean) == []


def test_shadow_discovery_never_imports_the_fetcher() -> None:
    """The shadow-discovery connector imports ONLY ``src.onboard.models`` —
    never the fetch door, ``run_connector``, or any HTTP client. Parsed from the
    source with ``ast`` so the guarantee is STRUCTURAL, not a runtime accident: a
    connector that cannot import a network path cannot fetch, so it can only ever
    surface metadata pointers."""
    leaked = _forbidden_imports_in_source(_SHADOW_DISCOVERY_SRC.read_text())
    assert not leaked, f"shadow_discovery.py imports a network-capable module: {leaked}"


# ---------------------------------------------------------------------------
# Wikimedia User-Agent policy: a live GET must carry a descriptive
# default User-Agent (en.wikipedia.org 403s an anonymous UA), and an explicit
# caller header still WINS over the default. Live mode only — fixture mode
# raises before any httpx call, so it is untouched. Kept network-free by
# monkeypatching httpx.get.
# ---------------------------------------------------------------------------


class _CaptureResponse:
    """A minimal httpx-response stand-in: answers only the two methods the fetch
    door calls after a GET (``raise_for_status`` + ``json``), plus a ``text``
    attribute for the text door."""

    text = "body"

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"ok": True}


def _patch_live_capture(monkeypatch) -> dict:
    """Put the fetch door in live mode with a fake ``httpx.get`` that RECORDS the
    kwargs it was called with, so a test can assert the headers actually sent —
    without ever touching the network."""
    captured: dict = {}

    def _fake_get(url, params=None, timeout=None, headers=None):
        captured["url"] = url
        captured["headers"] = headers
        return _CaptureResponse()

    monkeypatch.setattr(httpx, "get", _fake_get)
    monkeypatch.setattr(fetch, "HTTP_MODE", "live")
    return captured


def test_onboard_user_agent_constant_is_descriptive_with_contact() -> None:
    """Wikimedia's UA policy requires a DESCRIPTIVE agent naming the tool + a
    contact. The module constant satisfies it (tool name + a contact URL)."""
    ua = fetch.ONBOARD_USER_AGENT
    assert "Ondoway" in ua
    assert "ondoway.com" in ua


def test_http_get_json_sends_default_user_agent(monkeypatch) -> None:
    """A live JSON GET with no caller headers still carries the descriptive
    default UA, because en.wikipedia.org answers an anonymous UA with a 403.

    UNDO: pass ``headers=headers`` (drop the UA merge) -> ``captured['headers']``
    is None -> this goes RED."""
    captured = _patch_live_capture(monkeypatch)
    fetch.http_get_json("https://en.wikipedia.org/w/api.php", params={"a": "b"})
    assert captured["headers"]["User-Agent"] == fetch.ONBOARD_USER_AGENT
    assert "Ondoway" in captured["headers"]["User-Agent"]


def test_http_get_text_sends_default_user_agent(monkeypatch) -> None:
    """The text door merges the SAME default UA in as the JSON door.

    UNDO: drop the UA merge in http_get_text -> ``captured['headers']`` is None
    -> RED."""
    captured = _patch_live_capture(monkeypatch)
    fetch.http_get_text("https://en.wikipedia.org/wiki/Paris")
    assert "Ondoway" in captured["headers"]["User-Agent"]


def test_caller_user_agent_overrides_default(monkeypatch) -> None:
    """An explicit caller ``User-Agent`` still WINS over the default (the merge
    spreads the caller's headers LAST). Guards that the default does not clobber a
    connector that sets its own UA."""
    captured = _patch_live_capture(monkeypatch)
    fetch.http_get_json(
        "https://en.wikipedia.org/w/api.php", headers={"User-Agent": "Custom-Caller/9.9"}
    )
    assert captured["headers"]["User-Agent"] == "Custom-Caller/9.9"
