---
description: Compare the latest exploration screenshots with the visual baseline (or record a new baseline)
argument-hint: [--baseline] [--run <ts>] [--threshold PCT] [--fail-on-change] [--all-projects]
---

Screenshot-level visual regression over the exploration runs on disk (deterministic, pure standard library):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foresight.py" visual --project "$(pwd)" $ARGUMENTS
```

- Without flags it compares the latest `exploration/<ts>/` run against `visual/baseline.json` and writes `visual/visual.json` + `visual.md`. Images are matched by platform and by the screen slug after the step number (`0003-login.png` and `0007-login.png` are the same screen).
- `--baseline` records the latest (or `--run <ts>`) run as the new baseline instead of comparing.
- `--threshold PCT` tolerates small pixel differences (anti-aliasing, cursors); default 0 flags any change.
- `--fail-on-change` exits non-zero when any image changed, resized, or went missing (new screens do not fail).

Present:

1. The counts — changed / resized / missing / new / unchanged.
2. Each changed image with its percent of pixels that differ and both file paths, so the user can open them side by side; resized images with old → new dimensions; missing screens.
3. If a changed screen belongs to a use case the coverage map calls `covered`, say so explicitly: a passing test with a changed screen is exactly the regression the tests don't see.

If there is no baseline yet, say so and offer to record one with `--baseline`. If there are no exploration runs, point the user at `/foresight <target>`.
