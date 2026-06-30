# foresight — Requirements

> **One line:** `foresight` is the forward-looking sibling of `hindsight`. Where hindsight *replays* the regressions you already have, foresight *discovers the regressions you don't have yet* and *validates that the ones you do have are sound, grouped, and prioritized* — by reading the code, the docs, and the running app (web, Android, iOS) and comparing what the product *does* against what the test suite *checks*.

---

## 1. Where foresight sits

The user runs a three-plugin pipeline:

```
   iterative-tdd                 foresight                    hindsight
 ───────────────        ─────────────────────────        ─────────────────
 Produces regression    Explores the product, finds       Replays the saved
 entries as a side      untested behavior, validates &    regression entries
 effect of the          organizes the regression corpus,  across all projects
 plan→test→judge loop.  proposes new entries for /tdd.     (CI-friendly).
        │                          │   ▲                          │
        │   writes .tdd/regression │   │ reads + repairs          │ reads
        └──────────────────────────┘   └──────────────────────────┘
```

- **iterative-tdd** writes regression entries to `<project>/.tdd/regression/<slug>/` as a by-product of a successful TDD session.
- **hindsight** discovers those entries across projects and replays them in priority order, grouped by feature, with a web dashboard.
- **foresight** is the *gap finder and corpus steward* in the middle: it figures out what *should* be a regression but isn't, and it keeps the existing corpus replayable, grouped, and prioritized so hindsight's sweeps are meaningful.

foresight reads and writes the **same on-disk contract** the other two plugins use (`.tdd/regression/<slug>/` and `replay.json`). It introduces no new central database.

---

## 2. The two core jobs

**Job A — Discover regressions that haven't been written yet.**
Build an inventory of what the product actually does — features, routes/screens, UI elements, and end-to-end use cases — by reading the code and docs and by *exploring the running app* (clicking through the web app, the Android app, the iOS app, and capturing screenshots of each E2E step). Cross-reference that inventory against the existing tests and regression entries. Anything the product does that nothing checks is a **coverage gap**. For the high-value gaps, emit a ready-to-run regression *proposal* in iterative-tdd's exact format so the user can hand it to `/tdd`.

