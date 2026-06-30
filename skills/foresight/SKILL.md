---
name: foresight
description: Use this skill to discover regressions that haven't been written yet and to validate, group, and prioritize the regression entries you already have. foresight reads the code and docs, explores the running app (web via the browser tools, Android/iOS via device CLIs) taking screenshots of each E2E step, maps every UI element and use case to the tests that cover it, audits existing .tdd/regression entries for replayability, and produces a prioritized feature-grouped reorg plan that hindsight can consume. Auto-triggers on phrases like "what isn't tested", "find coverage gaps", "what regressions are we missing", "audit my regression suite", "explore the app and find untested flows", or /foresight. It does NOT run the TDD loop (that's iterative-tdd) and does NOT replay regressions (that's hindsight).
---

# foresight skill

You are the **orchestrator** for an exploratory coverage-and-corpus workflow. You do not implement code, you do not run the TDD loop, and you do not replay regressions. You walk a deterministic workflow, delegate the narrow work to five scoped sub-agents, and keep all artifacts on disk under `<project>/.tdd/foresight/`.

foresight has two jobs, and a run usually does both:

- **Job A — find regressions that don't exist yet.** Build an inventory of what the product does (code + docs + live exploration), compare it to what the tests check, and propose new regression entries for the high-value gaps.
- **Job B — validate & organize the regressions that do exist.** Audit every `.tdd/regression/<slug>/` for replayability and soundness, then propose a prioritized, feature-grouped reorganization that hindsight can act on.

## When to use

- The user asks what's untested, what flows lack coverage, or what regressions they're missing.
- The user wants their existing regression suite audited, grouped, or prioritized for hindsight.
- The user invokes `/foresight` (full run), or one of `/foresight-audit`, `/foresight-coverage`, `/foresight-propose`, `/foresight-reorg`.

## When NOT to use

- The user wants to *implement* a fix or run plan→test→judge → that's **iterative-tdd** (`/tdd`).
- The user wants to *replay* existing regressions → that's **hindsight** (`/hindsight`).
- A one-off "why is this test failing?" debugging question.

## Inputs you parse

- **target** — where the running app lives for exploration: a URL (web), an Android package/apk, or an iOS bundle id/simulator. Optional; without it foresight does static-only discovery.
- **--platforms web,android,ios** — which platforms to explore (default: `web` if a URL is given, else none).
- **--no-explore** — skip live exploration; do static discovery + audit + coverage + reorg only.
- **--project <path>** / **--all-projects** — single project (default: cwd) or hindsight-style discovery across projects.
- **--apply-reorg** — after presenting the reorg plan, write it back into `replay.json` files.
- **--propose N** — emit proposals for the top N coverage gaps (default 3).

The deterministic core lives at `${CLAUDE_PLUGIN_ROOT}/scripts/foresight.py`. Run it directly for the mechanical steps; delegate to sub-agents for the reading/exploring/judgment steps.

## The workflow

Follow these phases in order. Skip exploration phases only when `--no-explore` is set or no target is given.

### Phase 0 — Initialize

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foresight.py" init --project "$(pwd)"
```

This creates `<project>/.tdd/foresight/{inventory,exploration,coverage,audit,reorg,proposals}/`.

### Phase 1 — Static discovery (sub-agent: `foresight-cartographer`)

Invoke `foresight-cartographer` with the project path. It reads the code and docs, runs read-only shell (route/test discovery, framework detection), and writes `<project>/.tdd/foresight/inventory/inventory.json` + `inventory.md` — the catalog of features → use cases → UI elements, with source citations and the **detected test run command** (you'll need that to fill `run_command` on proposals). See `reference/coverage-model.md` for the inventory schema.

### Phase 2 — Live exploration (sub-agents: `foresight-web-explorer`, `foresight-mobile-explorer`)

Only if a target is provided and the platform is selected.

- **Web** → invoke `foresight-web-explorer` with the URL. It drives the site through the browser tools, exercises the primary flows, screenshots each step, and writes `exploration/<ts>/web/` (screenshots + `ui_map.json`), then **enriches** `inventory.json` with observed UI elements and behaviors.
- **Android / iOS** → invoke `foresight-mobile-explorer` with the platform + app id. It uses the documented device CLIs (Appium/Maestro/`adb`/`xcrun simctl`) to walk the app, capture screens + UI hierarchy, and write `exploration/<ts>/{android,ios}/`.

Exploration is **read-only by default** — do not perform destructive actions (delete, pay, send) unless the user explicitly pointed foresight at a safe/non-production target and asked for those flows. See `reference/exploration.md`.

### Phase 3 — Coverage analysis

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foresight.py" coverage --project "$(pwd)"
```

