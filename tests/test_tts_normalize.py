"""Unit tests for TTS pronunciation normalization.

The normalizer runs ONLY on TTS-consumed text (inside the real providers'
``generate``), never on the stored corpus. It rewrites tokens a neural TTS voices
as letters/symbols into the plain English a listener expects to hear:

- regnal / papal Roman numerals: ``Louis XIV`` -> ``Louis the fourteenth``
- initials joined by a hyphen: ``J.-B.`` -> ``J. B.``

Correctness matters less than RESTRAINT: the corpus is a Paris-history text full
of ``World War II``, the pronoun ``I``, ``Chapter IV`` and ``Vatican II`` — none
of which may be touched. A curated regnal-name allow-list gates every rewrite.
"""

from __future__ import annotations

import pytest

from src.audio.tts_normalize import normalize_for_tts


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # ── regnal / papal numerals -> ordinal words ──
        ("Louis XIV was the Sun King.", "Louis the fourteenth was the Sun King."),
        ("Napoleon III rebuilt Paris.", "Napoleon the third rebuilt Paris."),
        ("Henri IV signed the edict.", "Henri the fourth signed the edict."),
        ("Charles V ruled an empire.", "Charles the fifth ruled an empire."),
        ("Louis VI the Fat.", "Louis the sixth the Fat."),
        ("Pope John XXIII opened the council.", "Pope John the twenty-third opened the council."),
        ("Napoléon Ier reigned briefly.", "Napoléon Ier reigned briefly."),  # 'Ier' is not roman
        # possessive + sentence-final punctuation must survive
        ("Louis XVI's head fell.", "Louis the sixteenth's head fell."),
        ("It ended under Louis XIV.", "It ended under Louis the fourteenth."),
        ("Louis I founded the line.", "Louis the first founded the line."),
        # hyphenated regnal stem
        ("Saint-Louis IX was canonized.", "Saint-Louis the ninth was canonized."),
        # ── initials joined by a hyphen (incl. chains) ──
        ("The architect J.-B. Colbert.", "The architect J. B. Colbert."),
        ("Read W.-E.-B. Du Bois.", "Read W. E. B. Du Bois."),
        # ── newly covered pope / emperor stems (skeptic-C corpus misses) ──
        ("Pope Boniface VIII issued the bull.", "Pope Boniface the eighth issued the bull."),
        ("Baldwin II sold the relics.", "Baldwin the second sold the relics."),
        # ── FALSE-POSITIVE GUARDS (must be left byte-identical) ──
        ("The Second World War II era.", "The Second World War II era."),  # 'War' not regnal
        ("Everyone whom I met agreed.", "Everyone whom I met agreed."),  # pronoun I
        ("Vatican II reformed the mass.", "Vatican II reformed the mass."),  # council, not regnal
        ("See Chapter IV for details.", "See Chapter IV for details."),  # 'Chapter' not regnal
        ("Room XIV is upstairs.", "Room XIV is upstairs."),  # 'Room' not regnal
        ("A plain sentence with no numerals.", "A plain sentence with no numerals."),
        ("", ""),
    ],
)
def test_normalize_cases(raw: str, expected: str) -> None:
    assert normalize_for_tts(raw) == expected


def test_idempotent() -> None:
    once = normalize_for_tts("Louis XIV met Napoleon III near J.-B. Colbert's atelier.")
    assert normalize_for_tts(once) == once


def test_chained_initials_idempotent() -> None:
    # Skeptic-A finding: a chain must fully resolve AND be idempotent.
    once = normalize_for_tts("A.-B.-C. Smith wrote it.")
    assert once == "A. B. C. Smith wrote it."
    assert normalize_for_tts(once) == once


def test_malformed_roman_left_alone() -> None:
    # 'IIII' / 'VX' are not canonical Roman numerals -> never rewritten.
    assert normalize_for_tts("Louis IIII imagined.") == "Louis IIII imagined."
    assert normalize_for_tts("Charles VX imagined.") == "Charles VX imagined."


def test_high_regnal_number() -> None:
    assert normalize_for_tts("Ramses II built it.") == "Ramses the second built it."
    assert normalize_for_tts("Pius XII reigned.") == "Pius the twelfth reigned."


def test_clause_em_dash_becomes_comma() -> None:
    # A bare em-dash is voiced as "dash"; a clause em-dash → comma (a spoken pause).
    out = normalize_for_tts("head for the Meurice — about a 5-minute walk.")
    assert "—" not in out
    assert out == "head for the Meurice, about a 5-minute walk."
    # No flanking spaces still folds.
    assert (
        normalize_for_tts("the grandest hotel—colonised the quarter")
        == "the grandest hotel, colonised the quarter"
    )
    # Idempotent.
    assert normalize_for_tts(out) == out


def test_spaced_en_dash_becomes_comma_but_ranges_preserved() -> None:
    # A SPACED en-dash is a clause break -> comma; a TIGHT numeric/date range is NOT
    # comma-swapped (no "1615, 1630") and must still read as a range.
    en = chr(0x2013)  # en-dash, built via chr to avoid an ambiguous source literal
    assert normalize_for_tts(f"open May {en} September") == "open May, September"
    for rng in (f"built 1615{en}1630", f"at 57{en}59 rue de Rivoli", f"the 1912{en}14 works"):
        assert normalize_for_tts(rng) == rng, f"range corrupted: {rng!r}"


def test_hyphenated_compounds_survive_byte_identical() -> None:
    # A hyphen (U+002D) joins compounds/ordinals — never touched by the dash rule.
    for s in (
        "an 8-minute walk",
        "Saint-Germain-des-Prés",
        "the twenty-third arrondissement",
        "Jean-Baptiste Colbert",
    ):
        assert normalize_for_tts(s) == s


def test_normalize_dashes_for_reading_shared_helper():
    from src.audio.tts_normalize import normalize_dashes_for_reading
    em, en = chr(0x2014), chr(0x2013)
    assert normalize_dashes_for_reading(f"A{em}B") == "A, B"
    assert normalize_dashes_for_reading(f"1940 {en} 1944 it fell") == "1940, 1944 it fell"
    tight = f"the 1940{en}44 range"
    assert normalize_dashes_for_reading(tight) == tight  # tight range kept
