# Job 2 Wave 1 + Job 1 — synthesis for Quentin

**From:** Jules  
**Date:** 2026-08-16  
**Dial (Job 2):** `+447367002651` · build `cf0b516`  
**Detail / SIDs:** [`JOB2_WAVE1_FINDINGS_2026-08-16.md`](JOB2_WAVE1_FINDINGS_2026-08-16.md) · call sheet [`ADVERSARIAL_SESSION_2026-08-15.md`](ADVERSARIAL_SESSION_2026-08-15.md)

---

## Verdict

**Adversarial Wave 1 (A1–A10) is complete.** No diary-corrupting failure on JV — Susie survived interrupters, mind-changers, FAQ dodges, name corrections, time-vs-duration traps, impossible hours, withheld caller book+move, and quit-at-various-points outcome checks.

What we found are **edge UX / barge-in / silence-handler** bugs. They are real (callers get confused or wait in silence) but not “wrong appointment written.” **Batch 1** is queued to fix them next.

**Separately (Job 1):** Emma Clifton’s theorem reschedule/cancel mess is **fixed and ported** (`ffceb94` → theorem `02fd991`). Acuity already showed the intended end state — **no patient callback**.

Job 3 remains closed; your env plate (sheets / digest) unchanged.

---

## Wave 1 at a glance

| | |
|---|---|
| Scripts run | A1–A10 (+ one A9a retry) |
| Hard FAILs that stayed failed | **0** (A9a first attempt flaked; retry PASS) |
| Diary wrong-writes found | **0** |
| Soft / UX defects to fix | **4** in Batch 1 |

Highlights that behaved well: reason question latch (A4), name correction to diary (A6), “30th” as date not duration (A7), 8:30 last slot honesty (A8), quit labelling when nothing booked (A10a–c).

---

## Batch 1 (next engineering)

Canonical-first → `jv_v2` (and shared paths on theorem).

1. **Withheld keypad** — say *why* before “type on keypad”  
2. **After move/book CTA** — clear stale slot-selection flag; don’t re-ask “which day?” after a write; re-speak confirm if barge-cut; short “anything else / take care” close  
3. **Greeting** — don’t let early “hi”/noise steal the opening  
4. **Phone readback** — slow the digit rattle (TTS pacing, not prompt theatre)

Full cause notes + SIDs in the findings doc.

---

## Your plate (unchanged)

1. `SHEETS_ENABLED` / JV sheet finish  
2. Digest `email_to`  
3. Optional: clear leftover `EVAL_STAFF_SMS_TO` on VE  

No need to chase Emma. Optional later: mutation Wave 2 of A1–A10.

---

## Docs housekeeping (this pass)

Stale dated sweeps/sessions moved under `docs/plan/archive/`. Live index: [`README.md`](README.md). Working set is handover + Job 2/3 synthesis/findings + acceptance registers.
