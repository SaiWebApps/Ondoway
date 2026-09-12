"""The scripted Guggenheim job the slice-7 runner tests share.

One Lonely Planet unit (the real chunk-07 Upper East Side passage, copied
into a per-test chunk dir with a manifest that names only it), one story
at the Guggenheim, and the answers a CORRECT model would give at every
phase — as `llm.MockAnswer`s keyed the way `llm.MockClient` draws them
(`answers` per role in call order for the sync phases P2/P4/P6,
`batch_answers` per custom_id for the batch phases P1/P3/P5). The mock
never fabricates, so every answer the runner will draw is spelled here.

Shared by tests/test_ingest_run.py, tests/test_ingest_routes.py and the
workbench proof in tests/test_workbench_ui.py (the last two through
`write_script`, the JSON form `src.ingest.run.client_from_env` loads).
A test helper, never product code.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from src.ingest import judge_claims, judge_narration, llm, model, narrate
from src.ingest import unit as unit_mod

REPO_ROOT = Path(__file__).resolve().parent.parent
CITY = "new_york"
LP_SOURCE = "lonely-planet-new-york-city"
LP_CHUNK = "chunk-07-upper-east-side"
REAL_CHUNK = REPO_ROOT / "Books" / CITY / LP_SOURCE / f"{LP_CHUNK}.txt"
PUBLISHER = "Lonely Planet"

#: The poi-raw.json name, so `gates.resolve_place` resolves it (exact match).
PLACE = "Solomon R. Guggenheim Museum"
STORY_TITLE = "How the museum came to be"
STORY_SLUG = model.slug(STORY_TITLE)
BEAT_ID = f"{CITY}/{model.slug(PLACE)}/{STORY_SLUG}"

#: Model ids as the RESPONSE reports them — distinct from the configured
#: ROLE_MODEL entries so a test can tell a stamp from a config copy.
RESPONSE_AUTHOR_MODEL = "claude-opus-5-20260601"
RESPONSE_JUDGE_MODEL = "claude-haiku-4-5-20251001"
RESPONSE_MERGE_MODEL = "claude-sonnet-5-20260601"

# ── The origins story's claims, spans verbatim in chunk-07 ─────────────────
COLLECTING: dict = {
    "text": (
        "Solomon R. Guggenheim began collecting abstract art in his sixties, "
        "urged on by his adviser, the German baroness Hilla Rebay."
    ),
    "kind": "event",
    "span": (
        "a New York mining magnate who began acquiring abstract art in his 60s "
        "at the behest of his art adviser, an eccentric German baroness named Hilla Rebay"
    ),
}
OPENED_1939: dict = {
    "text": (
        "Solomon Guggenheim's temporary museum, run by Hilla Rebay, opened on "
        "54th Street in 1939 under the name Museum of Non-Objective Painting."
    ),
    "kind": "event",
    "span": (
        "In 1939, with Rebay serving as director, Guggenheim opened a temporary "
        "museum on 54th St titled the Museum of Non-Objective Painting."
    ),
}
COMPLETED: dict = {
    "text": (
        "The Guggenheim building was finished in 1959, by which time both "
        "Frank Lloyd Wright and Solomon Guggenheim were dead."
    ),
    "kind": "event",
    "span": (
        "Construction was finally completed in 1959 – after both "  # noqa: RUF001
        "Wright and Guggenheim had passed away."
    ),
}
CLAIMS: list[dict] = [COLLECTING, OPENED_1939, COMPLETED]

#: A fact the three claims above leave out — the omission the judge may
#: find; its span crosses a line break in the chunk (whitespace-folded).
TICKET: dict = {
    "text": "When the Guggenheim opened in October 1959, a ticket cost fifty cents.",
    "kind": "event",
    "span": "When the Guggenheim opened its doors in October 1959, the ticket price was 50¢",
}
TICKET_SENTENCE = "A ticket cost fifty cents when the doors opened in October 1959."

#: Three sentences, one per claim, no eight-word run with any span, no
#: leak, no framing verb — passes `narrate.narration_gates` as written.
NARRATION = (
    "Solomon Guggenheim started buying abstract art late in life on the advice "
    "of Hilla Rebay. "
    "In 1939 Hilla Rebay ran a temporary museum for him on 54th Street, called the "
    "Museum of Non-Objective Painting. "
    "The building was finished in 1959, by which time both Wright and Guggenheim had died."
)

# ── Two beats the corpus already holds at the place (a prior job's) ─────────
ADDRESS: dict = {
    "text": (
        "The Solomon R. Guggenheim Museum stands at 1071 Fifth Avenue on the "
        "corner of East 89th Street."
    ),
    "kind": "state",
    "span": "1071 Fifth Ave, at E 89th St",
}
HOURS_2023: dict = {
    "text": "Pay-what-you-wish admission at the Guggenheim runs from 5pm to 8pm on Saturdays.",
    "kind": "state",
    "span": "pay-what-you-wish 5-8pm Sat",
}
ORIGINS_ID = f"{CITY}/{model.slug(PLACE)}/how-the-museum-came-to-be"
VISITING_ID = f"{CITY}/{model.slug(PLACE)}/visiting-the-guggenheim"
ORIGINS_NARRATION = (
    "Solomon Guggenheim started buying abstract art late in life on the advice "
    "of Hilla Rebay. The building was finished in 1959, by which time both "
    "Wright and Guggenheim had died."
)
VISITING_NARRATION = (
    "The museum stands at 1071 Fifth Avenue, on the corner of East 89th Street. "
    "On Saturday evenings from five to eight you pay what you wish."
)

#: What the author writes for the origins beat once the 1939 claim joins
#: it (file order: collecting, completed, then the appended 1939 claim).
RERUN_NARRATION = (
    ORIGINS_NARRATION + " "
    "In 1939 Hilla Rebay ran a temporary museum for him on 54th Street, called the "
    "Museum of Non-Objective Painting."
)

ENRICHMENT: dict = {
    "physical_cues": [],
    "entities": ["Solomon R. Guggenheim", "Hilla Rebay", "Frank Lloyd Wright"],
    "narrative_function": "establishing",
    "emotional_register": "neutral",
    "sensory_anchor": False,
    "inline_foreign_phrases": [],
    "pronunciation": "",
    "kid_friendly": "yes",
    "sub_location": "",
    "trigger_address": "",
}


def _lp_claim(claim_id: str, item: dict) -> dict:
    """A resolved, single-source Lonely Planet claim as it sits on disk."""
    return {
        "claim_id": claim_id,
        "text": item["text"],
        "kind": item["kind"],
        "status": "resolved",
        "sources": [
            {
                "source_id": LP_SOURCE,
                "chunk": LP_CHUNK,
                "span": item["span"],
                "as_of": 2023,
                "rights_basis": "owned_copy",
                "stated_value": None,
            }
        ],
        "resolved_value": None,
        "resolution": None,
        "verdict": {
            "judge_model": RESPONSE_JUDGE_MODEL,
            "entailed": True,
            "bound_to": model.bind(item["text"], item["span"]),
        },
    }


def _beat(beat_id: str, story_slug: str, title: str, beat_type: str, claims: list[dict],
          narration_text: str) -> dict:
    return {
        "beat_id": beat_id,
        "city_name": CITY,
        "poi_name": PLACE,
        "story_slug": story_slug,
        "title": title,
        "beat_type": beat_type,
        "lenses": ["hidden_history"],
        "sub_location": None,
        "trigger_address": None,
        "claims": claims,
        "narration": {
            "text": narration_text,
            "claims_hash": model.claims_hash(claims),
            "author_model": RESPONSE_AUTHOR_MODEL,
            "verdict": {
                "judge_model": RESPONSE_JUDGE_MODEL,
                "sentences_entailed": 2,
                "sentences_total": 2,
                "bound_to": model.bind(narration_text),
            },
            "flags": [],
        },
        "physical_cues": [],
        "entities": [],
        "narrative_function": None,
        "emotional_register": None,
        "sensory_anchor": False,
        "inline_foreign_phrases": [],
        "pronunciation": None,
        "kid_friendly": None,
        "duration_sec": narrate.duration_sec(narration_text),
        "review": {"held": False, "reason": None},
    }


def existing_records() -> list[dict]:
    """Two Lonely Planet beats at the place, valid on disk: the origins
    story (collecting + completed — no 1939 claim) and a stop orientation."""
    origins = _beat(
        ORIGINS_ID, "how-the-museum-came-to-be", STORY_TITLE, "anecdote",
        [_lp_claim("c01", COLLECTING), _lp_claim("c02", COMPLETED)], ORIGINS_NARRATION,
    )
    visiting = _beat(
        VISITING_ID, "visiting-the-guggenheim", "Visiting the Guggenheim", "stop_orientation",
        [_lp_claim("c01", ADDRESS), _lp_claim("c02", HOURS_2023)], VISITING_NARRATION,
    )
    records = [origins, visiting]
    assert model.validate(records) == []
    return records


def seed_beats(data_root: Path, records: list[dict]) -> Path:
    path = data_root / CITY / "beats.json"
    path.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def chunk_dir(root: Path, *, publisher: str | None = PUBLISHER) -> Path:
    """`{root}/Books/new_york/lonely-planet-new-york-city/` holding the real
    chunk-07 byte-for-byte and a manifest naming only it. Its parent is the
    city's chunks root, the shape `model.validate(chunks_root=)` reads."""
    target = root / "Books" / CITY / LP_SOURCE
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REAL_CHUNK, target / f"{LP_CHUNK}.txt")
    manifest: dict[str, Any] = {
        "book_title": "Lonely Planet New York City (12th edition)",
        "author": "Ali Lemer et al.",
        "city": CITY,
        "chunks": [
            {
                "chunk_number": 7,
                "filename": f"{LP_CHUNK}.txt",
                "section_title": "Upper East Side",
            }
        ],
    }
    if publisher is not None:
        manifest["publisher"] = publisher
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return target


