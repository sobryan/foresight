#!/usr/bin/env python3
"""foresight.py — the deterministic, LLM-free core of the foresight plugin.

foresight is the forward-looking sibling of hindsight. hindsight *replays* the
regression entries you already have; foresight *finds the ones you don't have
yet* and *validates and organizes the ones you do*. The exploratory half of
foresight (driving the web/Android/iOS app, taking screenshots, mapping UI
elements to use cases) is performed by the plugin's sub-agents using the agent
runtime's tools. This script is the half that must run anywhere, repeatably,
and in CI — so it is pure Python standard library and never drives the app or
calls an LLM.

It reads and writes the same on-disk contract that iterative-tdd and hindsight
use (`<project>/.tdd/regression/<slug>/replay.json`) and keeps all of its own
output under `<project>/.tdd/foresight/`.

Subcommands
-----------
  init        Create the .tdd/foresight/ output tree.
  audit       Validate every regression entry: replayable? sound? grouped?
              Exits non-zero when error-severity findings exist (CI gate).
  coverage    Build the coverage map + risk-ranked gap report. Richer when an
              inventory.json from the exploration phase is present.
  reorg       Propose priority/feature/serial grouping for hindsight.
              --apply writes it back into each replay.json, backwards-compatibly.
  report      Assemble a top-level human-readable report.md from whatever
              audit/coverage/reorg/exploration artifacts exist.

Project discovery mirrors hindsight: an explicit `--project <path>` (single
project), or `--all-projects` which uses ~/.config/hindsight/projects.yaml when
present, else auto-scans ~/Developer/* for dirs containing .tdd/regression/.
"""
from __future__ import annotations

import argparse
import hashlib
import html as _html
import json
import re
import struct
import sys
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

CONFIG_FILE = Path.home() / ".config" / "hindsight" / "projects.yaml"
AUTO_SCAN_ROOT = Path.home() / "Developer"

PRIORITY_ORDER = ["critical", "high", "normal", "low"]

# Risk vocabulary used both to rank coverage gaps and to suggest priorities.
# Higher weight => more dangerous if it regresses.
RISK_KEYWORDS = {
    "auth": 4, "login": 4, "logout": 2, "password": 4, "credential": 4,
    "token": 3, "session": 3, "permission": 4, "rbac": 4, "role": 3,
    "admin": 3, "security": 5, "encrypt": 4, "secret": 4,
    "payment": 5, "pay": 3, "billing": 4, "charge": 5, "checkout": 4,
    "invoice": 3, "refund": 4, "subscription": 3,
    "delete": 4, "destroy": 4, "remove": 2, "drop": 4, "purge": 4,
    "migration": 3, "schema": 2, "backup": 3, "restore": 3,
    "upload": 2, "download": 2, "export": 2, "import": 2,
    "checkout_": 4, "data": 1, "privacy": 4, "pii": 5, "gdpr": 4,
}

# tokens too common to be distinctive when matching use cases to tests
STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "with",
    "is", "are", "be", "should", "must", "when", "then", "that", "this",
    "it", "as", "by", "at", "from", "into", "test", "tests", "add", "make",
    "page", "view", "returns", "return", "value", "field", "entry", "entries",
    "all", "each", "new", "via", "use", "using", "build", "create", "ensure",
}

# Words that appear in almost every task and use-case description. They are
# fine for risk scoring but say nothing about *which* behaviour a test covers,
# so the coverage matcher drops them on top of STOPWORDS.
MATCH_STOPWORDS = STOPWORDS | {
    "user", "users", "plan", "plans", "app", "apps", "flow", "flows", "screen",
    "screens", "feature", "features", "support", "supports", "supported",
    "allow", "allows", "allowed", "show", "shows", "shown", "display",
    "displays", "list", "lists", "get", "gets", "set", "sets", "update",
    "updates", "updated", "handle", "handles", "check", "checks", "run", "runs",
    "fix", "fixes", "fixed", "bug", "bugs", "existing", "current", "correct",
    "correctly", "properly", "work", "works", "working", "mode", "type", "types",
    "id", "ids", "name", "names", "start", "starts", "open", "opens", "click",
    "clicks", "tap", "taps", "select", "selects", "enter", "enters", "submit",
    "submits", "button", "buttons", "link", "links", "icon", "icons", "text",
    "label", "labels", "item", "items", "data", "info", "details", "also",
    "only", "after", "before", "not", "no", "yes", "can", "cannot", "will",
    "without", "within", "role", "roles", "backend", "frontend", "api", "web",
    "mobile", "ios", "android", "model", "models", "route", "routes",
}

SOURCE_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rb", ".rs", ".java",
    ".kt", ".swift", ".m", ".mm", ".c", ".cc", ".cpp", ".cs", ".php",
    ".vue", ".svelte", ".dart", ".scala", ".ex", ".exs",
}
IGNORE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "dist", "build", "target",
    "__pycache__", ".next", ".nuxt", "vendor", ".tdd", "coverage",
    ".idea", ".vscode", "site-packages",
}

PATH_RE = re.compile(r"[\w./-]+\.[A-Za-z0-9]{1,5}")
SEVERITY_WEIGHT = {"error": 5, "warning": 2, "info": 0}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n")


def _tokens(text: str) -> set[str]:
    """Lower-cased alphanumeric tokens, stopwords and 1-char tokens removed."""
    raw = re.split(r"[^a-z0-9]+", (text or "").lower())
    return {t for t in raw if len(t) > 1 and t not in STOPWORDS}


def _match_tokens(text: str) -> set[str]:
    """Tokens for coverage matching: `_tokens` minus generic product words."""
    return {t for t in _tokens(text) if t not in MATCH_STOPWORDS}


# Markers after which a task description lists what it intentionally does NOT do.
# Counting those words as features/risk produces false positives (e.g. a task
# whose "Non-goals: ... auth ..." line wrongly tags it as a critical auth entry).
_NONGOAL_RE = re.compile(r"(?is)\b(non-?goals?|out[ -]of[ -]scope)\b.*$")


def _effective_text(text: str) -> str:
    """Task text with any trailing non-goals / out-of-scope clause removed."""
    return _NONGOAL_RE.sub("", text or "")


def _regression_root(project: Path) -> Path:
    return project / ".tdd" / "regression"


def _foresight_root(project: Path) -> Path:
    return project / ".tdd" / "foresight"


# ---------------------------------------------------------------------------
# project discovery (compatible with hindsight)
# ---------------------------------------------------------------------------


def _norm(path: Path) -> Path:
    p = Path(path).expanduser()
    return p.resolve() if p.exists() else p


def _read_projects_file() -> list[Path] | None:
    if not CONFIG_FILE.exists():
        return None
    paths: list[Path] = []
    for line in CONFIG_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        paths.append(_norm(Path(line)))
    return paths


def _auto_scan() -> list[Path]:
    if not AUTO_SCAN_ROOT.exists():
        return []
    return sorted(
        p for p in AUTO_SCAN_ROOT.iterdir()
        if p.is_dir() and _regression_root(p).exists()
    )


def discover_projects() -> list[Path]:
    registered = _read_projects_file()
    if registered is not None:
        return [p for p in registered if p.exists()]
    return _auto_scan()


def resolve_projects(args) -> list[Path]:
    """Honor --project (single) or --all-projects (hindsight discovery)."""
    if getattr(args, "all_projects", False):
        return discover_projects()
    project = getattr(args, "project", None) or "."
    return [_norm(Path(project))]


# ---------------------------------------------------------------------------
# regression entry model (compatible with hindsight's Entry)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    slug: str
    project: Path
    entry_dir: Path
    replay_data: dict

    @property
    def task(self) -> str:
        return self.replay_data.get("task", "")

    @property
    def run_command(self) -> str:
        return (self.replay_data.get("run_command") or "").strip()

    @property
    def tests(self) -> list[dict]:
        return self.replay_data.get("tests", []) or []

    @property
    def priority(self) -> str:
        raw = self.replay_data.get("priority", "normal")
        if not isinstance(raw, str):
            return "normal"
        norm = raw.lower().strip()
        return norm if norm in PRIORITY_ORDER else "normal"

    @property
    def features(self) -> list[str]:
        raw = self.replay_data.get("feature")
        if isinstance(raw, str):
            return [raw] if raw else []
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, str)]
        return []

    @property
    def serial(self) -> bool:
        raw = self.replay_data.get("serial", False)
        return raw if isinstance(raw, bool) else False

    @property
    def has_runs(self) -> bool:
        runs = self.entry_dir / "runs"
        return runs.is_dir() and any(runs.iterdir())


def discover_entries(project: Path) -> list[Entry]:
    """Every regression entry dir under a single project (sorted by slug)."""
    root = _regression_root(project)
    entries: list[Entry] = []
    if not root.is_dir():
        return entries
    for entry_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        replay = _read_json(entry_dir / "replay.json")
        entries.append(
            Entry(
                slug=entry_dir.name,
                project=project,
                entry_dir=entry_dir,
                replay_data=replay if isinstance(replay, dict) else {},
            )
        )
    return entries


# ---------------------------------------------------------------------------
# AUDIT  (FR-4 — validate existing regression entries)
# ---------------------------------------------------------------------------


def _referenced_paths(entry: Entry) -> list[str]:
    """Path-like strings mentioned in test_plan.md and the run_command."""
    found: set[str] = set()
    tp = entry.entry_dir / "test_plan.md"
    text = ""
    if tp.exists():
        text += tp.read_text(errors="ignore")
    text += " " + entry.run_command
    for m in PATH_RE.findall(text):
        if "/" in m and not m.startswith(("http", "www.")):
            found.add(m.strip("`.,()<>\"'"))
    return sorted(found)


