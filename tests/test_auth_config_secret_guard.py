"""Regression tests for the fail-closed JWT/magic-link signing-secret guard.

JWT_SECRET_KEY / MAGIC_LINK_SECRET_KEY must never default to "": a single missing
env var in a deploy would silently downgrade to an HS256 key over the empty
string — trivially forgeable by an attacker. The guard must fail closed (raise) on
an empty/short secret in any real deploy, while still allowing a deterministic
non-empty dev placeholder for local development and the test suite.
"""

from __future__ import annotations

import logging
import sys

import pytest

from src.api.auth import config


class TestSigningSecretGuard:
    def test_empty_secret_fails_closed_when_insecure_not_allowed(self, monkeypatch):
        """An empty secret must raise (not silently return "") outside dev/test."""
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        monkeypatch.delenv("ONDOWAY_ALLOW_INSECURE_AUTH_SECRETS", raising=False)
        # Force _allow_insecure_secrets() to return False by hiding pytest from
        # the module's view of sys.modules (restored automatically after test).
        monkeypatch.delitem(sys.modules, "pytest", raising=False)

        # Inject a no-op sleep: the prod path re-reads with backoff before
        # failing closed; without this the test would sleep ~7.5s of real time.
        with pytest.raises(RuntimeError, match="trivially-forgeable"):
            config._load_signing_secret("JWT_SECRET_KEY", sleep=lambda _: None)

    def test_short_secret_fails_closed(self, monkeypatch):
        """A too-short secret is as dangerous as empty and must also raise."""
        monkeypatch.setenv("JWT_SECRET_KEY", "short")
        monkeypatch.delenv("ONDOWAY_ALLOW_INSECURE_AUTH_SECRETS", raising=False)
        monkeypatch.delitem(sys.modules, "pytest", raising=False)

        with pytest.raises(RuntimeError):
            config._load_signing_secret("JWT_SECRET_KEY", sleep=lambda _: None)

    def test_never_returns_empty_string(self, monkeypatch):
        """Even with the dev escape hatch, the secret is never the empty string."""
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        monkeypatch.setenv("ONDOWAY_ALLOW_INSECURE_AUTH_SECRETS", "1")

        result = config._load_signing_secret("JWT_SECRET_KEY")
        assert result != ""
        assert len(result) >= config._MIN_SECRET_LEN

    def test_strong_secret_is_used_verbatim(self, monkeypatch):
        """A real, long secret is honored as-is (production path)."""
        strong = "x" * config._MIN_SECRET_LEN
        monkeypatch.setenv("JWT_SECRET_KEY", strong)
        monkeypatch.delenv("ONDOWAY_ALLOW_INSECURE_AUTH_SECRETS", raising=False)

        assert config._load_signing_secret("JWT_SECRET_KEY") == strong

    def test_module_level_secrets_are_non_empty(self):
        """The imported module constants must never be the empty string."""
        assert config.JWT_SECRET_KEY != ""
        assert config.MAGIC_LINK_SECRET_KEY != ""


