# Demo sweep — the calls to make, and the calls to show

**Written:** 2026-08-05 · **For:** Quentin · **Branches:** `latency-eval`, `theorem-onboarding`

Two sweeps, two purposes. **Sweep A** on `latency-eval` is what gets *shown* — it
is chosen to demonstrate breadth, and every case in it is one I can defend.
**Sweep B** on `theorem-onboarding` is the live-calendar mission: book, then move,
then cancel a real appointment in Mark's Acuity.

> **The rule for today, from the meeting:** anything this sweep breaks gets fixed
> **in this session**. Nothing is deferred to the morning of the demo. If a case
> below cannot be made to pass, it comes **out of the demo script** rather than
> being shown and talked around.

---

## 0. Pre-flight — five minutes, do not skip

### 0.1 Which number reaches which branch

| Dial | Clinic | Branch / service | Books into |
|---|---|---|---|
| **+44 7366 263180** | `jv_v1` — Joint Venture Physiotherapy | **`latency-eval`** | Google Calendar |
| **+44 7380 841468** | `theorem_v3` — Theorem Health and Wellness | **`theorem-onboarding`** | **Acuity — Mark's live calendar** |

`+447366263180` is a repurposed staging number and is **not** a patient line —
that is why it is safe to sweep hard on it. `+447380841468` **is** Theorem's live
line. Sweep B books real appointments; that is the point of it, and it is
authorised.

> ⚠️ **The number→service mapping lives in the Render dashboard, not in this
> repo.** I cannot verify it from here. Confirm it with the `[build_info]` check
> below before trusting a single result — a call that landed on the wrong service
> produces findings about the wrong code.

### 0.2 Confirm what is actually running

`/health` returns a hardcoded `1.0.0` and proves nothing. The only proof is one
line in the **Render log** at call cleanup:

```
[build_info] running build <sha>
```

| Service | Expect |
|---|---|
| `latency-eval` | `4eb1e0c` or later |
| `theorem-onboarding` | `d2a3338` or later |

Both were pushed this morning. If either shows an older sha, the deploy has not
finished and **the fixes below are not in the call you are about to make.**

Second line worth reading, once per call, at STT init:

```
[ms_stt] init — stt_variant=
```

`latency-eval` should say `u3.5-pro`. A surprise here changes how you read every
mis-heard name in the sweep.

### 0.3 What landed this morning, and what each case therefore proves

Three fixes went onto **both** branches — three commits on `latency-eval`
(`7090e4c` B-57, `3d5d0b8` B-39, `4eb1e0c` B-40/fillers), squashed into one on
`theorem-onboarding` (`d2a3338`):

| ID | Was | Now |
|---|---|---|
| `B-57` | Theorem's cancel gate could **never open** — the gate armed on the retention question's "altogether", and Theorem's prompt mandates "shall I go ahead and cancel that?" | Either wording arms it. A bare "yes" is accepted **only** against the single-action CTA, where it is unambiguous |
| `B-39` | The retention question was told it was required "EVERY TIME" — asked 3× in 27 s on one call, and once in the *same turn* as the cancellation | Bounded to **one ask per call**, and barred from the reschedule path entirely — **this is your bonus item** |
| `B-40` | `cancel_appointment` and `reschedule_appointment` had **no filler pool** — 11.1 s of dead air measured on a cancel | Both mapped to reassuring pools; "bear with me" and "just a second" removed |

**Cases A4, A5, B2 and B3 below are the acceptance tests for those three.** They
are not optional colour.

---

## Sweep A — `latency-eval`, the demo script

Six calls. A1–A3 are the ones to **show**; A4–A6 are the ones that most need to
**pass**, and A5 is worth showing too if it goes cleanly.

Persona throughout: use a **consistent name and number** across A1→A4, because
A2 and A3 depend on A1's booking existing.

---

### A1 · Book, with a clinical concern and a service question — *show this one*

The opening call. Demonstrates knowledge retrieval, screening and booking in one
pass, and it is the call that sounds most like a real patient.

> **You:** "Hi — I've had lower back pain for about three weeks now, it's worse
> in the mornings. I'm not sure whether I need a physio or something else."
>
> *(let her answer — she should ask about the concern, not jump to the diary)*
>
> **You:** "How much is an assessment, and how long does it take?"
>
> **You:** "Okay, can I book one in. Sometime next week, afternoons are better
> for me."
>
> *(she offers slots — pick one)*
>
> **You:** "That works." → give name → give number

| Watch for | Fail looks like |
|---|---|
| She engages the concern **before** the diary | Straight to "when would you like to come in?" |
| Price and duration answered from the clinic's own data | A hedge, or a wrong number |
| Phone number **read back** to you (B-46) | The number never repeated |
| Closing states day, date and time | "You're all booked" with no specifics |

**Reconcile in Google Calendar.** The event must exist, with the right name,
service and time. *"The booking exists"* is the one thing a call cannot prove
about itself.

---

### A2 · Reschedule, with a mid-flow change of mind — *show this one*

Ring back on A1's booking. This is the case that most often exposes the retention
question in the wrong place, so it is also the `B-39` acceptance test.