def data_dir(root: Path) -> Path:
    """`{root}/data/` holding the city's real poi-raw.json (so P2's place
    resolution sees the real POI names) and nothing else — no beats file,
    so a first job finds no beat at any place."""
    city_dir = root / "data" / CITY
    city_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO_ROOT / "data" / CITY / "poi-raw.json", city_dir / "poi-raw.json")
    return root / "data"


def unit(chunks: Path, *, as_of: int = 2023, rights_basis: str = "owned_copy") -> unit_mod.Unit:
    """The one unit a job over `chunk_dir(...)` intakes."""
    return unit_mod.load_unit(
        chunks.parent, city=CITY, source_id=LP_SOURCE, chunk=LP_CHUNK,
        as_of=as_of, rights_basis=rights_basis,
    )


def _author(payload: dict) -> llm.MockAnswer:
    return llm.MockAnswer(text=json.dumps(payload), model_id=RESPONSE_AUTHOR_MODEL)


def _judge(payload: dict) -> llm.MockAnswer:
    return llm.MockAnswer(text=json.dumps(payload), model_id=RESPONSE_JUDGE_MODEL)


def claims_answer(claims: list[dict] = CLAIMS) -> llm.MockAnswer:
    return _author({"claims": [dict(c) for c in claims]})


def stories_answer(*, place: str = PLACE, claim_ids: list[str] | None = None) -> llm.MockAnswer:
    ids = claim_ids or [f"c{i:02d}" for i in range(1, len(CLAIMS) + 1)]
    return _author(
        {
            "stories": [
                {
                    "title": STORY_TITLE,
                    "place": place,
                    "beat_type": "anecdote",
                    "lenses": ["hidden_history", "visual_art"],
                    "claim_ids": ids,
                    "enrichment": ENRICHMENT,
                }
            ]
        }
    )


