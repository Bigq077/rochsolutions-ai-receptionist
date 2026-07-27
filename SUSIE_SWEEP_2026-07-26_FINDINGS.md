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

## Not run tonight (gate halt)
Blocks B, C, D and all desk-work items — deferred to the restarted sweep after the fix
is verified on a live call.
