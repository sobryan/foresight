---
description: Explore the product, find untested regressions, and audit/organize the ones you have
argument-hint: [target-url-or-app] [--platforms web,android,ios] [--no-explore] [--apply-reorg] [--propose N]
---

Arguments: $ARGUMENTS

Invoke the **foresight** skill to run the full workflow against the current project:

1. Initialize `.tdd/foresight/`.
2. Static discovery (`foresight-cartographer`) → `inventory.json`.
3. Live exploration of the target (`foresight-web-explorer` / `foresight-mobile-explorer`) → screenshots + UI maps, enriching the inventory. Skipped if `--no-explore` or no target is given.
4. Coverage analysis → `coverage/gaps.md` (risk-ranked).
5. Corpus audit → `audit/audit.md` (flags entries hindsight can't replay).
6. Reorg plan for hindsight → `reorg/reorg.md` (apply only if `--apply-reorg` or the user approves).
7. Proposals for the top `--propose N` gaps (default 3) → `proposals/<slug>/`, in iterative-tdd format.
8. Assemble `report.md` and give a short chat summary.

Parse from `$ARGUMENTS`: a leading non-flag token is the exploration **target** (URL or app id); `--platforms` selects which platforms to explore; `--no-explore` does static-only; `--apply-reorg` writes the reorg back to `replay.json`; `--propose N` sets the proposal count. Default to `--project "$(pwd)"`.

Follow the skill's phase order exactly; delegate the reading/exploring/judging to the scoped sub-agents and use `${CLAUDE_PLUGIN_ROOT}/scripts/foresight.py` for the deterministic steps.
