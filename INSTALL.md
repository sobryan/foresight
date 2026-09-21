# Install — foresight

This folder is a Claude Code plugin, a single-plugin marketplace, and the source for a GitHub Copilot port. Install it whichever way fits.

## Claude Code

### Option 1 — Project-local plugin (recommended for one project)

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

### Option 2 — Personal install (available in every project)

Unzip somewhere stable (e.g. `~/.claude/plugins/foresight`), then:

```text
/plugin marketplace add ~/.claude/plugins/foresight
/plugin install foresight@foresight-marketplace
```

### Option 3 — Loose components (no plugin system)

Copy the folders into your project's `.claude/`:

```text
<your-project>/.claude/
  agents/                # copy contents of foresight/agents/ here
  commands/              # copy contents of foresight/commands/ here
  skills/foresight/      # copy foresight/skills/foresight/ here
```

Copy `foresight/scripts/` somewhere accessible (e.g. `.claude/scripts/`) and edit the `${CLAUDE_PLUGIN_ROOT}/scripts/...` references in `SKILL.md` and the command files to point at the new location. Put `skills/foresight/reference/schemas/` next to the script as `scripts/schemas/` so `validate` can find it. (Plugin install does this automatically — only do it by hand if you're skipping the plugin system on purpose.)

### Updating an existing install

The plugin cache is a copy, not a link. After pulling changes into this folder:

```text
/plugin update foresight@foresight-marketplace
```

or from a shell: `claude plugin update foresight@foresight-marketplace`. `claude plugin validate /path/to/foresight` checks the manifest and component layout.

## GitHub Copilot (VS Code and Copilot CLI)

```bash
# one project
python3 /path/to/foresight/scripts/install_copilot.py --target /path/to/your-project

# every project (Copilot CLI + VS Code read ~/.copilot/{skills,agents,hooks})
python3 /path/to/foresight/scripts/install_copilot.py --user
```

The project install writes `.github/skills/foresight*/`, `.github/agents/foresight-*.agent.md`, `.github/hooks/foresight.json`, `.github/instructions/foresight.instructions.md`, and merges the Playwright MCP server into `.mcp.json` and `.vscode/mcp.json` (existing servers untouched). `--dry-run` shows the plan, `--uninstall` removes exactly what was written, re-running is idempotent. Details and product differences: `copilot/README.md`.

Web exploration under Copilot needs Node for `npx @playwright/mcp@latest`.

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

This needs no app, no LLM, and no exploration tooling — it just validates the existing corpus and will immediately tell you which entries aren't actually replayable. Add `--fix-run-command` to repair the ones whose test plan declares the command.

For the full experience, point it at a running app:

```text
/foresight http://localhost:3000 --platforms web
```

The first such run records a visual baseline; later runs report which screens changed.

## Requirements

- Claude Code with the plugin system enabled, or GitHub Copilot (VS Code / CLI).
- Python 3.8+ on PATH (used by `scripts/foresight.py`; standard library only — nothing to `pip install` for the core).
- **Optional, for live exploration:**
  - **Web** — the Claude-in-Chrome browser tools (Claude Code) or the Playwright MCP server (Copilot).
  - **Android** — Maestro *or* Appium (UiAutomator2), plus `adb` and an emulator/device.
  - **iOS** (macOS) — Maestro *or* Appium (XCUITest), plus Xcode's `xcrun simctl`.
  - foresight runs fine without any of these — exploration just enriches the inventory; static analysis, audit, coverage, and reorg work regardless.

## Uninstall

```text
/plugin uninstall foresight
/plugin marketplace remove foresight-marketplace
```

Copilot: `python3 scripts/install_copilot.py --target <project> --uninstall` (or `--user --uninstall`).

Your `.tdd/foresight/` output is yours — leave it or delete it. foresight never deletes regression entries; `reorg --apply` and `audit --fix-run-command` only touch metadata / an empty `run_command` and always leave a `replay.json.bak`.
