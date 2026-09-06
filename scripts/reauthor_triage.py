"""Re-triage the re-authored backlog with two gates that cost nothing.

`reauthor_verify.py` cleared 384 of 524 rewrites with a two-model panel that
included the model which wrote them, so those approvals carry no independent
evidence. Nothing shipped: every affected beat still holds its original copied
body. This module re-sorts the existing output before any further spend, using
only what a file and a CPU can settle.

**Gate 1 — the longest shared run, outside attributed quotation.**
`verbatim_ratio` divides matched shingles by body length, so a lifted clause
inside a long body scores near zero; every rewrite passed it while 81 of the 384
approvals still carried an 8+ word run. The run length is the measure that
matches the stated criterion, and it does not move when the prose around the lift
grows. Words inside an ATTRIBUTED quotation are cut out of the body before the
run is measured, because quoting Colette and citing her is not copying the
guidebook that also quoted her. A quotation with nobody named is not exempt.

**Gate 2 — a disagreement between the source and the shipped body.**
The rewrite prompt names `source_passage` as the only facts that may appear, so
where the source and `body_before` disagree the rewrite adopts the source and no
judge is licensed to object — a shipped fact changes silently. Wherever the two
disagree on a number, a date or a proper name, the beat cannot auto-approve.

**Neither gate makes a rewrite shippable.** They are necessary, not sufficient.
Passing both means "eligible for real verification", never "verified": the
approvals were graded by a conflicted panel, and most rewrites still follow their
source's sentence order, which is the arrangement copyright protects.
`order_follows_source` reports that third fact and deliberately does not gate on
it — it is a property Stage 1 must design away, not one a checker can repair.

Free and deterministic, so it is re-runnable rather than persisted:
`data/{city}/reauthored.json` is gitignored and this writes nothing to it.

Run as `uv run python scripts/reauthor_triage.py --city paris`.
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

from scripts.corpus_report import (
    VERBATIM_RUN_BLOCK,
    max_verbatim_run,
    shared_run_at,
    verbatim_ratio,
    verbatim_word_spans,
    verbatim_words,
)
from scripts.reauthor_preview import EXCLUDED_CITIES
from scripts.reauthor_review import load_candidates

#: Verdicts this triage assigns. `CLEAR` means both gates passed, which makes a
#: rewrite eligible for verification — never verified.
CLEAR = "clear"
BLOCKED_RUN = "blocked-run"
BLOCKED_CONFLICT = "blocked-conflict"
BLOCKED_BOTH = "blocked-both"

#: Quotation delimiters that a rewrite actually uses. Straight and curly doubles,
#: guillemets, and curly singles. The straight apostrophe is handled separately:
#: it is the same character as the possessive, so it needs a position test rather
#: than a character test.
_QUOTE_PAIRS = (('"', '"'), ("\u201c", "\u201d"), ("\u00ab", "\u00bb"), ("\u2018", "\u2019"))

#: A straight single quote opens a quotation only at a boundary and before a word,
#: and closes one only after a word and before a boundary. "Colette's" matches
#: neither, so a possessive never opens a span that swallows the rest of a body.
_SINGLE_OPEN = re.compile("(?:(?<=^)|(?<=[\\s(\u2014\u2013:,]))'(?=[\\w\u00c0-\u024f])")
_SINGLE_CLOSE = re.compile("(?<=[\\w\u00c0-\u024f.,!?;])'(?=$|[\\s)\u2014\u2013.,;:!?])")

#: Words that name who is being quoted. A quotation with none of these near it is
#: a quotation attributed to nobody, which is exactly the shape of an unmarked
#: lift, so it earns no exemption.
# fmt: off
_ATTRIBUTION_CUES = frozenset([
    "wrote", "writes", "written", "write", "said", "says", "say", "saying", "told", "tells",
    "telling", "asked", "asks", "called", "calls", "calling", "recalled", "recalls",
    "described", "describes", "describing", "noted", "notes", "observed", "observes",
    "remarked", "remarks", "quipped", "declared", "declares", "dubbed", "termed", "put",
    "letter", "diary", "memoir", "inscription", "inscribed", "motto", "epitaph", "reads",
    "reading", "according", "quoted", "quoting", "words", "line", "verse", "plaque", "sign"
])
# fmt: on

#: How far either side of a quotation an attribution may sit. One clause, not a
#: paragraph: "she wrote:" leads a quote and "he told a friend" interrupts one.
_ATTRIBUTION_WINDOW = 120

#: Words too common to identify which fact a number belongs to. A number's
#: neighbour is its slot key, and "the 1793" would collide with every other date
#: in the passage, reporting a conflict wherever two dates merely differ.
# fmt: off
_SLOT_STOPWORDS = frozenset([
    "the", "a", "an", "of", "in", "on", "at", "to", "and", "or", "but", "for", "from", "by",
    "with", "as", "is", "was", "were", "are", "be", "been", "that", "this", "it", "its",
    "his", "her", "their", "there", "here", "about", "over", "under", "until", "since",
    "between", "during", "after", "before", "near", "around", "some", "only", "just", "he",
    "she", "they", "we", "you", "i", "him", "them", "who", "whose", "which", "what", "when",
    "where"
])
# fmt: on

#: A capitalised word, which is what a proper name is made of. Digits are excluded
#: deliberately: a figure is the numeric gate's business, and letting one into a name
#: makes "July 27" and "July 27-29" a disagreement about a name.
_NAME_TOKEN = re.compile("[A-Z\u00c0-\u00dc][\\w\u00c0-\u024f'\u2019-]*")

#: Accents a scanned guidebook loses and a rewrite restores, folded away before two
#: names are compared.
_FOLD = str.maketrans("àáâãäåçèéêëìíîïñòóôõöùúûüýÿ", "aaaaaaceeeeiiiinooooouuuuyy")


def quoted_char_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges of `text` that sit inside a quotation, opener to closer.

    Unpaired openers are dropped rather than run to the end of the body: a stray
    quote mark must not exempt everything after it.
    """
    spans: list[tuple[int, int]] = []
    for opener, closer in _QUOTE_PAIRS:
        if opener == closer:
            marks = [m.start() for m in re.finditer(re.escape(opener), text)]
            spans.extend((marks[i], marks[i + 1]) for i in range(0, len(marks) - 1, 2))
            continue
        depth, start = 0, 0
        for index, char in enumerate(text):
            if char == opener and depth == 0:
                depth, start = 1, index
            elif char == closer and depth == 1:
                spans.append((start, index))
                depth = 0
    opens = [m.start() for m in _SINGLE_OPEN.finditer(text)]
    closes = [m.start() for m in _SINGLE_CLOSE.finditer(text)]
    for start in opens:
        following = [c for c in closes if c > start]
        if following:
            spans.append((start, following[0]))
            closes = [c for c in closes if c > following[0]]
    return sorted(spans)


