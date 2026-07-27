# Final 48 hours — sequenced by regression risk

**Written 2026-07-27 evening. Demo Wednesday 29 July.**
Supersedes the schedule in `FIX_QUEUE_PRE_DEMO.md`. Findings it acts on are in
`UK_CALL_ANALYSIS_2026-07-27.md`.

---

## The governing constraint

**A push to `latency-eval` is a live deploy, and every deploy invalidates every
call made before it.** So the scarce resource is not engineering time — it is
*validated call time*. Each deploy costs at least one call to re-establish that
the booking path still works, and we need three clean runs before freeze.

Budget: **two deploys.** Not five, not one.

The second constraint is diagnostic, and it is the reason for the batching rule
below: **if two changes ship together and the next call is worse, you cannot tell
which one did it.** Sunday night proved this — four commits shipped as one deploy,
the gate failed 0/3, and the whole evening went to bisecting instead of testing.

---

## Batching rule

> **Changes may share a deploy only if a single call can validate both, and only
> if they cannot interact.**
>
> A write-only change (nothing the caller hears) never interacts with anything, so
> it can ride along with one audible change. **Two audible changes never share a
> deploy.**

---

## Risk classification of every candidate fix

Risk here means *probability this breaks something that currently works*, not
severity of the bug it fixes.

| Fix | Touches | Caller hears a difference? | Can it break a booking? | Risk |
|---|---|---|---|---|
| **1. Capture `service` + duration in the call record** | call-record build (write path) | no | no — write-only, defensive | **NIL** |
| **2. Capture `_false_confirm_guard_fired` in obs** | call-record build (write path) | no | no | **NIL** |
| **3. DVT escalation wording** | `clinic.json` string | yes — on escalation only | no — not on the booking path | **VERY LOW** |
| **4. Slot cap (2 days, 1 time each)** | `receptionist_tools.py` tool result | yes — every booking call | **yes — feeds keypad selection and slot resolution** | **MEDIUM** |
| 5. Scripted lines → `obs_turns` | ~59 call sites in `connection.py` | no | possible double-log / ordering | **MEDIUM** — out |
| 6. F-8 silent time substitution | model behaviour + prompt | yes | yes | **HIGH** — out |
| 7. F-9 name determinism | `name_collector` state machine | yes | yes | **HIGH** — out |
| 8. F-2 keypad cluster | DTMF + collection state | yes | yes | **HIGH** — out |
| 9. F-5/F-12 screening triggers | `clinic.json` triggers | yes — screens fire more | no, but over-fire risk | **LOW-MEDIUM** — out unless slack |

**Fixes 5–9 are out.** Not because they don't matter — F-8 and F-9 are among the
most dangerous defects found — but because each is a behavioural change to a state
machine, two days before a demo, with no time to earn confidence in it. They go in
the post-demo queue with their evidence intact.

---

## Fix 4 is gated on an investigation, not assumed

The slot cap is the most valuable fix available and also the only one that can
break a booking. **Before writing a line of it, answer these three questions from
the code:**

1. Is `presentation_mode` computed **before** the `[:3]` cap at
   `receptionist_tools.py:2353`? If capping changes a `multi_day` into a
   `single_day`, the entire response shape changes and the blast radius is far
   larger than one line.
2. Does `_resolve_slot_iso` genuinely resolve a time that was **not read out**,
   via `session["available_days"]` (set at line 2296, read at line 777)? If not, a
   caller asking for an unlisted-but-real time gets refused — a **new** defect,
   and worse than the one being fixed.
3. Does the `more_times` flag still read correctly after the cap? The formatter
   uses it to decide whether to say *"I've a few others that day"*. If capping
   makes `more_times` false while times remain, she will tell callers there is
   nothing else when there is.

**If any answer is unclear, drop fix 4 and script around it** — a demo caller who
names a specific day gets a `single_day` response with far fewer options anyway.
That is the pre-agreed escape hatch, and taking it is a success of the method.

---

## Per-change test gate — every fix, no exceptions

1. **Failing test first.** It must fail before the change and pass after.
2. **Full suite, diffed against the baseline set** — not the count. Baseline is
   **95 failed / 1928 passed**, verified on `5f393f7` today, file-by-file in
   `TEST_BASELINE.md`. A new failing *file* is a stop.
3. **Commit alone.** One concern per commit, so any single fix can be reverted
   without taking its neighbours.
4. **Do not start the next fix until the current one is committed and green.**

---

## The schedule

### Tonight (Mon) — desk only, ZERO deploys

Write fixes 1, 2, 3 and the fix-4 investigation. Commit each. **Push nothing.**

Rationale: the current build has two verified UK bookings. That is the only
validated state we have. Do not disturb it in the evening with no time to recover.

Also tonight, no code required:
- Confirm **which Render service** owns `+447366263180` — two different service IDs
  appeared in the shell today (`srv-d9ac6bf…` and `srv-d56h5bm…`).
- Confirm **what hour Wednesday's demo is.** This blocks Tuesday's clean runs and
  is still unanswered.

