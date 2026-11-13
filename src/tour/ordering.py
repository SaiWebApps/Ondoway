"""ORDER — visiting order for the selected anchor set.

The greedy's insertion order is a by-product of selection, not an optimum;
the ORDER step makes it exact. ``order_stops`` is the entry point every
caller uses: it orders EXACTLY (Held-Karp) while that is tractable, and by
cheapest insertion above it. No OR-Tools dependency.

Costs come from the same LegSecondsFn divisor selection uses (routed when
Valhalla is up, pace-corrected haversine otherwise) and may be asymmetric —
the DP is directed throughout.
"""

from __future__ import annotations

from .contract import POI
from .routing import LegSecondsFn, default_leg_seconds, insertion_cost_seconds

#: Most points the EXACT open-path Held-Karp solver may be handed. NOT a product
#: limit on tour length — duration is the only such bound. Purely a tractability
#: wall: the DP costs 2^n·n^2 transitions at a MEASURED ~34 ns each and holds
#: 2^n·n slots at ~40 bytes, so n=16 is 0.55 s / 76 MB, n=17 is 1.22 s / 123 MB
#: (past the sub-second guarantee tests/test_tour_ordering.py pins),
#: and n=25 is ~12 minutes / ~33 GB — an unkillable test, not a slow one.
#: Above this, order_stops falls back to cheapest insertion; it never DROPS a
#: stop the time budget earned.
ORDERING_EXACT_MAX: int = 16


def held_karp_open(
    points: list[POI] | tuple[POI, ...],
    *,
    fixed_start: tuple[float, float],
    fixed_end: POI | None = None,
    round_trip: bool = False,
    routed_cost_fn: LegSecondsFn | None = None,
) -> list[POI]:
    """Optimal visiting order for ``points`` walking out of ``fixed_start``.

    Open path by default — the walk simply stops wherever its cheapest end
    is. ``round_trip`` adds the return-to-start leg to the objective (a
    closed tour); ``fixed_end`` pins one of the points as the mandatory
    final stop (the endpoint-pull contract). The two are mutually exclusive
    because a round trip already ends at the origin.

    Exact for the full cost function, including asymmetric (directed) costs.
    Equal-cost optima tie-break on the ending POI's id, so the result is
    deterministic regardless of input order.
    """
    if fixed_end is not None and round_trip:
        raise ValueError("fixed_end and round_trip are mutually exclusive")
    pts = list(points)
    n = len(pts)
    if n <= 1:
        return pts
    if fixed_end is not None and all(p.id != fixed_end.id for p in pts):
        raise ValueError(f"fixed_end {fixed_end.id!r} is not among the points")

    cost = routed_cost_fn or default_leg_seconds
    start_lat, start_lng = fixed_start

    from_start = [cost(start_lat, start_lng, p.lat, p.lng) for p in pts]
    leg = [
        [0 if i == j else cost(a.lat, a.lng, b.lat, b.lng) for j, b in enumerate(pts)]
        for i, a in enumerate(pts)
    ]
    to_start = [cost(p.lat, p.lng, start_lat, start_lng) for p in pts] if round_trip else None

    # dp[mask][i] = cheapest path from start visiting exactly `mask`, ending
    # at points[i]; parent[mask][i] reconstructs it.
    size = 1 << n
    inf = float("inf")
    dp = [[inf] * n for _ in range(size)]
    parent = [[-1] * n for _ in range(size)]
    for i in range(n):
        dp[1 << i][i] = from_start[i]

    for mask in range(size):
        row = dp[mask]
        for i in range(n):
            base = row[i]
            if base == inf or not (mask >> i) & 1:
                continue
            leg_i = leg[i]
            for j in range(n):
                if (mask >> j) & 1:
                    continue
                nxt = mask | (1 << j)
                cand = base + leg_i[j]
                if cand < dp[nxt][j]:
                    dp[nxt][j] = cand
                    parent[nxt][j] = i

    full = size - 1
    if fixed_end is not None:
        end_indices = [i for i, p in enumerate(pts) if p.id == fixed_end.id]
    else:
        end_indices = range(n)

    def _final_cost(i: int) -> float:
        return dp[full][i] + (to_start[i] if to_start is not None else 0)

    best_end = min(end_indices, key=lambda i: (_final_cost(i), pts[i].id))

    order: list[int] = []
    mask, i = full, best_end
    while i != -1:
        order.append(i)
        nxt_i = parent[mask][i]
        mask ^= 1 << i
        i = nxt_i
    order.reverse()
    return [pts[i] for i in order]