def audit_entry(entry: Entry) -> dict:
    """Return a verdict dict: {slug, findings:[{code,severity,message}], ...}."""
    findings: list[dict] = []

    def add(code: str, severity: str, message: str) -> None:
        findings.append({"code": code, "severity": severity, "message": message})

    replay_path = entry.entry_dir / "replay.json"
    raw = _read_json(replay_path)

    if not replay_path.exists():
        add("MISSING_REPLAY_JSON", "error", "No replay.json — hindsight cannot see this entry.")
    elif raw is None:
        add("MALFORMED_REPLAY_JSON", "error", "replay.json is not valid JSON.")
    elif not isinstance(raw, dict):
        add("MALFORMED_REPLAY_JSON", "error", "replay.json is not a JSON object.")

    # Replayability — the single most important check.
    if not entry.run_command:
        add(
            "NO_RUN_COMMAND",
            "error",
            "run_command is empty/missing; hindsight & tdd_regression return "
            "'no_run_command' and the entry never actually runs.",
        )

    # Companion files.
    for fname, code in (
        ("task.md", "MISSING_TASK_MD"),
        ("test_plan.md", "MISSING_TEST_PLAN"),
        ("plan.md", "MISSING_PLAN_MD"),
    ):
        if not (entry.entry_dir / fname).exists():
            sev = "warning" if fname != "test_plan.md" else "error"
            add(code, sev, f"{fname} is missing.")

    # Tests array.
    if not entry.tests:
        add("EMPTY_TESTS", "error", "No tests listed in replay.json.")

    # Metadata validity.
    if isinstance(raw, dict):
        p = raw.get("priority", "normal")
        if not (isinstance(p, str) and p.lower().strip() in PRIORITY_ORDER):
            add("INVALID_PRIORITY", "warning",
                f"priority {p!r} is not one of {PRIORITY_ORDER}; treated as 'normal'.")
        f = raw.get("feature")
        if f is not None and not (
            isinstance(f, str) or (isinstance(f, list) and all(isinstance(x, str) for x in f))
        ):
            add("INVALID_FEATURE", "warning",
                "feature must be a string or list of strings; ignored otherwise.")
        s = raw.get("serial", False)
        if not isinstance(s, bool) and "serial" in raw:
            add("INVALID_SERIAL", "warning", "serial must be a boolean; treated as false.")

    # Grouping hygiene (info — feeds reorg).
    if not entry.features:
        add("NO_FEATURE", "info", "No feature tag; will fall into hindsight's 'untagged' bucket.")
    if isinstance(raw, dict) and "priority" not in raw:
        add("DEFAULT_PRIORITY", "info", "No explicit priority; defaults to 'normal'.")

    # Never run.
    if not entry.has_runs:
        add("NEVER_RUN", "info", "No runs/ history; this entry has never been replayed.")

    # Stale path references.
    refs = _referenced_paths(entry)
    if refs:
        missing = [r for r in refs if not (entry.project / r).exists()]
        if len(missing) == len(refs):
            add("STALE_TEST_PATHS", "warning",
                f"None of the {len(refs)} referenced path(s) exist under the project "
                f"(e.g. {refs[0]}); the test plan may be stale.")
        elif missing:
            add("STALE_TEST_PATHS", "info",
                f"{len(missing)} of {len(refs)} referenced path(s) no longer exist: "
                f"{', '.join(missing[:5])}.")

    replayable = not any(
        x["code"] in ("NO_RUN_COMMAND", "MISSING_REPLAY_JSON", "MALFORMED_REPLAY_JSON", "EMPTY_TESTS")
        for x in findings
    )
    return {
        "slug": entry.slug,
        "project": str(entry.project),
        "priority": entry.priority,
        "features": entry.features,
        "replayable": replayable,
        "n_tests": len(entry.tests),
        "findings": findings,
    }


def _health_score(verdicts: list[dict]) -> int:
    """0-100. 100 = no findings. Each finding subtracts its severity weight."""
    if not verdicts:
        return 100
    penalty = sum(
        SEVERITY_WEIGHT.get(f["severity"], 0)
        for v in verdicts for f in v["findings"]
    )
    max_possible = max(len(verdicts) * 10, 1)
    return max(0, round(100 * (1 - min(penalty, max_possible) / max_possible)))


NEAR_DUPLICATE_THRESHOLD = 0.8   # overlap coefficient on task tokens
NEAR_DUPLICATE_MIN_TOKENS = 4    # tiny tasks match everything; ignore them


def _near_duplicate_of(task_tokens: set[str], earlier: list[tuple[str, set[str]]]) -> str | None:
    """The slug of an earlier entry whose task text is nearly the same, if any."""
    if len(task_tokens) < NEAR_DUPLICATE_MIN_TOKENS:
        return None
    for slug, toks in earlier:
        if len(toks) < NEAR_DUPLICATE_MIN_TOKENS:
            continue
        overlap = len(task_tokens & toks) / min(len(task_tokens), len(toks))
        if overlap >= NEAR_DUPLICATE_THRESHOLD:
            return slug
    return None


def build_audit(projects: list[Path]) -> dict:
    verdicts: list[dict] = []
    seen_slugs: dict[str, str] = {}
    seen_tasks: list[tuple[str, set[str]]] = []
    for project in projects:
        for entry in discover_entries(project):
            v = audit_entry(entry)
            if entry.slug in seen_slugs:
                v["findings"].append({
                    "code": "DUPLICATE_SLUG", "severity": "warning",
                    "message": f"slug also defined in {seen_slugs[entry.slug]}; "
                               "hindsight keeps first-wins.",
                })
            else:
                seen_slugs[entry.slug] = str(project)
            toks = _tokens(entry.task)
            twin = _near_duplicate_of(toks, seen_tasks)
            if twin and twin != entry.slug:
                v["findings"].append({
                    "code": "NEAR_DUPLICATE", "severity": "warning",
                    "message": f"task text is nearly identical to `{twin}`; "
                               "consider merging or retiring one of them.",
                })
            seen_tasks.append((entry.slug, toks))
            verdicts.append(v)

    return _summarize_audit(verdicts, projects)


def _summarize_audit(verdicts: list[dict], projects: list[Path]) -> dict:
    n_error = sum(1 for v in verdicts for f in v["findings"] if f["severity"] == "error")
    n_warning = sum(1 for v in verdicts for f in v["findings"] if f["severity"] == "warning")
    n_not_replayable = sum(1 for v in verdicts if not v["replayable"])
    return {
        "generated_at_iso": _now_iso(),
        "projects": [str(p) for p in projects],
        "n_entries": len(verdicts),
        "n_not_replayable": n_not_replayable,
        "n_error_findings": n_error,
        "n_warning_findings": n_warning,
        "health_score": _health_score(verdicts),
        "entries": verdicts,
    }


def _scope_audit(audit: dict, project: Path) -> dict:
    """The audit restricted to one project, with totals recomputed for it."""
    entries = [v for v in audit["entries"] if v["project"] == str(project)]
    scoped = _summarize_audit(entries, [project])
    scoped["generated_at_iso"] = audit["generated_at_iso"]
    return scoped


def extract_run_command(test_plan_path: Path) -> str:
    """The test command declared in a test_plan.md, or "" when none is found.

    Reads the section headed "## How to run the tests" (iterative-tdd's
    convention) and returns the first command found either as an inline
    `code` line or inside the first fenced block, whatever the fence's info
    string. Lines ending in a backslash are joined. Prose is ignored — a
    command has to be marked up as code to count."""
    if not test_plan_path.exists():
        return ""
    in_section = False
    in_fence = False
    pending: list[str] = []
    for line in test_plan_path.read_text(errors="ignore").splitlines():
        stripped = line.strip()
        if not in_section:
            if stripped.lower().startswith("## how to run the tests"):
                in_section = True
            continue
        if stripped.startswith("## ") and not in_fence:
            break
        if stripped.startswith("```"):
            if in_fence and pending:
                return " ".join(pending)
            in_fence = not in_fence
            pending = []
            continue
        if in_fence:
            if not stripped:
                continue
            if stripped.endswith("\\"):
                pending.append(stripped[:-1].strip())
                continue
            pending.append(stripped)
            return " ".join(pending)
        if stripped.startswith("`") and stripped.endswith("`") and len(stripped) > 2:
            return stripped.strip("`").strip()
    return " ".join(pending) if pending else ""


def _repair_run_commands(projects: list[Path]) -> int:
    """Fill an EMPTY run_command from the entry's own test_plan.md.

    Never overwrites a non-empty command; backs up replay.json first.
    Returns the number of entries repaired."""
    fixed = 0
    for project in projects:
        for entry in discover_entries(project):
            if entry.run_command:
                continue
            cmd = extract_run_command(entry.entry_dir / "test_plan.md")
            if not cmd:
                continue
            replay_path = entry.entry_dir / "replay.json"
            data = _read_json(replay_path)
            if not isinstance(data, dict):
                continue
            _backup(replay_path)
            data["run_command"] = cmd
            replay_path.write_text(json.dumps(data, indent=2) + "\n")
            fixed += 1
    return fixed


def _backup(path: Path) -> None:
    backup = path.with_suffix(".json.bak")
    if not backup.exists():
        backup.write_text(path.read_text())


def _audit_markdown(audit: dict) -> str:
    out = ["# foresight — regression corpus audit", "",
           f"Generated: {audit['generated_at_iso']}  ",
           f"Projects: {', '.join(audit['projects'])}", "",
           f"**Health score: {audit['health_score']}/100** · "
           f"{audit['n_entries']} entries · "
           f"{audit['n_not_replayable']} not replayable · "
           f"{audit['n_error_findings']} errors · "
           f"{audit['n_warning_findings']} warnings", ""]
    if audit["n_not_replayable"]:
        out += ["## ⛔ Not replayable (fix these first)", ""]
        for v in audit["entries"]:
            if not v["replayable"]:
                why = "; ".join(f["message"] for f in v["findings"] if f["severity"] == "error")
                out.append(f"- `{v['slug']}` — {why}")
        out.append("")
    out += ["## All entries", ""]
    for v in audit["entries"]:
        badge = "✓ replayable" if v["replayable"] else "✗ NOT replayable"
        feats = ", ".join(v["features"]) or "untagged"
        out.append(f"### `{v['slug']}`  ·  {v['priority']}  ·  {feats}  ·  {badge}")
        if v["findings"]:
            for f in v["findings"]:
                icon = {"error": "⛔", "warning": "⚠️", "info": "ℹ️"}[f["severity"]]
                out.append(f"- {icon} **{f['code']}** — {f['message']}")
        else:
            out.append("- (no findings)")
        out.append("")
    return "\n".join(out)


