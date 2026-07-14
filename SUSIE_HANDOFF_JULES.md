# Susie — Engineering Handoff (for Jules)

**Purpose.** You're taking over Susie (the Joint Venture Physiotherapy AI phone
receptionist) for a ~60-call battle-hardening campaign before we ship the template to
~200 clinics. You will **test and fix**. This doc is everything you need to understand
the system, run and redeploy the eval safely, read the latency logs, and land fixes
without breaking anything. Read it once end-to-end before your first call.

Companion doc: **`SUSIE_BATTLE_HARDENING_PLAYBOOK.md`** — the call scripts, scenario
matrix, results tracker and defect tracker. This doc is the "how it works"; that one is
the "what to run each day".

---

## 0. TL;DR / ground rules

- **Branch you work on:** `latency-eval`. All your fixes land here. **Never touch the
  live JV line** (`+44 7367 002651`) or the `jv-v1-onboarding`/`main` branches.
- **Where you test:** the isolated eval — Render service `low-latency-joint-venture`
  + its own Twilio number. SMS is off, Google Sheets is off, and per-turn latency
  instrumentation (`[LAT]`) is on. Nothing you do here reaches a real patient.
- **Deploys are manual.** autoDeploy is OFF. After you push a fix you must click
  Manual Deploy in Render. **Never redeploy while a call is in progress.**
- **The #1 bug class** is Theorem-template leakage (see §3). Any mention of
  Alcester/Redditch/Mark/Acuity/Theorem prices on a JV call is an automatic fail.
- **Don't fix mid-run.** Batch a block of calls, log every defect, then fix. A fix
  changes behaviour for every subsequent call and muddies the run.

---

## 1. What Susie is + the pipeline

Susie answers a phone call and behaves as a clinic receptionist: answers FAQs, books/
reschedules/cancels appointments, handles emergencies safely, and transfers to a human
when needed. It's a real-time, full-duplex voice loop:

```
Caller ──Twilio Media Stream (μ-law 8k → PCM16 16k)──▶  app/media_streams/
   ▲                                                     │
   │                                                     ▼
   │                              stt_stream.py  ── AssemblyAI v3 streaming STT
   │                                                     │  (partials = barge-in,
   │                                                     │   final = end_of_turn)
   │                                                     ▼
   │                              connection.py  ── ORCHESTRATOR (turn-taking,
   │                                                     │   watchdog, DTMF, flows)
   │                                                     ▼
   │                              llm_stream.py  ── Claude (Sonnet-4-6 primary,
   │                                                     │   Haiku-4-5 for fast/slot turns)
   │                                                     │   + tools (check_availability,
   │                                                     │   get_clinic_info, book, …)
   │                                                     ▼
   │                              chunker.py  ── splits token stream into speakable
   │                                                     │   TTS chunks at sentence
   │                                                     │   boundaries
   │                                                     ▼
   └──────Twilio◀── tts_stream.py ── ElevenLabs (eleven_flash_v2_5) streaming TTS
```

Everything is `asyncio`, single-threaded, one task set per call. Turns are strictly
sequential (a barge-in cancels the current turn before the next dispatches).

---

## 2. Code map (where things live)

All paths under `app/` unless noted. The two big files (`connection.py` ~12k lines,
`flow.py` ~20k lines) you navigate by grep, not by reading top-to-bottom.

> **Line numbers everywhere in this doc are approximate anchors, not exact.** They came
> from a code survey and the files change. Always **grep the described symbol/string**
> (e.g. `backfill_surname`, `_is_clinic_own_number`, `"non-scheduling single word"`) rather
> than jumping to a line. If an anchor doesn't match, the symbol is what's authoritative.

