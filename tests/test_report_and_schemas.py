"""Tests for report extras (HTML, off-schema artifacts) and the `validate` subcommand.

Run: pytest -q tests/test_report_and_schemas.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import foresight  # noqa: E402
from test_foresight import base_replay, make_entry  # noqa: E402
from test_catalog import complete_el, write_catalog_inventory  # noqa: E402

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "skills" / "foresight" / "reference" / "schemas"


def run_everything(project: Path) -> None:
    make_entry(project, "login-auth",
               replay=base_replay(task="add login authentication flow",
                                  tests=[{"id": "T1", "name": "login with valid credentials"}]),
               runs=True)
    write_catalog_inventory(project, [{
        "id": "auth", "name": "Authentication", "sources": ["src/auth.py:1"],
        "use_cases": [{"id": "auth.login.valid", "name": "login with valid credentials",
                       "platform": ["web"], "user_facing": True, "ui_elements": ["#email"]}],
        "ui_elements": [complete_el(id="login-submit", use_case="auth.login.valid")],
    }])
    for cmd in ("init", "audit", "coverage", "catalog", "reorg", "visual", "report"):
        foresight.main([cmd, "--project", str(project)])


# ---------------------------------------------------------------------------
# report --html
# ---------------------------------------------------------------------------


def test_report_html_is_self_contained(tmp_path):
    run_everything(tmp_path)
    assert foresight.main(["report", "--project", str(tmp_path), "--html"]) == 0
    html_path = tmp_path / ".tdd" / "foresight" / "report.html"
    html = html_path.read_text()
    assert html.lstrip().lower().startswith("<!doctype html")
    assert "<h1>" in html and "foresight report" in html
    assert "Corpus audit" in html
    assert 'src="http' not in html and 'href="http' not in html
    assert "<script" not in html


def test_report_html_escapes_markup(tmp_path):
    # a proposal slug (a directory name) is echoed into the report verbatim
    (tmp_path / ".tdd" / "foresight" / "proposals" / "x-<b>bold").mkdir(parents=True)
    foresight.main(["report", "--project", str(tmp_path), "--html"])
    html = (tmp_path / ".tdd" / "foresight" / "report.html").read_text()
    assert "<b>bold" not in html
    assert "&lt;b&gt;bold" in html


# ---------------------------------------------------------------------------
# report lists artifacts the agents wrote outside the documented set
# ---------------------------------------------------------------------------


def test_report_lists_off_schema_artifacts(tmp_path):
    root = tmp_path / ".tdd" / "foresight"
    (root / "inventory").mkdir(parents=True)
    (root / "inventory" / "roles-matrix.json").write_text("{}")
    (root / "coverage").mkdir()
    (root / "coverage" / "per-role-matrix.md").write_text("# roles\n")
    (root / "audit").mkdir()
    (root / "audit" / "auditor_notes.md").write_text("# notes\n")
    foresight.main(["report", "--project", str(tmp_path)])
    report = (root / "report.md").read_text()
    assert "## Other artifacts" in report
    assert "inventory/roles-matrix.json" in report
    assert "coverage/per-role-matrix.md" in report
    assert "audit/auditor_notes.md" in report


def test_report_omits_other_artifacts_section_when_none(tmp_path):
    make_entry(tmp_path, "e", replay=base_replay())
    foresight.main(["audit", "--project", str(tmp_path)])
    foresight.main(["report", "--project", str(tmp_path)])
    assert "## Other artifacts" not in (tmp_path / ".tdd" / "foresight" / "report.md").read_text()


# ---------------------------------------------------------------------------
# schema validation (acceptance criterion 6)
# ---------------------------------------------------------------------------


def test_schema_files_exist_for_every_artifact():
    for name in ("inventory", "audit", "coverage", "reorg", "catalog", "visual",
                 "visual-baseline", "ui-map", "overrides"):
        assert (SCHEMA_DIR / f"{name}.schema.json").exists(), name


def test_validator_reports_type_required_enum_and_items():
    schema = {
        "type": "object",
        "required": ["n", "status", "tags"],
        "properties": {
            "n": {"type": "integer"},
            "status": {"type": "string", "enum": ["ok", "bad"]},
            "tags": {"type": "array", "items": {"type": "string"}},
            "opt": {"type": ["string", "null"]},
        },
    }
    assert foresight.validate_json({"n": 1, "status": "ok", "tags": ["a"], "opt": None}, schema) == []
    errors = foresight.validate_json({"n": "1", "status": "meh", "tags": ["a", 2]}, schema)
    joined = " | ".join(errors)
    assert "$.n" in joined and "$.status" in joined and "$.tags[1]" in joined
    assert any("required" in e for e in foresight.validate_json({}, schema))


def test_validate_command_passes_for_generated_artifacts(tmp_path):
    run_everything(tmp_path)
    assert foresight.main(["validate", "--project", str(tmp_path)]) == 0


def test_validate_command_flags_malformed_inventory(tmp_path, capsys):
    inv = tmp_path / ".tdd" / "foresight" / "inventory" / "inventory.json"
    inv.parent.mkdir(parents=True)
    inv.write_text(json.dumps({"features": {"auth": {}}}))
    assert foresight.main(["validate", "--project", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "inventory/inventory.json" in out and "$.features" in out


def test_validate_command_flags_unknown_status_in_coverage(tmp_path):
    cov = tmp_path / ".tdd" / "foresight" / "coverage" / "coverage.json"
    cov.parent.mkdir(parents=True)
    cov.write_text(json.dumps({
        "generated_at_iso": "x", "project": "p", "inventory_present": True,
        "summary": {"total": 1, "covered": 0, "weak": 0, "partial": 0, "uncovered": 1},
        "items": [{"id": "a", "kind": "use_case", "feature": "f", "name": "n",
                   "status": "maybe", "matched_tests": [], "weak_matches": [],
                   "risk": 1.0, "evidence": []}],
    }))
    assert foresight.main(["validate", "--project", str(tmp_path)]) == 1


def test_validate_command_is_quiet_when_nothing_to_validate(tmp_path):
    assert foresight.main(["validate", "--project", str(tmp_path)]) == 0


def test_inventory_schema_accepts_visual_fields_and_rejects_bad_story(tmp_path):
    schema = json.loads((SCHEMA_DIR / "inventory.schema.json").read_text())
    good = {"generated_at_iso": "x", "project": "p", "features": [{
        "id": "f", "name": "F", "use_cases": [], "ui_elements": [complete_el()]}]}
    assert foresight.validate_json(good, schema) == []
    bad = json.loads(json.dumps(good))
    bad["features"][0]["ui_elements"][0]["user_story"] = 42
    assert foresight.validate_json(bad, schema)
