# Getting started with foresight

foresight answers three questions about your tests:

1. **What does the product do that nothing checks?** (coverage gaps)
2. **What changed on screen since last time?** (visual regression)
3. **Are the regressions I already have actually any good?** (corpus audit, repair, reorg)

You can get value from #3 in 10 seconds with no setup, then graduate to #1 and #2 when you point it at a running app. Everything below works the same under GitHub Copilot once you've run `scripts/install_copilot.py` (see `copilot/README.md`).

## 0. Prerequisites

- Python 3.8+ on PATH. That's it for the deterministic half.
- For live exploration later: the browser tools (web) and/or Maestro/Appium + `adb`/`xcrun simctl` (mobile). All optional.

## 1. Audit what you already have (no setup, no LLM)

From any project that has `.tdd/regression/` entries:

```text
/foresight-audit
```

You'll get a corpus **health score** and, first, the entries that are **not replayable**. The most common finding is `NO_RUN_COMMAND`: the entry exists and shows up in `hindsight list`, but its `replay.json` has an empty `run_command`, so hindsight and `tdd_regression.py` return `no_run_command` and the test never actually runs. Most of these are recoverable from the entry's own test plan:

```text
/foresight-audit --fix-run-command
```

Headless / CI (exits non-zero on error-severity findings):

```bash
python3 .claude/plugins/foresight/scripts/foresight.py audit --project .
echo "exit: $?"
```

## 2. Group & prioritize for hindsight

```text
/foresight-reorg
```

foresight proposes a `priority` (critical/high/normal/low), a `feature` tag, and a `serial` flag for every entry — derived from risk vocabulary (auth, payments, delete, security…) and shared code areas. Values you already set by hand are marked *protected* and left alone. Review the plan, then apply it:

```text
/foresight-reorg --apply
```

Applying writes those keys back into each `replay.json` — backwards-compatibly (every other key preserved), with a `replay.json.bak` backup, and idempotently. hindsight immediately starts sorting, `--feature`-filtering, and parallelizing with the better metadata. To override a protected value, add it to `.tdd/foresight/reorg/overrides.json` (the architect agent writes its corrections there) or pass `--force`.

## 3. Find what isn't tested (point it at the running app)

Start your app, then:

```text
/foresight http://localhost:3000 --platforms web
```

foresight will:

1. **Map the code & docs** → an inventory of features, use cases, and UI elements.
2. **Drive the app** through the browser tools, screenshotting each end-to-end step (`NNNN-<screen>.png`), and record what every UI element does.
3. **Look at the screenshots** and write a user story for every visible control, then find the code that implements each one → the UI catalog in `inventory/inventory.md` (and how complete it is).
4. **Record a visual baseline** on the first run; on later runs, **compare** and list the screens that changed, with the percent of pixels that differ.
5. **Match** the inventory against your tests and regression entries.
6. **Report gaps** in `coverage/gaps.md`, risk-ranked — the dangerous, user-facing, untested flows first. A `weak` match (two shared words) is listed as a gap, not as covered.

Add `--platforms web,android,ios` to also walk a mobile build (needs the mobile CLIs installed).

## 4. Watch the UI, not just the tests

After any exploration run:

```text
/foresight-visual              # compare with the baseline
/foresight-visual --baseline   # the change was intended: make this run the new baseline
```

A screen that changed while its use case is "covered" is the regression your tests don't see. Headless:

```bash
python3 .claude/plugins/foresight/scripts/foresight.py visual --project . --fail-on-change --threshold 0.5
```

## 5. Turn gaps into regressions

```text
/foresight-propose 3
```

foresight writes the top 3 gaps (including the visual inspector's candidates) as proposals under `.tdd/foresight/proposals/<slug>/` — each a `task.md`, a `test_plan.md` with a `## How to run the tests` section, a draft `replay.json` with a **real** `run_command`, and `EVIDENCE.md`. Promote one into a real, hindsight-replayable regression by handing its task to iterative-tdd:

```text
/tdd <paste the task from proposals/<slug>/task.md>
```

That's the loop: **foresight finds, sees, and frames it → iterative-tdd implements it → hindsight replays it forever after.**

## Where everything lands

```
<project>/.tdd/foresight/
  inventory/  exploration/<ts>/  coverage/  audit/  reorg/  visual/  proposals/  report.md  report.html
```

`/foresight` finishes by validating every artifact against its schema, writing `report.md`, and giving you a short summary. Re-run any single command (`/foresight-audit`, `/foresight-coverage`, `/foresight-catalog`, `/foresight-visual`, `/foresight-reorg`) any time — they're cheap and idempotent.

## Tips

- Run `/foresight-audit` and `foresight.py visual --fail-on-change` in CI next to hindsight's `replay-all` — together they catch "it regressed", "it was never really testing anything", and "it looks different".
- The coverage matcher is conservative — it may flag a real test as a gap. The `foresight-auditor` agent reviews gaps against the code before proposing, so promoted proposals aren't duplicates.
- Keep screenshot slugs and the viewport stable between runs; the visual comparison keys on them.
- No app to explore? Everything except exploration, inspection, and visual diff still runs. Static analysis + audit + coverage (source-area signal) + reorg need only the repo.
