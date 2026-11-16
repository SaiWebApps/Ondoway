"""The one place a model id, its price and its per-job settings are spelled.

Every caller names the JOB it is doing (a role such as ``tour_compose``) and reads the
model, the effort and the price from here; no other module writes a model id. The
settings live in ``src/models.json`` (under ``src/`` so the production image ships it):

* ``prices_checked`` — the date the listed prices were last checked against the
  providers' price lists;
* ``providers`` — pricing rules a provider applies to every model it serves, such as
  the multipliers on the input price for writing and reading cached input;
* ``models`` — each exact model id: its provider, its list price per million tokens,
  a price per minute of audio for a speech model, a speech model's default voice and
  voice settings, and an optional output-ceiling multiplier for models that spend
  more output tokens on the same request;
* ``aliases`` — a short family name for a model id, so one edit moves every job that
  uses that family;
* ``roles`` — each job's model (an alias or a model id), either as a plain string or as
  ``{"model": ..., "effort": ...}`` when the job sets the provider's effort level.

The file is read once and checked whole: every alias and every role must resolve to a
model listed under ``models``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_REGISTRY_PATH = Path(__file__).resolve().parent / "models.json"


@dataclass(frozen=True)
class _Role:
    model_id: str
    effort: str | None


@dataclass(frozen=True)
class _Registry:
    prices_checked: str
    providers: dict[str, dict[str, object]]
    models: dict[str, dict[str, object]]
    roles: dict[str, _Role]


def _resolve(name: str, models: dict[str, dict[str, object]], aliases: dict[str, str]) -> str:
    model_id = aliases.get(name, name)
    if model_id not in models:
        raise ValueError(f"{name!r} names no model listed in {_REGISTRY_PATH.name}")
    return model_id


@lru_cache(maxsize=1)
def _registry() -> _Registry:
    raw = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    models: dict[str, dict[str, object]] = raw["models"]
    aliases: dict[str, str] = raw["aliases"]
    for alias in aliases:
        _resolve(alias, models, aliases)
    roles: dict[str, _Role] = {}
    for role, entry in raw["roles"].items():
        if isinstance(entry, str):
            roles[role] = _Role(_resolve(entry, models, aliases), None)
        else:
            roles[role] = _Role(_resolve(entry["model"], models, aliases), entry.get("effort"))
    return _Registry(
        prices_checked=raw["prices_checked"],
        providers=raw.get("providers", {}),
        models=models,
        roles=roles,
    )


def _role(role: str) -> _Role:
    roles = _registry().roles
    if role not in roles:
        raise KeyError(f"no model role {role!r} in {_REGISTRY_PATH.name}")
    return roles[role]


def _model(model_id: str) -> dict[str, object]:
    models = _registry().models
    if model_id not in models:
        raise KeyError(f"model {model_id!r} is not listed in {_REGISTRY_PATH.name}")
    return models[model_id]


def model_for(role: str) -> str:
    """The exact model id a job uses."""
    return _role(role).model_id


def effort_for(role: str) -> str | None:
    """The effort level a job sends, or None when the job sends none."""
    return _role(role).effort


def known_model_ids() -> frozenset[str]:
    """Every model id the registry lists."""
    return frozenset(_registry().models)


def price_per_mtok(model_id: str) -> tuple[float, float]:
    """A model's list price in US dollars per million (input, output) tokens."""
    entry = _model(model_id)
    if "usd_per_mtok_input" not in entry or "usd_per_mtok_output" not in entry:
        raise KeyError(f"model {model_id!r} carries no price in {_REGISTRY_PATH.name}")
    return float(entry["usd_per_mtok_input"]), float(entry["usd_per_mtok_output"])


def _required(model_id: str, field: str) -> float:
    entry = _model(model_id)
    if field not in entry:
        raise KeyError(f"model {model_id!r} carries no {field} in {_REGISTRY_PATH.name}")
    return float(entry[field])


def input_price_per_mtok(model_id: str) -> float:
    """A model's list price in US dollars per million input tokens."""
    return _required(model_id, "usd_per_mtok_input")


def price_per_audio_minute(model_id: str) -> float:
    """A speech model's list price in US dollars per minute of audio it produces."""
    return _required(model_id, "usd_per_audio_minute")


def default_voice(model_id: str) -> str:
    """The voice a speech model uses when the caller names none."""
    entry = _model(model_id)
    if "default_voice" not in entry:
        raise KeyError(f"model {model_id!r} carries no default_voice in {_REGISTRY_PATH.name}")
    return str(entry["default_voice"])


def voice_settings(model_id: str) -> dict[str, float]:
    """The voice settings a speech model's requests send."""
    entry = _model(model_id)
    if "voice_settings" not in entry:
        raise KeyError(f"model {model_id!r} carries no voice_settings in {_REGISTRY_PATH.name}")
    return {key: float(value) for key, value in dict(entry["voice_settings"]).items()}


def cache_multipliers(model_id: str) -> tuple[float, float]:
    """The (write, read) multipliers a model's provider applies to the input price for
    cached input."""
    provider = str(_model(model_id)["provider"])
    rules = _registry().providers.get(provider)
    if rules is None:
        raise KeyError(f"provider {provider!r} carries no cache pricing in {_REGISTRY_PATH.name}")
    return float(rules["cache_write_multiplier"]), float(rules["cache_read_multiplier"])


def prices_checked() -> str:
    """The date the listed prices were last checked, as YYYY-MM-DD."""
    return _registry().prices_checked


def output_ceiling_multiplier(model_id: str) -> int:
    """How many times the base output-token ceiling a model's requests get."""
    return int(_model(model_id).get("output_ceiling_multiplier", 1))


__all__ = [
    "cache_multipliers",
    "default_voice",
    "effort_for",
    "input_price_per_mtok",
    "known_model_ids",
    "model_for",
    "output_ceiling_multiplier",
    "price_per_audio_minute",
    "price_per_mtok",
    "prices_checked",
    "voice_settings",
]
