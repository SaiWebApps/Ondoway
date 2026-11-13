"""Hermetic tests for the weather door — the forecast is fetched, never asked.

No network, no skips: open-meteo is an ``httpx.MockTransport`` speaking the
documented daily-forecast JSON shape (the ``tests/test_tour_routing_engine.py``
mould). The module under test is ``src/tour/weather.py``.
"""

from __future__ import annotations

import httpx

from src.onboard.fetch import ONBOARD_USER_AGENT
from src.tour.weather import RAIN_PROBABILITY_THRESHOLD, fetch_rain_likelihood

# Notre-Dame forecourt — a real Paris point, the routing-engine test convention.
LAT, LNG = 48.8530, 2.3499
DATE = "2026-08-15"


def _forecast_handler(probability: object):
    """A handler answering the documented open-meteo daily shape."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "latitude": LAT,
                "longitude": LNG,
                "timezone": "Europe/Paris",
                "daily": {"time": [DATE], "precipitation_probability_max": [probability]},
            },
        )

    return handler


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_rain_at_and_above_the_threshold():
    """A day at or past the named threshold plans as rain.

    The system asks the sky, not the user: this signal alone flips one request
    from dry to rain.
    """
    with _client(_forecast_handler(RAIN_PROBABILITY_THRESHOLD)) as http:
        assert fetch_rain_likelihood(DATE, LAT, LNG, client=http) == "rain"
    with _client(_forecast_handler(90)) as http:
        assert fetch_rain_likelihood(DATE, LAT, LNG, client=http) == "rain"


def test_dry_below_the_threshold():
    """Below the threshold the day plans dry — the rain test's other half.

    A confident-enough forecast is the ONLY thing that may move a day off the
    dry plan, so anything under the line is dry.
    """
    with _client(_forecast_handler(RAIN_PROBABILITY_THRESHOLD - 1)) as http:
        assert fetch_rain_likelihood(DATE, LAT, LNG, client=http) == "dry"
    with _client(_forecast_handler(0)) as http:
        assert fetch_rain_likelihood(DATE, LAT, LNG, client=http) == "dry"


def test_connect_error_fails_open_to_none_after_two_attempts():
    """A dead network yields None after exactly two tries, never an exception.

    The forecast is never asked and never BLOCKING — no signal means today's
    planning, byte-identical (two tries, then None): a rain day degrades to a
    plain day rather than to an error.
    """
    attempts: list[str] = []

    def refusing(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url.host)
        raise httpx.ConnectError("connection refused", request=request)

    with _client(refusing) as http:
        assert fetch_rain_likelihood(DATE, LAT, LNG, client=http) is None
    assert len(attempts) == 2


def test_http_500_fails_open_to_none():
    """An upstream 5xx is no signal, not an error (never blocking: the day falls
    back to the dry plan when the sky cannot be read)."""
    with _client(lambda request: httpx.Response(500, text="upstream burp")) as http:
        assert fetch_rain_likelihood(DATE, LAT, LNG, client=http) is None


def test_garbage_json_fails_open_to_none():
    """A non-JSON body is no signal, not a crash (never blocking: a dated
    request must survive a mangled forecast answer)."""
    with _client(lambda request: httpx.Response(200, text="<html>not json</html>")) as http:
        assert fetch_rain_likelihood(DATE, LAT, LNG, client=http) is None


def test_payload_missing_the_daily_block_fails_open_to_none():
    """A well-formed answer with no daily block is no signal (never blocking:
    the request degrades to the plain day, never to a KeyError)."""
    with _client(lambda request: httpx.Response(200, json={"latitude": LAT})) as http:
        assert fetch_rain_likelihood(DATE, LAT, LNG, client=http) is None


def test_null_probability_fails_open_to_none():
    """open-meteo answers a date outside its horizon with a null probability —
    a well-formed "cannot say", still no signal (never blocking: a request
    dated past the horizon plans as today)."""
    with _client(_forecast_handler(None)) as http:
        assert fetch_rain_likelihood(DATE, LAT, LNG, client=http) is None


def test_request_carries_the_descriptive_ua_and_documented_params():
    """Pin the outgoing wire: the descriptive User-Agent and the documented
    open-meteo query — daily max precipitation probability, timezone=auto,
    start=end=the one day.

    The UA is the shared ingest constant (anonymous clients get refused, so
    there is no second HTTP door without a UA). Recording mould: the routing
    engine's ``test_requests_use_documented_wire_format``.
    """
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _forecast_handler(75)(request)

    with _client(recording) as http:
        assert fetch_rain_likelihood(DATE, LAT, LNG, client=http) == "rain"

    (request,) = seen  # one successful fetch = exactly one request
    assert request.headers["User-Agent"] == ONBOARD_USER_AGENT
    assert request.url.scheme == "https"
    assert request.url.host == "api.open-meteo.com"
    assert request.url.path == "/v1/forecast"
    params = dict(request.url.params)
    assert float(params["latitude"]) == LAT
    assert float(params["longitude"]) == LNG
    assert params["daily"] == "precipitation_probability_max"
    assert params["timezone"] == "auto"
    assert params["start_date"] == DATE
    assert params["end_date"] == DATE
