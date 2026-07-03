# Susie Fix Booklet

> Running record of each fix made in the sweep-remediation sessions, so **Jules and
> Quentin can exchange info** without re-reading diffs. One entry per finding.
> Source backlog: [docs/sweep_findings.md](sweep_findings.md) (FINAL COMPILE, grouped by root cause).
> Workflow: [docs/playbook.md](playbook.md) — diagnose → failing test first (TDD) → one change → verify → one commit.

**Entry template:** Finding · Root cause · Change (file/fn) · Test · Verified (auto/phone) · Commit · Notes.

---

## ⚠️ Branch baseline note (read first)
As of the start of the 2026-07-02 fix session, branch `investigate/susie-call-flows`
already has **~90 pre-existing failing tests** (in `test_name_collector.py`,
`test_silence_handler.py`, `test_sms_templates.py`, `test_soft_context.py`,
`test_returning_treatment_plan_exit.py`, and others). **These are NOT caused by any
fix in this booklet** — verified by stashing each change and re-running (baseline =
90 failed, after each fix = still 90 + our own passing tests). They pre-date our work
and are their own cleanup track. Every entry below states its net effect on this count.

---

## F13 — Booking-CTA appended to pure-FAQ answers  ✅ SIGNED OFF (prompt-only, phone-verified 2026-07-02)
**Priority:** HIGH (professionalism / conversion). **Sweep:** Group 2; Calls 4 (prices) & 5 (parking) — FAIL-level.

### Symptom (from sweep)
On plain informational questions Susie tacked on a booking push:
- Call 4: *"It's £85 for fifty minutes. **Would you like to book an appointment?**"*
- Call 5: parking answer + *"**Would you like to book an appointment?**"*
The sweep's Call 4 FAIL criterion is "booking push before turn 10"; Call 5 expects
"answers parking (NOT booking)". Correctly suppressed on treatment/concern turns
(Calls 9/12/14) — so it was specific to *non-treatment* FAQ answers.

