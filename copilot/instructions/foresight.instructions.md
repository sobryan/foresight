---
name: 'foresight regression contract'
description: 'What a .tdd/regression entry must contain to be replayable by hindsight and iterative-tdd'
applyTo: '.tdd/**'
---

# The shared regression contract (foresight / iterative-tdd / hindsight)

- Every `.tdd/regression/<slug>/replay.json` needs a non-empty `run_command`, a non-empty `tests[]`, and the companion `task.md`, `plan.md`, `test_plan.md`. An empty `run_command` means the entry silently never runs.
- `test_plan.md` must start with a `## How to run the tests` section whose command sits in a ```` ```bash ```` fence or inline backticks — never a bare ```` ``` ```` fence (the iterative-tdd extractor returns an empty command for those).
- Optional metadata: `priority` (critical | high | normal | low), `feature` (string or list), `serial` (bool). Set explicitly only when you mean it; foresight's reorg treats explicit values as protected.
- Never write new entries into `.tdd/regression/` by hand — proposals go under `.tdd/foresight/proposals/<slug>/` and are promoted through the TDD loop. foresight only ever adds `priority`/`feature`/`serial` or fills an empty `run_command`, always with a `replay.json.bak`.
- To check a corpus: `/foresight-audit` (add `--fix-run-command` to repair empty commands). To see what the tests miss: `/foresight-coverage`. To compare screenshots against the baseline: `/foresight-visual`.
