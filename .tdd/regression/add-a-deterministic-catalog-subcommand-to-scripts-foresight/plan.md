# Plan — iteration 1

## Problem statement

Add a `catalog` subcommand to `scripts/foresight.py` that reads optional new fields
(`user_story`, `source_refs`, `visual`) from `<project>/.tdd/foresight/inventory/inventory.json`,
renders a grouped UI-element + user-story catalog into `inventory/inventory.md` between
idempotent HTML comment markers, and reports which elements are under-documented.
The core is pure stdlib, never calls an LLM, and never breaks existing behavior.
All new tests live in `tests/test_catalog.py`; `pytest -q tests/test_catalog.py` is the
run command.

## Context I gathered

### Existing codebase patterns

- `scripts/foresight.py` — 965 lines, pure stdlib. Key helpers in scope:
  - `_read_json(path)` (line 104) — returns `None` on OSError or bad JSON.
  - `_write_text(path, text)` (line 116) — creates parent dirs, enforces trailing newline.
  - `_now_iso()` (line 96) — UTC ISO timestamp.
  - `_foresight_root(project)` (line 142) — returns `project / ".tdd" / "foresight"`.
  - `resolve_projects(args)` (line 186) — honors `--project` / `--all-projects`.
  - `_add_project_args(p)` (line 917) — adds `--project` and `--all-projects` to any subparser.
  - `build_coverage(project)` (line 529) — model for `build_catalog`; iterates
    `features[].use_cases[]` and `features[].ui_elements[]`; returns a dict without writing files.
  - `_coverage_markdown(cov)` (line 618) — model for `_catalog_markdown_section`.
  - `cmd_coverage(args)` (line 652) — model for `cmd_catalog`; calls `build_*`, writes artifacts, prints summary, returns exit code.
  - `cmd_report(args)` (line 861) — needs a new `## UI catalog` section appended before the `_write_text` call.
  - `build_parser()` (line 923) — all subparsers wired here; `catalog` subparser goes after `coverage`.

- `inventory.json` schema (existing): `{ "features": [{ "id", "name", "use_cases": [{...}], "ui_elements": [{...}] }] }`.
  - `use_cases[].ui_elements` is a list of *selector strings* (used by coverage for matching).
  - `features[].ui_elements` is a list of *element objects* with `id`, `selector`, `role`, `behavior`.

- New optional fields on `features[].ui_elements[]` (all optional, never required by existing code):
  - `user_story`: string ("As a … so that …")
  - `source_refs`: list of "file:line" strings
  - `visual`: object with sub-keys `screenshot` (string path), `region` (array or null), `label` (string), `discovered_by` (string)

- `tests/test_foresight.py` — mirrors the import pattern: `sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))` then `import foresight`. Uses `tmp_path` fixture, `foresight.main([...])` for CLI tests, a `write_inventory(project, features)` helper.

### Inventory path
`_foresight_root(project) / "inventory" / "inventory.json"` — same path that `build_coverage` reads (line 532 of foresight.py).

### Catalog output path
`_foresight_root(project) / "inventory" / "inventory.md"` — same dir as inventory.json.

### Insertion-marker convention (new, established here)
`<!-- foresight:catalog:start -->` … `<!-- foresight:catalog:end -->`

---

## Proposed approach

### 1. `_element_completeness(el: dict) -> list[str]`

Slot: just before the CATALOG block (after `cmd_coverage`).

Returns a list of zero or more strings from `["user_story", "source_refs", "screenshot"]`:
- Append `"user_story"` if `not el.get("user_story")` (absent or empty string).
- Append `"source_refs"` if `not el.get("source_refs")` (absent, None, or empty list).
- Append `"screenshot"` if `not (el.get("visual") or {}).get("screenshot")` (absent, None, or empty string).

Empty return = element is complete.

### 2. `build_catalog(project: Path) -> dict`

Reads `_foresight_root(project) / "inventory" / "inventory.json"` via `_read_json`.