def cmd_audit(args) -> int:
    projects = resolve_projects(args)
    n_fixed = 0
    if getattr(args, "fix_run_command", False):
        n_fixed = _repair_run_commands(projects)
    audit = build_audit(projects)

    if n_fixed and not args.json:
        print(f"repaired run_command on {n_fixed} entry(ies) from test_plan.md "
              f"(backups: replay.json.bak)")
    if args.json:
        print(json.dumps(audit, indent=2))
    else:
        print(f"foresight audit — {audit['n_entries']} entries across "
              f"{len(projects)} project(s)")
        print(f"  health score: {audit['health_score']}/100")
        print(f"  not replayable: {audit['n_not_replayable']}")
        print(f"  errors: {audit['n_error_findings']}  warnings: {audit['n_warning_findings']}")
        for v in audit["entries"]:
            mark = "✓" if v["replayable"] else "✗"
            print(f"  {mark} {v['slug']}  [{v['priority']}]")
            for f in v["findings"]:
                if f["severity"] != "info" or args.verbose:
                    print(f"        {f['severity']:7s} {f['code']}: {f['message']}")

    # Persist a per-project view (totals recomputed for that project alone).
    for project in projects:
        scoped = _scope_audit(audit, project)
        _write_json(_foresight_root(project) / "audit" / "audit.json", scoped)
        _write_text(_foresight_root(project) / "audit" / "audit.md", _audit_markdown(scoped))

    return 0 if audit["n_error_findings"] == 0 else 1


# ---------------------------------------------------------------------------
# COVERAGE  (FR-3 — map use cases / UI elements to tests, rank gaps)
# ---------------------------------------------------------------------------


def _risk_score(text: str, user_facing: bool = False) -> float:
    toks = _tokens(text)
    score = 1.0 + sum(w for kw, w in RISK_KEYWORDS.items() if kw in toks)
    # substring catch for compound words the tokenizer split apart
    low = (text or "").lower()
    score += sum(0.5 for kw in RISK_KEYWORDS if kw in low and kw not in toks)
    if user_facing:
        score += 2.0
    return round(score, 2)


def _coverage_signals(entries: list[Entry]) -> list[dict]:
    """Per regression entry: the tokens + literal strings it can 'cover'."""
    signals = []
    for e in entries:
        specific = e.slug + " " + " ".join(str(t.get("name", "")) for t in e.tests)
        text = e.task + " " + specific
        signals.append({
            "slug": e.slug,
            "tokens": _match_tokens(text),
            # What the entry actually *checks* (slug + test names), as opposed
            # to what its task text merely mentions.
            "specific_tokens": _match_tokens(specific),
            "text_low": text.lower(),
            "refs": set(_referenced_paths(e)),
        })
    return signals


_SELECTOR_CHARS = set("#.[]=:>/@")


def _is_selector_like(sel: str) -> bool:
    """A string that looks like a CSS selector, route, or locator — not a word.

    Plain words ("button", "Sign in") appear in ordinary prose, so a literal
    hit on them says nothing about coverage."""
    sel = (sel or "").strip()
    if not sel:
        return False
    if any(c in _SELECTOR_CHARS for c in sel):
        return True
    return (" " not in sel) and any(c in sel for c in "-_")


_LINE_SUFFIX_RE = re.compile(r":\d+(-\d+)?$")


def _ref_file(ref: str) -> str:
    return _LINE_SUFFIX_RE.sub("", (ref or "").strip())


def _refs_overlap(source_refs: list[str], entry_refs: set[str]) -> bool:
    for ref in source_refs or []:
        f = _ref_file(ref)
        if not f:
            continue
        if f in entry_refs:
            return True
        if any(r.endswith("/" + f) or f.endswith("/" + r) for r in entry_refs):
            return True
    return False


def _match_detail(name: str, ident: str, selectors: list[str], signals: list[dict],
                  source_refs: list[str] | None = None) -> dict:
    """Classify one inventory item against every regression entry's signal.

    covered   — a selector-like literal hit, a source-file reference hit, or a
                token overlap (three shared distinctive tokens, or two on a
                short <=3-token item) where at least one shared token comes
                from the entry's slug or test names — i.e. something the entry
                *checks*, not just something its task text mentions.
    weak      — a token overlap that doesn't meet that bar (two shared tokens
                on a richer item, or any overlap found only in the task text).
                Reported as a gap: suggestive, not evidence.
    partial   — one shared token.
    uncovered — nothing.
    Generic words (user, plan, page, …) never count. The heuristic prefers a
    false gap over a false "covered". `reason` says which rule fired."""
    distinctive = _match_tokens(name) | _match_tokens(ident)
    matched: list[str] = []
    weak: list[str] = []
    reason = "none"
    best_overlap = 0
    for sig in signals:
        shared = distinctive & sig["tokens"]
        overlap = len(shared)
        literal_hit = next((sel for sel in selectors
                            if _is_selector_like(sel) and sel.lower() in sig["text_low"]), None)
        by_ref = _refs_overlap(source_refs or [], sig["refs"])
        enough = overlap >= 3 or (overlap == 2 and len(distinctive) <= 3)
        checked = bool(shared & sig["specific_tokens"])
        if literal_hit or by_ref or (enough and checked):
            matched.append(sig["slug"])
            if reason == "none" or reason.startswith(("weak", "partial")):
                reason = (f"literal:{literal_hit}" if literal_hit
                          else "source:ref" if by_ref else f"tokens:{overlap}")
        elif overlap >= 2:
            weak.append(sig["slug"])
            if reason == "none" or reason.startswith("partial"):
                reason = f"weak:{overlap}" + ("" if checked else " (task text only)")
        best_overlap = max(best_overlap, overlap)
    if matched:
        return {"status": "covered", "matched": matched, "weak": [], "reason": reason}
    if weak:
        return {"status": "weak", "matched": [], "weak": weak, "reason": reason}
    if best_overlap == 1:
        return {"status": "partial", "matched": [], "weak": [], "reason": "partial:1"}
    return {"status": "uncovered", "matched": [], "weak": [], "reason": "none"}


def _match_item(name: str, ident: str, selectors: list[str], signals: list[dict],
                source_refs: list[str] | None = None):
    """Return (status, matched_slugs) for one inventory item."""
    d = _match_detail(name, ident, selectors, signals, source_refs)
    return d["status"], d["matched"]


def build_coverage(project: Path) -> dict:
    entries = discover_entries(project)
    signals = _coverage_signals(entries)
    inv = _read_json(_foresight_root(project) / "inventory" / "inventory.json")
    items: list[dict] = []

    if isinstance(inv, dict) and inv.get("features"):
        for feature in inv["features"]:
            fname = feature.get("name", feature.get("id", ""))
            for uc in feature.get("use_cases", []):
                selectors = list(uc.get("ui_elements", []))
                d = _match_detail(uc.get("name", ""), uc.get("id", ""), selectors,
                                  signals, uc.get("sources", []))
                items.append({
                    "id": uc.get("id", ""),
                    "kind": "use_case",
                    "feature": feature.get("id", fname),
                    "name": uc.get("name", ""),
                    "platform": uc.get("platform", []),
                    "status": d["status"],
                    "matched_tests": d["matched"],
                    "weak_matches": d["weak"],
                    "match_reason": d["reason"],
                    "risk": _risk_score(
                        f"{fname} {uc.get('name','')} {uc.get('id','')}",
                        uc.get("user_facing", False)),
                    "evidence": uc.get("evidence", []),
                })
            for el in feature.get("ui_elements", []):
                d = _match_detail(el.get("behavior", ""), el.get("id", ""),
                                  [el.get("selector", "")], signals,
                                  el.get("source_refs", []))
                shot = (el.get("visual") or {}).get("screenshot")
                items.append({
                    "id": el.get("id", ""),
                    "kind": "ui_element",
                    "feature": feature.get("id", fname),
                    "name": el.get("behavior", el.get("selector", "")),
                    "status": d["status"],
                    "matched_tests": d["matched"],
                    "weak_matches": d["weak"],
                    "match_reason": d["reason"],
                    "risk": _risk_score(f"{fname} {el.get('behavior','')}"),
                    "evidence": [shot] if shot else [],
                })

    summary = {
        "total": len(items),
        "covered": sum(1 for i in items if i["status"] == "covered"),
        "weak": sum(1 for i in items if i["status"] == "weak"),
        "partial": sum(1 for i in items if i["status"] == "partial"),
        "uncovered": sum(1 for i in items if i["status"] == "uncovered"),
    }
    result = {
        "generated_at_iso": _now_iso(),
        "project": str(project),
        "inventory_present": bool(isinstance(inv, dict) and inv.get("features")),
        "summary": summary,
        "items": sorted(items, key=lambda i: (-i["risk"], i["id"])),
    }
    if not result["inventory_present"]:
        result["static"] = _static_coverage(project, entries)
    return result