> **You:** "Hello again — I need to move the appointment I've just booked."
>
> *(she should look it up and confirm which one)*
>
> **You:** "Can you do the Thursday instead?"
>
> *(she offers times)*
>
> **You:** "Actually, no — make it the Friday morning if you've got anything."
>
> *(she re-offers)*
>
> **You:** "Yes, that one."

| Watch for | Fail looks like |
|---|---|
| 🔴 **She NEVER asks "would you like to reschedule, or cancel it altogether?"** | Any offer to cancel — **this is `B-39` recurring, and it is a stop** |
| The change of mind is absorbed without restarting the flow | Re-asking for your name or number |
| A filler covers the calendar write, and it is a *moving* phrase | Silence over 3 s, or "getting that booked in" |
| The closing states the **new** time and does not re-state the old one | Both times spoken, or the old one |

---

### A3 · Cancel, with a reason and a retention beat — *show this one*

> **You:** "I'm afraid I need to cancel my appointment — I'm going to be away
> for work."
>
> *(she confirms which appointment, then asks the retention question ONCE)*
>
> **You:** "No, cancel it altogether please."

| Watch for | Fail looks like |
|---|---|
| The retention question asked **exactly once** | A second or third ask — `B-39` |
| It is **not** re-spoken in the same turn as the cancellation | Question and confirmation back to back |
| A warm filler covers the write — *not* "bear with me" / "just a second" | Either banned phrase, or dead air |
| She confirms the cancellation only **after** the write succeeded | A confirmation with no `cancel_appointment` in the log |

**Reconcile:** the calendar event must be **gone**.

---

### A4 · The `B-57` acceptance test — cancel answered with a bare "yes"

The one case I would most want run before the demo, because it is the fix with
the most machinery behind it. The old behaviour was a **loop**: the caller says
they want to cancel, the gate refuses, she re-asks, they say "yes", it refuses
again. `B-44` recorded a caller stating the intent four times across 89 seconds.

> **You:** "Hi, I need to cancel my appointment."
>
> *(retention question)*
>
> **You:** "Yes please." ← **deliberately bare. This is the test.**

| Watch for | Fail looks like |
|---|---|
| It cancels, or asks **once** for clarification and then cancels | Asking twice or more — the loop is back |
| No phantom confirmation before the write | "That's cancelled" followed by another question |

> **Note on why a bare "yes" may still get one clarification here, correctly:**
> against the *retention* question ("reschedule, **or** cancel altogether?") a
> bare "yes" genuinely identifies nothing, and the fix deliberately still asks.
> Against a **single-action** CTA ("shall I go ahead and cancel that?") the "yes"
> now cancels. **One clarification is a pass. Two is a fail.**

---

### A5 · Cancel then change your mind — the guard that must NOT have loosened

`B-57` widened a **destructive** gate. This is the call that proves it did not
widen too far, and it is the one I would fail the sweep on without hesitation.

> **You:** "I want to cancel my appointment on Thursday."
>
> *(retention question)*
>
> **You:** "Actually, no — don't cancel it. Can you move it to the Friday?"

| Watch for | Fail looks like |
|---|---|
| 🔴 **Nothing is cancelled** | Any cancellation at all — **full stop, do not demo** |
| She moves into reschedule | Confusion, or re-asking whether to cancel |

**Reconcile:** the original event must **still exist** until the move, and then
exist at the new time. A caller who says "don't cancel" and loses their
appointment is the worst outcome this system can produce.

---

### A6 · Awkward turns — the robustness pass

Not for showing. For finding out whether the demo can survive a Hands On Money
attendee talking over her.

> - **Interrupt her mid-sentence** while she is offering slots, with "sorry — do
>   you do evenings?"
> - **Read your number in groups**: "oh seven five oh two … two one one … two oh
>   seven" → expect **11 unbroken digits** in the log's FINAL
> - **Self-correct**: "I want the Tues— actually, make it Wednesday"
> - **Go silent** for ~5 s after she asks a question → expect a re-ask, not dead
>   air
> - **Ask something off-script**: parking, or whether she is a real person

| Watch for | Fail looks like |
|---|---|
| Interruption absorbed, flow resumes where it was | Restarting, or losing collected details |
| Digits arrive whole | A truncated or discarded number |
| No `—` in the FINAL after a self-correction | Em-dash in the transcript, name extraction broken |
| Silence draws a re-ask within ~3 s | Nothing at all |

---

## Sweep B — `theorem-onboarding`, the live-calendar mission

Three calls on **+44 7380 841468**, in order, all on the **same** appointment.
This is Acuity and Mark's real diary.

### The complicated flow to book (B1)

Theorem's structure gives you real complexity without inventing any: **two sites**
(Alcester, Redditch) and **practitioner days** —

| | Mon | Tue | Wed | Thu | Fri |
|---|---|---|---|---|---|
| **Alcester** | Mark | Mark | Mark | Leanne | Leanne |
| **Redditch** | Leanne | — | — | Mark | — |

So *"I want Mark, at Redditch"* has **exactly one** possible day. That is the
constraint to lean on, and it exercises site selection, practitioner selection
and availability together.

