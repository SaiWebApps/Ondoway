---
description: Shape or deliver one bounded Ondoway change through the shared Claude/Codex workflow.
argument-hint: "<request, charter path, or run directory>"
---

Use the project skill at `.agents/skills/team/SKILL.md` for `$ARGUMENTS`.

The skill is the decision guide. `.agents/team/teamflow.py` is the sole authority for validation, leases, evidence, and state transitions. Read only the skill reference needed for the current work type.

Before proposing implementation, run:

```sh
python3 .agents/team/teamflow.py ownership inspect --query "$ARGUMENTS"
```

If `$ARGUMENTS` identifies an existing run, report its status and perform only its next legal action. Otherwise shape a charter and present its hash for approval. Never begin mutation without an approved charter, a ready slice, and an acquired lease.

One runtime writes. Other agents, if used, are read-only. A reviewer may reject a broken promise but may not add a promise. Finish with the skill's four-line owner report.
