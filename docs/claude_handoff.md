# Claude Handoff / Continuity

> **START HERE if you are a fresh Claude session with no memory of prior work.**
> This is the durable, in-repo record so context is never lost between sessions.
> Keep it updated at the end of every session. Newest status at the top.
> (Human-facing counterparts: [playbook.md] = how we work · [fix_booklet.md] =
> per-fix exchange record for Jules ↔ Quentin · [dev_notes.md] = Jules's personal
> notes + log-cleaning command · [sweep_findings.md] = the source-of-truth backlog.)

---

## Who / what
- **Project:** Susie — an AI phone receptionist (clinic: **Theorem Health**, config id `theorem_v3`).
- **People:** **Jules** (the dev in the chair; git author "Jules Decorps") and **Quentin**
  (partner, often away — the booklet is how they exchange progress).
- **Work in progress:** remediating findings from a 14-call production sign-off sweep.
  Branch: **`investigate/susie-call-flows`**. Main branch: `main`.

## How we work (non-negotiable — full detail in [playbook.md])
Standing instruction from Jules, paste-frame every session:
> *"Professional engineer on a live, regression-sensitive codebase. Steady verifiable
> progress, not speed. Smallest correct change, root cause before editing, never modify
> code I didn't ask for."*

Per fix, in order:
1. **Diagnose read-only first.** Name the exact file+function and the minimal edit; get
   agreement before touching code. `flow.py` / `connection.py` are ~25k lines and
   interconnected — they regress easily.
2. **TDD is mandatory** — write a failing test, then the fix, then green. Then full `pytest`.
3. **One small commit per fix** (fix+test together; docs/booklet separate). **Jules pastes
   the git commands himself** — give him ready `git add` + `git commit -m … -m
   "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"` blocks; do NOT run them.
4. **Phone re-test on staging is the real sign-off gate** (number `+447366263180`).
   ⚠️ **Staging runs the DEPLOYED commit, not the working tree** — a call only exercises a
   change after commit → push → Render redeploys. Order: commit→push→deploy→call, THEN the
   next fix. No batching.

**Log-cleaning command** (Jules copies the Render log, then runs this to strip noise onto the
clipboard; also stored in [dev_notes.md]):
```bash
pbpaste | grep -vE 'httpx|raw slot\(s\)|barge-in: partial|Redis (read|write) error' | pbcopy
```

## Gotchas that will waste your time if you don't know them
- **~90 pre-existing failing tests on this branch** (test_name_collector, test_silence_handler,
  test_sms_templates, test_soft_context, test_returning_treatment_plan_exit, + 2 in
  test_dead_air_safety_net). They PRE-DATE our work — confirm via stash-compare. A clean fix
  keeps the count at 90 (plus its own new passing tests). Don't chase them.
- **Acuity is NOT isolated on staging** — a real `book_appointment` creates a REAL appointment
  on Mark's Acuity. When testing booking, drive to the confirm but **never say a clean "yes"**;
  🟡 hang up first.
- **gate5** (`turn_handler.sanitise_response`) strips banned phrases per-chunk — it will silently
  eat LLM prose (openers, "bear with me", etc.). Deterministic lines must NOT rely on gate-able
  LLM output.
- `medical_emergency_detected` is never set on the LLM red-flag path — the 999 text is
  model-generated. Key off `last_bot_prompt` content instead.
- `TRANSFER_DISABLED` (staging kill-switch) suppresses the live dial + heads-up SMS; the
  transfer TTS line still plays (that's how F17 is phone-verifiable on staging).

## Status — as of 2026-07-04 (v2 resolver, gap 1/3 done). Ship target: Monday 2026-07-06.
**8 sweep fixes + v2-1 SIGNED OFF (TDD + phone-verified), all validated in the full 14-call sweep:**
- **F13** — no booking-CTA on pure-FAQ answers. Prompt-only (susie_system_prompt.py ~L2292); a
  deterministic gate was tried and **reverted** (regressed concern-turn CTA).
- **F14** — bank-holiday/closure FAQ no longer triggers "which clinic?" (`_faq_needs_clinic` +
  `_FAQ_CLINIC_INDEPENDENT_RE`, connection.py).
- **F17** — deterministic G18 transfer line at `_on_transfer_request` choke point.
- **F20** — `book_appointment` requires a clear YES (`_book_confirmation_ok`, llm_stream.py).
- **F23** — calm 999 re-anchor instead of chirpy reset (`_emergency_reask_override`).
- **F24/F26** — practitioner-deflection scoped to clinical only; logistics answered
  (online→theoremhealth.co.uk, phone/video→in-person) via get_clinic_info.
- **F25** — Wellness Massage + Psychotherapy stated Awlstuh-only; generic massage clarifies.
- **Clinic-loop escape hatch** — `_location_ladder_exhausted`: after the keypad rung, a further
  unrecognized utterance breaks out to the LLM instead of looping the keypad. **PARTIAL** (see v2).

**14-call sweep COMPLETE** — safety spine (Calls 6, 10) + canonical facts (Call 4) + all fixes
verified. Full result table + verdict at the bottom of [fix_booklet.md].

**v2 clinic-resolver (Group 4) — IN PROGRESS. Gap 1 of 3 SIGNED OFF (2026-07-04).**
- ✅ **v2-1 Indifference → default clinic** — "whichever/either/you pick/whatever's easiest/both/
  I don't mind/doesn't matter" now resolve to Alcester instead of re-asking. Deterministic
  `_is_location_indifference()` + `_LOCATION_INDIFFERENCE_RE` + `_DEFAULT_CLINIC` in connection.py;
  one guard before the `_resolved != "unknown"` check reuses the tested resolved path. Test:
  `tests/test_location_indifference.py` (39). Commit `c04c45c`. Phone-verified (call `CA5fa771b2…`).
- ⏭ **v2-2 Sticky re-ask [NEXT — highest remaining value]** — after the escape hatch clears the
  gate, `booking_flow_active`+unresolved clinic re-fires the clinic question on the next utterance
  (`location gate fired — intent=booking`). Escape breaks one loop; the question returns. Start
  read-only in the escape-hatch block (connection.py ~7442) + the `location gate fired` condition
  (~6090) to see why the gate re-arms.
- ⏭ **v2-3 Spoken-clinic friction** — a clear "redditch"/"this clinic" sometimes still needs the
  ladder; inconsistent ("alter" resolved cleanly in Call 8). Lower severity, resolver-tuning pass.

**Then / deferred:**
- **F21** — long/un-bargeable TTS (8–18s, EVERY slot/clinical/objection call — biggest UX drag).
  Needs a design call (split/shorten vs allow barge after sentence 1 on clinical turns).
- **Infra / Quentin (not Susie code):** I2 `/twilio/status`→prod 403; I5 `GOOGLE_SERVICE_ACCOUNT_JSON`
  invalid on staging; **Acuity isolation** (a real `book_appointment` books a real appt).

**Loose observations logged (not yet fixed):** Turn-2 location detour ("alcester"→intent=booking,
STT-driven → Group 4); clunky booking-correction (multiple check_availability blocks + long TTS →
F21); stray call-start TTS fragment seen once in the F23 log.

## Where the truth lives
- **Backlog / root causes:** [sweep_findings.md] (FINAL COMPILE section).
- **Per-fix record:** [fix_booklet.md] (one entry per finding; symptom→root cause→fix→test→phone result).
- **How-to / cadence:** [playbook.md].
- **Jules's notes + log command:** [dev_notes.md].
- Private Claude memory (outside the repo): `.claude/projects/…/memory/` — `susie-fix-workflow`,
  `susie-project-state`.
