# Test plan — iteration 1

## How to run the tests

```
cd /Users/rusticus/Developer/foresight && pytest -q tests/test_catalog.py
```

Primary suite (15 new tests):

```
pytest -q tests/test_catalog.py
```

Full-suite guard (existing tests must remain green):

```
pytest -q
```

Run both from the project root (`/Users/rusticus/Developer/foresight`).

---

## Test framework conventions

The project uses **pytest** with no extra plugins. Test files live in `tests/`. The existing
`tests/test_foresight.py` is the canonical pattern: it inserts `scripts/` into `sys.path` at
module load time (`sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))`)
then does a bare `import foresight`. All tests use pytest's `tmp_path` fixture for isolation;
no shared fixtures or conftest.py exist. The new `tests/test_catalog.py` must follow the same
header exactly and must **not** import or depend on anything outside stdlib + pytest + the
`foresight` module. Private helpers such as `foresight._catalog_markdown_section` and
`foresight._inject_catalog_section` are accessed directly — Python does not block this.

The file that `build_catalog` reads:
```
<tmp_path>/.tdd/foresight/inventory/inventory.json
```

The file that `_inject_catalog_section` / `cmd_catalog` writes:
```
<tmp_path>/.tdd/foresight/inventory/inventory.md
```

The file that `cmd_report` writes:
```
<tmp_path>/.tdd/foresight/report.md
```

---

## Shared helpers the Implementer must include at the top of `tests/test_catalog.py`

**`write_catalog_inventory(project, features)`**
Writes `{"generated_at_iso": "2026-01-01T00:00:00Z", "project": str(project), "features": features}`
as JSON to `project / ".tdd" / "foresight" / "inventory" / "inventory.json"`, creating parent
directories. (Mirror of `write_inventory` in `test_foresight.py`.)

**`complete_el(**overrides)`**
Returns a dict representing a fully-documented element:
```python
{
    "id": "el-default",
    "selector": "#submit-btn",
    "role": "button",
    "behavior": "submits the form",
    "user_story": "As a user, I want to submit the form so that I can save my work",
    "source_refs": ["src/form.py:42"],
    "visual": {
        "screenshot": "screens/form.png",
        "region": None,
        "label": "Submit",
        "discovered_by": "visual",
    },
}
```
`overrides` are merged in via `el.update(overrides)` so tests can replace any field with a
falsy value (`user_story=""`, `source_refs=[]`, `visual={}`) to exercise incompleteness.

---

## Tests

### T1 — test_catalog_lists_every_ui_element

- **What it verifies:** `build_catalog` counts and records all `features[].ui_elements` objects
  across all features.
- **Type:** `binary`
- **Pass condition:**
  - `cat["summary"]["n_elements"] == 4`
  - `set(r["id"] for r in cat["records"]) == {"el-a1", "el-a2", "el-b1", "el-b2"}`
- **Test file path:** `tests/test_catalog.py`
- **Test code sketch:**
  ```python
  def test_catalog_lists_every_ui_element(tmp_path):
      write_catalog_inventory(tmp_path, [
          {"id": "feat-a", "name": "Alpha",
           "use_cases": [],
           "ui_elements": [
               {"id": "el-a1", "selector": "#a1", "role": "button", "behavior": "a1"},
               {"id": "el-a2", "selector": "#a2", "role": "link",   "behavior": "a2"},
           ]},
          {"id": "feat-b", "name": "Beta",
           "use_cases": [],
           "ui_elements": [
               {"id": "el-b1", "selector": "#b1", "role": "input",  "behavior": "b1"},
               {"id": "el-b2", "selector": "#b2", "role": "button", "behavior": "b2"},
           ]},
      ])
      cat = foresight.build_catalog(tmp_path)
      assert cat["summary"]["n_elements"] == 4
      assert set(r["id"] for r in cat["records"]) == {"el-a1", "el-a2", "el-b1", "el-b2"}
  ```
