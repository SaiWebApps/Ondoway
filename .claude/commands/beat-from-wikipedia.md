You are a content extraction specialist for the Ondoway audio tour platform. You extract factual narrative beats from **Wikipedia articles** and classify them with structured metadata, to enhance the beat coverage of POIs that already exist in a city's corpus.

Your task: extract and classify narrative beats from the Wikipedia article for a POI in the city of **$ARGUMENTS** (default city: "paris").

The user provides a **POI name** that already exists in `data/{city_slug}/poi-raw.json` (e.g. `/beat-from-wikipedia paris "Saint-Sulpice"`). Optionally they may pass an explicit Wikipedia title to override article resolution (e.g. `--title "Saint-Sulpice, Paris"`).

This skill is for **enhancing existing POIs**. It does NOT discover new POIs from geography — that is a separate concern. If extraction surfaces a clearly distinct sub-zone of the POI, emit a sub-POI exactly as `/unified-beat-extract` does (PHASE 3 Case 3); but do not invent free-standing new POIs from a Wikipedia article's outbound links.

---

## RELATIONSHIP TO `/unified-beat-extract` — READ THIS FIRST

This skill **inherits the entire extraction contract** of `.claude/commands/unified-beat-extract.md`. Everything in that skill applies unchanged unless explicitly overridden below:

- The ZERO HALLUCINATION POLICY (every word traces to the source)
- PHASE 1 chunked reading + multi-granularity (parent / sub_location / address grains)
- PHASE 2 beat generation: atomicity, the source-span gate (B12), length classes, fabrication self-flagging (B11), the `extractor_state` contract and 40% `imported_context` ceiling
- All ENRICHMENT FIELDS (entities, sensory_anchor, narrative_function, beat_type, emotional_register, subject_tag, physical_cues, sub_location, beat_length_class, inline_foreign_phrases, pronunciation)
- The `scripts.beat_builder.make_beat` / `scripts.extract_validators.validate_beat` / `scripts.audit_extraction.audit_chunk` helpers — call them, don't hand-roll
- The atomic `scripts.beats_io.commit` write path and BEAT ID FORMAT
- The full SELF-VERIFICATION checklist and PIPELINE REPORT

**Do not duplicate those rules here. Read that file, apply it, and layer the deltas below on top.** When that skill changes, this skill inherits the change.

The deltas exist because the source is an encyclopedia article, not an authored guidebook.

---

## DELTA 1 — SOURCE ACQUISITION + REVISION PINNING (PHASE 0)

Books are immutable; Wikipedia mutates. A `source_passage` quote that verified today can silently rot when an editor rewrites the paragraph tomorrow. So we **pin the revision that is current at fetch time** (recording its `revid` and saving its exact text) and treat `(article, revision_id)` as the unit of work. We do not fetch a user-specified historical revision; a re-run after an edit pins the new current revision and is handled as an update pass (step 3).

Before any extraction:

1. **Resolve the POI.** Read `data/{city_slug}/poi-raw.json` and find the entry whose `name` (or `name_variations`) matches the user's POI argument. HARD REFUSE if no match — this skill only enhances existing POIs. Capture its `importance_tier`, coordinates, and `name_variations` (you need the tier for the tier-3+ physical-cue rule).

2. **Fetch + pin the article in one approved call.** Run:
   ```
   make wiki-fetch POI="<canonical POI name>" [TITLE="<explicit Wikipedia title>"]
   ```
   This is the ONLY way to acquire the source. It hits the MediaWiki API, follows redirects, pins the current `revid`, saves the raw plain-text extract to `data/{city_slug}/wikipedia/{poi_slug}-rev-<revid>.txt`, and prints a JSON summary. Never fetch Wikipedia with `WebFetch` — WebFetch returns an LLM-*summarised* page, and extracting from a summary (or from memory) is what let a prior Carousel run produce beats that did not trace to their pinned source. `make wiki-fetch` is allow-listed, so it runs without a prompt.

   Read the printed JSON and branch on `status`:
   - `"disambiguation"` → STOP and ask the user for an explicit `--title`, then re-run with `TITLE=`. Never guess past a disambiguation page.
   - `"not_found"` → the title does not resolve; ask the user to confirm the article title.
   - `"ok"` → proceed. Use its fields downstream: `resolved_title`, `revid`, `source_chunk_slug`, `oldid_url`, `retrieved_at`, `saved_path`.

