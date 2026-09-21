"""Tests for scripts/install_copilot.py — the GitHub Copilot (VS Code + CLI) port.

Run: pytest -q tests/test_install_copilot.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import install_copilot  # noqa: E402


def frontmatter(path: Path) -> dict:
    text = path.read_text()
    assert text.startswith("---\n"), path
    head = text.split("\n---\n", 1)[0][4:]
    fm: dict = {}
    for line in head.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


def tools_of(path: Path) -> list[str]:
    raw = frontmatter(path).get("tools", "")
    return [t.strip().strip("'\"") for t in raw.strip("[]").split(",") if t.strip()]


def install(target: Path, *extra: str) -> int:
    return install_copilot.main(["--target", str(target), *extra])


# ---------------------------------------------------------------------------
# layout
# ---------------------------------------------------------------------------


def test_install_creates_skills_agents_hooks_instructions(tmp_path):
    assert install(tmp_path) == 0
    gh = tmp_path / ".github"
    assert (gh / "skills" / "foresight" / "SKILL.md").exists()
    assert (gh / "skills" / "foresight" / "scripts" / "foresight.py").exists()
    assert (gh / "skills" / "foresight" / "scripts" / "copilot_session_start.py").exists()
    assert (gh / "skills" / "foresight" / "reference" / "schemas" / "inventory.schema.json").exists()
    for cmd in ("audit", "coverage", "catalog", "visual", "propose", "reorg"):
        assert (gh / "skills" / f"foresight-{cmd}" / "SKILL.md").exists(), cmd
    for agent in ("cartographer", "web-explorer", "mobile-explorer", "visual-inspector",
                  "auditor", "architect"):
        assert (gh / "agents" / f"foresight-{agent}.agent.md").exists(), agent
    assert (gh / "hooks" / "foresight.json").exists()
    assert (gh / "instructions" / "foresight.instructions.md").exists()
    assert (tmp_path / ".mcp.json").exists()


def test_no_claude_specific_references_remain(tmp_path):
    install(tmp_path)
    offenders = []
    for p in (tmp_path / ".github").rglob("*.md"):
        text = p.read_text()
        for needle in ("${CLAUDE_PLUGIN_ROOT}", "mcp__claude-in-chrome", "mcp__Claude_in_Chrome",
                       "$ARGUMENTS"):
            if needle in text:
                offenders.append((p.relative_to(tmp_path).as_posix(), needle))
    assert offenders == []


def test_skill_frontmatter_meets_agent_skills_spec(tmp_path):
    install(tmp_path)
    for skill_md in (tmp_path / ".github" / "skills").glob("*/SKILL.md"):
        fm = frontmatter(skill_md)
        assert fm["name"] == skill_md.parent.name
        assert re.fullmatch(r"[a-z0-9-]{1,64}", fm["name"]), fm["name"]
        assert 0 < len(fm["description"]) <= 1024, skill_md


def test_skill_paths_point_at_installed_script(tmp_path):
    install(tmp_path)
    main_skill = (tmp_path / ".github" / "skills" / "foresight" / "SKILL.md").read_text()
    assert "python3 .github/skills/foresight/scripts/foresight.py" in main_skill
    audit_skill = (tmp_path / ".github" / "skills" / "foresight-audit" / "SKILL.md").read_text()
    assert ".github/skills/foresight/scripts/foresight.py audit" in audit_skill


def test_installed_script_finds_its_schemas(tmp_path):
    install(tmp_path)
    project = tmp_path / "proj"
    inv = project / ".tdd" / "foresight" / "inventory" / "inventory.json"
    inv.parent.mkdir(parents=True)
    inv.write_text(json.dumps({"features": "not-a-list"}))
    script = tmp_path / ".github" / "skills" / "foresight" / "scripts" / "foresight.py"
    proc = subprocess.run([sys.executable, str(script), "validate", "--project", str(project)],
                          capture_output=True, text=True)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "$.features" in proc.stdout


# ---------------------------------------------------------------------------
# agents
# ---------------------------------------------------------------------------


def test_agent_tools_are_mapped_to_copilot_names(tmp_path):
    install(tmp_path)
    agents = tmp_path / ".github" / "agents"
    assert tools_of(agents / "foresight-cartographer.agent.md") == ["read", "search", "execute", "edit"]
    assert tools_of(agents / "foresight-auditor.agent.md") == ["read", "search", "execute", "edit"]
    web = tools_of(agents / "foresight-web-explorer.agent.md")
    assert "playwright/*" in web and "execute" in web
    for p in agents.glob("*.agent.md"):
        fm = frontmatter(p)
        assert fm["name"] == p.name[:-len(".agent.md")]
        assert fm["description"]
        assert "model" not in fm


def test_web_explorer_uses_playwright_procedure(tmp_path):
    install(tmp_path)
    text = (tmp_path / ".github" / "agents" / "foresight-web-explorer.agent.md").read_text()
    assert "browser_navigate" in text and "browser_take_screenshot" in text
    assert ".github/skills/foresight/scripts/foresight.py" in text


# ---------------------------------------------------------------------------
# MCP + hooks + instructions
# ---------------------------------------------------------------------------


def test_mcp_merge_preserves_existing_servers(tmp_path):
    (tmp_path / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"mine": {"type": "local", "command": "x", "args": []}}}, indent=2))
    install(tmp_path)
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert set(data["mcpServers"]) == {"mine", "playwright"}
    assert data["mcpServers"]["playwright"]["command"] == "npx"
    assert "@playwright/mcp@latest" in data["mcpServers"]["playwright"]["args"]
    vs = json.loads((tmp_path / ".vscode" / "mcp.json").read_text())
    assert "playwright" in vs["servers"]


def test_mcp_never_overwrites_a_users_playwright_entry(tmp_path):
    (tmp_path / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"playwright": {"type": "local", "command": "custom", "args": ["--mine"]}}}))
    install(tmp_path)
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert data["mcpServers"]["playwright"]["command"] == "custom"


def test_hook_file_is_copilot_cli_dialect(tmp_path):
    install(tmp_path)
    hooks = json.loads((tmp_path / ".github" / "hooks" / "foresight.json").read_text())
    assert hooks["version"] == 1
    [entry] = hooks["hooks"]["sessionStart"]
    assert entry["type"] == "command"
    assert "copilot_session_start.py" in entry["bash"]
    assert entry["timeoutSec"] >= 5


def test_session_start_hook_reports_corpus_health(tmp_path):
    install(tmp_path)
    entry_dir = tmp_path / ".tdd" / "regression" / "broken"
    entry_dir.mkdir(parents=True)
    (entry_dir / "replay.json").write_text(json.dumps(
        {"slug": "broken", "task": "t", "run_command": "", "tests": [{"id": "T1", "name": "n"}]}))
    for f in ("task.md", "plan.md", "test_plan.md"):
        (entry_dir / f).write_text("#")
    script = tmp_path / ".github" / "skills" / "foresight" / "scripts" / "copilot_session_start.py"
    proc = subprocess.run([sys.executable, str(script)], cwd=tmp_path, capture_output=True, text=True)
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert "1 not replayable" in out["additionalContext"]
    assert "foresight-audit" in out["additionalContext"]


def test_session_start_hook_is_silent_without_a_corpus(tmp_path):
    install(tmp_path)
    script = tmp_path / ".github" / "skills" / "foresight" / "scripts" / "copilot_session_start.py"
    proc = subprocess.run([sys.executable, str(script)], cwd=tmp_path, capture_output=True, text=True)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_instructions_file_scopes_to_tdd_dir(tmp_path):
    install(tmp_path)
    fm = frontmatter(tmp_path / ".github" / "instructions" / "foresight.instructions.md")
    assert ".tdd/" in fm["applyTo"]


# ---------------------------------------------------------------------------
# idempotency, dry run, uninstall, user install
# ---------------------------------------------------------------------------


def snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


def test_install_is_idempotent(tmp_path):
    install(tmp_path)
    first = snapshot(tmp_path)
    install(tmp_path)
    assert snapshot(tmp_path) == first


def test_dry_run_writes_nothing(tmp_path, capsys):
    assert install(tmp_path, "--dry-run") == 0
    assert not (tmp_path / ".github").exists()
    assert ".github/skills/foresight/SKILL.md" in capsys.readouterr().out


def test_uninstall_removes_only_owned_files(tmp_path):
    other = tmp_path / ".github" / "agents" / "other.agent.md"
    other.parent.mkdir(parents=True)
    other.write_text("---\nname: other\ndescription: keep me\n---\n")
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"mine": {"command": "x"}}}))
    install(tmp_path)
    assert install(tmp_path, "--uninstall") == 0
    assert other.exists()
    assert not (tmp_path / ".github" / "skills" / "foresight").exists()
    assert not (tmp_path / ".github" / "agents" / "foresight-auditor.agent.md").exists()
    assert not (tmp_path / ".github" / "hooks" / "foresight.json").exists()
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert set(data["mcpServers"]) == {"mine"}   # our playwright entry removed, theirs kept


def test_user_install_targets_copilot_home_with_absolute_paths(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    assert install_copilot.main(["--user"]) == 0
    skill = home / ".copilot" / "skills" / "foresight" / "SKILL.md"
    assert skill.exists()
    assert (home / ".copilot" / "agents" / "foresight-auditor.agent.md").exists()
    assert (home / ".copilot" / "hooks" / "foresight.json").exists()
    assert str(home / ".copilot" / "skills" / "foresight" / "scripts" / "foresight.py") in skill.read_text()
    mcp = json.loads((home / ".copilot" / "mcp-config.json").read_text())
    assert "playwright" in mcp["mcpServers"]


def test_manifest_is_portable_across_machines(tmp_path):
    install(tmp_path)
    manifest = json.loads(
        (tmp_path / ".github" / "skills" / "foresight" / ".foresight-install.json").read_text())
    assert manifest["files"], "manifest lists the owned files"
    for f in manifest["files"] + manifest["mcp_added"]:
        assert not Path(f).is_absolute(), f
        assert str(tmp_path) not in f
    assert ".github/skills/foresight/SKILL.md" in manifest["files"]
    assert ".mcp.json" in manifest["mcp_added"]