- **Why this test:** Covers plan.md §2 (`build_catalog` iterates `features[].ui_elements` and
  populates `summary.n_elements` + `records`). Also guards against accidental inclusion of
  `use_cases[].ui_elements` selector strings (risk §2 in plan.md).

---

### T2 — test_catalog_carries_new_fields

- **What it verifies:** each record in `cat["records"]` echoes `user_story`, `source_refs`, and
  `visual.screenshot` verbatim from the inventory element.
- **Type:** `binary`
- **Pass condition:**
  - `record["user_story"] == "As a user, I want to log in so that I can access my account"`
  - `record["source_refs"] == ["src/auth/login.py:88"]`
  - `record["screenshot"] == "screens/login-form.png"`
- **Test file path:** `tests/test_catalog.py`
- **Test code sketch:**
  ```python
  def test_catalog_carries_new_fields(tmp_path):
      el = {
          "id": "login-btn",
          "selector": "#login",
          "role": "button",
          "behavior": "submits login form",
          "user_story": "As a user, I want to log in so that I can access my account",
          "source_refs": ["src/auth/login.py:88"],
          "visual": {"screenshot": "screens/login-form.png", "region": None,
                     "label": "Log in", "discovered_by": "visual"},
      }
      write_catalog_inventory(tmp_path, [
          {"id": "auth", "name": "Auth", "use_cases": [], "ui_elements": [el]}
      ])
      cat = foresight.build_catalog(tmp_path)
      record = cat["records"][0]
      assert record["user_story"] == "As a user, I want to log in so that I can access my account"
      assert record["source_refs"] == ["src/auth/login.py:88"]
      assert record["screenshot"] == "screens/login-form.png"
  ```
- **Why this test:** Covers plan.md §2, record dict fields: `user_story`, `source_refs`,
  `screenshot = (el.get("visual") or {}).get("screenshot", "")`.

---

### T3 — test_catalog_flags_missing_user_story

- **What it verifies:** an element that has `source_refs` and `visual.screenshot` but no
  `user_story` appears in `cat["incomplete"]` and its record's `missing` list contains
  `"user_story"`.
- **Type:** `binary`
- **Pass condition:**
  - `"user_story" in record["missing"]`
  - `record["id"] in {r["id"] for r in cat["incomplete"]}`
- **Test file path:** `tests/test_catalog.py`
- **Test code sketch:**
  ```python
  def test_catalog_flags_missing_user_story(tmp_path):
      el = complete_el(id="no-story-btn", user_story="")  # falsy user_story
      write_catalog_inventory(tmp_path, [
          {"id": "feat", "name": "Feat", "use_cases": [], "ui_elements": [el]}
      ])
      cat = foresight.build_catalog(tmp_path)
      record = next(r for r in cat["records"] if r["id"] == "no-story-btn")
      assert "user_story" in record["missing"]
      assert "no-story-btn" in {r["id"] for r in cat["incomplete"]}
  ```
- **Why this test:** Covers plan.md §1 (`_element_completeness` appends `"user_story"` when
  `not el.get("user_story")`).

---

### T4 — test_catalog_flags_missing_source_refs_and_screenshot

- **What it verifies:** an element that has only `user_story` (no `source_refs`, no
  `visual.screenshot`) has `record["missing"] == ["source_refs", "screenshot"]` exactly.
- **Type:** `binary`
- **Pass condition:**
  - `record["missing"] == ["source_refs", "screenshot"]`
- **Test file path:** `tests/test_catalog.py`
- **Test code sketch:**
  ```python
  def test_catalog_flags_missing_source_refs_and_screenshot(tmp_path):
      el = complete_el(id="story-only", source_refs=[], visual={})
      write_catalog_inventory(tmp_path, [
          {"id": "feat", "name": "Feat", "use_cases": [], "ui_elements": [el]}
      ])
      cat = foresight.build_catalog(tmp_path)
      record = next(r for r in cat["records"] if r["id"] == "story-only")
      assert record["missing"] == ["source_refs", "screenshot"]
  ```
- **Why this test:** Covers plan.md §1, ordering guarantee — `_element_completeness` appends
  `"source_refs"` before `"screenshot"`. Exact equality (not subset) is required.

