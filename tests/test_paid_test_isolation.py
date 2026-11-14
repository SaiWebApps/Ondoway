"""Guards the paid-provider shard and the definitive test executor."""

from __future__ import annotations

import ast
import runpy
import urllib.request
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]


def test_audio_functional_module_is_marked_live() -> None:
    """The real OpenAI TTS/Whisper suite must run only in the live shard.

    Regression case: the module documented itself as live but lacked a pytest
    marker. On any machine with ``OPENAI_API_KEY`` set, the hermetic local shard
    silently made paid calls. Parse the file instead of importing it because its
    collection-time reachability probe itself uses the network.
    """
    path = REPO / "tests" / "test_audio_functional.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    has_live_marker = any(
        isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets
        )
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "live"
        and isinstance(node.value.value, ast.Attribute)
        and node.value.value.attr == "mark"
        and isinstance(node.value.value.value, ast.Name)
        and node.value.value.value.id == "pytest"
        for node in tree.body
    )

    assert has_live_marker, (
        "tests/test_audio_functional.py makes real paid OpenAI calls and must set "
        "module-level `pytestmark = pytest.mark.live` so `make test` routes it "
        "through `test-live`"
    )


def test_audio_functional_collection_never_probes_the_network(
    monkeypatch,
) -> None:
    """A configured credential must not turn collection into a real API call."""
    path = REPO / "tests" / "test_audio_functional.py"
    monkeypatch.setenv("OPENAI_API_KEY", "canary-must-never-leave-the-process")

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("network attempted while collecting paid tests")

    monkeypatch.setattr(urllib.request, "build_opener", _forbidden)
    monkeypatch.setattr(httpx, "get", _forbidden)
    runpy.run_path(str(path), run_name="__paid_collection_probe__")


def test_default_conftest_scrubs_paid_keys_before_importing_the_api() -> None:
    """Collection-time imports cannot retain paid credentials."""
    source = (REPO / "tests" / "conftest.py").read_text(encoding="utf-8")
    scrub = source.index('os.environ[_paid_key] = ""')
    api_import = source.index("from src.api.app import create_app")

    assert scrub < api_import
    for key in (
        "ANTHROPIC_API_KEY",
        "ELEVENLABS_API_KEY",
        "OPENAI_API_KEY",
        "RESEND_API_KEY",
    ):
        assert f'"{key}"' in source[:api_import]


def test_makefile_live_target_fetches_render_and_enables_live_collection() -> None:
    source = (REPO / "Makefile").read_text(encoding="utf-8")
    live = source.split("test-live:", 1)[1].split("\n\n", 1)[0]

    assert "$(RENDER_TEST_EXEC)" in live
    assert "$(LIVE_TEST_FILES)" in live
    assert "ONDOWAY_LIVE_TESTS=1" in live
    assert "-o addopts=" in live
    assert "-m live" in live


def test_database_reset_cannot_address_cloud_or_all_compose_volumes() -> None:
    """A DATA-LOSS guard: db-reset goes through the one-local-DB check, removes
    exactly the ONE volume it was told, and never runs a compose-wide down or a
    volume prune."""
    source = (REPO / "Makefile").read_text(encoding="utf-8")
    reset = source.split("\ndb-reset:", 1)[1].split("\n\n", 1)[0]
    assert "$(check_db)" in reset, "db-reset must refuse any DB outside LOCAL_DBS"
    assert "docker compose down" not in reset
    assert "volume prune" not in reset and "volume rm -f $(shell" not in reset
    assert reset.count("docker volume rm") == 1, "exactly one volume, the one asked for"
    assert "$(call db_volume,$(DB))" in reset
    check = source.split("\ncheck_db = ", 1)[1].split("\n\n", 1)[0]
    assert "$(LOCAL_DBS)" in check and "never cloud" in check


def test_testflight_bumps_before_building_the_uploaded_ipa() -> None:
    source = (REPO / "Makefile").read_text(encoding="utf-8")
    # The slice is anchored on the NEXT target. `str.split` on a missing
    # needle silently returns the whole remainder — the test then scans far
    # more Makefile than it claims to — so the anchor's existence is asserted
    # loudly first: if `render-status:` is ever removed, re-anchor this to
    # whatever target follows testflight, don't let the slice widen.
    anchor = "\nrender-status:"
    assert anchor in source, "re-anchor this slice to the target after testflight"
    target = source.split("\ntestflight:", 1)[1].split(anchor, 1)[0]

    assert "testflight: flutter-ipa" not in source
    assert target.index("agvtool next-version") < target.index("flutter-ipa")
    assert target.index("flutter-ipa") < target.index("altool --upload-app")


def test_core_configuration_never_loads_dotenv() -> None:
    for relative in ("src/connection.py", "src/api/auth/config.py"):
        source = (REPO / relative).read_text(encoding="utf-8")
        assert "load_dotenv" not in source
