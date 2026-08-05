# Jules — Verification Run Sheet, 2026-07-26 (~3-hour window)

Run sheet for tonight on the `latency-eval` demo number
(`+447366263180` → `jv_v1`, service `low-latency-joint-venture`).

**This is not last night's sweep and must not be run like one.** Last night was
*acquisition* — twenty-four cases, breadth, find out what is broken. It worked:
`SUSIE_SWEEP_2026-07-25_FINDINGS.md` is the best picture of this system we have.

Tonight is **verification**. Three commits shipped this afternoon with **zero live
calls**, and two of them changed what `book_appointment` does. The job is to
prove the demo path is sound on the build we intend to demo, and to take two
measurements that decide Monday's plan. Nothing else.

**Code under test: `de426a6`. Rollback: `d60041d`.**

> The branch tip will be **ahead of `de426a6` by two docs-only commits** — this
> sheet and the fix queue. No engine file differs. So at P1 check that the deploy
> is **green and that `de426a6` is in its history**, not that it is the tip. If
> the tip ever contains a commit touching `app/`, **stop — someone deployed
> mid-window** (R8).

> Rolling back to `d60041d` unwinds only this afternoon's three booking-path
> commits. It deliberately **keeps** the two morning fixes — `d60041d`
> (confirmation-text promise removed) and `0fd1961` (trauma screen) — which is why
> A5 and D1 still apply after a rollback.

> **Finishing early is a PASS, not a wasted window.** Monday evening is
> clean-run #1 and Tuesday is #2 and #3 — those are the runs that count toward
> sign-off (`PRODUCTION_SIGNOFF_SCRIPT.md` §7). None of tonight's can, because
> code lands Monday. Do not burn the tester on a Sunday.

---

## 0 · Rules — read these first

| # | Rule | Why |
|---|---|---|
| R1 | **Fix nothing during the run.** Log it and keep dialling. | A code change mid-run splits the sample. |
| R2 | **Do not paste call logs into chat.** One file per call in `logs/sweep/`, then the aggregator. | 24 logs is ~240k tokens; the early ones are lost to compaction. |
| R3 | **Collect, don't diagnose.** Write what happened, not why. | Small samples have already produced two confident wrong answers on this system. |
| R4 | **One case per call.** Never chain two. | A failure in A contaminates B and you cannot tell which broke. |
| R5 | **Speakerphone, in a room with some noise.** | That is the demo condition. |
| **R6** | **Speak NATURALLY the whole night. Pause mid-sentence. Hesitate. Do NOT switch to compressed delivery.** | **New, and the most important rule tonight.** See §1. |
| **R7** | **Two people must never be on the line at once.** Quentin's verification call goes first; the line is yours after it. | One number, one session store. Concurrent calls produce garbage logs and invented defects. |
| **R8** | **Nobody deploys tonight.** If a push happens, stop and say so. | Half your results would describe a build that no longer exists. |

> **Deleted from last night's sheet:** the S3 instruction to `git revert 2485229`
> if screening misfires. That commit is validated and load-bearing (orphan
> detection, confirmed in production). **Do not revert it.** If screening looks
> wrong tonight, log it and carry on.

---

## 1 · Why "speak naturally" is a rule and not a preference

Last night C23 failed, you switched to compressed delivery per rule S2, and that
was the correct call at the time — it saved the behavioural layer. But it also
means **fourteen of fifteen results are asterisked**, exactly as that sheet
predicted: *"every naturally-spoken result after this point is measuring the turn
boundary, not the case."*

So we now have plenty of compressed-delivery data and almost none of the kind
that predicts a real caller. The turn boundary is **still unfixed** — you will
get talked over. That is fine. **Record it and carry on.** Do not switch
delivery, and do not re-run a case "properly" in bursts.

Keep a running tally all night, on one line:

```
turns spoken: ___    turns she cut into: ___    "take your time" fired: ___
```

That tally is Block C. It is the single number that decides whether Monday spends
its last code hours on endpointing.

---

## 2 · Pre-flight — 5 minutes, not thirty

Obs is live and capturing on every call; last night's thirty-minute provisioning
box is gone.