#: The kind a CORRECT judge reads for each scripted claim, by id (c01-c03
#: are CLAIMS in order; c04 is the TICKET claim the omission re-ask adds).
KINDS: dict[str, str] = {
    **{f"c{i:02d}": c["kind"] for i, c in enumerate(CLAIMS, 1)},
    "c04": TICKET["kind"],
}


def verdicts(
    u: unit_mod.Unit, claim_ids: list[str], attempt: int = 1, kinds: dict[str, str] | None = None
) -> dict[str, llm.MockAnswer]:
    """Entailed verdicts whose kind AGREES with the author's (`KINDS`, or
    `kinds` per id), so no claim is re-kinded on the happy path."""
    return {
        judge_claims.judge_custom_id(u, cid, attempt): _judge(
            {"entailed": True, "reason": "the span states it", "kind": (kinds or KINDS)[cid]}
        )
        for cid in claim_ids
    }


def omissions_answer(u: unit_mod.Unit, findings: list[dict] = ()) -> dict[str, llm.MockAnswer]:
    return {judge_claims.omissions_custom_id(u): _judge({"omitted": list(findings)})}


def narration_answer(text: str = NARRATION) -> llm.MockAnswer:
    return _author({"narration": text})


def sentence_verdicts(
    claim_texts: list[str],
    text: str = NARRATION,
    *,
    attempt: int = 1,
    refused: dict[int, str] | None = None,
) -> dict[str, llm.MockAnswer]:
    """One verdict per sentence of `text`, keyed the way P5 keys them: by
    the claims_hash P4 stamps on the draft and the attempt. `refused`
    maps a sentence index to the judge's reason for refusing it."""
    refused = refused or {}
    draft = narrate.NarrationDraft(
        text=text,
        claims_hash=model.claims_hash([{"text": t, "status": "resolved"} for t in claim_texts]),
        author_model=RESPONSE_AUTHOR_MODEL,
        duration_sec=narrate.duration_sec(text),
    )
    return {
        judge_narration.sentence_custom_id(draft, i, attempt): _judge(
            {"entailed": False, "reason": refused[i]}
            if i in refused
            else {"entailed": True, "reason": "a claim states it"}
        )
        for i in range(len(judge_narration.sentences(text)))
    }