| Area | File | What it owns |
|---|---|---|
| **Orchestrator** | `media_streams/connection.py` | The whole live call: turn dispatch, barge-in, the silence **watchdog** (re-ask ladder), **DTMF** keypad handling, flow routing, name/phone capture wiring, end-of-call notifications. |
| **Flow states** | `media_streams/session.py` | `CallState` enum — GREETING → COLLECT_REASON → … → CONFIRM_BOOKING, plus reschedule/cancel/FAQ states. Session dict schema. |
| **STT** | `media_streams/stt_stream.py` | AssemblyAI v3 websocket, partial/final handling, reconnect, word-boost list. |
| **LLM** | `media_streams/llm_stream.py` | Claude streaming, model selection, tool loop, **booking-integrity guards** (§8 B-series), gate5 sanitiser hooks. |
| **Chunker** | `media_streams/chunker.py` | Token→TTS-chunk boundary detection (`MIN_WORDS=15`). |
| **TTS** | `media_streams/tts_stream.py` | ElevenLabs streaming, currency + pronunciation substitutions (§8 Q-series). |
| **Reasoning scrubber** | `media_streams/turn_handler.py` | Gate 5 — strips chain-of-thought / internal narration before it reaches TTS. |
| **Tools** | `tools/receptionist_tools.py` | Tool schemas + exec: `check_availability`, `get_clinic_info`, `transfer_to_human`, `add_to_waitlist`, `book_appointment`, `log_call_outcome`. |
| **Slots** | `tools/slots.py` | Candidate-slot generation, busy-block filtering, spoken formatting. |
| **Name capture** | `name_capture.py` | Pure functions: extract first/surname, particle handling, back-fill straggler surname. |
| **Config / dials** | `media_streams/config.py` | Every tunable: STT `min_turn_silence`, chunker words, models, filler phrases, watchdog timings, injected prompt rules. |
| **Notifications** | `notifications/*` | End-of-call patient SMS, owner alerts, digest email, outcome classification. |
| **Clinic data** | `clinics/jv_v1/clinic.json` + `knowledge.md` | JV's services, pricing, hours, policies, personas, emergency text. **This is what makes it "JV" vs "Theorem".** |

---

## 3. Multi-tenant model + the #1 risk (read twice)

This is **one shared codebase** serving four clinics: `demo`, `jv_v1`, `theorem`,
`vital_edge`. A lot of the Python was written **Theorem-first and is Theorem-hardcoded**:

- `config.py CLINIC_CONFIG` and much of the injected prompt scaffolding is Theorem.
- `tools/receptionist_tools.py:882-906` — the `check_availability` tool schema hardcodes
  `service='physiotherapy assessment'` and a `location` enum of `["alcester","redditch"]`.
- Watchdog/DTMF copy and the multi-clinic "which clinic?" machinery mention Alcester/
  Redditch/Mark.

**`jv_v1` overrides all of this at runtime** via `clinics/jv_v1/clinic.json`. JV is
**single-site** (Bolton, practitioner **Marcus**), so the two-clinic machinery must stay
**dormant**.

> **⛔ THE #1 FAILURE MODE:** any Theorem/Alcester ("Awlstuh")/Redditch/Mark/Leanne/
> Acuity wording, any Theorem price, or any "which clinic?" / "Alcester or Redditch?"
> question on a JV call. This is an automatic fail on **every** scenario. If you see it,
> the leak is almost always a Theorem default not being overridden by clinic.json.

**Template model:** new clinics are onboarded by writing a `clinic.json` (+ `knowledge.md`)
only — the engine is frozen. So a bug you fix in shared code helps all 200 clinics; a
Theorem-hardcoded value that leaks is a latent bug in the template for every single-site
clinic. When you fix leakage, fix it at the source (make the code read clinic.json), not
by patching JV's copy.

**JV authoritative facts** (from `clinic.json`, use these to judge pass/fail — never
invent a value):
- Greeting (verbatim): *"Hi there, I'm Susie, Joint Venture Physiotherapy's AI
  receptionist — how can I help you today?"*
- Emergency (verbatim): *"If you are experiencing a medical emergency, please hang up and
  call 999 immediately, or go to your nearest A&E."*
- MSK initial assessment **£52 / 40 min**; follow-up **£46 / 30 min**; virtual **£40**;
  acupuncture **£48** in-clinic (30 min) / **£70** home / **6× £250** package; sports
  massage **£40**/30 or **£55**/60; neuro initial **£80**/**£70** remote, follow-up
  **£65**/**£60**; outdoor rehab **£55**/45; home visit MSK **£80** / acu **£70**.
- Corticosteroid injections = **coming soon, never book**.
- Address: **Flexspace Bolton (Lythgoe House), Manchester Road, Bolton, BL3 2NZ**; free
  24/7 parking; access code → top keypad → waiting area; wheelchair accessible.
- Hours: evenings Mon–Fri + Saturday morning (last appt 8:30pm weekdays / 7:30pm Fri /
  1:30pm Sat; Sun closed). Bank holidays = defer to Marcus.
