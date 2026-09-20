# Delivery

Select one archetype:

- `CAPABILITY`: vertical observable behavior and end-to-end proof.
- `REPRODUCER_FIX`: failing reproducer, root cause, correction, regression proof.
- `CHARACTERIZATION_REFACTOR`: captured invariants and unchanged external behavior.
- `MIGRATION_DATA`: dry run, idempotency, reconciliation, backup, rollback.
- `UI_PROOF`: interaction, accessibility, risk-scaled render/device evidence.
- `DEPLOYMENT_CANARY`: pinned artifact, health thresholds, observation, rollback.
- `DEPENDENCY_PROBE`: bounded feasibility/auth/quota/schema evidence.

Write the red test at the canonical behavior seam. Acquire the slot lease, implement only the slot, attach evidence, and advance through the shared transition CLI.

Before candidate review, run:

```sh
python3 .agents/team/teamflow.py verify reachability
python3 .agents/team/teamflow.py verify single-authority
python3 .agents/team/teamflow.py verify retirement --charter <charter.json>
```

If reality changes implementation details, update working notes. If it changes an outcome, invariant, owner, risk, or acceptance meaning, stop with `CONTRACT_INVALID` or request `OWNER_CHANGE`.
