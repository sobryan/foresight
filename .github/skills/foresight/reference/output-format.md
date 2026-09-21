# Output formats

Everything foresight produces lives under `<project>/.tdd/foresight/`. Each
analysis writes a **machine-readable JSON** (for hindsight and other tooling)
and a **human-readable Markdown** alongside it. Every JSON artifact has a
schema in `schemas/` and `foresight.py validate` checks the ones present.

```
<project>/.tdd/foresight/
  inventory/   inventory.json   inventory.md   catalog.json   visual_candidates.md
  exploration/<ts>/{web,android,ios}/  NNNN-<slug>.png  pages.json  ui_map.json   summary.md
  coverage/    coverage.json    gaps.md
  audit/       audit.json       audit.md       auditor_notes.md
  reorg/       reorg.json       reorg.md       overrides.json
  visual/      baseline.json    visual.json    visual.md
  proposals/<slug>/  task.md  test_plan.md  replay.json  EVIDENCE.md
  report.md    report.html
```

## audit.json  (`schemas/audit.schema.json`)

```json
{
  "generated_at_iso": "…",
  "projects": ["/abs/project"],
  "n_entries": 5,
  "n_not_replayable": 4,
  "n_error_findings": 4,
  "n_warning_findings": 5,
  "health_score": 40,
  "entries": [
    {
      "slug": "…", "project": "/abs/project",
      "priority": "normal", "features": [],
      "replayable": false, "n_tests": 10,
      "findings": [
        {"code": "NO_RUN_COMMAND", "severity": "error", "message": "…"}
      ]
    }
  ]
}
```

