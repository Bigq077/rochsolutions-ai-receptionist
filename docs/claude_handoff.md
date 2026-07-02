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

## Status — as of 2026-07-02/03
**Today's 4-item safety spine — ALL SIGNED OFF (TDD + phone-verified):**
- **F13** — no booking-CTA on pure-FAQ answers. Prompt-only fix
  (susie_system_prompt.py ~L2292). NOTE: a deterministic gate was tried and **reverted** for
  regressing the concern-turn CTA (`v3_treatment_mentioned` doesn't fire on plain injuries).
- **F17** — deterministic G18 transfer line ("Putting you through now — please stay on the
  line.") emitted at the `_on_transfer_request` choke point (connection.py). Prod TwiML `<Say>` kept.
- **F20** — `book_appointment` requires a clear YES (`_book_confirmation_ok` in llm_stream.py,
  reusing fast_path `_YES_PATTERNS`/`_NO_PATTERNS`; caller utterance threaded via new
  `last_user_text` param).
- **F23** — after a 999/A&E escalation, the dead-air re-ask is a calm re-anchor, not "how can I
  help today?" (`_emergency_reask_override` in connection.py, gating `_silence_safety_net`).

**Deferred backlog (next sessions), priority order** — details in [fix_booklet.md] / [sweep_findings.md]:
1. **F21** — long / un-bargeable TTS (8–18.7s single responses). Needs a design call
   (split/shorten vs allow barge after sentence 1 on clinical turns).
2. **F24 / F26** — "That's one for the practitioner" catch-all over-used; a few question types
   unanswered ("online?", "over the phone?").
3. **F25** — massage service naming ("sports" vs "wellness") + enforce Alcester-only gating.
4. **Group 4 / F16** — location-resolution & "this clinic" friction (own investigation).
5. **Infra / Quentin track (not Susie code):** I2 `/twilio/status`→prod 403 every call;
   I5 `GOOGLE_SERVICE_ACCOUNT_JSON` invalid on staging; **Acuity isolation** (above);
   missing filler `.ulaw` clips; no Python version pin.

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
