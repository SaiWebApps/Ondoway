"""The model registry resolves each job to a model, its effort and its price."""

from __future__ import annotations

import json
import pathlib

import pytest

from src import model_registry

REGISTRY_FILE = pathlib.Path(model_registry.__file__).resolve().parent / "models.json"


def _raw() -> dict:
    return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))


def test_a_role_naming_an_alias_resolves_to_the_aliased_model() -> None:
    raw = _raw()
    aliased = {
        role: entry if isinstance(entry, str) else entry["model"]
        for role, entry in raw["roles"].items()
    }
    alias_roles = {role: name for role, name in aliased.items() if name in raw["aliases"]}
    assert alias_roles, "no role names an alias"
    for role, alias in alias_roles.items():
        assert model_registry.model_for(role) == raw["aliases"][alias]


def test_every_role_resolves_to_a_listed_model() -> None:
    raw = _raw()
    assert raw["roles"]
    for role in raw["roles"]:
        assert model_registry.model_for(role) in model_registry.known_model_ids()


def test_an_unknown_role_raises_naming_the_role() -> None:
    with pytest.raises(KeyError, match="no_such_job"):
        model_registry.model_for("no_such_job")
    with pytest.raises(KeyError, match="no_such_job"):
        model_registry.effort_for("no_such_job")


def test_an_unknown_model_raises() -> None:
    with pytest.raises(KeyError, match="no-such-model"):
        model_registry.price_per_mtok("no-such-model")
    with pytest.raises(KeyError, match="no-such-model"):
        model_registry.output_ceiling_multiplier("no-such-model")


def test_effort_is_the_roles_setting_or_none() -> None:
    raw = _raw()
    for role, entry in raw["roles"].items():
        expected = None if isinstance(entry, str) else entry.get("effort")
        assert model_registry.effort_for(role) == expected
    assert any(model_registry.effort_for(role) is not None for role in raw["roles"])
    assert any(model_registry.effort_for(role) is None for role in raw["roles"])


def test_every_anthropic_model_is_priced() -> None:
    raw = _raw()
    anthropic = [m for m, entry in raw["models"].items() if entry["provider"] == "anthropic"]
    assert anthropic
    for model in anthropic:
        usd_in, usd_out = model_registry.price_per_mtok(model)
        assert 0 < usd_in < usd_out


def test_a_model_without_a_price_raises() -> None:
    raw = _raw()
    unpriced = [m for m, entry in raw["models"].items() if "usd_per_mtok_input" not in entry]
    assert unpriced
    with pytest.raises(KeyError, match="no price"):
        model_registry.price_per_mtok(unpriced[0])


def test_the_speech_model_is_priced_per_minute_and_per_text_token() -> None:
    voice = model_registry.model_for("text_to_speech")
    assert model_registry.price_per_audio_minute(voice) > 0
    assert model_registry.input_price_per_mtok(voice) > 0
    with pytest.raises(KeyError, match="usd_per_audio_minute"):
        model_registry.price_per_audio_minute(model_registry.model_for("tour_compose"))


def test_speech_models_carry_their_voice_settings() -> None:
    raw = _raw()
    openai_voice = model_registry.model_for("text_to_speech")
    expected_voice = raw["models"][openai_voice]["default_voice"]
    assert model_registry.default_voice(openai_voice) == expected_voice
    elevenlabs = model_registry.model_for("text_to_speech_elevenlabs")
    assert model_registry.voice_settings(elevenlabs) == raw["models"][elevenlabs]["voice_settings"]
    with pytest.raises(KeyError, match="default_voice"):
        model_registry.default_voice(model_registry.model_for("tour_compose"))


def test_cache_pricing_comes_from_the_models_provider() -> None:
    raw = _raw()
    rules = raw["providers"]["anthropic"]
    write_x, read_x = model_registry.cache_multipliers(model_registry.model_for("tour_compose"))
    assert (write_x, read_x) == (rules["cache_write_multiplier"], rules["cache_read_multiplier"])
    with pytest.raises(KeyError, match="openai"):
        model_registry.cache_multipliers(model_registry.model_for("text_to_speech"))


def test_the_prices_carry_the_date_they_were_checked() -> None:
    assert model_registry.prices_checked() == _raw()["prices_checked"]


def test_a_role_naming_an_unlisted_model_is_refused(monkeypatch, tmp_path) -> None:
    bad = tmp_path / "models.json"
    bad.write_text(
        json.dumps(
            {
                "prices_checked": "2026-01-01",
                "models": {},
                "aliases": {},
                "roles": {"job": "missing-model"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(model_registry, "_REGISTRY_PATH", bad)
    model_registry._registry.cache_clear()
    try:
        with pytest.raises(ValueError, match="missing-model"):
            model_registry.model_for("job")
    finally:
        monkeypatch.undo()
        model_registry._registry.cache_clear()
