"""Spend and authorization guards on ``src/api/routes/trips.py``.

1. The non-live suite's product authoring factory hands back the offline executor,
   so no test in it can bill a provider.
2. Anthropic provider failures (429/529/timeout) on the compose path map to a 503
   with Retry-After or a 502, never a raw 500, so a client can tell a transient
   upstream throttle from a server bug and back off.
3. GET /trips, POST /trips/generate and POST /trips/{id}/compose require a signed-in
   caller, and a foreign profile or trip is a 404 that never confirms it exists.

Everything here is hermetic and $0: no test reaches the network.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.auth.tokens import create_access_token
from src.api.dependencies import (
    get_driver,
    get_premium_compose_executor,
    get_session,
)
from src.api.routes import trips as trips_route
from src.tour.premium_tour import (
    AnthropicPremiumExecutor,
    OfflinePremiumExecutor,
)

# Reuse the proven hermetic corpus fakes rather than re-deriving them.
from tests.test_trip_preview_contract import (
    START,
    _FakeDriver,
    _green_cluster_records,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _RaisingPremiumExecutor:
    """Explicit non-billing stub whose physical call raises an exception."""

    cost_bearing = False
    provider_name = "offline"

    def __init__(self, exc: BaseException):
        self.exc = exc

    def execute(self, _unit):
        raise self.exc


class _FakeAuthSession:
    """Neo4j session double for the auth/ownership Cypher only.

    ``owns`` False makes the ownership MATCH miss, which is the foreign-profile /
    foreign-trip case the IDOR fix must turn into a 404.
    """

    def __init__(self, user_id: str, *, owns: bool):
        self.user_id = user_id
        self.owns = owns

    def run(self, query: str, **params):
        if "HAS_PROFILE" in query or "IS_CAPTAIN_OF" in query:
            return _Single({"id": params.get("pid") or params.get("tid")} if self.owns else None)
        if "MATCH (u:User" in query:
            return _Single(
                {"u": {"id": self.user_id, "email": "a@example.com", "created_at": None}}
            )
        raise AssertionError(f"unexpected query in the auth path:\n{query}")

    def close(self) -> None:
        return None


class _Single:
    def __init__(self, record):
        self._record = record

    def single(self):
        return self._record


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------




@pytest.fixture()
def auth_client():
    """(user_id, owns) -> TestClient whose session double answers the auth Cypher."""

    def _make(user_id: str, *, owns: bool) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_session] = lambda: _FakeAuthSession(user_id, owns=owns)
        app.dependency_overrides[get_driver] = lambda: _FakeDriver(_green_cluster_records())
        # Compose authors through the premium executor, so THAT is the boundary a
        # foreign caller must never reach. It raises rather than
        # returning a stub: if the ownership check ever stops short-circuiting, the
        # test fails loudly instead of quietly composing a victim's trip.
        app.dependency_overrides[get_premium_compose_executor] = lambda: _RaisingPremiumExecutor(
            AssertionError("an unauthorized caller reached the authoring provider")
        )
        return TestClient(app, raise_server_exceptions=False)

    return _make


# ---------------------------------------------------------------------------
# The non-live suite never bills a provider
# ---------------------------------------------------------------------------


def test_product_authoring_factory_is_offline_in_the_non_live_suite():
    """AC-7, third clause: the conftest money-guard actually arms this boundary.

    Every paid call either surface can make now goes through ONE product factory,
    ``get_premium_compose_executor()``. Nothing else in the non-live suite asserts
    that the factory is neutered, and the only executable money-guard pin that used
    to cover the old whole-tour compose client is deleted later in this ledger — so
    without this test the hermetic suite could start billing real Opus and every
    other test would stay green, because they all inject their own doubles and never
    touch the product path.

    Hermetic and $0: it constructs the product executor and asserts what it IS,
    without executing it.

    UNDO: delete the PREMIUM authoring money-guard arm in tests/conftest.py (the
    ``AnthropicPremiumExecutor`` monkeypatch) -> the factory hands back the real
    billing executor and this goes RED.
    """
    executor = get_premium_compose_executor()

    assert isinstance(executor, OfflinePremiumExecutor), (
        "the product authoring factory built a real provider client in the "
        f"non-live suite: {type(executor).__module__}.{type(executor).__qualname__}"
    )
    assert executor.cost_bearing is False
    # (A _is_cost_bearing() assertion sat here so the deleted rate limiter agreed
    # the offline executor was free. Both are gone; `cost_bearing` above is still
    # the flag the offline swap sets, so the guarantee is unchanged.)
    # The swap is what makes it free — the class the factory names is billable.
    assert AnthropicPremiumExecutor.cost_bearing is True


# ---------------------------------------------------------------------------
# Provider failures map to 503/502, never a raw 500
# ---------------------------------------------------------------------------


def _http_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def test_provider_throttle_is_503_with_retry_after():
    """anthropic.RateLimitError must not surface as a 500.

    ``_upstream_provider_errors`` wraps every provider call on the compose path.
    UNDO: let the throttle through the wrapper -> RED (the raw SDK error escapes).
    """
    anthropic = pytest.importorskip("anthropic")
    exc = anthropic.RateLimitError(
        "throttled",
        response=httpx.Response(429, request=_http_request()),
        body=None,
    )
    with pytest.raises(HTTPException) as raised, trips_route._upstream_provider_errors():
        raise exc
    assert raised.value.status_code == 503, raised.value.status_code
    assert raised.value.headers == {"Retry-After": "30"}


def test_provider_timeout_is_502():
    """anthropic.APITimeoutError (an APIError subclass) -> a clean 502.

    UNDO: drop APIError from the wrapper's mapping -> RED (the raw SDK error escapes).
    """
    anthropic = pytest.importorskip("anthropic")
    exc = anthropic.APITimeoutError(request=_http_request())
    with pytest.raises(HTTPException) as raised, trips_route._upstream_provider_errors():
        raise exc
    assert raised.value.status_code == 502, raised.value.status_code


# ---------------------------------------------------------------------------
# Authentication + ownership on the user-facing trip surface
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/api/v1/trips?profile_id=victim-profile", None),
        (
            "post",
            "/api/v1/trips/victim-trip/compose",
            {"route_id": "victim-trip-opt1"},
        ),
        (
            "post",
            "/api/v1/trips/generate",
            {
                "profile_id": "victim-profile",
                "start_date": "2026-08-01",
                "end_date": "2026-08-01",
                "center_lat": START[0],
                "center_lng": START[1],
            },
        ),
    ],
)
def test_trip_routes_reject_an_unauthenticated_caller(auth_client, method, path, payload):
    """No bearer token -> no access to anyone's trips.

    UNDO: remove ``Depends(get_current_user)`` from the route -> RED (the call
    succeeds or fails on something other than authentication).
    """
    client = auth_client("attacker", owns=False)
    resp = getattr(client, method)(path, json=payload) if payload else getattr(client, method)(path)
    assert resp.status_code in (401, 403), (
        f"{method.upper()} {path} answered {resp.status_code} without a token — "
        "the endpoint is unauthenticated"
    )


def test_listing_another_profiles_trips_is_404_not_a_leak(auth_client):
    """Profile A's token must not read profile B's trips — and must not confirm
    that B exists (404, never 403).

    UNDO: revert the ownership MATCH to the existence-only
    ``MATCH (p:Profile {id: $pid})`` -> RED (the trips come back 200).
    """
    client = auth_client("user-a", owns=False)
    token = create_access_token("user-a", "a@example.com")
    resp = client.get(
        "/api/v1/trips?profile_id=victim-profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404, f"foreign profile leaked with {resp.status_code}"


def test_composing_another_users_trip_is_404_and_never_mutates(auth_client):
    """/compose replaces stops and burns the one-shot compose budget, so a
    foreign trip must be refused BEFORE any write or any compose call.

    UNDO: remove the IS_CAPTAIN_OF ownership MATCH from compose_trip -> RED (the
    request proceeds and the injected compose client's AssertionError fires).
    """
    client = auth_client("user-a", owns=False)
    token = create_access_token("user-a", "a@example.com")
    resp = client.post(
        "/api/v1/trips/victim-trip/compose",
        json={"route_id": "victim-trip-opt1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404, f"foreign trip was composable ({resp.status_code})"
