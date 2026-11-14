"""Narration quality signals — a $0, deterministic AI-tell LINT for tour narration.

WHAT THIS IS: a transparent surface-feature lint. Its trustworthy output is the
``hits`` dict — the exact stilted/AI-generated tells found, quoted, so a HUMAN can
eyeball them — plus raw per-100-word rates and sentence statistics. It flags the
measured "sounds generated" tells published craft sources name (moralizing closers,
puffery, AI-vocab, empty transitions, negative parallelism, em-dash spam, uniform
sentence rhythm) and the good-spoken-narration surface features (second-person,
sentence-initial look/move prompts, sentence-length variation).

WHAT THIS IS NOT: it is NOT a quality
oracle and NOT a precise comparator. It reads only SURFACE features, so:
  - Content-free but "you"-stuffed, length-varied drivel can score well.
  - A single legitimate factual construction can dominate a short text.
So the composite ``stilted_score`` / ``engagement_score`` are COARSE: use them only
on long aggregates (a whole tour, not one beat — see ``reliable`` below) and only
for LARGE gaps. Whether Opus or ChatGPT writes a BETTER tour is decided by a human /
acceptance read of the actual narration; this lint only surfaces the tells to look
at. Never present a small score delta as evidence of a quality difference.

Sources for the tells: Rick Steves / VoiceMap / Nubart / Pathoura / Method Writing
(write for the ear; second person; short varied sentences; end on image not moral),
GPTZero (burstiness), Wikipedia "Signs of AI writing" (puffery / significance-close
/ empty transitions / negative parallelism / em-dash / AI vocab), Tilden
(provocation not instruction).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .generation import split_sentences

# --- lexicons (each hardened against the false positives noted beside it) ---

# Significance / moralizing closers — telling the listener what to think. Requires a
# SIGNIFICANCE noun after "as a/an/the" so plain factual "serves as a museum / the
# archive" does NOT fire (a verified false positive); only "serves/stands as a
# symbol/testament/…" does.
_SIGNIF = r"(testament|reminder|symbol|monument|tribute|embodiment|reflection|celebration)"
_MORALIZING = re.compile(
    rf"\b(stands?|serves?|served|stood|endures?)\s+as\s+(a|an|the)\s+{_SIGNIF}\b"
    rf"|\bis\s+(a|an)\s+{_SIGNIF}\s+to\b"
    rf"|\ba\s+{_SIGNIF}\s+to\b"
    r"|\bspeaks?\s+to\s+the\s+enduring\b"
    r"|\bmark(s|ing|ed)?\s+a\s+pivotal\b"
    r"|\b(plays?|played)\s+a\s+(vital|crucial|pivotal|significant|key)\s+role\b"
    r"|\breminds?\s+us\s+(that|of)\b",
    re.IGNORECASE,
)

# Puffery / travel-cliché adjectives and phrases (nestled lives here).
_PUFFERY = re.compile(
    r"\b(vibrant|bustling|nestled|breathtaking|picturesque|stunning|iconic|"
    r"charming|quaint|majestic|timeless|storied|hidden\s+gem|must-see|"
    r"rich\s+(tapestry|cultural\s+heritage|history\s+and\s+culture)|"
    r"a\s+land\s+of\s+contrasts|where\s+\w+\s+meets\s+\w+)\b",
    re.IGNORECASE,
)

# "AI vocabulary" RLHF over-weights. NOTE: 'testament'/'nestled' deliberately NOT
# here — they are already scored by _MORALIZING / _PUFFERY (no double counting).
_AI_VOCAB = re.compile(
    r"\b(delve|delves|delving|tapestry|realm|underscore[sd]?|myriad|"
    r"intricate|intricacies|boasts?)\b",
    re.IGNORECASE,
)

# Empty/throat-clearing transitions as a sentence-opener. "In addition" only when NOT
# "in addition to X" (a meaningful prepositional phrase — a verified false positive).
_EMPTY_TRANSITION = re.compile(
    r"(^|[.!?]\s+)(Furthermore|Moreover|Additionally|In addition(?!\s+to)|Indeed|"
    r"Notably|Importantly|Ultimately|In conclusion|It is worth noting|"
    r"It's worth noting)\b",
    re.IGNORECASE,
)

# Negative parallelism — including the spoken contraction form ("isn't just X, it's
# Y"), the most common realization of this tell (a verified false negative before).
_NEGATIVE_PARALLELISM = re.compile(
    r"\b(not|isn'?t|aren'?t|wasn'?t|weren'?t)\s+(just|only|merely|simply)\b"
    r"[^.!?]{0,80}?\b(but|it'?s|they'?re|it\s+is|its)\b",
    re.IGNORECASE,
)

# A 4-digit year (1000-2099), optional 's' ("the 1800s"), NOT immediately followed by
# a measure/unit noun (so "1063 feet tall", "2000 workers" do NOT count as dates).
_YEAR = re.compile(
    r"\b(1[0-9]{3}|20[0-9]{2})s?\b"
    r"(?!\s*(feet|foot|metres?|meters?|workers?|people|miles?|kilometres?|"
    r"kilometers?|steps?|seats?|acres?|tonnes?|tons?|pounds?|francs?|inches|"
    r"yards?|hectares?))",
    re.IGNORECASE,
)

_SECOND_PERSON = re.compile(r"\b(you|you'?re|you'?ll|your|yourself)\b", re.IGNORECASE)
# A look/move prompt counts ONLY when it OPENS a sentence (a real imperative), so the
# nouns "the picture hangs" / "the find made news" / auxiliary "stop" do NOT count.
_LOOK_INITIAL = re.compile(
    r"^(look|notice|spot|glance|watch|listen|turn|cross|walk|stop|pause|head|"
    r"carry\s+on|step|face|find)\b",
    re.IGNORECASE,
)

_WORD = re.compile(r"[A-Za-z0-9']+")
# Minimum words for the COMPOSITE scores to be meaningful (short texts saturate).
_RELIABLE_MIN_WORDS = 150

# --- transition and causal-chain proxies (craft_score ranking terms only) ---

# MOTIVATED TRANSITION openers/connectives: phrasing that states WHY the next idea
# follows (purpose, consequence, or a stated contrast), not mere sequence. It catches
# a hand-written opener like "To understand how a hotel like this came to belong here,
# we have to imagine..." Also covers the cleft-causal "It was here ... that X happened"
# and contrastive "However,". "However" lives ONLY here (never in _CAUSAL_CHAIN below —
# see that pattern's note) so the two proxies stay disjoint and a single "However," is
# never counted twice.
# The cleft-causal gap is 90 chars, wide enough for a hand-written sentence with 74
# chars between "here" and "that": "It was here, supplying Empress Eugenie and the other
# ladies of the Napoleonic court, that French haute couture was born" (pinned by
# test_cleft_causal_matches_the_gold_sentence).
# KNOWN FALSE NEGATIVE: this is a small, closed phrase list — a causal transition
# phrased any other way (most of them) scores zero. It is a floor, not a detector.
_MOTIVATED_TRANSITION = re.compile(
    r"(^|[.!?]\s+)(To\s+(understand|see|grasp)\s+(how|why)\b|"
    r"This\s+(is\s+why|meant|led|explains)\b|That\s+is\s+(why|what|how)\b|"
    r"Because\s+of\s+(this|that)\b|As\s+a\s+result\b|Which\s+(is\s+why|meant|explains)\b|"
    r"However\b)|\bIt\s+was\s+(here|this|then)\b[^.!?]{0,90}?\bthat\b",
    re.IGNORECASE,
)

# BARE ADJACENCY (the motivated transition's negative): a sentence-initial filler that
# moves to the next fact without saying why ("Also," / "Nearby," / "Similarly,").
# 'Additionally'-class words belong to _EMPTY_TRANSITION. 'Nearby,' is excluded because
# it is often a spatial cue.
_BARE_ADJACENCY = re.compile(r"(^|[.!?]\s+)(Also|Similarly|Elsewhere)\b,?", re.IGNORECASE)

# CAUSAL CHAIN: mid-sentence connectives where one fact is stated as the CAUSE of the
# next, rather than facts merely listed side by side. It catches a hand-written chain
# like "helped make this stretch ... fashionable, drawing an English clientele".
# 'however' is scored only by _MOTIVATED_TRANSITION; the two term sets stay disjoint so
# one connective never counts twice.
# KNOWN FALSE POSITIVE: "in turn" occasionally appears in a text that is otherwise
# still a list (this only samples one connective, not the whole chain).
# KNOWN FALSE NEGATIVE: a causal chain built from plain "and" + verb tense with no
# marker word (common, and exactly how the machine texts read) scores zero — this
# proxy can only catch the MARKED case, never infer causation from bare adjacency.
_CAUSAL_CHAIN = re.compile(
    r"\b(which\s+(made|drew|drove|led|helped|caused|meant|sparked|prompted|allowed|earned)|"
    r"help(?:ed|ing)\s+make\b|in\s+turn\b|thereby\b|hence\b|drawing\s+an?\b|drew\s+an?\b|"
    r"so\s+that\b|leading\s+to\b|led\s+to\b|giving\s+rise\s+to\b|gave\s+rise\s+to\b|"
    r"pav(?:ed|ing)\s+the\s+way\s+for\b)",
    re.IGNORECASE,
)


def _words(text: str) -> list[str]:
    return _WORD.findall(text)


@dataclass
class NarrationQuality:
    """Transparent per-text signals. The ``hits`` dict is the trustworthy output.
    ``stilted_score`` (0..1, lower better) and ``engagement_score`` (0..1, higher
    better) are COARSE — meaningful only when ``reliable`` is True and only for LARGE
    gaps (see module docstring)."""

    n_words: int
    n_sentences: int
    mean_sentence_words: float
    burstiness: float
    long_sentence_rate: float
    per_100w: dict[str, float] = field(default_factory=dict)
    hits: dict[str, list[str]] = field(default_factory=dict)
    second_person_rate: float = 0.0
    look_prompt_rate: float = 0.0
    stilted_score: float = 0.0
    engagement_score: float = 0.0
    reliable: bool = False  # False => composite scores are noise; read ``hits``


def _rate_per_100(n: int, n_words: int) -> float:
    return round(100.0 * n / n_words, 3) if n_words else 0.0


def score_narration(text: str) -> NarrationQuality:
    """Score one narration string. Pure and deterministic."""
    sents = [s for s in (p.strip() for p in split_sentences(text)) if s]
    words = _words(text)
    n_words = len(words)
    n_sents = len(sents)
    lengths = [len(_words(s)) for s in sents] or [0]
    mean_len = sum(lengths) / len(lengths)
    if len(lengths) > 1 and mean_len > 0:
        var = sum((x - mean_len) ** 2 for x in lengths) / len(lengths)
        burstiness = round((var**0.5) / mean_len, 3)
    else:
        burstiness = 0.0
    long_rate = round(sum(1 for x in lengths if x > 30) / len(lengths), 3)

    def _find(pat: re.Pattern[str]) -> list[str]:
        return [m.group(0).strip() for m in pat.finditer(text)]

    tells = {
        "moralizing_closer": _find(_MORALIZING),
        "puffery": _find(_PUFFERY),
        "ai_vocab": _find(_AI_VOCAB),
        "empty_transition": _find(_EMPTY_TRANSITION),
        "negative_parallelism": _find(_NEGATIVE_PARALLELISM),
    }
    years = _YEAR.findall(text)
    per_100 = {k: _rate_per_100(len(v), n_words) for k, v in tells.items()}
    per_100["year_density"] = _rate_per_100(len(years), n_words)
    # Em/en dashes only (a spaced hyphen "100 - 200" is a numeric range, not a dash).
    n_dash = text.count("\u2014") + text.count("\u2013")  # em-dash + en-dash
    per_100["em_dash"] = _rate_per_100(n_dash, n_words)

    sp_rate = _rate_per_100(len(_SECOND_PERSON.findall(text)), n_words)
    look_rate = round(
        sum(1 for s in sents if _LOOK_INITIAL.match(s)) / n_sents, 3
    ) if n_sents else 0.0

    # COARSE composite (see docstring). year_density is down-weighted — it is a weak,
    # FP-prone signal; the strong tells (moralizing/puffery/uniform rhythm) dominate.
    penalties = (
        1.6 * per_100["moralizing_closer"]
        + 1.4 * per_100["puffery"]
        + 1.0 * per_100["ai_vocab"]
        + 1.0 * per_100["empty_transition"]
        + 1.2 * per_100["negative_parallelism"]
        + 0.15 * per_100["year_density"]
        + 0.4 * per_100["em_dash"]
        + 2.5 * long_rate
        + 2.0 * max(0.0, 0.45 - burstiness)
    )
    stilted = round(min(1.0, penalties / 6.0), 3)
    engagement = round(
        min(1.0, 0.10 * sp_rate + 0.7 * look_rate + 0.6 * min(burstiness, 0.6)), 3
    )

    return NarrationQuality(
        n_words=n_words,
        n_sentences=n_sents,
        mean_sentence_words=round(mean_len, 2),
        burstiness=burstiness,
        long_sentence_rate=long_rate,
        per_100w=per_100,
        hits={k: v for k, v in tells.items() if v},
        second_person_rate=sp_rate,
        look_prompt_rate=look_rate,
        stilted_score=stilted,
        engagement_score=engagement,
        reliable=n_words >= _RELIABLE_MIN_WORDS,
    )


# --- craft_score: a deterministic "well-written" ranker for best-of-N SELECTION ---
# Built from checkable writing rules (sentence-variety percussion, anti-redundancy =
# "the same point twice is padding", crutch-word control)
# plus the AI-tell lint. NOT a quality ORACLE — a RELATIVE ranker among candidate
# composes of the SAME stop: among fact-safe candidates, pick the highest craft_score so
# a flat/repetitive/choppy sample loses to a well-written one. This is the reliability
# lever — it converts run-to-run LLM variance into a consistent pick.

# Function words excluded from crutch/over-repetition detection (repeating "the" is fine;
# repeating "Conciergerie" or "prisoners" five times is a crutch).
_STOPWORD_STR = (
    "the a an and or but of to in on at by for with from as is was were are be been being "
    "it its this that these those he she they them his her their you your our we us i not "
    "had has have will would could should can may might do does did so then than there here "
    "which who whom whose when where what how why all any some one two into over under out "
    "up down off about after before between through during still only just also more most"
)
_STOPWORDS = frozenset(_STOPWORD_STR.split())


def _near_dup_pairs(sents: list[str]) -> int:
    """Sentence pairs that RESTATE each other (rapidfuzz token_set_ratio >= 85) — the
    'same point twice in new words' redundancy that pads a stop."""
    n = 0
    for i in range(len(sents)):
        for j in range(i + 1, len(sents)):
            if fuzz.token_set_ratio(sents[i], sents[j]) >= 85:
                n += 1
    return n


def _max_content_repeat(words: list[str]) -> int:
    """Highest count of any single content word — a crutch/over-repetition signal."""
    c = Counter(w.lower() for w in words if len(w) > 3 and w.lower() not in _STOPWORDS)
    return max(c.values(), default=0)


def craft_score(text: str) -> float:
    """Deterministic 'well-written' score, HIGHER = better, for ranking candidate composes
    of one stop. Rewards sentence-variety percussion (a short punch AND a longer line),
    burstiness, second-person and look-cues; penalizes redundant restatement, crutch-word
    over-repetition, and the measured AI tells.

    The motivated_transition / causal_chain terms below are a LEXICAL PROXY for two
    semantic checks: does each transition say why the next idea follows, and are facts
    chained by cause rather than listed. This module is already an explicitly
    lexical/statistical heuristic (see module docstring). See _MOTIVATED_TRANSITION /
    _BARE_ADJACENCY / _CAUSAL_CHAIN above for the false-positive/false-negative shapes
    of this proxy.

    THESE TERMS ALMOST NEVER CHANGE A RANKING. Do not cite them as a quality
    improvement. Over 303 real composed stops from ``data/paris/tours/``, adding them
    changes 0 of 303 scores and flips 0 of 45,753 pairwise winners, while an engineered
    probe does move (+0.2000, 3 pattern matches). The terms DO separate hand-written
    reference narration (combined rate 0.781/100w) from machine output
    (0-0.186/100w), but a best-of-N tie-break compares TWO MACHINE CANDIDATES, and both
    score ~0 on these patterns. A metric whose discriminating power sits at the
    hand-written end cannot break ties at the machine end.

    To make these terms steer output, either widen the patterns until they register
    the variation that EXISTS between machine candidates (and re-measure the flip
    rate), or move the effort to the compose PROMPT (what gets written) or beat
    SELECTION (what gets seated).

    WHERE THIS FUNCTION IS USED: no production code calls it; only its tests do. It is
    a RANKER, not a gate: its intended use is a best-of-N tie-break among candidates
    that already tied on fact-safety, where losing means an equally fact-safe candidate
    ships instead and no content is deleted.

    BOUNDING THE TIE-BREAK RISK: the transition and causal-chain terms below are
    deliberately kept small relative to the rest of the score so they cannot flip a
    close comparison on their own. Each of
    motivated_rate/causal_chain_rate/bare_adjacency_rate is a per-SENTENCE fraction
    (0..1, same shape as ``look_prompt_rate``, not an unbounded per-100-words rate), and
    each is weighted at 0.2 — so the combined best-case swing from one sentence
    (+0.2 motivated, +0.2 causal, both firing on the same sentence) is at most 0.4,
    below the 0.5 ceiling of the smallest other bounded positive term
    (``look_prompt_rate``, weight 0.5 x max rate 1.0) and far below percussion (1.5) or
    the redundancy penalty (-1.5/pair). This bounds, but does not eliminate, the risk on
    a near-tie best-of-N comparison: a 0.2-0.4 swing from phrasing alone COULD still be
    the deciding factor — that is inherent to any composite score used to break ties.
    test_however_is_not_double_counted pins the bound for a single "However,".
    """
    q = score_narration(text)
    sents = [s for s in (p.strip() for p in split_sentences(text)) if s]
    lengths = [len(_words(s)) for s in sents]
    # A stop needs a sentence under 8 words (percussion) AND one over 20
    percussion = 1.0 if (any(x < 8 for x in lengths) and any(x > 20 for x in lengths)) else 0.0
    redundancy = _near_dup_pairs(sents)
    crutch = _max_content_repeat(_words(text))
    tells = sum(
        q.per_100w.get(k, 0.0)
        for k in ("moralizing_closer", "puffery", "ai_vocab", "empty_transition",
                  "negative_parallelism")
    )
    n_sents = len(sents)
    # The transition and causal-chain rates are a per-SENTENCE fraction (0..1), the
    # same bounded shape as ``look_prompt_rate`` above — deliberately NOT the unbounded
    # per-100-words rate used for the AI-tell lint terms, whose rate spikes on short
    # texts. See the docstring's BOUNDING THE TIE-BREAK RISK note for why this shape +
    # these weights.
    motivated_rate = (
        round(sum(1 for s in sents if _MOTIVATED_TRANSITION.search(s)) / n_sents, 3)
        if n_sents else 0.0
    )
    bare_adjacency_rate = (
        round(sum(1 for s in sents if _BARE_ADJACENCY.search(s)) / n_sents, 3)
        if n_sents else 0.0
    )
    causal_chain_rate = (
        round(sum(1 for s in sents if _CAUSAL_CHAIN.search(s)) / n_sents, 3)
        if n_sents else 0.0
    )
    score = (
        1.5 * percussion
        + 1.2 * min(q.burstiness, 0.6)
        + 0.10 * q.second_person_rate
        + 0.5 * q.look_prompt_rate
        + 0.2 * motivated_rate  # stated-reason transitions (bounded, see docstring)
        + 0.2 * causal_chain_rate  # facts chained by cause (bounded, see docstring)
        - 0.2 * bare_adjacency_rate  # bare-adjacency filler (bounded, see docstring)
        - 1.5 * redundancy  # the same fact restated is the top wart — penalise hard
        - 0.4 * max(0, crutch - 3)  # a content word repeated >3x is a crutch
        - 1.0 * tells
    )
    return round(score, 3)


__all__ = ["NarrationQuality", "craft_score", "score_narration"]
