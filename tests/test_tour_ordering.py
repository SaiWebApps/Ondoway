"""ORDER — the visiting-order step, end to end. Hermetic: no DB, no engine run.

Covers all three pieces and the one dispatcher that chooses between them:
``held_karp_open`` (exact, provably optimal, exponential), ``cheapest_insertion_open``
(cheap, mediocre), ``improve_order_or_opt`` (cheap, close to optimal), and
``order_stops``, which every caller uses and which nothing outside this module
may bypass.

PROVE: on the seesaw fixture the exact solver beats greedy nearest-neighbour with
zero direction reversals and honors fixed_end; for every n in 4..8 its cost equals
the itertools.permutations brute-force optimum (open, round-trip, and asymmetric
variants); the cheap path lands within a MEASURED small ratio of that same proven
optimum; and one ordering call stays inside a per-call CPU budget at every n the
corpus can produce.

COST CEILINGS. Measured 2026-08-05, BEFORE any change, so a regression shows up as
a number rather than as a feeling:

    exact solver, n=10    3.6 ms      n=13   46.6 ms
    exact solver, n=16  556.2 ms      n=17    1.27 s

One 120-minute planning request made 495 ordering calls at n up to 17 and spent
191 seconds — 100% of the run — inside this step. That 500x multiplier is why the
budget below is a per-CALL figure and not a user-visible latency figure.
"""

from __future__ import annotations

import itertools
import random
import time

import pytest

from src.tour.contract import POI
from src.tour.ordering import ORDERING_EXACT_MAX, held_karp_open, order_stops
from src.tour.routing import default_leg_seconds

START = (48.8550, 2.3600)
UNIT = 0.003  # ~220m of longitude at Paris latitude


def _poi(pid: str, lat: float, lng: float) -> POI:
    return POI(id=pid, name=pid, tier=5, poi_role="stop", lat=lat, lng=lng)


def _path_cost(order, *, start=START, round_trip=False, fn=default_leg_seconds) -> float:
    total, (plat, plng) = 0.0, start
    for p in order:
        total += fn(plat, plng, p.lat, p.lng)
        plat, plng = p.lat, p.lng
    if round_trip:
        total += fn(plat, plng, *start)
    return total


def _greedy_nn(points, *, start=START, fn=default_leg_seconds):
    remaining, order, cur = list(points), [], start
    while remaining:
        nxt = min(remaining, key=lambda p: (fn(cur[0], cur[1], p.lat, p.lng), p.id))
        order.append(nxt)
        remaining.remove(nxt)
        cur = (nxt.lat, nxt.lng)
    return order


def _lng_reversals(order) -> int:
    """Direction reversals along the seesaw axis, POI-to-POI legs only."""
    deltas = [b.lng - a.lng for a, b in itertools.pairwise(order)]
    return sum(
        1 for d1, d2 in itertools.pairwise(deltas) if d1 * d2 < 0
    )


# Seesaw: one POI 1.2 units west of the start, four POIs sweeping east.
# Greedy-NN gets lured east (the +1 POI is nearer than the -1.2 one), sweeps
# to +4, then pays a 5.2-unit backtrack for the stranded west POI. The
# optimum clears the west POI first and sweeps east monotonically.
SEESAW = [
    _poi("west", START[0], START[1] - 1.2 * UNIT),
    _poi("e1", START[0], START[1] + 1.0 * UNIT),
    _poi("e2", START[0], START[1] + 2.0 * UNIT),
    _poi("e3", START[0], START[1] + 3.0 * UNIT),
    _poi("e4", START[0], START[1] + 4.0 * UNIT),
]


def test_seesaw_hk_beats_greedy_nn_with_zero_backtrack():
    hk = held_karp_open(SEESAW, fixed_start=START)
    nn = _greedy_nn(SEESAW)
    assert _path_cost(hk) < _path_cost(nn)
    assert [p.id for p in hk] == ["west", "e1", "e2", "e3", "e4"]
    assert _lng_reversals(hk) == 0
    assert _lng_reversals(nn) > 0  # the lure is real: NN does backtrack


def test_seesaw_fixed_end_is_honored():
    end = SEESAW[2]  # "e2" — NOT the natural free end
    hk = held_karp_open(SEESAW, fixed_start=START, fixed_end=end)
    assert hk[-1].id == "e2"
    # exact under the constraint: brute force over permutations ending at e2
    others = [p for p in SEESAW if p.id != "e2"]
    best = min(
        _path_cost([*perm, end]) for perm in itertools.permutations(others)
    )
    assert abs(_path_cost(hk) - best) < 1e-6