def is_attributed(text: str, span: tuple[int, int]) -> bool:
    """Whether a quotation names who said it, within a clause of either end."""
    start, end = span
    window = (
        text[max(0, start - _ATTRIBUTION_WINDOW) : start] + text[end : end + _ATTRIBUTION_WINDOW]
    )
    return any(word in _ATTRIBUTION_CUES for word in verbatim_words(window))


def attributed_quote_spans(text: str) -> list[tuple[int, int]]:
    """The quotations of `text` that name a speaker — the only exempt ones."""
    return [span for span in quoted_char_spans(text) if is_attributed(text, span)]


def unquoted_segments(text: str) -> list[list[str]]:
    """`text` as word runs, cut wherever an attributed quotation removes words.

    Cutting rather than deleting is what makes the gate read a quotation as a
    boundary: a lift that resumes on the far side of a quote is two shorter runs,
    and a genuine quotation split by "he told a friend" leaves only those four
    words behind instead of one long apparent lift straddling both halves.
    """
    exempt = attributed_quote_spans(text)
    segments: list[list[str]] = []
    current: list[str] = []
    for word, start, end in verbatim_word_spans(text):
        if any(qs <= start and end <= qe for qs, qe in exempt):
            if current:
                segments.append(current)
                current = []
            continue
        current.append(word)
    if current:
        segments.append(current)
    return segments


