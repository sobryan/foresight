# foresight

A Claude Code plugin (with a GitHub Copilot port) that finds the regressions you *haven't* written yet, sees what changed on screen, and keeps the regressions you have sound, grouped, and prioritized. It is the forward-looking sibling of [`hindsight`](../hindsight): where hindsight **replays** the regression entries accumulated by [`iterative-tdd`](../iterative-tdd), foresight **explores the product** — the code, the docs, and the *running* web/Android/iOS app — and compares what the product actually does (and looks like) against what the test suite actually checks.

```
 iterative-tdd  ──writes──▶  .tdd/regression/  ──replays──▶  hindsight
                                   ▲   │
                    audits, repairs│   │ proposes new
                          regroups │   ▼ entries for /tdd
                                  foresight  ──explores code/docs/app──▶ coverage gaps
                                             ──inspects screenshots───▶ UI catalog + visual diffs
```

## What it does

foresight has two jobs, and a full run does both.

**Job A — discover untested regressions.** It builds an inventory of what the product does (features, use cases, UI elements, routes/screens) by reading the code and docs and by *driving the running app* — clicking through the web app via the browser tools, the Android/iOS app via device CLIs — capturing a screenshot at each end-to-end step. A visual-inspection pass then looks at those screenshots the way a person would: every visible control gets a user story and a link back to the code that implements it, rendered into a **UI catalog**. Screenshots are compared with a **baseline** so a screen that changed shows up even when the tests pass. Every use case and UI element is matched against the existing tests and regression entries; whatever nothing checks is a coverage gap, and for the high-value gaps foresight writes a ready-to-run **proposal** in iterative-tdd's exact format so you can hand it straight to `/tdd`.

**Job B — validate and organize the corpus you already have.** It audits every `.tdd/regression/<slug>/` for *appropriateness*: can it actually be replayed (does it have a real `run_command`?), do its tests still map to real code, does it have a sensible `priority` and `feature` group, is it a duplicate? It can **repair** empty run commands from each entry's own test plan. Then it proposes a prioritized, feature-grouped reorganization that hindsight can act on — and can apply it by writing `priority`/`feature`/`serial` back into each `replay.json`, backwards-compatibly and without overwriting values a human set.