**Graceful fallback** (no inventory or invalid JSON):
```python
{
    "generated_at_iso": _now_iso(),
    "project": str(project),
    "inventory_present": False,
    "summary": {
        "n_features": 0, "n_elements": 0, "n_use_cases": 0,
        "n_with_story": 0, "n_with_source_refs": 0, "n_with_screenshot": 0,
        "n_complete": 0, "n_incomplete": 0, "completeness_pct": 100,
    },
    "records": [],
    "incomplete": [],
}
```

**When inventory present** — iterate `inv["features"]`:
- Count `n_features`, `n_use_cases` (sum of `len(feature.get("use_cases", []))`) per feature.
- For each element in `feature.get("ui_elements", [])` (the object list, not use_case selector strings):
  - Build a `record` dict:
    ```python
    {
        "id": el.get("id", ""),
        "feature": feature.get("id", feature.get("name", "")),
        "selector": el.get("selector", ""),
        "role": el.get("role", ""),
        "behavior": el.get("behavior", ""),
        "use_case": el.get("use_case", ""),
        "user_story": el.get("user_story", ""),
        "source_refs": el.get("source_refs") or [],
        "screenshot": (el.get("visual") or {}).get("screenshot", ""),
        "region": (el.get("visual") or {}).get("region"),
        "label": (el.get("visual") or {}).get("label", ""),
        "missing": _element_completeness(el),
    }
    ```
  - Accumulate counts for `n_with_story`, `n_with_source_refs`, `n_with_screenshot`, `n_complete`, `n_incomplete`.
- Compute `completeness_pct = round(100 * n_complete / n_elements, 1) if n_elements else 100`.
- Return:
  ```python
  {
      "generated_at_iso": _now_iso(),
      "project": str(project),
      "inventory_present": True,
      "summary": { n_features, n_elements, n_use_cases, n_with_story, n_with_source_refs,
                   n_with_screenshot, n_complete, n_incomplete, completeness_pct },
      "records": records,      # all elements
      "incomplete": [r for r in records if r["missing"]],
  }
  ```

Does **not** write any files.

### 3. `_catalog_markdown_section(cat: dict) -> str`

Returns a string (no trailing newline needed — `_inject_catalog_section` handles that).
**Must be fully deterministic** — no timestamp — so idempotency holds.

Structure:
```
## UI element catalog

N elements across F features — C complete (P%)

### Documentation completeness

(if n_incomplete > 0:)
The following elements are missing documentation:

- `<id>` (`<feature>`): missing user_story, source_refs
...

(if n_incomplete == 0:)
All N elements are fully documented.

### Elements by feature

#### <feature name>

##### <element id> — <role> <selector>

> <user_story or *(no user story)*>

![<label>](<screenshot>) (or *(no screenshot)*)

Sources: `file:line`, `file:line`  (or *(no source refs)*)

---
```

Key rendering choices:
- `###` for "Documentation completeness" subsection.
- Per-feature heading: `#### <feature id/name>` (one per distinct feature).
- Per-element heading: `#####` with id + role + selector.
- Screenshot as standard Markdown image: `![label](screenshot)`.
- `user_story` as blockquote line: `> user_story`.
- `source_refs` as comma-separated inline code spans.
- Separator `---` between elements.

Tests T13 and T14 verify: section contains the screenshot path, the user_story text, a source_ref file:line string, and a heading per feature name.

### 4. `_inject_catalog_section(md_path: Path, section: str) -> None`

Injection algorithm (idempotent):

```
START_MARKER = "<!-- foresight:catalog:start -->"
END_MARKER   = "<!-- foresight:catalog:end -->"

block = START_MARKER + "\n" + section.rstrip("\n") + "\n" + END_MARKER + "\n"

if md_path exists:
    existing = md_path.read_text()
    if START_MARKER in existing:
        # replace between markers (inclusive) using regex
        new_content = re.sub(
            r"<!-- foresight:catalog:start -->.*?<!-- foresight:catalog:end -->",
            block.rstrip("\n"),   # sub doesn't add trailing newline; we normalize after
            existing,
            flags=re.DOTALL,
        )
    else:
        # append, with a blank separator line
        new_content = existing.rstrip("\n") + "\n\n" + block
else:
    new_content = block

# write via _write_text which enforces trailing newline and creates parent dirs
_write_text(md_path, new_content)
```

