# Susie Verification Run — 2026-07-26

**Run sheet:** `docs/plan/JULES_SWEEP_2026-07-26.md`. **Code under test:** `de426a6`.
**Outcome:** Block A gate FAILED on the first calls → sweep halted per the gate rule
→ root-caused → **fixed** (Quentin green-lit fixing in-window) → to be re-verified,
then the sweep restarts from 0.

---

## BLOCK A GATE — FAILED (0/3 booked)
Three happy-path attempts, natural delivery, `+33` test line. **No booking completed.**
Susie read the caller-ID back — *"is that the best number for the booking?"* — the caller
said "yes", she re-read it and asked again: **23 read-back asks across 2 calls**, never
booking. SIDs `CA4f929d5e42908d481c5ac0aa9ead9141`, `CA44b1b076f739fb547f3fd887df8a4c85`.

## Root cause (verified in code, not theorised)
- `connection.py:5421` sets `twilio_from_local` **only** for `+44` caller-IDs. The `+33`
  test line left it **empty**.
- The verbal phone-confirm interceptor (`v3_phone_dtmf_active` branch) took the number to
  store from `twilio_from_local` alone → empty → the `_caller_num and _is_use_this_number(...)`
  guard was falsy → the caller's "yes" fell to the conversational-exit branch →
  `phone_confirmed` never set. (`_is_use_this_number("yes")` was never the problem.)
- `f302ddb` (shipped the same afternoon) now hard-requires `phone_confirmed is True` →
  `[book] BLOCKED — phone not confirmed (A1)` → model re-read the number → unbounded loop.

**Scope note:** substantially exposed by the **non-UK test number**. A real UK `+44`
caller would have `twilio_from_local` set and would likely confirm on "yes" — *unverified*
tonight (R3). But the loop still (a) blocks the whole sweep since the tester is on `+33`,
and (b) is a genuine brittleness for any non-UK caller-ID.

## Fix — `073e563`
`_confirm_caller_number(session)`: prefer the UK-local form, **fall back to the full E.164
caller-ID** when it's absent, so any caller-ID can be voice-confirmed. Applied at both
verbal-confirm sites (connection.py ~6037, ~6139). **UK path unchanged** (local form still
wins) → only non-UK IDs affected. TDD: `tests/regression/test_phone_confirm_non_uk_caller_id.py`
(4 pass; was red on the missing helper). Existing phone-confirm + keypad tests: 60 pass.

## Separate, NOT fixed (cosmetic, noted)
The read-back also mangled the number: `+33617769867` spoken as "0 3 3 6 1 7 7 6 9 8 6 7"
(leading 0 prepended to the E.164). Storage is now correct (clean E.164); the *spoken*
read-back wording is a separate LLM/prompt formatting issue. Not blocking; UK numbers read
the clean local form.

## Next
1. Deploy `073e563` (Quentin green-lit) → wait green + 5 min.
2. **Phone-verify:** one `+33` call → reach the read-back → "yes" → confirms + books, no loop
   (`phone_confirmed=True`, booking success, `[obs.store] captured`).
3. Only then **restart the sweep from 0** (Block A ×3 → gate → B/C/D per the run sheet).

## VERIFY CALL — FAILED (phantom booking, F-023 reborn) · call `CA77eebe…`
The post-fix verify call did **not** book, despite Susie saying *"All booked — you're in for
Wednesday the 29th"* **three times**:
```
book_appointment BLOCKED ×2   ·   success/calendar-event lines: 0
outcome=abandoned · collected=None · Row built name=John Smith · obs turns=18
```
Three things went wrong on the one call:
1. **Phone confirm phrasing gap.** Caller answered "is that the best number?" with **"it is"** —
   not in `_PHONE_CONFIRM_AFFIRMATIVES` — so it exited as conversational, number not confirmed.
   `073e563` (E.164 fallback) is correct but only helps recognised affirmatives ("yes/yeah/sure");
   "it is" slips through.
