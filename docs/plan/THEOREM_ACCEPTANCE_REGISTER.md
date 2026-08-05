# Theorem acceptance run — defect register

**Branch:** `theorem-onboarding` · **Clinic:** `theorem_v3` · **Opened:** 2026-08-04
Companion to `THEOREM_ACCEPTANCE_SUITE.md`. Findings only — fixes are batched
after the sweep so attribution survives, per the run sheet.

Build under test at open: `4dcad7d`.

---

## T-0 — "Are you a real person?" → **"Yes."**  ⚠️ OPEN, NOT FIXED

**Severity:** high — this is a disclosure failure, not a wording nit.
**Status:** logged 2026-08-04 on owner instruction, deliberately **not** fixed
during the run.

Observed, throwaway call, 21:04:39:

> Caller: *"um are you a real person"*
> Susie: *"**Yes**, I'm an AI receptionist — what can I help you with?"*

The sentence contradicts itself. The honest answer to "are you a real person"
is **no** — she is an AI receptionist. The trailing clause happens to be true,
which is what makes this easy to miss on a listen-through: the turn *sounds*
like a disclosure while its first word is a denial of one.

Why it matters more than it reads:

- A caller who hears "yes" and stops listening has been told a human is on the
  line. That is the one thing an AI receptionist must never assert.
- It is the first question a suspicious caller asks, so it lands early and
  colours the whole call.
- It is the sentence most likely to be quoted back to the clinic.

**Not yet located.** The turn was LLM-generated, so the cause is either the
`theorem_v3` prompt's identity block or the absence of one. Do not assume a
prompt line says "yes" — find the rendered instruction before writing a fix,
and render via `_build_theorem_v3()`, never `clinic.json`.

**Fix shape when we get to it:** the answer must open with the negation —
*"No — I'm Susie, {clinic}'s AI receptionist."* Assert the correction as a
required opening word, not a suggested tone.

---

## T-1 — the caller's spoken question was swallowed by the slot gate

**Severity:** high · **Status:** open

Caller asked two things at once: *"anytime next saturday if you have any
availability or friday, I don't know if you're open on saturdays."*

Susie generated the correct answer and the caller never heard it:

```
[ms_tts] pre-slot chunk suppressed — check_availability detected this turn:
  "We're not open on Saturdays, but Friday is no problem. Let m"
```

`app/media_streams/connection.py:12166-12176`. The gate drops **all** pre-slot
text once `check_availability` fires mid-stream. Its purpose is sound — stop
half-formed slot chatter reaching TTS before the real slot data — but it cannot
distinguish preamble from a direct answer to a factual question.

Will recur constantly: asking a question in the same breath as a timing
preference is ordinary caller behaviour.

---

## T-2 — one call writes two rows to Sheets, one of them wrong

**Severity:** medium · **Status:** open · **Bites at handover, not before**

```
📊 Row built — outcome=abandoned            name=None         phone=no
📊 Row built — outcome=reached_confirmation name=Quentin Rook phone=yes
```

Two independent paths each build and queue a row: `app/routes/twilio.py:492`
(the `/twilio/status` webhook, which fires first against an empty session and
therefore writes `abandoned`) and `app/media_streams/connection.py:14259`
(connection cleanup, which has the real data).

Invisible today because `SHEETS_ENABLED` is off. It is **on** at handover, and
Mark's sheet will then show every call twice, once as abandoned.

---

## T-3 — watchdog does not re-ask a request phrased as a statement

**Severity:** low · **Status:** watch, not confirmed

```
[ms_watchdog] Spec W: turn asked nothing and no question is outstanding —
  nothing to re-ask: "Thanks Quentin — if you'd like me to use the number
  you're c"
```

That turn is a request, but carries no question mark, so the watchdog saw
nothing outstanding. Caller silence there would have produced dead air with no
re-ask. Did not bite — the caller answered. Recorded as a pattern to watch
across the 20; promote only if a second instance appears.

---

## T-4 — caller-ID number confirmed without ever being spoken  ✅ FIXED

**Severity:** high · **Fixed:** 2026-08-04, before call 1, on owner instruction.

Susie offered *"if you'd like me to use the number you're calling from, just
say use this number"* and never said the digits. The caller confirmed a number
they had not heard, and it went onto the booking.

Caller ID is not reliably the caller's own number — diverted lines, office
switchboards and carrier-substituted numbers all arrive looking normal. A blind
yes writes a stranger's number to the booking, and the confirmation text and
every reminder follow it there.

Two prompt instructions were actively causing this, both now inverted:

| Was | Now |
|---|---|
| `…confirms the calling number — no readback needed.` | speak the digits when offering |
| `caller_number_spaced … ← do NOT read it back aloud` | `← SPEAK this value aloud, digit by digit` |

