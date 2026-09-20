# Bounded Review

The reviewer is read-only and emits an immutable evidence envelope. The router alone records dispositions and changes state.

Review only:

1. Charter and slice criteria.
2. Regressions against the sealed baseline.
3. Safety: security, privacy, destructive data change, compliance, availability, credentials, and irreversible spend.
4. Dead production code, duplicate authority, consumer drift, and incomplete retirement.

Return no more than five findings. Each must contain a reproducer or named evidence artifact and one disposition:

- `BLOCK`: proven breach.
- `EVIDENCE_REQUIRED`: exactly one bounded probe remains.
- `OUTSIDE_CONTRACT`: no current criterion is implicated; discard it.

Do not suggest unrelated improvements. Do not create tickets. A disputed charter criterion goes to its predeclared oracle and never defaults to discard.