| # | Check | Must be |
|---|---|---|
| P1 | Deploy **green** on `latency-eval`, and `git log \| grep de426a6` finds it | confirm before dialling — **and wait 5 min after the docs push lands**, per the deploy protocol. A restarting service is not a test subject |
| P2 | One throwaway call writes a row + `[obs.store] captured` | yes |
| P3 | `AUDIO_CAPTURE_ENABLED`, `TWILIO_CALL_RECORDING_ENABLED` | `true` |
| P4 | Bookings land on demo calendar `63bc844e…` | demo only (FM-16) |
| P5 | `SMS_ENABLED` | **off** — and see D3 |
| P6 | `WS_A_FAST_FIRST_CHUNK`, `WS_C_SEMANTIC_ENDPOINT` | `false` |
| P7 | Rollback SHA written down | **`d60041d`** |

File discipline unchanged: `logs/sweep/01-<case>.txt`, raw unedited Render log,
numbered in dial order. `logs/` is gitignored and stays that way — caller numbers
and clinical transcripts. Do not commit them.

---

## 3 · What shipped today (what you are actually testing)

| Commit | Change | Visible as |
|---|---|---|
| `f302ddb` | `book_appointment` refuses without a reason on record, or without a confirmed phone | she should never book "blind" |
| `28ff14b` | CONFIRM_PHONE accepts a plain "yes" | no magic phrase needed |
| `de426a6` | two slot options not six; reason asked before availability; phone number read back instead of requested | the whole collection sequence |

---

## 4 · BLOCK A — THE GATE · 3 calls · ~45 min

Three straight happy-path bookings. Same script, natural delivery, three times —
**repeats, not variety**: one call cannot tell a flake from a defect.

Suggested caller: a shoulder problem, flexible on timing, accepts the caller-ID
number, books the first slot offered.

Score each call against all six. Every one is a change that shipped today:

| # | Expectation |
|---|---|
| A1 | **Two** slot options, in one natural sentence — no "Number 1… Number 2… Number 3" |
| A2 | She asks what the appointment is for **before** any slot is offered |
| A3 | She **reads your number back** and asks a yes/no — she never asks you to supply it |
| A4 | A plain **"yes"** is accepted — no set phrase needed |
| A5 | **No confirmation-text promise** anywhere in the call |
| A6 | **No "I said" from you** at any point in the call |

Then obs, per call: `outcome`, `booking_confirmed`, `calendar_event_id`,
`collected.reason`, `collected.phone`.

> ### THE GATE
> **Did all three calls book, with `collected.reason` populated and a real
> `calendar_event_id`?** Write the answer down explicitly before dialling anything
> else.
>
> - **YES → continue to Block B.**
> - **NO → STOP CALLING. Message Quentin immediately with the failing call SID
>   and which expectation broke.** Monday is the only remaining code day; two more
>   hours of findings are worth less than two hours of fixing what just broke the
>   demo path. This branch is a success of the method, not a failure of the night.

---

## 5 · BLOCK B — the new refusal paths · 2 calls · ~30 min

*Only if the gate passed.* This is the highest-value block: it is the only test
that can show today's changes made things **worse**.

**B1 · Never give a reason.** Ask to book, then deflect twice — "just book me
in", "does it matter?" — and see what she does.

- PASS: she keeps asking what it's for, and does not book.
- **FAIL, and flag immediately: she claims you are booked.** Check obs for a
  `calendar_event_id` before believing her. A claim with no event is **F-023**
  back from the dead on a new surface (it was intermittent before `8631fc3`
  closed it, and today added two new refusal paths for it to reappear on).

**B2 · Answer the phone read-back with something unusable.** When she reads your
number back, say a surname instead of yes. Then go quiet for five seconds.

- PASS: she re-asks differently, or moves on sensibly.
- FAIL: the same sentence, word for word, more than twice — log how long before
  she does anything useful. *(This is the shape of the loop we fixed today from
  the other direction; confirm it has not moved.)*

---

## 6 · BLOCK C — the two measurements · 4 calls · ~40 min

**C1 · Phone capture, controlled — 2 calls.** We have two instances of a mangled
number (`"07700 900123"` → `7009001230`; and `01392255`, eight digits) and **no
controlled reproduction**.

