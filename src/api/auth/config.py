"""Auth configuration loaded from environment variables."""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Callable

# Minimum acceptable length (in characters) for HS256 signing secrets. A short
# or empty secret makes JWTs trivially forgeable; render.yaml provisions these
# with generateValue:true, which is comfortably longer than this floor.
_MIN_SECRET_LEN: int = 32

# Fixed, obviously-fake stand-in used ONLY in non-production contexts (the test
# suite, or an explicit local opt-in). It is deterministic and clearly not a
# real secret, but it is never the empty string, so tokens minted in dev are
# not signed over "" and a missing env var can never silently downgrade the
# production deploy to a trivially-forgeable key.
_DEV_PLACEHOLDER_SECRET: str = "ondoway-insecure-dev-secret-do-not-use-in-production"

# Bounded retry for the PRODUCTION fail-closed path only: if a signing secret
# reads empty/short at startup, re-read os.getenv a few times with a short
# backoff before giving up. This is CHEAP INSURANCE + TELEMETRY against a deploy
# platform injecting env late, not a cure. Honest limit: under CPython/POSIX exec
# semantics os.getenv reads os.environ, a snapshot taken at process start, so a
# value Render injects into the container AFTER this process started is NOT
# visible to an in-process re-read — only a FRESH process (container restart)
# recovers there. This loop therefore only rescues a value that becomes
# observable IN-PROCESS within the budget (e.g. an import-order / dotenv
# transient); it can never weaken the fail-closed guard below, costs nothing on
# every healthy path, and — most usefully — logs each attempt so any recurrence
# is diagnosable. Genuinely-late CONTAINER env needs an entrypoint shell wait
# before `exec uvicorn`, which the Dockerfile does not have.
_SECRET_RETRY_MAX_ATTEMPTS: int = 6
# One backoff per gap between attempts (max_attempts - 1 == 5 gaps); the sum is
# 7.5s, a hard <=10s ceiling far under Render's deploy timeout. Extra gaps (were
# max_attempts ever raised past len+1) clamp to the last value.
_SECRET_RETRY_BACKOFFS: tuple[float, ...] = (0.5, 1.0, 2.0, 2.0, 2.0)


def _allow_insecure_secrets() -> bool:
    """Whether an empty/short signing secret may fall back to a dev placeholder.

    True only outside a real deploy: while running under pytest, or when a
    developer explicitly opts in via ONDOWAY_ALLOW_INSECURE_AUTH_SECRETS. Never
    true on the production deploy, which loads real generated secrets and does
    not set this flag or run under pytest.
    """
    if os.getenv("ONDOWAY_ALLOW_INSECURE_AUTH_SECRETS", "").lower() in {"1", "true", "yes"}:
        return True
    return "pytest" in sys.modules


def _load_signing_secret(
    env_var: str,
    *,
    max_attempts: int = _SECRET_RETRY_MAX_ATTEMPTS,
    backoffs: tuple[float, ...] = _SECRET_RETRY_BACKOFFS,
    sleep: Callable[[float], None] | None = None,
) -> str:
    """Load a JWT signing secret, failing closed on an empty/short value.

    Raises RuntimeError at import time if the secret is missing or too short,
    so the app refuses to start rather than accepting a trivially-forgeable
    HS256 key. In non-production contexts (see _allow_insecure_secrets) a
    deterministic non-empty dev placeholder is substituted instead.

    On the PRODUCTION fail-closed path only, a first empty/short read triggers a
    bounded, backing-off re-read (see _SECRET_RETRY_* for the rationale and its
    honest limits) before raising; every other path returns immediately without
    sleeping. ``sleep`` is injectable so the retry is unit-testable without real
    wall-clock (mirrors src/audio/_http.py:post_with_retry).
    """
    value = os.getenv(env_var, "")
    if len(value) >= _MIN_SECRET_LEN:
        return value
    if _allow_insecure_secrets():
        return _DEV_PLACEHOLDER_SECRET
    # Production, and the secret read empty/short. Re-read with a bounded backoff
    # before failing closed; the dev placeholder is never substituted on this path.
    do_sleep = sleep if sleep is not None else time.sleep
    for attempt in range(1, max_attempts):
        logging.getLogger("ondoway.api").warning(
            "auth signing secret %s empty/short on read attempt %d/%d (len=%d); "
            "backing off then re-reading",
            env_var,
            attempt,
            max_attempts,
            len(value),
        )
        # `backoffs` is an injectable kwarg; guard the empty tuple so a misuse
        # degrades to zero-delay retries + the normal fail-closed raise, never an
        # IndexError. The shipped default (non-empty) path is unchanged.
        delay = backoffs[min(attempt - 1, len(backoffs) - 1)] if backoffs else 0.0
        do_sleep(delay)
        value = os.getenv(env_var, "")
        if len(value) >= _MIN_SECRET_LEN:
            return value
    raise RuntimeError(
        f"{env_var} is unset or shorter than {_MIN_SECRET_LEN} characters. "
        "Refusing to start with an empty/trivially-forgeable HS256 signing key. "
        "Set a strong secret (render.yaml uses generateValue:true), or for local "
        "development export ONDOWAY_ALLOW_INSECURE_AUTH_SECRETS=1 to use a dev placeholder."
    )


JWT_SECRET_KEY: str = _load_signing_secret("JWT_SECRET_KEY")
MAGIC_LINK_SECRET_KEY: str = _load_signing_secret("MAGIC_LINK_SECRET_KEY")
GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
MAGIC_LINK_PROVIDER: str = os.getenv("MAGIC_LINK_PROVIDER", "console")
RESEND_API_KEY: str = os.getenv("RESEND_API_KEY", "")
APPLE_TEAM_ID: str = os.getenv("APPLE_TEAM_ID", "")
BUNDLE_ID: str = "com.ondoway.app"

FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3000")
MAGIC_LINK_FROM_EMAIL: str = os.getenv("MAGIC_LINK_FROM_EMAIL", "onboarding@resend.dev")

ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
REFRESH_TOKEN_EXPIRE_DAYS: int = 14
MAGIC_LINK_EXPIRE_MINUTES: int = 15

JWT_ALGORITHM: str = "HS256"
