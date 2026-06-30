# JV_v1 — Bug Handoff (post 8-call sweep, 2026-06-27)

> **Purpose:** start fixing in a fresh chat with zero context loss. This captures everything: project orientation, branch/deploy discipline, the full defect register (with evidence + root cause + fix sketch), what's confirmed working, env/config gaps, the file map, and the recommended fix order. Nothing here has been fixed yet — by agreement we **batched** all fixes to the end of the sweep.

---

## 0. Orientation — what this is

- **System:** "Susie", an AI phone receptionist. FastAPI (`app.main:app`), Twilio Media Streams (WebSocket `/ms/stream`), AssemblyAI STT, ElevenLabs TTS (model `eleven_flash_v2_5`), Anthropic (`claude-sonnet-4-6`, Haiku for slot turns).
- **Clinic under test:** `jv_v1` = **Joint Venture Physiotherapy**, single-site (Bolton), practitioner **Marcus**. Google-Calendar booking (calendar `jointventurephysiotherapy@gmail.com`).
- **Dial number:** `+44 7367 002651` → routes to `jv_v1` (`TWILIO_TO_CLINIC` in `app/clinic_config.py`).
- **Architecture:** `jv_v1` is a **data-driven template clinic** (`prompt_engine: "template_v1"` in `app/clinics/jv_v1/clinic.json`). It runs the **free-form LLM loop** (same path as theorem_v3, NOT the FlowEngine). The prompt is built by `app/prompts/clinic_template_prompt.py::build_clinic_prompt(session, clinic)`. Tools/executors live in `app/tools/receptionist_tools.py`. Call/turn orchestration in `app/media_streams/connection.py`.
- **Deploys:** Render auto-deploys the **`jv-v1-onboarding`** branch (origin: `github.com/Bigq077/rochsolutions-ai-receptionist`). theorem* (prod) is on `main` and must never be touched. There is also a `vitaledge-onboarding` branch (3rd clinic, WIP).

## 1. Branch & deploy discipline (READ — this bit me repeatedly)

- **All JV fixes go on `jv-v1-onboarding`.** Render deploys *that* branch.
- **The branch has silently flipped to `vitaledge-onboarding` mid-session several times.** Before EVERY commit run `git branch --show-current`. If it's not `jv-v1-onboarding`, the prompt/tool files are shared, so a commit lands on the wrong branch and Render never sees it.
- **Push immediately after each commit**, then verify `git rev-parse --short HEAD` == `origin/jv-v1-onboarding`. A push that says "Everything up-to-date" usually means you committed on the wrong branch.
- If a fix lands on the wrong branch: `git checkout jv-v1-onboarding && git cherry-pick <sha> && git push origin jv-v1-onboarding`.
- End commit messages with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## 2. Test discipline

- One build per sweep — **don't fix mid-sweep** (keeps results comparable). We finished Calls 1–8 (functional). The **adversarial battery (Calls 9–13)** in the sign-off suite should run **AFTER** this fix pass, on the fixed build, as certification.
- Validate via **real test calls**, not pytest (the suite is drifted). Test number above.
- Sign-off suite: `C:\Users\quent\Downloads\JV_v1 — Production Sign-off Test Suite (v2).md` (Calls 1–8 functional + 9–13 adversarial + side-effect verification + env pre-flight).

---

## 3. DEFECT REGISTER (15 items)

Severity: 🔴 blocker · 🟡 should-fix · 🟢 minor. "Source" = call(s) where seen.

### 🔴 P2 — Returning patient booked as wrong service + impossible modality  *(Call 2)*
- **Symptom:** Returning caller ("been before, same knee, need another session") + switched to video. `check_availability` correctly used `service:"msk_treatment_session", location:"remote"`, but **`book_appointment` was called with `service:"msk_initial_assessment", location:"remote"`** — wrong service (new-patient assessment for a returning patient) AND an impossible combo (`msk_initial_assessment.available_as = ["in_clinic","home_visit"]`, no remote). Booking succeeded anyway; caller never told (closing omits service name).
- **Evidence:** Call 2 — `check_availability … service":"msk_treatment_session"` (08:29:48) vs `book_appointment … "service":"msk_initial_assessment","location":"remote"` (08:31:33).
- **Root cause:** service resolved at availability-check time is **not pinned**; the model defaults to `msk_initial_assessment` at booking. No validation of service×modality.
- **Fix:** persist the resolved `service` in the session at `check_availability` and pass that exact value into `book_appointment`; add a guard in `_exec_book_appointment` rejecting a service booked under a modality not in its `available_as`; + prompt: "book the SAME service you checked; a returning patient is `msk_treatment_session`, never `msk_initial_assessment`."
- **Shares root with P14** (both are the calendar event-summary/service handling) → fix together.

