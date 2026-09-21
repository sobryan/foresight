# foresight for GitHub Copilot (VS Code and Copilot CLI)

foresight is authored as a Claude Code plugin. This directory holds the Copilot
port: an installer that transforms the plugin's skill, commands, agents, and
hooks into the files Copilot reads, plus the one agent that has to be written
differently because Copilot drives the browser through the Playwright MCP
server instead of Claude in Chrome.

## Install into a project

```bash
python3 scripts/install_copilot.py --target /path/to/your-project
```

That writes, inside the target project:

| Path | What | Read by |
|---|---|---|
| `.github/skills/foresight/` | the main skill (`SKILL.md`), `reference/` docs + schemas, and `scripts/foresight.py` | VS Code, Copilot CLI |
| `.github/skills/foresight-{audit,coverage,catalog,visual,propose,reorg}/` | one thin skill per slash command (`/foresight-audit` …) | VS Code, Copilot CLI |
| `.github/agents/foresight-*.agent.md` | the six sub-agents, tools mapped to Copilot names | VS Code, Copilot CLI, coding agent |
| `.github/hooks/foresight.json` | a `sessionStart` hook that injects one line of corpus health | Copilot CLI (VS Code preview) |
| `.github/instructions/foresight.instructions.md` | the regression contract, applied when editing `.tdd/**` | VS Code, Copilot CLI |
| `.mcp.json` and `.vscode/mcp.json` | the Playwright MCP server entry (merged; existing servers untouched) | Copilot CLI / VS Code agent host; VS Code local agent |

Every file the installer writes is recorded in
`.github/skills/foresight/.foresight-install.json`, so `--uninstall` removes
exactly those and nothing else. Re-running the installer is idempotent.
`--dry-run` prints the plan.

## Install for every project (Copilot CLI)

```bash
python3 scripts/install_copilot.py --user
```

Writes to `~/.copilot/{skills,agents,hooks}/` with absolute script paths and
merges Playwright into `~/.copilot/mcp-config.json`. VS Code reads
`~/.copilot/skills` and `~/.copilot/agents` too.

## Using it

- `/foresight http://localhost:3000` — full run (skills are the slash commands in both products).
- `/foresight-audit`, `/foresight-coverage`, `/foresight-catalog`, `/foresight-visual`, `/foresight-propose 3`, `/foresight-reorg --apply`.
- Copilot CLI: `copilot --agent=foresight-auditor -p "audit the corpus"` or pick one with `/agent`.
- The web explorer needs Node (for `npx @playwright/mcp@latest`); the first run downloads a browser.

## What differs from the Claude Code version

- **Browser driving**: `copilot/agents/foresight-web-explorer.agent.md` uses `browser_navigate` / `browser_snapshot` / `browser_take_screenshot` / `browser_click` … and asks for a fixed viewport via `browser_resize`. On-disk output is identical, so `foresight.py visual` and `catalog` work the same.
- **Tool names**: `Read → read`, `Write`/`Edit → edit`, `Grep`/`Glob → search`, `Bash → execute`. Model pins are dropped (Copilot picks the model).
- **Paths**: `${CLAUDE_PLUGIN_ROOT}/scripts/foresight.py` becomes `.github/skills/foresight/scripts/foresight.py` (relative to the repo root) or an absolute `~/.copilot/...` path for `--user` installs. The copied script finds its schemas next to itself.
- **Slash commands**: Copilot CLI does not support prompt files and VS Code is retiring them, so each command is a skill. Arguments typed after the command arrive as the user message.
- **Hooks**: written in the Copilot CLI dialect (`"version": 1`, camelCase events, `bash`), which VS Code also parses. The hook prints `{"additionalContext": "..."}` only when a `.tdd/regression/` corpus exists.

## Keeping the port in sync

The Claude files are the source of truth. After editing `skills/`, `commands/`,
or `agents/`, re-run the installer against any project (and against this repo
itself — `.github/` here is a dogfood install kept current by
`python3 scripts/install_copilot.py --target .`). `tests/test_install_copilot.py`
checks the transformation.
