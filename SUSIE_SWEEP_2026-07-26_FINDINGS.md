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

## Not run tonight (gate halt)
Blocks B, C, D and all desk-work items — deferred. No clean sweep tonight; code lands Monday.
