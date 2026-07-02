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

## F13 — Booking-CTA appended to pure-FAQ answers  ✅ code done · phone re-test pending
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

### Fix — two commits (gate first for a hard guarantee, then prompt to remove the contradiction)

**Commit 1 — deterministic gate (TDD).**
`app/media_streams/turn_handler.py` → `sanitise_response`, Gate 5c.
Was: strip the trailing booking-offer CTA only when `booking_flow_active`.
Now: strip it **unless it's a concern turn** — i.e. keep the CTA only when
`booking_flow_active is False AND v3_treatment_mentioned is True`. The existing
**whole-response guard** still protects a standalone booking question / the closing
confirmation ("shall I go ahead and book that in?") from being stripped to empty.
- Discriminator logic: pure informational FAQ = neither flag set → strip.
  Concern turn = `v3_treatment_mentioned` → keep (Calls 9/12/14 want the offer).
  Mid-booking = `booking_flow_active` → strip redundant tail (unchanged).

**Commit 2 — prompt reconciliation.**
`app/prompts/susie_system_prompt.py` around [:2292](../app/prompts/susie_system_prompt.py#L2292).
Added an explicit exclusion so the "close with a CTA after an FAQ" rule now agrees
with the 1141/1467 rule: purely INFORMATIONAL questions (prices, hours, parking,
location, directions, services) get **no** CTA — end with "Is there anything else I
can help you with?" instead. Offer booking only on a described concern/injury or an
explicit booking ask.

### Test (TDD) — `tests/test_faq_booking_cta.py` (new, 5 tests)
- `test_faq_price_answer_strips_trailing_cta` — RED before, GREEN after (F13 core).
- `test_faq_parking_answer_strips_trailing_cta` — RED before, GREEN after (F13 core).
- `test_concern_turn_keeps_booking_cta` — guard: `v3_treatment_mentioned` keeps the offer.
- `test_standalone_booking_question_kept` — guard: whole-response CTA is kept.
- `test_booking_flow_active_still_strips_redundant` — guard: existing mid-booking behaviour unchanged.

### Verification
- **Automated (local, no deploy needed):** 5/5 F13 tests pass. Full suite = **90 failed
  / 1002 passed** with the fix vs **90** pre-existing at baseline → **0 new failures,
  0 regressions**. Prompt module imports cleanly after the edit.
- **Phone re-test requires a DEPLOY first.** Staging runs the **deployed** commit, not
  the working tree — a phone call only exercises a change once it is
  **committed → pushed → Render has redeployed** staging. Order: commit both fixes →
  push → wait for Render deploy → 10s STT smoke call → then run the calls below on
  staging `+447366263180`.
- **Phone (PENDING — Jules to run after deploy):**
  1. "How much is a session?" → £85 answer, **no** "would you like to book?" — ends
     "Is there anything else I can help you with?"
  2. "Do you have parking?" → clinic gate → parking answer, **no** booking push.
  3. Control: "I've hurt my shoulder" → concern response **should still** offer an
     assessment (CTA must NOT disappear here).
  Log check: `[ms_gate5] removed out-of-place booking offer (booking_flow_active=False, concern=False)`
  on FAQ turns; absent on the concern turn.

### Commits
- Commit 1: _(pending — gate fix + test)_
- Commit 2: _(pending — prompt reconciliation)_

### Notes / blast radius
- Gate 5c now runs on more turns (any non-concern turn, not just mid-booking). Mitigated
  by the whole-response guard (keeps standalone booking questions) and by
  `_BOOKING_OFFER_RE` only matching explicit *offer* phrasings, not booking-flow
  questions ("which clinic?", "what day or time?").
- Per-chunk limitation (pre-existing): if the CTA is split across TTS chunks the regex
  may not match within one chunk. Not introduced here; watch on phone re-test.