3. **Dedup decision (HARD REFUSE) from the fetch output.** The fetch already scanned EVERY `book_title: "Wikipedia"` log entry (not just the first — the log can hold duplicate Wikipedia entries, so a first-entry-only check would miss a logged chunk). Read its `already_processed` and `prior_revisions` fields:
   - `already_processed: true` → the current revision was already extracted. HARD REFUSE verbatim (read the matching log entry for `<processed_at>` and `<n>`):

     `Refused: Wikipedia "<title>" <poi_slug>-rev-<revid> was processed on <processed_at> (<n> beats extracted). The article has not changed since. Run /beat-wipe <city_slug>/wikipedia --chunk <poi_slug>-rev-<revid> --apply to re-extract, or wait for a newer revision.`

   - `prior_revisions` non-empty (an OLDER `<poi_slug>-rev-*` was processed) → the article changed since; continue — re-extraction at the new revision is the intended path to pick up new content. Note the prior revision in the pipeline report so the user knows this is an update pass. (Old revisions' beats and their stale `-rev-` chunk are left in place; the user wipes the superseded revision explicitly if they want it gone.)

   The single-`"Wikipedia"`-entry design this depends on is load-bearing (see DELTA 3): `/beat-wipe` matches the log entry via `slugify_title(book_title)`, which must equal the beats' `book_slug` (`"wikipedia"`). Keep `book_title` exactly `"Wikipedia"`.

4. **The saved file is the only source of truth.** Extract solely by quoting `saved_path` (`data/{city_slug}/wikipedia/{poi_slug}-rev-<revid>.txt`) — every `source_passage` must appear verbatim in it, and `/fact-check` re-reads it. Section headings are preserved as plain text (structural signals — see DELTA 4). Never quote from your memory of the article or from any other rendering.

---

## DELTA 2 — GENRE DISCIPLINE (TIGHTER THAN BOOKS)

`/unified-beat-extract` tells you to "use the source text's own vivid language and narrative details." Wikipedia has fewer of those — its register is flat and encyclopedic. This creates a specific, dangerous temptation: **to supply the narrative colour the article lacks by reaching into your own training knowledge.** That is fabrication, and it is the #1 failure mode for this source type.

Rules that bind harder here than for books:

- **A flat fact stays a flat fact.** If the article says "the statue was cast from silverware donated by parishioners and was known as 'Our Lady of the Old Tableware'," you may arrange those facts into a complete beat — but you may NOT add the weight of the silver, the names of donors, the emotional reaction of the congregation, or any detail the article does not state. Narrative *arrangement* of sourced facts is allowed; narrative *invention* is not.
- **Run `fabrication_probe` on every beat without exception.** Because Wikipedia is so quotable, the probe's false-positive rate is low and its catches are real. Treat every flagged claim as guilty until you can point at the exact sentence in the saved text file.
- **When the article is thin, the beat is short.** A one-sentence Wikipedia fact is a `seasoning` or `micro` beat, never a `mid` you pad to length. The source-span gate (B12) is not optional.
- **Wikipedia's "Cultural Allusions" / "In popular culture" sections** are legitimate beat material (e.g. Da Vinci Code crowds at Saint-Sulpice, the church's posted refutation) but are prone to trivia-listing. Extract only allusions that carry a self-contained story; skip bare name-drops.
- **Recency is Wikipedia's edge over the books.** The guidebooks in this corpus predate ~2010. Wikipedia carries recent events (2019 fires, post-2010 restorations, recent archaeology) the books cannot. Prioritise material that the existing corpus genuinely lacks — the pipeline report's `new_coverage` section (from `audit_chunk` with `live_beats`) tells you what is already covered.

---

## DELTA 3 — PROVENANCE (`source_attribution` + log)

`source_attribution` is free-form metadata in `beats.json` (it is not in the upload Pydantic model, so its shape is unconstrained). Uploaded provenance is `book_slug` + `source_chunk_slug`. Set them so Wikipedia beats are unambiguous and coexist with book beats:

- `book_slug` = `"wikipedia"` (the identity tuple `(city, poi, lens, book_slug, topic_slug)` then keeps Wikipedia beats from ever colliding with a book beat at the same POI/lens)
- `source_chunk_slug` = `"{poi_slug}-rev-<revid>"`
- `source_attribution` dict shape (override the book shape from `make_beat`):
  ```json
  {
    "source_type": "wikipedia",
    "book_title": "Wikipedia",
    "article_title": "<resolved article title>",
    "url": "https://en.wikipedia.org/wiki/<title>?oldid=<revid>",
    "revision_id": "<revid>",
    "section": "<the article section this beat came from, e.g. 'Interior'>",
    "retrieved_at": "<ISO 8601 fetch time>"
  }
  ```
  Take `article_title`/`url`/`revision_id`/`retrieved_at` straight from the `make wiki-fetch` JSON (`resolved_title`, `oldid_url`, `revid`, `retrieved_at`) so provenance matches the pinned file byte-for-byte. Only `section` is your judgment (which article section the beat came from).
  `book_title: "Wikipedia"` is retained (not dropped for `article_title`) because `/export-validate` groups beats by `source_attribution.book_title` + chunk; without it, Wikipedia beats fall into the catch-all "pipeline-generated" export bucket. The real article identity lives in `article_title` + `url`. The `?oldid=<revid>` URL is a permanent link to the exact revision — `/fact-check` and any human can re-read precisely what the beat was extracted from.

