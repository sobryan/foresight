---
description: Propose (and optionally apply) priority/feature/serial grouping for hindsight
argument-hint: [--apply] [--all-projects]
---

Propose how every regression entry should be prioritized and grouped so hindsight's sorted, feature-filtered, parallel sweeps are meaningful:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foresight.py" reorg --project "$(pwd)" $ARGUMENTS
```

Writes `<project>/.tdd/foresight/reorg/reorg.json` + `reorg.md`. For higher-quality results, invoke the `foresight-architect` sub-agent to review and correct the deterministic keyword-based suggestions against the actual code before presenting.

Present:

1. Whether a **reorg is needed** and why (e.g. everything still default `normal`, or one feature bucket holds most of the corpus).
2. The proposed `feature` buckets and their sizes.
3. The per-entry changes — `priority`, `feature`, `serial` — current → suggested.

**Applying.** Only when the user passes `--apply` (or approves after seeing the plan): the command writes the suggested `priority`/`feature`/`serial` back into each `replay.json`. This is backwards-compatible (only those keys are touched, every other key preserved), backs the original up to `replay.json.bak`, and is idempotent. After applying, note that hindsight will now sort/group/parallelize using the improved metadata.