2. **Phantom "All booked" (F-023).** book_appointment was BLOCKED (confirmation-question gate saw
   `last_bot_prompt="Take your time."`) yet the LLM claimed success anyway. Exactly the B1 risk the
   run sheet flagged.
3. **Endpointing (C23) was the proximate trigger.** "do you think…" fragmented into `do`/`you`/
   `think` → "Take your time" became the last prompt → blocked the book → hallucinated success.

## Did F-023 come from this afternoon? — investigated
- The phone **loop** = yes, this afternoon (`f302ddb`'s hard phone requirement + `+44`-only
  `twilio_from_local`).
- The **phantom** = NOT a change to the guard. None of `f302ddb`/`28ff14b`/`de426a6` touch the
  success-language guard `8631fc3` or the confirmation gate; `8631fc3` is an ancestor of `d60041d`
  so the rollback keeps it unchanged. Last night (pre-afternoon) every "All booked" had a real
  booking; tonight "All booked"×3 had 0 bookings. The afternoon flow rework + added blocks + the
  pre-existing endpointing bug made a *new path to the phantom reachable* — they didn't break the
  guard.
- **Rollback consequence:** `d60041d` fixes the loop and returns to last night's clean-booking
  flow (phantom goes away in practice), but does NOT close the guard gap or the endpointing trigger
  — both survive the rollback. **Re-verify a booking after rollback.**

## Decision: ROLLBACK to `d60041d` (Quentin — by the book)
`073e563` (my phone fix) is unwound by the rollback along with the afternoon commits — fine, since
the loop it addressed is removed with `f302ddb`. Monday must-fixes: re-do the booking rework +
phone-confirm phrasing ("it is"), harden F-023 on the new refusal paths, and the endpointing (C23).

## UPDATE — Quentin green-lit in-window re-fix; sweep re-run (2026-07-27 early hrs)
Rollback (above) shipped, then Quentin authorised re-fixing tonight. Re-applied the 4 afternoon
commits and landed 3 fixes (all TDD, full suite no new failures vs baseline):
- **Fix #1** `_confirm_caller_number` E.164 fallback (non-UK caller-ID) — commit in 924bbcf lineage
- **Fix #2** `4c95c95` phone-confirm accepts "it is"/"that's it" at the read-back
- **Fix #3** `17d90e7` F-023: catch bare "all booked" phantom (excl. "all booked up")

**Phase 2 verify (V1/V2/V3):** all booked for real (`success:true` + calendar event), no loop, no
phantom. V2 clean (single ask). V1 friction traced to name/STT mangling, not the fix.

### Re-sweep findings tracker (BATCH — fix by priority AFTER the full sweep, R1)
| ID | Sev(draft) | Where | Finding |
|---|---|---|---|
| RS-01 | P2 | V1 | Name-capture fragile to STT mangling ("Tom Green"→"home green"/"like the color"); name-correction **exits DTMF**, so phone-confirm answers arrive out of state → friction/blocks before it recovers |
| RS-02 | **P1?** | B1 | Booked despite caller **deflecting on the reason** — `reason:"shoulder pain"` appears **model-supplied** (caller never clearly stated it; 2 turns garbled). `f302ddb` reason-guard bypassable by an inferred reason. **Listen-back needed** (did caller say anything reason-like?) |
| RS-03 | P2 | B2 | Phone-confirm **doesn't recover** from an unusable answer + silence: `phone_confirmed=None`, 4 blocks, caller abandoned. (No phantom — guard held, `ms_gate5f`=0, no false "all booked".) |
| — | note | B1/B2 | F-023 not yet cleanly live-exercised (B1 booked real; B2 never claimed booked). Unit-green 47/47. |
| RS-04 | P2 | C1a | Verbal **alternate** number ignored: caller declined caller-ID and read "07700 900123" aloud, but `collected.phone` kept `+33617769867` (the caller-ID). Spoken alternate-number entry not captured. |
| RS-05 | P2 | C1b | Keypad entry + readback stalls: keypad captured `07368306992` but booking did not complete (0 success, 4 readback-asks — looped on confirm). |
| RS-06 | **P1** | all C | Endpointing (C23) **severe** — barge/talk-over partials per call: C1a 85, C1b 49, C2a 44, C2b 41; confirmations split across turns (C2b "that is the best number" / "for the booking"). Biggest single degrader; contaminates RS-01/03/05. |
| RS-07 | P2 | operator note | Number **read-back repeats on every re-ask** (C1b ×4) instead of once. Fix: latch `readback_done`; re-read ONLY if caller changes/rejects the number; never after `phone_confirmed=True`; never on an unrelated re-ask (name/surname/slot). |

