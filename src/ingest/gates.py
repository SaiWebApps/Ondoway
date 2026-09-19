"""Item-level correctness gates for a decomposed claim.

Docs/ingestion/rebuild-spec.md and CONTEXT.md pin the shapes below.

Step 2 lands the whitespace and copying gates: `normalize_ws`
(the one whitespace fold every gate in this module uses), `span_in_unit`
(a claim's cited span must exist in the unit's own text — exact except for
whitespace, because case, punctuation and dashes prove the span was quoted
from the passage rather than paraphrased), and `lift` (text must not copy
`run_length` or more consecutive unit words outside an attributed quotation or
a proper name — since the owner ruling of 2026-09-13 a NARRATION gate only).

Step 3 landed `leak` (a claim must not carry guidebook apparatus
or a phone/URL/domain listing — `APPARATUS_RE`/`LISTING_RE`, carried
verbatim from the quarantined `_APPARATUS`/`_LISTING` in
`_to_be_deleted/scripts/reauthor_cleanroom.py`), `self_contained` (a claim
must not open on a dangling pronoun or demonstrative — `ANAPHOR_RE`,
carried verbatim from that file's `_ANAPHOR`, including its
demonstrative-before-a-verb lookahead so "That is the oldest..." is not
flagged), `default_kind` (maps the LLM's own `ambiguous` kind to `state`
and passes `event`/`state`/`belief` through, refusing everything else), and
`claim_gates` (the per-claim aggregate: span, leak, self_contained, in that
order, one reason per failing gate, never short-circuiting; lift left it on
2026-09-13).

`LIFT_RUN_LENGTH` (8) is the validator's FLOOR, the same number as
`scripts.verbatim.VERBATIM_RUN_BLOCK`; the pipeline no longer has one copying
threshold (decisions.spec_extensions #3 is superseded by the owner ruling of
2026-09-13): claims are not tested, the P4 narration gate passes seven
(`narrate.NARRATION_LIFT_GATE_RUN`), proper names are exempt.

`lift` measures a claim against the WHOLE unit text, not just its own
cited span: the span sits inside the unit, so a unit-wide measure is at
least as strict, and `scripts.verbatim.run_outside_quotation` already
exempts a quotation that names who said it — quoting a named speaker who
also appears in the source book is not the same failure as an unmarked
lift.

Slice 5 lands the two narration-only gates: `provenance_leak` (a narration
must never reveal it was read from a book — the fixed phrases `the book`,
`described here`, `the guide`, `the author`, plus whatever publisher names
the caller passes from the source's manifest; CONTEXT.md "Provenance
leak") and `framing` (`imagine` and `envision` in any form, `picture` only
as the framing verb with an object — the noun is ordinary art vocabulary;
framing belongs to the tour engine, never to a beat). Both return every
match, so a re-ask can quote each one back.

Step 9 lands `resolve_place` (exact casefold+whitespace match against a
POI's `name` and `name_variations`, a leading "the" and full stops ignored,
else flagged `new_poi`).

Step 10 landed the P2 set gates: `every_claim_once` and
`known_claim_ids` (every claim assigned to exactly one story, and every
assigned id actually known), `claim_floor` (`STRUCTURAL_BEAT_TYPES` need
only one claim, everything else needs two, and zero never passes),
`lenses_valid`, `beat_type_valid`, `unique_slugs`, and `story_problems` —
the aggregate a caller runs over a whole story partition. The atomic
functions return a reason of the form `"<code>: <detail>"`; `story_problems`
threads each story's own slug into that code (`"<code>:<slug>: <detail>"`)
so a caller sees which story a per-story defect belongs to, while
`every_claim_once`/`known_claim_ids`/`unique_slugs` already name the claim
id or slug the defect is about.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from scripts.verbatim import run_outside_quotation
from src.ingest.model import BEAT_TYPE_VALUES, STRUCTURAL_BEAT_TYPES
from src.ingest.model import slug as story_slug_of
from src.schema.definitions import TAGGABLE_LENSES

#: A shared run of this many words, outside an attributed quotation, is a
#: lift. Same number as `scripts.verbatim.VERBATIM_RUN_BLOCK` — see the
#: module docstring for why it is not spelled as that name here.
LIFT_RUN_LENGTH: int = 8

_WHITESPACE_RUN = re.compile(r"\s+")


def normalize_ws(text: str) -> str:
    """Collapse every run of whitespace to a single space; trim the ends.

    The only whitespace fold in this module. Case, punctuation and dashes
    are never touched — a gate that folded those too would stop being a
    verbatim check.
    """
    return _WHITESPACE_RUN.sub(" ", text).strip()


def span_in_unit(span: str, unit_text: str) -> str | None:
    """A claim's cited span must exist in the unit's own text.

    Whitespace-normalized on both sides (a span reflowed across a line
    break is still the same span) but exact otherwise: case, punctuation
    and dashes must match the unit byte-for-byte, because this gate proves
    the STORED span is the passage's own text, not a paraphrase of it. An
    author's citation that only straightened typography is first swapped for
    the passage's text by `ground_span` (decompose, restate), so passing here
    does not prove the author typed it that way.

    Returns None when the (normalized) span is found in the (normalized)
    unit text; otherwise a reason starting with `span_not_in_unit` that
    names the span, so a caller can see what was claimed to be there.
    """
    normalized_span = normalize_ws(span)
    if not normalized_span:
        return "span_not_in_unit: span is empty"
    if normalized_span in normalize_ws(unit_text):
        return None
    return f"span_not_in_unit: {span!r} is not in the unit text"


#: Typographic characters a model straightens when it copies a span, and
#: their ASCII forms. Folded ONLY while locating a citation (locate_span, for a
#: judge's omission or an author's claim via ground_span); the strict gate
#: (span_in_unit) never folds them.
_TYPOGRAPHY_FOLD = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
    }
)


def _fold_for_locating(text: str) -> tuple[str, list[int]]:
    """Fold typography to ASCII and collapse each whitespace run to one
    space; return the folded text and, per folded character, the index of
    the original character it came from (a run maps to its first).

    A run that follows a word's hyphen and precedes a letter or digit is
    folded away: the passage breaks "mural-" across a line where a model
    writes "mural-lined" (slice 9 job 1 re-run, 2026-09-13)."""
    out: list[str] = []
    origin: list[int] = []
    in_run = False
    for index, char in enumerate(text):
        if char.isspace():
            if not in_run:
                in_run = True
                rest = text[index:].lstrip()
                if (
                    out[-2:-1]
                    and out[-1] == "-"
                    and out[-2].isalnum()
                    and rest[:1].isalnum()
                ):
                    continue
                out.append(" ")
                origin.append(index)
            continue
        in_run = False
        out.append(char.translate(_TYPOGRAPHY_FOLD))
        origin.append(index)
    return "".join(out), origin


def locate_span(span: str, unit_text: str) -> str | None:
    """Find a judge's or author's citation (see `ground_span`) in the unit
    even when the model straightened the passage's quotes or dashes, and
    return the passage's OWN text for it — whitespace-normalized exactly as
    `span_in_unit` compares, typography intact — so what a caller stores is
    still verbatim. None when no fold
    finds it (a paraphrase, or an empty citation). Exists because the live
    omission judge of 2026-09-12 cited "Guggenheim's" for the passage's
    "Guggenheim\u2019s" and two real findings were discarded for it.
    """
    folded_span, _ = _fold_for_locating(span)
    folded_span = folded_span.strip()
    if not folded_span:
        return None
    folded_unit, origin = _fold_for_locating(unit_text)
    start = folded_unit.find(folded_span)
    if start < 0:
        return None
    last = start + len(folded_span) - 1
    return normalize_ws(unit_text[origin[start] : origin[last] + 1])


def ground_span(span: str, unit_text: str) -> str:
    """The span to STORE for an author's citation: the citation itself when the
    strict gate already finds it, else the passage's own text wherever
    `locate_span` finds it, else the citation unchanged — so `span_in_unit`
    still refuses a paraphrase in the author's words, and a citation that only
    straightened the passage's typography is stored verbatim instead of
    dropped. Slice 9's job 1 re-run (2026-09-13) dropped 34 author claims as
    span_not_in_unit; this fold recovers 33 of them (one had lost its
    apostrophe to a line break and stays refused).
    """
    if span_in_unit(span, unit_text) is None:
        return span
    located = locate_span(span, unit_text)
    return span if located is None else located


#: Joining words a proper name may carry without breaking its capitalized
#: run: "Museum of Non-Objective Painting", "Sant Ambroeus and Irving Farm
#: Roasters", "Portrait of Adele Bloch-Bauer I".
PROPER_NAME_SMALL_WORDS: frozenset[str] = frozenset(
    {"a", "an", "the", "of", "and", "for", "on", "at", "in", "de", "du", "la",
     "le", "von", "der", "di", "y", "i", "ii", "iii", "iv", "v"}
)

#: Placeholder a masked name is replaced with: a non-word character, so the
#: masked words can never join a run on either side.
_MASK = "␀"


def _cased_run(run_text: str, text: str) -> re.Match[str] | None:
    """The run as it appears in `text`, case and punctuation intact."""
    words = run_text.split()
    if not words:
        return None
    pattern = r"\W*".join(re.escape(w) for w in words)
    return re.search(pattern, text, re.I)


def is_proper_name(cased: str) -> bool:
    """True when every word of the run, small joining words and numbers
    set aside, is capitalized — a name or a title, which has one wording
    and is a fact, not the source's expression."""
    tokens = [t for t in re.findall(r"[^\W_]+(?:'[^\W_]+)?", cased) if t]
    heads = [t for t in tokens if t.lower() not in PROPER_NAME_SMALL_WORDS and not t[0].isdigit()]
    return len(heads) >= 2 and all(t[0].isupper() for t in heads)


