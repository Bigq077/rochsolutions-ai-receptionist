# Media Streams Test Protocol

> Last updated: 2026-03-15
> Author: Media Streams build — Claude Sonnet 4.6
> Status: READY FOR TESTING (pending MEDIA_STREAMS_ENABLED=true on Render)

---

## Overview

50 calls total across 5 phases. All must pass before cutting Mark's number over.

The test number points to `/ms/incoming`. Mark's production number stays on
`/twilio/voice` throughout. Switching Mark's number is the final step, done only
after all 50 calls pass.

---

## Prerequisites

Before running any test calls:

- [ ] `MEDIA_STREAMS_ENABLED=true` set in Render environment variables
- [ ] `RENDER_EXTERNAL_URL` set in Render (e.g. `https://susie-ai-receptionist.onrender.com`)
- [ ] Separate Twilio test number configured to call `/ms/incoming` (POST webhook)
- [ ] Production number (`Mark's number`) unchanged — still pointing to `/twilio/voice`
- [ ] Redis available and connected (check `/health` endpoint)
- [ ] AssemblyAI API key valid (test via direct API call)
- [ ] ElevenLabs API key valid (test via direct API call)
- [ ] Anthropic API key valid
- [ ] Render logs streaming open in a second terminal for real-time monitoring
- [ ] Note start time before each phase

---

## Phase 1 — Basic Connection (5 calls)

**Goal:** Confirm the WebSocket connects and Susie speaks a greeting.

**Test procedure:**
1. Dial the test number
2. Time from ring to first audio
3. Listen for the full greeting ("Hello, this is Susie...")
4. Stay silent for 3 seconds — confirm no dead air beyond 3s (watchdog fires)
5. Hang up

**Pass criteria:**
- [ ] Caller hears greeting within 2 seconds of WebSocket connecting
- [ ] WebSocket connects cleanly (check Render logs for `[ms_conn] new WebSocket connection`)
- [ ] Session created in Redis (check `ms_session:{call_sid}` key exists)
- [ ] Session mirror-saved to `call:{call_sid}` on hang-up
- [ ] No unhandled exceptions in logs

**Fail criteria:**
- Any silence longer than 5 seconds on pickup
- `[ms_router] UNSTABLE CALL` in logs
- WebSocket close code 1011 (internal error)

**Log markers to confirm:**
```
[ms_conn] new WebSocket connection
[ms_conn] start call_sid=CA... stream_sid=MZ...
[ms_conn] greeting: "Hello, this is Susie..."
[ms_tts] synthesise_chunk: ...
[ms_conn] cleanup call_sid=CA... stable=True
[ms_conn] mirrored to call: prefix
```

---

## Phase 2 — Fast Path (10 calls)

**Goal:** Verify all 7 fast-path turn types resolve quickly without LLM involvement.

**Test 2a — Clinic selection (2 calls):**
- When Susie asks which clinic, say "Alcester" / "Redditch"
- Pass: Response within 500ms of FinalTranscript, no LLM call in logs

**Test 2b — Yes/No confirmation (2 calls):**
- When Susie asks a yes/no question, say "Yes" then "No"
- Pass: Fast-path match logged, interim phrase plays, LLM follows

**Test 2c — Full name (2 calls):**
- Say a first and last name clearly
- Pass: Name captured without LLM, session.collected.name set correctly

**Test 2d — Phone number (2 calls):**
- Give first 5 digits, then last 6 digits
- Pass: Both parts captured via fast-path, full phone assembled in session

**Test 2e — Slot selection (2 calls):**
- When slots are offered, say "the first one" / "option two"
- Pass: Fast-path slot resolution, slot stored in session.selected_slot

**Pass criteria (all fast-path tests):**
- [ ] Fast-path turns complete in under 500ms from FinalTranscript
- [ ] No full LLM call for `needs_llm_followup=False` turn types
- [ ] `fast_path_last_resolved` field populated in session after each match
- [ ] Interim phrases play correctly for `needs_llm_followup=True` types

**Fail criteria:**
- Any fast-path turn takes over 2 seconds
- LLM is called for a turn that should be fast-path only
- Session fields not populated correctly

---

## Phase 3 — Full Booking Flow (15 calls)

**Goal:** Complete a real booking end-to-end from greeting to confirmation.

**Standard booking flow:**
1. Call connects, hear greeting
2. Select clinic (Alcester or Redditch)
3. Say whether new or returning patient
4. Give full name
5. Give phone number (two-part)
6. Confirm phone number is correct
7. Hear available slots
8. Select a slot
9. Confirm the booking
10. Hear confirmation with booking details

**Per call, verify:**
- [ ] Each turn transitions correctly to the next
- [ ] Session.collected has all fields populated at end of call
- [ ] Acuity booking ID present if booking was completed
- [ ] Call summary correct in session.turns
- [ ] No duplicate questions (same question asked twice in same call)
- [ ] No wrong state (e.g. offering slots before location confirmed)

**Pass criteria:**
- All 15 bookings complete cleanly end to end
- Zero unhandled exceptions in any call
- Zero dead air events over 3 seconds (check for `[ms_watchdog]` fires — any fires are a latency warning, not an automatic fail, but investigate)
- Session state at end of call is complete and correct

**Fail criteria:**
- Booking flow gets stuck in a loop
- Wrong slots offered (next-week slots when this week is available)
- Session corrupted mid-call (e.g. location resets)
- Any unhandled exception
- Dead air > 5 seconds at any point

