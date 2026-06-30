# Visual UI-Element Inventory + User-Story Catalog (foresight "Job A" extension)

## Context

foresight's "Job A" (discovery) currently inventories UI elements from **code and the
running app's DOM/accessibility tree** — the cartographer reads source, the explorers
drive the app and record selectors + observed behavior. What it does *not* do is look at
the **screenshots it already captures** and reason about the UI the way a person would:
enumerate every visually-distinct control, say *why a user would use it* (a user story),
and tie it back to the code that implements it.

This feature adds that visual layer. A new **visual-inspection agent** walks an exploration
run's screenshots, enumerates every visible UI element, derives a user story per element,
and (via the cartographer) associates each element with its source `file:line`. All of this
enriches the existing `inventory.json`. A new **deterministic, pytest-tested** `catalog`
subcommand then renders this into human+LLM-readable documentation **inside the existing
`inventory.md`**, and reports which elements are under-documented. High-value user stories
feed the existing regression-proposal path so visual inspection also yields *testable
regressions* — exactly the "regression, info, documentation" triad the user asked for.

**Build method:** the deterministic `catalog` generator is built test-first via `/tdd`
(it has a real `run_command` and binary tests). The agent/skill markdown changes are made
alongside in the same effort and verified by review (they are not unit-testable).

### Architectural guardrails (must hold)
- The deterministic core (`scripts/foresight.py`) stays **pure stdlib, no LLM, never drives
  the app**. It only *reads/aggregates/validates* the new inventory fields.
- All new `inventory.json` fields are **optional sibling additions** — `build_coverage`,
  `_match_item`, `suggest_features`, `apply_reorg` read none of them, so nothing existing breaks.
- Documentation lands in **`inventory/inventory.md`** (per the chosen approach), via an
  **idempotent marker-delimited section** — no new `ui_catalog.*` files.
- `source_refs` are written by the **cartographer agent** (grep + judgment). The core does
  not generate or path-validate them; it only checks presence for the completeness report.

---

## Schema extension (backwards-compatible)

New **optional** fields on `features[].ui_elements[]` in `inventory.json`:

| Field | Type | Written by | Meaning |
|---|---|---|---|
| `user_story` | string | visual agent | `"As a <role>, I want <action> so that <benefit>"` |
| `source_refs` | `["file:line", ...]` | cartographer | code that implements the element (same shape as `features[].sources`) |
| `visual` | object | visual agent | provenance: `{ "screenshot": "<path>", "region": [x,y,w,h] or null, "label": "<visible text>", "discovered_by": "visual"|"code"|"exploration" }` |

Optional rollup field on `use_cases[]`: `user_story` (string) — the section-level story the
element stories nest under.

The completeness rule keys only on simple presence of `user_story`, `source_refs`, and
`visual.screenshot`; `region`/`label` are opaque pass-through (loose agent output can't break
the generator).

---

## Part A — Deterministic `catalog` generator (built via `/tdd`)

All additions go in `scripts/foresight.py`, slotted after the COVERAGE block, mirroring the
shape of `cmd_coverage`/`_coverage_markdown`. Reuse existing helpers: `_read_json`,
`_write_text`, `_now_iso`, `_foresight_root`, `resolve_projects`, `_add_project_args`.

**Functions**
- `_element_completeness(el) -> list[str]` — returns the missing aspects (`"user_story"`,
  `"source_refs"`, `"screenshot"`); empty list = complete.
- `build_catalog(project) -> dict` — reads `inventory/inventory.json`; for every feature,
  normalizes each `ui_element` into a doc record (`id, selector, role, behavior, use_case,
  user_story, source_refs, screenshot, region, label, missing`); computes a `summary`
  (`n_features, n_elements, n_use_cases, n_with_story, n_with_source_refs, n_with_screenshot,
  n_complete, n_incomplete, completeness_pct`) and a flat `incomplete[]` list. Returns the
  dict (does **not** write files). Graceful when no inventory: `inventory_present=False`,
  `n_elements=0`, `completeness_pct=100`.
- `_catalog_markdown_section(cat) -> str` — renders the catalog block: a summary line, a
  `### Documentation completeness` subsection listing incomplete elements, then per-feature
  groups where each element shows its screenshot as a Markdown image link, the `user_story`
  as a blockquote, and `source_refs` as inline-code `file:line`.
- `_inject_catalog_section(md_path, section)` — idempotently writes the block into
  `inventory.md` between `<!-- foresight:catalog:start -->` / `<!-- foresight:catalog:end -->`
  markers: replaces the existing block if markers are present, else appends them; creates
  `inventory.md` if absent. Re-running with the same inventory produces a byte-identical file.