def lift(text: str, unit_text: str, run_length: int = LIFT_RUN_LENGTH) -> str | None:
    """Text must not copy `run_length`+ consecutive unit words (the
    narration gate passes NARRATION_LIFT_GATE_RUN; the validator's floor
    is LIFT_RUN_LENGTH). Since the owner ruling of 2026-09-13 this is a
    NARRATION gate only — `claim_gates` no longer calls it.

    A shared run that is a PROPER NAME (`is_proper_name`: "Museum of
    Non-Objective Painting", "Portrait of Adele Bloch-Bauer I") is exempt,
    as an attributed quotation is: a name is a fact with one wording. The
    name is masked and the measure runs again, so a lift elsewhere in the
    text is still found.

    Measured against the whole unit (not just the cited span) with
    `run_outside_quotation`, which already exempts a quotation that names
    who said it. Returns None when the longest shared run outside
    quotation is below `run_length`; otherwise a reason starting with
    `lift` that carries the copied words.
    """
    masked = text
    for _ in range(16):
        result = run_outside_quotation(masked, unit_text)
        if result["length"] < run_length:
            return None
        cased = _cased_run(result["text"], masked)
        if cased is None or not is_proper_name(cased.group(0)):
            return f"lift: {result['text']!r} copied verbatim from the unit"
        masked = masked[: cased.start()] + _MASK * len(cased.group(0)) + masked[cased.end() :]
    return None


