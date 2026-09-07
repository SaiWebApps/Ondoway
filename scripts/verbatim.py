"""How much of a body was copied out of the source it cites.

One home for the copying measure, because two callers need it at opposite ends of
the pipeline and a second implementation would drift: `extract_validators` gates a
beat as it is written, `corpus_report` reports the corpus long after. It lives
here rather than in either of them — `corpus_report` already imports from
`extract_validators`, so the measure sitting in the reporter could not be reached
by the gate without a cycle.

Two measures, and only one of them decides anything.

**The longest shared RUN** is the gate. It answers "was a sentence lifted", does not
move when the prose around a lift grows, and is measured outside attributed
quotation — quoting a named speaker and citing them is not copying the book that
also quoted them. A quotation attributed to nobody earns no exemption, because
that is the shape of an unmarked lift.

**The RATIO** — matched 8-word shingles over body length — describes how copied a
whole body is. It is reported and never gated on: one lifted clause inside a long
body scores near zero under it, which is how a corpus reached 78% lifted while
this number said 17%.
"""

from __future__ import annotations

import re
from typing import Any

from scripts.dedup_pairs import shingle_set

#: Shingle width for the copied-body test. Eight words is evidence of copying.
VERBATIM_SHINGLE: int = 8

#: At or above this share of shared shingles, a body was copied rather than written.
VERBATIM_THRESHOLD: float = 0.70

#: A shared run of this many words is a lift. Same eight words as VERBATIM_SHINGLE,
#: named separately because it gates a decision while the shingle width only sizes a
#: ratio, and the two would otherwise be one constant serving two jobs.
VERBATIM_RUN_BLOCK: int = 8

#: The word contract for the run measure: digits and letters, case-folded, everything
#: else a boundary. Punctuation, apostrophes and hyphens do not survive it, so
#: "dog-barbers" is two words and "city's" is two. Deliberately looser than
#: `shingle_set`'s whitespace split, which keeps punctuation glued to a word and so
#: reads a lift as unshared whenever a comma moved.
_VERBATIM_WORD = re.compile(r"[0-9a-z\u00c0-\u024f]+")

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

_ATTRIBUTION_CUES = frozenset(
    [
        "wrote",
        "writes",
        "written",
        "write",
        "said",
        "says",
        "say",
        "saying",
        "told",
        "tells",
        "telling",
        "asked",
        "asks",
        "called",
        "calls",
        "calling",
        "recalled",
        "recalls",
        "described",
        "describes",
        "describing",
        "noted",
        "notes",
        "observed",
        "observes",
        "remarked",
        "remarks",
        "quipped",
        "declared",
        "declares",
        "dubbed",
        "termed",
        "letter",
        "diary",
        "memoir",
        "inscription",
        "inscribed",
        "motto",
        "epitaph",
        "quoted",
        "quoting",
    ]
)

#: How far either side of a quotation an attribution may sit. One clause, not a
#: paragraph: "she wrote:" leads a quote and "he told a friend" interrupts one.
_ATTRIBUTION_WINDOW = 120


def verbatim_ratio(script_body: str, source_passage: str) -> float:
    """Share of the body's 8-word shingles that also appear in its source passage.

    Returns 0.0 when either side is empty: a beat with no recorded source cannot
    be shown to have been copied from one.
    """
    body = shingle_set(script_body, VERBATIM_SHINGLE)
    if not body:
        return 0.0
    source = shingle_set(source_passage, VERBATIM_SHINGLE)
    if not source:
        return 0.0
    return len(body & source) / len(body)


def verbatim_word_spans(text: str) -> list[tuple[str, int, int]]:
    """Every word of `text` as `(word, start_char, end_char)`, case-folded.

    Positions are kept because a caller has to ask where a matched run sits — inside
    a quotation or out in the narration — and a bare word list cannot answer that.
    """
    return [(m.group(), m.start(), m.end()) for m in _VERBATIM_WORD.finditer(text.lower())]


def verbatim_words(text: str) -> list[str]:
    """The case-folded words of `text`, in order."""
    return [word for word, _, _ in verbatim_word_spans(text)]


def max_verbatim_run(script_body: str, source_passage: str) -> int:
    """Length of the longest word run the body shares with its source passage.

    This is the copying measure with a threshold behind it. `verbatim_ratio` divides
    matched shingles by body length, so one lifted clause inside a long body scores
    near zero — it describes how copied a whole body is, and answers nothing about
    whether any sentence was lifted. The longest run answers exactly that and does not
    move when the body around it grows.

    Returns 0 when either side is empty.
    """
    return longest_shared_run(verbatim_words(script_body), verbatim_words(source_passage))


def longest_shared_run(body_words: list[str], source_words: list[str]) -> int:
    """Length of the longest contiguous run `body_words` shares with `source_words`.

    Binary search on the run length: a shared run of length k implies one of length
    k-1, so the property is monotone and log(n) k-gram set builds settle it.
    """
    return shared_run_at(body_words, source_words)[0]


def shared_run_at(body_words: list[str], source_words: list[str]) -> tuple[int, int]:
    """The longest shared run as `(length, first index in body_words)`.

    Index is -1 when there is no shared run at all.
    """
    if not body_words or not source_words:
        return 0, -1

    def shares(k: int) -> bool:
        source_grams = {tuple(source_words[i : i + k]) for i in range(len(source_words) - k + 1)}
        return any(
            tuple(body_words[i : i + k]) in source_grams for i in range(len(body_words) - k + 1)
        )

    low, high = 0, min(len(body_words), len(source_words))
    while low < high:
        mid = (low + high + 1) // 2
        if shares(mid):
            low = mid
        else:
            high = mid - 1
    if low == 0:
        return 0, -1
    source_grams = {tuple(source_words[i : i + low]) for i in range(len(source_words) - low + 1)}
    for i in range(len(body_words) - low + 1):
        if tuple(body_words[i : i + low]) in source_grams:
            return low, i
    return low, -1


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