- `cmd_catalog(args)` — for each project: `build_catalog`, inject the section into
  `inventory/inventory.md`, print a one-line summary (or `--json` dump of the dict). Returns
  exit code 1 when `--fail-on-incomplete` is set **and** any element is incomplete, else 0.

**CLI** (in `build_parser`, mirroring the `coverage` subparser):
```
p_cat = sub.add_parser("catalog", help="render the UI-element + user-story catalog into inventory.md")
_add_project_args(p_cat)
p_cat.add_argument("--json", action="store_true")
p_cat.add_argument("--fail-on-incomplete", action="store_true",
                   help="exit non-zero if any UI element lacks a story / source_refs / screenshot")
p_cat.set_defaults(func=cmd_catalog)
```

**`cmd_report`** — add a `## UI catalog` section that calls `build_catalog(project)` (cheap,
no LLM) and prints `n_elements`, `completeness_pct`, and the top ~5 incomplete elements; or
`- not enriched yet — run the visual pass + \`foresight.py catalog\`` when `n_elements == 0`.

`cmd_init` is unchanged (documentation lives in the existing `inventory/` dir — no new subdir,
less risk).

---

## Part B — Agent & skill changes (built alongside; verified by review)

- **New `agents/foresight-visual-inspector.md`** (tools: `Read, Write, Glob, Grep, Bash`;
  read-only on source). Runs in Phase 2 over an exploration run's screenshots
  (`exploration/<ts>/{web,android,ios}/NNNN-*.png`) — without re-driving the app. For each
  screenshot: enumerate every visually-distinct element (buttons, fields, links, toggles,
  icons, menu items, cards, badges), including ones the DOM/hierarchy pass missed; record
  `visual.{screenshot,region,label,discovered_by:"visual"}`; derive a strict-format
  `user_story`; match to an existing `ui_element` by selector/label or create a new one; and
  merge into `inventory.json` (never delete cartographer/explorer entries). It does **not**
  fill `source_refs`. It flags high-value stories as regression candidates in its summary.
- **`agents/foresight-cartographer.md`** — add a **source_refs backfill** responsibility:
  for each `ui_element` (especially visual-only ones), grep/Glob the codebase for the
  `selector`/`visual.label`/visible text and populate `source_refs` (`["path:line", ...]`);
  omit rather than guess a line. Re-invoked after the visual pass (Phase 2b). Stays read-only.
- **`agents/foresight-architect.md`** — note that `ui_element.user_story` is prime material
  for `task.md`/`EVIDENCE.md`, so visual stories become concrete `/tdd` regression proposals
  (the "testable regression from visual inspection" path). No tool changes.
- **Docs:**
  - `skills/foresight/reference/coverage-model.md` — add the new optional `ui_element`/
    `use_case` fields to the `inventory.json` example + a note that the deterministic coverage
    core ignores them and the `catalog` command consumes them.
  - `skills/foresight/reference/exploration.md` — add a "Visual-inspection pass" subsection
    and extend the `ui_map.json` element example with `user_story`.
  - `skills/foresight/SKILL.md` — document the new fields, insert **Phase 2 visual pass**,
    **Phase 2b source_refs backfill**, and a **catalog** step
    (`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foresight.py" catalog --project "$(pwd)"`) that
    runs after inventory enrichment and before coverage; note `inventory.md` is enriched
    in-place via markers and `--fail-on-incomplete` is a CI gate meaningful only after the
    visual pass ran.
  - `commands/foresight.md` — add the visual pass + catalog steps to the numbered workflow.
  - **New `commands/foresight-catalog.md`** — thin command running only the catalog phase
    (mirror `commands/foresight-coverage.md`).

**Ordering note:** within a `/foresight` run, `catalog` must be the **last writer to
`inventory.md`** (cartographer/explorers (re)write it earlier; the marker block is injected
after). Re-running the cartographer later means re-running `catalog`.

---

## The `/tdd` task (Part A only)

