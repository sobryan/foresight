---
name: foresight
description: Use this skill to discover regressions that haven't been written yet, to verify the UI visually across runs, and to validate, group, and prioritize the regression entries you already have. foresight reads the code and docs, explores the running app (web via the browser tools, Android/iOS via device CLIs) taking screenshots of each E2E step, inspects those screenshots to catalog every visible UI element with a user story, compares screenshots against a baseline to catch visual regressions, maps every UI element and use case to the tests that cover it, audits existing .tdd/regression entries for replayability (and can repair empty run commands), and produces a prioritized feature-grouped reorg plan that hindsight can consume. Auto-triggers on phrases like "what isn't tested", "find coverage gaps", "what regressions are we missing", "audit my regression suite", "did the UI change", "visual regression", "explore the app and find untested flows", or /foresight.
argument-hint: [target-url-or-app] [--platforms web,android,ios] [--no-explore] [--fix] [--apply-reorg] [--propose N] [--rebaseline] [--threshold PCT]
---

# foresight skill

You are the **orchestrator** for an exploratory coverage-and-corpus workflow. You do not implement code, you do not run the TDD loop, and you do not replay regressions. You walk a deterministic workflow, delegate the narrow work to six scoped sub-agents, and keep all artifacts on disk under `<project>/.tdd/foresight/`.

foresight has two jobs, and a run usually does both:

- **Job A — find regressions that don't exist yet.** Build an inventory of what the product does (code + docs + live exploration + visual inspection of the screenshots), compare it to what the tests check, detect what changed visually since the last baseline, and propose new regression entries for the high-value gaps.
- **Job B — validate & organize the regressions that do exist.** Audit every `.tdd/regression/<slug>/` for replayability and soundness (repairing empty run commands when asked), then propose a prioritized, feature-grouped reorganization that hindsight can act on.

## When to use

- The user asks what's untested, what flows lack coverage, or what regressions they're missing.
- The user asks whether the UI changed, wants screenshots compared, or says "visual regression".
- The user wants their existing regression suite audited, grouped, repaired, or prioritized for hindsight.
- The user invokes `/foresight` (full run), or one of `/foresight-audit`, `/foresight-coverage`, `/foresight-catalog`, `/foresight-visual`, `/foresight-propose`, `/foresight-reorg`.

## When NOT to use

- The user wants to *implement* a fix or run plan→test→judge → that's **iterative-tdd** (`/tdd`).
- The user wants to *replay* existing regressions → that's **hindsight** (`/hindsight`).
- A one-off "why is this test failing?" debugging question.

## Inputs you parse

- **target** — where the running app lives for exploration: a URL (web), an Android package/apk, or an iOS bundle id/simulator. Optional; without it foresight does static-only discovery.
- **--platforms web,android,ios** — which platforms to explore (default: `web` if a URL is given, else none).
- **--no-explore** — skip live exploration and the visual pass; do static discovery + audit + coverage + reorg only.
- **--project <path>** / **--all-projects** — single project (default: cwd) or hindsight-style discovery across projects.
- **--fix** — let the audit repair empty `run_command`s from each entry's `test_plan.md` (backs up `replay.json`).
- **--apply-reorg** — after presenting the reorg plan, write it back into `replay.json` files.
- **--propose N** — emit proposals for the top N coverage gaps (default 3).
- **--rebaseline** — record this run's screenshots as the new visual baseline instead of comparing against the old one.
- **--threshold PCT** — percent of pixels a screenshot may differ before it counts as changed (default 0).

The deterministic core lives at `.github/skills/foresight/scripts/foresight.py`. Run it directly for the mechanical steps; delegate to sub-agents for the reading/exploring/judgment steps. After any agent writes an artifact, `foresight.py validate` is cheap insurance that the next phase gets well-formed input.

## The workflow

Follow these phases in order. Skip Phase 2 (and 2a–2b) when `--no-explore` is set or no target is given.

### Phase 0 — Initialize

```bash
python3 .github/skills/foresight/scripts/foresight.py init --project "$(pwd)"
```

Creates `<project>/.tdd/foresight/{inventory,exploration,coverage,audit,reorg,proposals,visual}/`.

### Phase 1 — Static discovery (sub-agent: `foresight-cartographer`, mode `map`)

