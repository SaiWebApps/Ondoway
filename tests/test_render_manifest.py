"""Deploy-manifest guard — the public Render service must gate off the workbench.

``render.yaml`` provisions the public ``ondoway-api`` service, so it must set
``WORKBENCH_API_ENABLED`` to an off value.

``src/api/app.py`` mounts the editorial-workbench graph-CRUD routers (graph,
nodes, edges, schema) — an UNAUTHENTICATED create / update / DETACH DELETE
surface over the live Neo4j/Aura graph — whenever ``WORKBENCH_API_ENABLED`` is
not an explicit off value (its default is ``"true"``). If the manifest that
provisions prod leaves the flag out, that destructive write surface is live on
the internet.

This test reads the committed manifest and asserts the gate is set to a value
that ``app._workbench_api_enabled()`` actually treats as OFF. It fails when the
flag is absent.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from src.api.app import _workbench_api_enabled

_RENDER_YAML = Path(__file__).resolve().parents[1] / "render.yaml"


def _ondoway_api_service() -> dict:
    """Return the ``ondoway-api`` web service block from render.yaml."""
    manifest = yaml.safe_load(_RENDER_YAML.read_text())
    services = manifest["services"]
    return next(s for s in services if s.get("name") == "ondoway-api")


def _ondoway_api_env() -> dict[str, str]:
    """Return the ``ondoway-api`` web service's env-var map from render.yaml."""
    api = _ondoway_api_service()
    return {var["key"]: var.get("value") for var in api.get("envVars", []) if "value" in var}


def _ondoway_api_declared_keys() -> set[str]:
    """Every env-var key the manifest declares (pinned, generated, or sync:false).

    A ``sync: false`` entry has no value in the manifest but IS declared — Render
    requires the operator to supply it, so the service is reproducible from the
    blueprint. A key that appears nowhere is simply absent in a fresh deploy.
    """
    api = _ondoway_api_service()
    return {var["key"] for var in api.get("envVars", [])}


def _ondoway_api_env_entry(key: str) -> dict:
    api = _ondoway_api_service()
    return next(var for var in api.get("envVars", []) if var.get("key") == key)


def test_public_render_deploy_gates_workbench_crud_off():
    """The manifest must pin WORKBENCH_API_ENABLED to an off value, so the
    unauthenticated graph-CRUD surface is never mounted in production."""
    env = _ondoway_api_env()
    assert "WORKBENCH_API_ENABLED" in env, (
        "render.yaml must set WORKBENCH_API_ENABLED so the public deploy disengages "
        "the unauthenticated graph-CRUD (node/edge create/update/DETACH DELETE) surface"
    )
    value = env["WORKBENCH_API_ENABLED"]
    # Prove the pinned value is one app._workbench_api_enabled() treats as OFF —
    # a truthy/typo'd value would silently leave the surface live.
    import os

    prior = os.environ.get("WORKBENCH_API_ENABLED")
    os.environ["WORKBENCH_API_ENABLED"] = str(value)
    try:
        assert _workbench_api_enabled() is False, (
            f"WORKBENCH_API_ENABLED={value!r} in render.yaml is not treated as OFF "
            "by app._workbench_api_enabled(); the workbench CRUD surface stays mounted"
        )
    finally:
        if prior is None:
            os.environ.pop("WORKBENCH_API_ENABLED", None)
        else:
            os.environ["WORKBENCH_API_ENABLED"] = prior


# --- Defect: a pinned provider whose credential the manifest never declares ---
#
# render.yaml pins TTS_PROVIDER=openai but declared no OPENAI_API_KEY, so a
# blueprint-provisioned service has no TTS credential at all: every synthesis
# raises TTSError and surfaces as HTTP 502 "TTS generation failed (openai)"
# (src/api/routes/app/audio.py:150-152). The mapping below mirrors
# audio._provider_available (audio.py:110-116) — whatever a provider needs to
# be *available* at runtime is exactly what the manifest must declare.

_TTS_PROVIDER_CREDENTIALS = {
    "openai": ["OPENAI_API_KEY"],
    "elevenlabs": ["ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"],
    "mock": [],
}

