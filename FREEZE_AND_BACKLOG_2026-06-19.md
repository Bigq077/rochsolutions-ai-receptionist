# Susie — Freeze Status & Backlog (2026-06-19, updated 2026-06-23)

> Single-page situation report + prioritized change list.
> Local/untracked (not pushed) so it can't affect the frozen build.

---

## 🔄 2026-06-23 UPDATE — SMS bug fixed + 8-call re-sweep PASS + new freeze

**FROZEN DEMO BUILD = `a9b044a`** (`release-v1` re-pointed to it). This is the build the
8-call re-sweep validated and what's deployed. Redeploy `release-v1` if anything wobbles
before the Wed 2026-06-24 Mark demo. **Do not deploy demo morning.**

`a9b044a` contents (newest first):
- `a9b044a` cancel stale reminders on cancel/reschedule
- `b2ae92c` automatic day-before + 2-hour reminder SMS
- `85f840f` full clinic name in SMS
- `c6bf178` **post-call session-handoff fix** (mirror to HMAC-tagged `call:` key) — likely *why*
  the post-call SMS router reliably finds the phone now
- `f32f9b7` + `be0ce28` **booking-flow "use this number" SMS fixes** (this session)

**The "no SMS" bug is RESOLVED and verified.** Root was two-fold: (1) booking-flow verbal
"use this number" never set `phone_confirmed=True` (so `_get_confirmed_phone` ignored the
stored number) — fixed in `be0ce28`/`f32f9b7`; (2) post-call session handoff — fixed in
`c6bf178`. SMS now sends on both `reached_confirmation` and `abandoned`, across the
deterministic "use this number" path AND the conversational number-read-back path. **Wording
approved by owner.**

**Safety core RE-VERIFIED (Call 6)** — the long-standing ship gate is closed: AI disclosure
("Yes, I'm an AI receptionist"), no-diagnosis (slipped-disc → redirect to assessment),
emergency → "call 999 or A&E — we're not an emergency service", transfer_to_human → staff
number + staff-notify SMS.

