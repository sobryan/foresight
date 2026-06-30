# Coverage model — inventory & matching

## The inventory

The inventory is the catalog of *what the product does*. The cartographer
builds it from code + docs; the explorers enrich it with observed UI behavior.
It is the input to coverage analysis. Schema of `inventory/inventory.json`:

```json
{
  "generated_at_iso": "2026-05-28T17:30:00Z",
  "project": "/abs/path/to/project",
  "test_run_command": "pytest -q",
  "frameworks": ["pytest", "react"],
  "features": [
    {
      "id": "auth",
      "name": "Authentication",
      "sources": ["src/auth/login.ts:42", "docs/auth.md"],
      "use_cases": [
        {
          "id": "auth.login.valid",
          "name": "Log in with valid credentials",
          "platform": ["web", "ios", "android"],
          "user_facing": true,
          "ui_elements": ["#email", "button[type=submit]"],
          "evidence": ["exploration/20260528-180000/web/0001-login.png"]
        }
      ],
      "ui_elements": [
        {"id": "login-submit", "selector": "button[type=submit]", "role": "button",
         "behavior": "submits the login form", "use_case": "auth.login.valid"}
      ]
    }
  ]
}
```

Field notes:
- `id` values are stable dotted identifiers (`feature.thing.variant`) so coverage results are diffable across runs.
- `user_facing: true` raises a use case's risk score (a broken user-facing flow matters more).
- `test_run_command` is reused verbatim when generating proposals, so a proposed `replay.json` is actually replayable.

## Matching use cases / UI elements to tests

`foresight.py coverage` builds a "coverage signal" per regression entry from its
task text, slug, and test names, plus any file paths referenced in its
`test_plan.md`. For each inventory item it computes:

- **distinctive tokens** = lower-cased alphanumeric tokens of the item's name + id, minus stopwords.
- **literal match** = whether any of the item's selectors/routes appears verbatim in an entry's text.

Classification:

| Status | Condition |
|---|---|
| `covered` | a literal selector/route match, **or** ≥ 2 distinctive tokens overlap with some entry |
| `partial` | exactly 1 distinctive token overlaps and nothing stronger |
| `uncovered` | no entry shares a literal match or ≥ 1 distinctive token |

`matched_tests` lists the slugs that produced a `covered` verdict, so the result
is explainable, not a black box.

This is a deliberately conservative, deterministic heuristic — it can miss a
real match (false "uncovered") but rarely claims coverage that isn't there. The
`foresight-auditor` agent reviews `gaps.md` against the code to catch the
heuristic's misses before anything is proposed.

## Risk ranking

Each item gets a risk score: a base of 1, plus weights for risk-vocabulary
terms in its name/feature (auth, payment, delete, security, pii, … — see
`RISK_KEYWORDS` in `foresight.py`), plus 2 if `user_facing`. Gaps in `gaps.md`
are sorted highest-risk-first so the user fixes the dangerous holes before the
cosmetic ones. The same risk score drives `reorg`'s priority suggestion.

## Static (no-inventory) mode

Before any exploration has produced an inventory, `coverage` falls back to a
coarse signal: it lists top-level source areas (directories) that no regression
entry references at all. Useful as a first look; replaced by per-use-case
coverage once `inventory.json` exists.