- Practitioner: **Marcus** (sole practitioner; Masters in Advanced Clinical Practice, ex-
  Hull Kingston Rovers). Transfer/owner-alert number **+447586605462**.

---

## 4. Environment & isolation

The eval is deliberately safe to hammer.

- **Dial (test line):** **`+44 7366 263180`** — the isolated eval number (a repurposed
  staging line, not patient-facing). **This is the only number you call.**
- **Never dial** the live JV patient line `+44 7367 002651`.

| Env var | Value on eval | Meaning |
|---|---|---|
| `LATENCY_TIMING` | `true` | Emits `[LAT]`/`[LAT-EP]` per turn. Keep on all campaign. |
| `SMS_ENABLED` | unset/false | No outbound SMS. Keep off. |
| `SHEETS_ENABLED` | unset/false | No Google Sheets writes (code-gated). Keep off. |
| `WS_A_FAST_FIRST_CHUNK` | **off** | Shelved latency lever (null result). **Confirm it's OFF before Day 1** — it was flipped on for an earlier A/B. |
| `MEDIA_STREAMS_CLINIC_ID` | `jv_v1` | Routes the eval to JV. |

> **WS-C latency lever — read before you plan the A/B.** Only **Phase 1 (measurement)** is
> shipped: it records `endpoint_wait_ms` + `[LAT-EP]` cutoffs. **Phase 2 (the actual
> semantic endpointing) is NOT built** — there is no env flag today that changes turn-taking
> behaviour. So you cannot "flip a flag and A/B" yet: you must first **build Phase 2** per
> `LATENCY_WS-C_MEASUREMENT_AND_PLAN.md`, then A/B. When you build it, gate it behind the env
> name `latency_timing.py:44` already maps to `flags=C` — **`WS_C_SEMANTIC_ENDPOINT`** — so
> the `[LAT]` tag lights up (the WS-C plan doc calls it `WS_C_PHASE_ENDPOINT`; pick one name
> and reconcile). Until Phase 2 exists, **every turn is baseline (`flags=-`)** and the only
> WS-C data you're collecting is the Phase-1 endpoint baseline.

**Redeploy:** Render → `low-latency-joint-venture` → **Manual Deploy → Deploy latest
commit**. autoDeploy is OFF, so a `git push` alone does nothing until you deploy.
**Never redeploy mid-call** — you'll cut off an in-flight turn (we saw this distort a
run: the worker restarts and the current turn spikes).

**Isolation guarantee:** the eval uses its own Twilio number, SMS off, Sheets off. The
live JV patient line `+44 7367 002651` and the `jv-v1-onboarding`/`main` branches are
never touched by your work. (You may still see a cosmetic `call-summary row queued to
Sheets` log line — the write is suppressed; ignore it.)

---

## 5. Dev workflow (do this exactly)

1. Clone the repo (`github.com/Bigq077/rochsolutions-ai-receptionist`) somewhere on your
   own machine and check out the branch: `git checkout latency-eval`. (Don't rely on any
   pre-existing worktree path from another machine — it won't exist on yours.)
2. Confirm you're on `latency-eval`: `git branch --show-current`.
3. Make the fix. Keep diffs tight and additive.
4. **EOL footgun — this repo will corrupt your commit if you get it wrong.** The repo is
   `autocrlf=true` with no `.gitattributes`, so working-tree files may be CRLF while the
   stored blob is LF (or, for a few files, genuinely CRLF). Before staging **check how the
   HEAD blob is stored**:
   ```
   git show HEAD:app/media_streams/<file> | python -c "import sys;d=sys.stdin.buffer.read();print('CRLF' if b'\r\n' in d else 'LF')"
   ```
   - **LF blob** (most files, incl. `latency_timing.py`, `stt_stream.py`, `chunker.py`,
     `config.py`, `llm_stream.py`, and the `.md`/`.py` docs): stage with **default**
     `git add`.
   - **CRLF blob** (notably `connection.py`): stage with **`git -c core.autocrlf=false add`**.
   - Then **always** `git diff --cached --stat` — if you see a whole-file churn (e.g.
     "300 insertions, 290 deletions" on a 5-line change) you used the wrong one. Unstage
     and switch.
5. Commit, then **push immediately**: `git push origin latency-eval`.
6. Manual-deploy on Render, confirm it's live, then resume calling.

---

## 6. The latency work (context you inherit)

We measured the real anatomy of one turn (caller stops speaking → first audio):

```
[caller stops]  ─ ~600ms endpoint silence ─▶ [dispatch] ─ ~1225ms llm_ttft
                                                          ─ ~724ms  chunk_gate
                                                          ─ ~121ms  tts_first_byte  ─▶ [first audio]
```
True voice-to-voice ≈ **2.65s** (≈600 endpoint + ≈2050 TTFA).

Three latency levers were planned; here's where they landed (full write-ups in the
`LATENCY_*.md` files):
- **WS-A (chunk gate)** — `LATENCY_WS-A_RESULT.md`. **NULL result, shelved.** chunk_gate
  is floored by first-sentence generation time, so the word-gate barely helps. Flag stays
  OFF. Don't reopen this.