def cheapest_insertion_open(
    points: list[POI] | tuple[POI, ...],
    *,
    fixed_start: tuple[float, float],
    fixed_end: POI | None = None,
    round_trip: bool = False,
    routed_cost_fn: LegSecondsFn | None = None,
) -> list[POI]:
    """Cheapest-insertion order for ``points`` walking out of ``fixed_start``.

    O(n^3) leg evaluations through ``routing.insertion_cost_seconds`` — the same
    marginal-cost currency the greedy already uses (selection.py's ``_insertion``).
    Not optimal; tractable at any n the corpus can produce (MEASURED 10 ms at
    n=40, 0.51 s at n=150). Contract-identical to ``held_karp_open``: same
    argument names, same ValueErrors, ``fixed_end`` pinned last, deterministic
    under input permutation (candidates are consumed in ascending id order and
    equal-cost insertions keep the earliest index, per ``insertion_cost_seconds``'
    own strict ``<``).

    EVERY stop survives. The fallback trades away the optimality guarantee, never
    a stop the time budget earned — that distinction is the whole point of having
    it, because a dropped stop is a product change and a longer walk is not.
    """
    if fixed_end is not None and round_trip:
        raise ValueError("fixed_end and round_trip are mutually exclusive")
    pts = list(points)
    if len(pts) <= 1:
        return pts
    if fixed_end is not None and all(p.id != fixed_end.id for p in pts):
        raise ValueError(f"fixed_end {fixed_end.id!r} is not among the points")
    pool = sorted(
        (p for p in pts if fixed_end is None or p.id != fixed_end.id),
        key=lambda p: p.id,
    )
    ordered: list[POI] = []
    for cand in pool:
        _extra, idx = insertion_cost_seconds(
            cand,
            ordered,
            start_lat=fixed_start[0],
            start_lng=fixed_start[1],
            round_trip=round_trip,
            leg_seconds_fn=routed_cost_fn,
        )
        ordered.insert(idx, cand)
    if fixed_end is not None:
        ordered.append(fixed_end)
    return ordered


def order_stops(
    points: list[POI] | tuple[POI, ...],
    *,
    fixed_start: tuple[float, float],
    fixed_end: POI | None = None,
    round_trip: bool = False,
    routed_cost_fn: LegSecondsFn | None = None,
) -> list[POI]:
    """Order ``points`` exactly when tractable, by cheapest insertion above it.

    ``len(points) <= ORDERING_EXACT_MAX`` -> ``held_karp_open``, byte-identical to
    every input the engine could previously produce, so no existing golden tour
    moves. Above it -> ``cheapest_insertion_open``. The tour KEEPS every stop the
    time budget earned; only the optimality guarantee is traded away.

    THIS IS THE ENTRY POINT. Calling ``held_karp_open`` directly from a planning
    path re-arms the hang this dispatcher exists to prevent: with the stop
    ceilings removed on 2026-08-04, duration alone decides n, and the exact DP
    does not return at n=25.
    """
    if fixed_end is not None and round_trip:
        raise ValueError("fixed_end and round_trip are mutually exclusive")
    if len(points) <= ORDERING_EXACT_MAX:
        return held_karp_open(
            points,
            fixed_start=fixed_start,
            fixed_end=fixed_end,
            round_trip=round_trip,
            routed_cost_fn=routed_cost_fn,
        )
    ordered = cheapest_insertion_open(
        points,
        fixed_start=fixed_start,
        fixed_end=fixed_end,
        round_trip=round_trip,
        routed_cost_fn=routed_cost_fn,
    )
    # NEVER WORSE THAN THE ORDER GIVEN. The heuristic builds its
    # open chain blind to the pin and appends ``fixed_end`` after the fact, so a
    # chain whose tail wandered away from the pin buys a huge closing leg: on the
    # 120-minute one-way sweep (21 stops — heuristic territory only since the
    # three-minute tight seats more stops) it returned an order 624 s of walking
    # WORSE than the caller's own incoming order, the repair priced its incumbent
    # off that inflated number, and the shipped day walked past its budget. An
    # optimizer may trade away optimality, never feasibility: when the caller's
    # input order already satisfies the same constraints, return whichever of the
    # two walks less. (Exact Held-Karp needs no guard — it cannot be beaten.)
    given = list(points)
    given_feasible = fixed_end is None or (given and given[-1].id == fixed_end.id)
    if given_feasible and _chain_seconds(
        given, fixed_start=fixed_start, round_trip=round_trip, routed_cost_fn=routed_cost_fn
    ) < _chain_seconds(
        ordered, fixed_start=fixed_start, round_trip=round_trip, routed_cost_fn=routed_cost_fn
    ):
        return given
    return ordered


def _chain_seconds(
    chain: list[POI],
    *,
    fixed_start: tuple[float, float],
    round_trip: bool,
    routed_cost_fn: LegSecondsFn | None,
) -> int:
    """Walking seconds of ``chain`` exactly as consumers price it: start -> each
    stop in the given order (+ the return leg on a round trip), every leg through
    the same currency the insertion heuristic itself uses."""
    cost = routed_cost_fn or default_leg_seconds
    total = 0
    prev = fixed_start
    for poi in chain:
        total += cost(prev[0], prev[1], poi.lat, poi.lng)
        prev = (poi.lat, poi.lng)
    if round_trip and chain:
        total += cost(prev[0], prev[1], fixed_start[0], fixed_start[1])
    return total