#: Claims about the BOOK rather than the place — page numbers, numbered walk
#: steps, "the author recommends". Carried verbatim from the quarantined
#: `_APPARATUS` in `_to_be_deleted/scripts/reauthor_cleanroom.py`: a
#: guidebook passage carries its own apparatus, and a claim that repeats it
#: is stating a fact about the book, not the place.
APPARATUS_RE = re.compile(
    r"\b(guidebook|guide ?book|this walk|the walk|walking tour|itinerary"
    r"|step \d+|point [A-Z]|page \d+|pp?\. \d+|the author (advises|suggests|recommends|says)"
    r"|numbered (entry|entries|point|step))\b",
    re.I,
)

#: A listing a listener cannot use — a phone number, a URL, a bare domain.
#: Carried verbatim from the quarantined `_LISTING`.
LISTING_RE = re.compile(
    r"(?:\b\d{3}[-. ]\d{3}[-. ]?\d{4}\b|\b(?:www\.|https?://)\S+"
    r"|\b[\w-]+\.(?:com|org|net|edu|us|fr|gov)\b)",
    re.I,
)

#: Openings that point at a neighbouring claim: a bare pronoun, or a
#: demonstrative/"Later"/"Also"/"The same" that needs the claim before it.
#: Carried verbatim from the quarantined `_ANAPHOR` — including its
#: lookahead, which treats a demonstrative followed by is/are/was/were
#: ("That is the oldest...") as a complete sentence, not a dangling
#: reference.
ANAPHOR_RE = re.compile(
    r"^\s*(?:(?:He|She|It|They|Him|Her|Them|His|Their|Its)\b"
    r"|(?:That|This|These|Those)\s+(?!is\b|are\b|was\b|were\b)\w"
    r"|(?:Later|Also|Afterwards|Subsequently|The same)\b)"
)