- **WS-B (streaming TTS)** — **skip.** `tts_first_byte` is already ~121ms; nothing to win.
- **WS-C (endpointing)** — `LATENCY_WS-C_MEASUREMENT_AND_PLAN.md`. **The live lever.** The
  ~600ms silence floor is the only large attackable slice, AND it's the shared root of the
  "didn't understand me" clipping. **Phase-1 measurement is shipped; Phase 2 (the actual
  semantic endpointer) is NOT built yet** — building it (a phase-aware config change) is the
  main latency task in this campaign. See the §4 box for the flag-name reconciliation before
  you A/B.
- **`llm_ttft` (~1225ms, the biggest slice)** is already prompt-cached (`llm_stream.py:443`
  two-block caching) — near its floor for Sonnet+tools. Not a cheap win.
- **Biggest real lever = response length.** Susie's turns run 10–17s on slot lists and FAQ
  answers; that wastes time *and* causes the echo-clipping (a caller answers over her and
  only the tail is transcribed). Shortening responses beats every latency lever on both
  speed and comprehension — but it's a prompt change, out of scope until after the meeting.

### The `LATENCY_*.md` documents — what they are & reading order

You'll live in these. Read them in this order:

| # | Document | What it is | Read it… |
|---|---|---|---|
| 1 | `LATENCY_SIDE_BRANCH_EVAL_PLAN.md` | The master plan: isolation rules, the 3 levers, the "measure first" philosophy. | First — the *why*. |
| 2 | `LATENCY_BASELINE_LOCKED.md` | The locked baseline numbers (n=28) any lever is judged against. | Second — the *target*. |
| 3 | **`LATENCY_WS-C_MEASUREMENT_AND_PLAN.md`** | **Your main doc.** Phase-1 endpoint measurement (shipped) + the Phase-2 phase-aware endpointing plan you're building. | Third — and keep open while you work. |
| 4 | `LATENCY_WS-A_RESULT.md` | Why WS-A was a null result. | To avoid re-running a dead lever. |
| 5 | `LATENCY_MEASUREMENT_SPEC.md`, `LATENCY_INSTRUMENTATION_WIRING.md`, `LATENCY_HARNESS_SETUP.md` | How the `[LAT]` harness is designed and wired (the 6 timestamps, the `TurnTiming` record). | When you touch the instrumentation itself. |
| 6 | `LATENCY_WS-A_CHUNK_GATE_SPEC.md` + `_PSEUDOCODE.md` | The WS-A design (shelved; historical). | Only for context. |
| — | `lat_parse.py` | The offline analyser you run on grepped `[LAT]`/`[LAT-EP]`. | Every measurement. |
| — | `lat_baseline_29turns.txt`, `lat_wsA_ON_27turns.txt` | Raw data behind the baseline and the WS-A null — reproduce with `lat_parse.py`. | To sanity-check the numbers. |

### Where the latency work is right now — YOU ARE HERE

| Item | State |
|---|---|
| Turn anatomy measured, baseline locked | ✅ done (`LATENCY_BASELINE_LOCKED.md`) |
| WS-A (chunk gate) | ✅ tried → **null → shelved**, flag OFF. Don't reopen. |
| WS-B (streaming TTS) | ✅ decided **skip** (ceiling too low) |
| WS-C **Phase 1** — endpoint + cutoff instrumentation | ✅ **shipped** (`e7f64ff`) — but **not yet measured on calls**. Deploying the code ≠ having the baseline. |
| WS-C **Phase 2** — the actual semantic endpointing | ⬜ **NOT started** ← the frontier; this is your main build. |
| Response length (biggest real lever) | ⬜ deferred — prompt change, post-meeting. |