### Root cause (what the sweep didn't have)
The v3 system prompt **contradicts itself**:
- [susie_system_prompt.py:1141](../app/prompts/susie_system_prompt.py#L1141) & [:1467](../app/prompts/susie_system_prompt.py#L1467)
  say *do NOT* offer booking after an informational answer.
- [susie_system_prompt.py:2292-2304](../app/prompts/susie_system_prompt.py#L2292) said the opposite:
  *"after answering any FAQ question, close with a booking call-to-action."*

The model followed the 2292 instruction on price/parking. The deterministic guard
(Gate 5c in `sanitise_response`) only stripped the CTA when `booking_flow_active`
was True (mid-booking), so a pure-FAQ turn — where **both** `booking_flow_active`
and `v3_treatment_mentioned` are absent ([connection.py:7681-7699](../app/media_streams/connection.py#L7681-L7699)) —
sailed straight through.

### Fix — FINAL: prompt-only (the gate was tried, regressed, and reverted)
Phone re-test (call `CAbc1f0122…`, 2026-07-02 21:11) showed the **prompt fix alone**
handled the FAQ turns (turn 1 price produced NO CTA — gate never fired), while the
**gate over-stripped a legitimate concern-turn booking offer** on "I've hurt my
shoulder" (`concern=False` → offer removed). The gate keyed off `v3_treatment_mentioned`,
which does NOT fire on a plain injury description (only treatment-word mentions,
connection.py:7681). No reliable clinical signal is available at strip time
(`_clinical_response_active` is computed per-chunk downstream and isn't in `session`).
**Decision: revert the gate, keep the prompt fix** — smallest correct change; a fix
that breaks the concern flow is not a fix. F13 is now prompt-only (no unit test;
phone-verified). Determinism can be re-added later via a proper upstream
clinical-complaint session flag if F13 ever recurs.

<details><summary>Reverted gate approach (commit 1 — kept for the record)</summary>

**Commit 1 — deterministic gate (TDD).** REVERTED.
`app/media_streams/turn_handler.py` → `sanitise_response`, Gate 5c.
Was: strip the trailing booking-offer CTA only when `booking_flow_active`.
Now: strip it **unless it's a concern turn** — i.e. keep the CTA only when
`booking_flow_active is False AND v3_treatment_mentioned is True`. The existing
**whole-response guard** still protects a standalone booking question / the closing
confirmation ("shall I go ahead and book that in?") from being stripped to empty.
- Discriminator logic: pure informational FAQ = neither flag set → strip.
  Concern turn = `v3_treatment_mentioned` → keep (Calls 9/12/14 want the offer).
  Mid-booking = `booking_flow_active` → strip redundant tail (unchanged).
</details>

**Commit 2 — prompt reconciliation (KEPT — this is the fix).**
`app/prompts/susie_system_prompt.py` around [:2292](../app/prompts/susie_system_prompt.py#L2292).
Added an explicit exclusion so the "close with a CTA after an FAQ" rule now agrees
with the 1141/1467 rule: purely INFORMATIONAL questions (prices, hours, parking,
location, directions, services) get **no** CTA — end with "Is there anything else I
can help you with?" instead. Offer booking only on a described concern/injury or an
explicit booking ask.

### Test — removed with the gate revert
`tests/test_faq_booking_cta.py` was added with the gate (commit 1) and is removed by the
revert. Its `test_concern_turn_keeps_booking_cta` guard used `v3_treatment_mentioned=True`,
which a real concern turn does NOT set — so the suite was green but did not represent the
actual concern turn, and missed the regression. **Lesson: unit-test fixtures must match the
real session state a live turn produces.** The prompt-only fix has no unit test (prompt
behaviour); it is phone-verified.

### Verification — phone re-test (call `CAbc1f0122…`, 2026-07-02 21:11, deployed staging)
| Turn | Said | Result |
|---|---|---|
| 1 | "How much is a session?" | ✅ "£85 for fifty minutes…" + "Is there anything else I can help you with?" — **no CTA** (gate never fired → **prompt fix did it**). |
| 2 | "Do you have parking?" | ✅ parking answer, no booking push. *(Side issue: "alcester" resolved `intent=booking` and detoured to "day or time?" before the parking answer — location/re-queue friction, Group 4, not F13.)* |
| 3 | "I've hurt my shoulder" (control) | ❌ gate logged `removed out-of-place booking offer (…concern=False)` and **stripped a legit booking offer** → **regression** → gate reverted. |

**Re-verify after revert (call `CA8b7099…`, 2026-07-02 21:29, prompt-only deploy) — ALL PASS:**
| Turn | Said | Result |
|---|---|---|
| 1 | "How much is a session?" | ✅ "£85… Is there anything else I can help you with?" — no push, no gate line. |
| 2 | "Do you have parking?" | ✅ "free parking… ~eighty spaces… anything else?" — no push. Detour did NOT recur (FAQ re-queued cleanly; last time's detour was STT-driven "al foster"→intent=booking). |
| 3 | "I've hurt my shoulder" | ✅ "…an assessment would look at what's going on… **Would you like to book one with Mark?**" — CTA restored, concern flow intact. |

→ **F13 SIGNED OFF.** Prompt-only fix; concern flow preserved; no gate residue.

### Commits
- Commit 1 (gate + test): **made, then REVERTED** after the phone test.
- Commit 2 (prompt reconciliation): **KEPT** — this is the fix.
- Revert commit: _(pending — `git revert` of commit 1)._

### Notes / blast radius
- Final state touches only the prompt ([susie_system_prompt.py:2292](../app/prompts/susie_system_prompt.py#L2292)) —
  `turn_handler.py` returns to its original gate-5c behaviour (strip only when
  `booking_flow_active`).
- Follow-up idea (not now): to re-add a deterministic FAQ-CTA guard safely, set a
  `session` flag marking a clinical-complaint turn *upstream* of the response stream, then
  gate on it — instead of inferring concern from `v3_treatment_mentioned`.

---

## F17 — Silent transfer: verbatim G18 line not spoken  ✅ SIGNED OFF (phone-verified both paths 2026-07-02)
**Priority:** HIGH (safety). **Sweep:** Group 1 (safety-script line guarantees); Call 6.

### Symptom (from sweep)
On "can I just speak to someone" the LLM called `transfer_to_human` and the call bridged
with **no spoken line** — the required verbatim G18 line *"Putting you through now — please
stay on the line."* was never delivered. Silent transfer on a safety path.

### Root cause
The LLM transfer path had **no deterministic spoken line**:
- Its intended line is LLM prose — the prompt says *"…just bear with me"*
  ([susie_system_prompt.py:1538](../app/prompts/susie_system_prompt.py#L1538)) — and gate5's
  `bear_with_me` pattern ([turn_handler.py:45](../app/media_streams/turn_handler.py#L45))
  strips the whole sentence → silence.
- The only deterministic line, the TwiML `<Say>` ([realtime.py:466](../app/routes/realtime.py#L466)),
  is (a) suppressed on staging — `TRANSFER_DISABLED` returns at
  [realtime.py:443](../app/routes/realtime.py#L443) *before* the TwiML is built — and (b) only
  fires *after* the REST redirect, so a dead-air gap precedes it in prod.
- DTMF press-1 works only because it queued a TTS line before dialing
  ([connection.py:4442](../app/media_streams/connection.py#L4442)) — audio on the live stream,
  independent of gate5 and the kill-switch.

`_on_transfer_request` ([connection.py:10907](../app/media_streams/connection.py#L10907)) is
the single choke point for ALL transfer paths (LLM tool via
[connection.py:9329](../app/media_streams/connection.py#L9329), DTMF, silence, emergency).

### Fix (one commit) — decision: KEEP BOTH lines (safe)
`app/media_streams/connection.py`:
1. In `_on_transfer_request`, after the guard passes and before `_handle_transfer`, queue the
   verbatim G18 line to `tts_text_queue`. TTS on the live stream bypasses gate5 AND plays even
   when the dial is suppressed on staging → guaranteed + phone-verifiable.
2. Removed the DTMF-path line ([old 4442](../app/media_streams/connection.py#L4442)) so it
   doesn't stack two lines before the dial — all paths now emit the one unified G18 line.

The prod TwiML `<Say>` in `realtime._handle_transfer` is **kept** as the post-redirect delivery
(chosen over a dial-only TwiML: safest "line always delivered"; accepts a possible brief
overlap in prod — overlap ≫ silence on a safety path).

### Test (TDD) — `tests/test_transfer_line_spoken.py` (new, 2 tests)
- `test_authorised_transfer_speaks_g18_line` — RED before, GREEN after: an authorised transfer
  enqueues the verbatim line, then dials.
- `test_blocked_transfer_speaks_nothing` — guard: a blocked transfer speaks nothing / no dial.

### Verification
- **Automated:** 2/2 F17 tests pass; `test_transfer_disabled_gate.py` still 4/4 (kill-switch
  intact); full suite **90 failed / 1004 passed** = pre-existing 90 + our 2 new → **0 regressions**.
- **Phone — BOTH PATHS PASS (2026-07-02, staging, TRANSFER_DISABLED set):**
  | Path | Call | Result |
  |---|---|---|
  | DTMF press-1 | `CAfd864352…` 21:52 | ✅ `transfer SUPPRESSED` + `synthesise_chunk 'Putting you through now — please stay on the line.'` — old "Transferring you to Mark now…" wording gone. No dial/SMS. |
  | LLM "speak to someone" | `CAa796799a…` 21:54 | ✅ gate5 `removed banned phrase (bear_with_me)` (root cause still strips the prose) **then** `synthesise_chunk 'Putting you through now — please stay on the line.'` fires anyway. `transfer SUPPRESSED`. No dial/SMS. |
  → The exact Call-6 silent-transfer path now speaks the verbatim line. **F17 SIGNED OFF.**

### Commits
- Fix + test (`Fix F17: speak verbatim G18 transfer line…`): **DONE.**
- Booklet: _(this commit)._

---

## F20 — book_appointment not gated on a clear YES  ✅ SIGNED OFF (unit-proven + phone-safe 2026-07-02)
**Priority:** HIGH (safety). **Sweep:** Group 1; Call 8 (user-raised).

### Symptom / root cause
The book guard ([llm_stream.py:1477](../app/media_streams/llm_stream.py#L1477)) blocked
`book_appointment` unless `last_bot_prompt` contained "shall i go ahead" / "book that in"
— i.e. it only checked the confirmation **question was asked**, never that the caller
**said yes**. Once asked, a weak/ambiguous/negative *verbal* reply could still book.
(Silence was already safe: no transcript → no turn.)

### Fix (one commit) — `app/media_streams/llm_stream.py`
1. New `_book_confirmation_ok(session, last_user_text)` — allows booking only when the
   confirm question was asked AND the caller's reply is a clear yes, reusing fast_path's
   `_YES_PATTERNS` / `_NO_PATTERNS` as `is_yes and not is_no`. Ambiguous (both/neither) or
   empty → False (block).
2. Guard now calls that helper; two distinct block messages — `confirmation_required`
   (question not asked, unchanged) and `affirmation_required` (asked, but no clear yes →
   re-ask and wait for a clear yes).
3. Threaded the caller's last utterance into `_execute_tools` via a new `last_user_text`
   param — extracted at the single call site ([llm_stream.py:903](../app/media_streams/llm_stream.py#L903))
   from `messages`.
- **Bias:** a false block just re-asks (safe); a false allow is a wrong booking → ambiguity blocks.

### Test (TDD) — `tests/test_book_affirmative_gate.py` (new, 4 tests)
- clear yes ("yes please") → books; negative ("no, change it") → blocked;
  ambiguous ("um not sure") → blocked; confirm-not-asked + "yes" → blocked (existing behaviour).

### Verification
- **Automated:** 4/4 pass; full suite **90 failed / 1008 passed** = pre-existing 90 + our 4
  → **0 regressions**.
- **⚠️ Phone — SAFE SUBSET ONLY (Acuity NOT isolated on staging).** A genuine `book_appointment`
  creates a REAL appointment on Mark's Acuity (sweep Group 7). So on staging:
  - **DO test the negative path:** drive to "…shall I go ahead and book that in?", then say
    "no, change the time" or "um, I'm not sure" → PASS = Susie does NOT book, re-asks/corrects;
    no real appointment. (If it misfired, log would show `book_appointment BLOCKED — no clear YES`.)
  - **DO NOT say a clean "yes"** at the confirm on staging — it would book for real. The positive
    "clear yes books" path is covered by the unit test, not by phone.
  - 🟡 hang up before any real booking.
- **Phone result (call `CA86363383…`, 2026-07-02 22:19):** drove to readback
  *"…shall I go ahead and book that in?"*, then said **"do a different time"** → LLM called
  `check_availability` (offered other times), **NOT** `book_appointment` → **nothing booked** ✅.
  The guard itself wasn't triggered (the model declined to book on its own — expected on a clean
  correction); had it tried, "different" → `is_no` → block. So: **guard logic = unit-proven;
  end-to-end safety = phone-confirmed.** Sign-off stands for a defense-in-depth guard.
- Side-observation (not F20): correction was clunky — `check_availability BLOCKED — name+phone
  already collected` fired 2× + one 11.9s TTS. `booking_details_already_complete` interaction +
  long-TTS → F21 territory; logged for later.

### Commits
- Fix + test: **DONE** (`Fix F20: require a clear YES before book_appointment fires`).
- Booklet: **DONE.**

---

## F23 — Chirpy dead-air re-ask right after a 999 escalation  ✅ SIGNED OFF (phone-verified 2026-07-02)
**Priority:** MED (safety-tone). **Sweep:** Group 1; Call 10.

### Symptom / root cause
After firm 999/A&E instructions, caller silence made `_silence_safety_net` fire its generic
reset *"Sorry, I can't quite hear you — how can I help today?"* ([connection.py:11190](../app/media_streams/connection.py#L11190)),
undercutting the emergency. `medical_emergency_detected` is never set on the LLM red-flag path
(the 999 text is model-generated), so the reliable signal is the content of `last_bot_prompt`
(the full spoken reply, set at [llm_stream.py:496](../app/media_streams/llm_stream.py#L496)).

### Fix (one commit) — `app/media_streams/connection.py`
- New static `_emergency_reask_override(session)` → returns a calm re-anchor
  (*"If this feels like an emergency, please call 999 or go to A and E now — I'm still here if
  you need me."*) when `last_bot_prompt` contains `999` / `a and e` / `a&e` / `emergency
  service`; `None` otherwise (normal turns untouched — specific markers → low false-positive).
- `_silence_safety_net` checks it **first**, before the location/slot/generic branches.
- Called via the **class** (`WebSocketCallHandler._emergency_reask_override`), not `self` — the
  dead-air test drives the net with a `SimpleNamespace` stand-in, and `self.<newmethod>` would
  `AttributeError`. (Caught by the suite: it broke 4 dead-air tests until fixed — good guard.)

### Test (TDD) — `tests/test_emergency_reask_suppression.py` (new, 4 tests)
- emergency `last_bot_prompt` (999 / A&E) → override returns a 999-bearing phrase, no "how can I
  help today"; normal prompt / empty → `None`.

### Verification
- **Automated:** 4/4 pass; `test_dead_air_safety_net.py` back to its baseline 2-fail/8-pass
  (the 2 are pre-existing, unrelated); full suite **90 failed / 1012 passed** = 90 baseline + 4
  → **0 regressions**.
- **Phone (PENDING — after deploy):** staging `+447366263180`. Safe (no booking/transfer).
  1. Trigger a red flag, e.g. *"My back went and now I've got numbness around my saddle area
     and I can't control my bladder"* → Susie gives the 999/A&E redirect.
  2. **Go silent ~12-20s.** PASS = the dead-air re-ask is the calm emergency re-anchor
     (*"…please call 999 or go to A and E now — I'm still here…"*), **NOT** "how can I help today?".
  Log: `[ms_safety_net]` fires with the emergency phrase; no "how can I help today".
- **Phone result (call `CA59944a8e…`, 2026-07-02 22:53):** cauda-equina red flag → Susie escalated
  ("red flag symptom that needs urgent medical attention…get urgent help now"); after ~16.8s silence
  `[ms_safety_net]` fired **"If this feels like an emergency, please call 999 or go to A and E now —
  I'm still here if you need me."** — NOT "how can I help today?". ✅ **F23 SIGNED OFF.**
- Side-observation (not F23, pre-existing): a stray `tts_finished … "Those symptoms need urgent
  medical attention right away…"` appeared at call-start (22:53:07) before the caller spoke — looks
  like a leftover TTS fragment; not from this change (only the re-ask wording moved). Worth a glance.

### Commits
- Fix + test: **DONE** (`Fix F23: calm re-anchor instead of chirpy reset after a 999 escalation`).
- Booklet: **DONE.**

---

## F25 — Massage naming + Alcester-only mis-gate  ✅ SIGNED OFF (phone-verified 2026-07-03)
**Priority:** LOW-MED. **Sweep:** Group 6 (canonical consistency) + location mis-gate; Calls 12 & 14.

### Symptom / root cause
Two prompt-behaviour symptoms (the underlying DATA was already correct):
- **Naming drift** — Call 12 "I just need a massage" → Susie said "Sports massage"; Call 14
  "stress relief massage" → "wellness massage with in-light therapy". Inconsistent.
- **Location mis-gate** — Call 14: asked "which clinic?" for the Wellness Massage, which is
  **Awlstuh (Alcester) only** (canonical `wellness_massage.locations == ["alcester"]`).

The canonical source is right ([canonical.py:248](../app/clinics/theorem/canonical.py#L248)) AND the
prompt facts block already says *"Wellness Massage… Awlstuh only"* — the LLM just wasn't acting on
it. So this is prompt-tuning, not a code path. (NB "sports massage" = a JV-clinic service /
soft-tissue-within-physio; theorem's standalone relaxation service is the Wellness & Stress Relief
Massage.)

### Fix (one commit) — prompt-only + a data lock
`app/prompts/susie_system_prompt.py` (services facts block, ~L2237): added two rules —
1. **SINGLE-LOCATION SERVICES:** Wellness Massage + Psychotherapy are Awlstuh ONLY → never ask
   "which clinic?"; go straight to Awlstuh, don't offer Redditch.
2. **GENERIC MASSAGE:** on a bare "a massage" request, clarify goal (pain/injury → physio
   assessment; relaxation → Wellness & Stress Relief Massage, Awlstuh, enquiry-led); don't label
   it "sports massage" unprompted.

### Test — regression lock (not red→green; prompt behaviour isn't unit-testable)
`tests/test_theorem_canonical.py` (+2): `test_single_location_services_are_alcester_only`
(wellness_massage + psychotherapy stay `["alcester"]`) and `test_dual_location_services_still_list_both`
(acupuncture/shockwave keep both — the rule mustn't over-reach). Locks the data the prompt rule
relies on. Behaviour itself is phone-verified.

### Verification
- **Automated:** canonical suite 27 pass (+2); full suite **90 failed / 1014 passed** → 0 regressions.
- **Phone (PENDING — after deploy):** staging `+447366263180`, safe (no booking/transfer needed):
  1. "Do you do psychotherapy at Redditch?" → **Alcester/Awlstuh only** (offers Awlstuh, not Redditch).
  2. "Can I book the stress relief massage?" → states it's **Awlstuh only**, does **NOT** ask
     "which clinic?".
  3. "I just need a massage" → **clarifies** goal (pain vs relaxation), doesn't blurt "sports massage".
- **Phone result (call `CA95602172…`, 2026-07-03 17:48) — ALL 3 PASS:**
  | Turn | Said | Result |
  |---|---|---|
  | 1 | "psychotherapy at Redditch?" | ✅ "available at Awlstuh only, so Redditch wouldn't…" |
  | 2 | "book the stress relief massage" | ✅ "Wellness and Stress Relief Massage is also at Awlstuh only… not at Redditch" — **no "which clinic?"** |
  | 3 | "I just need a massage" | ✅ clarifies: "…a specific pain/injury or more of a relaxation and wellness massage?" — no "sports massage" |
  → The "which clinic" mis-gate was LLM behaviour (no deterministic gate); the prompt rule fixed it. **F25 SIGNED OFF.**

### Commits
- Fix + test: **DONE** (`Fix F25: Alcester-only massage/psychotherapy no clinic-ask + naming consistency`).
- Booklet: **DONE.**