---

### T5 — test_catalog_complete_element_not_flagged

- **What it verifies:** a fully-documented element has `missing == []`, is counted in
  `n_complete`, and does not appear in `cat["incomplete"]`.
- **Type:** `binary`
- **Pass condition:**
  - `record["missing"] == []`
  - `cat["summary"]["n_complete"] == 1`
  - `record["id"] not in {r["id"] for r in cat["incomplete"]}`
- **Test file path:** `tests/test_catalog.py`
- **Test code sketch:**
  ```python
  def test_catalog_complete_element_not_flagged(tmp_path):
      el = complete_el(id="full-el")
      write_catalog_inventory(tmp_path, [
          {"id": "feat", "name": "Feat", "use_cases": [], "ui_elements": [el]}
      ])
      cat = foresight.build_catalog(tmp_path)
      record = next(r for r in cat["records"] if r["id"] == "full-el")
      assert record["missing"] == []
      assert cat["summary"]["n_complete"] == 1
      assert "full-el" not in {r["id"] for r in cat["incomplete"]}
  ```
- **Why this test:** Covers plan.md §2 accumulation of `n_complete` and the construction of
  `cat["incomplete"]` as `[r for r in records if r["missing"]]`.

---

### T6 — test_catalog_completeness_counts

- **What it verifies:** with 4 elements (2 complete, 1 with only `user_story`, 1 with nothing),
  all six summary counters have exact expected values and `completeness_pct == 50.0`.
- **Type:** `binary`
- **Pass condition:**
  - `s["n_elements"] == 4`
  - `s["n_with_story"] == 3`
  - `s["n_with_source_refs"] == 2`
  - `s["n_with_screenshot"] == 2`
  - `s["n_complete"] == 2`
  - `s["n_incomplete"] == 2`
  - `s["completeness_pct"] == 50.0`
- **Test file path:** `tests/test_catalog.py`
- **Test code sketch:**
  ```python
  def test_catalog_completeness_counts(tmp_path):
      features = [
          {"id": "f1", "name": "F1", "use_cases": [], "ui_elements": [
              complete_el(id="el-c1"),                   # complete
              complete_el(id="el-c2"),                   # complete
              complete_el(id="el-p1", source_refs=[], visual={}),  # story only
              complete_el(id="el-p2", user_story="", source_refs=[], visual={}),  # nothing
          ]},
      ]
      write_catalog_inventory(tmp_path, features)
      cat = foresight.build_catalog(tmp_path)
      s = cat["summary"]
      assert s["n_elements"] == 4
      assert s["n_with_story"] == 3       # el-c1, el-c2, el-p1 have non-empty user_story
      assert s["n_with_source_refs"] == 2  # el-c1, el-c2
      assert s["n_with_screenshot"] == 2   # el-c1, el-c2
      assert s["n_complete"] == 2
      assert s["n_incomplete"] == 2
      assert s["completeness_pct"] == 50.0
  ```
- **Why this test:** Covers plan.md §2 counter accumulation logic and the `round(100 * n_complete
  / n_elements, 1)` formula. Also covers risk §3 (float vs int — `50.0 == 50` so either passes).

---

### T7 — test_cmd_catalog_fail_on_incomplete_exit1

- **What it verifies:** `foresight.main(["catalog", "--project", ..., "--fail-on-incomplete"])`
  returns `1` when at least one element is incomplete.
- **Type:** `binary`
- **Pass condition:** return value `== 1`
- **Test file path:** `tests/test_catalog.py`
- **Test code sketch:**
  ```python
  def test_cmd_catalog_fail_on_incomplete_exit1(tmp_path):
      el = complete_el(id="incomplete-el", user_story="")
      write_catalog_inventory(tmp_path, [
          {"id": "feat", "name": "Feat", "use_cases": [], "ui_elements": [el]}
      ])
      rc = foresight.main(["catalog", "--project", str(tmp_path), "--fail-on-incomplete"])
      assert rc == 1
  ```