### Tuesday 09:00–10:00 — DEPLOY 1 · the safe batch

Fixes **1 + 2 + 3**. Two write-only changes and one config string.

They may share a deploy because 1 and 2 are inaudible and cannot interact with
anything, and 3 only affects the escalation script.

**Validation — 2 calls:**

| Call | Script | Passes if |
|---|---|---|
| V1 | *"My leg's been swollen and warm for a couple of days"* | escalation says **"Symptoms like that"**, never "calf"; `arm_paths={'dvt':'arming_utterance'}` |
| V2 | plain happy-path booking, name a **specific day** | books; obs row now shows **`service` and duration**; `_false_confirm_guard_fired` present |

**Gate:** V2 must book with a real `calendar_event_id`. If it does not, **stop and
roll back `clinic.json` + the capture commits** — that is three reverts of changes
that cannot plausibly have caused it, which tells you the problem was pre-existing
and Tuesday becomes a diagnosis day, not a fix day.

**Rollback:** `git revert` the three commits individually. Nothing here is
entangled.

### Tuesday 10:30–12:00 — DEPLOY 2 · the slot cap, alone

Fix **4 only** — *if and only if* the three investigation questions above came
back clean. Nothing else ships in this deploy, so any regression has exactly one
possible cause.

**Validation — 3 calls, and all three are needed:**

| Call | Script | Passes if |
|---|---|---|
| V3 | *"as soon as possible"* | **two** options, not six |
| V4 | *"anytime next week"* | **two** options — this is the phrasing that produced **nine** |
| V5 | ask for a time she did **not** read out, but which exists | she books it — proves `available_days` resolution survived the cap |

**V5 is the regression test and the most important call of the day.** It is the
one that proves the cap did not amputate real availability.

**Gate:** all three pass, and V3/V4 book. Any failure → `git revert` fix 4, redeploy,
re-validate with one booking call, and go to the demo with the six-option readout
and a scripted caller. **That is an acceptable outcome.** A long slot list is
embarrassing; a broken booking is fatal.

### Tuesday 13:00 — FREEZE

No further pushes. Tag the commit. Record both SHAs — frozen, and the rollback
target — and put the rollback command somewhere you can reach it from your phone.

From here, **any push is an incident, not a plan.**

### Tuesday 14:00 → evening — the three clean runs

Full happy-path bookings on the frozen build, **natural delivery**, from a **UK
mobile**, following the demo script constraints below. **One of the three must be
at Wednesday's demo hour.**

Score each against the eight-point checklist in `DEMO_HANDOVER_CALL_SHEET.md`.

**Handover gate:** 3 of 3 book with a real calendar event, correct service and
duration, correct phone, and no *"I said"* from the caller.

### Tuesday evening — the fallback kit

- **Record a clean end-to-end booking call** to play if the live line dies.
- **Operator rehearsal ×2** — whoever demos makes the call themselves, twice.

### Wednesday — demo day

- One smoke call in the morning: line up, deploy green, calendar reachable.
- In pocket: fallback recording, frozen SHA, rollback command.
- **Zero changes.** The temptation will be "one more small fix". It is the single
  highest-risk move available to you.

---

## Stop rules — decided now, while calm

| Trigger | Action |
|---|---|
| A deploy's validation call fails to book | Revert that deploy immediately. Do not debug on a live branch. |
| Two consecutive calls fail for different reasons | Stop calling. The build is unstable; roll back to `5f393f7`. |
| A new failing test **file** appears | Stop. That is a real regression, not baseline drift. |
| Fix 4 investigation is ambiguous | Drop fix 4. Script around it. |
| It is past **13:00 Tuesday** and anything is unvalidated | Revert it. Freeze wins. |
| A phantom or wrong-number booking appears | Full stop, escalate — that is a do-not-hand-over condition |

---

## Known-good state

`5f393f7` — 2 verified UK bookings, correct slots, correct phone, prices correct,
DVT screen arming deterministically. **If everything goes wrong, this build can do
the demo** with a scripted caller. It is the floor, and it is not a bad floor.

---

## Demo script constraints — non-negotiable, established by today's calls

Each of these avoids a defect that is real, reproduced, and shipping unfixed.

1. **Name a specific day and time** — never "as soon as possible" or "anytime next
   week" *(F-1: six and nine options)*.
2. **Ask only for a time she actually offered** *(F-8: silent time substitution —
   ask for an unoffered time and she books a different one without saying so)*.
3. **Accept the caller-ID number. Never ask to use a different one. Never touch
   the keypad** *(F-2: 220-second dead-end, six confirmation asks, no booking)*.
4. **Give a simple name, clearly, with no "that would be…" lead-in** *(F-9:
   "Benton Rock"; spoken name ≠ stored name)*.
5. **Name one service only** *(F-021, reproducible 4/4, unfixed)*.
6. **State the clinical reason plainly** *(RS-02 deferred — reason can be
   model-supplied)*.

Whoever makes the demo call should have rehearsed these twice, and should have
this list in front of them.
