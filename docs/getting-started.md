# Getting started with foresight

foresight answers two questions about your tests:

1. **What does the product do that nothing checks?** (coverage gaps)
2. **Are the regressions I already have actually any good?** (corpus audit + reorg)

You can get value from #2 in 10 seconds with no setup, then graduate to #1 when you point it at a running app.

## 0. Prerequisites

- Python 3.8+ on PATH. That's it for the deterministic half.
- For live exploration later: the browser tools (web) and/or Maestro/Appium + `adb`/`xcrun simctl` (mobile). All optional.

## 1. Audit what you already have (no setup, no LLM)

From any project that has `.tdd/regression/` entries:

```text
/foresight-audit
```

You'll get a corpus **health score** and, first, the entries that are **not replayable**. The most common finding is `NO_RUN_COMMAND`: the entry exists and shows up in `hindsight list`, but its `replay.json` has an empty `run_command`, so hindsight and `tdd_regression.py` return `no_run_command` and the test never actually runs. foresight tells you exactly which command each one is missing.

Headless / CI (exits non-zero on error-severity findings):

```bash
python3 .claude/plugins/foresight/scripts/foresight.py audit --project . 
echo "exit: $?"
```

## 2. Group & prioritize for hindsight

```text
/foresight-reorg
```

foresight proposes a `priority` (critical/high/normal/low), a `feature` tag, and a `serial` flag for every entry — derived from risk vocabulary (auth, payments, delete, security…) and shared code areas. Review the plan, then apply it:

```text
/foresight-reorg --apply
```

Applying writes those three keys back into each `replay.json` — backwards-compatibly (every other key preserved), with a `replay.json.bak` backup, and idempotently. hindsight immediately starts sorting, `--feature`-filtering, and parallelizing with the better metadata.

## 3. Find what isn't tested (point it at the running app)

Start your app, then:

```text
/foresight http://localhost:3000 --platforms web
```

foresight will:

1. **Map the code & docs** → an inventory of features, use cases, and UI elements.
2. **Drive the app** through the browser tools, screenshotting each end-to-end step, and record what every UI element does.
3. **Match** the inventory against your tests and regression entries.
4. **Report gaps** in `coverage/gaps.md`, risk-ranked — the dangerous, user-facing, untested flows first.

Add `--platforms web,android,ios` to also walk a mobile build (needs the mobile CLIs installed).

## 4. Turn gaps into regressions

```text
/foresight-propose 3
```

foresight writes the top 3 gaps as proposals under `.tdd/foresight/proposals/<slug>/` — each a `task.md`, a `test_plan.md`, and a draft `replay.json` with a **real** `run_command`. Promote one into a real, hindsight-replayable regression by handing its task to iterative-tdd:

```text
/tdd <paste the task from proposals/<slug>/task.md>
```

That's the loop: **foresight finds and frames it → iterative-tdd implements it → hindsight replays it forever after.**

## Where everything lands

```
<project>/.tdd/foresight/
  inventory/  exploration/<ts>/  coverage/  audit/  reorg/  proposals/  report.md
```

`/foresight` finishes by writing `report.md` and giving you a short summary. Re-run any single command (`/foresight-audit`, `/foresight-coverage`, `/foresight-reorg`) any time — they're cheap and idempotent.

## Tips

- Run `/foresight-audit` in CI next to hindsight's `replay-all` — a regression with no `run_command` passes hindsight vacuously but fails foresight's audit, so the two together catch both "it regressed" and "it was never really testing anything".
- The coverage matcher is conservative — it may flag a real test as a gap. The `foresight-auditor` agent reviews gaps against the code before proposing, so promoted proposals aren't duplicates.
- No app to explore? Everything except Phase 2 still runs. Static analysis + audit + coverage (source-area signal) + reorg need only the repo.