def merge_answer(story: str, beat_id: str, claims: list[dict]) -> llm.MockAnswer:
    return llm.MockAnswer(
        text=json.dumps({"story": story, "beat_id": beat_id, "claims": claims}),
        model_id=RESPONSE_MERGE_MODEL,
    )


def script(u: unit_mod.Unit, *, place: str = PLACE) -> dict[str, Any]:
    """The whole happy-path script for one job over `u`: kwargs for
    `llm.MockClient(events, **script(u))`."""
    claim_ids = [f"c{i:02d}" for i in range(1, len(CLAIMS) + 1)]
    return {
        "answers": {"author": [stories_answer(place=place), narration_answer()]},
        "batch_answers": {
            u.custom_id(1): claims_answer(),
            **verdicts(u, claim_ids),
            **omissions_answer(u),
            **sentence_verdicts([c["text"] for c in CLAIMS]),
        },
    }


def _answer_to_json(answer: llm.MockAnswer) -> dict:
    return {"text": answer.text, "model_id": answer.model_id, "stop_reason": answer.stop_reason}


def write_script(path: Path, scripted: dict[str, Any]) -> Path:
    """Serialize a `script(...)` dict to the JSON form the mock client
    factory in src.ingest.run reads (INGEST_MOCK_SCRIPT)."""
    payload = {
        "answers": {
            role: [_answer_to_json(a) for a in answers]
            for role, answers in scripted.get("answers", {}).items()
        },
        "batch_answers": {
            cid: _answer_to_json(a) for cid, a in scripted.get("batch_answers", {}).items()
        },
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