def run_outside_quotation(body_after: str, source_passage: str) -> dict[str, Any]:
    """The longest run the rewrite shares with its source outside an attributed quote.

    Returns the run's `length` and the `text` of the words that matched, so a
    reader can see the lift rather than a number claiming there was one.
    """
    source_words = verbatim_words(source_passage)
    best_length, best_words = 0, []
    for segment in unquoted_segments(body_after):
        length, index = shared_run_at(segment, source_words)
        if length > best_length:
            best_length, best_words = length, segment[index : index + length]
    return {"length": best_length, "text": " ".join(best_words)}


def run_shape(run_text: str, body_after: str) -> str:
    """What kind of material a blocked run is made of.

    A run of proper nouns and digits — a museum's holdings, a list of signatories —
    is shared fact carrying no expression, and it is blocked all the same because
    the gate the brief specifies has one exemption and this is not it. Naming the
    shape is how the count stays honest about what it demoted.
    """
    words = run_text.split()
    if not words:
        return "none"
    capitalised = {w.lower() for w in _NAME_TOKEN.findall(body_after)}
    factual = sum(1 for w in words if w in capitalised or w.isdigit())
    return "names-and-numbers" if factual * 2 >= len(words) else "prose"


def numeric_slots(text: str) -> dict[str, set[str]]:
    """Every number in `text`, filed under the words beside it.

    A number alone says nothing about which fact it belongs to; "Pier 59" and
    "20,000 sq ft" are only comparable because of the word next to them. Both
    neighbours are used as keys, so a number is found whether its noun leads
    ("Pier 59") or trails ("20,000 sq ft").

    A neighbour never crosses a sentence boundary. "under €6. Wines of the month"
    would otherwise file 6 under `wines` and match it against a €12 two sentences
    away, reporting a disagreement between two texts that agree.
    """
    slots: dict[str, set[str]] = {}
    for sentence in sentences_of(re.sub(r"(?<=\d),(?=\d\d\d\b)", "", text)):
        words = verbatim_words(sentence)
        for index, word in enumerate(words):
            if not word.isdigit():
                continue
            previous = words[index - 1] if index else ""
            following = words[index + 1] if index + 1 < len(words) else ""
            for key in (previous, following):
                if key and not key.isdigit() and key not in _SLOT_STOPWORDS:
                    slots.setdefault(key, set()).add(word)
    return slots


def numeric_conflicts(source_passage: str, body_before: str) -> list[dict[str, Any]]:
    """Numbers the shipped body asserts that its source contradicts on the same slot.

    One-directional by design: a number the SOURCE carries and the body does not is
    the source being fuller, which costs nothing. A number the BODY carries that
    the source answers differently is a shipped fact the rewrite is licensed to
    overwrite, which is the defect. Only slots both texts use are compared, so an
    unmentioned figure is never read as a contradiction.
    """
    source, before = numeric_slots(source_passage), numeric_slots(body_before)
    found = []
    for key, values in sorted(before.items()):
        if key not in source:
            continue
        disputed = sorted(values - source[key])
        if disputed:
            found.append({"slot": key, "body_before": disputed, "source": sorted(source[key])})
    return found


def proper_names(text: str) -> set[tuple[str, ...]]:
    """Capitalised word runs of `text`, as lowercased token tuples.

    A single capitalised word that opens a sentence is dropped: it is capitalised
    by grammar, not because it names anything.
    """
    names: set[tuple[str, ...]] = set()
    for sentence in re.split(r"(?<=[.!?;:])\s+", text.strip()):
        run = rf"(?:{_NAME_TOKEN.pattern})(?:\s+(?:{_NAME_TOKEN.pattern}))*"
        for match in re.finditer(run, sentence):
            tokens = tuple(t.lower().translate(_FOLD) for t in match.group().split())
            if match.start() == 0 and len(tokens) == 1:
                continue
            names.add(tokens)
    return names