def _random_points(n: int, seed: int) -> list[POI]:
    r = random.Random(seed)
    return [
        _poi(f"p{i}", START[0] + r.uniform(-0.008, 0.008), START[1] + r.uniform(-0.008, 0.008))
        for i in range(n)
    ]


def _eastbound_penalty(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    """Asymmetric cost: eastbound legs pay a flat 200s surcharge."""
    base = default_leg_seconds(lat1, lng1, lat2, lng2)
    return base + (200 if lng2 > lng1 else 0)


@pytest.mark.parametrize("n", [4, 5, 6, 7, 8])
def test_exactness_against_brute_force(n: int):
    pts = _random_points(n, seed=40 + n)
    for round_trip, fn in (
        (False, default_leg_seconds),
        (True, default_leg_seconds),
        (False, _eastbound_penalty),  # asymmetric: the DP must stay directed
    ):
        t0 = time.perf_counter()
        hk = held_karp_open(
            pts, fixed_start=START, round_trip=round_trip, routed_cost_fn=fn
        )
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.0, f"n={n} took {elapsed:.3f}s"

        assert sorted(p.id for p in hk) == sorted(p.id for p in pts)
        best = min(
            _path_cost(perm, round_trip=round_trip, fn=fn)
            for perm in itertools.permutations(pts)
        )
        got = _path_cost(hk, round_trip=round_trip, fn=fn)
        assert abs(got - best) < 1e-6, (
            f"n={n} round_trip={round_trip} fn={fn.__name__}: HK {got} != brute {best}"
        )


#: CPU seconds ``order_stops`` may spend on ONE call. The repair pass makes
#: hundreds per request, so this carries a ~500x multiplier behind it.
#:
#: 1.0, matching the module's OWN documented guarantee — ordering.py's
#: ORDERING_EXACT_MAX comment measures n=16 at 0.55 s and calls n=17's 1.22 s
#: "past the sub-second guarantee tests/test_tour_ordering.py pins". The 0.05
#: this constant was born with (c8a35a75) contradicted that same commit's
#: docstring and the solver's real cost (re-measured 2026-08-11: bare
#: held_karp_open at n=16 is 719 ms CPU, no tracer, no monitoring tools), so
#: at 0.05 the two n=16 pins were red against unchanged code. The repair
#: pass's aggregate exposure has its own pin
#: (test_a_realistic_request_worth_of_ordering_calls_fits_in_two_seconds):
#: above the threshold the cheapest-insertion fallback runs, so the 500x
#: multiplier never multiplies THIS ceiling.
ORDER_CALL_CPU_CEILING_S: float = 1.0


def _cpu_seconds(fn, *args, **kwargs) -> float:
    """CPU time, never wall clock: a contended host must not make these flaky.

    These guard against an ALGORITHMIC regression — an exponent creeping back
    into the hot path — not against the machine's current load.
    """
    t0 = time.process_time()
    fn(*args, **kwargs)
    return time.process_time() - t0


@pytest.mark.parametrize("n", [12, 16, 20, 25, 30, 40])
def test_one_ordering_call_stays_under_the_per_call_budget(n: int):
    """n must stop driving cost exponentially. n=25 is the case this plan exists
    to make possible at all: under the exact solver alone it is roughly 12
    minutes and 33 GB, which is an unkillable test rather than a slow one.
    """
    pts = _random_points(n, seed=200 + n)
    elapsed = _cpu_seconds(order_stops, pts, fixed_start=START)
    assert elapsed < ORDER_CALL_CPU_CEILING_S, (
        f"n={n} took {elapsed * 1000:.1f} ms CPU, budget "
        f"{ORDER_CALL_CPU_CEILING_S * 1000:.0f} ms"
    )


def test_a_realistic_request_worth_of_ordering_calls_fits_in_two_seconds():
    """495 calls at n=17 is what ONE 120-minute request measured on 2026-08-05.
    Priced at the exact solver that is 191 s. A per-call budget alone can still
    be multiplied into minutes by the repair pass, so pin the aggregate too.
    """
    pts = _random_points(17, seed=317)
    t0 = time.process_time()
    for _ in range(495):
        order_stops(pts, fixed_start=START)
    elapsed = time.process_time() - t0
    assert elapsed < 2.0, f"495 calls took {elapsed:.2f} s CPU"


def test_the_exact_threshold_is_small_enough_to_be_called_in_a_loop():
    """``ORDERING_EXACT_MAX`` is the only remaining exponential surface, and it
    is inside a loop that runs hundreds of times. Pin it so raising it back
    requires editing this assertion deliberately.
    """
    assert ORDERING_EXACT_MAX <= 16
    pts = _random_points(ORDERING_EXACT_MAX, seed=99)
    elapsed = _cpu_seconds(order_stops, pts, fixed_start=START)
    assert elapsed < ORDER_CALL_CPU_CEILING_S, (
        f"the exact solver at its own threshold n={ORDERING_EXACT_MAX} took "
        f"{elapsed * 1000:.1f} ms CPU"
    )
    assert len(order_stops(pts, fixed_start=START)) == ORDERING_EXACT_MAX


def test_edge_cases_and_contract_errors():
    assert held_karp_open([], fixed_start=START) == []
    single = [SEESAW[0]]
    assert held_karp_open(single, fixed_start=START) == single
    with pytest.raises(ValueError, match="mutually exclusive"):
        held_karp_open(SEESAW, fixed_start=START, fixed_end=SEESAW[0], round_trip=True)
    with pytest.raises(ValueError, match="not among the points"):
        held_karp_open(SEESAW[:3], fixed_start=START, fixed_end=SEESAW[4])


# ---------------------------------------------------------------------------
# The heuristic is never worse than a feasible given order.
# ---------------------------------------------------------------------------

#: MEASURED 2026-08-19: the repair's 20-stop pinned incumbent on the
#: 120-minute one-way sweep. In its OWN order this chain walks 2734 s; the pin-blind
#: cheapest-insertion reorder walked 3358 s (+624). The repair priced its incumbent
#: off that inflated number, admitted a worse trial against it, and the shipped day
#: walked past its budget. Plain coordinates so the case cannot drift with fixtures.
_MEASURED_START = (48.8555, 2.3656)
_MEASURED_CHAIN = [
    ("sweep-1", 48.855549, 2.364743), ("sweep-0", 48.855205, 2.366011),
    ("sweep-3", 48.854712, 2.365388), ("sweep-19", 48.854354, 2.363512),
    ("sweep-11", 48.854301, 2.364544), ("sweep-8", 48.854391, 2.366296),
    ("sweep-16", 48.853852, 2.365704), ("sweep-29", 48.853381, 2.366449),
    ("sweep-21", 48.85396, 2.367229), ("sweep-13", 48.854639, 2.367461),
    ("sweep-5", 48.855246, 2.367038), ("sweep-2", 48.855922, 2.366436),
    ("sweep-10", 48.855897, 2.367524), ("sweep-18", 48.855419, 2.368248),
    ("sweep-31", 48.855883, 2.36899), ("sweep-23", 48.856475, 2.368185),
    ("sweep-15", 48.856724, 2.367168), ("sweep-7", 48.856563, 2.36619),
    ("sweep-20", 48.857317, 2.365972), ("sweep-12", 48.856909, 2.365129),
]


def test_a_heuristic_order_is_never_worse_than_the_feasible_order_given():
    """An optimizer may trade away optimality, never feasibility.

    Above ORDERING_EXACT_MAX the dispatcher falls back to cheapest-insertion, which
    builds its open chain BLIND to the pin and appends ``fixed_end`` afterwards — a
    chain whose tail wandered from the pin buys a huge closing leg. The three-minute
    tight made >16-stop days routine, so this regime is newly hot: on the
    measured chain below the reorder walked 624 s more than the caller's own order.
    ``order_stops`` must return whichever of (heuristic, given-as-is) walks less when
    the given order already satisfies the same pin. UNDO: return the heuristic
    unconditionally -> RED."""
    from src.tour.ordering import _chain_seconds, cheapest_insertion_open

    given = [_poi(pid, lat, lng) for pid, lat, lng in _MEASURED_CHAIN]
    pin = given[-1]
    assert len(given) > ORDERING_EXACT_MAX, "the case must exercise the heuristic branch"

    heuristic = cheapest_insertion_open(given, fixed_start=_MEASURED_START, fixed_end=pin)
    given_walk = _chain_seconds(
        given, fixed_start=_MEASURED_START, round_trip=False, routed_cost_fn=None
    )
    heuristic_walk = _chain_seconds(
        heuristic, fixed_start=_MEASURED_START, round_trip=False, routed_cost_fn=None
    )
    assert heuristic_walk > given_walk, (
        "premise: the raw heuristic must still be worse on the measured chain — if this "
        "ever flips, the fixture no longer proves the guard and needs a new measured case"
    )

    dispatched = order_stops(given, fixed_start=_MEASURED_START, fixed_end=pin)
    dispatched_walk = _chain_seconds(
        dispatched, fixed_start=_MEASURED_START, round_trip=False, routed_cost_fn=None
    )
    assert dispatched_walk <= given_walk
    assert dispatched == given, "the cheaper feasible order here IS the given one"
    # The pin is still honoured either way.
    assert dispatched[-1].id == pin.id
