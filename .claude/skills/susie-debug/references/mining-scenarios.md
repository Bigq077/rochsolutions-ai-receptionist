# Mining a regression scenario from a real call

`app/obs/to_scenario.py` turns a stored, judged call into a committed, PII-free
scenario. This is the difference between *reconstructing* a bug from a
description and *replaying* the call that actually failed.

Available on `origin/latency-eval` (the full 22-module `app/obs/`). Not on
`jv-v1-onboarding`, which carries 2 modules.

---

## The loop

```bash
python -m app.obs.to_scenario <call_sid>      # mine it
python -m app.obs.regress                     # replay offline, free
```

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
- **`regress.py` checks only deterministic assertions.** LLM-judged ones are
  reported, not failed, and stay the live suite's job. If your fix hinges on a
  judged assertion, say plainly that the offline pass does not cover it.
- Offline replay uses no live server and no Claude call, so it is free and runs
  in CI — `python tests/auto/run_tests.py --ci` delegates to it.

## Why this matters here

`known-bugs.md` class 3 (ASK_LOCATION) is five fixes and three reverts in nine
days, all tuned without a reproduction. Class 4 (dead air) has a fix reverted
within 24 hours. Both are the same failure: changing behaviour against a
description of a call rather than the call.

A mined scenario also cannot go **stale** in the way a hand-written one can — it
is a recording, not an assumption about what the flow asks. When a hand-written
scenario asserts a question `BOOKING_FLOW` no longer contains, that is a test
bug; a mined scenario simply replays what happened.
