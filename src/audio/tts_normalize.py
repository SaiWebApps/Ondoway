"""Pronunciation normalization for TTS-consumed text.

Neural TTS engines voice some corpus tokens as letters/symbols rather than the
words a listener expects: ``Louis XIV`` becomes "Louis ex-eye-vee" instead of
"Louis the fourteenth". :func:`normalize_for_tts` rewrites those tokens to plain
English so the pronunciation is correct *by construction* — the fix does not rely
on the TTS model's inconsistent Roman-numeral handling.

CRITICAL SCOPE: this runs ONLY on the text handed to a real TTS provider's
``generate`` — never on the stored corpus and never on the eval reference. The
corpus keeps its canonical spelling ("Louis XIV"); only the voiced text changes.
The WER eval reference (src/audio/eval.py) stays RAW because Whisper's own
number formatting usually restores the Roman spelling in running prose, so a
real paragraph beat round-trips at low WER against the raw reference. (In a SHORT
isolated regnal sentence Whisper may instead transcribe the spoken ordinal
verbatim — e.g. "the ninth" — nudging WER up; that is a property of the noisy
Whisper metric on tiny inputs, not a pronunciation defect, and it does not occur
on real multi-sentence beats. Normalizing the reference would instead spike WER
whenever Whisper DID restore the Roman form, so emission-only is the safer seam.)

RESTRAINT over reach: a Paris-history corpus is full of ``World War II``, the
pronoun ``I``, ``Chapter IV`` and ``Vatican II`` — none may be touched. Every
regnal rewrite is gated by a curated allow-list of monarch/pope name-stems AND a
canonical-Roman-numeral check, so only genuine ``{Sovereign} {Numeral}`` pairs
are rewritten.
"""

from __future__ import annotations

import re

TTS_NORMALIZATION_VERSION = "ondoway-tts-normalize-v1"

# ── Regnal / papal name-stems that legitimately precede a Roman numeral ──
# Drawn from the live Paris corpus survey (louis 814x, henri 461x, charles 197x,
# napoleon 151x, …) plus well-known European sovereigns and popes. Deliberately
# EXCLUDES ambiguous common words (war, chapter, room, vatican, urban) so those
# never trip the rewrite.
_REGNAL_NAMES: frozenset[str] = frozenset(
    {
        "louis",
        "saint-louis",
        "henri",
        "henry",
        "charles",
        "napoleon",
        "napoléon",
        "francois",
        "françois",
        "francis",
        "philippe",
        "philip",
        "alexandre",
        "alexander",
        "edward",
        "edmund",
        "jean",
        "john",
        "robert",
        "baudouin",
        "baldwin",
        "boniface",
        "dagobert",
        "pius",
        "leo",
        "léon",
        "gregory",
        "grégoire",
        "clement",
        "clément",
        "innocent",
        "benedict",
        "benoît",
        "paul",
        "victor",
        "nicholas",
        "nicolas",
        "frederick",
        "frederic",
        "frédéric",
        "george",
        "william",
        "guillaume",
        "richard",
        "james",
        "ferdinand",
        "peter",
        "pierre",
        "pedro",
        "sancho",
        "ramses",
        "ramesses",
        "alfonso",
        "alphonse",
    }
)

_ROMAN_VALUE: dict[str, int] = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

_ONES_ORDINAL: tuple[str, ...] = (
    "zeroth",
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
    "eleventh",
    "twelfth",
    "thirteenth",
    "fourteenth",
    "fifteenth",
    "sixteenth",
    "seventeenth",
    "eighteenth",
    "nineteenth",
)
_TENS_CARDINAL: dict[int, str] = {20: "twenty", 30: "thirty", 40: "forty"}
_TENS_ORDINAL: dict[int, str] = {20: "twentieth", 30: "thirtieth", 40: "fortieth"}

# A name-stem (letter-initial, may be hyphenated/apostrophed) followed by a
# whitespace and an all-Roman-numeral token bounded on both sides.
_REGNAL_RE = re.compile(r"\b([^\W\d_][\w-]*)\s+([IVXLCDM]+)\b")

