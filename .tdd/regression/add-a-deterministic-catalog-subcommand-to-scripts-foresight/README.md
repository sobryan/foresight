# Regression: add-a-deterministic-catalog-subcommand-to-scripts-foresight

Original task:

> Add a deterministic catalog subcommand to scripts/foresight.py that reads .tdd/foresight/inventory/inventory.json and renders a UI-element + user-story catalog into inventory/inventory.md between idempotent <!-- foresight:catalog:start/end --> markers, grouped by feature, each element showing selector/role/behavior/user_story/source_refs/screenshot. Implement _element_completeness, build_catalog, _catalog_markdown_section, _inject_catalog_section, cmd_catalog; wire a catalog argparse subparser (--json, --fail-on-incomplete); add a UI catalog section to cmd_report. New inventory fields are optional; existing tests must still pass; core never calls an LLM. All new tests go in tests/test_catalog.py. Run command: pytest -q tests/test_catalog.py.

Replay with:

```
/tdd-regression replay add-a-deterministic-catalog-subcommand-to-scripts-foresight
```

Or, headless (CI):

```
python3 .claude/plugins/iterative-tdd/scripts/tdd_regression.py replay add-a-deterministic-catalog-subcommand-to-scripts-foresight --project .
```
