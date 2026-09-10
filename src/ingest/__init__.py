"""The ingest package — the rebuilt content pipeline (Docs/ingestion/rebuild-spec.md).

Built incrementally, one atomic record-model step at a time. The first
step lands src.ingest.model: the record's pydantic shape, its hashes,
slug, and shape (new vs legacy) detection.

A later step adds src.ingest.llm: the model-client seam
(roles, phases, prices, the ModelClient Protocol, and its two
implementations — MockClient for tests, AnthropicClient for the real
Opus/Sonnet/Haiku calls) that later slices use to run the extraction and
judging phases. Prompt text, phase logic, and answer parsing are out of
scope for this slice (Docs/ingestion/rebuild-spec.md phases land
separately).
"""