# Capital initials joined by a hyphen: ``J.-B.`` -> ``J. B.`` (a bare hyphen
# between initials is voiced as "dash" by some engines). A zero-width lookahead
# for the NEXT initial lets a chain (``A.-B.-C.``) resolve in one pass and keeps
# the rewrite idempotent (the result contains no ``.-`` to re-match).
_INITIALS_HYPHEN_RE = re.compile(r"(?<![A-Za-z])([A-Z])\.-(?=[A-Z]\.)")

# TTS voices a bare em/en-dash as the word "dash", so a clause dash reads as an
# audible "dash" mid-sentence. Rewrite CLAUSE dashes to a comma (a spoken pause):
# an em-dash (U+2014) is always a clause/parenthetical break; a SPACED en-dash
# (U+2013 flanked by whitespace) likewise. A TIGHT en-dash joins a numeric/date
# range and a hyphen (U+002D) joins compounds; both lack flanking spaces and are
# left byte-identical so ranges and compounds still read correctly. Unicode
# escapes (not literal dashes) keep the source unambiguous for the linter.
_EM_DASH = chr(0x2014)
_EN_DASH = chr(0x2013)
_EM_DASH_RE = re.compile(rf"\s*{_EM_DASH}\s*")
_EN_DASH_CLAUSE_RE = re.compile(rf"\s+{_EN_DASH}\s+")


def normalize_dashes_for_reading(text: str) -> str:
    """Replace clause em-dashes and spaced en-dashes with a comma.

    Shared by TTS normalization AND the workbench preview display so what you
    READ matches what you HEAR: the spoken audio pauses (not the word 'dash')
    and the printed narration is not dash-riddled (the tourist complaint). Tight
    numeric/date ranges (12 to 14) and hyphen compounds keep their stroke.
    """
    text = _EM_DASH_RE.sub(", ", text)
    text = _EN_DASH_CLAUSE_RE.sub(", ", text)
    return text


def _int_to_roman(n: int) -> str:
    """Canonical Roman numeral for 1..3999 (used to reject malformed input)."""
    table = (
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    )
    out: list[str] = []
    for value, sym in table:
        while n >= value:
            out.append(sym)
            n -= value
    return "".join(out)


def _roman_to_int(token: str) -> int | None:
    """Parse a Roman numeral, returning None unless it is the CANONICAL form.

    Rejects malformed strings like ``IIII`` or ``VX`` (which parse leniently but
    are not real regnal numbers) by round-tripping through :func:`_int_to_roman`.
    """
    total = 0
    prev = 0
    for ch in reversed(token):
        value = _ROMAN_VALUE[ch]  # token is all-Roman by the regex class
        if value < prev:
            total -= value
        else:
            total += value
            prev = value
    if total <= 0 or _int_to_roman(total) != token:
        return None
    return total


def _ordinal_word(n: int) -> str:
    """English ordinal words for 1..40 (regnal numbers never exceed this)."""
    if n < 20:
        return _ONES_ORDINAL[n]
    if n % 10 == 0:
        return _TENS_ORDINAL[n]
    return f"{_TENS_CARDINAL[(n // 10) * 10]}-{_ONES_ORDINAL[n % 10]}"


def _regnal_sub(match: re.Match[str]) -> str:
    name, roman = match.group(1), match.group(2)
    # Must be a proper noun (capitalized) AND a known sovereign/pope stem.
    if not name[:1].isupper() or name.lower() not in _REGNAL_NAMES:
        return match.group(0)
    n = _roman_to_int(roman)
    if n is None or not 1 <= n <= 40:
        return match.group(0)
    return f"{name} the {_ordinal_word(n)}"


def normalize_for_tts(text: str) -> str:
    """Rewrite TTS-mispronounced tokens to plain English. Idempotent.

    - ``Louis XIV`` -> ``Louis the fourteenth`` (regnal/papal numerals only)
    - ``J.-B.`` -> ``J. B.`` (hyphen-joined initials)
    - clause em-dash / spaced en-dash -> comma (a spoken pause, not "dash");
      tight numeric/date ranges and hyphenated compounds are preserved.

    All other text — including ``World War II``, ``Chapter IV`` and the pronoun
    ``I`` — is returned byte-identical.
    """
    if not text:
        return text
    text = _REGNAL_RE.sub(_regnal_sub, text)
    text = _INITIALS_HYPHEN_RE.sub(r"\1. ", text)
    text = normalize_dashes_for_reading(text)
    return text


__all__ = ["normalize_for_tts"]