def name_conflicts(source_passage: str, body_before: str) -> list[dict[str, Any]]:
    """Names the shipped body spells one way and its source another.

    A conflict is one token substituted at one position in a name of the same
    length: "Charles Dickens" against "Charles Darwin". No similarity threshold,
    because a threshold tuned on the two examples that motivated the rule is a
    number fitted to its own evidence.

    A one-word name is never compared. With a single token, "differs in exactly one
    position" is true of every pair of unequal names, so the rule would read Molière
    against Aragon as a misspelling of it.

    Accents are folded first, because a scanned guidebook loses them and a rewrite
    restores them; "Hotel Chenizot" and "Hôtel Chenizot" are one name, not two.

    The cost is recall: a name the source renames entirely goes unreported here.
    """
    source, before = proper_names(source_passage), proper_names(body_before)
    found = []
    for name in sorted(before - source):
        if len(name) < 2:
            continue
        for other in sorted(source):
            if len(other) != len(name):
                continue
            differing = [i for i in range(len(name)) if name[i] != other[i]]
            if len(differing) == 1:
                found.append({"body_before": " ".join(name), "source": " ".join(other)})
                break
    return found


def sentences_of(text: str) -> list[str]:
    """Sentences, split on terminal punctuation followed by a capital."""
    parts = re.split(r"(?<=[.!?])\s+(?=[\"“A-ZÀ-Ü])", text.strip())
    return [p.strip() for p in parts if p.strip()]


def order_follows_source(source_passage: str, body_after: str) -> bool | None:
    """Whether the rewrite presents the source's material in the source's order.

    Each rewrite sentence is matched to the source sentence it shares most content
    words with; the rewrite follows the source when those indices never go
    backwards. Reported, never gated: a rewrite that fixes this is a rewrite that
    was never shown the source's order, which is an authoring change.

    None when the source has fewer than three sentences or nothing matched — order
    is not a property a two-sentence passage has.
    """
    source = sentences_of(source_passage)
    if len(source) < 3:
        return None
    bags = [set(verbatim_words(s)) for s in source]
    matched: list[int] = []
    for sentence in sentences_of(body_after):
        bag = set(verbatim_words(sentence))
        if not bag:
            continue
        scores = [(len(bag & b) / len(bag | b), -i) for i, b in enumerate(bags) if b]
        best = max(scores, default=(0.0, 0))
        if best[0] > 0:
            matched.append(-best[1])
    if len(matched) < 2:
        return None
    return all(matched[i] <= matched[i + 1] for i in range(len(matched) - 1))


