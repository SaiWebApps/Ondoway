# Phase Charter

The charter freezes the promise and its proof, not an implementation plan.

Populate every required field in `.agents/team/schema.json`; use `N/A: <reason>` where a category genuinely does not apply. A criterion is either:

- `MECHANICAL`: exact command or artifact plus a literal threshold.
- `JUDGMENT`: named oracle, permitted evidence, tie-break authority, timeout, and unavailable disposition.

Each immutable slot names one archetype, its linked criteria, bounded attempts, change intent, behavior owner, consumers, and the CodeGraph search used to find existing implementations.

Create each run at `.teamflow/runs/<phase-id>-v<version>`. `teamflow shape` records that run as the repository's active run so the mutation guard can enforce its lease; terminal transitions remove the pointer.

Approve the initial slots with the charter. A separate slot approval is required only for production, destructive, security/privacy, irreversible spend, public contract changes, migrations without rehearsed rollback, risks above the charter threshold, or slots the owner explicitly names.

Use `CONTRACT_INVALID` when evidence proves the charter contradictory or infeasible. Use `OWNER_CHANGE` when desired behavior changes. Both close the old version and require a new hash and approval.