**8-call re-sweep verdict:** 1,2,3,8 book→`reached_confirmation`+SMS ✅ · 4 FAQ→abandoned SMS ✅ ·
6 safety core ✅ · **5 = the one real defect (see NEW #1 below).**

### NEW TOP BACKLOG ITEM (post-demo #1) — mid-call clinic-switch location bug
**Call 5 (2026-06-23).** Caller confirmed **Alcester** for an FAQ (`use this clinic`), then
explicitly asked to **book at Redditch**. `check_availability` correctly used Redditch
(acuity_33801703, Thursday-only slots shown), but the **session's confirmed location stayed
Alcester** — booking-ack logged `location known (alcester)`. Split-brain state → **wrong-clinic
booking risk** if the caller completes (readback/booking could resolve to Alcester while the
slot is a Redditch slot). Caller hung up at slots this time, so no mis-booking occurred.
**Root:** a mid-call clinic switch (FAQ-confirmed clinic A → books clinic B) doesn't update the
session-confirmed location. **Fix (post-demo, frozen-zone — needs care + full re-test):** re-confirm/
update session location whenever the caller names a clinic for the booking, and reconcile it with
the location `check_availability` actually used. Edge case (uncommon), so deferred — but it's a
real data-integrity risk, ranked **above** the name-path polish.

### Other deferred (unchanged, all post-demo)
- **Name-path consistency** (was Tier-1 candidate): behaviour hangs off which name-capture path
  fired — clean full name arms the phone-collection phase ("use this number" script) + persists
  cleanly; fragmented/confirmed name skips arming → LLM free-forms the phone phrasing AND the
  persist hook "misses" (summary recovers first name only). Fix: arm phone-collection on all
  name paths. Owner decided NOT to do before demo (frozen-zone, no runway).
- **Surname STT garble** on the phone channel ("Roch" → "Rock"/"Rook"); first name (the anchor)
  is reliably correct. Pre-existing, consistent with the reverted full-name work.
- Verbosity (slot lists / FAQ answers 8–20s), TTS chunk-drop backstops, `SESSION_SECRET` ops.

---

## 1. SITUATION

### What we did this week
Ran a production sign-off of the cancel/reschedule lifecycle (Mark approved real Acuity
mutations). Found and fixed a **data-integrity bug**, then verified the full lifecycle on
clean builds.

### The bug that was fixed (data-integrity, critical)
Reschedule = book-new-first, then cancel-old. The cancel step had **no original-appointment
id** to work from in the v3 path, so it **cancelled by name search** and matched the
just-created NEW booking → cancelled the wrong appointment, leaving the caller with nothing
while told they were rescheduled.

**Fix (`abaa58d`, `receptionist_tools.py`):**
- `lookup_patient` now persists the found appointment's id + datetime + type.
- `_cancel_appointment_acuity` cancels by **exact id** (passed by reschedule, or from a
  preceding `lookup_patient`); name-search is last-resort only.
- `_reschedule_appointment_acuity` captures the ORIGINAL id *before* booking the new slot,
  carries the looked-up real name onto the new booking, and cancels the original by exact id.
- (`cb0b4f5`) also fixed blank date/time on the booking SMS.

### Verified on clean, settled builds (no mid-deploy)
- **Reschedule (10:07 call):** original `1724469496` (9 Jul) → new `1724470432` (6 Jul);
  cancel hit the ORIGINAL, kept the NEW. SMS sent. ✅
- **Cancel (10:20 call):** LLM passed `patient_name="Unknown"` — old name-search would have
  FAILED; exact-id path cancelled the right appointment. SMS sent. ✅ (Strongest proof the fix
  was *required*, not just nice-to-have.)
- **Book:** verified earlier (Phase 2A). ✅

**Full lifecycle GREEN: book ✅ · reschedule ✅ · cancel ✅ — all via the exact-id path.**

### Freeze
- `main` tip = **`abaa58d`** = frozen demo build.
- **`release-v1` re-pointed to `abaa58d`** (was stale `55786cf`) and pushed = the known-good
  fallback. Redeploy it if anything wobbles.
- Render auto-deploys `main` on push, so **freeze = do not push to `main`** until Monday.

### Objective / timeline
- **Mark demo: Wednesday 2026-06-24.**
- Real patients ~1 week out.
- Owner schedule: back Sun · **Mon = main work block (do + verify)** · **Tue = buffer / one
  verify call, NO new changes, NO deploys** · Wed = demo.
- **HARD RULE: last change + last verify done by Tue; nothing deployed the day of the meeting.**

---

## 2. NEEDED CHANGES — prioritized backlog

Core mutations are verified and frozen; **nothing below touches the frozen state machine**
except Tier 4 #6 (noted). None block the Wednesday demo — the build is already demo-solid.

### TIER 1 — Monday, before the demo (approved, prompt-only, low risk)
Do both as isolated commits, then ONE clean full reschedule+cancel re-verify on a settled build.

1. **Cancel-response phone double-ask (Change A).**
   Legacy line `susie_system_prompt.py:784` leaks "could I take the number you booked under"
   BEFORE the system's location→phone steps → phone asked twice, first one out of order.
   **Fix:** align that line (+ its twin at `:726`) to the v3 ack *"No problem at all."* (~1 line ×2).

2. **Redundant "reschedule or cancel?" in the reschedule flow.**
   Skip the question when the caller already stated reschedule/cancel; keep it only when intent
   is ambiguous. Prompt conditional in the v3 `reschedule_cancel` block (`:2024`).
   Low risk — the downstream "shall I go ahead and move/cancel that?" confirm protects correctness.

   *Shared root:* stale legacy `_cancel_reschedule_block` (inserted `:841`/`:940`) coexisting with
   the live v3 block (`:2024`, inserted `:3658`). Model blends legacy lines into v3 wording. See Tier 4 #7.

### TIER 2 — Before real patients (ops, not demo-blocking)
3. **Set `SESSION_SECRET` on Render.** 1 min, no code. Session keys not HMAC-protected;
   healthcare data → do before live patients. Anytime Mon/Tue.

### TIER 3 — Highest-value real-patient bugs (post-demo, in the ~1-week window)
4. **Multi-date hint wrongly refused.**
   "Do you have the 8th *or* 9th?" → *"I don't have availability on the 8th or 9th"* despite slots
   existing. The "X or Y" disjunction skips the week filter and returns global-earliest days.
   Single-date hints work fine, so it's narrow but trust-eroding. `receptionist_tools` date-hint
   parsing; medium effort + verification. **Top post-demo item.**

5. **Multi-appointment lookup.**
   `lookup_patient` returns the first match when a number has several future bookings; no
   "no → offer the next one" path in v3. Low frequency (most patients have one appt) and SAFE
   (degrades to human transfer, never a wrong cancel — confirm gate protects). `lookup_patient`
   + prompt change. Post-launch.

### TIER 4 — Deferred backlog (regression-risky or structural; don't rush)
6. **TTS out-of-order chunk-drop + `tts_inhibit` eating a genuine answer → dead air**
   on same-breath barge-ins. **General `connection.py` pipeline behaviour (hot path = real
   regression risk)**; backstops (`_ooo_force_fire`, 10s safety re-ask) already recover it.
   Worst case today = awkward truncation or ~10s silence-then-reask, not a hang.
   Leave until a dedicated instrumented session.

7. **Full legacy-block retirement (the "proper" version of Tier 1).**
   Retire the stale `_cancel_reschedule_block` flow so v3 owns cancel/reschedule cleanly →
   removes latent risk of other legacy leaks (old `lookup_appointment`, upfront full-name ask).
   **Prereq:** confirm v3 has a "couldn't find your appointment" not-found/retry path before
   removing legacy RC2; preserve the STT-garble rule (counsel/console/cancle→cancel) +
   booking-vs-cancel ambiguity prompt. Do after the demo with a full re-verify.

### TIER 5 — Cosmetic (whenever, lowest priority)
8. **"Awlstuh" pronunciation.** Log shows `pronunciation_dict.json missing id/version_id — run
   scripts/setup_pronunciation_dictionary.py`. Setting it up fixes Alcester's pronunciation. Polish.
9. **`patient_name="Unknown"` placeholder → name-reminder SMS** (first==last trips the
   placeholder check). Booking still carries the looked-up real name; cosmetic.

---

## 3. SEQUENCING
- **Mon:** Tier 1 (both) + `SESSION_SECRET` → one full reschedule+cancel re-verify on a settled build.
- **Tue:** buffer / second verify call only. No new changes, no deploys.
- **Wed:** demo on the settled build; fall back to `release-v1` if needed.
- **Real-patient window (after Wed):** Tier 3 #4 → #5 → Tier 4 #7 → Tier 5 anytime.

## 4. DISCIPLINE (carry every change)
- Isolated, individually-revertable commits; push after each (push = deploy).
- Regression-weigh every change before implementing.
- NO test calls during a deploy — wait for Live + ~60s quiet (3 calls were contaminated this
  morning by mid-deploy restarts; a port-10000 boot mid-call voids the result).
- gate5 (`turn_handler.py`) overrides the prompt — if a prompt change "doesn't take", check the
  banned-phrase filter AND check for a conflicting duplicate prompt block first.