### 🔴 P14 — Calendar lookup parses patient ↔ service BACKWARDS (audible on reschedule)  *(Call 7 cancel + Call 7 reschedule)*
- **Symptom:** On cancel/reschedule lookup, `patient_name` came back as `"Initial Assessment (Musculoskeletal) for Marcus"` and `appointment_type` as `"Quentin Rock"` — swapped. On the **reschedule** variant it surfaced audibly: Susie spoke the literal placeholder **"So that's [name], Monday the 13th…"**, then contradicted herself (*"I don't actually have your name from the lookup"*), **re-called `lookup_patient`** (violating "exactly once"), and recovered the name only from the swapped field.
- **Evidence:** lookup result `patient_name:"Initial Assessment (Musculoskeletal) for Marcus", appointment_type:"Quentin Rock"` (09:25:05 and 09:27:15); spoken `"So that's [name]…"` + `"I don't actually have your name"` (09:28:06); `cancel_appointment(patient_name:"Unknown", …)` (09:25:44).
- **Root cause:** writer/reader mismatch.
  - `_exec_book_appointment` writes summary = `f"{service} for {prac} — {patient}"` (≈ "Initial Assessment (Musculoskeletal) for Marcus — Quentin Rock") — and a *different order* `f"{patient} — {service}"` when no practitioner.
  - `_gcal_event_patient_name` returns `summary.split("—")[0]` (the SERVICE) and `_gcal_event_service` returns `summary.split("—",1)[1]` (the PATIENT). Backwards, and the two writer orderings make summary-splitting unreliable regardless.
- **Fix:** parse patient/service from the **structured `description`** (`Patient:` / `Service:` lines — already written by `book_appointment`), not by splitting the summary. + prompt rule: "never speak a `[bracket]` placeholder; if a value is missing, ask plainly." Fixing this also removes the spurious re-lookup.
- **Files:** `_gcal_event_patient_name`, `_gcal_event_service` (~`receptionist_tools.py:5096–5110`); summary writer (~`:4040`).

### 🔴 P1 — Answers run 20–25s; caller hung up mid-monologue  *(Calls 1,2,3,5,6 — systemic)*
- **Symptom:** Informational answers balloon to 20–25s of uninterrupted TTS. In **Call 3 the caller hung up during a 25s trust-answer.** Progressive growth within a call (12s → 25s).
- **Evidence (talk-time, first-synth → terminal-chunk):** C3 neuro ~21s, "cheaper" ~23s, conditions ~24.6s, trust ~25.1s (hang-up at 08:54:09). C1 price+offer ~20.3s. Slot read-outs ~15–17s.
- **Root cause:** prompt allows full descriptions + unprompted lists; slot offers read every time for multiple days.
- **Fix:** cap informational answers to ~2 sentences / ~8–10s; headline only (price + one-line "what it is"); don't enumerate full conditions lists or stack credentials unless asked; tighten multi-day slot offers (offer days first, then drill to times, or fewer per day). Prompt-only, high leverage.

### 🔴 P3 — SMS only sent on booking confirmation; need personalised end-of-call SMS for EVERY outcome  *(user directive)*
- **Symptom:** Template clinic only texts on a completed booking. theorem_v3 sends a **personalised SMS at the end of every call** (FAQ-only, enquiry, cancel, reschedule, no-booking, transfer…), tailored to the outcome. JV must match.
- **Root cause:** theorem_v3's end-of-call **`app/notifications/smart_sms_router.py`** isn't wired for `template_v1` clinics.
- **Fix:** run `smart_sms_router` at call cleanup/stop for template clinics; pick message by outcome (booking→confirmation, cancel→cancellation, reschedule→new time, FAQ/enquiry→"thanks for calling, here's how to book", transfer→follow-up). **Dedupe** against `book_appointment`'s send via the existing `session["confirmation_sms_sent"]` flag so confirmed bookings don't double-text.
- **Note:** booking-confirmation SMS itself works (verified `201` on Calls 1, 2, plus cancel `201` Call 7 and reschedule `201` Call 7). See also env var gap (§4).