#: What the P1 schema's `kind` enum accepts from the model, including the
#: escape hatch `ambiguous` that `default_kind` resolves.
KIND_RESPONSE_VALUES: tuple[str, str, str, str] = ("event", "state", "belief", "ambiguous")


def leak(text: str) -> str | None:
    """A claim must not carry guidebook apparatus or a phone/URL listing.

    Returns None when neither `APPARATUS_RE` nor `LISTING_RE` matches;
    otherwise a reason starting with `leak` that names the matched text.
    """
    match = APPARATUS_RE.search(text) or LISTING_RE.search(text)
    if match is None:
        return None
    return (
        f"leak: {match.group(0)!r} is guidebook apparatus or a listing, "
        "not content about the place"
    )


#: Phrases that betray a narration was read from a book rather than
#: told at the place. Publisher names are the caller's to pass — they
#: live in each source's manifest, never in this module.
PROVENANCE_LEAK_RE = re.compile(
    r"\b(the book|this book|described here|the guide|this guide|the author)\b",
    re.I,
)

#: The framing verbs a beat may never use (Docs/ingestion/rebuild-spec.md
#: §3 P4). `picture` is caught only as the verb with its framing object,
#: so "the picture hangs" and "Wright pictured" pass.
FRAMING_RE = re.compile(
    r"\b(?:imagine|imagining|imagined|envision|envisioning|envisioned"
    r"|picture (?:yourself|yourselves|this|that|it))\b",
    re.I,
)


def provenance_leak(text: str, publishers: Sequence[str] = ()) -> list[str]:
    """Every phrase in `text` that reveals it was read from a book.

    Matches `PROVENANCE_LEAK_RE` plus each name in `publishers` as a
    whole-word, case-insensitive phrase, in text order. Returns the
    matched text as written, [] for a clean narration.
    """
    patterns = [PROVENANCE_LEAK_RE]
    if publishers:
        alternatives = "|".join(re.escape(name) for name in publishers if name.strip())
        if alternatives:
            patterns.append(re.compile(rf"\b(?:{alternatives})\b", re.I))
    hits = [
        (match.start(), match.group(0)) for pattern in patterns for match in pattern.finditer(text)
    ]
    found = sorted(hits, key=lambda hit: hit[0])
    return [matched for _start, matched in found]


def framing(text: str) -> list[str]:
    """Every framing verb in `text` (`FRAMING_RE`), as written, in text order."""
    return [match.group(0) for match in FRAMING_RE.finditer(text)]


def self_contained(text: str) -> str | None:
    """A claim must not open on a dangling pronoun or demonstrative.

    Returns None when `ANAPHOR_RE` does not match the start of `text`;
    otherwise a reason starting with `not_self_contained`.
    """
    if ANAPHOR_RE.match(text) is None:
        return None
    return (
        f"not_self_contained: {text!r} opens on a pronoun or demonstrative "
        "that needs its neighbor"
    )


def default_kind(kind: str | None) -> str | None:
    """Resolve the P1 schema's `kind` enum to the three real kinds.

    `ambiguous` maps to `state` (the LLM's own escape hatch); `event`,
    `state` and `belief` pass through unchanged. Anything else — `fact`,
    `observation`, an empty string, wrong case, or None — returns None,
    since only the four `KIND_RESPONSE_VALUES` are ever answered here.
    """
    if kind == "ambiguous":
        return "state"
    if kind in ("event", "state", "belief"):
        return kind
    return None


def claim_gates(text: str, span: str, unit_text: str) -> list[str]:
    """Run every item-level gate against one claim; never short-circuit.

    Returns the reasons for every failing gate, in the fixed order span,
    leak, self_contained (OWNER RULING 2026-09-13: lift is a NARRATION gate
    only — a claim is provenance, never spoken, and on the first real job the
    claim-level test deleted 139 facts) — so a caller sees every defect in one
    pass rather than re-asking once per gate. Returns [] for a clean claim.
    """
    reasons = []
    for gate_reason in (
        span_in_unit(span, unit_text),
        leak(text),
        self_contained(text),
    ):
        if gate_reason is not None:
            reasons.append(gate_reason)
    return reasons


#: Words too common in place names to identify one (slice 10: candidate
#: places for P2). A POI is a candidate only through a word NOT in here.
_GENERIC_PLACE_WORDS: frozenset[str] = frozenset({
    "museum", "museums", "park", "new", "york", "city", "street", "avenue", "center", "centre",
    "house", "building", "church", "hall", "library", "gallery", "galleries", "collection",
    "national", "design", "art", "arts", "society", "square", "garden", "gardens", "memorial",
    "institute", "theater", "theatre", "university", "bridge", "station", "island", "upper",
    "lower", "east", "west", "north", "south", "fifth",
})


