---
description: Explore the product, inspect it visually, find untested regressions, and audit/organize the ones you have
argument-hint: [target-url-or-app] [--platforms web,android,ios] [--no-explore] [--fix] [--apply-reorg] [--propose N] [--rebaseline] [--threshold PCT]
---

Arguments: $ARGUMENTS

Invoke the **foresight** skill to run the full workflow against the current project:

1. Initialize `.tdd/foresight/`.
2. Static discovery (`foresight-cartographer`, mode `map`) → `inventory.json`.
3. Live exploration of the target (`foresight-web-explorer` / `foresight-mobile-explorer`) → screenshots + UI maps, enriching the inventory. Skipped if `--no-explore` or no target is given.
   - 3a. Visual inspection of the screenshots (`foresight-visual-inspector`) → user stories + screenshot provenance per UI element, regression candidates.
   - 3b. Source backfill (`foresight-cartographer`, mode `backfill`) → `source_refs` for visually-discovered elements.
   - 3c. `catalog` → UI-element documentation in `inventory.md` + `catalog.json`, with completeness.
   - 3d. `visual` → compare screenshots with the baseline (or record one with `--rebaseline` / when none exists).
4. Coverage analysis → `coverage/gaps.md` (risk-ranked; `weak` matches count as gaps).
5. Corpus audit → `audit/audit.md` (flags entries hindsight can't replay; `--fix` repairs empty run commands from the test plans).
6. Reorg plan for hindsight → `reorg/reorg.md` (the architect's corrections go in `reorg/overrides.json`; apply only if `--apply-reorg` or the user approves).
7. Proposals for the top `--propose N` gaps (default 3) → `proposals/<slug>/`, in iterative-tdd format.
8. `validate` every artifact, assemble `report.md` + `report.html`, and give a short chat summary.

Parse from `$ARGUMENTS`: a leading non-flag token is the exploration **target** (URL or app id); `--platforms` selects which platforms to explore; `--no-explore` does static-only; `--fix` passes `--fix-run-command` to the audit; `--apply-reorg` writes the reorg back to `replay.json`; `--propose N` sets the proposal count; `--rebaseline` records this run as the visual baseline; `--threshold PCT` sets the visual change tolerance. Default to `--project "$(pwd)"`.

Follow the skill's phase order exactly; delegate the reading/exploring/judging to the scoped sub-agents and use `${CLAUDE_PLUGIN_ROOT}/scripts/foresight.py` for the deterministic steps.
