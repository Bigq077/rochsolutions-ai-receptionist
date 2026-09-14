# Architecture — what owns what, and what points at it

Derived by reading the code on `jv-v1-onboarding` (2026-09-14). Line counts are
from that branch and will drift; the ownership boundaries drift much slower.

**Use this to localise, not to learn the system.** Read the module you land on.

---

## The call path

```
Caller
 └─ Twilio PSTN
     └─ app/routes/twilio.py            1,675   webhook, signature check, TwiML, greeting
         └─ app/media_streams/router.py   718   /ms/incoming TwiML + WS route
             └─ connection.py          11,854   WS lifecycle, turn taking, watchdogs
                 ├─ audio_in.py           128   Twilio µ-law 8k → PCM16 16k
                 ├─ stt_stream.py         743   AssemblyAI v3 streaming ASR
                 ├─ utterance_router.py   722   classify mixed/repair/context utterances
                 ├─ first_turn_extractor  303   deterministic first-utterance signals
                 ├─ policy_gate.py        161   first-turn policy decisions
                 ├─ fast_path.py          488   deterministic replies (latency win)
                 ├─ flow.py            24,820   FlowEngine — the conversation brain
                 │   ├─ name_collector.py 1,720 all four name-collection states
                 │   ├─ location_resolver  646  noisy STT → alcester|redditch
                 │   ├─ service_fit_policy 576  "do you treat this?"
                 │   ├─ authoritative_policy 288 service fit / eligibility / referral
                 │   ├─ llm_stream.py    2,187  Anthropic/OpenAI/Groq streaming
                 │   └─ app/tools/receptionist_tools.py  tool calls
                 │        └─ app/booking/booking/providers/{acuity,google_calendar}.py
                 ├─ chunker.py            470   LLM tokens → speakable TTS chunks
                 ├─ filler_guard.py       148   pre-synthesised clip on availability turns
                 ├─ tts_stream.py         642   ElevenLabs streaming TTS
                 ├─ audio_out.py          223   PCM16 16k → Twilio µ-law 8k
                 └─ session.py            603   Redis, prefix `ms_session:`
```

`turn_handler.py` is a **compatibility shim** — its docstring says so. All
conversation logic moved to `flow.py`. Do not fix behaviour there.

---

## Module ownership → symptoms that point at it

### `app/routes/twilio.py` — telephony edge
Owns: signature verification, TwiML, the greeting text, `/status` callback,
transfer TwiML, voicemail fallback.
**Points here:** call not answered; wrong greeting wording; `end_reason` wrong
in the record; transfer dials nothing; 403s from Twilio.
**Does not own:** anything after the first WS frame.

### `app/media_streams/connection.py` — the WS and the clock
Owns: WS lifecycle, audio frame pumping, turn-taking, barge-in, silence
watchdogs, the re-ask ladder, DTMF, and a large set of `_is_*` transcript
predicates (slot-selection candidates, fragments, patience, name corrections).
**Points here:** dead air; a re-ask that fires too early or not at all; barge-in
cutting Susie off; `direct_ws_session_lost`; caller audio never transcribed;
DTMF collected in the wrong state.
**Note:** it holds transcript *classification* helpers (`_is_slot_selection_candidate`,
`_is_post_slot_confirmation`, …). A turn misrouted before the flow sees it is
usually one of these, not `flow.py`.

### `app/media_streams/stt_stream.py` — ASR
Owns: the AssemblyAI v3 WS, auth, word boosts, endpointing config.
**Points here:** empty `user_text_raw` in `turn_traces`; garbage transcripts;
3006 close errors. **If `user_text_raw` is populated, it is not this module.**

### `app/media_streams/utterance_router.py` — classification only
Owns: classifying mixed/repair/context-rich utterances. Its docstring is
explicit: the LLM here **never generates caller-facing text and never picks a
state**.
**Points here:** a compound utterance ("no, and can I ask about parking?")
handled as only one of its parts.