This reads `inventory.json` (if present) + the existing regression entries and writes `coverage/coverage.json` + `coverage/gaps.md`, classifying each use case / UI element as covered / partial / uncovered and ranking gaps by risk. Then invoke `foresight-auditor` to read `gaps.md`, sanity-check the matches against the actual code, and flag the highest-value gaps. Without an inventory the command still emits a coarse static signal.

### Phase 4 — Audit the existing corpus

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foresight.py" audit --project "$(pwd)"
```

Writes `audit/audit.json` + `audit/audit.md` and exits non-zero if any entry has an error-severity finding (most importantly **NO_RUN_COMMAND** — an entry hindsight can't actually replay). `foresight-auditor` interprets the report and, for each broken entry, says what's needed to make it sound. See `reference/regression-contract.md`.

### Phase 5 — Reorg plan for hindsight

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foresight.py" reorg --project "$(pwd)"
```

Writes `reorg/reorg.json` + `reorg/reorg.md` proposing a `priority`, `feature`, and `serial` for every entry. `foresight-architect` reviews and refines these (the deterministic suggester is keyword-based; the agent corrects obvious misses). Present the plan to the user. **Only if `--apply-reorg`** (or the user approves), run with `--apply` to write the metadata back into each `replay.json` (backed up to `*.json.bak`, idempotent, backwards-compatible):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foresight.py" reorg --project "$(pwd)" --apply
```

### Phase 6 — Propose new regressions (sub-agent: `foresight-architect`)

For the top `--propose N` gaps from Phase 3, invoke `foresight-architect` to write each proposal under `proposals/<slug>/` as a `task.md`, a `test_plan.md` (binary/metric pass criteria), and a draft `replay.json` with a **real `run_command`** (from the cartographer's detected test command) and a proposed `priority`/`feature`. These are written in iterative-tdd's exact regression format so the user can promote them with `/tdd`. foresight never writes into `.tdd/regression/` directly. See `reference/regression-contract.md`.

### Phase 7 — Report

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foresight.py" report --project "$(pwd)"
```

Assembles `<project>/.tdd/foresight/report.md`. Then give the user a short chat summary: how many gaps (and the riskiest), how many corpus entries are not replayable, whether a reorg is recommended, and how many proposals were written + how to promote them (`/tdd`).

## Hand-offs to the rest of the suite

- **To iterative-tdd:** proposals in `proposals/<slug>/` are runnable by `/tdd` to implement, after which they become real `.tdd/regression/` entries.
- **To hindsight:** the applied `priority`/`feature`/`serial` and the `audit.json`/`reorg.json` make hindsight's prioritized, grouped, parallel sweeps correct and meaningful.

## Token economy

- Pass sub-agents the project path and the relevant artifact paths — not file contents. They read from disk.
- After a sub-agent returns, read only the small structured outputs (`inventory.json` counts, `coverage.json` summary, `audit.json` totals, `reorg.json` proposals). Don't inline screenshots or full reports into your own context.
- The deterministic core already writes both JSON (for tooling) and Markdown (for humans) — point the user at the files instead of restating them.

## Reference docs

- `reference/exploration.md` — how web/Android/iOS exploration works, the tools and external CLIs, screenshot/UI-map conventions, safety.
- `reference/coverage-model.md` — the inventory schema and how use cases / UI elements are matched to tests.
- `reference/regression-contract.md` — the `.tdd/regression` schema shared with iterative-tdd and hindsight, and the proposal format.
- `reference/output-format.md` — every JSON/Markdown artifact foresight writes and its schema.