def _static_coverage(project: Path, entries: list[Entry]) -> dict:
    """Coarse coverage signal when no inventory.json exists yet.

    Lists top-level source areas that no regression entry references, so the
    user gets something useful before running the (LLM-driven) exploration."""
    referenced: set[str] = set()
    for e in entries:
        for ref in _referenced_paths(e):
            referenced.add(ref.split("/")[0])
    source_areas: dict[str, int] = {}
    for path in project.rglob("*"):
        if not path.is_file() or path.suffix not in SOURCE_EXTS:
            continue
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        rel = path.relative_to(project)
        top = rel.parts[0] if len(rel.parts) > 1 else "(root)"
        source_areas[top] = source_areas.get(top, 0) + 1
    uncovered = sorted(a for a in source_areas if a not in referenced and a != "(root)")
    return {
        "note": "No inventory.json present — run the exploration phase "
                "(/foresight) for per-use-case coverage. This is a coarse "
                "source-area signal only.",
        "n_source_files": sum(source_areas.values()),
        "n_regression_entries": len(entries),
        "source_areas": dict(sorted(source_areas.items())),
        "areas_with_no_regression_reference": uncovered,
    }


def _coverage_markdown(cov: dict) -> str:
    s = cov["summary"]
    out = ["# foresight — coverage & gap report", "",
           f"Generated: {cov['generated_at_iso']}  ",
           f"Project: {cov['project']}", ""]
    if cov["inventory_present"]:
        out += [f"**{s['covered']} covered · {s.get('weak', 0)} weak · {s['partial']} partial · "
                f"{s['uncovered']} uncovered** (of {s['total']} items)", "",
                "## Risk-ranked gaps (uncovered, weak & partial, highest risk first)", "",
                "_weak = two shared words with some entry, which is suggestive but not "
                "evidence; treat as a gap until a human confirms the match._", ""]
        gaps = [i for i in cov["items"] if i["status"] != "covered"]
        if not gaps:
            out.append("No gaps — every inventoried item maps to a regression. 🎉")
        for i in gaps:
            ev = f"  _(evidence: {', '.join(i['evidence'])})_" if i.get("evidence") else ""
            wk = (f"  _(weak match: {', '.join(i['weak_matches'])})_"
                  if i.get("weak_matches") else "")
            out.append(f"- **[{i['status']}]** `{i['id']}` (risk {i['risk']}, "
                       f"feature `{i['feature']}`) — {i['name']}{wk}{ev}")
        out += ["", "## Covered", ""]
        for i in cov["items"]:
            if i["status"] == "covered":
                out.append(f"- `{i['id']}` ← {', '.join(i['matched_tests'])} "
                           f"_({i.get('match_reason', '')})_")
    else:
        st = cov["static"]
        out += ["> " + st["note"], "",
                f"Source files: {st['n_source_files']} · "
                f"Regression entries: {st['n_regression_entries']}", "",
                "## Source areas with no regression reference", ""]
        if st["areas_with_no_regression_reference"]:
            for a in st["areas_with_no_regression_reference"]:
                out.append(f"- `{a}/` ({st['source_areas'].get(a, 0)} files)")
        else:
            out.append("Every source area is referenced by at least one regression entry.")
    return "\n".join(out)


def cmd_coverage(args) -> int:
    projects = resolve_projects(args)
    worst = 0
    for project in projects:
        cov = build_coverage(project)
        _write_json(_foresight_root(project) / "coverage" / "coverage.json", cov)
        _write_text(_foresight_root(project) / "coverage" / "gaps.md", _coverage_markdown(cov))
        if args.json:
            print(json.dumps(cov, indent=2))
        else:
            s = cov["summary"]
            if cov["inventory_present"]:
                print(f"{project.name}: {s['uncovered']} uncovered / {s['weak']} weak / "
                      f"{s['partial']} partial / {s['covered']} covered "
                      f"(of {s['total']})")
            else:
                n = len(cov["static"]["areas_with_no_regression_reference"])
                print(f"{project.name}: no inventory yet — {n} source area(s) "
                      f"with no regression reference (static signal)")
        high_risk_gaps = sum(
            1 for i in cov["items"]
            if i["status"] in ("uncovered", "weak") and i["risk"] >= 5)
        worst = max(worst, high_risk_gaps)
    if args.fail_on_gap and worst:
        return 1
    return 0


# ---------------------------------------------------------------------------
# CATALOG  (render UI-element + user-story catalog into inventory.md)
# ---------------------------------------------------------------------------

_CATALOG_START = "<!-- foresight:catalog:start -->"
_CATALOG_END   = "<!-- foresight:catalog:end -->"


def _screenshot_path(shot: str, root: Path) -> Path | None:
    """Resolve a screenshot reference: absolute, under .tdd/foresight/, or under
    the project root. Returns the first that exists, else None."""
    if not shot:
        return None
    candidates = [Path(shot)] if Path(shot).is_absolute() else [
        root / shot, root.parent.parent / shot]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _element_completeness(el: dict, root: Path | None = None) -> list:
    """Missing documentation aspects: user_story, source_refs, screenshot.

    With `root` (the .tdd/foresight/ dir) a screenshot reference must also
    resolve to a real file — otherwise it is reported as `screenshot_file`."""
    missing: list = []
    if not el.get("user_story"):
        missing.append("user_story")
    if not el.get("source_refs"):
        missing.append("source_refs")
    shot = (el.get("visual") or {}).get("screenshot")
    if not shot:
        missing.append("screenshot")
    elif root is not None and _screenshot_path(shot, root) is None:
        missing.append("screenshot_file")
    return missing


def build_catalog(project: Path) -> dict:
    """Build the UI element catalog from inventory.json. Never writes files."""
    root = _foresight_root(project)
    inv = _read_json(root / "inventory" / "inventory.json")

    _empty_summary = {
        "n_features": 0, "n_elements": 0, "n_use_cases": 0,
        "n_with_story": 0, "n_with_source_refs": 0, "n_with_screenshot": 0,
        "n_complete": 0, "n_incomplete": 0, "completeness_pct": 100,
    }

    if not (isinstance(inv, dict) and inv.get("features")):
        return {
            "generated_at_iso": _now_iso(),
            "project": str(project),
            "inventory_present": False,
            "summary": _empty_summary,
            "records": [],
            "incomplete": [],
        }

    records: list = []
    n_features = 0
    n_use_cases = 0
    n_with_story = 0
    n_with_source_refs = 0
    n_with_screenshot = 0
    n_complete = 0
    n_incomplete = 0

    for feature in inv["features"]:
        n_features += 1
        n_use_cases += len(feature.get("use_cases", []))
        for el in feature.get("ui_elements", []):
            missing = _element_completeness(el, root)
            shot = (el.get("visual") or {}).get("screenshot", "")
            shot_exists = bool(shot) and _screenshot_path(shot, root) is not None
            record = {
                "id": el.get("id", ""),
                "feature": feature.get("id", feature.get("name", "")),
                "selector": el.get("selector", ""),
                "role": el.get("role", ""),
                "behavior": el.get("behavior", ""),
                "use_case": el.get("use_case", ""),
                "user_story": el.get("user_story", ""),
                "source_refs": el.get("source_refs") or [],
                "screenshot": shot,
                "screenshot_exists": shot_exists,
                "region": (el.get("visual") or {}).get("region"),
                "label": (el.get("visual") or {}).get("label", ""),
                "missing": missing,
            }
            records.append(record)
            if el.get("user_story"):
                n_with_story += 1
            if el.get("source_refs"):
                n_with_source_refs += 1
            if shot_exists:
                n_with_screenshot += 1
            if missing:
                n_incomplete += 1
            else:
                n_complete += 1

    n_elements = len(records)
    completeness_pct = round(100 * n_complete / n_elements, 1) if n_elements else 100

    return {
        "generated_at_iso": _now_iso(),
        "project": str(project),
        "inventory_present": True,
        "summary": {
            "n_features": n_features,
            "n_elements": n_elements,
            "n_use_cases": n_use_cases,
            "n_with_story": n_with_story,
            "n_with_source_refs": n_with_source_refs,
            "n_with_screenshot": n_with_screenshot,
            "n_complete": n_complete,
            "n_incomplete": n_incomplete,
            "completeness_pct": completeness_pct,
        },
        "records": records,
        "incomplete": [r for r in records if r["missing"]],
    }


def _catalog_markdown_section(cat: dict) -> str:
    """Render the UI element catalog as a markdown section string (no timestamp — deterministic)."""
    s = cat["summary"]
    n_el = s["n_elements"]
    n_feat = s["n_features"]
    n_comp = s["n_complete"]
    pct = s["completeness_pct"]

    out = ["## UI element catalog", ""]
    out.append(f"{n_el} elements across {n_feat} features — {n_comp} complete ({pct}%)")
    out += ["", "### Documentation completeness", ""]

    incomplete = cat.get("incomplete", [])
    if incomplete:
        out.append("The following elements are missing documentation:")
        out.append("")
        for r in incomplete:
            out.append(f"- `{r['id']}` (`{r['feature']}`): missing {', '.join(r['missing'])}")
    else:
        out.append(f"All {n_el} elements are fully documented.")

    out += ["", "### Elements by feature", ""]

    records = cat.get("records", [])
    seen_features: list = []
    feature_records: dict = {}
    for r in records:
        feat = r["feature"]
        if feat not in feature_records:
            seen_features.append(feat)
            feature_records[feat] = []
        feature_records[feat].append(r)

    for feat in seen_features:
        out += [f"#### {feat}", ""]
        for r in feature_records[feat]:
            out.append(f"##### {r['id']} — {r['role']} {r['selector']}")
            out.append("")
            story = r.get("user_story") or ""
            out.append(f"> {story}" if story else "> *(no user story)*")
            out.append("")
            shot = r.get("screenshot") or ""
            label = r.get("label") or ""
            if shot and r.get("screenshot_exists", True):
                out.append(f"![{label}]({shot})")
            elif shot:
                out.append(f"*(screenshot file not found: `{shot}`)*")
            else:
                out.append("*(no screenshot)*")
            out.append("")
            refs = r.get("source_refs") or []
            if refs:
                out.append("Sources: " + ", ".join(f"`{ref}`" for ref in refs))
            else:
                out.append("*(no source refs)*")
            out.append("")
            out.append("---")
            out.append("")

    return "\n".join(out)


