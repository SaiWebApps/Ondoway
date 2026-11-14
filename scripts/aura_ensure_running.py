"""Wake a PAUSED Aura instance and BLOCK until the database answers.

The free prod Aura auto-pauses after ~3 days; while paused its DNS is withdrawn
(connections fail with NXDOMAIN). Every `-cloud` make target runs this FIRST so a
paused instance is resumed and awaited instead of being mistaken for a dead one
(the 2026-07-12 incident). Unlike the
request-path ResumeCoordinator (fire-and-forget), a deploy MUST wait, so this
polls to completion.

READINESS IS THE DATABASE ANSWERING, never a status string. Aura reports two
things independently: a CONTROL-PLANE lifecycle status (`GET /v1/instances/{id}`,
what the console's instance list shows) and the Bolt endpoint the data actually
travels on. They disagree by design — an instance under a rolling `updating`
keeps serving queries throughout, which is why the console can show `UPDATING`
and `connected` at the same moment. Measured 2026-09-03: this script polled a
`updating` instance to its 300 s timeout and failed the cloud shard while
`make db-parity TARGET=cloud` passed against that same instance completely.
`updating` is not even in the status vocabulary ``src.aura_resume`` documents,
so it was neither accepted nor terminal — just unrecognised, and fatal.

So the status is consulted for exactly one thing: deciding whether to fire a
resume and whether it is still worth waiting. What ENDS the wait is a real
read-only query coming back, which is the same evidence `healthz` reports. A
probe trusts only evidence it observed itself, never another system's report.

Safe NO-OP when AURA_CLIENT_ID/SECRET are unset (local dev / CI / a self-hosted
graph): prints a note and exits 0, so it never blocks a non-Aura target.
"""

from __future__ import annotations

import sys
import time

from src.aura_resume import AuraClient, AuraConfig, AuraResumeError

_TIMEOUT_S = 300.0
_POLL_S = 10.0
_TERMINAL_BAD = {"destroying", "loading"}  # states a resume can't fix


def database_answers() -> bool:
    """True when the configured database really answers a read-only query.

    The one piece of evidence this module accepts. Deliberately the same shape
    `healthz` uses (`RETURN 1` through the configured driver) rather than
    `verify_connectivity`, so what is proven is a query completing rather than a
    socket opening. Any failure — paused instance, NXDOMAIN, auth, timeout — is
    the answer "no", because every one of them means a `-cloud` target cannot run
    yet; the caller's poll loop decides how long to keep asking.
    """
    from src.connection import create_driver, get_database

    try:
        driver = create_driver(verify=False)
        try:
            with driver.session(database=get_database()) as session:
                session.run("RETURN 1 AS ok").single()
            return True
        finally:
            driver.close()
    except Exception:
        return False


def ensure_running(
    client: AuraClient,
    *,
    answers=database_answers,
    timeout: float = _TIMEOUT_S,
    poll: float = _POLL_S,
    sleep=time.sleep,
    clock=time.monotonic,
    echo=print,
) -> str:
    """Wait until the database answers, resuming the instance if it is paused.

    Returns ``"serving"``; raises AuraResumeError/TimeoutError otherwise. Pure
    over the injected client/probe/clock/sleep so it is unit-testable without a
    live graph. The return value names the evidence: the database answered — not
    that a control-plane field held a particular string.
    """
    if answers():
        return "serving"

    status = client.get_status()
    echo(f"[aura] database not answering; control-plane status = {status}")
    if status == "paused":
        client.resume()
        echo("[aura] paused → resume initiated; waiting for the database...")

    deadline = clock() + timeout
    while clock() < deadline:
        sleep(poll)
        if answers():
            return "serving"
        status = client.get_status()
        echo(f"[aura] still waiting; status = {status}")
        if status in _TERMINAL_BAD:
            raise AuraResumeError(f"instance in unrecoverable state {status!r}")
    raise TimeoutError(
        f"Aura database did not answer within {timeout:.0f}s (last status {status!r})"
    )


def main(argv: list[str] | None = None) -> int:
    cfg = AuraConfig.from_env()
    if cfg is None:
        print("[aura] AURA_CLIENT_ID/SECRET unset — resume disabled, skipping (no-op).")
        return 0
    try:
        ensure_running(AuraClient(cfg))
        print("[aura] ✓ database answering.")
        return 0
    except (AuraResumeError, TimeoutError) as exc:
        print(f"[aura] ensure-running FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
