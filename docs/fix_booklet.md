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

## F17 — Silent transfer: verbatim G18 line not spoken  ⚙️ code done · re-verify pending
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
- **Phone (PENDING — after deploy):** staging `+447366263180`. Because `TRANSFER_DISABLED` is
  set on staging, the **dial stays suppressed but the TTS line now plays** — that's the point.
  1. Press **1** at the greeting → must HEAR *"Putting you through now — please stay on the
     line."* then `[realtime] transfer SUPPRESSED — TRANSFER_DISABLED set` (no dial, no SMS).
  2. "Can I just speak to someone?" → same verbatim line spoken, then SUPPRESSED.
  Log: expect the line queued to TTS on both; ZERO `Messages.json` / dial to +447870166861.

### Commits
- Fix + test: _(pending)._
- Booklet: _(pending)._