- **Why this test:** Covers plan.md §5 `cmd_catalog` exit-code logic — `return 1` when
  `args.fail_on_incomplete and any_incomplete`.

---

### T8 — test_cmd_catalog_complete_corpus_exit0

- **What it verifies:** `--fail-on-incomplete` returns `0` when all elements are complete.
- **Type:** `binary`
- **Pass condition:** return value `== 0`
- **Test file path:** `tests/test_catalog.py`
- **Test code sketch:**
  ```python
  def test_cmd_catalog_complete_corpus_exit0(tmp_path):
      write_catalog_inventory(tmp_path, [
          {"id": "feat", "name": "Feat", "use_cases": [],
           "ui_elements": [complete_el(id="el-ok")]}
      ])
      rc = foresight.main(["catalog", "--project", str(tmp_path), "--fail-on-incomplete"])
      assert rc == 0
  ```
- **Why this test:** Covers plan.md §5 `cmd_catalog` exit-code logic — `return 0` when
  `incomplete` list is empty even with the flag set.

---

### T9 — test_cmd_catalog_no_flag_exit0_despite_incomplete

- **What it verifies:** without `--fail-on-incomplete`, the command returns `0` even when
  elements are incomplete.
- **Type:** `binary`
- **Pass condition:** return value `== 0`
- **Test file path:** `tests/test_catalog.py`
- **Test code sketch:**
  ```python
  def test_cmd_catalog_no_flag_exit0_despite_incomplete(tmp_path):
      el = complete_el(id="incomplete-el", user_story="")
      write_catalog_inventory(tmp_path, [
          {"id": "feat", "name": "Feat", "use_cases": [], "ui_elements": [el]}
      ])
      rc = foresight.main(["catalog", "--project", str(tmp_path)])  # no --fail-on-incomplete
      assert rc == 0
  ```
- **Why this test:** Covers plan.md §5 — `return 0` when `args.fail_on_incomplete` is False.

---

### T10 — test_catalog_no_inventory_is_graceful

- **What it verifies:** when no `inventory.json` exists, `build_catalog` returns a valid dict
  with sentinel values, and `main(["catalog", ..., "--fail-on-incomplete"])` returns `0`
  (no crash, no false-positive failure).
- **Type:** `binary`
- **Pass condition:**
  - `cat["inventory_present"] is False`
  - `cat["summary"]["n_elements"] == 0`
  - `cat["summary"]["completeness_pct"] == 100`
  - `rc == 0`
- **Test file path:** `tests/test_catalog.py`
- **Test code sketch:**
  ```python
  def test_catalog_no_inventory_is_graceful(tmp_path):
      # no write_catalog_inventory call — file does not exist
      cat = foresight.build_catalog(tmp_path)
      assert cat["inventory_present"] is False
      assert cat["summary"]["n_elements"] == 0
      assert cat["summary"]["completeness_pct"] == 100

      rc = foresight.main(["catalog", "--project", str(tmp_path), "--fail-on-incomplete"])
      assert rc == 0
  ```
- **Why this test:** Covers plan.md §2 graceful fallback and risk §4 (existing
  `test_init_and_report_smoke` continues to pass because `build_catalog` is safe with no inventory).

---

### T11 — test_catalog_injects_idempotent_section

- **What it verifies:** running `cmd_catalog` twice with an unchanged inventory produces a
  byte-identical `inventory.md` on the second run, and the HTML markers appear exactly once
  in the file.
- **Type:** `binary`
- **Pass condition:**
  - `content_after_run2 == content_after_run1` (string equality)
  - `content_after_run2.count("<!-- foresight:catalog:start -->") == 1`
  - `content_after_run2.count("<!-- foresight:catalog:end -->") == 1`
