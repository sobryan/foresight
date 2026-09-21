# Live exploration — web, Android, iOS — and the visual pass

foresight's deterministic core (`foresight.py`) never drives the app. The
exploration is done by two scoped sub-agents using the tools available in the
session; a third agent then inspects the screenshots. Everything they observe
is written to disk so the run is reviewable and so `foresight.py visual` can
compare runs.

## Principles

1. **Read-only by default.** Navigate, read, screenshot. Do **not** click anything that mutates real state (delete, pay, send, submit-to-prod) unless the user explicitly pointed foresight at a safe/non-production target and asked to cover those flows.
2. **One screenshot per step, stable names.** Every navigation and every interaction gets a numbered screenshot `NNNN-<slug>.png`. The slug names the *screen* (`login`, `login-error`, `dashboard`) and must be the same on every run — the visual comparison matches images by platform + slug, ignoring the step number. Keep the viewport size constant between runs.
3. **Record behavior, not just presence.** For each interactive element capture what it *does* when exercised (observed effect), the use case it belongs to, and a stable selector/locator.
4. **Enrich the inventory.** After exploring, merge observed use cases and UI elements back into `inventory/inventory.json` (with `visual.screenshot` provenance) so coverage analysis can map them to tests and the catalog can render them.
5. **Validate before returning.** `foresight.py validate --project <project>` checks `inventory.json` and every `ui_map.json` against their schemas.

## Web (`foresight-web-explorer`)

Driven through the Playwright MCP browser tools (`playwright/*`):

- `tabs_context_mcp` first, then work in a fresh tab from `tabs_create_mcp`; close it with `tabs_close_mcp`.
- `navigate` to each route; `get_page_text` / `read_page` to enumerate content and interactive elements; `find` to locate elements; `computer`/`form_input` to exercise flows; a screenshot at each step.
- Discover routes from the cartographer's inventory (router config, sitemap, nav links) and by following links observed on each page.
- For each page record: URL, title, the interactive elements (role + selector + label), and the primary use case(s) the page serves.

Output: `exploration/<ts>/web/NNNN-<slug>.png` screenshots, `exploration/<ts>/web/pages.json` (per-page text + elements), and `exploration/<ts>/web/ui_map.json` (see schema below).

Under GitHub Copilot the same agent drives the Playwright MCP server instead (`browser_navigate`, `browser_snapshot`, `browser_take_screenshot`, `browser_click`, `browser_fill_form`, …); the procedure and the on-disk output are identical. See `copilot/README.md`.

## Android (`foresight-mobile-explorer`, `--platform android`)

External CLIs the user must have installed (documented, not bundled):

- **Maestro** (`maestro test`, `maestro studio`, `maestro hierarchy`) — preferred for flow capture, or
- **Appium** (UiAutomator2 driver) for programmatic control, plus
- **`adb`** for `adb shell uiautomator dump` (UI hierarchy) and `adb exec-out screencap -p` (screenshots), and an emulator/device.

Walk the app's primary activities, dump the view hierarchy per screen, screenshot each, and map elements (resource-id / content-desc / text) to use cases.

Output: `exploration/<ts>/android/NNNN-<screen>.png`, `hierarchy-NNNN.xml`, and `ui_map.json`.

## iOS (`foresight-mobile-explorer`, `--platform ios`)

macOS + Xcode required. External CLIs:

- **`xcrun simctl`** — `xcrun simctl io booted screenshot out.png` for screenshots, boot/launch a simulator.
- **Maestro** or **Appium** (XCUITest driver) to drive the app and capture the accessibility hierarchy.

Same procedure as Android: walk screens, capture accessibility tree + screenshot per step, map elements (accessibility id / label) to use cases.

Output: `exploration/<ts>/ios/NNNN-<screen>.png`, `hierarchy-NNNN.json`, and `ui_map.json`.

## Visual-inspection pass (`foresight-visual-inspector`)

Runs after the explorers, over the run's screenshots, without re-driving the
app. For each screenshot it enumerates every visually distinct control
(including icon-only buttons and states the DOM pass missed), writes a strict
`As a <role>, I want <action> so that <benefit>` user story per element,
records `visual: {screenshot, region, label, discovered_by: "visual"}`, and
merges into `inventory.json` — matching existing elements by selector/label,
creating new ones otherwise, never deleting. It also lists high-value stories
in `inventory/visual_candidates.md` for the architect. The cartographer then
runs in `backfill` mode to add `source_refs`.

## Visual regression (`foresight.py visual`)

Deterministic, stdlib only. `visual --baseline` records the latest run
(sha256 + dimensions per image) in `visual/baseline.json`. `visual` compares
the latest run against it and writes `visual/visual.json` + `visual.md`:

| Status | Meaning |
|---|---|
| `unchanged` | identical bytes, or pixel difference within `--threshold` |
| `changed` | the PNGs differ; `diff_pct` is the percent of pixels that changed (decoded in pure Python for 8-bit non-interlaced PNGs up to 4 MP; otherwise hash-only) |
| `resized` | dimensions differ — usually a viewport or layout change |
| `new` | present in this run, not in the baseline (does not fail the gate) |
| `missing` | present in the baseline, absent from this run |

`--fail-on-change` is the CI gate. Re-record with `--baseline` when a change is intended.

## `ui_map.json` schema (per platform per run)

Machine version: `schemas/ui-map.schema.json`.

```json
{
  "platform": "web",
  "target": "http://localhost:3000",
  "explored_at_iso": "2026-05-28T18:00:00Z",
  "steps": [
    {
      "n": 1,
      "location": "/login",
      "screenshot": "0001-login.png",
      "use_case": "auth.login.valid",
      "elements": [
        {"id": "email", "selector": "#email", "role": "textbox", "label": "Email",
         "behavior": "accepts the account email",
         "user_story": "As a returning user, I want to enter my email so that the app knows who is signing in"},
        {"id": "submit", "selector": "button[type=submit]", "role": "button",
         "label": "Sign in", "behavior": "POSTs credentials, redirects to /dashboard on success"}
      ]
    }
  ]
}
```

## Degraded mode

If none of the exploration tooling is available, skip Phase 2 (and 2a–2d)
entirely. Static discovery (Phase 1) + audit + coverage (from static signals)
+ reorg still run and still produce a useful result; exploration only
*enriches* the inventory and enables the visual comparison.
