# Release and External Mutation

Classify every mutable resource before approval:

- `LOCAL_RECOVERABLE`: lease plus sealed baseline and diff reconciliation.
- `FENCED_EXTERNAL`: every write carries a fencing generation or native equivalent and idempotency key.
- `UNFENCEABLE_EXTERNAL`: supervised exclusive window, snapshots, reconciliation, and recovery authority.

Unfenceable production, destructive, payment, email, or irreversible-cost operations never run unattended.

Build one aggregate manifest containing the source ref, artifacts, locks, configuration, data/schema versions, feature flags, dependency evidence with expiry, and exact targets. `RELEASING` covers deployment, canary, observation, reconciliation, and recovery. `RELEASED` requires all charter evidence against that aggregate.

Historical release records are immutable. Operational conformance is `CURRENT`, `STALE`, or `ROLLED_BACK`; fresh evidence may restore an identical aggregate from `STALE` to `CURRENT`.