Plus the three worked examples, which steer harder than the rules do.

**Already correct, left alone:** keypad-entered numbers were read back on the
booking path *and* on cancel/reschedule lookups already (`U-03 REVERSED`, owner
decision 2026-08-03, `connection.py:6274`). The gap was only the caller-ID
shortcut.

### Two things this exposed, worth remembering

1. **Three of the five sites first edited were dead text.** `theorem_v3` has no
   `prompt_engine` key, so the `CALLER ID FIRST`, `Step 4b` and cancel-flow
   blocks in `susie_system_prompt.py` never render for this clinic. They were
   reverted byte-exact. The regression test asserts against the **rendered**
   prompt for exactly this reason — a source-level assertion would have passed
   while the live model saw nothing.
2. **The first draft hardcoded a real mobile** (the tester's own, lifted from
   the call log) into worked examples that render on every call — a number the
   model could have spoken onto a booking. Examples now use Ofcom's reserved
   drama range, `07700 900123`, and a test pins that.

---

## T-5 — a twenty-second monologue, which the caller interrupted

**Severity:** high · **Status:** open · **Call 2, 21:13:47**

Caller asked two short questions: *"what are your redditch opening hours and is
there parking"*. The answer ran **20.2 seconds** in one unbroken block:

```
[ms_silence] tts_finished in 20.2s: 'Redditch is open Thursdays only, nine in the morning until t'
```

Four chunks: opening hours → parking → the train station walk → *"would you
like me to put you through to Mark"* → *"or would you prefer to book at Awlstuh
instead?"*. The caller barged in at 21:14:06, eighteen seconds in, before the
last question finished playing.

The turn immediately before it ran **12.0 s** on the same pattern.

This is not a latency bug in the pipeline — the LLM answered in 3.3 s. It is an
answer-length bug: two questions were answered with five pieces of information
plus two competing offers. `docs/plan` sets the bar at no dead air over 3 s;
nothing sets a ceiling on how long Susie may hold the floor, and it shows.

The caller interrupting is the evidence. Note also that the barge-in landed in
the playback-only window (`synthesis_active=False playback_active=True`), so the
recovery path worked — the defect is that it was needed.

---

## T-6 — the staff-notify log says "sent" when SMS is suppressed

**Severity:** low (observability) · **Status:** open · **Call 2, 21:14:13**

```
[sms] SMS_ENABLED is off — outbound SMS suppressed (not sent)
[ms_conn] staff notify SMS sent → +447870166861
```

`connection.py:14330` logs success unconditionally, without checking whether
`send_sms` returned a SID. The sibling path in `smart_sms_router` gets this
right and logs `Smart SMS NOT sent … send_sms returned no SID`.

Harmless while SMS is off and everything is suppressed. At handover SMS is ON,
and then this line cannot distinguish a delivered staff alert from a silently
rejected one — which is exactly the moment someone needs to know.

---

## Confirmations from call 2

- **T-2 reproduced.** Two `Row built` lines again (21:14:16, 21:14:18), both
  `human_requested`. Second observation, same call path. Not a one-off.
- **Redditch redirect works.** `check_availability BLOCKED — location 'redditch'
  not bookable (Redditch redirect)`. The guard fired before any Acuity call.
- **Transfer path works end to end** — `transfer_to_human` → Twilio POST →
  `[realtime] transfer initiated … → +447870166861`. Mark's real mobile, as
  agreed (TRANSFER_DISABLED deliberately not set, see the run notes).
- **Benign, not a defect:** `[DIGEST] no recipient configured for theorem_v2` is
  the digest scheduler ticking for the *other* Theorem line
  (`+447366530580`, `clinic_config.py:38`). Not this call, not this clinic.

---

## ⚠️ BUILD BOUNDARY — a deploy landed mid-sweep

**21:18:56, after call 3.** `==> Deploying…` → `Your service is live 🎉`.

That was the push of `76cef3d` (the T-4 caller-ID readback fix). It means:

| Calls | Build | Notes |
|---|---|---|
| throwaway, 2, 3 | `4dcad7d` | T-4 **not** in these — the phone step still offers the number without saying it |
| 4 onward | `76cef3d` | T-4 fix live |

Do not compare a call 3 result against a call 5 result without accounting for
this. Record the boundary in the run sheet before scoring anything else.

**Also note: `ASSEMBLYAI_USE_U35` is ON.** The call 2 handshake shows
`speech_model=universal-3-5-pro`, `min_turn_silence=600`, `max_turn_silence=1280`.
The recommendation before the run was to leave it off precisely so a FAIL could
be attributed to the engine rather than to the acoustic model or the changed
endpointing thresholds. It is on, so every transcription-shaped finding in this
sweep carries that caveat. Not wrong — just no longer separable.

