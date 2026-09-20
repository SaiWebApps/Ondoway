# One Authoritative Implementation

The capability map in `.agents/team/ownership.json` names the canonical owner of each shared behavior.

Before coding:

1. Inspect the capability map and CodeGraph result.
2. Choose `EXTEND_EXISTING`, `EXTRACT_SHARED`, `REPLACE`, or `NEW_CAPABILITY`.
3. Name every consumer and adapter.
4. For replacement, name the old path and its removal step.

Routes, workbench code, mobile services, CLIs, and jobs may adapt authentication, transport, serialization, and presentation. They may not repeat domain decisions, constants, normalization, provider fallback, persistence meaning, or error classification.

Allowed duplicate shapes are presentation, thin adapters, generated code, fixtures, independent contract assertions, and approved compatibility overlap. Clone detection is a warning for these categories until the slicecard classifies them; an unclassified clone blocks.

Code reachable only from tests is dead. Dynamically registered production entrypoints need an explicit registry receipt. Dormant code needs a flag, activation slot, owner, expiry, and evidence that it is neutral while dormant.
