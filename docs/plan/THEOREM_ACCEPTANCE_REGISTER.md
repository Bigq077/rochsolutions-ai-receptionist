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