Construct beats with `make_beat` for all the mechanical fields, then overwrite the `source_attribution` key on the returned dict with the Wikipedia shape above. (A small `make_beat` `source_attribution=` override may be added later; until then, overwrite the key after construction.)

**Log update at commit:** there must be exactly ONE `book_title: "Wikipedia"` entry after your write. Before appending: if zero exist, create one (`book_title: "Wikipedia"`, `author: "Wikipedia contributors"`); if more than one exists (a known failure mode — see the dedup note above), **consolidate them into a single entry first**, merging their `chunks_processed` and de-duplicating by `chunk` name. Then append a `chunks_processed` dict whose `chunk` is `<poi_slug>-rev-<revid>` (byte-identical to every emitted beat's `source_chunk_slug`), with `beats_extracted`, `pois_touched`. The authoritative beat-count per chunk is derivable from the committed beats themselves (group `book_slug=="wikipedia"` beats by `source_chunk_slug`) — prefer that over trusting a possibly-stale prior log entry. The chunk name MUST match `source_chunk_slug` exactly: `/beat-wipe` takes one `--chunk` argument and uses it to match BOTH the beats (`find_matching_beats` on `source_chunk_slug`) AND the log entry (`remove_chunk_from_log` on the log's `chunk`). If the two names diverge, wipe removes the beats but orphans the log entry. Go through `scripts.beats_io.commit` exactly as `/unified-beat-extract` does — never write `beats.json` or `book-log.json` directly.

---

## DELTA 4 — STRUCTURE MAPPING + BEAT TYPES THAT DON'T APPLY

Wikipedia articles are structured by **topic section**, not by a walking route. Use that structure:

- **Section headings → sub_location / lens signals.** "Interior", "West Façade", "Organs", "Gnomon", "Crypt" map naturally to `sub_location` values on tier-4/5 POIs. "History", "Construction", "French Revolution" map to lenses (`hidden_history`, `historic_arch`, `war_conflict`). Honor the article's own sectioning the way the book skill honors typographic sub-heads.
- **No walking structure exists in Wikipedia.** Therefore:
  - `beat_type: transit` — **never emit.** Wikipedia has no walking directions between stops.
  - `beat_type: stop_orientation` — **never emit.** Wikipedia has no "stand here, face this" staging.
  - `trigger_address` — almost always `null`. Wikipedia does not circle a square address-by-address. Populate only in the rare case the article itself ties a discrete story to a specific street address.
  - `beat_type: sidebar` — rare; only for a genuinely self-contained tangent (e.g. a "Cultural Allusions" story that stands apart from the main narrative).
- **Establishing beats are welcome.** Wikipedia's lead section is an ideal source for a clean `narrative_function: establishing` beat on a POI that lacks one — useful for filling the establishing-coverage gaps the book corpus left.

Everything else — anchor/mid/seasoning/micro classing, the lens scan, multi-beat-per-lens, sub-POI emergence, physical_cues for visible features — works exactly as in `/unified-beat-extract`.

---

## OUTPUT + FOLLOW-UPS

Identical to `/unified-beat-extract`:

- Commit beats + updated log atomically via `scripts.beats_io.commit`.
- Print the PIPELINE REPORT from `scripts.audit_extraction.audit_chunk(beats=new_beats, chunk_text=<saved wiki text>, poi_index=poi_by_name, live_beats=<existing beats.json>)`. The `new_coverage` section against `live_beats` is the headline metric for this skill — it shows how much novel material Wikipedia added beyond the book corpus.
- Report follow-ups: **always** recommend `/fact-check {city_slug}` on the new beats (Wikipedia claims need the two-source verification the pipeline guardrails require before they are trusted), and `/poi-geocode` / `/poi-dedup` only if sub-POIs were emitted.

## SELF-VERIFICATION — additions to the inherited checklist

In addition to the full `/unified-beat-extract` SELF-VERIFICATION list:

15. **Revision is pinned** — every beat's `source_attribution.revision_id` is set and its `url` carries `?oldid=<revid>`. `source_chunk_slug` ends in `-rev-<revid>`.
16. **`book_slug == "wikipedia"`** on every beat emitted by this skill.
17. **No `transit` or `stop_orientation` beats** were emitted (Wikipedia has no walking structure).
18. **source_passage matches the saved revision file** — every `source_passage` quote appears verbatim in `data/{city_slug}/wikipedia/{poi_slug}-rev-<revid>.txt`, not merely in your memory of the article.
