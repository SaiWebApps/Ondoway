"""The magic-link ceiling must stop abuse and NEVER throttle a real person.

No limit in this codebase is keyed on the CLIENT IP, because per-IP throttling
punishes the wrong people: mobile carriers put thousands of subscribers behind a
handful of addresses, a hotel is one address, and a workbench is one address.

This ceiling uses two keys that a legitimate person structurally cannot trip —
the TARGET EMAIL and a GLOBAL total — because ``/auth/magic-link/request`` is
unauthenticated and sends a REAL email to whatever address the body names, and
Resend enforces its abuse policy by suspending the account. That failure takes
login away from everybody.

``test_a_real_person_is_never_throttled`` and
``test_one_caller_mailing_many_addresses_is_not_throttled_per_caller`` check
that the ceiling never throttles a real person. If either goes red, the ceiling
has turned into a per-person or per-IP quota.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.auth import routes as auth_routes


@pytest.fixture()
def client(monkeypatch):
    """An app whose email send always succeeds, so only the ceiling can 429."""
    monkeypatch.setattr(auth_routes, "send_magic_link", _noop_send)
    auth_routes.reset_magic_link_ceiling()
    with (
        patch("src.api.app.init_driver"),
        patch("src.api.app.close_driver"),
        TestClient(create_app()) as c,
    ):
        yield c
    auth_routes.reset_magic_link_ceiling()


async def _noop_send(email: str, token: str) -> None:
    return None


def _request(client: TestClient, email: str):
    return client.post("/api/v1/auth/magic-link/request", json={"email": email})


def test_a_real_person_is_never_throttled(client) -> None:
    """THE user-facing guarantee. Someone who did not get the first email and
    keeps pressing "send it again" must never be stopped.

    Ten consecutive requests for one's own address is already unusual human
    behaviour; the default ceiling is 30 per hour. If this ever goes red the
    ceiling has been tightened into a quota.
    """
    codes = [_request(client, "owner@ondoway.com").status_code for _ in range(10)]
    assert codes == [200] * 10, (
        f"a legitimate user was throttled after {codes.index(429) if 429 in codes else '?'} "
        f"requests for their OWN address: {codes}"
    )


def test_one_caller_mailing_many_addresses_is_not_throttled_per_caller(client) -> None:
    """There is NO per-caller key, and this proves it.

    A per-IP counter would put everyone behind one carrier or hotel in a shared
    bucket. Here the same caller mails 40 DIFFERENT
    addresses — comfortably past the 30-per-email ceiling — and every one
    succeeds, because the count is per TARGET, not per sender.

    UNDO TEST: reintroduce any per-IP or per-caller counter -> RED.
    """
    codes = [_request(client, f"person{i}@example.com").status_code for i in range(40)]
    assert set(codes) == {200}, (
        f"a single caller was throttled while mailing distinct addresses, which "
        f"means a per-caller key crept back in: {sorted(set(codes))}"
    )


def test_bombing_one_address_is_stopped(client) -> None:
    """The harm this exists for: flooding one victim's inbox.

    UNDO TEST: set MAGIC_LINK_PER_EMAIL_MAX=0 -> this goes RED and the endpoint
    is an open mailer again.
    """
    victim = "victim@example.com"
    codes = [_request(client, victim).status_code for _ in range(40)]
    assert 429 in codes, "an address was mailed 40 times with no ceiling reached"
    first_block = codes.index(429)
    assert first_block >= auth_routes._MAGIC_LINK_PER_EMAIL_MAX, (
        f"blocked at request {first_block}, before the configured ceiling of "
        f"{auth_routes._MAGIC_LINK_PER_EMAIL_MAX} — too tight"
    )
    assert codes[-1] == 429, "the ceiling stopped holding partway through the flood"


def test_the_block_tells_the_caller_when_to_retry(client) -> None:
    """A 429 with no Retry-After is a dead end for a legitimate client."""
    victim = "retry@example.com"
    resp = None
    for _ in range(auth_routes._MAGIC_LINK_PER_EMAIL_MAX + 2):
        resp = _request(client, victim)
    assert resp is not None and resp.status_code == 429
    assert resp.headers.get("Retry-After"), resp.headers
    assert int(resp.headers["Retry-After"]) > 0


def test_the_email_key_is_normalised(client) -> None:
    """Case and whitespace must not open a fresh bucket for the same inbox."""
    for _ in range(auth_routes._MAGIC_LINK_PER_EMAIL_MAX):
        _request(client, "Mixed@Example.com")
    blocked = _request(client, "  mixed@example.com  ")
    assert blocked.status_code == 429, (
        "re-casing the address bypassed the ceiling, so bombing costs an attacker "
        "one .upper() call"
    )


def test_rejected_requests_do_not_leak_counter_keys(client) -> None:
    """The keys come from an attacker-supplied body, so they must be reclaimed.

    Without this an unauthenticated caller grows the worker's RSS one permanent
    dict entry per unique address until the container is OOM-killed — a cheaper
    attack than the one the ceiling blocks.
    """
    monkey = auth_routes
    monkey.reset_magic_link_ceiling()
    for i in range(200):
        _request(client, f"ephemeral{i}@example.com")
    live = len(monkey._magic_link_by_email)
    assert live <= 200, f"counter table grew to {live} keys"
    # Expire everything, then one more request must reclaim the whole table.
    monkey._magic_link_by_email.clear()
    _request(client, "after@example.com")
    assert len(monkey._magic_link_by_email) == 1


def test_the_ceiling_can_be_switched_off(client, monkeypatch) -> None:
    """0 disables it. It is a knob: a ceiling switched off is still present,
    which is different from a ceiling removed, and the code keeps them apart.
    """
    monkeypatch.setattr(auth_routes, "_MAGIC_LINK_PER_EMAIL_MAX", 0)
    monkeypatch.setattr(auth_routes, "_MAGIC_LINK_GLOBAL_MAX", 0)
    auth_routes.reset_magic_link_ceiling()
    codes = [_request(client, "unbounded@example.com").status_code for _ in range(50)]
    assert set(codes) == {200}, sorted(set(codes))