def _inject_catalog_section(md_path: Path, section: str) -> None:
    """Inject section between idempotent HTML comment markers (create file if absent)."""
    block = _CATALOG_START + "\n" + section.rstrip("\n") + "\n" + _CATALOG_END

    if md_path.exists():
        existing = md_path.read_text()
        if _CATALOG_START in existing:
            # A callable replacement: the block is content, not a regex template,
            # so backslashes in selectors / Windows paths must not be interpreted.
            new_content = re.sub(
                r"<!-- foresight:catalog:start -->.*?<!-- foresight:catalog:end -->",
                lambda _m: block,
                existing,
                flags=re.DOTALL,
            )
        else:
            new_content = existing.rstrip("\n") + "\n\n" + block
    else:
        new_content = block

    _write_text(md_path, new_content)


def cmd_catalog(args) -> int:
    projects = resolve_projects(args)
    any_incomplete = False
    for project in projects:
        cat = build_catalog(project)
        md_path = _foresight_root(project) / "inventory" / "inventory.md"
        section = _catalog_markdown_section(cat)
        _inject_catalog_section(md_path, section)
        if cat["inventory_present"]:
            _write_json(_foresight_root(project) / "inventory" / "catalog.json", cat)
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
    if args.fail_on_incomplete and any_incomplete:
        return 1
    return 0


# ---------------------------------------------------------------------------
# VISUAL  (screenshot baseline + comparison across exploration runs)
# ---------------------------------------------------------------------------
#
# Exploration runs land under exploration/<ts>/<platform>/NNNN-<slug>.png.
# `visual --baseline` records the latest (or --run) run: one sha256 + size per
# image. `visual` compares a run against that baseline and, when the bytes
# differ, decodes both PNGs (pure stdlib) to report the percentage of pixels
# that changed. This turns the screenshots the explorers capture into a
# regression signal instead of just evidence.

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_STEP_PREFIX_RE = re.compile(r"^\d+[-_]")
_PNG_CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}   # color type -> samples per pixel
VISUAL_MAX_PIXELS = 4_000_000               # beyond this, compare by hash only


def _png_chunks(data: bytes):
    pos = len(_PNG_SIG)
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        yield kind, body
        pos += 12 + length
        if kind == b"IEND":
            break


def _unfilter(raw: bytes, width: int, height: int, bpp: int) -> bytes | None:
    """Undo PNG scanline filtering (types 0-4). Returns None on a bad filter."""
    stride = width * bpp
    out = bytearray(stride * height)
    prev = bytes(stride)
    pos = 0
    for y in range(height):
        if pos + 1 + stride > len(raw):
            return None
        ftype = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        if ftype == 0:
            pass
        elif ftype == 1:      # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:      # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:      # Average
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:      # Paeth
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        else:
            return None
        out[y * stride:(y + 1) * stride] = line
        prev = bytes(line)
    return bytes(out)


def _png_info(path: Path):
    """(width, height, color_type, bit_depth, interlace) from IHDR, or None."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(33)
    except OSError:
        return None
    if len(head) < 33 or not head.startswith(_PNG_SIG) or head[12:16] != b"IHDR":
        return None
    w, h, depth, ctype, _c, _f, interlace = struct.unpack(">IIBBBBB", head[16:29])
    return w, h, ctype, depth, interlace


def _png_decode(path: Path):
    """Decode an 8-bit, non-interlaced PNG -> (width, height, channels, pixels).

    Returns None for anything it can't decode; callers fall back to a hash
    comparison. Pure standard library on purpose (see NFR-7)."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if not data.startswith(_PNG_SIG):
        return None
    width = height = channels = None
    idat = bytearray()
    try:
        for kind, body in _png_chunks(data):
            if kind == b"IHDR":
                w, h, depth, ctype, _c, _f, interlace = struct.unpack(">IIBBBBB", body)
                if depth != 8 or interlace != 0 or ctype not in _PNG_CHANNELS:
                    return None
                width, height, channels = w, h, _PNG_CHANNELS[ctype]
            elif kind == b"IDAT":
                idat += body
        if width is None or not idat:
            return None
        raw = zlib.decompress(bytes(idat))
    except (struct.error, zlib.error, ValueError):
        return None
    pixels = _unfilter(raw, width, height, channels)
    if pixels is None:
        return None
    return width, height, channels, pixels


def _pixel_diff_pct(a, b) -> float | None:
    """Percent of pixels that differ between two decoded images of equal shape."""
    wa, ha, ca, pa = a
    wb, hb, cb, pb = b
    if (wa, ha, ca) != (wb, hb, cb) or not pa:
        return None
    if pa == pb:
        return 0.0
    n = wa * ha
    diff = sum(1 for i in range(0, len(pa), ca) if pa[i:i + ca] != pb[i:i + ca])
    if diff == 0:
        return 0.0
    # Never round a real change down to 0.0 — one pixel is still a change.
    return max(round(100.0 * diff / n, 4), 0.0001)


def _exploration_runs(root: Path) -> list[Path]:
    expl = root / "exploration"
    if not expl.is_dir():
        return []
    return sorted(p for p in expl.iterdir() if p.is_dir())


def _pick_run(root: Path, run: str | None) -> Path | None:
    runs = _exploration_runs(root)
    if run:
        wanted = root / "exploration" / run
        return wanted if wanted.is_dir() else None
    return runs[-1] if runs else None


def _run_images(run_dir: Path) -> dict[str, Path]:
    """key ("<platform>/<name-without-step-prefix>") -> screenshot path."""
    images: dict[str, Path] = {}
    for platform_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        for f in sorted(platform_dir.glob("*.png")):
            key = f"{platform_dir.name}/{_STEP_PREFIX_RE.sub('', f.name)}"
            if key in images:                       # two steps, same slug: keep both
                key = f"{platform_dir.name}/{f.name}"
            images[key] = f
    return images


def _image_record(path: Path, root: Path) -> dict:
    info = _png_info(path)
    return {
        "file": str(path.relative_to(root)),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "width": info[0] if info else None,
        "height": info[1] if info else None,
    }


def build_visual_baseline(project: Path, run: str | None = None) -> dict | None:
    root = _foresight_root(project)
    run_dir = _pick_run(root, run)
    if run_dir is None:
        return None
    return {
        "recorded_at_iso": _now_iso(),
        "project": str(project),
        "run": run_dir.name,
        "images": {k: _image_record(p, root) for k, p in _run_images(run_dir).items()},
    }


def _compare_image(key: str, base: dict | None, cur: Path | None, root: Path,
                   threshold: float) -> dict:
    rec = {"key": key, "status": None, "diff_pct": None,
           "baseline_file": base["file"] if base else None,
           "file": str(cur.relative_to(root)) if cur else None,
           "baseline_width": base["width"] if base else None,
           "baseline_height": base["height"] if base else None,
           "width": None, "height": None}
    if base is None:
        rec["status"] = "new"
    elif cur is None:
        rec["status"] = "missing"
    if rec["status"]:
        if cur:
            info = _png_info(cur)
            rec["width"], rec["height"] = (info[0], info[1]) if info else (None, None)
        return rec

    current = _image_record(cur, root)
    rec["width"], rec["height"] = current["width"], current["height"]
    if (current["width"], current["height"]) != (base["width"], base["height"]):
        rec["status"] = "resized"
        return rec
    if current["sha256"] == base["sha256"]:
        rec["status"] = "unchanged"
        rec["diff_pct"] = 0.0
        return rec
    # Bytes differ: measure how much of the picture actually changed.
    too_big = (current["width"] or 0) * (current["height"] or 0) > VISUAL_MAX_PIXELS
    if not too_big:
        a = _png_decode(root / base["file"])
        b = _png_decode(cur)
        if a is not None and b is not None:
            rec["diff_pct"] = _pixel_diff_pct(a, b)
    if rec["diff_pct"] is None:
        rec["status"] = "changed"          # undecodable / oversized: trust the hash
    else:
        rec["status"] = "changed" if rec["diff_pct"] > threshold else "unchanged"
    return rec


def build_visual(project: Path, run: str | None = None, threshold: float = 0.0) -> dict:
    root = _foresight_root(project)
    baseline = _read_json(root / "visual" / "baseline.json")
    run_dir = _pick_run(root, run)
    result = {
        "generated_at_iso": _now_iso(),
        "project": str(project),
        "baseline_present": isinstance(baseline, dict) and "images" in baseline,
        "baseline_run": baseline.get("run") if isinstance(baseline, dict) else None,
        "run": run_dir.name if run_dir else None,
        "threshold_pct": threshold,
        "summary": {"total": 0, "unchanged": 0, "changed": 0, "resized": 0,
                    "new": 0, "missing": 0},
        "images": [],
    }
    if not result["baseline_present"]:
        result["note"] = ("no baseline yet — run `foresight.py visual --baseline` "
                          "after an exploration run to record one")
        return result
    if run_dir is None:
        result["note"] = "no exploration run to compare — run /foresight with a target"
        return result
    current = _run_images(run_dir)
    keys = sorted(set(baseline["images"]) | set(current))
    images = [_compare_image(k, baseline["images"].get(k), current.get(k), root, threshold)
              for k in keys]
    order = {"changed": 0, "resized": 1, "missing": 2, "new": 3, "unchanged": 4}
    images.sort(key=lambda i: (order[i["status"]], -(i["diff_pct"] or 0), i["key"]))
    result["images"] = images
    for i in images:
        result["summary"][i["status"]] += 1
    result["summary"]["total"] = len(images)
    return result


