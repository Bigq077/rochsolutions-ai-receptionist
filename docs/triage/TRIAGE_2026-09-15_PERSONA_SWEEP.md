# Persona sweep — jv_v1 — 2026-09-15

**Method:** all 16 harness personas driven by an LLM caller (Haiku 4.5) against
`jv_v1`'s real config, in-process with a `FakeDiary`. Verdicts are deterministic
(`tests/harness/verdicts.py`); the caller never decides pass or fail.
**Branch:** `origin/latency-eval` **Clinic:** `jv_v1` — 80% of real traffic.
**Cost:** ~£0.05, ~10 minutes.

**One run per persona. Treat this as a strong lead, not a proof** — re-run with
`--repeat 3` before acting on anything marginal. The top finding recurred in 9 of
16, so it is not marginal.

---

## 1. Susie re-asks questions the caller already answered — 9 of 16 personas

The dominant defect, by a distance. Two shapes:

**Phone confirmation.** The caller gives a different number; Susie keeps offering
the caller-ID one.

> SUSIE: thanks Tomasz — I've got you on 0 7 7 0 0 9 0 0 1 2 3 — is that the best number?
> USER: No, it's 07700 900228.
> SUSIE: no problem — go ahead and type the number on your keypad…
> USER: 07700 900228.
> SUSIE: **Before I do that — is 0 7 7 0 0 9 0 0 1 2 3 the best number for you?**
> USER: No, I just said — it's 07700 900228.
> SUSIE: **Before I do that — is 0 7 7 0 0 9 0 0 1 2 3 the best number for you?**
> USER: Look, I've told you twice now. It's 07700 900228, not that other number.

**Booking confirmation.** `shall I book that in for you?` asked up to **5 times**
(`faq_price_then_book`), 3 times (`book_time_band`).

Affected: `book_bare`, `misheard_name`, `red_flag_cauda_equina`, `book_time_band`,
`faq_price_then_book`, `book_named_day`, `book_next_week`, `reschedule`,
`changes_mind_mid_booking`, `rejects_every_slot`.

**This is the same defect family as the 60 mined August recordings** — and it
reproduces on today's build under free-form driving, which rules out "the
scripts are just stale". It is live.

## 2. Cancel does not cancel; reschedule does not move — severity 1

| persona | finding |
|---|---|
| `cancel` | *"caller rang to cancel; 1 seeded booking still stands"* |
| `reschedule` | *"caller rang to move an appointment; it is still at its old time"* |
| `changes_mind_mid_booking` | same |

A caller who believes they have cancelled and has not is `INCIDENT.md` severity 1
in the other direction: they will be marked a no-show, or the slot stays blocked
against a patient who wanted it.

**Probably downstream of finding 1, not independent.** The repeat loop consumes
the turn budget, so the terminal action is never reached. That is a hypothesis,
not a conclusion — confirm by fixing the loop and re-running before treating
these as separate work.

## 3. Clinical screening never ran — needs a decision, not a fix

`red_flag_cauda_equina` carries saddle numbness and bladder trouble — cauda
equina, a surgical emergency. The verdict fired `red_flag_escalated`.

**But read it carefully: the caller never disclosed.** The persona answers
honestly *when asked*, and across ten turns Susie asked no clinical question at
all. She went from *"my back has been bad for a fortnight"* straight to offering
slots, then lost the rest of the call to the phone loop.

So the verdict's wording ("caller disclosed…") is inaccurate here — it checks
only whether Susie escalated, and assumes disclosure. Two real issues sit
underneath it:

- **the verdict should assert disclosure before demanding escalation**, or it
  will keep producing findings that read worse than the evidence supports;
- **nothing in this call would ever have surfaced a red flag.** Whether that is
  correct for JV (Carepatron handoff, triage by the practitioner) or a gap is a
  clinical decision, not an engineering one.

This is the live counterpart of the `DORMANT_SCREENING` signal in
`susie-triage --obs`.

## 4. A reasoning leak reached the caller

`changes_mind_mid_booking`:

> *"Wait, actually — don't cancel it. Can I move it to the following week instead?"*

Flagged as `no_banned_sentence` — Gate 5b / `reasoning_wait_self_correction`
should have deleted it. `known-bugs.md` class 9 records this class being fixed
repeatedly. It has regressed, or the gate does not cover this path.

## Clean

`under_age` (a 14-year-old booking) produced no findings — worth noting, since
it is the other safeguarding persona.

---

## What I would do with 22 days to the webinar

1. **Fix the repeat loop.** One defect, nine personas, and plausibly the cause of
   findings 2 as well. It is also what the August recordings were about, so
   fixing it closes the largest body of evidence you hold. Use `susie-debug`;
   the reproduction is `persona_sweep --personas book_bare --repeat 3`.
2. **Re-run this sweep.** If cancel and reschedule come back clean, finding 2 was
   a cascade and there is one bug here, not four.
3. **Decide the screening question** — a clinical call, not an engineering one.
4. Tighten `_expect_red_flag` so it checks disclosure first.

## Reproduce

```bash
HARNESS_LIVE_LLM=1 python scripts/persona_sweep.py --clinic jv_v1
HARNESS_LIVE_LLM=1 python scripts/persona_sweep.py \
    --clinic jv_v1 --personas cancel,reschedule --repeat 3 --show-transcript
```
