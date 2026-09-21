"""Tests for the review-driven fixes to the deterministic core.

Run: pytest -q tests/test_core_fixes.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import foresight  # noqa: E402
from test_foresight import base_replay, make_entry  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def write_inventory(project: Path, features: list) -> None:
    inv = {"generated_at_iso": "2026-01-01T00:00:00Z",
           "project": str(project), "features": features}
    path = project / ".tdd" / "foresight" / "inventory" / "inventory.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(inv, indent=2) + "\n")


def replay_of(project: Path, slug: str) -> dict:
    return json.loads((project / ".tdd" / "regression" / slug / "replay.json").read_text())


# ---------------------------------------------------------------------------
# catalog: backslashes in content must not break re-injection
# ---------------------------------------------------------------------------


def test_catalog_reinjection_survives_backslashes(tmp_path):
    write_inventory(tmp_path, [{
        "id": "f", "name": "F", "use_cases": [], "ui_elements": [{
            "id": "e", "selector": r"div\:hover", "role": "button", "behavior": "b",
            "user_story": "s", "source_refs": [r"src\win\file.py:1"],
        }]}])
    md = tmp_path / ".tdd" / "foresight" / "inventory" / "inventory.md"
    assert foresight.main(["catalog", "--project", str(tmp_path)]) == 0
    first = md.read_text()
    assert foresight.main(["catalog", "--project", str(tmp_path)]) == 0
    assert md.read_text() == first
    assert r"div\:hover" in first


# ---------------------------------------------------------------------------
# multi-project: each project's artifacts carry ITS OWN totals
# ---------------------------------------------------------------------------


def test_all_projects_audit_writes_per_project_totals(tmp_path, monkeypatch):
    p1, p2 = tmp_path / "p1", tmp_path / "p2"
    make_entry(p1, "broken", replay=base_replay(run_command=""))
    make_entry(p2, "fine", replay=base_replay(feature="auth"), runs=True)
    monkeypatch.setattr(foresight, "discover_projects", lambda: [p1, p2])

    foresight.main(["audit", "--all-projects"])

    a1 = json.loads((p1 / ".tdd" / "foresight" / "audit" / "audit.json").read_text())
    a2 = json.loads((p2 / ".tdd" / "foresight" / "audit" / "audit.json").read_text())
    assert a1["n_entries"] == 1 and a1["n_not_replayable"] == 1
    assert a2["n_entries"] == 1 and a2["n_not_replayable"] == 0
    assert a2["health_score"] > a1["health_score"]
    assert a1["projects"] == [str(p1)] and a2["projects"] == [str(p2)]


def test_all_projects_reorg_writes_per_project_totals(tmp_path, monkeypatch):
    p1, p2 = tmp_path / "p1", tmp_path / "p2"
    make_entry(p1, "pay", replay=base_replay(task="payment checkout charge flow"))
    make_entry(p2, "ok", replay=base_replay(task="tweak footer", priority="low", feature="ui"))
    monkeypatch.setattr(foresight, "discover_projects", lambda: [p1, p2])

    foresight.main(["reorg", "--all-projects"])

    r1 = json.loads((p1 / ".tdd" / "foresight" / "reorg" / "reorg.json").read_text())
    r2 = json.loads((p2 / ".tdd" / "foresight" / "reorg" / "reorg.json").read_text())
    assert r1["n_entries"] == 1 and r2["n_entries"] == 1
    assert set(r1["feature_buckets"]) == {"payment"}
    assert set(r2["feature_buckets"]) == {"ui"}


# ---------------------------------------------------------------------------
# run_command extraction from test_plan.md (the iterative-tdd contract)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("body,expected", [
    ("## How to run the tests\n\n```\npytest -q tests/test_x.py\n```\n", "pytest -q tests/test_x.py"),
    ("## How to run the tests\n\n```bash\npytest -q tests/test_x.py\n```\n", "pytest -q tests/test_x.py"),
    ("## How to run the tests\n\n`pytest -q tests/test_x.py`\n", "pytest -q tests/test_x.py"),
    ("## How to run the tests\n\n```bash\ncd backend && \\\n  pytest -q\n```\n", "cd backend && pytest -q"),
    ("## How to run the tests\n\nRun this:\n\n```\nnpm test\n```\n", "npm test"),
    ("## Something else\n\n```\npytest\n```\n", ""),
])
def test_extract_run_command_handles_fence_styles(tmp_path, body, expected):
    tp = tmp_path / "test_plan.md"
    tp.write_text("# Test plan\n\n" + body + "\n## Tests\n\n### T1 — x\n")
    assert foresight.extract_run_command(tp) == expected


def test_audit_fix_run_command_repairs_empty_entries(tmp_path):
    entry_dir = make_entry(tmp_path, "broken", replay=base_replay(run_command=""))
    (entry_dir / "test_plan.md").write_text(
        "# Test plan\n\n## How to run the tests\n\n```\npytest -q tests/test_x.py\n```\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("")

    rc = foresight.main(["audit", "--project", str(tmp_path), "--fix-run-command"])

    data = replay_of(tmp_path, "broken")
    assert data["run_command"] == "pytest -q tests/test_x.py"
    assert (entry_dir / "replay.json.bak").exists()
    assert rc == 0  # the corpus is clean after the repair
    audit = json.loads((tmp_path / ".tdd" / "foresight" / "audit" / "audit.json").read_text())
    assert audit["n_not_replayable"] == 0


def test_audit_fix_run_command_never_overwrites_existing(tmp_path):
    entry_dir = make_entry(tmp_path, "keep", replay=base_replay(run_command="make test"))
    (entry_dir / "test_plan.md").write_text(
        "## How to run the tests\n\n```\npytest -q\n```\n")
    foresight.main(["audit", "--project", str(tmp_path), "--fix-run-command"])
    assert replay_of(tmp_path, "keep")["run_command"] == "make test"
    assert not (entry_dir / "replay.json.bak").exists()


def test_audit_fix_run_command_leaves_unfixable_flagged(tmp_path):
    make_entry(tmp_path, "hopeless", replay=base_replay(run_command=""))
    rc = foresight.main(["audit", "--project", str(tmp_path), "--fix-run-command"])
    assert rc == 1
    assert replay_of(tmp_path, "hopeless")["run_command"] == ""


# ---------------------------------------------------------------------------
# reorg --apply must not clobber explicit human metadata
# ---------------------------------------------------------------------------


def test_reorg_apply_protects_explicit_priority_and_feature(tmp_path):
    make_entry(tmp_path, "tweak-footer",
               replay=base_replay(task="tweak footer spacing",
                                  priority="critical", feature=["marketing-site"]))
    reorg = foresight.build_reorg([tmp_path])
    [p] = reorg["proposals"]
    assert set(p["protected"]) >= {"priority", "feature"}

    assert foresight.apply_reorg(reorg) == 0
    data = replay_of(tmp_path, "tweak-footer")
    assert data["priority"] == "critical"
    assert data["feature"] == ["marketing-site"]


def test_reorg_apply_force_overrides_protection(tmp_path):
    make_entry(tmp_path, "tweak-footer",
               replay=base_replay(task="tweak footer spacing",
                                  priority="critical", feature=["marketing-site"]))
    reorg = foresight.build_reorg([tmp_path])
    assert foresight.apply_reorg(reorg, force=True) == 1
    assert replay_of(tmp_path, "tweak-footer")["priority"] == "low"


def test_reorg_apply_never_writes_empty_feature(tmp_path):
    make_entry(tmp_path, "plain", replay=base_replay(task="tweak footer spacing"))
    reorg = foresight.build_reorg([tmp_path])
    foresight.apply_reorg(reorg)
    data = replay_of(tmp_path, "plain")
    assert "feature" not in data


def test_reorg_cli_force_flag(tmp_path):
    make_entry(tmp_path, "tweak-footer",
               replay=base_replay(task="tweak footer spacing", priority="critical"))
    foresight.main(["reorg", "--project", str(tmp_path), "--apply"])
    assert replay_of(tmp_path, "tweak-footer")["priority"] == "critical"
    foresight.main(["reorg", "--project", str(tmp_path), "--apply", "--force"])
    assert replay_of(tmp_path, "tweak-footer")["priority"] == "low"


# ---------------------------------------------------------------------------
# reorg overrides.json (the architect's corrections) feed the plan
# ---------------------------------------------------------------------------


def test_reorg_honors_overrides_file(tmp_path):
    make_entry(tmp_path, "pay-flow", replay=base_replay(task="payment checkout charge flow"))
    ov = tmp_path / ".tdd" / "foresight" / "reorg" / "overrides.json"
    ov.parent.mkdir(parents=True, exist_ok=True)
    ov.write_text(json.dumps({
        "pay-flow": {"priority": "high", "feature": ["billing"], "serial": True,
                     "reason": "checkout hits the shared Stripe sandbox"}}))

    reorg = foresight.build_reorg([tmp_path])
    [p] = reorg["proposals"]
    assert p["suggested"] == {"priority": "high", "feature": ["billing"], "serial": True}
    assert p["override"]["reason"] == "checkout hits the shared Stripe sandbox"

    foresight.apply_reorg(reorg)
    data = replay_of(tmp_path, "pay-flow")
    assert data["priority"] == "high"
    assert data["feature"] == ["billing"]
    assert data["serial"] is True


def test_reorg_override_with_invalid_priority_is_ignored(tmp_path):
    make_entry(tmp_path, "pay-flow", replay=base_replay(task="payment checkout charge flow"))
    ov = tmp_path / ".tdd" / "foresight" / "reorg" / "overrides.json"
    ov.parent.mkdir(parents=True, exist_ok=True)
    ov.write_text(json.dumps({"pay-flow": {"priority": "urgent"}}))
    reorg = foresight.build_reorg([tmp_path])
    [p] = reorg["proposals"]
    assert p["suggested"]["priority"] in ("critical", "high")


# ---------------------------------------------------------------------------
# coverage matcher: never claim coverage on weak evidence
# ---------------------------------------------------------------------------


def signals_for(*entries: tuple) -> list:
    """(slug, task, [test names]) tuples -> coverage signals, no disk needed."""
    return foresight._coverage_signals([
        foresight.Entry(slug, Path("."), Path("."),
                        {"task": task, "tests": [{"id": f"T{i}", "name": n}
                                                  for i, n in enumerate(names, 1)]})
        for slug, task, names in entries
    ])


def test_plain_word_selector_does_not_literal_match():
    sig = signals_for(("fix-button-color", "fix the button color on the about page", []))
    status, matched = foresight._match_item(
        "export user data as csv", "reports.export.csv", ["button"], sig)
    assert status == "uncovered"
    assert matched == []


def test_real_selector_literal_matches():
    sig = signals_for(("about-cta", "the about page CTA button[data-cta=signup] works", []))
    status, matched = foresight._match_item(
        "sign up from the about page", "marketing.signup", ["button[data-cta=signup]"], sig)
    assert status == "covered"
    assert matched == ["about-cta"]


def test_two_token_overlap_on_rich_item_is_weak_not_covered():
    # "billing" + "status" overlap, but the item is about checkout, not status.
    sig = signals_for(("billing-status-guard",
                       "billing role guard: GET /api/billing/status returns 403 for volunteers",
                       ["billing status forbidden for volunteer"]))
    status, matched = foresight._match_item(
        "start a Stripe checkout session for the billing status page banner",
        "billing.checkout.start", [], sig)
    assert status == "weak"
    assert matched == []


def test_three_token_overlap_is_covered():
    sig = signals_for(("login-auth", "add login authentication flow",
                       ["login with valid credentials"]))
    status, matched = foresight._match_item(
        "login with valid credentials", "auth.login.valid", [], sig)
    assert status == "covered"
    assert matched == ["login-auth"]


def test_source_ref_match_counts_as_covered(tmp_path):
    entry_dir = make_entry(tmp_path, "refund-tests",
                           replay=base_replay(task="cover the money paths"))
    (entry_dir / "test_plan.md").write_text("Test file: `tests/test_refund.py` exercises "
                                            "`src/billing/refund.py`.\n")
    write_inventory(tmp_path, [{
        "id": "billing", "name": "Billing", "use_cases": [],
        "ui_elements": [{"id": "refund-btn", "selector": "#refund", "role": "button",
                         "behavior": "issues a refund",
                         "source_refs": ["src/billing/refund.py:42"]}],
    }])
    cov = foresight.build_coverage(tmp_path)
    [item] = cov["items"]
    assert item["status"] == "covered"
    assert item["matched_tests"] == ["refund-tests"]


def test_coverage_summary_counts_weak_and_gaps_md_lists_it(tmp_path):
    make_entry(tmp_path, "billing-status-guard",
               replay=base_replay(task="billing role guard: GET /api/billing/status 403",
                                  tests=[{"id": "T1", "name": "billing status forbidden"}]))
    write_inventory(tmp_path, [{
        "id": "billing", "name": "Billing",
        "use_cases": [{"id": "billing.checkout.start",
                       "name": "start a Stripe checkout session for the billing status page banner",
                       "user_facing": True, "ui_elements": []}],
        "ui_elements": [],
    }])
    foresight.main(["coverage", "--project", str(tmp_path)])
    cov = json.loads((tmp_path / ".tdd" / "foresight" / "coverage" / "coverage.json").read_text())
    assert cov["summary"]["weak"] == 1
    assert cov["items"][0]["weak_matches"] == ["billing-status-guard"]
    gaps = (tmp_path / ".tdd" / "foresight" / "coverage" / "gaps.md").read_text()
    assert "[weak]" in gaps and "billing.checkout.start" in gaps


def test_fail_on_gap_counts_weak_high_risk_items(tmp_path):
    make_entry(tmp_path, "billing-status-guard",
               replay=base_replay(task="billing role guard: GET /api/billing/status 403",
                                  tests=[{"id": "T1", "name": "billing status forbidden"}]))
    write_inventory(tmp_path, [{
        "id": "billing", "name": "Billing",
        "use_cases": [{"id": "billing.checkout.start",
                       "name": "start a Stripe checkout session for the billing status page banner",
                       "user_facing": True, "ui_elements": []}],
        "ui_elements": [],
    }])
    assert foresight.main(["coverage", "--project", str(tmp_path), "--fail-on-gap"]) == 1


def test_ui_element_screenshot_is_coverage_evidence(tmp_path):
    write_inventory(tmp_path, [{
        "id": "auth", "name": "Auth", "use_cases": [],
        "ui_elements": [{"id": "login-btn", "selector": "#login", "role": "button",
                         "behavior": "submits login",
                         "visual": {"screenshot": "exploration/x/web/0001-login.png"}}],
    }])
    [item] = foresight.build_coverage(tmp_path)["items"]
    assert item["evidence"] == ["exploration/x/web/0001-login.png"]


# ---------------------------------------------------------------------------
# audit: partial staleness, near-duplicates
# ---------------------------------------------------------------------------


def test_partially_stale_paths_are_reported_as_info(tmp_path):
    entry_dir = make_entry(tmp_path, "x", replay=base_replay(run_command="pytest tests/test_real.py"))
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_real.py").write_text("")
    (entry_dir / "test_plan.md").write_text("see tests/test_real.py and tests/test_gone.py\n")
    [v] = foresight.build_audit([tmp_path])["entries"]
    stale = [f for f in v["findings"] if f["code"] == "STALE_TEST_PATHS"]
    assert len(stale) == 1
    assert stale[0]["severity"] == "info"
    assert "tests/test_gone.py" in stale[0]["message"]


def test_near_duplicate_tasks_are_flagged(tmp_path):
    make_entry(tmp_path, "add-idempotency-keys",
               replay=base_replay(task="add idempotency keys to POST /payments so retries don't double-charge"))
    make_entry(tmp_path, "add-idempotency-keys-v2",
               replay=base_replay(task="add idempotency keys to POST /payments so retries do not double-charge"))
    audit = foresight.build_audit([tmp_path])
    dups = [(v["slug"], f) for v in audit["entries"] for f in v["findings"]
            if f["code"] == "NEAR_DUPLICATE"]
    assert len(dups) == 1
    slug, finding = dups[0]
    assert slug == "add-idempotency-keys-v2"
    assert finding["severity"] == "warning"
    assert "add-idempotency-keys" in finding["message"]


def test_distinct_tasks_are_not_near_duplicates(tmp_path):
    make_entry(tmp_path, "a", replay=base_replay(task="add idempotency keys to POST /payments"))
    make_entry(tmp_path, "b", replay=base_replay(task="render the footer with the current year"))
    audit = foresight.build_audit([tmp_path])
    assert not any(f["code"] == "NEAR_DUPLICATE" for v in audit["entries"] for f in v["findings"])


# ---------------------------------------------------------------------------
# reorg: generic feature fallback vocabulary
# ---------------------------------------------------------------------------


def test_feature_fallback_ignores_metadata_words(tmp_path):
    e = foresight.Entry("x", tmp_path, tmp_path,
                        base_replay(task="add priority sorting to the feature list, run in parallel"))
    assert foresight.suggest_features(e, None) == []


# ---------------------------------------------------------------------------
# catalog: a screenshot only counts if the file exists
# ---------------------------------------------------------------------------


def catalog_el(**over) -> dict:
    el = {"id": "el", "selector": "#s", "role": "button", "behavior": "b",
          "user_story": "As a user, I want s so that b", "source_refs": ["src/a.py:1"],
          "visual": {"screenshot": "exploration/r/web/0001-s.png", "label": "S"}}
    el.update(over)
    return el


def test_catalog_flags_dangling_screenshot_file(tmp_path):
    write_inventory(tmp_path, [{"id": "f", "name": "F", "use_cases": [],
                                "ui_elements": [catalog_el()]}])
    cat = foresight.build_catalog(tmp_path)
    [r] = cat["records"]
    assert r["missing"] == ["screenshot_file"]
    assert r["screenshot_exists"] is False
    assert foresight.main(["catalog", "--project", str(tmp_path), "--fail-on-incomplete"]) == 1


def test_catalog_accepts_existing_screenshot_file(tmp_path):
    shot = tmp_path / ".tdd" / "foresight" / "exploration" / "r" / "web" / "0001-s.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"\x89PNG")
    write_inventory(tmp_path, [{"id": "f", "name": "F", "use_cases": [],
                                "ui_elements": [catalog_el()]}])
    [r] = foresight.build_catalog(tmp_path)["records"]
    assert r["missing"] == []
    assert r["screenshot_exists"] is True


# ---------------------------------------------------------------------------
# coverage matcher: generic words and task-only mentions are not evidence
# (found on a real inventory: a "comprehensive sweep" entry whose task text
#  mentioned billing/stripe/user covered the checkout use case)
# ---------------------------------------------------------------------------


def test_generic_words_do_not_count_as_shared_tokens():
    sig = signals_for(("rbac-phase-1",
                       "Phase 1 of Staff/Owner RBAC: billing role guard, plan field on the user model",
                       ["Organization model has owner_id column"]))
    status, matched = foresight._match_item(
        "Billing user starts Stripe checkout to upgrade plan", "billing.checkout", [], sig)
    assert status != "covered"
    assert matched == []


def test_task_only_mentions_without_a_named_test_are_weak():
    sig = signals_for(("owner-sweep",
                       "Owner-role comprehensive regression sweep: owners keep hitting bugs in "
                       "billing, stripe checkout, upgrade flows, schedule and settings pages",
                       ["schedule_page: FAB gated on canManageShifts",
                        "settings_page: owner tab visible"]))
    status, matched = foresight._match_item(
        "Billing user starts Stripe checkout to upgrade plan", "billing.checkout", [], sig)
    assert status == "weak"
    assert matched == []


def test_named_tests_make_the_same_overlap_covered():
    sig = signals_for(("billing-tests",
                       "Write backend/tests/test_billing.py for the Stripe money-path routes",
                       ["checkout: billing role creates a stripe customer",
                        "checkout: missing price_id returns 400"]))
    status, matched = foresight._match_item(
        "Billing user starts Stripe checkout to upgrade plan", "billing.checkout", [], sig)
    assert status == "covered"
    assert matched == ["billing-tests"]


def test_coverage_items_carry_a_match_reason(tmp_path):
    make_entry(tmp_path, "about-cta",
               replay=base_replay(task="the about page CTA button[data-cta=signup] works"))
    make_entry(tmp_path, "login-auth",
               replay=base_replay(task="add login authentication flow",
                                  tests=[{"id": "T1", "name": "login with valid credentials"}]))
    write_inventory(tmp_path, [{
        "id": "site", "name": "Site",
        "use_cases": [
            {"id": "marketing.signup", "name": "sign up from the about page",
             "ui_elements": ["button[data-cta=signup]"]},
            {"id": "auth.login.valid", "name": "login with valid credentials", "ui_elements": []},
            {"id": "reports.export.csv", "name": "export a quarterly report as csv", "ui_elements": []},
        ],
        "ui_elements": [],
    }])
    by_id = {i["id"]: i for i in foresight.build_coverage(tmp_path)["items"]}
    assert by_id["marketing.signup"]["match_reason"].startswith("literal:")
    assert by_id["auth.login.valid"]["match_reason"].startswith("tokens:")
    assert by_id["reports.export.csv"]["match_reason"] == "none"
