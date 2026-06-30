# Live exploration — web, Android, iOS

foresight's deterministic core (`foresight.py`) never drives the app. The
exploration is done by two scoped sub-agents using the tools available in the
session. Everything they observe is written to disk so the run is reviewable.

## Principles

1. **Read-only by default.** Navigate, read, screenshot. Do **not** click anything that mutates real state (delete, pay, send, submit-to-prod) unless the user explicitly pointed foresight at a safe/non-production target and asked to cover those flows.
2. **One screenshot per step.** Every navigation and every interaction gets a numbered screenshot so the E2E path is reconstructable from `exploration/<ts>/<platform>/`.
3. **Record behavior, not just presence.** For each interactive element capture what it *does* when exercised (observed effect), the use case it belongs to, and a stable selector/locator.
4. **Enrich the inventory.** After exploring, merge observed use cases and UI elements back into `inventory/inventory.json` so coverage analysis can map them to tests.

## Web (`foresight-web-explorer`)

Driven through the Claude-in-Chrome browser tools:

- `navigate` to each route; `get_page_text` / `read_page` to enumerate content and interactive elements; `find` to locate elements; `computer`/`form_input` to exercise flows; screenshots at each step.
- Discover routes from the cartographer's inventory (router config, sitemap, nav links) and by following links observed on each page.
- For each page record: URL, title, the interactive elements (role + selector + label), and the primary use case(s) the page serves.

Output: `exploration/<ts>/web/NNNN-<slug>.png` screenshots, `exploration/<ts>/web/pages.json` (per-page text + elements), and `exploration/<ts>/web/ui_map.json` (see schema below).

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

## `ui_map.json` schema (per platform per run)

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
         "behavior": "accepts the account email"},
        {"id": "submit", "selector": "button[type=submit]", "role": "button",
         "label": "Sign in", "behavior": "POSTs credentials, redirects to /dashboard on success"}
      ]
    }
  ]
}
```

## Degraded mode

If none of the exploration tooling is available, skip Phase 2 entirely. Static
discovery (Phase 1) + audit + coverage (from static signals) + reorg still run
and still produce a useful result; exploration only *enriches* the inventory.
