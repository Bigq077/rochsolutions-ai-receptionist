# Mining a regression scenario from a real call

`app/obs/to_scenario.py` turns a stored, judged call into a committed, PII-free
scenario. This is the difference between *reconstructing* a bug from a
description and *replaying* the call that actually failed.

Available on `origin/latency-eval` (the full 22-module `app/obs/`). Not on
`jv-v1-onboarding`, which carries 2 modules.

---

## The loop

```bash
python -m app.obs.to_scenario <call_sid>              # mine it
python tests/auto/run_tests.py --scenario <id>        # re-drive it against the code
```

`python -m app.obs.regress` is **not** the replay step. See "What regress.py
actually does" below.

`to_scenario` writes into `tests/auto/scenarios/regressions/` by default
(`--out` to change, `--force` to overwrite).

## What the mined module contains

| Field | What it is |
|---|---|
| `responses` | the caller's turns, redacted — the inputs that re-drive the bug |
| `transcript` | the redacted transcript, replayed offline by `regress.py` |
| `expected` | a **conservative** deterministic baseline (essentially `no_technical_error`) |
| `source` | `{call_sid, quality_score, failure_tags, rubric_version}` for context |

## The one thing you must do yourself

**`expected` is a placeholder, not an assertion.** The module's own docstring is
explicit: it captures the input and the recorded failure; *it does not invent the
correct behaviour*. A mined scenario will happily pass against the broken code it
was mined from.

So: **sharpen `expected` until it fails for the reported reason, before you touch
`app/`.** That failing assertion is the whole point — it is what proves the fix
later, and what stops the bug returning.

## Requirements and limits

- **The call must have been judged**, not merely captured. `to_scenario` needs
  the Phase 3 fields; an unjudged row has no `quality_score` or `failure_tags`.
  If it is missing, run the judge over that call first.
- **PII is asserted, not assumed.** `to_scenario` redacts via `app/obs/redact.py`
  with a hard assertion that nothing survives. Do not weaken it, and do not paste
  raw transcript text into a commit message, an issue, or a report — the mined
  module is the sanctioned artefact.
- **LLM-judged assertions are the live suite's job.** If your fix hinges on one,
  say plainly that no offline check covers it.

## Why this matters here

`known-bugs.md` class 3 (ASK_LOCATION) is five fixes and three reverts in nine
days, all tuned without a reproduction. Class 4 (dead air) has a fix reverted
within 24 hours. Both are the same failure: changing behaviour against a
description of a call rather than the call.

A mined scenario also cannot go **stale** in the way a hand-written one can — it
is a recording, not an assumption about what the flow asks. When a hand-written
scenario asserts a question `BOOKING_FLOW` no longer contains, that is a test
bug; a mined scenario simply replays what happened.

## What `regress.py` actually does

Verified 2026-09-14, because the docstring oversells it.

`app/obs/regress.py` imports **no `app` code whatsoever**, and `check_scenario()`
reads only the scenario's frozen `transcript` — never its `responses`. So its
result is a pure function of the scenario files:

- editing a stored transcript flips it to FAIL (confirmed empirically);
- changing engine code **cannot change its result at all**.

It is a **lint over a recorded corpus** — useful for catching a banned phrase or
a malformed scenario, worthless as proof that a fix works. Its docstring calls
itself an "offline regression runner" and points at `run_tests.py --ci`, a flag
that does not exist on this branch.

**To actually re-drive a mined call against current code**, run it through the
live suite, which uses `responses` to drive the flow:

```bash
python tests/auto/run_tests.py --scenario <scenario id>
```
