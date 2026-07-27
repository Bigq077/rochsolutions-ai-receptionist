# Jules — Tuesday 28 July · ~2 hours · the day before the demo

**The branch is FROZEN at `0297b22`. Log everything. Fix nothing.**

That is the whole brief in one line, and it is a change from how we have worked
all weekend. You have been fixing in-window on Quentin's say-so since Saturday and
that was right at the time. It is not right today.

> **No commits. No pushes. No config edits. No "one-line" fixes.**
> If you find something, write it down with the call SID and keep going.
> If something breaks the booking path, **stop and message Quentin** — do not
> diagnose it yourself.
>
> Rollback, if it is ever needed: `2d553b6` (validated by V1/V2 last night).

---

## Why you are not being asked to hunt for defects

Your instinct will be to trawl the logs for recurring problems. That work is
already done — 27 calls analysed, findings in `UK_CALL_ANALYSIS_2026-07-27.md` and
`NAME_CAPTURE_ANALYSIS_2026-07-27.md`, each with a repro and a call SID. Re-reading
the same rows produces the same list.

And with code frozen, a fresh defect list has **negative value today**: it creates
pressure to break the freeze, which is the highest-risk move available to us.

Everything you find goes in the post-demo queue. That queue is already written and
ranked, so Thursday starts with evidence rather than a blank page.

---

## What only you can give us

Every call on this build — all fourteen — has been **one voice, one handset, one
cadence**. `DEMO_HANDOVER_CALL_SHEET.md` lists accent and voice range explicitly
as *not covered*.

**You on your own phone is new signal.** That is the gap worth two hours.

---

## Task 1 · Clean runs #2 and #3 · ~45 min

Two full happy-path bookings on the frozen build, **your voice, your handset,
natural delivery** — pause mid-sentence, hesitate, do not compress. **One of them
at the demo's time of day.**

These count toward the three-clean-run freeze gate. Your time buys the gate
directly.

Follow the demo script exactly — the point is to validate the script, not to
stress the system:

| Step | Say |
|---|---|
| open | *"Hi — can I book an appointment please?"* |
| reason | a plain complaint, one service only |
| timing | **a specific day** — never "as soon as possible" or "anytime next week" |
| slot | a time **she actually offered**, repeated back in her words |
| name | **bare, no lead-in** — *"Tom Green"*, never *"yeah, that'd be Tom Green"* |
| number | *"Yes"* — accept the caller-ID, **never touch the keypad** |
| confirm | *"Yes please"* |

Record per call: booked yes/no, how many slot options she offered, and whether you
had to say *"I said"* at any point.

---

## Task 2 · Validate the mitigations · ~30 min

Six defects are live and mitigated **by script**. Nobody has confirmed the script
actually avoids them. This is the bounded, useful version of defect-hunting.

| Check | Passes if |
|---|---|
| name a specific day | she offers **two** options, not four or six |
| ask only for an offered time | she books exactly that time |
| bare name | `collected.name` matches what you said |
| accept caller-ID | no keypad, no loop |
| one service | `service == checked_service` in obs |
| state the reason plainly | `collected.reason` is populated |

**If a mitigation does not hold, that is the most valuable thing you can find
today** — because the fix is a script change, not a code change, and we can still
make it.

---

## Task 3 · Record the fallback call · ~15 min

One clean end-to-end booking, recorded, to play if the live line dies on
Wednesday. Do it while a working build is in front of you.

---

## After every call — read the record, do not trust the audio

Two defects are **invisible to a listener** and both are now queryable:

```sql
SELECT collected->>'name'            AS stored_name,
       collected->>'service'         AS booked,
       collected->>'checked_service' AS checked,
       booking_confirmed, calendar_event_id
FROM calls ORDER BY start_utc DESC LIMIT 3;
```

- **`stored_name` ≠ what you said** — she can read the right name back and store
  the wrong one. Seen twice yesterday (`Benton Rock`, `Quinton Rock`).
- **`booked` ≠ `checked`** — that is F-021, and this is the first build where it
  can be seen without listening back. `service`/`checked_service` shipped
  yesterday for exactly this.

A call that *sounded* perfect and wrote the wrong name is a **FAIL**.

---

## What shipped since you last looked

| Commit | Change |
|---|---|
| `83699c3` | DVT escalation no longer says "calf" at a caller who said "leg" — your `29e3f9b` widened the trigger to any limb; the wording had not caught up |
| `91bb11b` | `collected` now records `service`, `checked_service`, `location` |
| `2d553b6` | Call record now carries Gate 5f state (`guards`) |
| `b9baf79` | Slot read-out capped at two options |

Two of those are worth knowing about specifically:

**The B1 slot fix never took effect, and here is why.** `de426a6` rewrote step 5
of `clinic_template_prompt`. But the slot presentation is generated by
`SLOT_FORMATTER_SYSTEM_PROMPT` on Haiku (`llm_stream.py:995`), and that prompt
assumes *"available_days (already capped to at most 3)"* — a cap that only exists
in `_check_availability_acuity`. `jv_v1` uses the generic path, which capped
nothing. The fix is now data-side.

**The "All booked" phantom was probably never real.** The obs transcript is built
from `full_reply`, which is assembled **raw** (`llm_stream.py:1109`), while Gate 5f
runs on the TTS path only (`:1497`). So the transcript shows what the model
*generated*, not what the caller *heard*. Every "All booked" quoted from a
transcript — including the 26 Jul verify call that reopened F-023 — may be an
artifact. `2d553b6` captures the guard counter so this is now decidable.

---

## Post-demo queue, ranked and ready

1. **N-2 / N-1 name capture** — spoken ≠ stored, non-deterministic, surname
   re-asked after being given (5 of 25 exchanges). See
   `NAME_CAPTURE_ANALYSIS_2026-07-27.md`.
2. **F-8 silent time substitution** — caller asks for "half past five", she books
   "five past five" without flagging it.
3. **F-2 keypad cluster** — RS-01 / RS-05 / RS-07, a 220-second dead-end.
4. **F-021 wrong service** — now measurable.
5. **F-7** — she offered a day/time she had just said was unavailable.
6. **Screening triggers** — `"twisted it"`, `"slipped"` do not arm; DVT over-fires
   on a benign ankle.
7. **`OBS_JUDGE` / `ALERTS` / `DIGEST`** — still `false`. Capture is not alerting:
   a failure reaches no human automatically.

Also: rotate the `demo_obs` and `susie-obs` passwords once the demo is done.

---

## The one rule again, because it is the whole point

**Frozen at `0297b22`. Log everything, fix nothing.** A defect found and written
down is worth a great deal on Thursday. A defect fixed today costs a validation
call, resets the clean-run count to zero, and risks the one thing that matters on
Wednesday — that a live booking call works in front of a hundred clinics.
