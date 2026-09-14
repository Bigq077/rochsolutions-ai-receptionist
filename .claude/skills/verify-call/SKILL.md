---
name: verify-call
description: Answer "does Susie still work?" with a number. Run before claiming any behavioural change is done, before any deploy, or on demand. Produces a pass/fail verdict against the 97% bar, never a narrative.
---

# verify-call

**This skill produces a number and a verdict.** Not "looks good", not a summary
of what you changed — a pass rate, the bar it is measured against, and whether it
clears it. If you cannot produce a number, say so plainly; that is also a result.

Every agent runs this before claiming a behavioural change is done.

---

## Constraints

- **Never claim a pass you did not measure.** A skipped run is "not verified",
  not "fine".
- **Report the failures by name**, each with its earliest failing check. A bare
  rate hides which patient journey broke.
- **Cheapest gate that answers the question.** A full run costs hours and real
  money; most changes are answered by a phase or a replay.
- **Confirm the branch first.** Engine work lands on `origin/latency-eval`; a
  number from a clinic branch does not describe it.

---

## Step 1 — Confirm where you are

```bash
git fetch --all && git rev-parse --abbrev-ref HEAD && git log -1 --oneline
```

State the branch and sha in the verdict. A rate without them is unreproducible.

## Step 2 — Pick the cheapest gate that covers the change

| Gate | Command | Cost | Use when |
|---|---|---|---|
| **Corpus lint** | `python -m app.obs.regress` | free, seconds | Cheap sanity. **Not a regression gate — see caveats.** |
| **Scenario** | `python tests/auto/run_tests.py --scenario 4.1` | ~1 call | A specific reproduced defect |
| **Phase** | `python tests/auto/run_tests.py --phase 8` | ~5-10 calls | A change scoped to one journey |
| **Booking E2E** | `python tests/auto/run_tests.py --quick` | Phase 8 only | Any change touching booking |
| **Full** | `python tests/auto/run_tests.py` | 118 calls, hours | Before a deploy, or a release gate |
| **Phone path** | add `--real-calls` | real Twilio spend | Only when the defect is telephony |

Direct-WS is the default (`USE_DIRECT_WS` defaults true) — no Twilio charges.
`--real-calls` tests the full phone path and costs money; use it only when the
defect is in the phone path itself.

`--preflight` validates API keys and infrastructure without placing any call.
Run it first when a suite run fails in a way that smells like configuration.

**A mined scenario is only re-driven by the live suite.** To actually replay a
real call against current code, run it as a scenario:

```bash
python tests/auto/run_tests.py --scenario regression_<id>
```

That uses the scenario's `responses` (the caller's turns) to drive the flow.
`app.obs.regress` does not — see the caveats.

## Step 3 — Run it

Suite runs write a pair into `tests/auto/results/`: `results_<ts>.json` (full
records) and `report_<ts>.txt` (summary). The JSON is the one worth reading.

A full run averaged ~206 s per call the last time it was measured end to end —
budget hours, not minutes, and do not start one you cannot wait for.

## Step 4 — Name the failures

Do not hand back a rate alone. Run the triage collector over the results — it
gives every failure its earliest failing check and stall state:

```bash
python .claude/skills/susie-triage/scripts/collect_failures.py \
  tests/auto/results/results_<ts>.json
```

If more than about eight failures come back, stop reporting and hand the whole
run to `susie-triage` — that is clustering work, not verification work.

## Step 5 — Give the verdict

The bar is `MIN_PASS_RATE = 0.97` in `tests/auto/config.py` — 97%, which
`report.py` prints as `CLINIC READY` / `NOT READY`. On 118 scenarios that allows
**three** failures, not four.

```
VERIFY-CALL — <branch> @ <sha>
Gate:     phase 8 (5 scenarios, direct-WS)
Result:   5/5 = 100%
Bar:      97%
Verdict:  PASS
Corpus:   python -m app.obs.regress — 60/60, exit 0 (lint only, not a gate)
```

Failing shape:

```
VERIFY-CALL — latency-eval @ a063d489
Gate:     full suite (118 scenarios, direct-WS)
Result:   112/118 = 94.9%
Bar:      97%
Verdict:  FAIL — 6 failures, 3 allowed
Failures: 4.1 slot_confirmed | 4.2 slot_confirmed | 8.1 booking_confirmed
          8.2 booking_confirmed | 18.7 booking_confirmed | 19.4 booking_confirmed
Next:     susie-triage — these look like one cluster, not six
```

**Never soften a FAIL into a narrative.** "Mostly passing, just a few flaky
slot tests" is how a missed booking ships.

---

## Caveats that change the verdict

- **`app.obs.regress` cannot detect a code regression. Verified 2026-09-14.**
  It imports no `app` code at all and `check_scenario()` reads only the
  scenario's frozen `transcript` — never its `responses`. Its result is a pure
  function of the scenario files: editing a stored transcript flips it to FAIL,
  while changing engine code cannot change it at all. It is a **lint over a
  recorded corpus**, not a regression runner, despite its docstring. Never report
  a green `regress` as evidence a fix works.
- **And what it lints is currently near-vacuous.** Measured 2026-09-14: all
  **60** mined scenarios carry the identical
  placeholder `expected: {'no_technical_error': True}`. Every one was mined from
  a real call scored 1 or 2, collectively tagged `booking_error` ×47, `loop` ×47,
  `dead_end` ×45, `wrong_info` ×22, `hallucination` ×13 — and **none of those
  defects is asserted**. "OK: all 60 regression scenario(s) pass" currently means
  only that Susie never said "technical issue" in 60 recorded transcripts.
  Sharpening those `expected` blocks is outstanding work — but note that even
  sharpened, they would only assert properties of the stored text.
- **`regress.py` can only assert against transcript TEXT.** Its
  `_DETERMINISTIC_KEYS` are `no_technical_error`, `not_said`,
  `greeting_contains` / `_not_contains`, `first_susie_turn_contains` /
  `_not_contains`. It replays a stored transcript with no session state, so it
  **cannot** assert `booking_confirmed`, `selected_slot` or any flow field. The
  dominant mined defect class (`booking_error`) is therefore not expressible in
  this gate at all — it needs the live suite.
- **LLM-judged assertions are skipped offline**, reported not failed. A fix
  hinging on one is *not* proven by a green replay — say so.
- **`regress.py`'s docstring claims `run_tests.py --ci` delegates to it. There is
  no `--ci` flag** on this branch. Invoke `python -m app.obs.regress` directly.
- **Suite results are not production.** They say the code works, not that the
  clinics are healthy. For that, run `susie-triage --obs`.

## Done when

- A rate, a bar, and a verdict — in that order, with branch and sha.
- Every failure named with its earliest failing check.
- The verdict rests on a gate that actually executes engine code — a scenario,
  a phase, or the full suite. `app.obs.regress` alone is never sufficient.
