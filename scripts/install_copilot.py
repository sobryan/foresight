#!/usr/bin/env python3
"""install_copilot.py — install foresight for GitHub Copilot (VS Code + Copilot CLI).

The Claude Code plugin files are the source of truth. This script transforms
them into what Copilot reads and writes them into a project (or your Copilot
home for a personal install):

  .github/skills/foresight/            SKILL.md + reference/ (+ schemas) + scripts/
  .github/skills/foresight-<cmd>/      one thin skill per slash command
  .github/agents/foresight-*.agent.md  the sub-agents, tools mapped to Copilot names
  .github/hooks/foresight.json         sessionStart hook (Copilot CLI dialect)
  .github/instructions/foresight.instructions.md
  .mcp.json + .vscode/mcp.json         Playwright MCP server, merged in

Usage:
  python3 scripts/install_copilot.py --target <project> [--dry-run] [--no-mcp]
  python3 scripts/install_copilot.py --target <project> --uninstall
  python3 scripts/install_copilot.py --user            # ~/.copilot/{skills,agents,hooks}

Everything written is listed in a manifest so --uninstall removes exactly that.
Pure standard library.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC_ROOT = HERE.parent
COPILOT_DIR = SRC_ROOT / "copilot"

TOOL_MAP = {
    "Read": "read", "NotebookRead": "read",
    "Write": "edit", "Edit": "edit", "MultiEdit": "edit", "NotebookEdit": "edit",
    "Grep": "search", "Glob": "search",
    "Bash": "execute",
    "WebFetch": "web", "WebSearch": "web",
    "Task": "agent",
}
PLAYWRIGHT_CLI = {"type": "local", "command": "npx", "args": ["@playwright/mcp@latest"],
                  "tools": ["*"]}
PLAYWRIGHT_VSCODE = {"command": "npx", "args": ["@playwright/mcp@latest"]}
COMMAND_SKILLS = ("audit", "coverage", "catalog", "visual", "propose", "reorg")
MANIFEST_NAME = ".foresight-install.json"
DESCRIPTION_LIMIT = 1024   # agentskills.io spec


# ---------------------------------------------------------------------------
# frontmatter helpers (the simple "key: value" subset the plugin files use)
# ---------------------------------------------------------------------------


def split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    head, _, body = text[4:].partition("\n---\n")
    fields: dict = {}
    for line in head.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fields[k.strip()] = v.strip()
    return fields, body.lstrip("\n")


def parse_tools(raw: str) -> list[str]:
    raw = raw.strip()
    if raw.startswith("["):
        raw = raw.strip("[]")
    return [t.strip().strip("'\"") for t in raw.split(",") if t.strip()]


def render_frontmatter(fields: dict) -> str:
    lines = ["---"]
    for k, v in fields.items():
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(repr(x) for x in v)}]")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def fit_description(desc: str, limit: int = DESCRIPTION_LIMIT) -> str:
    if len(desc) <= limit:
        return desc
    cut = desc[:limit]
    for sep in (". ", "; ", ", "):
        i = cut.rfind(sep)
        if i > limit // 2:
            return cut[:i + 1].strip()
    return cut.rstrip()


def map_tools(tools: list[str]) -> list[str]:
    out: list[str] = []
    for t in tools:
        if t.startswith("mcp__"):
            continue                      # Claude-runtime MCP tools have no Copilot twin
        mapped = TOOL_MAP.get(t)
        if mapped and mapped not in out:
            out.append(mapped)
    return out


# ---------------------------------------------------------------------------
# layout
# ---------------------------------------------------------------------------


class Layout:
    """Where each artifact goes and how the skill directory is referenced."""

    def __init__(self, target: Path | None, user: bool):
        self.user = user
        if user:
            home = Path.home() / ".copilot"
            self.skills = home / "skills"
            self.agents = home / "agents"
            self.hooks = home / "hooks"
            self.instructions = None
            self.mcp_files = [(home / "mcp-config.json", "mcpServers", PLAYWRIGHT_CLI)]
            self.skill_ref = str(self.skills / "foresight")
            self.roots = [home]
        else:
            assert target is not None
            gh = target / ".github"
            self.skills = gh / "skills"
            self.agents = gh / "agents"
            self.hooks = gh / "hooks"
            self.instructions = gh / "instructions"
            self.mcp_files = [(target / ".mcp.json", "mcpServers", PLAYWRIGHT_CLI),
                              (target / ".vscode" / "mcp.json", "servers", PLAYWRIGHT_VSCODE)]
            self.skill_ref = ".github/skills/foresight"
            self.roots = [gh, target / ".vscode"]
        # Manifest paths are relative to this base so the manifest is portable
        # (a project install is committed and must not carry machine paths).
        self.base = (Path.home() / ".copilot") if user else target
        self.skill_dir = self.skills / "foresight"
        self.script_ref = f"{self.skill_ref}/scripts/foresight.py"
        self.hook_script_ref = f"{self.skill_ref}/scripts/copilot_session_start.py"

    def rel(self, path: Path) -> str:
        return path.relative_to(self.base).as_posix()

    def quoted_script(self) -> str:
        return f'"{self.script_ref}"' if " " in self.script_ref else self.script_ref


def transform_text(text: str, layout: Layout) -> str:
    """Rewrite Claude-specific references into their Copilot equivalents."""
    script = layout.quoted_script()
    text = text.replace('"${CLAUDE_PLUGIN_ROOT}/scripts/foresight.py"', script)
    text = text.replace("${CLAUDE_PLUGIN_ROOT}/scripts/foresight.py", script)
    text = text.replace("${CLAUDE_PLUGIN_ROOT}", layout.skill_ref)
    text = text.replace("`skills/foresight/reference/", f"`{layout.skill_ref}/reference/")
    text = text.replace("skills/foresight/reference/", f"{layout.skill_ref}/reference/")
    if layout.skill_ref != ".github/skills/foresight":
        text = text.replace(".github/skills/foresight", layout.skill_ref)
    text = text.replace("$ARGUMENTS", "the arguments the user typed after the command")
    text = text.replace("Claude-in-Chrome browser tools", "Playwright MCP browser tools")
    text = text.replace("(`mcp__claude-in-chrome__*`)", "(`playwright/*`)")
    text = text.replace("mcp__claude-in-chrome__", "playwright/browser_")
    return text


# ---------------------------------------------------------------------------
# build the file set
# ---------------------------------------------------------------------------


def build_files(layout: Layout) -> dict[Path, bytes]:
    """Absolute path -> content for everything the install writes (except MCP merges)."""
    files: dict[Path, bytes] = {}

    def put(path: Path, content) -> None:
        files[path] = content if isinstance(content, bytes) else content.encode()

    # --- main skill -------------------------------------------------------
    skill_src = (SRC_ROOT / "skills" / "foresight" / "SKILL.md").read_text()
    fm, body = split_frontmatter(skill_src)
    cmd_fm, cmd_body = split_frontmatter((SRC_ROOT / "commands" / "foresight.md").read_text())
    head = {"name": "foresight",
            "description": fit_description(fm.get("description", "")),
            "argument-hint": cmd_fm.get("argument-hint", "")}
    body = transform_text(body, layout)
    body += ("\n\n## When invoked as `/foresight`\n\n"
             + transform_text(cmd_body, layout).replace("Arguments: the arguments the user typed after the command\n\n", ""))
    put(layout.skill_dir / "SKILL.md", render_frontmatter(head) + body)

    # reference docs + schemas
    ref_src = SRC_ROOT / "skills" / "foresight" / "reference"
    for p in sorted(ref_src.rglob("*")):
        if p.is_file():
            rel = p.relative_to(ref_src)
            if p.suffix == ".md":
                put(layout.skill_dir / "reference" / rel, transform_text(p.read_text(), layout))
            else:
                put(layout.skill_dir / "reference" / rel, p.read_bytes())

    # scripts
    put(layout.skill_dir / "scripts" / "foresight.py", (HERE / "foresight.py").read_bytes())
    put(layout.skill_dir / "scripts" / "copilot_session_start.py",
        (HERE / "copilot_session_start.py").read_bytes())

    # --- one thin skill per command ---------------------------------------
    for cmd in COMMAND_SKILLS:
        src = SRC_ROOT / "commands" / f"foresight-{cmd}.md"
        cfm, cbody = split_frontmatter(src.read_text())
        head = {"name": f"foresight-{cmd}",
                "description": fit_description(cfm.get("description", ""))}
        if cfm.get("argument-hint"):
            head["argument-hint"] = cfm["argument-hint"]
        cbody = transform_text(cbody, layout)
        cbody = cbody.replace("Argument: the arguments the user typed after the command",
                              "Argument: the arguments the user typed after the command")
        put(layout.skills / f"foresight-{cmd}" / "SKILL.md", render_frontmatter(head) + cbody)

    # --- agents ------------------------------------------------------------
    for src in sorted((SRC_ROOT / "agents").glob("*.md")):
        name = src.stem
        override = COPILOT_DIR / "agents" / f"{name}.agent.md"
        if override.exists():
            put(layout.agents / f"{name}.agent.md", transform_text(override.read_text(), layout))
            continue
        afm, abody = split_frontmatter(src.read_text())
        head = {"name": name,
                "description": afm.get("description", ""),
                "tools": map_tools(parse_tools(afm.get("tools", "")))}
        put(layout.agents / f"{name}.agent.md",
            render_frontmatter(head) + transform_text(abody, layout))

    # --- hook --------------------------------------------------------------
    hook = {
        "version": 1,
        "hooks": {
            "sessionStart": [{
                "type": "command",
                "bash": f"python3 {layout.hook_script_ref}",
                "cwd": ".",
                "timeoutSec": 20,
            }]
        },
    }
    put(layout.hooks / "foresight.json", json.dumps(hook, indent=2) + "\n")

    # --- instructions (project installs only) ------------------------------
    if layout.instructions is not None:
        src = COPILOT_DIR / "instructions" / "foresight.instructions.md"
        put(layout.instructions / "foresight.instructions.md",
            transform_text(src.read_text(), layout))

    return files


# ---------------------------------------------------------------------------
# MCP merge / unmerge
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def merge_mcp(path: Path, key: str, entry: dict, dry_run: bool) -> bool:
    """Add a `playwright` server under `key` if absent. Returns True if added."""
    data = _load_json(path)
    servers = data.get(key)
    if not isinstance(servers, dict):
        servers = {}
        data[key] = servers
    if "playwright" in servers:
        return False
    servers["playwright"] = entry
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")
    return True


def unmerge_mcp(path: Path, key: str) -> None:
    data = _load_json(path)
    servers = data.get(key)
    if isinstance(servers, dict) and "playwright" in servers:
        del servers["playwright"]
        path.write_text(json.dumps(data, indent=2) + "\n")


# ---------------------------------------------------------------------------
# install / uninstall
# ---------------------------------------------------------------------------


def _relative_for_display(path: Path, layout: Layout, target: Path | None) -> str:
    base = target if (target and not layout.user) else Path.home()
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return str(path)


def install(layout: Layout, target: Path | None, dry_run: bool, with_mcp: bool) -> int:
    files = build_files(layout)
    manifest_path = layout.skill_dir / MANIFEST_NAME
    written: list[str] = []
    for path in sorted(files):
        print(("would write " if dry_run else "write ") + _relative_for_display(path, layout, target))
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(files[path])
        written.append(layout.rel(path))

    mcp_added: list[str] = []
    if with_mcp:
        for path, key, entry in layout.mcp_files:
            if merge_mcp(path, key, entry, dry_run):
                mcp_added.append(layout.rel(path))
                print(("would add " if dry_run else "add ") + "playwright MCP server to "
                      + _relative_for_display(path, layout, target))

    if not dry_run:
        # keep mcp_added stable across re-runs: remember earlier additions
        previous = _load_json(manifest_path).get("mcp_added", [])
        manifest = {"files": sorted(written),
                    "mcp_added": sorted(set(previous) | set(mcp_added))}
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{'would install' if dry_run else 'installed'} {len(written)} file(s) "
          f"into {layout.skills.parent}")
    return 0


def uninstall(layout: Layout) -> int:
    manifest_path = layout.skill_dir / MANIFEST_NAME
    manifest = _load_json(manifest_path)
    if not manifest:
        print(f"nothing to uninstall: no {MANIFEST_NAME} under {layout.skill_dir}")
        return 1
    removed = 0
    owned = [layout.base / f for f in manifest.get("files", [])]
    for p in owned:
        if p.is_file():
            p.unlink()
            removed += 1
    for f in manifest.get("mcp_added", []):
        for path, key, _entry in layout.mcp_files:
            if layout.rel(path) == f:
                unmerge_mcp(path, key)
    if manifest_path.exists():
        manifest_path.unlink()
    # prune directories we emptied, never beyond the roots we own
    for p in sorted(owned, reverse=True):
        d = p.parent
        while d not in layout.roots and d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            d = d.parent
    print(f"removed {removed} file(s)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="install_copilot.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--target", default=None, help="project root to install into")
    p.add_argument("--user", action="store_true",
                   help="install into ~/.copilot/{skills,agents,hooks} instead of a project")
    p.add_argument("--dry-run", action="store_true", help="print what would be written")
    p.add_argument("--no-mcp", action="store_true", help="skip the Playwright MCP merge")
    p.add_argument("--uninstall", action="store_true", help="remove a previous install")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.user and not args.target:
        print("pass --target <project> or --user", file=sys.stderr)
        return 2
    target = Path(args.target).expanduser().resolve() if args.target else None
    layout = Layout(target, args.user)
    if args.uninstall:
        return uninstall(layout)
    return install(layout, target, args.dry_run, with_mcp=not args.no_mcp)


if __name__ == "__main__":
    sys.exit(main())