---

## T-7 — the name extractor took "Own" as the caller's name

**Severity:** high · **Status:** open · **Call 3, 21:16:06**

Caller said: *"and just a shockwave on its own"* — a pricing question, no name
anywhere in it.

```
[ms_conn v3] first-turn name extracted: Own
```

`connection.py:10885-10905`. A regex sweep over the utterance produced the
candidate `Own`, which was not in the `_NOT_NAMES` denylist, so it was written
to `soft_context["name"]`.

Why this is high and not cosmetic: `soft_context["name"]` is the same slot the
booking path reads — the throwaway call shows `name persisted (normal path)`
feeding straight into the read-back. A caller who asks a pricing question and
*then* books can carry a junk first name into the summary, and from there onto
a real Acuity appointment. STT has already written a wrong surname to Mark's
calendar twice; this is the same failure from the other direction, and it does
not even need STT to mishear anything.

The guard is a **denylist**, which is the wrong shape for this: it can only ever
catch the junk names someone already thought of. `Own` is a word an English
sentence produces constantly ("on its own", "my own", "own it").

---

## T-8 — TTS chunking split "wellbeing" into "well" / "Being"

**Severity:** medium · **Status:** open · **Call 3, 21:17:25**

```
[ms_tts] synthesise_chunk: … text='the idea is to promote relaxation and well'
[ms_tts] synthesise_chunk: … text="Being by working with the body's natural energy…"
```

The chunk boundary fell inside the word. The caller hears "…relaxation and
well. **Being** by working with the body's natural energy" — a full stop and a
capitalised restart mid-word. It sounds like a glitch, and on a first call it
sounds like a glitch in the clinic.

Same turn also contradicts itself: opens *"Reiki and energy healing is something
we offer"*, then *"for pricing on that one I'd need to put you through to the
team"*, then describes the treatment and its duration anyway. Answer discipline,
same family as T-5.

---

## T-3 — PROMOTED from "watch" to CONFIRMED

**Severity:** medium · **Call 3 — five instances in one call**

21:15:43, 21:16:00, 21:17:07, 21:16:55, 21:17:44 — every bare price answer:

```
[ms_watchdog] Spec W: turn asked nothing and no question is outstanding —
  nothing to re-ask: 'Prescribing is twelve pounds fifty.'
```

After a flat FAQ answer there is **no watchdog armed at all**. A caller who
goes quiet there gets unbounded dead air with no re-ask and no prompt — the
call just sits until they hang up. This call ended `outcome=abandoned`.

It did not bite visibly here because the caller kept asking questions. Five
instances in one call means it is not an edge case; it is the default state of
every FAQ turn.

---

## T-9 — Acuity calendar IDs missing for both named practitioners

**Severity:** medium · **Status:** open · **Startup, 21:18:58**

```
⚠️ CLINIC CONFIG: Acuity calendar ID missing for clinic='theorem_v3' location='mark'
⚠️ CLINIC CONFIG: Acuity calendar ID missing for clinic='theorem_v3' location='leanne'
```

Same for `theorem` and `theorem_v2`. Booking by location works (the throwaway
call reached Acuity via `appointment_type_id=acuity_15823699`), so this is not
blocking the generic path — but any flow that routes to a *named practitioner*
has no calendar to write to.

**Check this before calls 18/19/20**, which are the only ones that write for
real. If any of them names Mark or Leanne, this is where it fails.

---

## Confirmations from call 3

- **T-5 reproduced, worse.** 14.8 s (shockwave), 9.7 s (package), 7.4 s (laser),
  and a reiki answer spanning 15.1 s + 19.1 s with terminal chunk at 21:17:44.
  The caller hung up two seconds later. Six long turns in one call.
- **T-2 reproduced a third time** (21:17:51, both `abandoned`). Three for three.
- **Benign, documented in-log:** ElevenLabs `401` on `/v1/models` at prewarm.
  The warning itself explains the key lacks `models_read` and that synthesis is
  unaffected — synthesis then worked all call. Not a finding.

---

## T-10 — SMS was off because the branch inherited an eval default  ✅ FIXED

**Severity:** high · **Fixed:** 2026-08-04, `6f664a4`, mid-sweep on owner request.

Mark's line was sending **no SMS at all**. Every call logged:

```
[sms] SMS_ENABLED is off — outbound SMS suppressed (not sent)
```

which reads as correct, because that is exactly what a healthy latency-eval
branch prints. theorem-onboarding descends from latency-eval and inherited its
default-OFF — past a comment sitting in that very function reading *"DO NOT
port this default flip to main/theorem/jv live branches."*

Second instance of the same class as `TRANSFER_DISABLED`, in the opposite
direction: **theorem-onboarding's lineage is latency-eval, not main, so it
carries eval-branch defaults and lacks main's live-branch fixes.** Worth
sweeping for a third.