Idempotency: given the same `section` string, the regex replacement always produces the same output. Running a second time with the same section text leaves the file byte-identical.

### 5. `cmd_catalog(args) -> int`

```python
def cmd_catalog(args) -> int:
    projects = resolve_projects(args)
    any_incomplete = False
    for project in projects:
        cat = build_catalog(project)
        md_path = _foresight_root(project) / "inventory" / "inventory.md"
        section = _catalog_markdown_section(cat)
        _inject_catalog_section(md_path, section)
        if args.json:
            print(json.dumps(cat, indent=2))
        else:
            s = cat["summary"]
            if not cat["inventory_present"]:
                print(f"{project.name}: no inventory — skipped")
            else:
                print(f"{project.name}: {s['n_elements']} elements, "
                      f"{s['completeness_pct']}% complete "
                      f"({s['n_incomplete']} incomplete)")
        if cat["incomplete"]:
            any_incomplete = True
    if getattr(args, "fail_on_incomplete", False) and any_incomplete:
        return 1
    return 0
```

### 6. `cmd_report` modification

In `cmd_report`, after the "## Proposed new regressions" block and before the `_write_text(root / "report.md", ...)` call, add:

```python
out += ["", "## UI catalog", ""]
cat = build_catalog(project)
s = cat["summary"]
if s["n_elements"] == 0:
    out.append("- not enriched yet — run the visual pass + `foresight.py catalog`")
else:
    out.append(f"- {s['n_elements']} elements, {s['completeness_pct']}% complete")
    top_incomplete = cat["incomplete"][:5]
    for r in top_incomplete:
        out.append(f"  - `{r['id']}` missing: {', '.join(r['missing'])}")
```

### 7. Argparse wiring in `build_parser()`

Add after the `p_cov` block and before `p_reorg`:

```python
p_cat = sub.add_parser("catalog",
                        help="render the UI-element + user-story catalog into inventory.md")
_add_project_args(p_cat)
p_cat.add_argument("--json", action="store_true", help="emit JSON to stdout")
p_cat.add_argument("--fail-on-incomplete", action="store_true",
                   help="exit non-zero if any UI element lacks a story / source_refs / screenshot")
p_cat.set_defaults(func=cmd_catalog)
```

`argparse` converts `--fail-on-incomplete` to `args.fail_on_incomplete` automatically.

### 8. New `tests/test_catalog.py`

Header mirrors `test_foresight.py`:
```python
"""Tests for the catalog subcommand.

Run: pytest -q tests/test_catalog.py
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import foresight  # noqa: E402
```

Helper `write_catalog_inventory(project, features)` — writes `inventory.json` with
`{"generated_at_iso": ..., "project": str(project), "features": features}` to
`project / ".tdd" / "foresight" / "inventory" / "inventory.json"`, creating parent dirs.

Helper `complete_el(**overrides)` — returns a fully-complete element dict (has user_story,
source_refs, visual with screenshot), with optional overrides so tests can drop specific fields.

**Tests — one function per T-number:**

- **T1** `test_catalog_lists_every_ui_element`: 4 elements across 2 features →
  `cat["summary"]["n_elements"] == 4`, all four ids present in `[r["id"] for r in cat["records"]]`.

- **T2** `test_catalog_carries_new_fields`: element with specific user_story / source_refs /
  visual.screenshot → record echoes each value verbatim (`record["user_story"]`,
  `record["source_refs"]`, `record["screenshot"]`).

- **T3** `test_catalog_flags_missing_user_story`: element with source_refs + visual but no
  user_story → `"user_story" in record["missing"]` and `record["id"] in
  [r["id"] for r in cat["incomplete"]]`.

- **T4** `test_catalog_flags_missing_source_refs_and_screenshot`: element with only user_story
  → `record["missing"] == ["source_refs", "screenshot"]`.

- **T5** `test_catalog_complete_element_not_flagged`: fully-complete element →
  `record["missing"] == []`, counted in `n_complete`, absent from `incomplete`.