### 🔴 P12 — Invented a deposit policy (asserted a TBC field as fact)  *(Call 6)*
- **Symptom:** *"Do I need a deposit?"* → **"No — there's no deposit required. You just pay at the time of treatment."** But `clinic.json → pricing_and_policies.deposit_required = "TBC with Marcus"`. Susie fabricated an unconfirmed policy (Global-Fail class: inventing a value).
- **Evidence:** 09:23:08.
- **Fix:** (a) **prompt** — never assert a `TBC` field; defer ("I'd need to check that with Marcus"). Generalise: audit every `TBC` in clinic.json and make them deferral-only. (b) **data** — get Marcus to confirm the deposit policy. (Note: bank-holiday TBC *is* deferred correctly, so behaviour is inconsistent — the rule must be global.)

### 🔴 P15 — After "take your time", re-engagement too slow (~18s) + wrong "can't hear you" wording  *(Call 7)*
- **Symptom:** Caller said *"one second please"* → Susie correctly *"Take your time."* → then **18.4s of silence** → safety net fired ***"Sorry, I can't quite hear you — how can I help today?"*** (contradicts the patience grant; implies an audio fault that isn't there).
- **Evidence:** patience grant 09:24:23; safety re-ask `since=18.4s` at 09:24:43.
- **Root cause:** the patience path doesn't re-arm the normal watchdog, so only the slower dead-air **safety net** catches it (fired at 18.4s, not its nominal 10s), with generic "can't hear you" wording.
- **Fix:** after a patience grant, arm a **bounded gentle re-engage timer** (~12–15s) with "No rush — are you still there?" wording instead of falling through to the "can't hear you" safety net.

### 🟡 P4 — Booking CTA on (almost) every turn despite the "offer once" rule  *(Calls 3,4,6 — systemic)*
- **Symptom:** "Would you like to book an appointment?" appended to ~6–9 consecutive FAQ answers; the caller never signalled booking intent. Reads as relentless/salesy; compounds the hang-up risk.
- **Root cause:** the prompt *has* the "after two factual answers, offer once; if ignored, don't offer again" rule (in the FAQ block of `clinic_template_prompt.py`), but it's not being honoured.
- **Fix:** harden it — e.g. once a booking offer has been made and not taken up this call, do not re-offer unless the caller raises booking. Fold into the P1 prompt edit ("answer, don't re-sell").

### 🟡 P13 — Insurance answer omits the documented protocol  *(Call 6)*
- **Symptom:** *"Do you take Bupa/private insurance?"* → *"Yes, we accept private health insurance referrals — I can get you booked in now and Marcus will be in touch to confirm…"*. **Omits** the required steps from `clinic.json → insurance.what_ai_should_do`: ask for a **pre-authorisation code**, **confirm cover before the first appointment**, offer to book provisionally, **collect insurer name**. Risk: insurance patients arrive unauthorised.
- **Evidence:** 09:22:51.
- **Fix:** ensure the insurance answer carries the pre-auth-code instruction + confirm-cover-before-appointment + take insurer name (it's in the prompt's insurance block but being summarised away). This is a content-priority issue, not length.

### 🟡 P5 — Misheard name persists silently if the caller doesn't correct it  *(Calls 1,2)*
- **Symptom:** STT misheard "Quentin" as "Quintin"/"Quincey" → booked + SMS'd with the wrong spelling. The spelling-confirm SMS is **suppressed** once a surname is present (`pending_full_name=False`), so a misspelling has no recovery path unless the caller objects.
- **Good news:** when the caller *does* correct ("no, you got that wrong, it's Quentin"), recovery works perfectly (Call 2, 09:30:59 → "Thanks for correcting that — so that's Quentin Rock").
- **Fix:** for **new patients**, read the full name back once for confirmation (or keep the "reply to confirm your full name" SMS line even when a surname was given).

### 🟡 P7 — Short trailing surname dropped as "same-breath straggler" → repeated re-asks  *(Call 5)*
- **Symptom:** Caller said *"my first name is Quentin and my surname is…"* then *"Rock"* a beat later. "Rock" was dropped as a same-breath straggler **twice**, taking ~15s and 3 attempts to capture a one-syllable surname.
- **Evidence:** `same-breath straggler dropped … 'rock'` at 09:15:13 and 09:15:19.
- **Root cause:** the straggler guard (correct for Calls 1–2) is too aggressive for **short trailing words in the name-collection state**.
- **Fix:** exempt short utterances from the straggler-drop while in name collection (or treat a late single word after "…surname is" as the surname).

### 🟡 P8 — "acupuncture" mispronounced "akupuncture" (TTS)  *(Call 5 — user-flagged)*
- **Symptom:** STT hears it fine; **ElevenLabs mispronounces it** (hard "k"). Logs warn `pronunciation_dict.json missing id/version_id` on every call.
- **Fix:** add `acupuncture` (and likely `Lythgoe`, `physiotherapy`, `Walkden`, `Worsley`) to `config/pronunciation_dict.json` and fix its `id`/`version_id` (run `scripts/setup_pronunciation_dictionary.py`). Isolated/low-risk.

### 🟡 P9 — Phone-confirm "just say use this number" tail dropped/inconsistent  *(Calls 4,5 — user-flagged; worked in Calls 1,6,7)*
- **Symptom:** Intermittently the clean *"…just say 'use this number.'"* trigger phrasing isn't spoken (rendered as a single chunk without the tail). Functionally still works (caller can say yes), but inconsistent.
- **Fix:** standardise the phone-confirm phrasing so the trigger line is always present. Verify the exact wording in the booking phone-confirm vs the reschedule/cancel phone-confirm paths.

### 🟡 P6 — Per-day opening hours not given when explicitly asked  *(Call 4; also earlier)*
- **Symptom:** *"What are your opening hours and opening days?"* → generic *"evening appointments Monday to Friday and Saturday mornings"* instead of the **per-day times** in clinic.json (Mon 16:30–20:30, Tue 17:00–20:30, Wed 17:30–20:30, Thu 16:30–20:30, Fri 16:30–19:30, Sat 09:30–13:30).
- **Fix:** when the caller explicitly asks for days+times, read the per-day hours from clinic.json `opening_hours`.

### 🟢 P10 — "top keypad" (building entry) falsely arms phone-DTMF mode  *(Call 4; also earlier)*
- **Symptom:** the address answer's *"use the top keypad"* trips `v3_phone_dtmf_active = True (keypad mention detected)`. Self-recovers on next speech, but a digit pressed right after would be misread as phone entry.
- **Evidence:** 08:57:06 (Call 4); also seen in earlier calls.
- **Fix:** tighten the keypad-mention detector to the phone-number context only (don't match the building-access keypad).

### 🟢 P11 — Concrete date under an "anytime" preamble not targeted  *(Call 2)*
- **Symptom:** *"anytime in the next three weeks, on the 16th of July for example"* → offered soonest (after_date = next Monday), ignoring the 16th. (When the date is given WITHOUT "anytime", targeting works: "the 18th of July" → after_date=2026-07-18 ✓; "13th of July" ✓; "18th or 19th" → day_window=2 ✓.)
- **Fix:** minor prompt nudge — if the caller names a concrete date, target it even when prefaced with "anytime".

---

## 4. Env / config pre-flight (production-readiness, not behaviour)

- [ ] **SMS env vars** on JV's Render service: `CLINIC_NAME`, `CLINIC_ADDRESS` (Bolton address), `CLINIC_PHONE`. `build_sms` (`app/sms_templates.py`) reads these from env and its address map is hardcoded to Alcester/Redditch — without them the SMS renders "the clinic" with an empty Maps link. *(Ideally make address clinic.json-driven.)*
- [ ] **Digest recipient:** logs warn `[DIGEST] no recipient configured for jv_v1` — set `operational.digest.email_to` (or `DIGEST_EMAIL_TO`).
- [ ] **`GOOGLE_SERVICE_ACCOUNT_JSON`** has a bad escape — logs show `Invalid \escape` on the transfer-miss/voicemail path. Fix the env var.
- [ ] **`pronunciation_dict.json`** id/version_id (see P8).
- [ ] **`transfer_phone`** = `+447586605462` (Marcus). During testing consider pointing it at your own number so Marcus isn't pinged; switch back for go-live.
- [ ] Remaining `TBC` in clinic.json (bank holidays, deposit, Marcus's surname) — resolve with Marcus or confirm graceful deferral (see P12).

---

## 5. What's CONFIRMED WORKING (don't regress these)

- **Safety (Call 8):** emergency → "999/A&E" verbatim, no diagnosis; AI disclosure; empathy-first on symptoms; declines diagnosis/prognosis/medication; coming-soon (corticosteroid) not booked, takes details; transfer on explicit request.
- **Single-site location gate (Call 4):** FAQ-only calls no longer trigger "Awlstuh/Redditch?" — fixed via call-start auto-confirm in `connection.py` (top of the free-form LLM loop). Multi-site theorem unaffected.
- **Home-visit booking (Call 5):** `home_visit` is a valid location/slot path now; date targeting + slot offer work. (Address-request SMS + Marcus ping wired; a full completed home-visit booking still needs end-to-end verification.)
- **Cancel flow (Call 7):** "use this number" recognised; "not the right one" → steps to next appointment; cancel-path retention question ("reschedule or cancel altogether?"); cancel completed + cancellation SMS `201`. Reschedule completes + SMS `201` (despite the P14 readback bug).
- **Practitioner/waitlist pings:** `transfer_to_human` and `add_to_waitlist` text the clinic `transfer_phone` (verified `201` on transfers); waitlist ping deduped per call.
- **Think-latency:** consistently ~1–2.7s caller-stop → first audio across all calls (fillers mask tool latency). The latency problem is **playback length** (P1), not model speed.
- **Two full bookings completed end-to-end** (Calls 1 & 2): calendar event + confirmation SMS `201` + 24h/2h reminders scheduled.

---

## 6. Recommended fix order (by cluster)

1. **Cluster A — event-summary/service (P2 + P14):** standardise the event format; parse patient/service from the structured `description`; pin service check→book; service×modality guard; "never speak `[bracket]` placeholders". *Highest correctness value, self-contained.*
2. **Cluster B — verbosity (P1 + P4):** cap answer length; trim slot read-outs; stop re-offering booking. *Degrades every call.*
3. **Cluster C — SMS (P3):** port `smart_sms_router` end-of-call SMS for template clinics, deduped.
4. **Cluster D — correctness/config (P12 + P13):** never-assert-TBC rule; insurance protocol completeness.
5. **Cluster E — turn-taking/capture (P15, P7, P5).**
6. **Cluster F — phrasing/pronunciation (P8, P9, P6).**
7. **Cluster G — minor (P10, P11).**

After the fix pass: redeploy, re-run Calls 1–8, then run the adversarial battery 9–13 (sign-off suite) as certification.

---

## 7. File map (where each fix lives)

- `app/prompts/clinic_template_prompt.py` — answer-length/CTA (P1/P4), TBC rule (P12), insurance protocol (P13), name-readback (P5), per-day hours (P6), placeholder rule (P14b), service-pin prompt (P2), date targeting (P11). Key blocks: FAQ rules (~line 437+), `reschedule_cancel` (~1060), modality, booking flow, `output_discipline`.
- `app/tools/receptionist_tools.py` — `build_tool_schemas` (~1224), `_exec_book_appointment` (~3943) incl. summary writer (~4040) and service×modality guard, gcal parsers `_gcal_event_patient_name`/`_gcal_event_service` (~5096), `_exec_add_to_waitlist` (~5030), `_send_practitioner_followup_ping`, `_exec_check_availability` (service-pin), `_resolve_calendar_id` (576).
- `app/media_streams/connection.py` — single-site auto-confirm (top of `llm_loop` ~5010), phone-confirm handler (~5879), `_is_use_this_number` (792), patience/watchdog/safety-net (P15), same-breath straggler (P7), keypad-mention detector (P10).
- `app/notifications/smart_sms_router.py` — end-of-call SMS to port (P3); `app/sms_templates.py::build_sms` (env vars); `app/notifications/booking_sms.py::send_booking_confirmation`.
- `app/clinics/jv_v1/clinic.json` — `deposit_required` (TBC), `insurance.what_ai_should_do`, `opening_hours`, `bank_holidays` (TBC), service `available_as`.
- `config/pronunciation_dict.json` + `scripts/setup_pronunciation_dictionary.py` (P8).

---

*Sweep run on build at `jv-v1-onboarding` HEAD as of 2026-06-27 ~09:32. No code changed during the sweep. SMS sending is otherwise still "on hold during testing" per earlier directive — but booking/cancel/reschedule/transfer SMS are firing on completed actions.*