**Task string:**
> Add a deterministic `catalog` subcommand to `scripts/foresight.py` that reads
> `<project>/.tdd/foresight/inventory/inventory.json` and renders a UI-element + user-story
> catalog into the existing `inventory/inventory.md` between idempotent
> `<!-- foresight:catalog:start/end -->` markers — grouped by feature, each element showing
> selector, role, behavior, `user_story`, `source_refs` (file:line), and a screenshot image
> link. Implement `_element_completeness(el)`, `build_catalog(project)` (returns a dict with a
> `summary` and flat `incomplete[]`, never writes files), `_catalog_markdown_section(cat)`,
> `_inject_catalog_section(md_path, section)` (idempotent), and `cmd_catalog(args)`; wire a
> `catalog` argparse subparser with `--project/--all-projects/--json/--fail-on-incomplete`;
> and add a "UI catalog" section to `cmd_report`. An element is documentation-complete when it
> has a non-empty `user_story`, ≥1 `source_refs`, and a `visual.screenshot`. `--fail-on-incomplete`
> returns exit 1 when any element is incomplete (0 otherwise, including when no inventory exists).
> All new inventory fields are optional/backwards-compatible and the core must never call an
> LLM. Put all tests in a new `tests/test_catalog.py` and pass them without modifying any
> existing test.

**run_command:** `pytest -q tests/test_catalog.py` (final guard: full `pytest -q`).

**Tests (binary), in new `tests/test_catalog.py`:**
1. `test_catalog_lists_every_ui_element` — 4 elements across 2 features → `summary.n_elements == 4`, every id present.
2. `test_catalog_carries_new_fields` — record echoes `user_story`, `source_refs`, `visual.screenshot` verbatim.
3. `test_catalog_flags_missing_user_story` — element w/o story → `"user_story" in record.missing` and in `incomplete`.
4. `test_catalog_flags_missing_source_refs_and_screenshot` — → `missing == ["source_refs","screenshot"]`.
5. `test_catalog_complete_element_not_flagged` — full element → `missing == []`, in `n_complete`, absent from `incomplete`.
6. `test_catalog_completeness_counts` — 2 of 4 complete → exact `n_with_*`, `n_complete`, `n_incomplete`, `completeness_pct == 50`.
7. `test_cmd_catalog_fail_on_incomplete_exit1` — incomplete + flag → `main([...]) == 1`.
8. `test_cmd_catalog_complete_corpus_exit0` — all complete + flag → `0`.
9. `test_cmd_catalog_no_flag_exit0_despite_incomplete` — incomplete, no flag → `0`.
10. `test_catalog_no_inventory_is_graceful` — no inventory → `inventory_present False`, `n_elements 0`, `pct 100`, flagged run → `0`.
11. `test_catalog_injects_idempotent_section` — run twice → `inventory.md` byte-identical; markers present exactly once.
12. `test_catalog_enriches_without_clobbering` — pre-existing prose above the markers survives injection.
13. `test_catalog_markdown_has_story_screenshot_sources` — section contains the screenshot path, the `user_story`, and a `source_refs` `file:line`.
14. `test_catalog_grouped_by_feature` — section contains a heading per feature `name`.
15. `test_report_includes_catalog_section` — after `catalog` then `report`, `report.md` contains `"UI catalog"` + the percent.

---

## Files

- `scripts/foresight.py` — new functions + subparser + `cmd_report` section *(TDD)*
- `tests/test_catalog.py` — new test file *(TDD)*
- `agents/foresight-visual-inspector.md` — new agent *(review)*
- `agents/foresight-cartographer.md` — source_refs backfill *(review)*
- `agents/foresight-architect.md` — user_story → proposal note *(review)*
- `skills/foresight/SKILL.md`, `reference/coverage-model.md`, `reference/exploration.md` *(review)*
- `commands/foresight.md`, new `commands/foresight-catalog.md` *(review)*

---

## Verification

1. **TDD loop:** `pytest -q tests/test_catalog.py` green, then `pytest -q` (all existing
   tests still pass — confirms the schema additions + `cmd_report` change broke nothing).
2. **End-to-end on a sample inventory:** write an `inventory.json` with enriched
   `ui_element`s under a temp project, run
   `python3 scripts/foresight.py catalog --project <tmp>`, and confirm `inventory.md` gains
   the marker-delimited catalog section with stories, screenshot links, and `source_refs`;
   re-run and confirm the file is byte-identical (idempotent).
3. **CI gate:** run `catalog --fail-on-incomplete` against an inventory with a missing
   `user_story` and confirm exit 1; complete it and confirm exit 0.
4. **Report integration:** `python3 scripts/foresight.py report --project <tmp>` and confirm
   the `## UI catalog` section shows the completeness percent.
5. **Agent layer (manual review):** confirm the visual-inspector agent's documented procedure
   writes the new fields into `inventory.json`, the cartographer backfills `source_refs`, and
   SKILL.md/commands wire the catalog phase after inventory enrichment and before coverage.
