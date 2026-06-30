"""Tests for the deterministic foresight core.

Run: pytest -q   (from the plugin root, or anywhere — the import is path-robust)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import foresight  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def make_entry(project: Path, slug: str, *, replay: dict | None = None,
               files=("task.md", "plan.md", "test_plan.md"),
               runs=False) -> Path:
    """Create a regression entry dir on disk and return its path."""
    entry_dir = project / ".tdd" / "regression" / slug
    entry_dir.mkdir(parents=True, exist_ok=True)
    if replay is not None:
        (entry_dir / "replay.json").write_text(json.dumps(replay, indent=2) + "\n")
    for f in files:
        (entry_dir / f).write_text(f"# {f}\n")
    if runs:
        run = entry_dir / "runs" / "20260101-000000"
        run.mkdir(parents=True, exist_ok=True)
        (run / "result.json").write_text('{"status": "passed"}\n')
    return entry_dir


def base_replay(**over) -> dict:
    d = {
        "slug": "x", "saved_at_iso": "2026-01-01T00:00:00Z",
        "original_session": "sess", "task": "do a thing",
        "run_command": "pytest tests/test_x.py",
        "tests": [{"id": "T1", "name": "thing works"}],
    }
    d.update(over)
    return d


def entry_for(project: Path, slug: str, replay: dict) -> foresight.Entry:
    entry_dir = project / ".tdd" / "regression" / slug
    return foresight.Entry(slug=slug, project=project, entry_dir=entry_dir,
                           replay_data=replay)


# ---------------------------------------------------------------------------
# Entry model — must match hindsight semantics
# ---------------------------------------------------------------------------


def test_priority_defaults_and_validates(tmp_path):
    assert entry_for(tmp_path, "s", base_replay()).priority == "normal"
    assert entry_for(tmp_path, "s", base_replay(priority="CRITICAL")).priority == "critical"
    assert entry_for(tmp_path, "s", base_replay(priority="bogus")).priority == "normal"
    assert entry_for(tmp_path, "s", base_replay(priority=42)).priority == "normal"


def test_feature_normalizes_to_list(tmp_path):
    assert entry_for(tmp_path, "s", base_replay(feature="auth")).features == ["auth"]
    assert entry_for(tmp_path, "s", base_replay(feature=["a", "b"])).features == ["a", "b"]
    assert entry_for(tmp_path, "s", base_replay(feature=42)).features == []
    assert entry_for(tmp_path, "s", base_replay(feature=["a", 1])).features == ["a"]


def test_serial_only_true_for_real_bool(tmp_path):
    assert entry_for(tmp_path, "s", base_replay(serial=True)).serial is True
    assert entry_for(tmp_path, "s", base_replay(serial="true")).serial is False
    assert entry_for(tmp_path, "s", base_replay()).serial is False


# ---------------------------------------------------------------------------
# AUDIT
# ---------------------------------------------------------------------------


def test_audit_flags_empty_run_command(tmp_path):
    make_entry(tmp_path, "no-cmd", replay=base_replay(run_command=""))
    [v] = foresight.build_audit([tmp_path])["entries"]
    codes = {f["code"] for f in v["findings"]}
    assert "NO_RUN_COMMAND" in codes
    assert v["replayable"] is False


def test_audit_clean_entry_has_no_errors(tmp_path):
    make_entry(tmp_path, "clean",
               replay=base_replay(priority="high", feature="auth"), runs=True)
    [v] = foresight.build_audit([tmp_path])["entries"]
    errors = [f for f in v["findings"] if f["severity"] == "error"]
    assert errors == []
    assert v["replayable"] is True


def test_audit_missing_replay_json(tmp_path):
    make_entry(tmp_path, "noreplay", replay=None)  # no replay.json written
    [v] = foresight.build_audit([tmp_path])["entries"]
    codes = {f["code"] for f in v["findings"]}
    assert "MISSING_REPLAY_JSON" in codes
    assert v["replayable"] is False


def test_audit_invalid_priority_is_warning(tmp_path):
    make_entry(tmp_path, "badprio", replay=base_replay(priority="urgent"))
    [v] = foresight.build_audit([tmp_path])["entries"]
    bad = [f for f in v["findings"] if f["code"] == "INVALID_PRIORITY"]
    assert bad and bad[0]["severity"] == "warning"


def test_audit_duplicate_slug_across_projects(tmp_path):
    p1, p2 = tmp_path / "p1", tmp_path / "p2"
    make_entry(p1, "dup", replay=base_replay())
    make_entry(p2, "dup", replay=base_replay())
    audit = foresight.build_audit([p1, p2])
    dup = [f for v in audit["entries"] for f in v["findings"]
           if f["code"] == "DUPLICATE_SLUG"]
    assert len(dup) == 1


def test_health_score_perfect_when_no_entries():
    assert foresight._health_score([]) == 100


def test_health_score_drops_with_errors(tmp_path):
    make_entry(tmp_path, "broken", replay=base_replay(run_command="", tests=[]))
    audit = foresight.build_audit([tmp_path])
    assert audit["health_score"] < 100
    assert audit["n_not_replayable"] == 1


def test_cmd_audit_exit_code(tmp_path, capsys):
    make_entry(tmp_path, "broken", replay=base_replay(run_command=""))
    rc = foresight.main(["audit", "--project", str(tmp_path)])
    assert rc == 1  # error finding => non-zero (CI gate)

    # a clean corpus exits 0
    clean = tmp_path / "clean"
    make_entry(clean, "ok", replay=base_replay(feature="auth"), runs=True)
    rc2 = foresight.main(["audit", "--project", str(clean)])
    assert rc2 == 0


def test_audit_writes_artifacts(tmp_path):
    make_entry(tmp_path, "e1", replay=base_replay())
    foresight.main(["audit", "--project", str(tmp_path)])
    assert (tmp_path / ".tdd" / "foresight" / "audit" / "audit.json").exists()
    assert (tmp_path / ".tdd" / "foresight" / "audit" / "audit.md").exists()


# ---------------------------------------------------------------------------
# COVERAGE
# ---------------------------------------------------------------------------


def write_inventory(project: Path, features: list[dict]) -> None:
    inv = {"generated_at_iso": "2026-01-01T00:00:00Z",
           "project": str(project), "features": features}
    path = project / ".tdd" / "foresight" / "inventory" / "inventory.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(inv, indent=2) + "\n")


def test_coverage_matches_use_case_to_regression(tmp_path):
    # regression entry about "login authentication"
    make_entry(tmp_path, "login-auth",
               replay=base_replay(task="add login authentication flow",
                                  tests=[{"id": "T1", "name": "login with valid credentials"}]))
    write_inventory(tmp_path, [{
        "id": "auth", "name": "Authentication",
        "use_cases": [
            {"id": "auth.login.valid", "name": "login with valid credentials",
             "user_facing": True, "ui_elements": []},
            {"id": "billing.refund", "name": "process a refund payment",
             "user_facing": True, "ui_elements": []},
        ],
        "ui_elements": [],
    }])
    cov = foresight.build_coverage(tmp_path)
    by_id = {i["id"]: i for i in cov["items"]}
    assert by_id["auth.login.valid"]["status"] == "covered"
    assert by_id["billing.refund"]["status"] == "uncovered"
    # the refund gap is higher risk than a generic item
    assert by_id["billing.refund"]["risk"] > 4


def test_coverage_static_signal_without_inventory(tmp_path):
    # a source area with no regression reference
    (tmp_path / "src" / "payments").mkdir(parents=True)
    (tmp_path / "src" / "payments" / "charge.py").write_text("def charge(): ...\n")
    make_entry(tmp_path, "unrelated", replay=base_replay(task="something else"))
    cov = foresight.build_coverage(tmp_path)
    assert cov["inventory_present"] is False
    assert "src" in cov["static"]["areas_with_no_regression_reference"]


# ---------------------------------------------------------------------------
# REORG
# ---------------------------------------------------------------------------


def test_suggest_priority_from_risk(tmp_path):
    e = entry_for(tmp_path, "pay", base_replay(task="handle payment checkout and refund"))
    assert foresight.suggest_priority(e) in ("critical", "high")
    e2 = entry_for(tmp_path, "cosmetic", base_replay(task="tweak footer spacing"))
    assert foresight.suggest_priority(e2) in ("low", "normal")


def test_nongoals_text_is_ignored_for_scoring(tmp_path):
    # "auth" appears only in the Non-goals clause; it must NOT drive priority.
    e = entry_for(tmp_path, "viz",
                  base_replay(task="Build a chart view. Non-goals: auth, payment, security."))
    assert foresight.suggest_priority(e) in ("low", "normal")
    assert "auth" not in foresight.suggest_features(e, None)


def test_suggest_serial_on_shared_state(tmp_path):
    e = entry_for(tmp_path, "db", base_replay(task="run a database migration on shared schema"))
    assert foresight.suggest_serial(e) is True
    e2 = entry_for(tmp_path, "pure", base_replay(task="format a string"))
    assert foresight.suggest_serial(e2) is False


def test_reorg_apply_is_backwards_compatible_and_idempotent(tmp_path):
    make_entry(tmp_path, "pay-flow",
               replay=base_replay(task="payment checkout charge flow",
                                  custom_key="keep me"))
    reorg = foresight.build_reorg([tmp_path])
    n = foresight.apply_reorg(reorg)
    assert n == 1

    replay_path = tmp_path / ".tdd" / "regression" / "pay-flow" / "replay.json"
    data = json.loads(replay_path.read_text())
    # changed metadata written
    assert data["priority"] in ("critical", "high")
    assert data["feature"] == ["payment"]
    # unchanged keys are NOT added (minimal, backwards-compatible writes):
    # serial stayed False so it is not injected
    assert "serial" not in data
    # untouched keys preserved
    assert data["custom_key"] == "keep me"
    assert data["task"] == "payment checkout charge flow"
    # backup created
    assert replay_path.with_suffix(".json.bak").exists()

    # idempotent: a second plan over the now-updated corpus changes nothing
    reorg2 = foresight.build_reorg([tmp_path])
    assert foresight.apply_reorg(reorg2) == 0


def test_reorg_apply_writes_serial_when_it_changes(tmp_path):
    make_entry(tmp_path, "db-mig",
               replay=base_replay(task="run a database migration touching shared schema"))
    reorg = foresight.build_reorg([tmp_path])
    foresight.apply_reorg(reorg)
    data = json.loads((tmp_path / ".tdd" / "regression" / "db-mig" / "replay.json").read_text())
    assert data["serial"] is True


def test_reorg_flags_when_everything_is_normal(tmp_path):
    for i in range(4):
        make_entry(tmp_path, f"plain-{i}",
                   replay=base_replay(task=f"tweak layout number {i}"))
    reorg = foresight.build_reorg([tmp_path])
    assert reorg["reorg_needed"] is True
    assert reorg["reorg_reasons"]


# ---------------------------------------------------------------------------
# INIT / REPORT smoke
# ---------------------------------------------------------------------------


def test_init_and_report_smoke(tmp_path):
    make_entry(tmp_path, "e1", replay=base_replay())
    assert foresight.main(["init", "--project", str(tmp_path)]) == 0
    foresight.main(["audit", "--project", str(tmp_path)])
    foresight.main(["coverage", "--project", str(tmp_path)])
    foresight.main(["reorg", "--project", str(tmp_path)])
    assert foresight.main(["report", "--project", str(tmp_path)]) == 0
    assert (tmp_path / ".tdd" / "foresight" / "report.md").exists()