### Block D — all PASS, no new findings
D1 no over-screening (0 clinical_screening) · D2 "fifty-two pounds" spoken as words · D3 waited
through 5s silence (0 talk-over) · D4 barge-in stopped promptly both times (operator-confirmed).

### Desk work
- **C3c (safety) — inconclusive/concerning.** She began "…with a sore calf and recent surgery…"
  (naming both DVT risk factors) but the caller hung up mid-reply (abandoned) — no explicit
  escalation reached; deterministic screen still didn't arm (calf→call). Model-only + cut off.
- **F-035 — OPEN.** `filler_guard] clip not found: audio_clips/filler_checking.ulaw` in tonight's logs.
- **F-036 — benign.** `SMS_ENABLED is off — suppressed` ×2 (nothing sent), but router logs
  `✅ booking confirmation SMS already sent` — misleading wording only.

---

## SWEEP COMPLETE — BATCHED BY PRIORITY (fix in this order)
**P1 — demo-blocking / integrity / safety**
- RS-06 Endpointing (C23) severe — biggest degrader, contaminates RS-01/03/05. Deep fix.
- RS-02 Reason-guard bypass — booked with model-supplied reason after deflection (needs listen-back).
- DVT screen doesn't arm (C3c/F-017) — STT calf→call; model-only backstop. Carried.

**P2 — phone-collection/readback cluster (high-leverage: fix together)**
- RS-07 readback repeats (should be once) · RS-01 name-correction exits DTMF ·
  RS-03 no recovery from unusable answer · RS-04 verbal alt-number ignored ·
  RS-05 keypad+readback stalls · F-035 filler clips missing (dead air).

**P3 / note**
- F-036 misleading "SMS already sent" log (nothing sent).

**Recommended fix order:** (1) the phone-collection cluster [RS-07+RS-01+RS-03+RS-05] as ONE
bounded, testable change (readback-once latch; don't drop DTMF on name-correction; recover not
re-block). (2) RS-02 after a listen-back. (3) RS-06 endpointing — decide attempt vs work-around
(compact demo delivery). (4) RS-04, F-035, F-036 as time permits.

---

## FIX LOG (2026-07-27, in-window, Quentin-authorised)
- **RS-06 endpointing — SHIPPED `41b8b97` + hold-window 1.8→2.5s.** Continuation-word endpointer:
  holds a mid-clause fragment (ends on conjunction/prep/article/possessive) and merges with the
  next final; complete turns keep today's latency. Helper unit-tested (20 cases). **Live verify:
  regression CLEAN** (normal bookings unaffected, no spurious holds, both booked; operator: "feels
  faster"). Safe + net positive.
- **RS-06b (NEW, P2) — SILENCE_WATCHDOG "take your time" still cuts in on pauses after COMPLETE
  phrases** (rs06-1: fired 7× in one call). Separate mechanism from the endpointer; not addressed.
  Fix later: stop it repeating and/or relax grace when the caller is actively engaging. Deferred.
- Next: DVT/F-017 screen arming, then RS-02 reason-guard.
