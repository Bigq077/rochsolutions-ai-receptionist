---
name: susie-debug
description: Take one identified Susie bug from report to proven fix. Use when a specific call went wrong, or when a triage cluster is picked up for fixing. Not for a clinic that is down right now — that is incident response, restore first.
---

# susie-debug

**One bug, one diff, one regression test.** `susie-triage` decides what to fix;
this skill fixes it and proves it.

**Not this skill:** a live clinic broken right now. Restore first, communicate
second, diagnose third, then come back here. (`engineering:incident-response` is
named in the plan for this but is **not installed**; there is no `docs/INCIDENT.md`
yet either.)

---

## Constraints — an agent that doesn't know these will violate them confidently

- **Smallest possible diff.** `flow.py` is frozen: `handle_transcript()` is a
  single 16,010-line method. Fix the reproduced defect, change nothing else. No
  restructuring, no "while I'm here" cleanup.
- **Clinic behaviour belongs in `clinic.json`**, never in engine code. If you are
  writing `if clinic == "..."` in `app/`, that is the bug, not the fix.
- **Every behavioural fix ships with a regression test** in `tests/regression/`
  that fails before and passes after.
- **No new dependencies** without asking — cold start affects first-call latency.
- **p95 turn latency under 1.5 s**; no dead air over 3 s without a filler.
- **Engine fixes land on `origin/latency-eval`** (settled 2026-09-14; ADR-002's
  `engine/converged` was never pushed). Clinic branches inherit by cherry-pick.
  See `references/gotchas.md` §3.

---

## Step 1 — Establish the source

Get the record: a triage cluster names its representative call or scenario
(`docs/triage/TRIAGE_<date>.md`); a suite failure is in
`tests/auto/results/results_<ts>.json`; a real call is a `call_sid`. Then — a
bug on one branch may not exist on another:

```bash
git fetch --all && git rev-parse --abbrev-ref HEAD
```

## Step 2 — Reproduce it as a scenario

**Do not start fixing an unreproduced bug.** This is the step history says gets
skipped: `known-bugs.md` class 3 is five fixes and three reverts in nine days,
all tuned without a reproduction.

**If the bug came from a real call, mine it — do not rewrite it from memory:**

```bash
python -m app.obs.to_scenario <call_sid>   # PII-redacted scenario from the real call
python -m app.obs.regress                  # replay offline: no server, no cost
```

The mined `expected` is a deliberately conservative placeholder. **Sharpen it
until it fails for the reported reason before you touch `app/`** — the tool
captures the input and the observed failure, not what correct behaviour is.
See `references/mining-scenarios.md` for requirements and limits.

**Otherwise** write or identify a scenario under `tests/auto/scenarios/` and run
it in direct-WS mode (free; `USE_DIRECT_WS` defaults true). If you cannot make it
fail, you do not yet understand the bug — say so rather than changing code.

## Step 3 — Localise from the earliest failing check

`references/symptom-map.md` gets you from check → subsystem;
`references/architecture.md` from subsystem → the owning module.

Read `turn_traces` first — it separates three cases a check name cannot:

| Evidence | Meaning | Goes to |
|---|---|---|
| `user_text_raw` empty | never heard | `stt_stream.py`, audio path |
| populated, `state_before == state_after` | heard, not matched | the `flow.py` matcher for that state |
| state advanced, `extracted_*` null | matched, not acted on | extractor or tool layer |

`handled_by` names the handler that ate the turn. A handler running in a state it
does not own is a bug you can name without reading any logic.

## Step 4 — Search git history before writing any code

Once localised, search for prior fixes, then read `references/known-bugs.md` for
the nine recurring classes:

```bash
git log --oneline -20 -- <path>
git log -S"<the symbol you are about to change>" --oneline
git log -i --grep="<symptom keyword>" --pretty='%ad %h %s' --date=short
git merge-base --is-ancestor <fix-commit> origin/<branch> && echo HAS || echo MISSING
```

*"This was fixed in April and regressed"* is the most valuable thing you can
learn here. Regression-by-omission is live in this repo, so check the old fix is
still present before writing a new one.

Order fixes by topology, not date — commit dates here are unreliable
(`gotchas.md` §5).

## Step 5 — Susie bug, or test bug?

Not every failure is Susie's. `tests/auto/fixer.py` already makes this call for
some shapes — read its `_SUSIE_BUG_MAP`. A scenario is **stale** when it asserts
behaviour the current `BOOKING_FLOW` no longer has. If it is a test bug, fix the
scenario and say so. Never change engine code to satisfy a stale assertion.

A mined scenario cannot be stale — it is a recording of a real call.

## Step 6 — Telephony?

If the defect is in the number, routing, transfer, caller ID or SMS delivery
rather than the conversation, it is a Twilio **configuration** problem, not an
`app/` bug. Fix it in the Twilio console or `app/routes/twilio.py`, and say
which.

## Step 7 — Fix under the constraints

Smallest diff that fixes the reproduced defect. Prefer deterministic code over a
prompt instruction — `known-bugs.md` class 9 is a prompt-only fix reverted and
replaced with deterministic strips, because the prompt did not hold. If your fix
would have to be repeated on another clinic branch it is in the wrong place:
that is what made one bug get fixed ten times.

After removing or renaming any symbol in `flow.py` or `connection.py` — five
NameError/UnboundLocalError bugs reached production this way, two booking-breaking:

```bash
grep -rn "<symbol>" app/ | grep -v __pycache__
python -c "import app.media_streams.flow, app.media_streams.connection"
```

## Step 8 — Prove it

Fastest loop first — offline, free, exits non-zero on any failed assertion, and
what `run_tests.py --ci` delegates to:

```bash
python -m app.obs.regress
```

Then: the step 2 reproduction passes; a regression test is committed (a mined
scenario counts, and beats a hand-written one — it is the real call); the
surrounding phase still passes. Trading one bug for another is the documented
failure mode in timing code.

`regress.py` checks only **deterministic** assertions; LLM-judged ones stay the
live suite's job. If the fix hinges on one, say the offline pass does not cover it.

State what you ran and what it returned. "Looks right" is not proof.

## Step 9 — Kill the class

What else has this shape? If the same matcher, handler or `except` block can fail
the same way elsewhere, either fix it in the same diff (only if it is genuinely the
same line) or hand it back as a new triage item. Do not silently leave siblings
broken.

---

## Done when

- The defect is reproduced, fixed, and proven by a test that failed before.
- The diff is small enough to read in one sitting.
- The class was considered, not just the instance.
- The branch the fix landed on was chosen deliberately and stated.
