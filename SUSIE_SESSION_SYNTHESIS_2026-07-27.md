# Susie — Session Synthesis for Quentin (2026-07-27, overnight)

Everything that happened this session, so you're fully up to date. Branch:
`latency-eval`. All work below is **committed, pushed, and live-verified** unless
marked otherwise.

---

## TL;DR
- Sunday-night **verification run** (2026-07-26 sheet) **failed its gate**: the demo
  happy-path couldn't book — an infinite phone-read-back loop, then a phantom
  "all booked".
- Per your "by the book" call, we **rolled back** to `d60041d` (loop gone). You
  then **green-lit re-fixing in-window**, so we re-applied the afternoon rework
  and landed the fixes properly, ran the **full sweep**, batched findings, and
  shipped + **live-verified the P1s**.
- **Net result: the demo booking path is fixed and verified** — books for real,
  no loop, no phantom, screens engage. Two items consciously **deferred**.

---

## What's on the engine now (all committed + verified)

| Fix | Commit | What | Verified |
|---|---|---|---|
| Phone-confirm E.164 fallback | (in `924bbcf` restore) | non-UK caller-ID (`+33` test line) can voice-confirm — was the root of the loop (`twilio_from_local` is `+44`-only) | ✅ live |
| Phone-confirm "it is"/"that's it" | `4c95c95` | natural yes/no answers accepted at the number read-back | ✅ live |
| F-023 phantom guard | `17d90e7` | Gate 5f now catches bare "all booked" ("All booked — you're in for…"); excludes "all booked **up**" | ✅ unit; live via V2/V3 |
| **RS-06 endpointer** | `41b8b97` + `201596d` | holds a mid-clause fragment (ends on continuation word) and merges with the next final, so a thinking pause doesn't finalise a fragment. Window 2.5s. Complete turns keep today's latency. | ✅ live (operator: "feels faster"; merge seen on dvt-2) |
| **DVT/F-017 arming** | `29e3f9b` | DVT screen now arms on the **symptom combination** ("swollen warm/red/hot") not just the calf-word — survives `calf`→`call`/`cough` STT mangling; benign "call me back"/"a cough" verified inert; negation still clears | ✅ live (dvt-1/2/3) |

**Live verify highlights:** 3 clean bookings (V1/V2/V3) with real calendar events,
no loop, no phantom; endpointer safe on normal bookings (no dropped/merged turns);
DVT `POSITIVE`+escalate on a mangled-calf symptom combo, `clear` on negation, no
fire on "call me back".

Booking calendar isolation confirmed throughout: events land in the demo calendar
`63bc844e…`, not live JV.

---

## The rollback/re-fix trail (for history)
`de426a6` (your afternoon rework) → verify **gate failed** → reverts `a0e87c9`,
`2f56cae`, `e6c235c`, `9278275` (rollback to `d60041d` behaviour, docs kept) →
you green-lit re-fix → restore `29d072d`, `a9a60f7`, `ededf04`, `924bbcf` → fixes
`4c95c95`, `17d90e7`, `41b8b97`, `201596d`, `29e3f9b`. Full suite unchanged vs the
~93-failure baseline throughout (those are the known pre-existing drift, none on a
safety/booking path).

---

## Deferred / post-demo backlog
Full detail + repros in **`SUSIE_SWEEP_2026-07-26_FINDINGS.md`** (batched by priority).

- **RS-02 reason-guard (P1, DEFERRED).** For jv_v1 the booking reason only ever
  comes from the model's `args["reason"]` (no caller-sourced slot on the LLM path),
  so preventing a fabricated reason needs **grounding against `obs_turns`** — which
  risks false-blocking a legitimate paraphrase (happy-path regression). Only fires
  on a caller who *deliberately refuses* a reason. Post-demo fix + verify.
- **RS-06b (P2).** SILENCE_WATCHDOG "take your time" still cuts in on pauses **after
  complete phrases** (separate mechanism from the endpointer).
- **F-035 (P2).** Filler clip `audio_clips/filler_checking.ulaw` still missing →
  dead air on non-tool turns.
- **RS-04 (P2).** Declining the caller-ID and reading an alternate number aloud →
  the spoken number isn't captured (reverts to caller-ID).
- **RS-05 (P2).** Keypad + read-back can stall.
- **F-036 (P3).** Router logs "booking confirmation SMS already sent" even though
  SMS is off and nothing is sent — misleading log only.

---

## Housekeeping done
- Removed empty `DEMO/` stubs (`a07ad94`).
- **Archived 12 superseded/other-clinic working docs** to `docs/archive/` (`498dd01`)
  — root went 21 → 9 `.md` files. CLAUDE.md §5 references repointed; nothing dangles.
  Live docs kept at root.
- Obs: capture live on `demo_obs` (fresh DB; not the old `susie-obs`). Every call
  writes a row. **Rotate/bin the `susie-obs` + `demo_obs` passwords after the demo**
  (they were pasted in chat; throwaway DBs).

---

## State right now
- **Branch `latency-eval`**, head after this doc's commit; pushed, in sync.
- Demo service redeploys on push (docs+fixes are live).
- **Demo booking path: green and verified.** Screens: cauda/serious_spinal/
  trauma_fracture/emergency all deterministic; DVT now robust.
- Not touched: RS-02 and the P2/P3 backlog above.

## Suggested next steps (yours to call)
1. A fresh **clean-run** of the sign-off matrix at demo time-of-day (Mon/Tue) now
   the booking path is fixed — toward the 3-clean-runs freeze criterion.
2. Decide RS-06b / F-035 (dead-air-visible) before freeze if there's time.
3. Confirm the C3c listen-back (was the model's escalation real or cut off?) — the
   one open safety question from the sweep.