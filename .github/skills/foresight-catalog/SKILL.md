---
name: foresight-catalog
description: Render the UI-element + user-story catalog into inventory.md and report documentation completeness
argument-hint: [--fail-on-incomplete] [--all-projects]
---

Render the visual UI catalog from the current inventory (deterministic, no LLM):

```bash
python3 .github/skills/foresight/scripts/foresight.py catalog --project "$(pwd)" the arguments the user typed after the command
```

Writes the catalog block into `<project>/.tdd/foresight/inventory/inventory.md` (between idempotent `<!-- foresight:catalog:start/end -->` markers, so the cartographer's prose above it survives) and `inventory/catalog.json`. An element is documentation-complete when it has a `user_story`, at least one `source_refs` entry, and a `visual.screenshot` whose file actually exists. With `--fail-on-incomplete` the command exits non-zero when any element is incomplete — a CI gate that is meaningful only after the visual pass has run.

Present:

1. Element count, feature count, and the completeness percent.
2. The incomplete elements grouped by what they lack (`user_story`, `source_refs`, `screenshot`, `screenshot_file`), and which agent fills each: the visual inspector for stories and screenshots, the cartographer (`backfill` mode) for source refs.
3. Point the user at `inventory.md` for the rendered catalog with screenshot links.

If there is no `inventory.json` yet, say so and suggest `/foresight <target>` to build one.