**Your next three moves, in order:**
1. **Capture the Phase-1 endpoint baseline** (nobody has yet). Redeploy the eval on the
   latest `latency-eval` commit, make ~30 `path=llm` turns, `grep [LAT]/[LAT-EP] | python
   lat_parse.py`, and record the **WS-C ENDPOINT** block — `endpoint_wait_ms` p50/p90 and the
   cutoff rate **per capture_phase**. That's the number Phase 2 must beat, and the cutoff
   rate it must not raise.
2. **Build WS-C Phase 2** per `LATENCY_WS-C_MEASUREMENT_AND_PLAN.md` §3 — turn on the dormant
   AssemblyAI v3 semantic endpointer (a config change, not a rebuild), **Approach A** (single
   semantic profile) first, gated behind `WS_C_SEMANTIC_ENDPOINT` so `[LAT]` shows `flags=C`.
   ⚠ Open question the plan flags: does v3 support **mid-stream config update**? Verify
   against current AssemblyAI docs before attempting the phase-aware **Approach B**.
3. **A/B it** — baseline vs `flags=C`, identical scripts. **Hard gate: zero new mid-capture
   cutoffs in name/phone** (an elderly caller reading a number must never be clipped to save
   300ms). If cutoffs rise, that arm reverts — it's config.

If you want the biggest possible win and have appetite for a prompt change, **response
length** is the real ceiling (see the bullet above) — but clear that with Quentin first;
it's behavioural, not a latency-eval config.

---

## 7. Reading the latency logs

Every turn emits one PII-free `[LAT]` line on the `susie.latency` logger. Fields:
`turn_seq · path · outcome · ttfa_ms (perceived) · content_ttfa_ms · ep_dispatch_ms ·
llm_ttft_ms · chunk_gate_ms · tts_first_byte_ms · audio_wire_ms · flags · model ·
capture_phase · endpoint_wait_ms`. `-1` means "not measured this turn". `flags=A|C`
records which latency levers are ON (so one log file can hold an A/B). `endpoint_wait_ms`
is the WS-C pre-dispatch silence.

The cutoff detector emits a sibling `[LAT-EP] ep_cutoff turn_seq=N reason=… capture_phase=…`
when a caller turn opens with a correction lead ("I said…", "I told you…") — advisory
signal that the previous turn's capture was clipped.

