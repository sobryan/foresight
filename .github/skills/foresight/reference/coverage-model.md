# Coverage model — inventory & matching

## The inventory

The inventory is the catalog of *what the product does*. The cartographer
builds it from code + docs; the explorers enrich it with observed UI behavior;
the visual inspector adds what a person sees in the screenshots; the
cartographer then backfills where the code lives. It is the input to coverage
analysis and to the catalog. Schema of `inventory/inventory.json` (machine
version: `schemas/inventory.schema.json`):

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
          "sources": ["src/auth/login.ts"],
          "user_story": "As a returning user, I want to sign in so that I reach my dashboard",
          "evidence": ["exploration/20260528-180000/web/0001-login.png"]
        }
      ],
      "ui_elements": [
        {"id": "login-submit", "selector": "button[type=submit]", "role": "button",
         "behavior": "submits the login form", "use_case": "auth.login.valid",
         "user_story": "As a returning user, I want to submit my credentials so that I can sign in",
         "source_refs": ["src/auth/LoginForm.tsx:88"],
         "visual": {"screenshot": "exploration/20260528-180000/web/0001-login.png",
                    "region": [412, 380, 160, 44], "label": "Sign in",
                    "discovered_by": "visual"}}
      ]
    }
  ]
}
```

Field notes:
- `id` values are stable dotted identifiers (`feature.thing.variant`) so coverage results are diffable across runs.
- `user_facing: true` raises a use case's risk score (a broken user-facing flow matters more).
- `test_run_command` is reused verbatim when generating proposals, so a proposed `replay.json` is actually replayable.
- `sources` (use case) and `source_refs` (UI element) are **optional** `path` or `path:line` citations. The matcher uses them: a regression entry whose test plan references the same file counts as covering the item.
- `user_story` and `visual` are **optional**, written by the visual inspector. The deterministic coverage core ignores them; `foresight.py catalog` consumes them, and `visual.screenshot` doubles as coverage `evidence`. `region` and `label` are opaque pass-through.
- `discovered_by` is `visual` (seen in a screenshot), `exploration` (found while driving the app), or `code` (found by the cartographer).

## Matching use cases / UI elements to tests

`foresight.py coverage` builds a "coverage signal" per regression entry from its
task text, slug, and test names, plus the file paths referenced in its
`test_plan.md` and `run_command`. For each inventory item it computes:

- **distinctive tokens** = lower-cased alphanumeric tokens of the item's name + id, minus stopwords **and minus generic product words** (`user`, `plan`, `page`, `button`, `role`, `api`, … — `MATCH_STOPWORDS` in `foresight.py`). Those words appear in almost every task and say nothing about *which* behaviour is tested.
- **specific tokens** of an entry = the tokens of its slug and its test names — what the entry *checks*, as opposed to what its task text mentions.
- **literal match** = a *selector-like* string (contains one of `# . [ ] = : > / @`, or is a hyphenated/underscored token with no spaces) appears verbatim in an entry's text. Plain words such as `button` or `Sign in` never literal-match — they occur in ordinary prose.
- **source match** = the item's `sources` / `source_refs` name a file that an entry's test plan or run command references.

Classification:

| Status | Condition |
|---|---|
| `covered` | a literal match, **or** a source match, **or** a token overlap that is *enough* (≥ 3 shared tokens, or 2 on an item with ≤ 3 distinctive tokens) **and** *checked* (at least one shared token is among the entry's specific tokens) |
| `weak` | a token overlap of ≥ 2 that is not both enough and checked — e.g. a long "sweep" task that mentions the feature without a test named for it. **Reported as a gap**; the slugs are listed in `weak_matches` so a reviewer can confirm or dismiss |
| `partial` | exactly 1 distinctive token overlaps and nothing stronger |
| `uncovered` | no entry shares a literal match, a source, or any distinctive token |

`matched_tests` lists the slugs that produced a `covered` verdict and
`match_reason` says which rule fired (`literal:<selector>`, `source:ref`,
`tokens:<n>`, `weak:<n>`, `weak:<n> (task text only)`, `partial:1`, `none`),
so the result is explainable, not a black box.

This is a deliberately conservative, deterministic heuristic — it would rather
report a false gap than claim coverage that isn't there, because nothing
downstream re-examines a `covered` item. The `foresight-auditor` agent reviews
`gaps.md` against the code to catch the heuristic's misses (in both
directions) before anything is proposed.

## Risk ranking

Each item gets a risk score: a base of 1, plus weights for risk-vocabulary
terms in its name/feature (auth, payment, delete, security, pii, … — see
`RISK_KEYWORDS` in `foresight.py`), plus 2 if `user_facing`. Gaps in `gaps.md`
are sorted highest-risk-first so the user fixes the dangerous holes before the
cosmetic ones. The same risk score drives `reorg`'s priority suggestion.
`coverage --fail-on-gap` exits non-zero when an `uncovered` or `weak` item has
risk ≥ 5.

## Static (no-inventory) mode

Before any exploration has produced an inventory, `coverage` falls back to a
coarse signal: it lists top-level source areas (directories) that no regression
entry references at all. Useful as a first look; replaced by per-use-case
coverage once `inventory.json` exists.