def _visual_markdown(vis: dict) -> str:
    out = ["# foresight — visual comparison", "",
           f"Generated: {vis['generated_at_iso']}  ",
           f"Project: {vis['project']}", ""]
    if vis.get("note"):
        out.append("> " + vis["note"])
        return "\n".join(out)
    s = vis["summary"]
    out += [f"Baseline run `{vis['baseline_run']}` → current run `{vis['run']}` "
            f"(threshold {vis['threshold_pct']}%)", "",
            f"**{s['changed']} changed · {s['resized']} resized · {s['missing']} missing · "
            f"{s['new']} new · {s['unchanged']} unchanged** (of {s['total']})", ""]
    flagged = [i for i in vis["images"] if i["status"] != "unchanged"]
    out += ["## Needs a look", ""]
    if not flagged:
        out.append("Nothing changed against the baseline.")
    for i in flagged:
        detail = ""
        if i["status"] == "changed":
            detail = (f" — {i['diff_pct']}% of pixels differ" if i["diff_pct"] is not None
                      else " — bytes differ (not decodable; compared by hash)")
        elif i["status"] == "resized":
            detail = (f" — {i['baseline_width']}×{i['baseline_height']} → "
                      f"{i['width']}×{i['height']}")
        files = " / ".join(f"`{f}`" for f in (i["baseline_file"], i["file"]) if f)
        out.append(f"- **[{i['status']}]** `{i['key']}`{detail}  {files}")
    out += ["", "## Unchanged", ""]
    for i in vis["images"]:
        if i["status"] == "unchanged":
            pct = f" ({i['diff_pct']}% within threshold)" if i["diff_pct"] else ""
            out.append(f"- `{i['key']}`{pct}")
    return "\n".join(out)


def cmd_visual(args) -> int:
    projects = resolve_projects(args)
    rc = 0
    for project in projects:
        root = _foresight_root(project)
        if args.baseline:
            base = build_visual_baseline(project, args.run)
            if base is None:
                print(f"{project.name}: no exploration run to baseline")
                continue
            _write_json(root / "visual" / "baseline.json", base)
            if args.json:
                print(json.dumps(base, indent=2))
            else:
                print(f"{project.name}: baseline recorded from run {base['run']} "
                      f"({len(base['images'])} image(s))")
            continue
        vis = build_visual(project, args.run, args.threshold)
        _write_json(root / "visual" / "visual.json", vis)
        _write_text(root / "visual" / "visual.md", _visual_markdown(vis))
        if args.json:
            print(json.dumps(vis, indent=2))
        elif vis.get("note"):
            print(f"{project.name}: {vis['note']}")
        else:
            s = vis["summary"]
            print(f"{project.name}: {s['changed']} changed / {s['resized']} resized / "
                  f"{s['missing']} missing / {s['new']} new / {s['unchanged']} unchanged "
                  f"(baseline {vis['baseline_run']} vs {vis['run']})")
        s = vis["summary"]
        if args.fail_on_change and (s["changed"] or s["resized"] or s["missing"]):
            rc = 1
    return rc


# ---------------------------------------------------------------------------
# REORG  (FR-5 — propose & optionally apply priority/feature/serial)
# ---------------------------------------------------------------------------

SERIAL_HINTS = {"database", "db", "migration", "filesystem", "global", "shared",
                "singleton", "port", "socket", "server", "stateful"}

# Generic product areas used to name a feature bucket when no inventory maps
# the entry. Deliberately free of test-corpus vocabulary ("priority",
# "feature", "parallel") — those describe metadata, not product areas.
FEATURE_FALLBACK_KEYWORDS = (
    "auth", "payment", "billing", "checkout", "security", "admin", "upload",
    "search", "dashboard", "api", "visualization", "settings", "onboarding",
    "notifications", "reports", "export", "import", "profile",
)


def suggest_priority(entry: Entry) -> str:
    risk = _risk_score(_effective_text(entry.task) + " " + entry.slug)
    if risk >= 6:
        return "critical"
    if risk >= 4:
        return "high"
    if risk >= 2:
        return "normal"
    return "low"


def suggest_features(entry: Entry, inv: dict | None) -> list[str]:
    toks = _tokens(_effective_text(entry.task) + " " + entry.slug)
    # If we have an inventory, map to its feature ids by token overlap.
    if isinstance(inv, dict) and inv.get("features"):
        matched = []
        for feature in inv["features"]:
            ftoks = _tokens(feature.get("name", "") + " " + feature.get("id", ""))
            if ftoks & toks:
                matched.append(feature.get("id", feature.get("name", "")))
        if matched:
            return sorted(set(matched))
    # Otherwise derive a feature from the first generic product-area word.
    for kw in FEATURE_FALLBACK_KEYWORDS:
        if kw in toks:
            return [kw]
    return []


def suggest_serial(entry: Entry) -> bool:
    return bool(_tokens(_effective_text(entry.task)) & SERIAL_HINTS)


def _protected_keys(entry: Entry) -> list[str]:
    """Metadata keys a human (or a previous apply) set explicitly in replay.json.

    The keyword heuristic never overwrites these; only an overrides.json entry
    or --force does."""
    raw = entry.replay_data
    protected = []
    if "priority" in raw:
        protected.append("priority")
    if raw.get("feature"):
        protected.append("feature")
    if "serial" in raw:
        protected.append("serial")
    return protected


def _read_overrides(project: Path) -> dict:
    """reorg/overrides.json — the architect's (or the user's) per-slug corrections.

    Shape: {"<slug>": {"priority": …, "feature": …, "serial": …, "reason": …}}.
    Invalid values are dropped so a sloppy override can't corrupt a plan."""
    raw = _read_json(_foresight_root(project) / "reorg" / "overrides.json")
    if not isinstance(raw, dict):
        return {}
    clean: dict[str, dict] = {}
    for slug, ov in raw.items():
        if not isinstance(ov, dict):
            continue
        accepted: dict = {}
        p = ov.get("priority")
        if isinstance(p, str) and p.lower().strip() in PRIORITY_ORDER:
            accepted["priority"] = p.lower().strip()
        f = ov.get("feature")
        if isinstance(f, str) and f:
            accepted["feature"] = [f]
        elif isinstance(f, list) and f and all(isinstance(x, str) for x in f):
            accepted["feature"] = f
        s = ov.get("serial")
        if isinstance(s, bool):
            accepted["serial"] = s
        if accepted:
            if isinstance(ov.get("reason"), str):
                accepted["reason"] = ov["reason"]
            clean[slug] = accepted
    return clean


def build_reorg(projects: list[Path]) -> dict:
    proposals: list[dict] = []
    for project in projects:
        inv = _read_json(_foresight_root(project) / "inventory" / "inventory.json")
        overrides = _read_overrides(project)
        for entry in discover_entries(project):
            sp = suggest_priority(entry)
            sf = suggest_features(entry, inv if isinstance(inv, dict) else None)
            ss = suggest_serial(entry)
            ov = overrides.get(entry.slug, {})
            sp = ov.get("priority", sp)
            # No heuristic idea => keep whatever the entry already has.
            sf = ov.get("feature", sf or entry.features)
            ss = ov.get("serial", ss)
            change = {
                "priority": None if sp == entry.priority else sp,
                # An empty suggestion is "no idea", never "remove the tag".
                "feature": None if (not sf or sorted(sf) == sorted(entry.features)) else sf,
                "serial": None if ss == entry.serial else ss,
            }
            proposal = {
                "slug": entry.slug,
                "project": str(project),
                "current": {"priority": entry.priority,
                            "feature": entry.features, "serial": entry.serial},
                "suggested": {"priority": sp, "feature": sf, "serial": ss},
                "changes": {k: v for k, v in change.items() if v is not None},
                "protected": _protected_keys(entry),
            }
            if ov:
                proposal["override"] = ov
            proposals.append(proposal)
    return _summarize_reorg(proposals, projects)


def _summarize_reorg(proposals: list[dict], projects: list[Path]) -> dict:
    feature_buckets: dict[str, int] = {}
    n_normal = 0
    for p in proposals:
        for f in (p["suggested"]["feature"] or ["untagged"]):
            feature_buckets[f] = feature_buckets.get(f, 0) + 1
        if p["current"]["priority"] == "normal":
            n_normal += 1
    n = max(len(proposals), 1)
    biggest = max(feature_buckets.values()) if feature_buckets else 0
    reorg_needed = (n_normal / n > 0.6) or (biggest / n > 0.5)
    reasons = []
    if n_normal / n > 0.6:
        reasons.append(f"{n_normal}/{n} entries are still default 'normal' priority")
    if biggest / n > 0.5:
        reasons.append("one feature bucket holds more than half the corpus")
    return {
        "generated_at_iso": _now_iso(),
        "projects": [str(p) for p in projects],
        "n_entries": len(proposals),
        "n_with_changes": sum(1 for p in proposals if p["changes"]),
        "feature_buckets": dict(sorted(feature_buckets.items())),
        "reorg_needed": reorg_needed,
        "reorg_reasons": reasons,
        "proposals": proposals,
    }


def _scope_reorg(reorg: dict, project: Path) -> dict:
    """The reorg plan restricted to one project, with totals recomputed."""
    proposals = [p for p in reorg["proposals"] if p["project"] == str(project)]
    scoped = _summarize_reorg(proposals, [project])
    scoped["generated_at_iso"] = reorg["generated_at_iso"]
    return scoped


