# The shared regression contract

foresight reads and writes the same on-disk format that **iterative-tdd**
produces and **hindsight** consumes. Honoring this contract exactly is what
lets the three plugins interoperate without translation.

## A regression entry on disk

```
<project>/.tdd/regression/<slug>/
  task.md          # the original task, verbatim
  plan.md          # the accepted plan (optional but expected)
  test_plan.md     # the frozen test plan with explicit pass/fail criteria
  replay.json      # the manifest hindsight/tdd_regression read
  README.md        # how to replay this entry
  runs/<ts>/       # per-replay output: result.json, stdout.txt, stderr.txt
```

## `replay.json` schema

```json
{
  "slug": "add-idempotency-keys-post-payments",
  "saved_at_iso": "2026-05-15T14:55:00Z",
  "original_session": "20260515-143200-add-idempotency-keys",
  "task": "add idempotency keys to POST /payments so retries don't double-charge",
  "run_command": "pnpm test src/payments/idempotency.test.ts",
  "tests": [
    {"id": "T1", "name": "duplicate POST returns cached response", "type": "binary"},
    {"id": "T2", "name": "p95 latency", "type": "metric",
     "metric": {"name": "p95_latency_ms", "operator": "<=", "threshold": 150}}
  ],

  "priority": "high",        // critical | high | normal | low   (hindsight: default normal)
  "feature": ["payments"],   // string or list of strings        (hindsight: → list; default [])
  "serial": true             // pin to serial execution           (hindsight: default false)
}
```

The last three keys are hindsight's extensions. They are **optional** and
fully backwards-compatible — an entry without them behaves exactly as before
(`normal`, untagged, parallel-eligible).

## The defect foresight exists to catch — and where it comes from

`run_command` is the field hindsight and `tdd_regression.py replay` execute. If
it is **empty or missing**, both return status `no_run_command` and the entry
silently never runs — it looks present in `list` output but provides zero
protection. `foresight audit` flags this as the highest-severity finding
(`NO_RUN_COMMAND`).

iterative-tdd fills `run_command` when it saves an entry by reading the
`## How to run the tests` section of the session's `test_plan.md`. Its
extractor treats a **bare ` ``` ` fence line as inline code and returns an
empty string**, so any test plan whose command sits in an untagged fence
produces an unreplayable entry; a ` ```bash ` fence or inline backticks work.
(As of 2026-09, 23 of 43 entries across this machine's projects were empty for
exactly this reason.) A one-line upstream fix is in
`docs/upstream/iterative-tdd-run-command-fence.patch`.

### Repairing entries: `audit --fix-run-command`

```bash
python3 .github/skills/foresight/scripts/foresight.py audit --project . --fix-run-command
```

For every entry whose `run_command` is empty, foresight parses the entry's own
`test_plan.md` with a fence-tolerant reader (bare fences, tagged fences, inline
code, backslash-continued lines), writes the command into `replay.json` (after
backing it up to `replay.json.bak`), and re-audits. It never overwrites a
non-empty command. Commands the test plan doesn't declare stay flagged.

## How foresight touches the contract

- **Reads:** `replay.json` (via an `Entry` model with the same `priority` /
  `features` / `serial` semantics as hindsight), plus `task.md` / `test_plan.md`
  for stale-path and metadata checks, and `runs/` for never-run detection.
- **Writes — metadata only:** `reorg --apply` adds/updates exactly `priority`,
  `feature`, and `serial` in an existing `replay.json`. Every other key and its
  order is preserved, the original is backed up to `replay.json.bak`, and
  re-running changes nothing (idempotent). Keys already present in the file are
  **protected**: the keyword heuristic never changes them; only an entry in
  `reorg/overrides.json` or `--force` does. An empty feature suggestion is never
  written.
- **Writes — repair only:** `audit --fix-run-command` fills an *empty*
  `run_command` as described above.
- **Never:** foresight does not create or edit entry *content* in
  `.tdd/regression/`. New regressions are written as **proposals** elsewhere.

## Proposals (new regressions foresight suggests)

A proposal is a draft regression entry written under
`<project>/.tdd/foresight/proposals/<slug>/`:

```
proposals/<slug>/
  task.md          # the gap framed as a /tdd task; points at this test_plan.md
  test_plan.md     # tests in iterative-tdd's test-plan structure
  replay.json      # draft manifest WITH a real run_command + proposed priority/feature
  EVIDENCE.md      # why: the uncovered use case, user story, and screenshot/route that motivated it
```

`test_plan.md` **must** begin with:

````markdown
## How to run the tests

```bash
pytest -q tests/test_refunds.py
```
````

because that is the section iterative-tdd's extractor reads when the promoted
session is saved. Use a `bash`-tagged fence or inline backticks — never a bare
fence. The command should run from the repo root without machine-specific
absolute paths, and be scoped to the new test file.

### Promotion

`/tdd` takes a task *string*; it does not read a proposal directory. So
`task.md` must say where the intended tests live, e.g. "Implement the tests in
`.tdd/foresight/proposals/<slug>/test_plan.md` and make them pass", so the
planner and test-planner open the proposal. Promote with:

```
/tdd <paste task.md>
```

When that TDD session succeeds it writes the real entry into
`.tdd/regression/<slug>/`, after which hindsight replays it on every sweep. This
keeps the boundary clean: **foresight proposes, iterative-tdd implements,
hindsight replays.**