Finding codes: `NO_RUN_COMMAND`, `MISSING_REPLAY_JSON`, `MALFORMED_REPLAY_JSON`,
`MISSING_TASK_MD`, `MISSING_TEST_PLAN`, `MISSING_PLAN_MD`, `EMPTY_TESTS`,
`INVALID_PRIORITY`, `INVALID_FEATURE`, `INVALID_SERIAL`, `NO_FEATURE`,
`DEFAULT_PRIORITY`, `NEVER_RUN`, `STALE_TEST_PATHS` (warning when *no*
referenced path exists, info when only some are missing), `DUPLICATE_SLUG`,
`NEAR_DUPLICATE` (task text ≥ 80 % the same as an earlier entry's).
Severities: `error` (blocks replay / corpus integrity), `warning` (quality),
`info` (feeds reorg). **Exit code is 1 when any `error` finding exists** — wire
`foresight audit` into CI the same way as hindsight's `replay-all`. In
`--all-projects` mode each project's `audit.json` carries that project's own
totals. `--fix-run-command` repairs empty commands before the audit runs.

## coverage.json  (`schemas/coverage.schema.json`)

```json
{
  "generated_at_iso": "…", "project": "/abs/project",
  "inventory_present": true,
  "summary": {"total": 12, "covered": 6, "weak": 1, "partial": 2, "uncovered": 3},
  "items": [
    {"id": "billing.refund", "kind": "use_case", "feature": "billing",
     "name": "process a refund payment", "platform": ["web"],
     "status": "weak", "matched_tests": [], "weak_matches": ["billing-status-guard"],
     "match_reason": "weak:2 (task text only)",
     "risk": 7.0, "evidence": ["…png"]}
  ]
}
```
`status` is `covered`, `weak`, `partial`, or `uncovered` (see
`coverage-model.md`). `weak_matches` names the entries that share words
with the item so a reviewer can confirm or dismiss; `match_reason` names the
rule that produced the verdict. When `inventory_present`
is false, a `static` block replaces per-item detail with source-area signals
(`areas_with_no_regression_reference`). `--fail-on-gap` exits 1 when an
`uncovered` or `weak` item has risk ≥ 5.

## reorg.json  (`schemas/reorg.schema.json`)

```json
{
  "generated_at_iso": "…", "projects": ["/abs/project"],
  "n_entries": 5, "n_with_changes": 5,
  "feature_buckets": {"api": 2, "payments": 1, "untagged": 2},
  "reorg_needed": true,
  "reorg_reasons": ["5/5 entries are still default 'normal' priority"],
  "proposals": [
    {"slug": "…", "project": "/abs/project",
     "current":   {"priority": "normal", "feature": [], "serial": false},
     "suggested": {"priority": "critical", "feature": ["payments"], "serial": true},
     "changes":   {"priority": "critical", "feature": ["payments"], "serial": true},
     "protected": [],
     "override":  {"priority": "critical", "reason": "live Stripe cutover 2026-05-24"}}
  ]
}
```
`changes` holds only the keys that differ from current — those are exactly what
`reorg --apply` writes back into `replay.json`. `protected` lists keys the
entry already sets explicitly; `--apply` skips them unless the key is named in
`override` or `--force` is passed. `override` echoes the accepted entry from
**`reorg/overrides.json`** (`schemas/overrides.schema.json`):

```json
{"<slug>": {"priority": "high", "feature": ["billing"], "serial": true, "reason": "…"}}
```

Invalid override values are dropped silently. A suggested feature of `[]` means
"keep what's there" and is never written.

## inventory/catalog.json  (`schemas/catalog.schema.json`)

Written by `foresight.py catalog` next to the marker block it injects into
`inventory.md`. One `record` per UI element with `missing` ⊆ {`user_story`,
`source_refs`, `screenshot`, `screenshot_file`} — the last means a screenshot
path was given but the file doesn't exist — plus `screenshot_exists` and a
`summary` with `completeness_pct`. `--fail-on-incomplete` exits 1 when any
record is incomplete.

## visual/baseline.json and visual/visual.json  (`schemas/visual-baseline.schema.json`, `schemas/visual.schema.json`)

```json
{"recorded_at_iso": "…", "project": "…", "run": "20260601-120000",
 "images": {"web/login.png": {"file": "exploration/20260601-120000/web/0001-login.png",
                              "sha256": "…", "width": 1280, "height": 800}}}
```

```json
{"generated_at_iso": "…", "project": "…", "baseline_present": true,
 "baseline_run": "20260601-120000", "run": "20260602-090000", "threshold_pct": 0.0,
 "summary": {"total": 3, "unchanged": 1, "changed": 1, "resized": 0, "new": 1, "missing": 0},
 "images": [{"key": "web/login.png", "status": "changed", "diff_pct": 3.42,
             "baseline_file": "…/0001-login.png", "file": "…/0001-login.png",
             "width": 1280, "height": 800, "baseline_width": 1280, "baseline_height": 800}]}
```
Images are keyed by `<platform>/<slug>.png` with the step number stripped.
`diff_pct` is `null` when the PNG couldn't be decoded or exceeds 4 megapixels
(then the hash decides). `visual --fail-on-change` exits 1 on any `changed`,
`resized`, or `missing` image.

## inventory.json / ui_map.json

See `coverage-model.md` (inventory) and `exploration.md` (ui_map). Schemas:
`schemas/inventory.schema.json`, `schemas/ui-map.schema.json`.

## report.md / report.html

`foresight.py report` assembles the top-level summary from whatever artifacts
exist: exploration runs, corpus audit, coverage, reorg, proposals, **Visual**
(the last comparison), **UI catalog** (completeness), and **Other artifacts**
(any file under `.tdd/foresight/` that this document doesn't name — the
agents' free-form notes). `--html` also writes a self-contained `report.html`
(no scripts, no external resources) for PRs and CI artifacts.

## validate

`foresight.py validate --project <p>` checks every artifact present against
its schema and exits 1 with `path: $.json.pointer: problem` lines on failure,
2 if the schema directory can't be found (`--schemas <dir>` to point at it).
Run it after any agent writes an artifact.

## Consumption by hindsight

hindsight does not need to learn a new format: foresight's value to hindsight is
delivered through the **applied `replay.json` metadata** (better `priority` /
`feature` / `serial` → better sorted, grouped, parallel sweeps) and the
repaired `run_command`s. `audit.json`, `reorg.json`, and `visual.json` are
additionally available for any dashboard or tooling that wants to surface
corpus health — they are stable, documented, schema-checked, and read-only.