def triage(record: dict) -> dict[str, Any]:
    """Both gates plus the reported diagnostics, for one re-authored candidate."""
    source = record.get("source_passage") or ""
    before = record.get("body_before") or ""
    after = record.get("body_after") or ""

    run = run_outside_quotation(after, source)
    numeric = numeric_conflicts(source, before)
    names = name_conflicts(source, before)
    blocked_run = run["length"] >= VERBATIM_RUN_BLOCK
    blocked_conflict = bool(numeric or names)

    if blocked_run and blocked_conflict:
        verdict = BLOCKED_BOTH
    elif blocked_run:
        verdict = BLOCKED_RUN
    elif blocked_conflict:
        verdict = BLOCKED_CONFLICT
    else:
        verdict = CLEAR

    return {
        "beat_id": record.get("beat_id", ""),
        "poi_name": record.get("poi_name", ""),
        "was": (record.get("verified") or {}).get("status", ""),
        "verdict": verdict,
        "run_outside_quotation": run["length"],
        "run_text": run["text"],
        "run_shape": run_shape(run["text"], after) if blocked_run else "none",
        "max_run_raw": max_verbatim_run(after, source),
        "verbatim_ratio": round(verbatim_ratio(after, source), 3),
        "numeric_conflicts": numeric,
        "name_conflicts": names,
        "order_follows_source": order_follows_source(source, after),
    }


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The triage as counts, split by the verdict the refuted panel gave each beat."""

    def slice_of(rows: list[dict[str, Any]]) -> dict[str, Any]:
        order = [r["order_follows_source"] for r in rows if r["order_follows_source"] is not None]
        return {
            "total": len(rows),
            CLEAR: sum(1 for r in rows if r["verdict"] == CLEAR),
            BLOCKED_RUN: sum(1 for r in rows if r["verdict"] == BLOCKED_RUN),
            BLOCKED_CONFLICT: sum(1 for r in rows if r["verdict"] == BLOCKED_CONFLICT),
            BLOCKED_BOTH: sum(1 for r in rows if r["verdict"] == BLOCKED_BOTH),
            "raw_run_at_or_over_block": sum(
                1 for r in rows if r["max_run_raw"] >= VERBATIM_RUN_BLOCK
            ),
            "run_shape_names_and_numbers": sum(
                1 for r in rows if r["run_shape"] == "names-and-numbers"
            ),
            "run_shape_prose": sum(1 for r in rows if r["run_shape"] == "prose"),
            "order_scoreable": len(order),
            "order_follows_source": sum(1 for v in order if v),
            "clear_and_order_divergent": sum(
                1 for r in rows if r["verdict"] == CLEAR and r["order_follows_source"] is False
            ),
        }

    return {
        "all": slice_of(rows),
        "was_auto_approved": slice_of([r for r in rows if r["was"] == "pass"]),
        "was_escalated": slice_of([r for r in rows if r["was"] == "escalate"]),
    }


def render(city_slug: str, summary: dict[str, Any]) -> str:
    """The triage as text, leading with what the two gates did to the approvals."""
    lines = [f"{city_slug} — Stage 0 triage ({summary['all']['total']} candidates)"]
    for label, key in (
        ("all", "all"),
        ("was auto-approved", "was_auto_approved"),
        ("was escalated", "was_escalated"),
    ):
        s = summary[key]
        if not s["total"]:
            continue
        lines.append(f"  {label} ({s['total']})")
        lines.append(
            f"    clear              {s[CLEAR]}   "
            f"blocked: run {s[BLOCKED_RUN]}, conflict {s[BLOCKED_CONFLICT]}, both {s[BLOCKED_BOTH]}"
        )
        lines.append(
            f"    run >= {VERBATIM_RUN_BLOCK} raw       {s['raw_run_at_or_over_block']} "
            f"(before the attributed-quotation exemption)"
        )
        lines.append(
            f"    blocked run shape  {s['run_shape_prose']} prose, "
            f"{s['run_shape_names_and_numbers']} names-and-numbers"
        )
        lines.append(
            f"    follows src order  {s['order_follows_source']} of "
            f"{s['order_scoreable']} scoreable — reported, not gated"
        )
        lines.append(f"    clear AND reordered {s['clear_and_order_divergent']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--city", default="paris", help="City slug under data/ (default paris).")
    parser.add_argument("--json", action="store_true", help="Emit every triaged row as JSON.")
    parser.add_argument(
        "--show",
        default="",
        help="Print the offending detail for rows with this verdict.",
    )
    args = parser.parse_args(argv)

    if args.city.lower() in EXCLUDED_CITIES:
        print(f"✗ {args.city} is excluded from re-author work; it has no candidates to triage.")
        return 2

    records = load_candidates(args.city)
    if not records:
        print(f"✗ no data/{args.city}/reauthored.json — nothing to triage.")
        return 1

    rows = [triage(r) for r in records]
    if args.json:
        print(json.dumps({"summary": summarise(rows), "rows": rows}, indent=2, ensure_ascii=False))
        return 0

    print(render(args.city, summarise(rows)))
    for row in rows:
        if args.show and row["verdict"] == args.show:
            print(f"\n  {row['beat_id']}  ({row['was']})")
            if row["run_outside_quotation"] >= VERBATIM_RUN_BLOCK:
                print(
                    f"    run {row['run_outside_quotation']}w "
                    f"[{row['run_shape']}]: {row['run_text']}"
                )
            for conflict in row["numeric_conflicts"]:
                print(
                    f"    number '{conflict['slot']}': body {conflict['body_before']} "
                    f"vs source {conflict['source']}"
                )
            for conflict in row["name_conflicts"]:
                print(
                    f"    name: body '{conflict['body_before']}' vs source '{conflict['source']}'"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