def _reorg_markdown(reorg: dict) -> str:
    out = ["# foresight — reorg plan (priority / feature / serial for hindsight)", "",
           f"Generated: {reorg['generated_at_iso']}  ",
           f"Projects: {', '.join(reorg['projects'])}", "",
           f"**Reorg needed: {'YES' if reorg['reorg_needed'] else 'no'}**"]
    if reorg["reorg_reasons"]:
        out.append("  \n_" + "; ".join(reorg["reorg_reasons"]) + "_")
    out += ["", f"{reorg['n_with_changes']} of {reorg['n_entries']} entries would change.",
            "", "## Feature buckets (proposed)", ""]
    for f, c in reorg["feature_buckets"].items():
        out.append(f"- `{f}`: {c}")
    out += ["", "## Per-entry proposals", ""]
    for p in reorg["proposals"]:
        if not p["changes"]:
            out.append(f"- `{p['slug']}` — no change "
                       f"({p['current']['priority']}, {p['current']['feature'] or 'untagged'})")
            continue
        bits = []
        override_keys = set(p.get("override", {})) - {"reason"}
        for k, v in p["changes"].items():
            tag = ""
            if k in override_keys:
                tag = " _(override)_"
            elif k in p.get("protected", []):
                tag = " _(protected — set explicitly; needs --force)_"
            bits.append(f"{k}: {p['current'][k]!r} → {v!r}{tag}")
        out.append(f"- `{p['slug']}` — " + "; ".join(bits))
    out += ["", "_Apply with_ `foresight.py reorg --apply` _(writes priority/feature/"
            "serial back into each replay.json, backs up to replay.json.bak, idempotent; "
            "explicit values are kept unless overridden in reorg/overrides.json or --force)._"]
    return "\n".join(out)


def _applicable_changes(proposal: dict, force: bool) -> dict:
    """The subset of a proposal's changes that --apply is allowed to write."""
    if force:
        return dict(proposal["changes"])
    override_keys = set(proposal.get("override", {})) - {"reason"}
    protected = set(proposal.get("protected", [])) - override_keys
    return {k: v for k, v in proposal["changes"].items() if k not in protected}


def apply_reorg(reorg: dict, force: bool = False) -> int:
    """Write suggested priority/feature/serial back into replay.json files.

    Backwards-compatible: only adds/updates those three keys, preserves every
    other key and its order, backs the original up to replay.json.bak, and is
    idempotent (re-running makes no further change). Keys a human set
    explicitly are left alone unless an overrides.json entry names them or
    `force` is set."""
    changed = 0
    for p in reorg["proposals"]:
        changes = _applicable_changes(p, force)
        if not changes:
            continue
        replay_path = Path(p["project"]) / ".tdd" / "regression" / p["slug"] / "replay.json"
        data = _read_json(replay_path)
        if not isinstance(data, dict):
            continue
        _backup(replay_path)
        for key in ("priority", "feature", "serial"):
            if key in changes:
                data[key] = changes[key]
        replay_path.write_text(json.dumps(data, indent=2) + "\n")
        changed += 1
    return changed


def cmd_reorg(args) -> int:
    projects = resolve_projects(args)
    reorg = build_reorg(projects)
    for project in projects:
        scoped = _scope_reorg(reorg, project)
        _write_json(_foresight_root(project) / "reorg" / "reorg.json", scoped)
        _write_text(_foresight_root(project) / "reorg" / "reorg.md", _reorg_markdown(scoped))

    if args.json:
        print(json.dumps(reorg, indent=2))
    else:
        print(f"foresight reorg — {reorg['n_with_changes']}/{reorg['n_entries']} "
              f"entries would change")
        print(f"  reorg needed: {'YES' if reorg['reorg_needed'] else 'no'}"
              + (f" ({'; '.join(reorg['reorg_reasons'])})" if reorg["reorg_reasons"] else ""))
        for p in reorg["proposals"]:
            if p["changes"]:
                bits = "; ".join(f"{k}→{v}" for k, v in p["changes"].items())
                print(f"  ~ {p['slug']}: {bits}")

    if args.apply:
        n = apply_reorg(reorg, force=getattr(args, "force", False))
        skipped = sum(
            1 for p in reorg["proposals"]
            if p["changes"] and not _applicable_changes(p, getattr(args, "force", False)))
        print(f"applied changes to {n} replay.json file(s) (backups: *.json.bak)")
        if skipped:
            print(f"  left {skipped} entry(ies) untouched — explicit metadata; "
                  f"add to reorg/overrides.json or pass --force")
    return 0


# ---------------------------------------------------------------------------
# INIT + REPORT
# ---------------------------------------------------------------------------


def cmd_init(args) -> int:
    for project in resolve_projects(args):
        root = _foresight_root(project)
        for sub in ("inventory", "exploration", "coverage", "audit", "reorg",
                    "proposals", "visual"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        print(f"initialized {root}")
    return 0


def cmd_report(args) -> int:
    for project in resolve_projects(args):
        root = _foresight_root(project)
        audit = _read_json(root / "audit" / "audit.json")
        cov = _read_json(root / "coverage" / "coverage.json")
        reorg = _read_json(root / "reorg" / "reorg.json")
        _prop_dir = root / "proposals"
        _expl_dir = root / "exploration"
        proposals = sorted(
            p.name for p in (_prop_dir.glob("*") if _prop_dir.exists() else [])
            if p.is_dir()
        )
        explorations = sorted(
            p.name for p in (_expl_dir.glob("*") if _expl_dir.exists() else [])
            if p.is_dir()
        )

        out = [f"# foresight report — {project.name}", "",
               f"Generated: {_now_iso()}", ""]
        out += ["## Exploration", ""]
        out.append(f"- runs on disk: {', '.join(explorations) or '(none yet — run /foresight)'}")
        out += ["", "## Corpus audit", ""]
        if audit:
            out.append(f"- health score: **{audit['health_score']}/100**")
            out.append(f"- not replayable: **{audit['n_not_replayable']}** / {audit['n_entries']}")
            out.append(f"- errors: {audit['n_error_findings']} · warnings: {audit['n_warning_findings']}")
        else:
            out.append("- not run yet — `/foresight-audit`")
        out += ["", "## Coverage", ""]
        if cov and cov.get("inventory_present"):
            s = cov["summary"]
            out.append(f"- {s['uncovered']} uncovered · {s['partial']} partial · "
                       f"{s['covered']} covered (of {s['total']})")
            top = [i for i in cov["items"] if i["status"] == "uncovered"][:5]
            for i in top:
                out.append(f"  - gap `{i['id']}` (risk {i['risk']})")
        elif cov:
            n = len(cov.get("static", {}).get("areas_with_no_regression_reference", []))
            out.append(f"- static signal only: {n} source area(s) with no regression reference")
        else:
            out.append("- not run yet — `/foresight-coverage`")
        out += ["", "## Reorg (for hindsight)", ""]
        if reorg:
            out.append(f"- reorg needed: **{'YES' if reorg['reorg_needed'] else 'no'}**")
            out.append(f"- entries that would change: {reorg['n_with_changes']} / {reorg['n_entries']}")
        else:
            out.append("- not run yet — `/foresight-reorg`")
        out += ["", "## Proposed new regressions", ""]
        if proposals:
            for slug in proposals:
                out.append(f"- `{slug}` → promote with `/tdd` then it becomes a hindsight entry")
        else:
            out.append("- none yet — `/foresight-propose`")

        out += ["", "## Visual", ""]
        vis = _read_json(root / "visual" / "visual.json")
        if vis and vis.get("baseline_present") and vis.get("run"):
            s_v = vis["summary"]
            out.append(f"- baseline `{vis['baseline_run']}` vs `{vis['run']}`: "
                       f"**{s_v['changed']} changed** · {s_v['resized']} resized · "
                       f"{s_v['missing']} missing · {s_v['new']} new · "
                       f"{s_v['unchanged']} unchanged")
            for i in [x for x in vis["images"] if x["status"] == "changed"][:5]:
                pct = f" ({i['diff_pct']}% of pixels)" if i["diff_pct"] is not None else ""
                out.append(f"  - `{i['key']}`{pct}")
        else:
            out.append("- not compared yet — after an exploration run: "
                       "`foresight.py visual --baseline`, then `foresight.py visual`")

        out += ["", "## UI catalog", ""]
        cat = build_catalog(project)
        s_cat = cat["summary"]
        if s_cat["n_elements"] == 0:
            out.append("- not enriched yet — run the visual pass + `foresight.py catalog`")
        else:
            out.append(f"- {s_cat['n_elements']} elements, {s_cat['completeness_pct']}% complete")
            top_incomplete = cat["incomplete"][:5]
            for r in top_incomplete:
                out.append(f"  - `{r['id']}` missing: {', '.join(r['missing'])}")

        extras = _other_artifacts(root)
        if extras:
            out += ["", "## Other artifacts", "",
                    "_Files the agents wrote outside the documented output set — "
                    "worth a look, nothing downstream consumes them automatically._", ""]
            out += [f"- `{rel}`" for rel in extras]

        _write_text(root / "report.md", "\n".join(out))
        print(f"wrote {root / 'report.md'}")
        if getattr(args, "html", False):
            _write_text(root / "report.html", _markdown_to_html("\n".join(out)))
            print(f"wrote {root / 'report.html'}")
    return 0


KNOWN_ARTIFACTS = {
    "inventory/inventory.json", "inventory/inventory.md", "inventory/catalog.json",
    "coverage/coverage.json", "coverage/gaps.md",
    "audit/audit.json", "audit/audit.md",
    "reorg/reorg.json", "reorg/reorg.md", "reorg/overrides.json",
    "visual/visual.json", "visual/visual.md", "visual/baseline.json",
    "report.md", "report.html",
}
_FREEFORM_DIRS = ("exploration", "proposals")


def _other_artifacts(root: Path) -> list[str]:
    """Files under .tdd/foresight/ that the documented output set doesn't name."""
    if not root.is_dir():
        return []
    found = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel in KNOWN_ARTIFACTS or rel.split("/")[0] in _FREEFORM_DIRS:
            continue
        found.append(rel)
    return sorted(found)


_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\w)_([^_]+)_(?!\w)")


def _inline_html(text: str) -> str:
    text = _html.escape(text, quote=True)
    text = _INLINE_CODE_RE.sub(r"<code>\1</code>", text)
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _ITALIC_RE.sub(r"<em>\1</em>", text)
    return text