---

## Phase 4 — Edge Cases (10 calls)

**Goal:** Verify graceful handling of caller behaviour outside the happy path.

**Test 4a — Clinic correction (2 calls):**
- Say "Alcester", then when asked to confirm say "no, Redditch"
- Expected: Susie re-asks clinic selection, accepts correction
- Pass: Final session has correct clinic

**Test 4b — Next-week availability (2 calls):**
- Decline all slots offered for this week, say you're available next week
- Expected: Susie offers only next-week slots
- Pass: No this-week slots appear after next-week is requested

**Test 4c — Mid-call silence (2 calls):**
- Stay completely silent for 5 seconds mid-call (after a question)
- Expected: Watchdog fires bridge phrase at 3s, re-ask fires at 5s
- Pass: Hear "Just bear with me..." then "Sorry about that — [question]"
- Log: `[ms_watchdog] dead air` then `[ms_reask] re-ask #1`

**Test 4d — Off-script question (2 calls):**
- Ask an unexpected question mid-booking (e.g. "Do you have parking?")
- Expected: Susie answers naturally via LLM, then re-prompts for the booking
- Pass: No dead air, booking flow resumes correctly after the question

**Test 4e — Unclear name (2 calls):**
- Give an unclear name that the fast-path won't match
- Expected: LLM asks for clarification, second attempt captured correctly
- Pass: Name correctly recorded after the second attempt

**Pass criteria (all edge cases):**
- [ ] All 10 scenarios handled gracefully with no dead air
- [ ] No wrong slot offered after correction
- [ ] Re-ask fires correctly after silence (check log markers)
- [ ] Unexpected question handled without crashing

**Fail criteria:**
- Dead air (no audio for > 5s) on any edge case
- Wrong slot offered after caller corrects clinic choice
- Session corrupted by the edge case

---

## Phase 5 — Stress Test (10 calls)

**Goal:** Verify session isolation and no data bleed between consecutive calls.

**Procedure:**
1. Make 10 back-to-back calls with less than 5 seconds between each
2. Each call: go through 2-3 turns, then hang up
3. After all 10, check Redis for the 10 `ms_session:` keys

**Per call, verify after all 10 complete:**
- [ ] Each `call_sid` has its own session key
- [ ] No session contains data from a different call
- [ ] `twilio_from` in each session matches the correct call
- [ ] `conversation_history` in each session is self-contained
- [ ] `call_sid` in session matches the key it's stored under

**Pass criteria:**
- All 10 sessions are distinct and self-contained
- No data from call N appears in call N+1's session
- Zero exceptions in any of the 10 calls

**Fail criteria:**
- Any session key missing from Redis after the calls
- Data from one call (name, phone, clinic) appearing in another session
- Any unhandled exception

---

## Cut-over Criteria

All 50 calls must complete with:

- [ ] Zero unhandled exceptions logged
- [ ] Zero dead air events over 3 seconds (watchdog fires are acceptable at 3s — these are latency events, not failures)
- [ ] Zero wrong slots offered
- [ ] Zero duplicate questions in the same call
- [ ] Latency on first audio under 2 seconds on 45 of 50 calls (90%)
- [ ] `[ms_conn] call reached stable state` logged on all 50 calls
- [ ] All 50 bookings (Phase 3) show correct data in Acuity

---

## How to Cut Mark's Number Over

Only when all 50 calls pass:

1. Log into the Twilio console
2. Find Mark's number (the production Theorem Health number)
3. Change the **Webhook URL (POST)** from:
   `https://susie-ai-receptionist.onrender.com/twilio/voice`
   to:
   `https://susie-ai-receptionist.onrender.com/ms/incoming`
4. Save
5. Make one live test call to Mark's number to confirm greeting plays
6. Monitor Render logs for the first 5 live calls

**Rollback:** If anything goes wrong, change the webhook URL back to `/twilio/voice`. The legacy system is untouched and instantly available.

---

## Environment Variable Summary

| Variable | Test value | Production value |
|----------|-----------|-----------------|
| `MEDIA_STREAMS_ENABLED` | `true` | `true` (after cut-over) |
| `RENDER_EXTERNAL_URL` | auto-set by Render | auto-set by Render |
| `ASSEMBLYAI_USE_V2` | `false` (use v3) | `false` |
| All other vars | unchanged from current | unchanged |

**Default is safe:** `MEDIA_STREAMS_ENABLED` defaults to `false` in `.env`, so the new system is off until deliberately enabled on Render. The legacy system runs normally at all times.

---

## Monitoring During Tests

Watch these log patterns in Render:

| Log pattern | Meaning |
|------------|---------|
| `[ms_conn] call reached stable state` | First complete cycle done — call is healthy |
| `[ms_router] UNSTABLE CALL` | Pipeline failed before first turn — investigate immediately |
| `[ms_watchdog] dead air` | LLM took > 3s — note the silence duration |
| `[ms_reask] re-ask #1` | Caller silent after question — re-ask fired |
| `[ms_reask] max re-asks` | 2 re-asks failed — transfer offered |
| `[ms_stt] reconnecting` | AssemblyAI disconnect — should self-heal |
| `[ms_tts] ElevenLabs error` | TTS API error — note frequency |
| `[ms_conn] LLM turn error` | Claude error — GPT fallback should activate |
