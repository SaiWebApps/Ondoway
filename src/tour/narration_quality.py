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
from dataclasses import dataclass, field

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


__all__ = ["NarrationQuality", "score_narration"]
