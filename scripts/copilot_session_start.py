#!/usr/bin/env python3
"""copilot_session_start.py — foresight's Copilot `sessionStart` hook.

Prints one line of regression-corpus health as `{"additionalContext": "..."}`
when the current project has a `.tdd/regression/` corpus, and nothing at all
otherwise (empty output = no-op in the Copilot hook contract). Never writes a
file and never fails the session: any error exits 0 silently.

Installed next to foresight.py by scripts/install_copilot.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    try:
        project = Path.cwd()
        if not (project / ".tdd" / "regression").is_dir():
            return 0
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import foresight  # noqa: E402  (the deterministic core, same directory)

        audit = foresight.build_audit([project])
        parts = [
            f"foresight: {audit['n_entries']} regression entries, "
            f"{audit['n_not_replayable']} not replayable "
            f"(health {audit['health_score']}/100)."
        ]
        vis = foresight._read_json(project / ".tdd" / "foresight" / "visual" / "visual.json")
        if isinstance(vis, dict) and vis.get("baseline_present"):
            s = vis.get("summary", {})
            flagged = s.get("changed", 0) + s.get("resized", 0) + s.get("missing", 0)
            if flagged:
                parts.append(f"{flagged} screen(s) differ from the visual baseline "
                             f"(run {vis.get('run')}).")
        parts.append("Use /foresight-audit for details (--fix-run-command repairs empty "
                     "run commands) and /foresight <url> for a full run.")
        print(json.dumps({"additionalContext": " ".join(parts)}))
        return 0
    except Exception:  # a hook must never break the session
        return 0


if __name__ == "__main__":
    sys.exit(main())