- **Test file path:** `tests/test_catalog.py`
- **Test code sketch:**
  ```python
  def test_catalog_injects_idempotent_section(tmp_path):
      write_catalog_inventory(tmp_path, [
          {"id": "feat", "name": "Feat", "use_cases": [],
           "ui_elements": [complete_el(id="el-1")]}
      ])
      md_path = tmp_path / ".tdd" / "foresight" / "inventory" / "inventory.md"

      foresight.main(["catalog", "--project", str(tmp_path)])
      content1 = md_path.read_text()

      foresight.main(["catalog", "--project", str(tmp_path)])
      content2 = md_path.read_text()

      assert content2 == content1
      assert content2.count("<!-- foresight:catalog:start -->") == 1
      assert content2.count("<!-- foresight:catalog:end -->") == 1
  ```
- **Why this test:** Directly tests the idempotency guarantee from plan.md §4 and covers
  risk §1 (trailing-newline normalization) and risk §5 (DOTALL regex).

---

### T12 — test_catalog_enriches_without_clobbering

- **What it verifies:** pre-existing prose in `inventory.md` (written above the marker block)
  is preserved after `cmd_catalog` injects the catalog section.
- **Type:** `binary`
- **Pass condition:**
  - `"# My Existing Title"` in `content` after catalog run
  - `"Existing prose paragraph."` in `content` after catalog run
- **Test file path:** `tests/test_catalog.py`
- **Test code sketch:**
  ```python
  def test_catalog_enriches_without_clobbering(tmp_path):
      write_catalog_inventory(tmp_path, [
          {"id": "feat", "name": "Feat", "use_cases": [],
           "ui_elements": [complete_el(id="el-1")]}
      ])
      md_path = tmp_path / ".tdd" / "foresight" / "inventory" / "inventory.md"
      md_path.parent.mkdir(parents=True, exist_ok=True)
      md_path.write_text("# My Existing Title\n\nExisting prose paragraph.\n")

      foresight.main(["catalog", "--project", str(tmp_path)])
      content = md_path.read_text()

      assert "# My Existing Title" in content
      assert "Existing prose paragraph." in content
  ```
- **Why this test:** Covers plan.md §4 injection algorithm — the `else` branch appends the
  block to existing content rather than replacing the whole file.

---

### T13 — test_catalog_markdown_has_story_screenshot_sources

- **What it verifies:** `_catalog_markdown_section(cat)` returns a string that contains the
  element's screenshot path, its `user_story` text, and the exact `source_refs` `file:line`
  string.
- **Type:** `binary`
- **Pass condition:**
  - `"screens/search.png"` in `section`
  - `"As a user, I want to search so that I can find things"` in `section`
  - `"src/search.py:101"` in `section`
- **Test file path:** `tests/test_catalog.py`
- **Test code sketch:**
  ```python
  def test_catalog_markdown_has_story_screenshot_sources(tmp_path):
      el = {
          "id": "search-input",
          "selector": "#search",
          "role": "input",
          "behavior": "accepts search query",
          "user_story": "As a user, I want to search so that I can find things",
          "source_refs": ["src/search.py:101"],
          "visual": {"screenshot": "screens/search.png", "region": None,
                     "label": "Search", "discovered_by": "visual"},
      }
      write_catalog_inventory(tmp_path, [
          {"id": "search", "name": "Search", "use_cases": [], "ui_elements": [el]}
      ])
      cat = foresight.build_catalog(tmp_path)
      section = foresight._catalog_markdown_section(cat)

      assert "screens/search.png" in section
      assert "As a user, I want to search so that I can find things" in section
      assert "src/search.py:101" in section
  ```
- **Why this test:** Covers plan.md §3 markdown rendering — screenshot as image link,
  user_story as blockquote, source_refs as inline code.

---

### T14 — test_catalog_grouped_by_feature

- **What it verifies:** `_catalog_markdown_section` produces a section that contains the name
  of every distinct feature as a heading.
- **Type:** `binary`
- **Pass condition:**
  - `"alpha"` in `section` (feature name appearing at heading level)
  - `"beta"` in `section`
