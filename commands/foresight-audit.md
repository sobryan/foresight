---
description: Validate existing regression entries — replayable? sound? grouped? (CI-friendly)
argument-hint: [--all-projects] [--verbose]
---

Run the deterministic corpus audit (no LLM in the loop, safe for CI):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foresight.py" audit --project "$(pwd)" $ARGUMENTS
```

(Use `--all-projects` to audit every project hindsight discovers instead of just the current one.)

The command writes `<project>/.tdd/foresight/audit/audit.json` + `audit.md` and **exits non-zero if any entry has an error-severity finding** — most importantly `NO_RUN_COMMAND`, which means hindsight returns `no_run_command` and the entry never actually runs.

Present a short summary:

1. The corpus **health score** and how many entries are **not replayable** out of the total.
2. The not-replayable entries first, each with the precise fix (e.g. the `run_command` it's missing).
3. Then warnings (stale test paths, invalid priority/feature, duplicate slugs) grouped by entry.
4. Mention that `info` findings (no feature, default priority, never run) feed `/foresight-reorg`.

If everything is clean, say so and report the exit code 0.
