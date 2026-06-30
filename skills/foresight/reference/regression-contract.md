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

## The defect foresight exists to catch

`run_command` is the field hindsight and `tdd_regression.py replay` execute. If
it is **empty or missing**, both return status `no_run_command` and the entry
silently never runs — it looks present in `list` output but provides zero
protection. `foresight audit` flags this as the highest-severity finding
(`NO_RUN_COMMAND`). (As of this writing, every entry in hindsight's own
`.tdd/regression/` has an empty `run_command` — exactly the blind spot foresight
surfaces.)

## How foresight touches the contract

- **Reads:** `replay.json` (via an `Entry` model with the same `priority` /
  `features` / `serial` semantics as hindsight), plus `task.md` / `test_plan.md`
  for stale-path and metadata checks, and `runs/` for never-run detection.
- **Writes — metadata only:** `reorg --apply` adds/updates exactly `priority`,
  `feature`, and `serial` in an existing `replay.json`. Every other key and its
  order is preserved, the original is backed up to `replay.json.bak`, and
  re-running changes nothing (idempotent).
- **Never:** foresight does not create or edit entry *content* in
  `.tdd/regression/`. New regressions are written as **proposals** elsewhere.

## Proposals (new regressions foresight suggests)

A proposal is a draft regression entry written under
`<project>/.tdd/foresight/proposals/<slug>/`:

```
proposals/<slug>/
  task.md          # the gap framed as a /tdd task
  test_plan.md     # tests in iterative-tdd's test-plan structure
  replay.json      # draft manifest WITH a real run_command + proposed priority/feature
  EVIDENCE.md      # why: the uncovered use case + screenshot/route that motivated it
```

`replay.json` for a proposal uses the inventory's `test_run_command` (scoped to
the relevant test file) so it is replayable the moment the test exists.

### Promotion

A proposal becomes a real regression by handing its task to iterative-tdd:

```
/tdd <paste task.md, or point at proposals/<slug>/task.md>
```

When that TDD session succeeds it writes the real entry into
`.tdd/regression/<slug>/`, after which hindsight replays it on every sweep. This
keeps the boundary clean: **foresight proposes, iterative-tdd implements,
hindsight replays.**