**Job B — Validate and organize the existing regression corpus.**
For every existing `.tdd/regression/<slug>/`, check that the entry is *appropriate*: that it can actually be replayed (e.g. it has a non-empty `run_command` — today none of hindsight's own entries do, so hindsight silently no-ops on them), that its tests still map to real code, that it has a sensible `priority` and `feature` grouping, and that it isn't a duplicate of another entry. Produce a prioritized, feature-grouped reorganization plan that hindsight can consume — and optionally apply it by writing `priority`/`feature` back into each `replay.json` (backwards-compatibly).

---

## 3. Functional requirements

### FR-1 — Static discovery (read code, docs, shell)
1. Read source code across languages; identify features, modules, public entry points, routes/endpoints, and UI components.
2. Read project docs (`README`, `docs/`, ADRs, PRDs) to recover *intended* behavior and use cases.
3. Run read-only shell commands to extract structure (e.g. `git ls-files`, route/grep sweeps, framework detection, test discovery, `package.json`/`pyproject.toml`/build manifests).
4. Detect the test framework(s) and how the suite is invoked — reused later to fill `run_command` on proposed entries.
5. Emit a machine-readable **inventory** (`inventory.json`) of features → use cases → UI elements, with source citations.

### FR-2 — Live exploration of the running app (agent-driven)
1. **Web:** drive the running site through the Claude-in-Chrome browser tools — navigate routes, enumerate interactive UI elements, exercise primary E2E flows, and capture a **screenshot at each step**. Record what each UI element *does* (observed effect), not just that it exists.
2. **Android:** drive an app/emulator via documented external CLIs (Appium / Maestro / `adb`), capture screen images and the UI hierarchy per step.
3. **iOS:** drive an app/simulator via documented external CLIs (Appium / Maestro / `xcrun simctl`), capture screen images and the UI hierarchy per step.
4. All exploration is **observational and read-only by default** (no destructive actions) unless the user opts into a flow that mutates state, and then only against a non-production target.
5. Each exploration run is saved under a timestamped directory with screenshots, captured page/screen text, and a per-step **UI map** (element → role → observed behavior → the use case it belongs to).
6. foresight's deterministic Python core **never drives the app itself** — exploration is performed by the scoped explorer agents using the tools available in the session. The core consumes the inventory/exploration artifacts they produce.

### FR-3 — Coverage analysis
1. Build a **coverage map**: for each discovered use case and UI element, list the tests / regression entries that exercise it (matched via test-file references in `test_plan.md`, test names, route/selector strings, and the inventory).
2. Classify each item as `covered`, `partially-covered`, or `uncovered`.
3. Produce a **gap report** (JSON + Markdown) ranked by risk (user-facing + frequently-used + previously-broken rank highest).
4. Coverage analysis must run deterministically against whatever artifacts exist; richer results when an `inventory.json` from the exploration phase is present, degraded-but-useful results from static signals alone.

### FR-4 — Validate existing regression entries
For every `.tdd/regression/<slug>/`, detect and report:
1. **Not replayable** — `run_command` is missing or empty (hindsight returns `no_run_command` and the entry never actually runs). *This is the highest-severity finding.*
2. **Schema problems** — missing `replay.json`, missing `task.md`/`test_plan.md`/`plan.md`, malformed JSON, missing `tests` array.
3. **Invalid metadata** — `priority` not in {critical, high, normal, low}; `feature` not a string/list of strings; `serial` not a bool.
4. **Stale mapping** — `test_plan.md` references test files or paths that no longer exist in the repo.
5. **Never run** — no `runs/` history.
6. **Duplicates / collisions** — same slug across projects (hindsight first-wins-with-warning), or near-duplicate tasks/test plans.
7. Emit per-entry verdicts plus a corpus-level **health score** in `audit.json` (machine-readable for hindsight) and `audit.md` (human-readable).

### FR-5 — Organize & prioritize for hindsight
1. Propose a **`feature`** tag (or tags) for every entry by clustering on shared code areas, task tokens, and inventory features — so hindsight's `--feature` filter and grouped dashboard are meaningful.
2. Propose a **`priority`** (critical/high/normal/low) per entry from risk signals (auth/payments/data-loss/security → higher; cosmetic → lower; previously-failing → higher).
3. Recommend **`serial: true`** for entries that touch shared state (so hindsight's parallel `replay-all` stays correct).
4. Emit the recommendation as a **reorg plan** (`reorg.json` + `reorg.md`). `reorg --apply` writes `priority`/`feature`/`serial` back into each `replay.json` *backwards-compatibly* (preserve all other keys, back up originals, idempotent).
5. Flag when a **reorg is needed** (e.g. everything is `normal`, or a feature bucket has grown too large to be useful).

### FR-6 — Propose new regression entries
1. For selected high-value gaps, generate a **proposal** in iterative-tdd's exact regression format: a draft `task.md`, a `test_plan.md` with binary/metric pass criteria, and a `replay.json` (with a real `run_command`, proposed `priority`/`feature`).
2. Proposals are written under `.tdd/foresight/proposals/<slug>/` — **never** directly into `.tdd/regression/` (that remains iterative-tdd's job). The proposal is designed to be handed to `/tdd` to implement and to `/tdd-regression`/hindsight to replay thereafter.
3. Each proposal cites the evidence that motivated it (the screenshot, the route, the uncovered use case).

### FR-7 — Outputs for both machines and humans
1. **Machine-readable JSON** for hindsight and other tooling: `inventory.json`, `coverage.json`, `audit.json`, `reorg.json` — stable schemas, documented in `skills/foresight/reference/output-format.md`.
2. **Human-readable Markdown** reports for the same: an exploration summary, a coverage/gap report, an audit report, and a reorg plan.
3. A single **`report` command** assembles a top-level `report.md` (and optional self-contained `report.html`) summarizing the run: what was explored, what's uncovered, what's broken in the corpus, and the proposed reorg — suitable for a human reviewer or for pasting into a PR.
4. Exit codes on the deterministic commands are CI-friendly (0 = clean, non-zero = findings), mirroring hindsight's `replay-all`.

---

## 4. Integration contract

foresight depends on and preserves the shared `.tdd/` contract.

### Reads
- `<project>/.tdd/regression/<slug>/replay.json` — same `Entry` shape hindsight uses: `slug`, `saved_at_iso`, `original_session`, `task`, `run_command`, `tests[]`, and the extension fields `priority` (critical/high/normal/low, default normal), `feature` (string | string[] → normalized to list), `serial` (bool, default false).
- `<project>/.tdd/regression/<slug>/{task.md, plan.md, test_plan.md, README.md}` and `runs/<ts>/result.json`.
- The same **project-discovery model** as hindsight: `~/.config/hindsight/projects.yaml` if present, else auto-scan `~/Developer/*` for dirs containing `.tdd/regression/`. foresight also accepts an explicit `--project <path>` for single-project use (like iterative-tdd's `tdd_regression.py`).

### Writes
- All foresight output goes under `<project>/.tdd/foresight/` (a *new* sibling of `.tdd/sessions/` and `.tdd/regression/`). foresight does not modify `.tdd/sessions/`.
- The **only** writes foresight makes into `.tdd/regression/` are via `reorg --apply`, and they are limited to adding/updating the `priority`, `feature`, and `serial` keys in `replay.json` (with a `.bak` backup), so hindsight reads improved metadata without any schema change.
- New regression *content* is written as **proposals** under `.tdd/foresight/proposals/`, never into `.tdd/regression/` — promoting a proposal to a real entry is done by running it through iterative-tdd's `/tdd`.

### Hand-offs
- **From iterative-tdd:** the regression entries are foresight's audit input.
- **To iterative-tdd:** proposals are formatted to be runnable by `/tdd` (and `--regression` replay) without translation.
- **To hindsight:** `audit.json`/`reorg.json` and the applied `priority`/`feature`/`serial` fields make hindsight's prioritized, grouped, parallel sweeps correct and meaningful.

---

## 5. Non-functional requirements

- **NFR-1 Separation of concerns by tool scope.** Like iterative-tdd, each sub-agent's frontmatter restricts its tools. Explorer agents can drive the browser/shell but cannot edit code; the auditor is read-only plus its report; the architect can write proposals under `.tdd/foresight/` but cannot touch source or `.tdd/regression/` content.
- **NFR-2 Deterministic, LLM-free core for CI.** Everything in `scripts/foresight.py` (audit, coverage, reorg, report) runs without an LLM in the loop and is safe to invoke from CI — exactly like `hindsight.py`/`tdd_regression.py`. The LLM-driven exploration is the opt-in, interactive half.
- **NFR-3 Token economy.** The orchestrator passes agents a directory path, not file contents. Agents read from disk and write terse artifacts. The core reads only small structured files.
- **NFR-4 Backwards compatibility.** Reading and writing `replay.json` must never break entries that predate foresight. `reorg --apply` only *adds* recognized keys and preserves everything else.
- **NFR-5 No new central state.** Like hindsight, foresight keeps per-project artifacts in that project's `.tdd/`. Losing any global config loses only discovery, not history.
- **NFR-6 Read-only by default.** Static analysis and exploration never mutate the product under test unless explicitly directed at a safe target.
- **NFR-7 Stdlib-only core.** `foresight.py` uses only the Python standard library so it runs anywhere `hindsight.py` runs. Exploration tooling (browser/mobile) is documented as optional, externally-provided capability — not a Python dependency.

---

## 6. Architecture

Mirrors the iterative-tdd / hindsight family so it reads as a deliberate suite.

- **Orchestrator = the `foresight` skill** (not an agent). It walks the exploratory workflow deterministically, manages the `.tdd/foresight/` artifacts, and delegates the narrow work to scoped sub-agents.
- **Deterministic core = `scripts/foresight.py`** with subcommands `init`, `audit`, `coverage`, `reorg`, `report`.
- **Scoped sub-agents** (`agents/`):
  - `foresight-cartographer` — static: reads code/docs, runs read-only shell, emits `inventory.json`.
  - `foresight-web-explorer` — drives the web app via the Claude-in-Chrome tools; screenshots + UI map.
  - `foresight-mobile-explorer` — drives Android/iOS via Appium/Maestro/`adb`/`xcrun`; screenshots + UI map.
  - `foresight-auditor` — runs the deterministic coverage/audit, interprets results, validates existing regression plans.
  - `foresight-architect` — writes new-regression proposals (iterative-tdd format) and the priority/feature reorg plan.
- **All artifacts are plain Markdown/JSON on disk.** Nothing lives in agent memory between phases; the run is replayable and reviewable by `cat`.

---

## 7. Environment & capability requirements

| Capability | Required for | Provided by |
|---|---|---|
| Python 3.8+ on PATH | the deterministic core (`foresight.py`) | host |
| Read/Grep/Glob + read-only Bash | static discovery | the agent runtime |
| Claude-in-Chrome browser tools | **web** exploration + screenshots | the agent runtime |
| Appium **or** Maestro, plus `adb` + an emulator/device | **Android** exploration | user-installed, documented |
| Appium **or** Maestro, plus `xcrun simctl` + Xcode | **iOS** exploration | user-installed (macOS), documented |
| A reachable run target (dev server / installed app build) | live exploration | user-provided URL/app id |

foresight degrades gracefully: with none of the exploration tooling present it still performs full static discovery, coverage analysis from static signals, corpus audit, and reorg. Exploration simply enriches the inventory.

---

## 8. Output layout

```
<project>/.tdd/foresight/
  inventory/
    inventory.json            # features → use cases → UI elements (+ source citations)
    inventory.md
  exploration/<ts>/
    web/         screenshots + page text + ui_map.json
    android/     screenshots + ui hierarchy + ui_map.json
    ios/         screenshots + ui hierarchy + ui_map.json
    summary.md
  coverage/
    coverage.json             # per-item covered / partial / uncovered
    gaps.md                   # risk-ranked gap report
  audit/
    audit.json                # per-entry verdicts + corpus health score
    audit.md
  reorg/
    reorg.json                # proposed priority/feature/serial per slug
    reorg.md
  proposals/<slug>/
    task.md  test_plan.md  replay.json   # iterative-tdd-format, ready for /tdd
  report.md                   # top-level human summary
  report.html                 # optional, self-contained
```

---

## 9. Commands

| Command | Does | LLM in loop? |
|---|---|---|
| `/foresight [target]` | Full run: static discovery → exploration → coverage → audit → reorg plan → proposals → report | yes (exploration) |
| `/foresight-audit` | Deterministic corpus validation (FR-4). CI-friendly, exits non-zero on findings | no |
| `/foresight-coverage` | Build/refresh the coverage map + gap report (FR-3) | no (richer if inventory present) |
| `/foresight-propose [gap-id]` | Emit new-regression proposals for high-value gaps (FR-6) | yes |
| `/foresight-reorg [--apply]` | Propose (and optionally apply) priority/feature/serial grouping for hindsight (FR-5) | no |

---

## 10. Acceptance criteria

1. `foresight-audit` run against hindsight's own `.tdd/regression/` flags **every** entry as *not replayable* (empty `run_command`) — the concrete defect that exists today — and exits non-zero.
2. `reorg --apply` adds `priority`/`feature`/`serial` to a `replay.json` without disturbing any existing key, is idempotent, and leaves a `.bak`.
3. A generated proposal under `proposals/<slug>/` is structurally valid iterative-tdd regression content (`/tdd --regression <slug>` could consume it after promotion).
4. `coverage` produces a gap report from static signals alone, and a richer one when `inventory.json` is present.
5. The deterministic core imports only the standard library and runs under the same Python that runs `hindsight.py`.
6. All four JSON outputs validate against the schemas documented in `reference/output-format.md`.
7. The plugin installs and exposes its commands the same way iterative-tdd and hindsight do.

---

## 11. Non-goals (this version)

- foresight does **not** run the TDD loop or implement code — proposals are handed to iterative-tdd.
- foresight does **not** replay regressions — that is hindsight's job.
- No continuous/watch mode, no git-hook auto-trigger (future work).
- No writing of new entries directly into `.tdd/regression/` (only metadata via `reorg --apply`).
- No production-data mutation during exploration.
- No bundled Playwright/Appium harness — exploration uses the agent runtime's tools and documented external CLIs.

## 12. Future work

- Watch mode: re-explore + re-audit on change.
- LLM-assisted triage that links a hindsight failure to the commit that caused it.
- A foresight web view (or a panel inside hindsight's dashboard) for the coverage map.
- Per-entry `run_command` inference confidence scoring.

## 13. Naming

`foresight` completes the set with `hindsight`. Hindsight looks back at what broke and replays it; foresight looks ahead at what *could* break but isn't covered yet, and keeps the corpus it inherits sound. Run together — hindsight replaying, foresight predicting — they read as one suite.
