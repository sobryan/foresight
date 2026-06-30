# Output formats

Everything foresight produces lives under `<project>/.tdd/foresight/`. Each
analysis writes a **machine-readable JSON** (for hindsight and other tooling)
and a **human-readable Markdown** alongside it.

```
<project>/.tdd/foresight/
  inventory/   inventory.json   inventory.md
  exploration/<ts>/{web,android,ios}/  *.png  pages.json  ui_map.json   summary.md
  coverage/    coverage.json    gaps.md
  audit/       audit.json       audit.md
  reorg/       reorg.json       reorg.md
  proposals/<slug>/  task.md  test_plan.md  replay.json  EVIDENCE.md
  report.md    report.html (optional)
```

## audit.json

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
`DEFAULT_PRIORITY`, `NEVER_RUN`, `STALE_TEST_PATHS`, `DUPLICATE_SLUG`.
Severities: `error` (blocks replay / corpus integrity), `warning` (quality),
`info` (feeds reorg). **Exit code is 1 when any `error` finding exists** — wire
`foresight audit` into CI the same way as hindsight's `replay-all`.

## coverage.json

```json
{
  "generated_at_iso": "…", "project": "/abs/project",
  "inventory_present": true,
  "summary": {"total": 12, "covered": 7, "partial": 2, "uncovered": 3},
  "items": [
    {"id": "billing.refund", "kind": "use_case", "feature": "billing",
     "name": "process a refund payment", "platform": ["web"],
     "status": "uncovered", "matched_tests": [], "risk": 7.0, "evidence": ["…png"]}
  ]
}
```
When `inventory_present` is false, a `static` block replaces per-item detail with
source-area signals (`areas_with_no_regression_reference`).

## reorg.json

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
     "changes":   {"priority": "critical", "feature": ["payments"], "serial": true}}
  ]
}
```
`changes` holds only the keys that differ from current — those are exactly what
`reorg --apply` writes back into `replay.json`.

## inventory.json / ui_map.json

See `coverage-model.md` (inventory) and `exploration.md` (ui_map).

## Consumption by hindsight

hindsight does not need to learn a new format: foresight's value to hindsight is
delivered through the **applied `replay.json` metadata** (better `priority` /
`feature` / `serial` → better sorted, grouped, parallel sweeps). `audit.json`
and `reorg.json` are additionally available for any dashboard or tooling that
wants to surface corpus health — they are stable, documented, and read-only.