It also made Susie untruthful — the theorem_v3 prompt closes a cancellation
with *"Confirmation text on its way"* unconditionally, so callers were promised
a text that could not arrive. Heard on call 2.

Default now ON. Suppression is still available but must be deliberate
(`SMS_ENABLED=false`). The test pins the *direction*, not the value: unset must
send.

### Still inert after this fix — config, not code

```
owner_alerts = None      digest = None      call_overflow = None
```

| Surface | After the fix |
|---|---|
| Patient booking confirmation | ✅ works |
| Staff notice on transfer | ✅ works (`transfer_phone`) |
| Reminders | ✅ worker running, 5-min tick |
| Owner alerts to Mark (book/cancel/reschedule) | ❌ needs `operational.owner_alerts` |
| Daily digest | ❌ needs `operational.digest.email_to` |

`owner_alert.py:31` no-ops when the clinic has no `owner_alerts` block. The
main-branch commit that added Theorem owner-alerts is one of the 128 never
ported — see the branch-lineage note above.

**Belt and braces for the sweep:** set `SMS_ENABLED=true` in Render explicitly
rather than relying on the new default. If the service has an old
`SMS_ENABLED=false` sitting in its env from the latency-eval lineage, the env
var still wins and the code default changes nothing.

---

## T-10 — VERIFIED LIVE, call 4, build `cc88fdb`

```
Phone normalised: 07502211207 → +447502211207
POST https://api.twilio.com/2010-04-01/Accounts/AC.../Messages.json
Response Status Code: 201
SMS sent successfully
✅ Smart SMS sent [abandoned] → ***1207
```

First SMS this service has ever sent. Fix confirmed end to end against Twilio,
not merely in test. Note the build: `cc88fdb`, which is the register commit —
so `e0ca288` (the location-ladder tests) was not yet deployed at call 4. Tests
only; no engine difference.

---

## T-11 — "the morning of" was captured as a timing preference

**Severity:** medium · **Status:** open · **Call 4, 21:30:43**

Caller asked a *policy* question: *"what if I rearrange the morning of"* — i.e.
the morning of the appointment, meaning short notice.

```
[ms_conn v3] time_of_day_preference captured: mornings
  (from utterance 'um what if i rearrange the morning of')
```

No preference was expressed. The caller was asking what a same-day change
costs. If they had gone on to book, they would have been silently steered to
morning slots on the strength of a word lifted out of a policy question.

**Same family as T-7** ("Own" taken as a name from *"a shockwave on its own"*).
Both are deterministic extractors scraping an utterance whose intent is a
question, not an answer, and both write to state the booking flow later trusts.
Worth fixing together as one class rather than one at a time: **do not run
answer-extractors on a turn the caller framed as a question.**

---

## T-12 — every abandoned call now texts the caller

**Severity:** medium · **Status:** open, needs an owner decision · **Call 4**

The caller asked FAQs and hung up. No booking, no name, no request for
follow-up — `outcome='abandoned'` — and they were sent an SMS.

That was invisible while SMS was suppressed. Now that T-10 is fixed it is live
behaviour, and it fires on **every** abandoned call. Two consequences:

1. During the rest of this sweep, every test call that ends without a booking
   texts the tester's phone.
2. In production, a member of the public who rings, asks a price and hangs up
   receives an unsolicited text from the clinic. That is a judgement call about
   what Mark wants his clinic doing, not a bug I should decide unilaterally.

