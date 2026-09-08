"""ONE definition of what standing at a place costs — the promise pricing seam.

Phase 3 prices a stop as a ``PromiseShape``: outside seconds, inside seconds and
queue seconds, three numbers because they behave nothing alike. Camille's
Sainte-Chapelle is 38 minutes of chapel and 28 minutes of line, and *"price the
building at a single 66 and the line becomes permanent"*
(``docs/personas/01-architecture-pilgrim.md:77-84``): it cannot shrink at opening
time, and Marcus cannot decline the wait without also declining the interior.

``visit_shape`` in ``src/tour/visit_time.py`` is the ONE pricer of that shape.
This file is half behaviour tests — each pinned to the persona or ruling that
demands it — and half source scans in the ``test_one_time_currency.py`` genre,
because the defect this phase removes is a SECOND EXPRESSION of one quantity, and
behaviour tests cannot see a duplicate: two expressions agree on every case you
thought to write and diverge on the one you did not.

The scans cover ``src`` and ``scripts`` — the engine and its plumbing — and
deliberately NOT ``tests``: a test must NAME the queue fields to build a POI
fixture, and a fixture is not a second pricer. A test that recomputes an expected
price is an independent guard, which §1.7 explicitly permits when said out loud.

These tests are hermetic: constructed POIs, a constructed lens graph, no database.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.tour.contract import POI, BeatRef
from tests.test_tour_selection import _snap

PARIS = (48.8566, 2.3522)

REPO_ROOT = Path(__file__).resolve().parent.parent
#: Where a second pricing could actually fork the engine. ``tests`` is out of
#: scope on purpose — see the module docstring.
ENGINE_ROOTS = ("src", "scripts")


def _place(
    pid: str,
    *,
    outside_min: int,
    inside_s: int | None,
    queue: str | None = None,
    peak: int = 0,
    offpeak: int = 0,
    bands: str = "",
    category: str = "",
    basis: str = "a sentence that argues for the numbers",
) -> POI:
    return POI(
        id=pid,
        name=pid,
        tier=4,
        poi_role="stop",
        lat=PARIS[0],
        lng=PARIS[1],
        areas=("Paris",),
        beat_count=3,
        typical_duration_min=outside_min,
        visit_seconds_inside=inside_s,
        visit_basis=basis,
        queue_class=queue,
        queue_minutes_peak=peak,
        queue_minutes_offpeak=offpeak,
        queue_peak_hours=bands,
        place_category=category,
    )


def _beat(poi_id: str, lens: str) -> BeatRef:
    return BeatRef(
        id=f"{poi_id}-{lens}",
        poi_id=poi_id,
        est_spoken_seconds=90,
        active_status="active",
        lenses=(lens,),
    )


#: A lens graph where `historic_arch` and `dark_history` are unrelated — neither is
#: a parent or child of the other — so a POI whose beats are tagged `historic_arch`
#: is a DIRECT hit for Camille and a MISS for Theo.
LENS_NEIGHBOURS = {
    "historic_arch": frozenset({"architecture"}),
    "dark_history": frozenset({"history"}),
}

CAMILLE = frozenset({"historic_arch"})
THEO = frozenset({"dark_history"})


def _corpus(poi: POI, *, beat_lens: str = "historic_arch"):
    return _snap(
        [poi],
        beats_by_poi={poi.id: [_beat(poi.id, beat_lens)]},
        lens_neighbors=LENS_NEIGHBOURS,
    )


def _chapelle(**overrides) -> POI:
    """Sainte-Chapelle as the queue pass would price it.

    Outside 15 min (Theo's exterior-only number, `02-dark-history-walker.md`),
    inside 38 min (Camille under the glass, `01-architecture-pilgrim.md` step 17),
    a 28-minute security line on a bright early afternoon and a 10-minute one at
    opening time (steps 16 and the "different number at opening time" clause).
    """
    fields = dict(
        outside_min=15,
        inside_s=38 * 60,
        queue="long",
        peak=28,
        offpeak=10,
        bands='[["10:30", "16:00"]]',
        category="church",
    )
    fields.update(overrides)
    return _place("sainte-chapelle", **fields)


# ---------------------------------------------------------------------------
# The shape itself — Camille's split (rule 1 + rule 2)
# ---------------------------------------------------------------------------


def test_camille_pays_the_chapel_and_the_line_as_two_separate_numbers():
    """The 28 + 38 split (`docs/personas/01-architecture-pilgrim.md:77-84`).

    *"Three numbers, not one: outside, inside, and a wait that belongs to the
    hour and the season."* At 13:22 on a bright Wednesday the line is 28
    minutes and the chapel is 38 — outside then inside at one place, both
    real. Fold them into one 66 and the wait becomes permanent.
    """
    from src.tour.visit_time import visit_shape

    poi = _chapelle()
    shape = visit_shape(poi, CAMILLE, _corpus(poi), clock_hour=13)

    assert shape.goes_inside is True
    assert shape.closed_today is False
    # The visit is the chapel's own 38 minutes, split across the door — and
    # BOTH sides of the split are real time, not bookkeeping zeros.
    assert shape.outside_seconds == 15 * 60
    assert shape.inside_seconds == 38 * 60 - 15 * 60
    assert shape.outside_seconds + shape.inside_seconds == 38 * 60
    # The line is its own number, priced at the hour she arrives.
    assert shape.queue_seconds == 28 * 60


def test_the_line_shrinks_at_opening_time_because_it_belongs_to_the_hour():
    """Step 16: *"a different number at opening time"* — the whole point of
    pricing the queue by arrival hour rather than folding it into capacity.
    09:00 sits before the 10:30-16:00 peak band, so the price is off-peak.
    """
    from src.tour.visit_time import visit_shape

    poi = _chapelle()
    at_opening = visit_shape(poi, CAMILLE, _corpus(poi), clock_hour=9)
    assert at_opening.queue_seconds == 10 * 60
    # The visit itself does not move with the hour; only the wait does.
    assert at_opening.outside_seconds + at_opening.inside_seconds == 38 * 60


def test_someone_who_stays_outside_never_pays_the_line():
    """`01-architecture-pilgrim.md:77-84`: pricing the building at a single 66
    makes the wait *"unskippable by someone who only wants the outside"*. Theo's
    interest misses this place, so he views it from the parvis — and a visitor
    who never reaches the door never stands in the line.
    """
    from src.tour.visit_time import visit_shape

    poi = _chapelle()
    shape = visit_shape(poi, THEO, _corpus(poi), clock_hour=13)

    assert shape.goes_inside is False
    assert shape.outside_seconds == 15 * 60
    assert shape.inside_seconds == 0
    assert shape.queue_seconds == 0


def test_an_undated_request_prices_the_line_at_off_peak():
    """No arrival hour means no claim about the hour, so the price is the
    MILDER claim — off-peak — never the peak the visitor might miss entirely.
    An undated plan that budgeted every line at its worst hour would refuse
    days that are actually fine.
    """
    from src.tour.visit_time import visit_shape

    poi = _chapelle()
    shape = visit_shape(poi, CAMILLE, _corpus(poi), clock_hour=None)
    assert shape.queue_seconds == 10 * 60


def test_an_unpredictable_queue_is_priced_at_its_worst_known_bound():
    """`queue_class="unpredictable"` is Marcus's museum: *"a twenty-minute
    posted wait is an unbounded risk to him — it could be forty"*
    (`docs/personas/04-layover-sprinter.md` step 7). A wait with no reliable
    band is priced at `queue_minutes_peak` whatever the hour, because the only
    honest single number for an unboundable wait is its worst known bound.
    """
    from src.tour.visit_time import visit_shape

    poi = _chapelle(queue="unpredictable", peak=20, offpeak=5)
    snapshot = _corpus(poi)
    for hour in (8, 13, None):  # off-band, in-band, undated — all the same
        shape = visit_shape(poi, CAMILLE, snapshot, clock_hour=hour)
        assert shape.queue_seconds == 20 * 60


def test_a_malformed_peak_band_fails_open_to_off_peak():
    """The `queue_peak_hours` parser mirrors the opening-hours parser's
    discipline (`_clock_exclusion_reason`): a table that does not parse is no
    peak data, never a lockout. Off-peak is the milder claim, and the
    structural bars in `tests/test_poi_queues.py` guard the data itself.
    """
    from src.tour.visit_time import visit_shape

    for bad in ("not json", '{"mon": 1}', '[["10:30"]]', '[["aa:bb", "16:00"]]', ""):
        poi = _chapelle(bands=bad)
        shape = visit_shape(poi, CAMILLE, _corpus(poi), clock_hour=13)
        assert shape.queue_seconds == 10 * 60, f"bands={bad!r} did not fail open"


def test_an_unpassed_or_audited_free_door_prices_no_queue():
    """None is no claim at all; "none" is the audited claim that there IS no
    line (src/tour/contract.py row 6.5). Both price zero seconds of queue —
    an unpassed corpus must reproduce today's behaviour exactly.
    """
    from src.tour.visit_time import visit_shape

    for queue in (None, "none"):
        poi = _chapelle(queue=queue)
        shape = visit_shape(poi, CAMILLE, _corpus(poi), clock_hour=13)
        assert shape.queue_seconds == 0
        assert shape.goes_inside is True  # no line does not mean no door


# ---------------------------------------------------------------------------
# The ceiling covers the whole stop (rule 3), the wall refuses lines (rule 4),
# a closed door demotes rather than deletes (rule 5)
# ---------------------------------------------------------------------------


def test_a_queue_the_ceiling_cannot_cover_collapses_the_stop_to_outside_only():
    """The queue is indivisible: nobody stands 28 minutes of line to spend the
    zero minutes the ceiling has left. When visit + queue exceed the party
    ceiling the visitor does not join the line — the stop survives as the
    outside view, capped by the same ceiling.
    """
    from src.tour.visit_time import visit_shape

    poi = _chapelle()
    snapshot = _corpus(poi)

    # 38 min visit + 28 min line = 66 min > a 45-minute ceiling: collapse.
    collapsed = visit_shape(
        poi, CAMILLE, snapshot, clock_hour=13, party_ceiling_seconds=45 * 60
    )
    assert collapsed.goes_inside is False
    assert collapsed.inside_seconds == 0
    assert collapsed.queue_seconds == 0
    assert collapsed.outside_seconds == 15 * 60

    # A 70-minute ceiling covers the whole 66: the full shape survives.
    covered = visit_shape(
        poi, CAMILLE, snapshot, clock_hour=13, party_ceiling_seconds=70 * 60
    )
    assert covered.goes_inside is True
    assert covered.outside_seconds + covered.inside_seconds == 38 * 60
    assert covered.queue_seconds == 28 * 60

    # The ceiling still caps the collapsed outside view: it covers the WHOLE
    # stop, not only the interior it just refused.
    tight = visit_shape(
        poi, CAMILLE, snapshot, clock_hour=13, party_ceiling_seconds=10 * 60
    )
    assert tight.goes_inside is False
    assert tight.outside_seconds == 10 * 60


@pytest.mark.parametrize("queue", ["short", "long", "unpredictable"])
def test_marcus_refuses_any_line_under_a_wall_but_keeps_the_building(queue: str):
    """*"Unpredictable stops must be refusable. A queue is not a duration, it
    is a distribution."* (`docs/personas/04-layover-sprinter.md:45-47`.) Under
    a `wall` end no stop whose duration is unboundable is ever offered (design
    §2.3), and a queue is excluded entirely (design §3.3) — ANY audited line,
    not only the unpredictable class, because being twelve minutes early costs
    Marcus nothing and being four minutes late costs him £180.

    The STOP is not deleted: the outside view survives. Wall refuses lines,
    never places — deleting the building would be the sabotage this test pins.
    """
    from src.tour.visit_time import visit_shape

    poi = _chapelle(queue=queue, peak=20, offpeak=5)
    shape = visit_shape(poi, CAMILLE, _corpus(poi), clock_hour=13, wall=True)

    assert shape.goes_inside is False
    assert shape.inside_seconds == 0
    assert shape.queue_seconds == 0
    assert shape.outside_seconds == 15 * 60, "wall must demote the stop, not delete it"


def test_a_wall_does_not_refuse_a_door_with_no_line():
    """Marcus DOES go inside Saint-Vincent-de-Paul for four minutes
    (`04-layover-sprinter.md` step 3): the wall refuses queues, not doors. A
    place with no audited line — or none audited at all — keeps its interior.
    """
    from src.tour.visit_time import visit_shape

    for queue in (None, "none"):
        poi = _chapelle(queue=queue)
        shape = visit_shape(poi, CAMILLE, _corpus(poi), clock_hour=13, wall=True)
        assert shape.goes_inside is True
        assert shape.inside_seconds > 0


def test_a_closed_door_keeps_the_exterior_minutes():
    """Delete-vs-demote (panel W1.9, dissent 1): a closed museum is still a
    building worth standing in front of. `closed_today` voids the door — the
    interior and its line are zeroed — but the outside minutes survive and the
    shape records WHY, so the promise is honest about which visit it is.
    """
    from src.tour.visit_time import visit_shape

    poi = _chapelle()
    shape = visit_shape(poi, CAMILLE, _corpus(poi), clock_hour=13, closed_today=True)

    assert shape.closed_today is True
    assert shape.goes_inside is False
    assert shape.inside_seconds == 0
    assert shape.queue_seconds == 0
    assert shape.outside_seconds == 15 * 60, "a closed door must not delete the stop"


# ---------------------------------------------------------------------------
# Rain (rule 6)
# ---------------------------------------------------------------------------


def test_rain_halves_an_uncovered_place_and_leaves_a_covered_one_alone():
    """Rain halves what standing in an UNCOVERED place is worth
    (`RAIN_DWELL_FRACTION`); an arcade, museum, church or gallery shelters the
    visit and keeps its full price. `07-rainy-tuesday.md` is the persona; the
    covered/open map lives in `visit_time.py` and nowhere else.
    """
    from src.tour.visit_time import RAIN_DWELL_FRACTION, visit_shape

    assert RAIN_DWELL_FRACTION == 0.5

    square = _place("place-des-vosges", outside_min=20, inside_s=None, category="square")
    dry = visit_shape(square, CAMILLE, _corpus(square), weather="dry")
    rain = visit_shape(square, CAMILLE, _corpus(square), weather="rain")
    assert dry.outside_seconds == 20 * 60
    assert rain.outside_seconds == 10 * 60

    # "dry" and an unstated weather are the same promise.
    unstated = visit_shape(square, CAMILLE, _corpus(square))
    assert unstated == dry

    museum = _chapelle(queue=None, category="museum")
    sheltered = visit_shape(museum, CAMILLE, _corpus(museum), weather="rain")
    assert sheltered.outside_seconds + sheltered.inside_seconds == 38 * 60


def test_a_voided_door_shelters_nobody_in_rain():
    """A covered category shelters the VISIT, and a voided door has no visit:
    a museum the clock closed (or the day's own repair stepped outside of) is
    a facade in the weather, and its outside stand pays the rain penalty like
    any square. The acceptance listen's finding: the closed Louvre was the
    rainy day's longest outdoor stand, exempt from the penalty it deserved
    because its CATEGORY said covered."""
    from src.tour.visit_time import visit_shape

    museum = _chapelle(queue=None, category="museum")
    closed_in_rain = visit_shape(
        museum, CAMILLE, _corpus(museum), weather="rain", closed_today=True
    )
    assert closed_in_rain.goes_inside is False
    assert closed_in_rain.outside_seconds == round(15 * 60 * 0.5), (
        "a clock-voided museum's outdoor stand must pay the rain penalty"
    )

    stepped_outside = visit_shape(
        museum, CAMILLE, _corpus(museum), weather="rain", exterior_only=True
    )
    assert stepped_outside.outside_seconds == round(15 * 60 * 0.5)

    # The door OPEN, the shelter holds — the existing promise, unmoved.
    open_in_rain = visit_shape(museum, CAMILLE, _corpus(museum), weather="rain")
    assert open_in_rain.outside_seconds + open_in_rain.inside_seconds == 38 * 60


def test_an_uncategorised_place_counts_as_uncovered_in_rain():
    """"" means the categoriser has not run (src/tour/contract.py row 6.7), and
    the safe direction is OPEN: never promise shelter the data cannot back
    (plan S3.3, deviation i). A wrong "covered" strands a visitor in the rain;
    a wrong "open" merely under-prices a dry arcade.
    """
    from src.tour.visit_time import visit_shape

    unknown = _place("unjudged", outside_min=20, inside_s=None, category="")
    rain = visit_shape(unknown, CAMILLE, _corpus(unknown), weather="rain")
    assert rain.outside_seconds == 10 * 60


def test_rain_never_shortens_the_queue():
    """The line takes what it takes — rain halves dwell worth, not physics. An
    uncovered place with a line halves its outside and inside seconds and pays
    the same wait.
    """
    from src.tour.visit_time import visit_shape

    garden = _place(
        "queued-garden",
        outside_min=10,
        inside_s=30 * 60,
        queue="short",
        peak=15,
        offpeak=10,
        bands='[["11:00", "17:00"]]',
        category="garden",
    )
    shape = visit_shape(garden, CAMILLE, _corpus(garden), clock_hour=9, weather="rain")

    assert shape.goes_inside is True
    assert shape.outside_seconds == 5 * 60  # halved
    assert shape.inside_seconds == 10 * 60  # halved (was 20 min beyond the door)
    assert shape.queue_seconds == 10 * 60  # NOT halved


# ---------------------------------------------------------------------------
# One definition, never two (rules 7 and 8)
# ---------------------------------------------------------------------------


def test_visit_seconds_is_the_shape_total_and_nothing_else():
    """``visit_seconds`` delegates to ``visit_shape`` at the identity defaults
    and returns ``shape_total_seconds`` — so on a queue-passed corpus the one
    total already carries the off-peak line, and there is no second pricer for
    a consumer to drift against.
    """
    from src.tour.visit_time import shape_total_seconds, visit_seconds, visit_shape

    poi = _chapelle()
    snapshot = _corpus(poi)
    shape = visit_shape(poi, CAMILLE, snapshot)

    total = visit_seconds(poi, CAMILLE, snapshot)
    assert total == shape_total_seconds(shape)
    # 38 minutes of chapel + the 10-minute off-peak line an undated request
    # prices. The wait is IN the one total — that is what "one definition"
    # buys: no gate can budget the visit while the tourist also pays the line.
    assert total == 38 * 60 + 10 * 60


def test_the_shape_total_has_one_spelling():
    """``shape_total_seconds`` is the one way to read a stop's whole cost.

    A second `outside + inside + queue` expression is a second definition of
    the total, and two expressions of one quantity agree only until one of
    them is edited. Scans non-comment engine lines for a components sum.
    """
    pattern = re.compile(
        r"(outside|inside|queue)_seconds\s*\+\s*[\w.]*(outside|inside|queue)_seconds"
    )
    allowed = {"src/tour/visit_time.py"}  # the one definition
    offenders = _scan(pattern, allowed)
    assert not offenders, (
        "these lines sum PromiseShape components by hand instead of calling "
        "shape_total_seconds:\n" + "\n".join(f"  {o}" for o in offenders)
    )


def test_the_queue_price_is_read_in_one_place():
    """Nothing outside ``visit_time.py`` may READ the queue price fields.

    The allow-list is plumbing, not pricing: the contract declares the fields,
    the loader and the upload carry them between the graph and the model, the
    data pass writes them and the export sync registers them. None of those
    decides what a line COSTS — the moment a second file turns these numbers
    into seconds, Camille's wait exists twice and shrinks in only one of them.
    """
    pattern = re.compile(r"\b(queue_minutes_peak|queue_minutes_offpeak|queue_peak_hours)\b")
    allowed = {
        "src/tour/visit_time.py",  # THE pricer
        "src/tour/contract.py",  # the field declaration (row 6.5)
        "src/tour/selection.py",  # loader plumbing: Cypher + POI construction
        "scripts/poi_queues.py",  # the data pass that writes the numbers
        "scripts/sync_poi_exports.py",  # export-sync registration
        "scripts/upload_paris.py",  # upload plumbing
    }
    offenders = _scan(pattern, allowed)
    assert not offenders, (
        "these lines read the queue price outside the one pricer and its "
        "plumbing:\n" + "\n".join(f"  {o}" for o in offenders)
    )


def test_coveredness_is_decided_in_one_place():
    """No second `place_category` -> covered map anywhere in the engine.

    The plan names the sabotage exactly: *"a second coveredness map in
    selection.py"*. A file that speaks of cover AND names `place_category` is
    making the rain decision; only `visit_time.py` may. The two categoriser
    scripts legitimately SAY "covered" — a covered arcade is what an arcade is
    — but they write the category, they do not price the weather.
    """
    cover_word = re.compile(r"\b(covered|uncovered|sheltered?)\b", re.IGNORECASE)
    allowed = {
        "src/tour/visit_time.py",  # THE map
        "scripts/poi_place_category.py",  # writes place_category; "covered passage" defines arcade
        "scripts/poi_place_judgements.py",  # its prompt describes the Galerie Vivienne
    }
    offenders = []
    for path in _engine_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if "place_category" not in text:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if cover_word.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()[:100]}")
    assert not offenders, (
        "these lines look like a second coveredness decision:\n"
        + "\n".join(f"  {o}" for o in offenders)
    )


def test_the_outside_inside_blend_is_computed_in_one_place():
    """Nothing else may blend `typical_duration_min * 60` with
    `visit_seconds_inside`: that arithmetic IS the visit price, and a second
    copy is the two-tour-algorithms defect in miniature. The capacity pass may
    restate the relationship — it is the independent guard on the DATA, which
    is the one restatement §1.7 permits.
    """
    multiply = re.compile(r"typical_duration_min\s*\*\s*60")
    allowed = {
        "src/tour/visit_time.py",  # THE blend
        "scripts/poi_visit_duration.py",  # the capacity pass: independent guard on the data
    }
    offenders = []
    for path in _engine_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if multiply.search(text) and "visit_seconds_inside" in text:
            offenders.append(rel)
    assert not offenders, (
        f"these files blend the outside minutes with the interior capacity — a "
        f"second visit pricer: {offenders}"
    )


def _engine_files() -> list[Path]:
    files: list[Path] = []
    for root in ENGINE_ROOTS:
        files.extend((REPO_ROOT / root).rglob("*.py"))
    return sorted(files)


def _scan(pattern: re.Pattern[str], allowed: set[str]) -> list[str]:
    """Non-comment engine lines matching `pattern`, outside `allowed` files.

    Comment-only lines are skipped for the same reason the currency guard
    skips them: a historical note must not keep a seam red forever, while a
    docstring describing a second pricer is wrong prose the seam must catch.
    """
    offenders: list[str] = []
    for path in _engine_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in allowed:
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if line.lstrip().startswith("#"):
                continue
            if pattern.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()[:100]}")
    return offenders
