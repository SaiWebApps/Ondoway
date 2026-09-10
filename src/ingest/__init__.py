"""The ingest package — the rebuilt content pipeline (Docs/ingestion/rebuild-spec.md).

Built incrementally, one atomic record-model step at a time. The first
step lands src.ingest.model: the record's pydantic shape, its hashes,
slug, and shape (new vs legacy) detection.
"""