- **T6** `test_catalog_completeness_counts`: 2 of 4 complete → assert exact values for
  `n_with_story`, `n_with_source_refs`, `n_with_screenshot`, `n_complete == 2`,
  `n_incomplete == 2`, `completeness_pct == 50.0`.

- **T7** `test_cmd_catalog_fail_on_incomplete_exit1`: write inventory with incomplete element,
  `foresight.main(["catalog", "--project", str(tmp_path), "--fail-on-incomplete"]) == 1`.

- **T8** `test_cmd_catalog_complete_corpus_exit0`: all elements complete + `--fail-on-incomplete`
  → return value `== 0`.

- **T9** `test_cmd_catalog_no_flag_exit0_despite_incomplete`: incomplete elements, no flag →
  `== 0`.

- **T10** `test_catalog_no_inventory_is_graceful`: no inventory file → `cat["inventory_present"]
  is False`, `cat["summary"]["n_elements"] == 0`, `cat["summary"]["completeness_pct"] == 100`,
  and `foresight.main(["catalog", "--project", str(tmp_path), "--fail-on-incomplete"]) == 0`.

- **T11** `test_catalog_injects_idempotent_section`: run `cmd_catalog` twice with same inventory
  → `inventory.md` content byte-identical on second run; markers appear exactly once in the file.

- **T12** `test_catalog_enriches_without_clobbering`: write `inventory.md` with some prose above
  the block, run `cmd_catalog` → pre-existing prose still present in file after injection.

- **T13** `test_catalog_markdown_has_story_screenshot_sources`: call
  `foresight._catalog_markdown_section(cat)` directly → returned string contains screenshot path,
  user_story text, and a source_refs file:line.

- **T14** `test_catalog_grouped_by_feature`: two features "alpha" and "beta" → section contains
  both feature names as headings.

- **T15** `test_report_includes_catalog_section`: run `catalog` then `report` on a project with
  an enriched inventory → `report.md` text contains `"UI catalog"` and the completeness percent
  string (e.g. `"50.0%"` or the actual value).

---

## Out of scope

- Part B: agent/skill markdown files (`foresight-visual-inspector.md`, cartographer changes,
  SKILL.md, commands/).
- Writing or path-validating `source_refs` — the core only checks presence.
- `_write_json` for catalog output (no catalog.json artifact; only `inventory.md` is written).
- Modifying `build_coverage`, `_match_item`, `suggest_features`, `apply_reorg`, or any other
  existing function.
- `cmd_init` changes (no new subdirectories needed).

---

## Risks and unknowns

1. **Idempotency edge case** — if `_write_text` normalizes trailing newlines differently than
   the regex replacement, the second run may differ by one newline. Tests T11 must catch this.
   Normalize carefully: strip and re-add exactly one `\n` at the end of the file.

2. **`features[].ui_elements` vs `use_cases[].ui_elements`** — existing code in `build_coverage`
   treats `use_cases[].ui_elements` as a list of selector *strings* and `features[].ui_elements`
   as a list of element *objects*. `build_catalog` must iterate only `features[].ui_elements`
   (the objects). Tests T1/T2 must use object-style elements under `features[].ui_elements`.

3. **`completeness_pct` type** — plan says integer percent in the summary but T6 asserts
   `== 50`. Implementation should produce `50.0` (float) or `50` (int). Since `round(100 * 2/4, 1) == 50.0`,
   the test should compare `== 50.0` or use `== 50` (both pass with `50.0 == 50`). Safe either way.

4. **`cmd_report` new dependency** — `build_catalog` is now called inside `cmd_report`. The
   existing smoke test `test_init_and_report_smoke` must still pass, meaning `build_catalog` must
   be graceful when no inventory exists. Confirmed: graceful fallback returns `n_elements=0`.

5. **Marker regex and DOTALL** — the `re.sub` in `_inject_catalog_section` must use `re.DOTALL`
   so the section body (which contains newlines) is matched correctly.

## Revisions from prior iteration

(Not applicable — iteration 1.)