def _markdown_to_html(md: str) -> str:
    """The small Markdown subset report.md uses -> a self-contained HTML page.

    No scripts, no external resources: the file can be attached to a PR or
    opened from a CI artifact without anything else."""
    body: list[str] = []
    list_depth = 0

    def close_lists(to_depth: int = 0) -> None:
        nonlocal list_depth
        while list_depth > to_depth:
            body.append("</ul>")
            list_depth -= 1

    title = "foresight report"
    for line in md.splitlines():
        stripped = line.strip()
        indent = (len(line) - len(line.lstrip(" "))) // 2
        if stripped.startswith("- "):
            depth = indent + 1
            while list_depth < depth:
                body.append("<ul>")
                list_depth += 1
            close_lists(depth)
            body.append(f"<li>{_inline_html(stripped[2:])}</li>")
            continue
        close_lists(0)
        if not stripped:
            continue
        if stripped.startswith("# "):
            title = stripped[2:]
            body.append(f"<h1>{_inline_html(stripped[2:])}</h1>")
        elif stripped.startswith("## "):
            body.append(f"<h2>{_inline_html(stripped[3:])}</h2>")
        elif stripped.startswith("### "):
            body.append(f"<h3>{_inline_html(stripped[4:])}</h3>")
        elif stripped.startswith("> "):
            body.append(f"<blockquote>{_inline_html(stripped[2:])}</blockquote>")
        else:
            body.append(f"<p>{_inline_html(stripped)}</p>")
    close_lists(0)
    style = (
        "body{font:15px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;"
        "max-width:860px;margin:2rem auto;padding:0 1rem;color:#1c1c1c;background:#fff}"
        "h1{font-size:1.6rem}h2{font-size:1.2rem;margin-top:2rem;border-bottom:1px solid #ddd}"
        "code{background:#f3f3f3;padding:.1em .3em;border-radius:3px;font-size:.92em}"
        "ul{padding-left:1.4rem}blockquote{color:#555;border-left:3px solid #ccc;"
        "margin:0;padding-left:.8rem}"
        "@media(prefers-color-scheme:dark){body{color:#e6e6e6;background:#141414}"
        "code{background:#262626}h2{border-color:#333}blockquote{color:#aaa;border-color:#444}}"
    )
    return ("<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{_html.escape(title)}</title><style>{style}</style></head>\n"
            "<body>\n" + "\n".join(body) + "\n</body></html>\n")


# ---------------------------------------------------------------------------
# VALIDATE  (acceptance criterion 6 — artifacts match their documented schemas)
# ---------------------------------------------------------------------------

_JSON_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "null": type(None),
}


def _is_type(value, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _JSON_TYPES.get(name, object))


def validate_json(data, schema: dict, path: str = "$") -> list[str]:
    """A small JSON-Schema subset validator (stdlib only): type, enum, required,
    properties, additionalProperties (as a schema), items. Returns a list of
    human-readable problems; empty means valid."""
    errors: list[str] = []
    types = schema.get("type")
    if types is not None:
        allowed = types if isinstance(types, list) else [types]
        if not any(_is_type(data, t) for t in allowed):
            errors.append(f"{path}: expected {' or '.join(allowed)}, "
                          f"got {type(data).__name__}")
            return errors
    if "enum" in schema and data not in schema["enum"]:
        errors.append(f"{path}: {data!r} is not one of {schema['enum']}")
    if isinstance(data, dict):
        for key in schema.get("required", []):
            if key not in data:
                errors.append(f"{path}: required key '{key}' is missing")
        props = schema.get("properties", {})
        for key, value in data.items():
            if key in props:
                errors += validate_json(value, props[key], f"{path}.{key}")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors += validate_json(value, schema["additionalProperties"], f"{path}.{key}")
    if isinstance(data, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(data):
            errors += validate_json(item, schema["items"], f"{path}[{i}]")
    return errors


def _schema_dir() -> Path | None:
    here = Path(__file__).resolve().parent
    for candidate in (here.parent / "skills" / "foresight" / "reference" / "schemas",  # plugin layout
                      here.parent / "reference" / "schemas",                          # Copilot skill layout
                      here / "schemas"):
        if candidate.is_dir():
            return candidate
    return None


ARTIFACT_SCHEMAS = [
    ("inventory/inventory.json", "inventory"),
    ("inventory/catalog.json", "catalog"),
    ("coverage/coverage.json", "coverage"),
    ("audit/audit.json", "audit"),
    ("reorg/reorg.json", "reorg"),
    ("reorg/overrides.json", "overrides"),
    ("visual/visual.json", "visual"),
    ("visual/baseline.json", "visual-baseline"),
]


def validate_project(project: Path, schema_dir: Path) -> list[tuple[str, list[str]]]:
    """[(relative artifact path, [errors])] for every artifact present."""
    root = _foresight_root(project)
    targets: list[tuple[Path, str]] = [(root / rel, name) for rel, name in ARTIFACT_SCHEMAS]
    for run in _exploration_runs(root):
        for platform_dir in run.iterdir():
            ui_map = platform_dir / "ui_map.json"
            if ui_map.is_file():
                targets.append((ui_map, "ui-map"))
    results = []
    for path, schema_name in targets:
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        schema = _read_json(schema_dir / f"{schema_name}.schema.json")
        if not isinstance(schema, dict):
            results.append((rel, [f"schema {schema_name}.schema.json is missing or malformed"]))
            continue
        data = _read_json(path)
        if data is None:
            results.append((rel, ["not valid JSON"]))
            continue
        results.append((rel, validate_json(data, schema)))
    return results


def cmd_validate(args) -> int:
    schema_dir = Path(args.schemas) if getattr(args, "schemas", None) else _schema_dir()
    if schema_dir is None or not schema_dir.is_dir():
        print("foresight validate: schema directory not found; pass --schemas <dir> "
              "(shipped at skills/foresight/reference/schemas/)")
        return 2
    n_files = n_errors = 0
    for project in resolve_projects(args):
        for rel, errors in validate_project(project, schema_dir):
            n_files += 1
            n_errors += len(errors)
            if errors:
                print(f"✗ {project.name}: {rel}")
                for e in errors[:20]:
                    print(f"    {e}")
                if len(errors) > 20:
                    print(f"    … {len(errors) - 20} more")
            elif getattr(args, "verbose", False):
                print(f"✓ {project.name}: {rel}")
    print(f"validated {n_files} artifact(s), {n_errors} error(s)")
    return 1 if n_errors else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _add_project_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--project", default=".", help="project root (default: cwd)")
    p.add_argument("--all-projects", action="store_true",
                   help="use hindsight discovery (registry, else ~/Developer/*)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="foresight.py", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="create the .tdd/foresight/ tree")
    _add_project_args(p_init)
    p_init.set_defaults(func=cmd_init)

    p_audit = sub.add_parser("audit", help="validate existing regression entries")
    _add_project_args(p_audit)
    p_audit.add_argument("--json", action="store_true", help="emit JSON to stdout")
    p_audit.add_argument("--verbose", action="store_true", help="include info findings")
    p_audit.add_argument("--fix-run-command", action="store_true",
                         help="fill an EMPTY run_command from the entry's test_plan.md "
                              "('## How to run the tests'); backs up replay.json")
    p_audit.set_defaults(func=cmd_audit)

    p_cov = sub.add_parser("coverage", help="build coverage map + gap report")
    _add_project_args(p_cov)
    p_cov.add_argument("--json", action="store_true", help="emit JSON to stdout")
    p_cov.add_argument("--fail-on-gap", action="store_true",
                       help="exit non-zero if any high-risk (>=5) gap is uncovered")
    p_cov.set_defaults(func=cmd_coverage)

    p_cat = sub.add_parser("catalog",
                            help="render the UI-element + user-story catalog into inventory.md")
    _add_project_args(p_cat)
    p_cat.add_argument("--json", action="store_true", help="emit JSON to stdout")
    p_cat.add_argument("--fail-on-incomplete", action="store_true",
                       help="exit non-zero if any UI element lacks a story / source_refs / screenshot")
    p_cat.set_defaults(func=cmd_catalog)

    p_vis = sub.add_parser("visual",
                           help="record a screenshot baseline / compare a run against it")
    _add_project_args(p_vis)
    p_vis.add_argument("--baseline", action="store_true",
                       help="record the run as the baseline instead of comparing")
    p_vis.add_argument("--run", default=None,
                       help="exploration run timestamp (default: the latest)")
    p_vis.add_argument("--threshold", type=float, default=0.0,
                       help="percent of pixels allowed to differ before an image "
                            "counts as changed (default 0)")
    p_vis.add_argument("--fail-on-change", action="store_true",
                       help="exit non-zero if any image changed, resized, or went missing")
    p_vis.add_argument("--json", action="store_true", help="emit JSON to stdout")
    p_vis.set_defaults(func=cmd_visual)

    p_reorg = sub.add_parser("reorg", help="propose/apply priority+feature grouping")
    _add_project_args(p_reorg)
    p_reorg.add_argument("--json", action="store_true", help="emit JSON to stdout")
    p_reorg.add_argument("--apply", action="store_true",
                         help="write suggestions into replay.json (backs up to *.bak)")
    p_reorg.add_argument("--force", action="store_true",
                         help="with --apply: also overwrite priority/feature/serial that "
                              "were set explicitly (default keeps them)")
    p_reorg.set_defaults(func=cmd_reorg)

    p_report = sub.add_parser("report", help="assemble report.md from artifacts")
    _add_project_args(p_report)
    p_report.add_argument("--html", action="store_true",
                          help="also write a self-contained report.html")
    p_report.set_defaults(func=cmd_report)

    p_val = sub.add_parser("validate",
                           help="check every artifact under .tdd/foresight/ against its schema")
    _add_project_args(p_val)
    p_val.add_argument("--schemas", default=None,
                       help="schema directory (default: the plugin's reference/schemas)")
    p_val.add_argument("--verbose", action="store_true", help="list valid files too")
    p_val.set_defaults(func=cmd_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