> Out of the box, `foresight audit` against this machine's projects found **23 of 43 entries "not replayable"** — an empty `run_command`, which hindsight silently no-ops on. The root cause is a one-line bug in iterative-tdd's test-plan reader (bare ` ``` ` fences); `docs/upstream/` has the patch and `audit --fix-run-command` repairs the entries that already exist.

## The deterministic core vs. the exploratory half

- **`scripts/foresight.py`** is pure Python standard library, with no LLM and no app-driving. `audit`, `coverage`, `catalog`, `visual`, `reorg`, `validate`, and `report` run anywhere `hindsight.py` runs and are safe to wire into CI (non-zero exit on findings). This is the half you can trust to be repeatable — including the PNG decoder behind the visual diff.
- **The skill + six sub-agents** do the reading, exploring, looking, and judgment that needs an LLM and the agent runtime's tools. This is the interactive, opt-in half.

## Install

```bash
# Claude Code
/plugin marketplace add /path/to/foresight
/plugin install foresight@foresight-marketplace

# GitHub Copilot (VS Code + Copilot CLI) — into one project, or --user for all
python3 scripts/install_copilot.py --target /path/to/your-project
```

See `INSTALL.md` for project-local, personal, loose-component, and Copilot options.

## Use

```text
# Full run against a running web app, repair broken entries, apply the reorg it recommends
/foresight http://localhost:3000 --platforms web --fix --apply-reorg

# Just validate the existing suite (deterministic, CI-friendly)
/foresight-audit

# What's untested? (risk-ranked; "weak" matches count as gaps)
/foresight-coverage

# Every visible UI element with its user story, screenshot, and source — and what's undocumented
/foresight-catalog

# Which screens changed since the baseline?
/foresight-visual

# Turn the top 3 gaps into ready-to-run regression proposals
/foresight-propose 3

# Group & prioritize the corpus for hindsight, then write it back
/foresight-reorg --apply
```

The skill also auto-triggers on phrases like "what isn't tested", "find coverage gaps", "did the UI change", or "audit my regression suite".

### Commands

| Command | Does | LLM in loop? |
|---|---|---|
| `/foresight [target]` | Full run: discover → explore → inspect → catalog → visual diff → coverage → audit → reorg → propose → validate → report | yes (exploration + inspection) |
| `/foresight-audit [--fix-run-command]` | Validate the corpus (replayable? sound? grouped?), optionally repair empty run commands. CI-friendly | no |
| `/foresight-coverage` | Build the coverage map + risk-ranked gaps | no |
| `/foresight-catalog` | Render the UI catalog and report documentation completeness | no |
| `/foresight-visual [--baseline]` | Compare exploration screenshots with the baseline, or record one | no |
| `/foresight-propose [N]` | Emit new-regression proposals for the top gaps | yes |
| `/foresight-reorg [--apply] [--force]` | Propose/apply priority+feature grouping for hindsight | no |

Deterministic extras: `foresight.py validate` checks every artifact against its JSON schema; `foresight.py report --html` writes a self-contained page.

## How it fits the suite

- **From iterative-tdd:** the regression entries iterative-tdd writes are foresight's audit input.
- **To iterative-tdd:** foresight's proposals are runnable by `/tdd` — promoting one turns it into a real `.tdd/regression/` entry. Each proposal's test plan carries the `## How to run the tests` section iterative-tdd needs to fill `run_command`.
- **To hindsight:** the `priority`/`feature`/`serial` foresight applies, the repaired `run_command`s, and its `audit.json`/`reorg.json` make hindsight's prioritized, grouped, parallel sweeps correct and meaningful.

foresight introduces **no new central store** — every artifact lives under each project's `.tdd/foresight/`, and the only things it ever writes into `.tdd/regression/` are metadata via `reorg --apply` and an empty `run_command` via `audit --fix-run-command` (both with a `.bak`).

## Output

```
<project>/.tdd/foresight/
  inventory/    inventory.json  inventory.md  catalog.json  visual_candidates.md
  exploration/<ts>/{web,android,ios}/  NNNN-<slug>.png + ui_map.json
  coverage/     coverage.json   gaps.md
  audit/        audit.json      audit.md
  reorg/        reorg.json      reorg.md      overrides.json
  visual/       baseline.json   visual.json   visual.md
  proposals/<slug>/   task.md  test_plan.md  replay.json  EVIDENCE.md
  report.md     report.html
```

Each analysis writes JSON (for hindsight & tooling, schema-checked) next to Markdown (for humans).

## Layout

```
foresight/
  .claude-plugin/plugin.json
  REQUIREMENTS.md
  agents/
    foresight-cartographer.md      # static: code/docs → inventory; backfills source_refs
    foresight-web-explorer.md      # web via the Claude-in-Chrome tools → screenshots + UI map
    foresight-mobile-explorer.md   # android/ios via device CLIs
    foresight-visual-inspector.md  # screenshots → UI elements + user stories
    foresight-auditor.md           # coverage gaps + validate existing entries
    foresight-architect.md         # propose new regressions + reorg overrides
  skills/foresight/
    SKILL.md
    reference/  exploration.md  coverage-model.md  regression-contract.md  output-format.md
    reference/schemas/*.schema.json
  commands/
    foresight.md  foresight-audit.md  foresight-coverage.md  foresight-catalog.md
    foresight-visual.md  foresight-propose.md  foresight-reorg.md
  scripts/
    foresight.py                   # deterministic core (stdlib only)
    install_copilot.py             # GitHub Copilot port installer
    copilot_session_start.py       # Copilot sessionStart hook
  copilot/                         # Copilot-specific pieces (Playwright web explorer, templates)
  .github/                         # dogfood Copilot install + CI workflow
  tests/
  docs/
    getting-started.md  plans/  upstream/
```

## Design notes

- **Separation of concerns by tool scope.** Each sub-agent's frontmatter restricts its tools: explorers can drive the app but can't edit code; the visual inspector only reads screenshots; the auditor is read-only; the architect writes proposals and reorg overrides under `.tdd/foresight/` but never touches source or `.tdd/regression/` content.
- **The deterministic core is the contract.** Audit/coverage/catalog/visual/reorg are plain Python over plain JSON — the same files iterative-tdd and hindsight already use — so the three plugins interoperate with no glue. `validate` keeps agent output honest.
- **Conservative matching, reviewed by an agent.** The coverage matcher would rather report a false gap than claim coverage that isn't there: plain words never literal-match, two shared words are only "weak", and the auditor agent reviews both directions before anything is proposed.
- **Human metadata is protected.** `reorg --apply` never overwrites a `priority`/`feature`/`serial` someone set explicitly unless the architect's `overrides.json` names it or you pass `--force`.
- **foresight proposes; iterative-tdd implements; hindsight replays.** That boundary is enforced, not just suggested.

## Requirements

- Claude Code with the plugin system enabled, **or** GitHub Copilot in VS Code / the Copilot CLI (see `copilot/README.md`).
- Python 3.8+ on PATH (the deterministic core is standard-library only).
- For live exploration: the browser tools (web — Claude in Chrome, or the Playwright MCP server under Copilot), and — optionally — Maestro/Appium plus `adb` (Android) or `xcrun simctl` (iOS). foresight degrades gracefully to static analysis when these aren't present.

## Development

```bash
pytest -q                                    # ~120 tests, stdlib + pytest only
python3 scripts/foresight.py audit --project .   # foresight audits its own corpus
python3 scripts/install_copilot.py --target .    # refresh the dogfood Copilot install
```

CI (`.github/workflows/ci.yml`) runs the suite on Python 3.8–3.13, audits and replays the repo's own regression corpus, validates artifacts, and checks the Copilot port is in sync with the Claude sources.