**To analyse:** grep both tags out of the Render logs (they're PII-free) and run the
parser:
```
grep -E "\[LAT" render.log | python lat_parse.py
```
It prints TTFA/chunk_gate/tts p50/p90/p95, a per-`capture_phase` breakdown, and (once WS-C
Phase-1 is deployed) a **WS-C ENDPOINT** block: endpoint dead-time p50/p90 and cutoff rate
per phase. The locked baseline for comparison is `LATENCY_BASELINE_LOCKED.md`.

---

## 8. Regression catalogue (re-check every one of these)

These are the known failure modes — nearly all already FIXED — that the campaign must
re-verify (regression), plus a few still open. Each has an ID the playbook references. Fix
status and file anchors below; **repro steps are in the playbook**.

### N — Name capture (`name_capture.py`, `connection.py:1129-1256`)
Policy: Susie reads back the **first name only**; the surname is never confirmed, so a
wrong-but-plausible surname is invisible to the caller and flows silently to SMS/Sheets.
**→ On every booking call, log the exact surname that lands in the summary row.**

| ID | Failure | Status | Anchor |
|---|---|---|---|
| N1 | Surname via "would be / will be X" dropped | FIXED | `name_capture.py:241-257` |
| N2 | "Sarah Jenkins, calling about my knee" → surname "Knee" | FIXED | `name_capture.py:259-318` |
| N3 | Late-turn surname dropped ("Quentin" locks, "Rook" lost) | FIXED | `backfill_surname` `name_capture.py:321-397`; `connection.py:1190-1235` |
| N4 | Spelled-out surname harvested a stray "s" from contractions | FIXED | `name_capture.py:350-377` |
| N5 | Bare one-word straggler ("Rock") dropped as noise | FIXED | straggler exemption `connection.py:5248-5326` |
| N6 | Time-first readback ("So that's quarter to twelve…") stored as name | FIXED | `_V3_SLOT_LEAD_WORDS` `connection.py:1147-1154` |
| N7 | `book_appointment` fired first-name-only (JV needs surname) | FIXED (backstop) | `llm_stream.py:1736-1745` |
| N8 | `_V3_SLOT_LEAD_WORDS` NameError crashed name-persist | FIXED | `connection.py:1148-1154` |

### F — FAQ / clinic / location (multi-clinic machinery must stay dormant on JV)
| ID | Failure | Status | Anchor |
|---|---|---|---|
| F1 | Clinic-named FAQ silently bound a location / started booking | FIXED | `connection.py:724-725`, `:1543-1559` |
| F2 | Pure-FAQ caller hit the location gate | FIXED | `connection.py:6347-6376` |
| F3 | FAQ opening-hours bled into booking `date_hint` | FIXED | slot cache clear `connection.py:6425-6460` |
| F4 | FAQ-before-clinic pending Q lost after DTMF pick | FIXED | `connection.py:4408-4429` |
| F5 | "which clinic" disambiguation (multi-clinic only) | Handled | `connection.py:3156-3242` |
| F6 | Clinic-not-bookable redirect (never say "we can't help") | Handled (prompt + router) | — |
| F7 | Treatment mention mis-routed FAQ vs booking | FIXED | `flow.py:750-800` |

### T — Turn-taking / audio
| ID | Failure | Status | Anchor |
|---|---|---|---|
| T1 | Spurious "didn't catch that" on a clean slot turn (gate5 over-drop) | FIXED | `llm_stream.py:1441-1444`, `turn_handler.py:385-401` |
| T2 | Echo of Susie's own TTS false-cancels the watchdog → re-ask loop | FIXED | `connection.py:1941-1958`, `:6246-6280` |
| T3 | Same-breath straggler fires a redundant 2nd turn | FIXED | `connection.py:5248-5326` |
| T4 | Location-ack race re-asks clinic (multi-clinic) | FIXED | `connection.py:3971-3977` |
| T5 | Watchdog re-ask loop during long FAQ/slot TTS | FIXED | `connection.py:2091-2122`, `:2491-2598` |
| T6 | Start-of-call dead air / watchdog false-fire before greeting | FIXED | greeting-gated arm `connection.py:1742-1747` |
| T7 | Silence → suppression → dead-air hangup | FIXED | 10s safety net `connection.py:4115-4231` |
| T8 | Single-word reject: valid answer discarded (**"anytime"** case) | **PARTIAL — fix queued** | `connection.py:262`, `:6086-6117` |
| T9 | **CoT/reasoning leaks to TTS** ("The caller said…", "look up the patient", "N slots", state labels) | FIXED | `turn_handler.py:53-143`, `:298-401` |

### Q — TTS / STT quality
| ID | Failure | Status | Anchor |
|---|---|---|---|
| Q1 | Currency garble ("£48" mis-read) | FIXED | `tts_stream.py:71-89` |
| Q2 | Mispronunciation subs (Alcester→"Awlstuh") | FIXED | `tts_stream.py:72,85` (on JV, "Awlstuh" appearing = fail) |
| Q3 | Midday counted as afternoon | FIXED | — |
| Q4 | STT mishears ("bolten", "markus", "acupunture", "joint vencher fizzy-oh") | FIXED (boost+alias) | `stt_stream.py:71-93`, `connection.py:1298-1651` |
| Q5 | Informal British time ("half nine") rejected | FIXED | `receptionist_tools.py:280-320` |
| Q6 | Digit-readback watchdog fired too early | FIXED | `connection.py:2749-2756` |

### B — Booking integrity (`llm_stream.py` unless noted)
| ID | Failure | Status | Anchor |
|---|---|---|---|
| B1 | `check_availability` re-run after name+phone → re-asks name | FIXED | `:1631-1686` |
| B2 | `check_availability` re-run after slot confirmed | FIXED | `:1687-1719` |
| B3 | Duplicate `check_availability` same turn | FIXED | `:1618-1624`, `:1720-1735` |
| B4 | Stale slots not cleared on FAQ/new-date | FIXED | `connection.py:6425-6460` |
| B5 | Double-booking under forwarded/shared number | FIXED | `_is_clinic_own_number` `connection.py:909-935` |
| B6 | Mandatory slot-confirm "Yes" skipped | FIXED | slot_pending_confirmation |
| B7 | Readback dead-end "I don't have a slot yet" | FIXED | `v3_confirmed_slot_phrase` `:1651-1668` |

### E — Emergency / clinical safety (**prompt-only; no code backstop — manual pass/fail every time**)
| ID | Failure | Status | Anchor |
|---|---|---|---|
| E1 | Emergency → false booking/"which clinic?" pivot | FIXED | `connection.py:8928-8949`, `:10991-11001` |
| E2 | Emergency safety content spoken (999 / A&E, no diagnosis) — LLM-generated, so exact wording may vary; a fail is *missing/wrong safety content*, not minor rewording | Prompt/knowledge | `knowledge.md`; flag `connection.py:11426-11437` |
| E3 | Diagnosis/prognosis/medication answered instead of deflected | Prompt only | — |
| E4 | Corticosteroid request must never book | Prompt | — |
| E5 | Clinical/empathy reply cut off by echo | FIXED | `connection.py:10991-10997` |

### M — Notifications
| ID | Failure | Status | Anchor |
|---|---|---|---|
| M1 | Forwarded-number wrong SMS recipient | FIXED | `connection.py:909-935` |
| M2 | Empty-session status after `SESSION_SECRET` set → no SMS | FIXED | mirror-save `connection.py:11897-11918` |
| M3 | **Garbage caller-name logged on name-less calls** | **OPEN (observe)** | — |
| M4 | Staff-alert recipient (falls back to a Theorem-named env var) | Working | `connection.py:11933` |
| M5 | Duplicate SMS/Sheets rows | FIXED (idempotent) | `connection.py:11855-11890` |

---

## 9. Production go-live env checklist (config gaps, NOT Susie bugs)

Before any clinic goes live these must be set (they're separate from code fixes):
- `MEDIA_STREAMS_ENABLED=true` — else the route falls back to the legacy path.
- `GOOGLE_SHEETS_ID` — the call-summary/digest log target (absent → silent no-op).
- `SESSION_SECRET` — once set, SMS/status depend on the mirror-save (M2); verify a booking
  still SMSes.
- `transfer_phone` in the clinic config — staff-alert + transfer recipient. **Falls back to
  a Theorem-named env var** if blank — a rollout smell to clean up for the template.
- `SILENCE_WINDOW_1_SEC=10` in production (the automated test runner uses 30).
- API keys: `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`, `ASSEMBLYAI_API_KEY`,
  `OPENAI_API_KEY` (fallback), `TWILIO_*`, `RENDER_EXTERNAL_URL`.
- JV `clinic.json` still has **`TBC`** values (company number, bank holidays, deposit,
  reports) and a **blank `digest.email_to`** — go-live blockers to confirm with Marcus,
  not bugs.

---

## 10. Open / residual risks (put these in the regression column)

1. **M3** — garbage caller-name on name-less calls (still open; observe on emergency/
   transfer calls).
2. **Silent wrong surname** — by design Susie never reads the surname back, so a homophone
   flows to SMS/Sheets unseen. Not a bug to "fix", but **log the landed surname every
   booking call** so we can measure how often STT gets it wrong.
3. **CONFIRM_ASSESSMENT tangents** — the yes/advance classifier is probabilistic; a pricing/
   weather tangent can be read as "yes" and advance early. Probe with 2–3 tangents.
4. **Theorem-named fallbacks + the multi-clinic redirect keyed on a hardcoded Theorem
   Twilio number** — inert on JV but a latent hazard for the 200-clinic template. Confirm
   none fire on a JV call; flag for template cleanup.
5. **Clinical safety is prompt-only** (E3/E4) — no deterministic backstop. Every diagnosis/
   prognosis/medication deflection is a manual judgement.

---

## 11. Defect workflow

1. During a call block, when something's wrong, **don't stop the run** — note it in the
   results log (call #, SID, scenario, what happened) and keep going.
2. After the block, open a defect in the tracker (see the playbook): ID, **severity**
   (P1 = wrong booking / leakage / safety; P2 = broken flow/recovery; P3 = cosmetic/tone),
   scenario ID, repro steps, your root-cause guess.
3. Fix on `latency-eval` (§5 workflow), push, Manual Deploy.
4. **Re-test the exact scenario** plus the neighbours it could regress. Mark the defect
   `re-tested ✅` only after a clean pass.
5. Add fixed defects to the **daily regression re-test** so a later change can't silently
   undo them.

Questions on any subsystem: grep the file anchors above first, then the `LATENCY_*.md` and
the audit docs (`handoff.md`, `SUSIE_AUDIT_REPORT.md`, `FIX_VERIFY_2026-06-18.md`) — but
note those are Theorem-era; JV's authoritative values live in `clinic.json`.