### `app/media_streams/flow.py` — FlowEngine, the brain
Owns: `BOOKING_FLOW` and the reschedule/cancel flows, state advance, intent
detection, FAQ handling, mid-flow interrupts, slot confirmation, readback.

Key entry points (line numbers on this branch):

| Symbol | Line | Size | What it is |
|---|---|---|---|
| `BOOKING_FLOW` | 2641 | — | the step list; `flow_step` indexes it |
| `ask_current_question()` | 3785 | 2,109 | what Susie says at a state |
| `handle_transcript()` | 5894 | **15,734** | every caller utterance |
| `_detect_intent()` | 21822 | — | intent classification |
| `_handle_mid_flow_interrupt()` | 22470 | 1,059 | FAQ/tangent mid-booking |
| `_handle_slot_confirmation()` | 23637 | — | yes/no/day-escape at a slot |
| `_handle_readback_confirmation()` | 24094 | — | final booking readback |

**Points here:** a state the call cannot leave; a question asked twice; wrong
order; a slot choice not matched; intent misclassified; FAQ answered then the
booking not resumed.

**Hazard:** `handle_transcript` is one 15,734-line async method. The policy is
**freeze, don't refactor** — smallest possible diff, one reproduced defect, one
regression test. No "while I'm here" cleanup.

### `app/media_streams/name_collector.py` — all name states
Owns: `COLLECT_NAME`, `COLLECT_NAME_RETURNING`, `COLLECT_NAME_RESCHEDULE`,
`COLLECT_NAME_CANCEL`. Deliberately **one canonical module shared byte-identically
by every clinic** (ea37fbbb, 2026-07-10) — that was the fix for the same name bug
being repaired per-clinic.
**Points here:** surname dropped; a title ("Dr.") rejected; a slot word or body
part captured as a name; a correction mid-dictation lost.
**Fix it here, never in a clinic branch** — that is what the canonical module is for.

### `app/media_streams/location_resolver.py` — two-clinic tenants only
Owns: weighted resolution of noisy transcripts to a clinic.
**Points here:** wrong clinic bound; the location question asked during an FAQ;
the location ladder looping. Single-site clinics should never reach it.

### `app/tools/receptionist_tools.py` — the tool layer
Owns: availability checks, booking, rescheduling, cancellation; the bridge to
Acuity and Google Calendar.
**Points here:** Susie says "you're booked" and Acuity has nothing; wrong service
or modality booked; availability empty when the calendar has slots.
**Hazard:** 81 of its 97 `except` clauses are `except Exception` or bare. The
worst failure mode in this system lives here — *the call sounds perfect and the
booking silently never happened*. When you touch one, decide whether the failure
is recoverable; if it is not, surface it to the caller **and** to an operator.

### `app/booking/booking/providers/acuity.py` — the calendar
Owns: the Acuity API, calendar ids per practitioner/site.
**Points here:** booking rejected; slot no longer free; duration or gap wrong.

### `app/media_streams/session.py` — state
Owns: Redis session under `ms_session:` (separate from the webhook path's
`call:` prefix).
**Points here:** state lost between turns; a field set but not read; two
sessions for one call.

### `app/prompts/` — what Susie sounds like
**Points here:** banned filler words; missing empathy; spoken reasoning leaking
to the caller; over-promising.
**Never** put clinic facts here — those belong in `clinic.json`.

### `app/clinics/<id>/clinic.json` + `knowledge.md` — the tenant
**Points here:** wrong price, hours, address, services, practitioner names,
greeting wording.
If you are about to write `if clinic == "..."` in `app/`, stop — that is the bug.

---

## Two paths, not one

There is an older webhook/`<Gather>` path in `app/routes/twilio.py` and the
Media Streams WS path. The suite exercises the WS path. A fix applied to only
one of them will look correct in tests and be absent on a real call, or the
reverse. When a fix touches turn-taking or greetings, check both.
