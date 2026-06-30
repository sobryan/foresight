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
import json
import re
import sys
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
        existing = [r for r in refs if (entry.project / r).exists()]
        if not existing:
            add("STALE_TEST_PATHS", "warning",
                f"None of the {len(refs)} referenced path(s) exist under the project "
                f"(e.g. {refs[0]}); the test plan may be stale.")

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


def build_audit(projects: list[Path]) -> dict:
    verdicts: list[dict] = []
    seen_slugs: dict[str, str] = {}
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
            verdicts.append(v)

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
    audit = build_audit(projects)

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

    # Persist into the first project's foresight dir (or each, when scoped to one).
    for project in projects:
        scoped = dict(audit)
        scoped["entries"] = [v for v in audit["entries"] if v["project"] == str(project)]
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
        text = e.task + " " + e.slug
        for t in e.tests:
            text += " " + str(t.get("name", ""))
        signals.append({
            "slug": e.slug,
            "tokens": _tokens(text),
            "text_low": text.lower(),
            "refs": set(_referenced_paths(e)),
        })
    return signals


def _match_item(name: str, ident: str, selectors: list[str], signals: list[dict]):
    """Return (status, matched_slugs) for one inventory item."""
    distinctive = _tokens(name) | _tokens(ident)
    matched: list[str] = []
    best_overlap = 0
    for sig in signals:
        overlap = len(distinctive & sig["tokens"])
        literal = any(sel and sel.lower() in sig["text_low"] for sel in selectors)
        if literal or overlap >= 2:
            matched.append(sig["slug"])
            best_overlap = max(best_overlap, overlap if not literal else 99)
        else:
            best_overlap = max(best_overlap, overlap)
    if matched:
        return "covered", matched
    if best_overlap == 1:
        return "partial", []
    return "uncovered", []


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
                status, matched = _match_item(
                    uc.get("name", ""), uc.get("id", ""), selectors, signals)
                items.append({
                    "id": uc.get("id", ""),
                    "kind": "use_case",
                    "feature": feature.get("id", fname),
                    "name": uc.get("name", ""),
                    "platform": uc.get("platform", []),
                    "status": status,
                    "matched_tests": matched,
                    "risk": _risk_score(
                        f"{fname} {uc.get('name','')} {uc.get('id','')}",
                        uc.get("user_facing", False)),
                    "evidence": uc.get("evidence", []),
                })
            for el in feature.get("ui_elements", []):
                status, matched = _match_item(
                    el.get("behavior", ""), el.get("id", ""),
                    [el.get("selector", "")], signals)
                items.append({
                    "id": el.get("id", ""),
                    "kind": "ui_element",
                    "feature": feature.get("id", fname),
                    "name": el.get("behavior", el.get("selector", "")),
                    "status": status,
                    "matched_tests": matched,
                    "risk": _risk_score(f"{fname} {el.get('behavior','')}"),
                    "evidence": [],
                })

    summary = {
        "total": len(items),
        "covered": sum(1 for i in items if i["status"] == "covered"),
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
        out += [f"**{s['covered']} covered · {s['partial']} partial · "
                f"{s['uncovered']} uncovered** (of {s['total']} items)", "",
                "## Risk-ranked gaps (uncovered & partial, highest risk first)", ""]
        gaps = [i for i in cov["items"] if i["status"] != "covered"]
        if not gaps:
            out.append("No gaps — every inventoried item maps to a regression. 🎉")
        for i in gaps:
            ev = f"  _(evidence: {', '.join(i['evidence'])})_" if i.get("evidence") else ""
            out.append(f"- **[{i['status']}]** `{i['id']}` (risk {i['risk']}, "
                       f"feature `{i['feature']}`) — {i['name']}{ev}")
        out += ["", "## Covered", ""]
        for i in cov["items"]:
            if i["status"] == "covered":
                out.append(f"- `{i['id']}` ← {', '.join(i['matched_tests'])}")
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
                print(f"{project.name}: {s['uncovered']} uncovered / "
                      f"{s['partial']} partial / {s['covered']} covered "
                      f"(of {s['total']})")
            else:
                n = len(cov["static"]["areas_with_no_regression_reference"])
                print(f"{project.name}: no inventory yet — {n} source area(s) "
                      f"with no regression reference (static signal)")
        high_risk_gaps = sum(
            1 for i in cov["items"] if i["status"] == "uncovered" and i["risk"] >= 5)
        worst = max(worst, high_risk_gaps)
    if args.fail_on_gap and worst:
        return 1
    return 0


# ---------------------------------------------------------------------------
# REORG  (FR-5 — propose & optionally apply priority/feature/serial)
# ---------------------------------------------------------------------------

SERIAL_HINTS = {"database", "db", "migration", "filesystem", "global", "shared",
                "singleton", "port", "socket", "server", "stateful"}


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
    # Otherwise derive a feature from the dominant risk-vocabulary hit.
    for kw in ("auth", "payment", "billing", "security", "admin", "upload",
               "search", "dashboard", "api", "visualization", "parallel",
               "priority", "feature"):
        if kw in toks:
            return [kw]
    return []


def suggest_serial(entry: Entry) -> bool:
    return bool(_tokens(_effective_text(entry.task)) & SERIAL_HINTS)


def build_reorg(projects: list[Path]) -> dict:
    proposals: list[dict] = []
    feature_buckets: dict[str, int] = {}
    n_normal = 0
    for project in projects:
        inv = _read_json(_foresight_root(project) / "inventory" / "inventory.json")
        for entry in discover_entries(project):
            sp = suggest_priority(entry)
            sf = suggest_features(entry, inv if isinstance(inv, dict) else None)
            ss = suggest_serial(entry)
            change = {
                "priority": None if sp == entry.priority else sp,
                "feature": None if sorted(sf) == sorted(entry.features) else sf,
                "serial": None if ss == entry.serial else ss,
            }
            for f in (sf or ["untagged"]):
                feature_buckets[f] = feature_buckets.get(f, 0) + 1
            if entry.priority == "normal":
                n_normal += 1
            proposals.append({
                "slug": entry.slug,
                "project": str(project),
                "current": {"priority": entry.priority,
                            "feature": entry.features, "serial": entry.serial},
                "suggested": {"priority": sp, "feature": sf, "serial": ss},
                "changes": {k: v for k, v in change.items() if v is not None},
            })
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
        for k, v in p["changes"].items():
            bits.append(f"{k}: {p['current'][k]!r} → {v!r}")
        out.append(f"- `{p['slug']}` — " + "; ".join(bits))
    out += ["", "_Apply with_ `foresight.py reorg --apply` _(writes priority/feature/"
            "serial back into each replay.json, backs up to replay.json.bak, idempotent)._"]
    return "\n".join(out)


def apply_reorg(reorg: dict) -> int:
    """Write suggested priority/feature/serial back into replay.json files.

    Backwards-compatible: only adds/updates those three keys, preserves every
    other key and its order, backs the original up to replay.json.bak, and is
    idempotent (re-running makes no further change)."""
    changed = 0
    for p in reorg["proposals"]:
        if not p["changes"]:
            continue
        replay_path = Path(p["project"]) / ".tdd" / "regression" / p["slug"] / "replay.json"
        data = _read_json(replay_path)
        if not isinstance(data, dict):
            continue
        backup = replay_path.with_suffix(".json.bak")
        if not backup.exists():
            backup.write_text(replay_path.read_text())
        for key in ("priority", "feature", "serial"):
            if key in p["changes"]:
                data[key] = p["changes"][key]
        replay_path.write_text(json.dumps(data, indent=2) + "\n")
        changed += 1
    return changed


def cmd_reorg(args) -> int:
    projects = resolve_projects(args)
    reorg = build_reorg(projects)
    for project in projects:
        scoped = dict(reorg)
        scoped["proposals"] = [p for p in reorg["proposals"] if p["project"] == str(project)]
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
        n = apply_reorg(reorg)
        print(f"applied changes to {n} replay.json file(s) (backups: *.json.bak)")
    return 0


# ---------------------------------------------------------------------------
# INIT + REPORT
# ---------------------------------------------------------------------------


def cmd_init(args) -> int:
    for project in resolve_projects(args):
        root = _foresight_root(project)
        for sub in ("inventory", "exploration", "coverage", "audit", "reorg", "proposals"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        print(f"initialized {root}")
    return 0


def cmd_report(args) -> int:
    for project in resolve_projects(args):
        root = _foresight_root(project)
        audit = _read_json(root / "audit" / "audit.json")
        cov = _read_json(root / "coverage" / "coverage.json")
        reorg = _read_json(root / "reorg" / "reorg.json")
        proposals = sorted(p.name for p in (root / "proposals").glob("*") if p.is_dir())
        explorations = sorted(p.name for p in (root / "exploration").glob("*") if p.is_dir())

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

        _write_text(root / "report.md", "\n".join(out))
        print(f"wrote {root / 'report.md'}")
    return 0


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
    p_audit.set_defaults(func=cmd_audit)

    p_cov = sub.add_parser("coverage", help="build coverage map + gap report")
    _add_project_args(p_cov)
    p_cov.add_argument("--json", action="store_true", help="emit JSON to stdout")
    p_cov.add_argument("--fail-on-gap", action="store_true",
                       help="exit non-zero if any high-risk (>=5) gap is uncovered")
    p_cov.set_defaults(func=cmd_coverage)

    p_reorg = sub.add_parser("reorg", help="propose/apply priority+feature grouping")
    _add_project_args(p_reorg)
    p_reorg.add_argument("--json", action="store_true", help="emit JSON to stdout")
    p_reorg.add_argument("--apply", action="store_true",
                         help="write suggestions into replay.json (backs up to *.bak)")
    p_reorg.set_defaults(func=cmd_reorg)

    p_report = sub.add_parser("report", help="assemble report.md from artifacts")
    _add_project_args(p_report)
    p_report.set_defaults(func=cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