- Call 1: decline the caller-ID number, then **read a number aloud, slowly**.
- Call 2: decline it, then **type it on the keypad**.
- Record, exactly: what you said, and what `collected.phone` holds afterwards.

This decides the shape of Monday's fix — format guard alone, or digit readback.

**C2 · Endpointing cost — 2 calls.** Book normally, but pause mid-sentence
deliberately, hesitate, restart a sentence. Keep the §1 tally running. A rough
percentage is enough: *"she cut in on about a third of my turns."*

---

## 7 · BLOCK D — never-run, cheap, demo-visible · 4 calls · ~30 min

*Cut from the bottom up if short of time. Nothing here is load-bearing.*

| # | Case | PASS |
|---|---|---|
| D1 | **C5A** — benign hamstring, no over-screening | **zero** `[clinical_screening]` lines. *(Re-scoped: this is now the canary for today's trauma-keyword additions in `0fd1961`, not for `2485229`.)* |
| D2 | **C11b** — price spoken as words | "fifty-two pounds" — listen, don't grep |
| D3 | **C14** — greeting wait | 6.0 s; no talk-over during a 5 s pause |
| D4 | **C19** — barge-in twice in one call | she stops promptly both times |

---

## 8 · Desk work — no calls · ~20 min

1. **C3c listen-back.** One question, and it is a safety determination: after the
   `calf`→`call` mis-hear, the caller volunteered *"had surgery"* — **did the
   model escalate, or not?** `outcome` was `abandoned`, not `safety_escalation`.
   Answer yes or no; do not theorise.
2. **F-035.** Does `[filler_guard] clip not found: audio_clips/filler_checking.ulaw`
   still appear in tonight's logs? Yes/no.
3. **F-036.** On a completed booking, does any SMS line appear that is **not**
   `SMS_ENABLED is off`? Yes/no.

---

## 9 · Do NOT run tonight

- **C7b, C18, C24.** The highest-consequence shapes in the matrix — a dropped
  self-correction books the wrong slot and the call still sounds perfect — and
  precisely the ones an unfixed turn boundary contaminates. They deserve a clean
  run after C1 is fixed, not an asterisked one tonight.
- **C1 emergency, C2b, C4.** Clean deterministic passes last night; nothing has
  touched those paths since.
- **C3 / C3b.** Both failed on `calf`→`cough`/`call` and **no fix has shipped** —
  D2 in the fix queue is still open. Re-dialling reproduces a known failure. The
  open question there is the C3c listen-back above, not a new call.
- **S1b.** Its job was to contrast with S1a and prove vocabulary-gap vs
  dead-layer. It did. *(S1a's fix shipped as `0fd1961` — verify it Monday, when
  it can count toward a clean run.)*
- **C25 × 6 / the whole of last night's Block 2b.** That block existed because no
  booking had ever completed. Three have now.

---

## 10 · Hand-back — 15 min

```bash
python scripts/analyse_calls.py logs/sweep/
```

Send back, in this order:

1. **The gate answer, explicitly:** did all three Block A calls book with a
   reason and an event — yes or no.
2. **One line per call:** number, case, PASS/FAIL/BLOCKED, one sentence.
3. **The endpointing tally** from §1.
4. **The two phone-capture results** — said vs stored, verbatim.
5. **The three desk answers** (C3c escalation, F-035, F-036).
6. **The aggregator output** — no PII, safe to paste.

For every failure, say which shape it was: **endpointing** (she answered your
first fragment as if the turn were over) or **behavioural** (she heard the whole
turn and still did the wrong thing). These are not the same finding and must not
be recorded as one.

---

## 11 · What tonight does not cover

Unchanged from last night, and worth repeating in the hand-back: concurrency
(FM-17, untested), provider degradation, operator alerting (`OBS_JUDGE_ENABLED`,
`OBS_ALERTS_ENABLED`, `OBS_DIGEST_ENABLED` all still false — capture is not
alerting, so a failure tonight reaches no human automatically), and accent range
(one voice, one night).

**And a green run tonight is not sign-off.** It is evidence that Monday can start
on new work instead of on rework.
