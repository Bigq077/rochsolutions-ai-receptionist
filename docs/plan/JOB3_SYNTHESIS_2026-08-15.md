# Job 3 synthesis — Joint Venture (`jv_v2`)

**For:** Quentin  
**From:** Jules  
**Date:** 2026-08-15  
**Dial:** `+447367002651`  
**Heads at close:** `latency-eval` **`bcfd1e1`** · `jv_v2` **`cf0b516`**

Working tracker (call SIDs + SHAs): [`JOB3_STATUS_2026-08-14.md`](JOB3_STATUS_2026-08-14.md).  
Handover source: [`HANDOVER_JULES_5DAY_2026-08-11.md`](HANDOVER_JULES_5DAY_2026-08-11.md) §Job 3.

---

## Verdict

**Job 3 engine work is done.** Every CAce1457d1 voice/booking defect that needed a code fix was landed canonical-first, ported to `jv_v2`, and call-proven. What remains is **your config** (sheet + digest recipient), not more engine work. **3d** live stall and **3b** live SMS were skipped on purpose.

Adversarial 50 dials stay parked until you want them resumed.

---

## Call-proven on `jv_v2`

| Item | What was wrong | Fix (short) | `jv_v2` | Call |
|---|---|---|---|---|
| **3c.4** | Day-only “friday” → ~3s silence (hold clip never armed) | `day_preference` + extract arms hold clip | `2969ccc` | **PASS** `CA8a08105defd4d96477a559d2d8eb8b82` |
| **3a** | After bare `Right —`, always asked day/time before reason | Booking-ack injector now respects `prompt_facts.reason_question` | `1537fa8` | **PASS** `CA47a74aeca19e068ac775a3b429ec6878` |
| **3c.5** | “That’s a time preference noted — …” | Gate 5b strip + ACKNOWLEDGEMENT RULE | `8adce54` | **PASS** `CAd15c3af6fc73480fe9dbe2df81c7bd6d` |
| **3c.1** | Accept slot → duplicate Acuity / re-list → accept twice (~24s) | Spec I keeps cache while awaiting selection; accept → `slot_offer_still_live` | `c8dcd05` | **PASS** `CA5db4ea0cf96d4c4ea272d5c8dad80315` |
| **3c.2** | Out-of-window offer with only “does that work?” | Gate 5 kept “closest … to [window]”; template requires naming their window | `a9155dd` | **PASS** `CAc875c27a7500df568f0942e81281fe63` |
| **3c.3** | Empathy/physio line generated then `_pre_slot_cancelled` dropped it | **Clinic gate only:** `prompt_facts.keep_pre_slot_speech` on jv_v1 — engine suppress unchanged elsewhere | `cf0b516` | **PASS** `CA2d123721b0b3c08da2c8fa38929f71a5` |

Canonical twin commits (revert targets on `latency-eval`): `445fff1` → `8c533d4` → `6d7c2ec` → `e0d914e` → `14eba0e` → `31624c8` (docs pins interleave).

---

## Verified without a live flip

| Item | Result |
|---|---|
| **3b** SMS call-mode toggle | Suite green (~36). **Do not** set `SMS_ENABLED=true` while you’re away — you asked for that. `call_overflow.enabled=false`; Marcus `+447586605462`. |
| **3d** booking-outcome fallback | Code on `jv_v2` (`_booking_outcome_unspoken`); `test_booking_outcome_fallback.py` **15 passed**. Live proof needs a provider stall (Persona 10) — **skipped**. |

---

## Your plate (not Jules)

These are env / ops, not missing code. Theorem already proves the sheets path.

1. **`SHEETS_ENABLED=true`** on the JV Render service (and VE when you want records there) — after JV’s sheet is finished. Until then: no call rows land in Sheets.
2. **`operational.digest.email_to`** (or `DIGEST_EMAIL_TO`) — blank on jv_v1 today → end-of-day Carepatron digest **skips**. Needs a real recipient + SMTP.
3. Optional later: unset any leftover **`EVAL_STAFF_SMS_TO`** on VE if still set from callback testing (French geo blocked Twilio).

---

## Explicitly out of scope / parked

- Adversarial ~50 / Wave 1 sheet (`ADVERSARIAL_SESSION_2026-08-14.md`)
- Live **3d** stall chase
- Live **3b** SMS exercise
- Refactors of `flow.py` / broad cleanup

---

## If something regresses

| Symptom | Likely revert on `jv_v2` |
|---|---|
| Hold silence after bare day name | before `2969ccc` |
| Reason asked after timing again | before `1537fa8` |
| “time preference noted” returns | before `8adce54` |
| Double accept / long Acuity on “that works for me” | before `c8dcd05` |
| Silent out-of-window “does that work?” | before `a9155dd` |
| Empathy gone again before slots (JV only) | before `cf0b516` / flip `keep_pre_slot_speech` off |

Canonical-first still applies for any new engine fix.
