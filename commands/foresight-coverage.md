---
description: Build the coverage map and risk-ranked gap report
argument-hint: [--fail-on-gap] [--all-projects]
---

Build the coverage map from the inventory (if present) + the existing regression entries:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foresight.py" coverage --project "$(pwd)" $ARGUMENTS
```

Writes `<project>/.tdd/foresight/coverage/coverage.json` + `gaps.md`. With `--fail-on-gap` it exits non-zero when a high-risk (≥5) use case is uncovered — useful as a CI gate once an inventory exists.

Present:

1. The counts — covered / partial / uncovered (of total) — when an inventory is present.
2. The **risk-ranked gaps**, highest first, each with its id, feature, risk score, and (if explored) the screenshot evidence.
3. For each `covered` item, the regression slug(s) that cover it, so the result is explainable.

If there's no `inventory.json` yet, the command emits a coarse static signal (source areas with no regression reference) — tell the user to run `/foresight` (which builds the inventory via exploration) for per-use-case coverage, then present the static signal.

After presenting gaps, offer `/foresight-propose` to turn the top gaps into ready-to-run regression proposals.