class TestSigningSecretStartupRetry:
    """The bounded, fail-closed startup retry: on the PRODUCTION path only, a
    first empty/short read (an env var injected late by the host) backs off and
    re-reads a few times before failing closed. Every other path must return
    immediately without sleeping. The retry is cheap insurance + telemetry, NOT a
    claimed cure for the race (see the _SECRET_RETRY_* comment in config.py).
    """

    def test_happy_path_returns_verbatim_with_zero_sleeps(self, monkeypatch):
        """A valid secret on the first read never enters the loop or sleeps."""
        strong = "x" * config._MIN_SECRET_LEN
        monkeypatch.setenv("JWT_SECRET_KEY", strong)
        monkeypatch.delenv("ONDOWAY_ALLOW_INSECURE_AUTH_SECRETS", raising=False)

        calls: list[float] = []
        result = config._load_signing_secret("JWT_SECRET_KEY", sleep=calls.append)

        assert result == strong
        assert calls == []  # zero sleeps on the happy path

    def test_insecure_optin_path_never_sleeps(self, monkeypatch):
        """Explicit dev opt-in returns the placeholder with no retry."""
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        monkeypatch.setenv("ONDOWAY_ALLOW_INSECURE_AUTH_SECRETS", "1")

        calls: list[float] = []
        result = config._load_signing_secret("JWT_SECRET_KEY", sleep=calls.append)

        assert result == config._DEV_PLACEHOLDER_SECRET
        assert calls == []

    def test_pytest_path_never_sleeps(self, monkeypatch):
        """Under pytest (no delitem), the placeholder path skips retry."""
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        monkeypatch.delenv("ONDOWAY_ALLOW_INSECURE_AUTH_SECRETS", raising=False)

        calls: list[float] = []
        result = config._load_signing_secret("JWT_SECRET_KEY", sleep=calls.append)

        assert result == config._DEV_PLACEHOLDER_SECRET
        assert calls == []

    def test_late_value_recovered_on_retry_prod_path(self, monkeypatch):
        """A value that appears during backoff is returned.

        This is the load-bearing undo-test: reverting _load_signing_secret to a
        single-read-then-raise flips this RED (it would raise instead of return),
        proving the retry loop is actually exercised.
        """
        monkeypatch.delitem(sys.modules, "pytest", raising=False)
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        monkeypatch.delenv("ONDOWAY_ALLOW_INSECURE_AUTH_SECRETS", raising=False)

        strong = "y" * 40
        calls: list[float] = []

        def fake_sleep(duration: float) -> None:
            calls.append(duration)
            # Simulate the env var becoming observable in-process after 2 backoffs.
            if len(calls) == 2:
                monkeypatch.setenv("JWT_SECRET_KEY", strong)

        result = config._load_signing_secret("JWT_SECRET_KEY", sleep=fake_sleep)

        assert result == strong  # recovered without raising
        assert len(calls) == 2  # two failed reads => exactly two backoffs

    def test_budget_is_bounded_and_uses_full_backoff_schedule(self, monkeypatch):
        """Total default backoff <= 10s over exactly max_attempts-1 sleeps."""
        monkeypatch.delitem(sys.modules, "pytest", raising=False)
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        monkeypatch.delenv("ONDOWAY_ALLOW_INSECURE_AUTH_SECRETS", raising=False)

        durations: list[float] = []
        with pytest.raises(RuntimeError, match="trivially-forgeable"):
            config._load_signing_secret("JWT_SECRET_KEY", sleep=durations.append)

        assert len(durations) == config._SECRET_RETRY_MAX_ATTEMPTS - 1
        assert durations == list(config._SECRET_RETRY_BACKOFFS)  # exact schedule
        assert sum(durations) <= 10.0  # hard wall-clock ceiling

    def test_exhausted_budget_still_fails_closed_when_empty(self, monkeypatch):
        """An empty secret on every read still raises after the budget."""
        monkeypatch.delitem(sys.modules, "pytest", raising=False)
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        monkeypatch.delenv("ONDOWAY_ALLOW_INSECURE_AUTH_SECRETS", raising=False)

        with pytest.raises(RuntimeError, match="trivially-forgeable"):
            config._load_signing_secret("JWT_SECRET_KEY", sleep=lambda _: None)

    def test_exhausted_budget_still_fails_closed_when_short(self, monkeypatch):
        """A persistently short secret is as dangerous as empty and raises."""
        monkeypatch.delitem(sys.modules, "pytest", raising=False)
        monkeypatch.setenv("JWT_SECRET_KEY", "short")
        monkeypatch.delenv("ONDOWAY_ALLOW_INSECURE_AUTH_SECRETS", raising=False)

        with pytest.raises(RuntimeError, match="trivially-forgeable"):
            config._load_signing_secret("JWT_SECRET_KEY", sleep=lambda _: None)

    def test_each_failed_attempt_is_logged_without_leaking_secret(
        self, monkeypatch, caplog
    ):
        """One WARNING per failed attempt, naming the var, leaking no secret."""
        monkeypatch.delitem(sys.modules, "pytest", raising=False)
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        monkeypatch.delenv("ONDOWAY_ALLOW_INSECURE_AUTH_SECRETS", raising=False)

        with (
            caplog.at_level(logging.WARNING, logger="ondoway.api"),
            pytest.raises(RuntimeError, match="trivially-forgeable"),
        ):
            config._load_signing_secret(
                "JWT_SECRET_KEY", max_attempts=3, sleep=lambda _: None
            )

        records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(records) == 2  # max_attempts - 1
        for rec in records:
            msg = rec.getMessage()
            assert "JWT_SECRET_KEY" in msg
            assert "attempt" in msg.lower()
            assert config._DEV_PLACEHOLDER_SECRET not in msg

    def test_empty_backoffs_still_fails_closed_not_indexerror(self, monkeypatch):
        """An empty backoffs tuple must fail closed with the normal RuntimeError,
        never leak an IndexError. `backoffs` is an injectable kwarg, so an empty
        tuple is a reachable misuse; it must degrade to zero-delay retries that
        still abort the boot, not crash with a confusing index error.
        """
        monkeypatch.delitem(sys.modules, "pytest", raising=False)
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        monkeypatch.delenv("ONDOWAY_ALLOW_INSECURE_AUTH_SECRETS", raising=False)

        with pytest.raises(RuntimeError, match="trivially-forgeable"):
            config._load_signing_secret(
                "JWT_SECRET_KEY", backoffs=(), sleep=lambda _: None
            )