**Question for the owner:** should the abandoned-call follow-up SMS fire for a
caller who never gave a name or asked for anything? It is defensible ("sorry we
got cut off — here's how to book"), but it should be a decision, not a
side-effect of turning SMS on.

---

## T-2 — reproduced a FOURTH time, and now the two paths disagree

Call 4, 21:32:33 and 21:32:35:

```
📊 Row built — outcome=abandoned name=None phone=no  dur=137s   ← /twilio/status
📊 Row built — outcome=abandoned name=None phone=yes dur=139s   ← cleanup
```

Previously the two rows differed only in `outcome`. Here they disagree about
whether a phone number exists at all — the webhook path also logged
`📵 No phone number — skipping SMS` while the cleanup path had one and sent.

Same root cause (two builders over two different views of the session), but it
now has a second symptom: the SMS decision is taken twice against contradictory
state. With SMS live, that is the difference between a caller getting a text
and not, decided by a race.

---

## Confirmations from call 4

- **T-5 reproduced.** 11.5 s (cancellation policy), 14.2 s (waitlist), 8.8 s,
  8.7 s, 7.5 s. Fifth call in a row.
- **T-3 reproduced.** `Spec W … nothing to re-ask` on five FAQ answers.
- **Answers themselves were correct** — 24-hour notice, 75% short-notice
  charge, self-pay/Bupa, payment methods, no GP referral needed, no video
  consultations, what to wear. Content is good; it is the delivery length and
  the missing watchdog that keep failing.
- Obs judge scored this call **4** (previous calls: 3).

---

## T-13 — a medication question was answered with "which clinic?"  ✅ FIXED

**Severity:** high · **Fixed:** `6e6d7aa` · **Call 5, 21:35:49**

With the clinic question pending, the caller asked:

> *"should I take ibuprofen, ice or heat in the meantime"*

and Susie said:

> *"No worries — did you say the Awlstuh clinic? If so, just say 'use this clinic'."*

```
[ms_conn v3] Haiku unknown non-question — rung 2 biased confirm (bias=alcester):
  'um should i take ibuprofen ice or heat in the meantime'
```

The caller had to repeat themselves — *"no I said should I take ibuprofen"* —
to be heard.

**Root cause.** `_QUESTION_SIGNALS` held only wh-words. English forms yes/no
questions by inverting an auxiliary, with no question word in the sentence at
all. And there is no punctuation to fall back on: the AssemblyAI handshake sets
`format_turns=false`, so the word list is the **only** signal — a gap in it is
silent, not degraded.

Reproduced offline before touching anything:

```
False  um should i take ibuprofen ice or heat in the meantime
False  can i come in today
False  do i need a gp referral
```

Fixed by adding the inversion forms. Nine spoken clinic answers were checked to
confirm they stay non-questions — otherwise the ladder could never resolve a
clinic through this path again.

**Note on scope:** this is a mis-*trigger* of the location ladder, and the tests
added in `e0ca288` would not have caught it. They pin what the ladder does once
armed; this was about whether it should have armed. Both matter.

---

## T-14 — "yeah but" read as booking assent

**Severity:** medium · **Status:** open · **Call 5, 21:35:31**

> *"um i want yeah but i want to know how many sessions do i need just to get a
> good idea"*

```
[ms_conn] booking_flow_active = True
[ms_conn v3] booking ack detected — intent=booking, loc Q queued
```

The caller was pushing back — *"yeah but"* — and asking a question. It was read
as assent to book, which queued the clinic question and produced the collision
that became T-13 four turns later.

**Third member of the T-7 / T-11 family.** All three lift an answer out of a
turn the caller framed as a question:

| | Utterance | Taken as |
|---|---|---|
| T-7 | "a shockwave on its own" | first name = `Own` |
| T-11 | "what if I rearrange the morning of" | timing preference = mornings |
| T-14 | "yeah but I want to know how many sessions" | booking assent |

Worth one fix, not three: **do not run answer-extractors on a turn that parses
as a question.** T-13's corrected `_transcript_is_question` is now a usable
predicate for exactly that.

---

## Confirmations from call 5

- **Age gate works, including the hard edge.** *"my daughter's 12"* → minimum
  age 15, redirected. Then *"she's 15 next month"* → *"we do need patients to be
  fifteen at the time of the appointment… just give us a call then."* Correct
  on both, and the second is the one that usually goes wrong.
- **Clinical deflection is right.** Cause-of-pain and medication questions were
  both routed to the practitioner rather than answered. Good safety behaviour.
- **T-5 reproduced, worst yet.** 12.8 s, 12.4 s, 11.1 s, 13.0 s, 15.0 s, 13.6 s
  — and the caller barged in on **every single one** (barge-ins #1–#4 all
  confirmed). Six calls running. The caller is now visibly fighting for a turn.
- **Repetition.** `duplicate response discarded (matches previous)` twice;
  *"That's one for the practitioner at your appointment"* was said three times.

---

## T-4 — VERIFIED LIVE, call 6, 21:45:28

```
[ms_tts] synthesise_chunk: text='Thanks Quentin — is oh, seven, five, oh, two, two, one, one,'
[ms_watchdog] BACKSTOP armed — … 'Thanks Quentin — is 0 7 5 0 2 2 1 1 2 0 7 the best number fo'
```

Susie spoke the number back digit by digit before asking the caller to confirm
it. The caller then said "use this number" against a number they had actually
heard. Closed.

Build was `a684e40`, so **T-13 (`6e6d7aa`) was not yet deployed on this call** —
the clinic-question fix is still unverified in production.

---

## T-2 — ESCALATE: the phantom row now pages an operator

**Severity:** raised from medium to HIGH · **Fifth reproduction, call 6**

This call **succeeded**: slot chosen, name given, number confirmed, read back.
The two summary paths recorded it as:

```
📊 Row built — outcome=abandoned            name=None         phone=no   ← /twilio/status
📊 Row built — outcome=reached_confirmation name=Quentin Rook phone=yes  ← cleanup
```

Then, seconds later:

```
[obs.store] judged … score=3
SMS service initialized … POST …/Messages.json → 201
SMS sent successfully
```

That is a **second, different SMS** from the caller confirmation at 21:45:51 —
an operator alert via `_send_operator_sms` → `config.OBS_ALERT_SMS_TO`
(`app/obs/alerts.py:195`). `abandoned_call` is wired `{"severity": "medium",
"cadence": IMMEDIATE, "channels": ("sms", "slack")}` at `alerts.py:47`.

So the consequence chain is now:

> two summary builders disagree → the losing one writes `abandoned` for a call
> that reached confirmation → an IMMEDIATE operator alert fires → someone is
> paged about a successful booking.

This is no longer a duplicated spreadsheet row. `OBS_ALERTS_ENABLED` is
evidently ON in this service and `OBS_ALERT_SMS_TO` is set — **find out whose
number that is before handover.** If it is Mark's, he will be alerted on
successful calls from day one, and the alerts will be ignored within a week,
which is how a real alert gets missed.

---

## T-3 — partially self-correcting: there IS a backstop, and it worked

Call 6, 21:45:36:

```
[ms_watchdog] BACKSTOP armed — turn asked nothing ('If so, just say use this
  number.') but a question is still outstanding: 'Thanks Quentin — is 0 7 5 …'
```

`tests/regression/test_questionless_turn_backstop.py` covers this mechanism and
it fired correctly. **T-3 is narrower than first written:** the gap is only
turns where no question is outstanding *at all* — a bare FAQ answer. When a
question is genuinely pending, the backstop arms.

Downgraded to medium. Still real: five bare FAQ answers on call 3 armed nothing.

---

## Working, worth recording

- **Ambiguous relative date handled well.** *"next friday"* → Susie said
  *"Next Friday being Friday the 14th of August — do you prefer…"*. She stated
  her interpretation aloud rather than silently picking one. Today is Tue 4 Aug,
  so 7 vs 14 August was a real ambiguity and she surfaced it.
- **Service-to-assessment redirect correct.** *"I want to book acupuncture"* →
  *"Acupuncture is something Mark works with — we'd recommend starting with a
  physiotherapy assessment first."* Booked the assessment.
- **Slot DTMF map armed** (`{'1': 'one in the afternoon', '2': 'two in the
  afternoon'}`) alongside the spoken options. Voice answer resolved it.
- **Abort rule held.** Caller hung up at *"shall I go ahead and book that in?"*;
  no `book_appointment`, nothing reached Acuity.
- **Patience handling.** *"one second please"* → *"No rush at all."* and the
  clinic question was correctly suppressed rather than fired into the pause.

## Still failing

- **T-5, worst single turn of the sweep: 20.1 s** on the acupuncture answer.
  Then 13.1 s and 12.1 s. Seven calls running.
- The caller had to say *"say that again you got cut off"* — the 20.1 s answer
  was replayed in full, costing another 13.1 s.

---

## T-15 — the answer to "mornings or afternoons?" was discarded

**Severity:** high · **Status:** fix written, NOT committed, NOT deployed
**Call 7, 21:54:47**

```
Susie:  "Do you prefer mornings or afternoons?"
Caller: "more so afternoons"
log:    [ms_conn] slot fragment ignored — re-arming: 'more so afternoons'
```

The answer was dropped on the floor. Ten seconds later the watchdog fired
*"Still with you — which of those would you like?"* — pointing at the 10th/11th/
12th August days the caller had **already rejected** — and they had to say the
whole thing again:

> *"i said i i don't want to book this week i want to book the week after"*

Roughly 30 seconds and two caller repetitions lost.

**Root cause, reproduced offline:**

```
True   'more so afternoons'   ← _is_short_meaningless_fragment
True   'afternoons'
True   'mornings'
False  'afternoons please'    ← only survived because "please" is on the list
```

`_is_short_meaningless_fragment` discards any utterance of ≤3 words containing
no word from `_COMMUNICATIVE_WORDS`. That list has no time-of-day words, so the
single most likely answer to a question Susie asks in every booking was, by
construction, meaningless to her. It survived on the caller's second attempt
only because they happened to add "please".

**This is a named, recurring failure in this codebase.** The comment on
`_PURE_FILLER_TOKENS`, twenty lines below the list that caused this, says it
outright: *"a hand-maintained vocabulary sitting between the caller and what
they asked for"*, citing B-25, the step-8 reword, the timing singles and B-36
cause 1. T-15 is the fifth instance of the same shape.

Fix written (adds the time-of-day vocabulary; widening only ever sends MORE to
the LLM, which is the safe direction) with 30 tests pinning both directions —
answers survive, genuine fragments still re-arm, phone numbers and negations
still always reach the LLM. **Held uncommitted on owner instruction.**

---

## T-2 — CORRECTION to the call-6 escalation

My call-6 entry said the duplicate `abandoned` row was firing the operator SMS
via the `abandoned_call` alert. **That was wrong.**

Call 7 settles it. Both summary rows agreed this time —
`reached_confirmation name=Quentin Rook phone=yes` twice — and an operator SMS
still fired at 21:57:33, immediately after:

```
[obs.store] judged call_sid=… score=3
```

The sender is `review_alert()` (`app/obs/alerts.py:220`), *"Immediate operator
alert for a low-quality call"*, gated only on `OBS_ALERTS_ENABLED`. It is
triggered by the **judge score**, not by the abandoned row.

What that changes:

- **T-2 goes back to medium.** It is duplicate rows and occasional disagreement,
  not a false-alert generator.
- **A new concern replaces it:** every call in this sweep has scored 3 or 4, and
  every one has fired an immediate operator SMS. If that threshold ships as-is,
  Mark is texted after essentially every call. Still worth finding out whose
  number `OBS_ALERT_SMS_TO` holds — but the reason is alert volume, not
  correctness.
- T-2's own consequence is narrower than I wrote: duplicated Sheets rows, and
  on calls 4 and 6 a row that misreported the outcome.

Also note the dedup **worked** on call 7: `📩 Follow-up SMS already sent —
skipping duplicate`.

---

## Confirmations from call 7

- **T-4 held again.** *"Thanks Quentin — is oh seven five oh two, two one one,
  two o…"* — digits spoken before the confirm, second call running.
- **Correct refusals, all three:** the August bank holiday (*"Monday the 25th…
  the clinic is closed that day"*), a March booking (*"we can only book up to 30
  days ahead"*), and an unavailable time (*"Four in the afternoon isn't one I
  have available on Wednesday — I've got two o'clock or three"*). That last one
  is the important one: she refused a slot that did not exist rather than
  accepting it.
- **"could you repeat the last day that you offered"** → replayed Wednesday 19th
  correctly.
- **Abort rule held.** Hung up at *"shall I go ahead and book that in?"*; no
  `book_appointment`, nothing reached Acuity.
- **T-5 continues:** 14.5 s, 14.0 s, 12.0 s, 12.6 s. Eight calls running.
- Call duration 198 s, 22 turns — the longest of the sweep, and roughly 30 s of
  it was the T-15 recovery.

---

# CALL SUITE V2 — Part A results (build `e2a44f3`)

## A1 — PASS, with one new finding

### Verified live

| | Result |
|---|---|
| **T-0** | ✅ `"No — I'm Susie, Theorem Health's AI receptionist."` Opens with No. |
| **T-5** | ✅ The identical hours+parking question that ran **20.2 s** now runs **5.2 s**, two sentences, no train station, no stacked offers. Follow-up 4.2 s, Bupa 6.1 s. |
| **Warmth** | ✅ *"A follow-up appointment is eighty-five pounds, and that's forty minutes."* Not clipped. This was the risk in T-5 and it held. |
| **T-7 / T-11** | ✅ `soft-context extraction skipped — caller asked a question` fired on **all four** question turns. |

Build not printed in the excerpt, but proven by behaviour: `"No — I'm Susie"`
and the extraction-skip line exist only in `e2a44f3`.

---

## T-16 — the caller named the clinic and was asked which clinic

**Severity:** medium · **Status:** open · **A1, 23:15:41**

> Caller: *"what are your **redditch** opening hours and is there parking"*
> Susie: *"Is this for our Awlstuh or Redditch clinic?"*
> Caller: *"**I said** you're Redditch clinic"*

```
[ms_conn v3] FAQ clinic gate: no clinic confirmed — injecting 'Which clinic?'
  and skipping run_turn (utterance='uh what are your redditch opening hours
  and is there parking')
```

The FAQ clinic gate fires on `v3_location_confirmed` being unset, without ever
checking whether the utterance in hand already names a clinic. The caller
answered the question before it was asked and was asked anyway.

**The detector already exists and works.** Sweep call 2 logged
`inline alias detected pre-ack: redditch` from *"i'd like to book at your
redditch clinic"* — the booking-ack path scans for an inline alias. The FAQ gate
path does not.

Same family as T-13: a gate firing on a turn that had already satisfied it. The
"I said" in the caller's reply is the same tell as T-15's *"I said I don't want
to book this week"* — it is what a caller says when the system did not listen.

Cheap: reuse the existing inline-alias scan before injecting the clinic
question, and skip the gate when it hits.

---

## Confirmations from A1

- **T-12 again.** Abandoned call → SMS to the caller. Still an owner decision.
- **T-2 again**, but both rows agreed this time (`abandoned`, phone=yes) and the
  dedup worked: `📩 Follow-up SMS already sent — skipping duplicate`.
- **Operator-alert threshold observation:** this call scored **4** and fired NO
  operator SMS. Earlier calls scored 3 and did. So `review_alert` appears to
  trigger below 4, not on every call — better than feared, and it means the
  alert-volume concern is proportional to call quality rather than constant.
- **T-3 twice** (`Spec W … nothing to re-ask`) after the follow-up price and the
  Bupa answer. Known, narrow.
- The **backstop** fired correctly once, holding the outstanding clinic question
  after the parking answer.

---

# GO-LIVE RUN — Part B

## B1 — FIRST REAL BOOKING ✅ (build `6901ffb`)

```
POST https://acuityscheduling.com/api/v1/appointments  200 OK
Created booking in Acuity
{"success": true, "acuity_booking_id": "1749165832",
 "booked_slot": "Wednesday 12 August at 15:00", "location": "Alcester"}
```

`book_appointment` fired for the first time on this branch. Confirmation SMS
201. Reminders scheduled (24 h and 2 h). Row `outcome=booked`.

**Verified live in the same call:** T-13 · the two-rung keypad ladder (first
time ever — *"um the ammon clinic"* → keypad on the FIRST re-ask, no biased
confirm) · invalid-key re-prompt with the keypad staying armed · T-15 (bare
*"afternoons"*) · T-7 · T-0 · T-4.

**Standout:** the caller gave a different number by voice, was moved to the
keypad, the typed number was committed (`typed, not caller ID`), read back
digit by digit, and caller ID was then twice refused as an overwrite. That is
the exact class that put a wrong surname on Mark's calendar twice.

---

## T-17 — a dead guard injected a synthetic turn on top of a live one

**Severity:** HIGH · **Status:** open · **B2 reschedule, 00:08:43**

The reschedule collapsed. `lookup_patient` ran twice, *"I can see an
appointment on Wednesday the 12th…"* was spoken twice, *"Let me pull that up
for you now"* and *"Bear with me just a moment…"* piled in behind it, five
`stale tts_finished ignored` lines followed, and the caller hung up.

**Root cause — a guard that cannot ever be false on this clinic.**
`connection.py:10778`:

```python
if (_prev_was_loc_q
    and self.session.get("v3_location_confirmed")
    and not self.session.get("_turn_speech_emitted")):
```

`_turn_speech_emitted` is reset to `False` before every turn
(`connection.py:10218`) and set back to `True` on a normal turn in exactly one
place: `_TrackedQueue.put()` at `flow.py:3722`.

`theorem_v3` never reaches FlowEngine — `connection.py:11600`:

```python
return  # CRITICAL: do not fall through to FlowEngine path
# FlowEngine path — theorem and theorem_v2
```

So the flag is **permanently False** here and the third clause is a no-op. The
re-queue fires whenever the first two hold, regardless of whether Susie spoke.

The timestamps show it plainly: TTS synthesised at `00:08:43.639`, the code
decided "no TTS emitted this turn" at `.676` — 37 ms later.

**Fourth instance of the FlowEngine-bypass class** (see
`flowengine-is-bypassed-on-every-live-clinic`): code that reads correct but is
dead on the path that actually runs. The others were greps finding dead code;
this one is worse — a *guard* that silently always passes.

**Nothing was corrupted.** Both lookups returned the right appointment
(`1749165832`), no write was attempted. This is conversation control, not data.

**Fix shape:** the re-queue needs a real signal that the turn was silent.
Either set `_turn_speech_emitted` on the v3 TTS path too, or — simpler and
safer — do not re-queue at all when a tool call ran this turn, since a lookup
that produced a spoken result is by definition not a silent turn.

---

## T-18 — the reschedule acknowledgement is a dead end

**Severity:** medium · **Status:** open · **B2, 00:07:51**

```
tts: "Let's get that moved for you."
[ms_watchdog] Spec W: turn asked nothing and no question is outstanding —
  nothing to re-ask
```

Seven seconds of dead air. The caller had to say *"hello"* to restart the call.

This is **T-3 on a real patient path** rather than on a bare FAQ answer, which
is why it matters more than T-3's current "medium, narrow" rating suggests. The
first thing a caller hears after asking to move an appointment is a statement
with no question and no watchdog behind it.

---

## Outstanding

- **Appointment `1749165832` is still on Mark's calendar** — Wednesday 12
  August, 15:00, Alcester. The reschedule never completed and cancel never ran.
  **Remove it.**
- **Cancel remains completely untested.**
- Judge scored the reschedule call **2**, the lowest of the sweep, and fired an
  operator alert — the alerting is working as intended.