Invoke `foresight-cartographer` with the project path. It reads the code and docs, runs read-only shell (route/test discovery, framework detection), and writes `inventory/inventory.json` + `inventory.md` — the catalog of features → use cases → UI elements, with source citations and the **detected test run command** (you'll need that to fill `run_command` on proposals). Schema: `reference/coverage-model.md`.

### Phase 2 — Live exploration (sub-agents: `foresight-web-explorer`, `foresight-mobile-explorer`)

Only if a target is provided and the platform is selected.

- **Web** → invoke `foresight-web-explorer` with the URL. It drives the site through the browser tools, exercises the primary flows, screenshots each step as `exploration/<ts>/web/NNNN-<slug>.png`, writes `ui_map.json`, and **enriches** `inventory.json` with observed UI elements (each carrying a `visual.screenshot` reference).
- **Android / iOS** → invoke `foresight-mobile-explorer` with the platform + app id. It uses the documented device CLIs (Appium/Maestro/`adb`/`xcrun simctl`) to walk the app, capture screens + UI hierarchy, and write `exploration/<ts>/{android,ios}/`.

Exploration is **read-only by default** — no destructive actions (delete, pay, send) unless the user explicitly pointed foresight at a safe/non-production target and asked for those flows. See `reference/exploration.md`.

### Phase 2a — Visual inspection (sub-agent: `foresight-visual-inspector`)

Only if Phase 2 produced screenshots. Invoke `foresight-visual-inspector` with the run directory. It looks at every screenshot, enumerates each visible control (including ones the DOM pass missed), writes a `user_story` per element with `visual` provenance, merges into `inventory.json`, and lists regression candidates in `inventory/visual_candidates.md`.

### Phase 2b — Source backfill (sub-agent: `foresight-cartographer`, mode `backfill`)

Only if Phase 2a ran. Re-invoke the cartographer in `backfill` mode so visually-discovered elements get `source_refs` (`file:line`) where the code can be found with confidence.

### Phase 2c — Catalog

```bash
python3 .github/skills/foresight/scripts/foresight.py catalog --project "$(pwd)"
```

Renders the UI-element + user-story catalog into `inventory/inventory.md` (idempotent marker block) and `inventory/catalog.json`, and reports documentation completeness (story + source refs + an existing screenshot file). `catalog` must be the **last writer** to `inventory.md` in a run. `--fail-on-incomplete` is a CI gate that only makes sense after the visual pass has run.

### Phase 2d — Visual regression

```bash
python3 .github/skills/foresight/scripts/foresight.py visual --project "$(pwd)" [--threshold PCT]
```

Compares this run's screenshots with `visual/baseline.json` (matched by platform + slug, so screen names must be stable across runs) and writes `visual/visual.json` + `visual.md`: per image `unchanged`, `changed` (with the percent of pixels that differ), `resized`, `new`, or `missing`. If there is no baseline yet, or the user passed `--rebaseline`, record one instead:

```bash
python3 .github/skills/foresight/scripts/foresight.py visual --project "$(pwd)" --baseline
```

Tell the user which screens changed; a changed screen that maps to a covered use case is a regression candidate even when the tests pass.

### Phase 3 — Coverage analysis

```bash
python3 .github/skills/foresight/scripts/foresight.py coverage --project "$(pwd)"
```

Reads `inventory.json` (if present) + the existing regression entries and writes `coverage/coverage.json` + `coverage/gaps.md`, classifying each use case / UI element as `covered`, `weak`, `partial`, or `uncovered` and ranking gaps by risk. `weak` means the only evidence is two shared words — treat it as a gap. Then invoke `foresight-auditor` to read `gaps.md`, sanity-check the matches against the actual code, and flag the highest-value gaps. Without an inventory the command still emits a coarse static signal.

### Phase 4 — Audit the existing corpus

```bash
python3 .github/skills/foresight/scripts/foresight.py audit --project "$(pwd)"          # add --fix-run-command when the user passed --fix
```

Writes `audit/audit.json` + `audit/audit.md` and exits non-zero if any entry has an error-severity finding (most importantly **NO_RUN_COMMAND** — an entry hindsight can't actually replay). With `--fix-run-command` it first fills *empty* commands from each entry's `## How to run the tests` section (with a `.bak`) and reports what it repaired. `foresight-auditor` interprets the report and, for each broken entry, says what's needed to make it sound. See `reference/regression-contract.md`.

### Phase 5 — Reorg plan for hindsight

```bash
python3 .github/skills/foresight/scripts/foresight.py reorg --project "$(pwd)"
```

Writes `reorg/reorg.json` + `reorg/reorg.md` proposing a `priority`, `feature`, and `serial` for every entry. Values a human set explicitly are marked **protected** and left alone. `foresight-architect` reviews the plan and writes corrections to `reorg/overrides.json`; re-run the command so the plan merges them, then present it to the user. **Only if `--apply-reorg`** (or the user approves), apply:

```bash
python3 .github/skills/foresight/scripts/foresight.py reorg --project "$(pwd)" --apply    # --force also overwrites protected values
```

Backed up to `*.json.bak`, idempotent, backwards-compatible.

### Phase 6 — Propose new regressions (sub-agent: `foresight-architect`)

For the top `--propose N` gaps from Phase 3 (plus visual candidates), invoke `foresight-architect` to write each proposal under `proposals/<slug>/` as `task.md`, `test_plan.md` (with a `## How to run the tests` section in a `bash` fence), a draft `replay.json`, and `EVIDENCE.md`. These are written in iterative-tdd's exact regression format so the user can promote them with `/tdd`. foresight never writes into `.tdd/regression/` directly.

### Phase 7 — Validate and report

```bash
python3 .github/skills/foresight/scripts/foresight.py validate --project "$(pwd)"
python3 .github/skills/foresight/scripts/foresight.py report --project "$(pwd)" --html
```

`validate` checks every artifact against its schema (`reference/schemas/`). `report` assembles `report.md` (and a self-contained `report.html`), including a "Visual" section and an "Other artifacts" list for anything the agents wrote outside the documented set. Then give the user a short chat summary: how many gaps (and the riskiest), which screens changed visually, how many corpus entries are not replayable (and how many were repaired), whether a reorg is recommended, and how many proposals were written + how to promote them (`/tdd`).

## Hand-offs to the rest of the suite

- **To iterative-tdd:** proposals in `proposals/<slug>/` are runnable by `/tdd` to implement, after which they become real `.tdd/regression/` entries. Paste `task.md`; it points at the proposal's test plan so the planner picks up the intended tests and run command.
- **To hindsight:** the applied `priority`/`feature`/`serial` and the `audit.json`/`reorg.json` make hindsight's prioritized, grouped, parallel sweeps correct and meaningful.

## Token economy

- Pass sub-agents the project path and the relevant artifact paths — not file contents. They read from disk.
- After a sub-agent returns, read only the small structured outputs (`inventory.json` counts, `coverage.json` summary, `audit.json` totals, `reorg.json` proposals, `visual.json` summary). Don't inline screenshots or full reports into your own context.
- The deterministic core already writes both JSON (for tooling) and Markdown (for humans) — point the user at the files instead of restating them.

## Reference docs

- `reference/exploration.md` — how web/Android/iOS exploration works, the tools and external CLIs, screenshot/UI-map conventions, the visual pass, safety.
- `reference/coverage-model.md` — the inventory schema and how use cases / UI elements are matched to tests.
- `reference/regression-contract.md` — the `.tdd/regression` schema shared with iterative-tdd and hindsight, the proposal format, and the run-command repair.
- `reference/output-format.md` — every JSON/Markdown artifact foresight writes and its schema.
- `reference/schemas/*.schema.json` — the machine-checkable versions, used by `foresight.py validate`.


## When invoked as `/foresight`

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

Parse from `the arguments the user typed after the command`: a leading non-flag token is the exploration **target** (URL or app id); `--platforms` selects which platforms to explore; `--no-explore` does static-only; `--fix` passes `--fix-run-command` to the audit; `--apply-reorg` writes the reorg back to `replay.json`; `--propose N` sets the proposal count; `--rebaseline` records this run as the visual baseline; `--threshold PCT` sets the visual change tolerance. Default to `--project "$(pwd)"`.

Follow the skill's phase order exactly; delegate the reading/exploring/judging to the scoped sub-agents and use `.github/skills/foresight/scripts/foresight.py` for the deterministic steps.