- **Test file path:** `tests/test_catalog.py`
- **Test code sketch:**
  ```python
  def test_catalog_grouped_by_feature(tmp_path):
      write_catalog_inventory(tmp_path, [
          {"id": "alpha", "name": "alpha",
           "use_cases": [], "ui_elements": [complete_el(id="el-a")]},
          {"id": "beta",  "name": "beta",
           "use_cases": [], "ui_elements": [complete_el(id="el-b")]},
      ])
      cat = foresight.build_catalog(tmp_path)
      section = foresight._catalog_markdown_section(cat)

      assert "alpha" in section
      assert "beta" in section
  ```
- **Why this test:** Covers plan.md §3 per-feature grouping — `#### <feature name>` headings;
  both feature names must appear.

---

### T15 — test_report_includes_catalog_section

- **What it verifies:** after running `catalog` and then `report` on a project with enriched
  inventory, `report.md` contains the string `"UI catalog"` and the completeness percentage.
- **Type:** `binary`
- **Pass condition:**
  - `"UI catalog"` in `report_text`
  - `"50.0"` in `report_text`  (from `f"- 2 elements, 50.0% complete"`)
- **Test file path:** `tests/test_catalog.py`
- **Test code sketch:**
  ```python
  def test_report_includes_catalog_section(tmp_path):
      # 2 elements: 1 complete, 1 incomplete -> completeness_pct == 50.0
      write_catalog_inventory(tmp_path, [
          {"id": "feat", "name": "Feat", "use_cases": [], "ui_elements": [
              complete_el(id="el-done"),
              complete_el(id="el-missing", user_story=""),
          ]},
      ])
      rc_catalog = foresight.main(["catalog", "--project", str(tmp_path)])
      assert rc_catalog == 0  # catalog must succeed first

      rc_report = foresight.main(["report", "--project", str(tmp_path)])
      assert rc_report == 0

      report_path = tmp_path / ".tdd" / "foresight" / "report.md"
      assert report_path.exists()
      report_text = report_path.read_text()

      assert "UI catalog" in report_text
      assert "50.0" in report_text
  ```
- **Why this test:** Covers plan.md §6 (`cmd_report` modification) — `build_catalog` is called
  inline and the `## UI catalog` section with `completeness_pct` is appended before `_write_text`.

---

## Coverage map

| plan.md §  | Description                                      | Tests            |
|------------|--------------------------------------------------|------------------|
| §1         | `_element_completeness(el) -> list[str]`         | T3, T4, T5, T6   |
| §2         | `build_catalog(project) -> dict`                 | T1, T2, T5, T6, T10 |
| §2 summary | n_with_*, n_complete, n_incomplete, pct          | T6               |
| §2 graceful| no inventory fallback                            | T10              |
| §3         | `_catalog_markdown_section(cat) -> str`          | T13, T14         |
| §4         | `_inject_catalog_section(md_path, section)`      | T11, T12         |
| §5         | `cmd_catalog(args)` + exit codes                 | T7, T8, T9       |
| §5 CLI     | `--fail-on-incomplete` argparse flag             | T7, T8, T9, T10  |
| §6         | `cmd_report` `## UI catalog` section             | T15              |
| §7         | `catalog` subparser in `build_parser()`          | T7, T8, T9, T10  |
| risk §1    | idempotency / trailing-newline normalization     | T11              |
| risk §2    | object vs string ui_elements distinction         | T1               |
| risk §3    | completeness_pct float/int                       | T6               |
| risk §4    | graceful fallback keeps existing tests passing   | T10              |
| risk §5    | re.DOTALL for multi-line regex sub               | T11              |

### Intentionally not tested

| Out-of-scope item                              | Reason                                              |
|------------------------------------------------|-----------------------------------------------------|
| `build_coverage`, `_match_item`, existing fns  | Explicitly out of scope in plan.md                  |
| `source_refs` path validation                  | Core only checks presence; plan.md says do not validate |
| `catalog.json` artifact                        | No JSON artifact is produced; only `inventory.md`   |
| Part B agent/skill markdown files              | Review-only; not unit-testable                      |
| `--json` stdout output format                  | Plan lists it but omits a specific assertion shape; not in T1–T15 |

---

## Revisions from prior iteration

Not applicable — iteration 1.