def _name_words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def candidate_places(pois: Sequence[Mapping[str, object]], unit_text: str) -> list[str]:
    """The canonical names of every POI the passage names — a POI with a
    name or name variation whose distinctive words (four letters or more,
    not a generic place word; hyphens split, so "Cooper-Hewitt" finds
    "Cooper Hewitt") ALL occur in the passage. All, not any: one shared word
    would offer "Museum of Jewish Heritage" for a Jewish Museum passage, and
    P2 picks from this list. P2 is shown these so a story's place is written
    the way `resolve_place` resolves it."""
    text_words = _name_words(unit_text)
    found: list[str] = []
    for poi in pois:
        names = [str(poi["name"]), *(str(v) for v in poi.get("name_variations", []) or [])]
        for name in names:
            distinctive = {
                word
                for word in _name_words(name)
                if len(word) >= 4 and word not in _GENERIC_PLACE_WORDS
            }
            if distinctive and distinctive <= text_words:
                if poi["name"] not in found:
                    found.append(str(poi["name"]))
                break
    return found


def resolve_place(place: str, pois: Sequence[Mapping[str, object]]) -> tuple[str, bool]:
    """Resolve a claim's cited place to a POI's canonical name.

    Exact match only — casefold + whitespace-normalized, a leading definite
    article and full stops ignored on both sides (slice 9's proof chunk held 12
    of 20 stories as new places over "The Frick Collection" vs "Frick
    Collection", and run 3 six Guggenheim stories over "R" vs "R.")
    — against each POI's `name` and `name_variations`. No prefix or fuzzy
    matching (PO Risk 1: 'Guggenheim' alone must not match 'Solomon R.
    Guggenheim Museum').

    Returns `(poi['name'], False)` on a match against that POI's name or
    one of its variations. Returns `(place, True)` — the place as given,
    flagged `new_poi` — when no POI matches, including when `pois` is
    empty.
    """
    normalized_place = _place_key(place)
    for poi in pois:
        candidates = [poi["name"], *poi.get("name_variations", [])]
        for candidate in candidates:
            if _place_key(candidate) == normalized_place:
                return poi["name"], False
    return place, True


def _place_key(name: str) -> str:
    """Casefold, whitespace-normalized, without full stops ("Solomon R
    Guggenheim Museum" is the POI "Solomon R. Guggenheim Museum", slice 9
    job 1 run 3) and without a leading "the"."""
    key = normalize_ws(name.replace(".", "")).casefold()
    return key[4:] if key.startswith("the ") else key


def every_claim_once(
    story_claim_ids: Sequence[Sequence[str]], claim_ids: Sequence[str]
) -> list[str]:
    """Every claim in `claim_ids` must be assigned to exactly one story.

    Counts each id's occurrences across every story's claim list. Returns
    one `orphan_claim:<id>` reason for each id in `claim_ids` that appears
    in no story, and one `double_booked:<id>` reason for each id that
    appears in more than one — in `claim_ids` order. Ids in a story's list
    that are not in `claim_ids` at all are `known_claim_ids`'s concern, not
    this gate's.
    """
    counts: dict[str, int] = {}
    for story_ids in story_claim_ids:
        for claim_id in story_ids:
            counts[claim_id] = counts.get(claim_id, 0) + 1

    reasons: list[str] = []
    for claim_id in claim_ids:
        n = counts.get(claim_id, 0)
        if n == 0:
            reasons.append(f"orphan_claim:{claim_id}: never assigned to any story")
        elif n > 1:
            reasons.append(f"double_booked:{claim_id}: assigned to {n} stories")
    return reasons


def known_claim_ids(
    story_claim_ids: Sequence[Sequence[str]], claim_ids: Sequence[str]
) -> str | None:
    """Every id a story claims must actually be in `claim_ids`.

    Returns None when every id referenced across every story is known;
    otherwise a single `unknown_claim:<id>[,<id>...]` reason naming each
    unrecognized id once, in first-seen order.
    """
    known = set(claim_ids)
    unknown: list[str] = []
    seen: set[str] = set()
    for story_ids in story_claim_ids:
        for claim_id in story_ids:
            if claim_id not in known and claim_id not in seen:
                unknown.append(claim_id)
                seen.add(claim_id)
    if not unknown:
        return None
    return f"unknown_claim:{','.join(unknown)}: not in the claim set"


