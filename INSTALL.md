# Install — foresight

This folder is both a Claude Code plugin and a single-plugin marketplace. Install it whichever way fits.

## Option 1 — Project-local plugin (recommended for one project)

Unzip into your project under `.claude/plugins/`:

```text
<your-project>/
  .claude/
    plugins/
      foresight/            <-- this folder
        .claude-plugin/
        agents/
        skills/
        commands/
        scripts/
        ...
```

Then in Claude Code:

```text
/plugin marketplace add ./.claude/plugins/foresight
/plugin install foresight@foresight-marketplace
```

`/foresight` is now available in this project.

## Option 2 — Personal install (available in every project)

Unzip somewhere stable (e.g. `~/.claude/plugins/foresight`), then:

```text
/plugin marketplace add ~/.claude/plugins/foresight
/plugin install foresight@foresight-marketplace
```

## Option 3 — Loose components (no plugin system)

Copy the folders into your project's `.claude/`:

```text
<your-project>/.claude/
  agents/                # copy contents of foresight/agents/ here
  commands/              # copy contents of foresight/commands/ here
  skills/foresight/      # copy foresight/skills/foresight/ here
```

Copy `foresight/scripts/` somewhere accessible (e.g. `.claude/scripts/`) and edit the `${CLAUDE_PLUGIN_ROOT}/scripts/...` references in `SKILL.md` and the command files to point at the new location. (Plugin install does this automatically — only do it by hand if you're skipping the plugin system on purpose.)

## Verify the install

```text
/foresight-audit
```

If you see an audit summary (even "0 entries") instead of "command not found", the plugin is wired up.

## First run

Against a project that already has `.tdd/regression/` entries (e.g. anything you've used iterative-tdd or hindsight on):

```text
/foresight-audit
```

This needs no app, no LLM, and no exploration tooling — it just validates the existing corpus and will immediately tell you which entries aren't actually replayable.

For the full experience, point it at a running app:

```text
/foresight http://localhost:3000 --platforms web
```

## Requirements

- Claude Code with the plugin system enabled.
- Python 3.8+ on PATH (used by `scripts/foresight.py`; standard library only — nothing to `pip install` for the core).
- **Optional, for live exploration:**
  - **Web** — the Claude-in-Chrome browser tools.
  - **Android** — Maestro *or* Appium (UiAutomator2), plus `adb` and an emulator/device.
  - **iOS** (macOS) — Maestro *or* Appium (XCUITest), plus Xcode's `xcrun simctl`.
  - foresight runs fine without any of these — exploration just enriches the inventory; static analysis, audit, coverage, and reorg work regardless.

## Uninstall

```text
/plugin uninstall foresight
/plugin marketplace remove foresight-marketplace
```

Your `.tdd/foresight/` output is yours — leave it or delete it. foresight never deletes regression entries; `reorg --apply` only adds metadata and always leaves a `replay.json.bak`.
