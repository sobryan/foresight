"""Tests for the catalog subcommand.

Run: pytest -q tests/test_catalog.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import foresight  # noqa: E402


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def write_catalog_inventory(project: Path, features: list) -> None:
    """Write inventory.json with the given features list (mirrors write_inventory)."""
    inv = {
        "generated_at_iso": "2026-01-01T00:00:00Z",
        "project": str(project),
        "features": features,
    }
    path = project / ".tdd" / "foresight" / "inventory" / "inventory.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(inv, indent=2) + "\n")


def complete_el(**overrides) -> dict:
    """Return a fully-documented element dict; overrides replace individual fields."""
    el = {
        "id": "el-default",
        "selector": "#submit-btn",
        "role": "button",
        "behavior": "submits the form",
        "user_story": "As a user, I want to submit the form so that I can save my work",
        "source_refs": ["src/form.py:42"],
        "visual": {
            "screenshot": "screens/form.png",
            "region": None,
            "label": "Submit",
            "discovered_by": "visual",
        },
    }
    el.update(overrides)
    return el


# ---------------------------------------------------------------------------
# T1 — test_catalog_lists_every_ui_element
# ---------------------------------------------------------------------------


def test_catalog_lists_every_ui_element(tmp_path):
    write_catalog_inventory(tmp_path, [
        {"id": "feat-a", "name": "Alpha",
         "use_cases": [],
         "ui_elements": [
             {"id": "el-a1", "selector": "#a1", "role": "button", "behavior": "a1"},
             {"id": "el-a2", "selector": "#a2", "role": "link",   "behavior": "a2"},
         ]},
        {"id": "feat-b", "name": "Beta",
         "use_cases": [],
         "ui_elements": [
             {"id": "el-b1", "selector": "#b1", "role": "input",  "behavior": "b1"},
             {"id": "el-b2", "selector": "#b2", "role": "button", "behavior": "b2"},
         ]},
    ])
    cat = foresight.build_catalog(tmp_path)
    assert cat["summary"]["n_elements"] == 4
    assert set(r["id"] for r in cat["records"]) == {"el-a1", "el-a2", "el-b1", "el-b2"}


# ---------------------------------------------------------------------------
# T2 — test_catalog_carries_new_fields
# ---------------------------------------------------------------------------


def test_catalog_carries_new_fields(tmp_path):
    el = {
        "id": "login-btn",
        "selector": "#login",
        "role": "button",
        "behavior": "submits login form",
        "user_story": "As a user, I want to log in so that I can access my account",
        "source_refs": ["src/auth/login.py:88"],
        "visual": {"screenshot": "screens/login-form.png", "region": None,
                   "label": "Log in", "discovered_by": "visual"},
    }
    write_catalog_inventory(tmp_path, [
        {"id": "auth", "name": "Auth", "use_cases": [], "ui_elements": [el]}
    ])
    cat = foresight.build_catalog(tmp_path)
    record = cat["records"][0]
    assert record["user_story"] == "As a user, I want to log in so that I can access my account"
    assert record["source_refs"] == ["src/auth/login.py:88"]
    assert record["screenshot"] == "screens/login-form.png"


# ---------------------------------------------------------------------------
# T3 — test_catalog_flags_missing_user_story
# ---------------------------------------------------------------------------


def test_catalog_flags_missing_user_story(tmp_path):
    el = complete_el(id="no-story-btn", user_story="")  # falsy user_story
    write_catalog_inventory(tmp_path, [
        {"id": "feat", "name": "Feat", "use_cases": [], "ui_elements": [el]}
    ])
    cat = foresight.build_catalog(tmp_path)
    record = next(r for r in cat["records"] if r["id"] == "no-story-btn")
    assert "user_story" in record["missing"]
    assert "no-story-btn" in {r["id"] for r in cat["incomplete"]}


# ---------------------------------------------------------------------------
# T4 — test_catalog_flags_missing_source_refs_and_screenshot
# ---------------------------------------------------------------------------


def test_catalog_flags_missing_source_refs_and_screenshot(tmp_path):
    el = complete_el(id="story-only", source_refs=[], visual={})
    write_catalog_inventory(tmp_path, [
        {"id": "feat", "name": "Feat", "use_cases": [], "ui_elements": [el]}
    ])
    cat = foresight.build_catalog(tmp_path)
    record = next(r for r in cat["records"] if r["id"] == "story-only")
    assert record["missing"] == ["source_refs", "screenshot"]


# ---------------------------------------------------------------------------
# T5 — test_catalog_complete_element_not_flagged
# ---------------------------------------------------------------------------


def test_catalog_complete_element_not_flagged(tmp_path):
    el = complete_el(id="full-el")
    write_catalog_inventory(tmp_path, [
        {"id": "feat", "name": "Feat", "use_cases": [], "ui_elements": [el]}
    ])
    cat = foresight.build_catalog(tmp_path)
    record = next(r for r in cat["records"] if r["id"] == "full-el")
    assert record["missing"] == []
    assert cat["summary"]["n_complete"] == 1
    assert "full-el" not in {r["id"] for r in cat["incomplete"]}


# ---------------------------------------------------------------------------
# T6 — test_catalog_completeness_counts
# ---------------------------------------------------------------------------


def test_catalog_completeness_counts(tmp_path):
    features = [
        {"id": "f1", "name": "F1", "use_cases": [], "ui_elements": [
            complete_el(id="el-c1"),                              # complete
            complete_el(id="el-c2"),                              # complete
            complete_el(id="el-p1", source_refs=[], visual={}),  # story only
            complete_el(id="el-p2", user_story="", source_refs=[], visual={}),  # nothing
        ]},
    ]
    write_catalog_inventory(tmp_path, features)
    cat = foresight.build_catalog(tmp_path)
    s = cat["summary"]
    assert s["n_elements"] == 4
    assert s["n_with_story"] == 3       # el-c1, el-c2, el-p1 have non-empty user_story
    assert s["n_with_source_refs"] == 2  # el-c1, el-c2
    assert s["n_with_screenshot"] == 2   # el-c1, el-c2
    assert s["n_complete"] == 2
    assert s["n_incomplete"] == 2
    assert s["completeness_pct"] == 50.0


# ---------------------------------------------------------------------------
# T7 — test_cmd_catalog_fail_on_incomplete_exit1
# ---------------------------------------------------------------------------


def test_cmd_catalog_fail_on_incomplete_exit1(tmp_path):
    el = complete_el(id="incomplete-el", user_story="")
    write_catalog_inventory(tmp_path, [
        {"id": "feat", "name": "Feat", "use_cases": [], "ui_elements": [el]}
    ])
    rc = foresight.main(["catalog", "--project", str(tmp_path), "--fail-on-incomplete"])
    assert rc == 1


# ---------------------------------------------------------------------------
# T8 — test_cmd_catalog_complete_corpus_exit0
# ---------------------------------------------------------------------------


def test_cmd_catalog_complete_corpus_exit0(tmp_path):
    write_catalog_inventory(tmp_path, [
        {"id": "feat", "name": "Feat", "use_cases": [],
         "ui_elements": [complete_el(id="el-ok")]}
    ])
    rc = foresight.main(["catalog", "--project", str(tmp_path), "--fail-on-incomplete"])
    assert rc == 0


# ---------------------------------------------------------------------------
# T9 — test_cmd_catalog_no_flag_exit0_despite_incomplete
# ---------------------------------------------------------------------------


def test_cmd_catalog_no_flag_exit0_despite_incomplete(tmp_path):
    el = complete_el(id="incomplete-el", user_story="")
    write_catalog_inventory(tmp_path, [
        {"id": "feat", "name": "Feat", "use_cases": [], "ui_elements": [el]}
    ])
    rc = foresight.main(["catalog", "--project", str(tmp_path)])  # no --fail-on-incomplete
    assert rc == 0


# ---------------------------------------------------------------------------
# T10 — test_catalog_no_inventory_is_graceful
# ---------------------------------------------------------------------------


def test_catalog_no_inventory_is_graceful(tmp_path):
    # no write_catalog_inventory call — file does not exist
    cat = foresight.build_catalog(tmp_path)
    assert cat["inventory_present"] is False
    assert cat["summary"]["n_elements"] == 0
    assert cat["summary"]["completeness_pct"] == 100

    rc = foresight.main(["catalog", "--project", str(tmp_path), "--fail-on-incomplete"])
    assert rc == 0


# ---------------------------------------------------------------------------
# T11 — test_catalog_injects_idempotent_section
# ---------------------------------------------------------------------------


def test_catalog_injects_idempotent_section(tmp_path):
    write_catalog_inventory(tmp_path, [
        {"id": "feat", "name": "Feat", "use_cases": [],
         "ui_elements": [complete_el(id="el-1")]}
    ])
    md_path = tmp_path / ".tdd" / "foresight" / "inventory" / "inventory.md"

    foresight.main(["catalog", "--project", str(tmp_path)])
    content1 = md_path.read_text()

    foresight.main(["catalog", "--project", str(tmp_path)])
    content2 = md_path.read_text()

    assert content2 == content1
    assert content2.count("<!-- foresight:catalog:start -->") == 1
    assert content2.count("<!-- foresight:catalog:end -->") == 1


# ---------------------------------------------------------------------------
# T12 — test_catalog_enriches_without_clobbering
# ---------------------------------------------------------------------------


def test_catalog_enriches_without_clobbering(tmp_path):
    write_catalog_inventory(tmp_path, [
        {"id": "feat", "name": "Feat", "use_cases": [],
         "ui_elements": [complete_el(id="el-1")]}
    ])
    md_path = tmp_path / ".tdd" / "foresight" / "inventory" / "inventory.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("# My Existing Title\n\nExisting prose paragraph.\n")

    foresight.main(["catalog", "--project", str(tmp_path)])
    content = md_path.read_text()

    assert "# My Existing Title" in content
    assert "Existing prose paragraph." in content


# ---------------------------------------------------------------------------
# T13 — test_catalog_markdown_has_story_screenshot_sources
# ---------------------------------------------------------------------------


def test_catalog_markdown_has_story_screenshot_sources(tmp_path):
    el = {
        "id": "search-input",
        "selector": "#search",
        "role": "input",
        "behavior": "accepts search query",
        "user_story": "As a user, I want to search so that I can find things",
        "source_refs": ["src/search.py:101"],
        "visual": {"screenshot": "screens/search.png", "region": None,
                   "label": "Search", "discovered_by": "visual"},
    }
    write_catalog_inventory(tmp_path, [
        {"id": "search", "name": "Search", "use_cases": [], "ui_elements": [el]}
    ])
    cat = foresight.build_catalog(tmp_path)
    section = foresight._catalog_markdown_section(cat)

    assert "screens/search.png" in section
    assert "As a user, I want to search so that I can find things" in section
    assert "src/search.py:101" in section


# ---------------------------------------------------------------------------
# T14 — test_catalog_grouped_by_feature
# ---------------------------------------------------------------------------


def test_catalog_grouped_by_feature(tmp_path):
    write_catalog_inventory(tmp_path, [
        {"id": "alpha", "name": "alpha",
         "use_cases": [], "ui_elements": [complete_el(id="el-a")]},
        {"id": "beta",  "name": "beta",
         "use_cases": [], "ui_elements": [complete_el(id="el-b")]},
    ])
    cat = foresight.build_catalog(tmp_path)
    section = foresight._catalog_markdown_section(cat)

    assert "alpha" in section
    assert "beta" in section


# ---------------------------------------------------------------------------
# T15 — test_report_includes_catalog_section
# ---------------------------------------------------------------------------


def test_report_includes_catalog_section(tmp_path):
    # 2 elements: 1 complete, 1 incomplete -> completeness_pct == 50.0
    write_catalog_inventory(tmp_path, [
        {"id": "feat", "name": "Feat", "use_cases": [], "ui_elements": [
            complete_el(id="el-done"),
            complete_el(id="el-missing", user_story=""),
        ]},
    ])
    rc_catalog = foresight.main(["catalog", "--project", str(tmp_path)])
    assert rc_catalog == 0  # catalog must succeed first

    rc_report = foresight.main(["report", "--project", str(tmp_path)])
    assert rc_report == 0

    report_path = tmp_path / ".tdd" / "foresight" / "report.md"
    assert report_path.exists()
    report_text = report_path.read_text()

    assert "UI catalog" in report_text
    assert "50.0" in report_text