def claim_floor(beat_type: str, n: int) -> str | None:
    """A story needs a minimum number of claims to be worth a beat.

    `STRUCTURAL_BEAT_TYPES` (stop_orientation, transit, sidebar, practicalities) need only
    one; everything else needs two. Zero never passes, structural or not.
    Returns None when `n` meets the floor; otherwise a `too_few_claims`
    reason naming the floor and the actual count.
    """
    floor = 1 if beat_type in STRUCTURAL_BEAT_TYPES else 2
    if n >= floor:
        return None
    return f"too_few_claims: beat_type {beat_type!r} requires >= {floor} claim(s), has {n}"


def lenses_valid(lenses: Sequence[str]) -> str | None:
    """A story's lenses must be non-empty, deduplicated, and all taggable.

    Returns None when `lenses` is non-empty, has no duplicates, and every
    entry is in `TAGGABLE_LENSES`; otherwise a `bad_lenses` reason.
    """
    if not lenses:
        return "bad_lenses: must include at least one lens"
    if len(set(lenses)) != len(lenses):
        return f"bad_lenses: {list(lenses)!r} contains duplicates"
    invalid = [lens for lens in lenses if lens not in TAGGABLE_LENSES]
    if invalid:
        return f"bad_lenses: {invalid!r} outside TAGGABLE_LENSES"
    return None


def beat_type_valid(beat_type: str) -> str | None:
    """A story's beat_type must be one of `BEAT_TYPE_VALUES`.

    Returns None when it is; otherwise a `bad_beat_type` reason.
    """
    if beat_type in BEAT_TYPE_VALUES:
        return None
    return f"bad_beat_type: {beat_type!r} is not a known beat_type"


def unique_slugs(slugs: Sequence[str]) -> list[str]:
    """No two stories may share a `story_slug`.

    Returns one `duplicate_slug:<slug>` reason per slug used more than
    once (not per extra occurrence); [] when every slug is unique.
    """
    counts: dict[str, int] = {}
    for candidate in slugs:
        counts[candidate] = counts.get(candidate, 0) + 1

    reasons: list[str] = []
    seen: set[str] = set()
    for candidate in slugs:
        if counts[candidate] > 1 and candidate not in seen:
            reasons.append(f"duplicate_slug:{candidate}: used by more than one story")
            seen.add(candidate)
    return reasons


def story_problems(stories: Sequence[Mapping[str, object]], claim_ids: Sequence[str]) -> list[str]:
    """The P2 aggregate: every set-level and per-story gate over a story partition.

    Runs, in order: `every_claim_once`, `known_claim_ids`, then per story
    (in story order) `claim_floor`, `lenses_valid`, `beat_type_valid`, and
    finally `unique_slugs` over every story's slug. A per-story reason has
    that story's own `story_slug` (`model.slug(story['title'])`) threaded
    into its code, e.g. `too_few_claims:<slug>: ...`, so a caller can tell
    which story a defect belongs to; `every_claim_once`, `known_claim_ids`
    and `unique_slugs` already name the claim id or slug directly.

    Returns `["empty_set"]` when `stories` is empty (no partition to
    check); [] for a clean partition.
    """
    if not stories:
        return ["empty_set"]

    story_claim_ids = [list(story["claim_ids"]) for story in stories]  # type: ignore[arg-type]
    reasons: list[str] = []
    reasons.extend(every_claim_once(story_claim_ids, claim_ids))

    unknown_reason = known_claim_ids(story_claim_ids, claim_ids)
    if unknown_reason is not None:
        reasons.append(unknown_reason)

    slugs = [story_slug_of(str(story["title"])) for story in stories]
    for story, slug in zip(stories, slugs, strict=True):
        for gate_reason in (
            claim_floor(str(story["beat_type"]), len(story["claim_ids"])),  # type: ignore[arg-type]
            lenses_valid(story["lenses"]),  # type: ignore[arg-type]
            beat_type_valid(str(story["beat_type"])),
        ):
            if gate_reason is None:
                continue
            code, _, detail = gate_reason.partition(": ")
            reasons.append(f"{code}:{slug}: {detail}")

    reasons.extend(unique_slugs(slugs))
    return reasons