> **You:** "Hi — I've got a shoulder problem, been going on a couple of months.
> I've seen Mark before, is he still there?"
>
> **You:** "I'd rather come to Redditch if that's possible."
>
> *(Mark at Redditch is Thursdays only — she should either offer a Thursday, or
> tell you Mark is at Alcester on other days and let you choose)*
>
> **You:** "Thursday's fine." → pick a slot → name → number

| Watch for | Fail looks like |
|---|---|
| She reconciles practitioner **and** site rather than picking one silently | Offering Mark at Redditch on a Tuesday |
| The site is confirmed back to you | The location never stated |
| Phone read back | Not read back |
| Closing names service, practitioner, site, day and time | A vague "you're booked in" |

**Reconcile in Acuity**, and note the appointment ID from the Render log — you
need it for B2 and B3.

### B2 · Reschedule it — **also the only live test of `T-18`**

`T-18` is the highest-value unverified item on this branch. Three separate
callers asked to move an appointment, heard *"Let's get that moved for you."*,
and then heard **nothing** — seven seconds of it; the third hung up. It was fixed
four times before it took: the first fix was defeated by three *other* prompt
blocks still teaching the flow to acknowledge and stop. **It has never been
dialled since.**

> **You:** "I need to move the appointment I just made with Mark."
>
> *(⚠️ **listen hard to this exact moment** — see the first row below)*
>
> **You:** "Have you got anything the following week?"

| Watch for | Fail looks like |
|---|---|
| 🔴 **`T-18`: the acknowledgement and the timing question arrive in the SAME turn** — *"Let's get that moved for you. Do you have a preference for when?"* | The acknowledgement alone, then silence. **That is `T-18` recurring and it is a stop** — it is the single most likely failure in this whole sweep |
| 🔴 **No "reschedule or cancel altogether?" question** — the bonus item, on the branch it was asked for | Any offer to cancel |
| A *moving* filler covers the write | Dead air, or a booking phrase |
| The old Acuity slot is freed and the new one taken | Two appointments, or the old one lingering |

The log line that names the failure:

```
[ms_watchdog] Spec W: turn asked nothing and no question is outstanding
```

If that appears after the reschedule acknowledgement, `T-18` is back.

### B3 · Cancel it

> **You:** "Actually I need to cancel it after all."
>
> *(retention question — once)*
>
> **You:** "Yes, cancel it."

| Watch for | Fail looks like |
|---|---|
| One retention ask, then it cancels | A loop — `B-57` did not deploy |
| Warm filler, no "bear with me" | Either banned phrase |
| Confirmation **after** the write | A phantom confirmation |

**Reconcile in Acuity: the appointment must be gone.** Leftover event
`1749165832` on Mark's calendar is available if you would rather run B2/B3
against something already there instead of a fresh B1 booking.

---

## Scoring from the Render log

Per call, the lines that decide a verdict:

```
[build_info] running build <sha>        # what ran — check first, every call
[ms_stt] init — stt_variant=            # which acoustic model
susie.latency turn_seq=.. ttfa_ms=..    # dead air. >3000 needs a filler; >1500 p95 is a bar miss
FINAL '<text>'                          # what she actually heard you say
tool  <name>                            # the WRITE. No line here = nothing happened
*_confirmation_required                 # a gate REFUSED a write — expect these only where intended
```

**The verdict that matters most is the pairing of the last two.** A confirmation
spoken to the caller with no corresponding `tool` line is the worst failure this
system has — the call sounds perfect and the booking never happened. Check every
"that's done" against a write.

For the three fixes specifically:

| Grep | Meaning |
|---|---|
| `cancellation_confirmation_required` | The cancel gate refused. On A4/B3 this should appear **at most once**, and never twice |
| `altogether` in a **reschedule** call | `B-39` recurring — a stop |
| `ttfa_ms` > 3000 on a cancel/reschedule turn with no filler in the preceding TTS | `B-40` not mitigated on that path |

---

## What I have not been able to prove from a desk

Stated plainly, so nothing here reads as more settled than it is:

1. **The fixes are verified by regression test and by the full suite** — 93
   failures on each branch against a 93-failure baseline captured on that same
   branch, fail-lists identical, zero new failures. Prompt hashes confirm only
   the intended clinics moved. **That is not the same as a working call**, and
   A4, A5, B2 and B3 exist because it is not.
2. **`B-40`'s underlying cause is untouched.** The ~10 s was the chunk gate
   holding output, and the fix gives those turns a filler pool rather than making
   them faster. If the filler path does not *arm* — which is what happened in the
   original sighting, because the tool call arrived at the end of generation —
   there is still nothing to play. **A cancel turn could still go quiet.** Watch
   `ttfa_ms` on A3 and B3 specifically.
3. **The number→service mapping is not in this repo.** §0.1 is my best reading;
   §0.2 is how you check it.
4. **Theorem's `theorem_v3` config is code, not `clinic.json`** — there is no
   `app/clinics/theorem_v3/` directory. Anything the sweep finds wrong in
   Theorem's *data* is an engine-file edit, not a config edit, and is therefore a
   bigger change than it sounds.
