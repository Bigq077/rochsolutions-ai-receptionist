# Theorem — deploy tonight: ranked plan

**Written:** 2026-08-04, after the 7-call sweep · **Goal:** ship tonight
**Evidence:** `THEOREM_SWEEP_SUMMARY.md` · `THEOREM_ACCEPTANCE_REGISTER.md`

Booking / cancel / reschedule verification is explicitly **out of scope** — that
is the final stage, run once the system is believed correct. This plan gets it
to that point.

---

## Deploy state right now

| | |
|---|---|
| Branch head | `8be0388` |
| Service running | `a684e40` |
| **Already fixed, committed, NOT deployed** | `6e6d7aa` (T-13) · `ec150b7` (T-15) |

**A deploy with zero further code changes already banks two fixes.** If the
night goes badly, that alone is worth shipping.

---

## TIER 1 — fix before deploying

Both are small, both are anchored to a line, both are things a caller meets in
the first minute.

### 1. T-0 — Susie says "Yes" to "are you a real person"

**This is the one I would not ship without.** Everything else is quality; this
one is a false statement about what she is, on the first question a suspicious
caller asks.

Heard on the throwaway call:

> Caller: *"um are you a real person"*
> Susie: *"**Yes**, I'm an AI receptionist — what can I help you with?"*

**Cause found, and it is not a wording problem — the instruction does not
exist.** `susie_system_prompt.py:1585` has a complete AI-disclosure block
("## 9b. AI disclosure") with a mandated answer. It **does not render for
theorem_v3**:

```
Do NOT deny being AI  ->  0 occurrences in the rendered prompt
```

Same trap as T-4: `theorem_v3` has no `prompt_engine` key, so large parts of
that file are dead text. The model had no disclosure instruction at all and
improvised.

**Fix:** add a disclosure block to the *rendered* `_build_theorem_v3` prompt.
Mandate the opening word, because the failure is the first word and not the
sentence: *"No — I'm Susie, Theorem Health's AI receptionist."*
Prompt-only. No engine risk.
**Verify against the rendered prompt, never the source file.**

### 2. T-7 — a junk first name can reach a real booking

> Caller: *"and just a shockwave on its own"* → `first-turn name extracted: Own`

`connection.py:10885`. A regex produced `Own`; it was not on the `_NOT_NAMES`
denylist, so it was written to `soft_context["name"]` — the slot the booking
read-back reads. STT has already put a wrong surname on Mark's calendar twice;
this needs no mishearing at all.

**Fix:** gate the extractor on `_transcript_is_question` (already corrected in
`6e6d7aa`). Do not extract an answer from a turn that parses as a question.

**T-11 and T-14 come free** if the gate is applied at the shared point rather
than to the name extractor alone — same cluster, same cause. If it is not
shared, fix T-7 only tonight and leave the other two.

---

## TIER 2 — only with time in hand

### 3. T-5 — answer length

**Highest impact on how the system *sounds*, and the highest regression risk to
change at night.** That combination is why it is Tier 2 and not Tier 1.

Present in all seven calls. Worst turns 20.2 s and 20.1 s. On call 5 the caller
barged in on every long answer; on call 6 they said *"say that again you got cut
off"*; call 3 ended `abandoned` two seconds after a ~20 s monologue.

**Important correction to an earlier assumption:** three-sentence caps *do*
already exist in the theorem prompt (clinical turns, `## 9b`-adjacent blocks,
line 214, 996, 2401, 2570). They simply do not cover the FAQ / pricing /
logistics answers that ran long. So this is not a missing cap — it is an
uncovered surface.

There is a proven precedent to copy: `9302cf1` on `main` — *"Cap objection/value
answers: warm but ~3 sentences, offer before over-explaining"* — written after
the same symptom ("18–28s of uninterrupted TTS on a live call, Call 11"). It is
one of the 128 main commits never ported here.

**If touched tonight:** port the *concept*, scoped to FAQ/pricing/logistics
turns only. Do not rewrite the existing clinical caps — they work.
**If time is short: leave it.** It makes calls long, not wrong.

---

## TIER 3 — park. Do not touch tonight.

Ordered by when they will actually start to matter.

| | Why it can wait |
|---|---|
| **T-9** — no Acuity calendar ID for `mark` / `leanne` | Only affects named-practitioner booking. Generic booking works. **Check before the booking-verification stage**, not before deploy. |
| **T-2** — two summary rows, sometimes disagreeing | Inert: `SHEETS_ENABLED` is off. Fix before Sheets is switched on, not before tonight. |
| **T-12** — every abandoned call texts the caller | Owner decision, not an engineering defect. Needs an answer before real patients call, not before a deploy. |
| **T-1** — slot gate swallows a factual question | Real, but the fix touches TTS gating on the booking path — the worst thing to change unverified at night. |
| **T-11 / T-14** | Free with T-7 if the gate is shared; otherwise same cluster, next session. |
| **T-3** — no watchdog after a bare FAQ answer | Narrower than first written; the backstop exists and fires when a question is outstanding. |
| **T-8** — "wellbeing" split across a TTS chunk | Sounds like a glitch once. Cosmetic. |
| **T-6** — staff-SMS log says "sent" when suppressed | Log-only. Misleads an engineer, never a caller. |

---

## Deploy checklist

**Environment — confirm, don't assume:**

- [ ] `SMS_ENABLED` — set explicitly to `true`. The code default now favours ON,
      but a stale `false` in the service env still wins and silences the clinic.
- [ ] `TRANSFER_DISABLED` — must be **unset**. Left set, Susie silently never
      transfers anyone to a human.
- [ ] `OBS_ALERT_SMS_TO` — **find out whose number this is.** Every call in the
      sweep scored 3–4 and fired an immediate operator SMS (`alerts.py:220`).
      If it is Mark's, he is texted after essentially every call, and alerts
      that always fire get ignored — which is how a real one gets missed.
- [ ] `ASSEMBLYAI_USE_U35` — currently ON. Fine to leave; just know that every
      transcription-shaped finding carries that caveat.

**After deploying:**

- [ ] Confirm the build SHA in the Render log: `[build_info] running build <sha>`.
      `/health` returns a hardcoded `1.0.0` and proves nothing.
- [ ] One call, three checks, no booking needed:
      1. *"Are you a real person?"* → the answer must start with **No**. (T-0)
      2. Reach the phone step → the digits must be spoken aloud. (T-4, holding)
      3. Ask a question mid-clinic-question, e.g. *"should I take ibuprofen"* →
         must be answered, not met with "which clinic?". (T-13, **never yet
         verified live**)

---

## Residual risk if you ship after Tier 1 only

Stated plainly so it is a decision rather than a surprise:

- **Calls will sound long-winded.** T-5 is unfixed and affects every call.
- **A caller who asks a question in the same breath as a timing preference may
  not get an answer.** T-1.
- **No booking, cancel or reschedule has ever completed on this branch.** That
  is by design tonight — but it means "deployed" is not yet "verified".

None of those is a correctness failure that reaches a patient record. T-0 and
T-7 are the two that could, and both are Tier 1.