_COMPOSE_PROVIDER_CREDENTIALS = {
    "anthropic": ["ANTHROPIC_API_KEY"],
    "claude": ["ANTHROPIC_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "chatgpt": ["OPENAI_API_KEY"],
}


def test_pinned_tts_provider_credential_is_declared():
    """Whichever TTS provider render.yaml pins must have its credential declared."""
    provider = _ondoway_api_env().get("TTS_PROVIDER")
    if provider is None:
        return  # not pinned — the app's default applies, nothing to guard here
    assert provider in _TTS_PROVIDER_CREDENTIALS, (
        f"render.yaml pins TTS_PROVIDER={provider!r}, which this guard does not know; "
        "add it to _TTS_PROVIDER_CREDENTIALS (mirror audio._provider_available)"
    )
    declared = _ondoway_api_declared_keys()
    for key in _TTS_PROVIDER_CREDENTIALS[provider]:
        assert key in declared, (
            f"render.yaml pins TTS_PROVIDER={provider!r} but never declares {key} "
            "(not even as `sync: false`), so a service provisioned from this blueprint "
            "has no TTS credential and every /audio generate call 502s"
        )


def test_every_tts_fallback_provider_credential_is_declared():
    """The understudies must be provisioned too, or the chain is decoration.

    TTS_FALLBACK names the providers a TOURIST's audio falls back to when the
    pinned voice cannot speak. An understudy whose credentials the manifest never
    declares raises "ELEVENLABS_API_KEY not set" the moment it is finally needed —
    which is precisely during the outage the chain exists to survive. Same guard
    as the pinned provider above, applied to every name in the chain.
    """
    fallback = _ondoway_api_env().get("TTS_FALLBACK")
    if not fallback:
        return  # no chain configured — nothing to guard
    declared = _ondoway_api_declared_keys()
    for name in [part.strip() for part in fallback.split(",") if part.strip()]:
        assert name in _TTS_PROVIDER_CREDENTIALS, (
            f"render.yaml lists TTS_FALLBACK={name!r}, which this guard does not know; "
            "add it to _TTS_PROVIDER_CREDENTIALS (mirror audio._provider_available)"
        )
        for key in _TTS_PROVIDER_CREDENTIALS[name]:
            assert key in declared, (
                f"render.yaml falls back to {name!r} but never declares {key} (not even as "
                "`sync: false`), so the understudy fails the moment the primary does"
            )


def test_pinned_compose_provider_credential_is_declared():
    """Same guard for the narration composer, so a future pin drags its key along."""
    provider = _ondoway_api_env().get("COMPOSE_PROVIDER")
    if provider is None:
        return
    assert provider in _COMPOSE_PROVIDER_CREDENTIALS, (
        f"render.yaml pins COMPOSE_PROVIDER={provider!r}, unknown to this guard; "
        "add it to _COMPOSE_PROVIDER_CREDENTIALS"
    )
    declared = _ondoway_api_declared_keys()
    for key in _COMPOSE_PROVIDER_CREDENTIALS[provider]:
        assert key in declared, (
            f"render.yaml pins COMPOSE_PROVIDER={provider!r} but never declares {key}"
        )


def test_premium_preview_is_bound_to_the_private_valhalla_service():
    manifest = yaml.safe_load(_RENDER_YAML.read_text())
    binding = _ondoway_api_env_entry("VALHALLA_URL")["fromService"]
    assert binding == {
        "type": "pserv",
        "name": "ondoway-valhalla",
        "property": "hostport",
    }
    routing = next(
        service for service in manifest["services"] if service.get("name") == "ondoway-valhalla"
    )
    assert routing["type"] == "pserv"
    assert routing["runtime"] == "image"
    assert routing["disk"]["mountPath"] == "/custom_files"
    routing_env = {entry["key"]: entry.get("value") for entry in routing["envVars"]}
    assert "ile-de-france" in routing_env["tile_urls"]
    assert "NewYork" in routing_env["tile_urls"]


# --- Defect: durable audio URLs written to ephemeral container storage ---
#
# AUDIO_STORAGE=local resolves to the container filesystem
# (src/audio/storage.py:52). On `plan: free` with no `disk:` block that
# filesystem is ephemeral, so every generated MP3 dies on restart — while
# beat.audio_url is persisted in Aura (stamped ON CREATE only, so a redeploy
# never clears it) and the generate routes short-circuit on the sticky URL.
# Result: a permanent 404 from GET /api/v1/audio/files/{key}. Local storage is
# only admissible here if the service actually mounts a persistent disk.

_DURABLE_STORAGE_CREDENTIALS = {
    "r2": ["R2_ENDPOINT_URL", "R2_PUBLIC_URL"],
    "s3": ["AWS_S3_BUCKET"],
}


def test_audio_storage_is_durable():
    """Audio must not be written to a filesystem that vanishes on restart."""
    service = _ondoway_api_service()
    storage = _ondoway_api_env().get("AUDIO_STORAGE", "local")

    if storage == "local":
        assert service.get("disk"), (
            "render.yaml sets AUDIO_STORAGE=local but the ondoway-api service declares "
            "no `disk:` block, so AUDIO_STORAGE_PATH is the ephemeral container "
            "filesystem: every generated MP3 is destroyed on restart while audio_url "
            "stays persisted in Aura, leaving a permanent 404. Use r2/s3, or mount a disk."
        )
        return

    assert storage in _DURABLE_STORAGE_CREDENTIALS, (
        f"render.yaml sets AUDIO_STORAGE={storage!r}, which get_storage() cannot build "
        "(available: local, s3, r2)"
    )
    declared = _ondoway_api_declared_keys()
    for key in _DURABLE_STORAGE_CREDENTIALS[storage]:
        assert key in declared, (
            f"render.yaml sets AUDIO_STORAGE={storage!r} but never declares {key}, "
            "so the storage provider raises StorageError at startup of the first upload"
        )
