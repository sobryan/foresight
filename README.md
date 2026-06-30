# foresight

A Claude Code plugin that finds the regressions you *haven't* written yet — and keeps the ones you have sound, grouped, and prioritized. It is the forward-looking sibling of [`hindsight`](../hindsight): where hindsight **replays** the regression entries accumulated by [`iterative-tdd`](../iterative-tdd), foresight **explores the product** — the code, the docs, and the *running* web/Android/iOS app — and compares what the product actually does against what the test suite actually checks.

```
 iterative-tdd  ──writes──▶  .tdd/regression/  ──replays──▶  hindsight
                                   ▲   │
                          audits & │   │ proposes new
                          regroups │   ▼ entries for /tdd
                                  foresight  ──explores code/docs/app──▶ coverage gaps
```

## What it does

foresight has two jobs, and a full run does both.

**Job A — discover untested regressions.** It builds an inventory of what the product does (features, use cases, UI elements, routes/screens) by reading the code and docs and by *driving the running app* — clicking through the web app via the browser tools, the Android/iOS app via device CLIs — capturing a screenshot at each end-to-end step. It then matches every use case and UI element against the existing tests and regression entries. Whatever the product does that nothing checks is a coverage gap, and for the high-value gaps it writes a ready-to-run **proposal** in iterative-tdd's exact format so you can hand it straight to `/tdd`.

**Job B — validate and organize the corpus you already have.** It audits every `.tdd/regression/<slug>/` for *appropriateness*: can it actually be replayed (does it have a real `run_command`?), do its tests still map to real code, does it have a sensible `priority` and `feature` group, is it a duplicate? Then it proposes a prioritized, feature-grouped reorganization that hindsight can act on — and can apply it by writing `priority`/`feature`/`serial` back into each `replay.json`, backwards-compatibly.

> Out of the box, `foresight audit` against hindsight's own suite reports that **every entry is "not replayable"** — each `replay.json` has an empty `run_command`, so hindsight silently no-ops on them. That blind spot is exactly what foresight is built to surface.

## The deterministic core vs. the exploratory half

- **`scripts/foresight.py`** is pure Python standard library, with no LLM and no app-driving. `audit`, `coverage`, `reorg`, and `report` run anywhere `hindsight.py` runs and are safe to wire into CI (non-zero exit on findings). This is the half you can trust to be repeatable.
- **The skill + five sub-agents** do the reading, exploring, and judgment that needs an LLM and the agent runtime's tools. This is the interactive, opt-in half.

## Install

```bash
# In Claude Code
/plugin marketplace add /path/to/foresight
/plugin install foresight@foresight-marketplace
```

See `INSTALL.md` for project-local, personal, and loose-component options.

## Use

```text
# Full run against a running web app, and apply the reorg it recommends
/foresight http://localhost:3000 --platforms web --apply-reorg

# Just validate the existing suite (deterministic, CI-friendly)
/foresight-audit

# What's untested? (risk-ranked)
/foresight-coverage

# Turn the top 3 gaps into ready-to-run regression proposals
/foresight-propose 3

# Group & prioritize the corpus for hindsight, then write it back
/foresight-reorg --apply
```

The skill also auto-triggers on phrases like "what isn't tested", "find coverage gaps", "what regressions are we missing", or "audit my regression suite".

### Commands

| Command | Does | LLM in loop? |
|---|---|---|
| `/foresight [target]` | Full run: discover → explore → coverage → audit → reorg → propose → report | yes (exploration) |
| `/foresight-audit` | Validate the corpus (replayable? sound? grouped?). CI-friendly | no |
| `/foresight-coverage` | Build the coverage map + risk-ranked gaps | no |
| `/foresight-propose [N]` | Emit new-regression proposals for the top gaps | yes |
| `/foresight-reorg [--apply]` | Propose/apply priority+feature grouping for hindsight | no |

## How it fits the suite

- **From iterative-tdd:** the regression entries iterative-tdd writes are foresight's audit input.
- **To iterative-tdd:** foresight's proposals are runnable by `/tdd` — promoting one turns it into a real `.tdd/regression/` entry.
- **To hindsight:** the `priority`/`feature`/`serial` foresight applies (and its `audit.json`/`reorg.json`) make hindsight's prioritized, grouped, parallel sweeps correct and meaningful.

foresight introduces **no new central store** — every artifact lives under each project's `.tdd/foresight/`, and the only thing it ever writes into `.tdd/regression/` is metadata via `reorg --apply` (with a `.bak`).

## Output

```
<project>/.tdd/foresight/
  inventory/    inventory.json    inventory.md
  exploration/<ts>/{web,android,ios}/  screenshots + ui_map.json
  coverage/     coverage.json     gaps.md
  audit/        audit.json        audit.md
  reorg/        reorg.json        reorg.md
  proposals/<slug>/   task.md  test_plan.md  replay.json  EVIDENCE.md
  report.md
```

Each analysis writes JSON (for hindsight & tooling) next to Markdown (for humans).

## Layout

```
foresight/
  .claude-plugin/plugin.json
  REQUIREMENTS.md
  agents/
    foresight-cartographer.md     # static: code/docs → inventory
    foresight-web-explorer.md     # web via browser tools → screenshots + UI map
    foresight-mobile-explorer.md  # android/ios via device CLIs
    foresight-auditor.md          # coverage gaps + validate existing entries
    foresight-architect.md        # propose new regressions + refine reorg
  skills/
    foresight/
      SKILL.md
      reference/
        exploration.md
        coverage-model.md
        regression-contract.md
        output-format.md
  commands/
    foresight.md  foresight-audit.md  foresight-coverage.md
    foresight-propose.md  foresight-reorg.md
  scripts/
    foresight.py                  # deterministic core (stdlib only)
  tests/
    test_foresight.py
  docs/
    getting-started.md
```

## Design notes

- **Separation of concerns by tool scope.** Each sub-agent's frontmatter restricts its tools: explorers can drive the app but can't edit code; the auditor is read-only; the architect writes proposals under `.tdd/foresight/` but never touches source or `.tdd/regression/` content.
- **The deterministic core is the contract.** Audit/coverage/reorg are plain Python over plain JSON — the same files iterative-tdd and hindsight already use — so the three plugins interoperate with no glue.
- **Conservative matching, reviewed by an agent.** The coverage matcher would rather report a false gap than claim coverage that isn't there; the auditor agent filters the false gaps against the code before anything is proposed.
- **foresight proposes; iterative-tdd implements; hindsight replays.** That boundary is enforced, not just suggested.

## Requirements

- Claude Code with the plugin system enabled.
- Python 3.8+ on PATH (the deterministic core is standard-library only).
- For live exploration: the browser tools (web), and — optionally — Maestro/Appium plus `adb` (Android) or `xcrun simctl` (iOS). foresight degrades gracefully to static analysis when these aren't present.
