---
description: Turn the top coverage gaps into ready-to-run regression proposals for /tdd
argument-hint: [N | gap-id]
---

Argument: $ARGUMENTS  (a count N — default 3 — or a specific gap id)

Generate new-regression **proposals** for the highest-value uncovered gaps:

1. Ensure coverage + audit are current (run `${CLAUDE_PLUGIN_ROOT}/scripts/foresight.py coverage` and `audit` if `coverage/coverage.json` / `audit/audit.json` are missing or stale).
2. Invoke the `foresight-auditor` sub-agent to confirm the gaps are real (filter the matcher's false "uncovered" results) and shortlist by risk.
3. Invoke the `foresight-architect` sub-agent to write each proposal under `<project>/.tdd/foresight/proposals/<slug>/` as `task.md`, `test_plan.md`, a draft `replay.json` (with a **real `run_command`** from the inventory's detected test command, plus a proposed `priority`/`feature`), and `EVIDENCE.md`.

Proposals are written in iterative-tdd's regression format and are **never** placed in `.tdd/regression/`. Present, for each proposal: the slug, the one-line intent, the gap it closes, and its proposed priority/feature.

Finish by telling the user how to promote a proposal into a real, hindsight-replayable regression:

```text
/tdd <the task from proposals/<slug>/task.md>
```
