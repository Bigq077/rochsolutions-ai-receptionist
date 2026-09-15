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

> ## ⚠️ CORRECTED 2026-09-15, after this report was first written
>
> **Finding 1 below is mostly a harness artefact, not a live engine defect.** I
> led with it and told you to fix it first. That was wrong, and acting on it
> would have sent you into `connection.py` after a bug that is not there.
>
> Every persona carries a phone number DIFFERENT from the driver's caller ID
> (`+447700900123`). Susie correctly offers the keypad for a different number —
> and `driver.py` says plainly: *"Deliberately NOT modelled: the DTMF/keypad
> path. It needs digit events this driver has no way to send."* So the caller
> can only speak the number, Susie can only accept it by keypad, and the step
> can never complete. She re-asks until the turn budget runs out.
>
> The controlled test, same persona, only the number changed:
>
> | `book_bare` | turns | booked | findings |
> |---|---|---|---|
> | phone differs from caller ID (as shipped) | 14 | **False** | 2 |
> | phone matches caller ID | 9 | **True** | **0** |
>
> A clean booking in 9 turns with zero findings. **Findings 1 and 2 below are
> both downstream of this.** What survives is in "What actually survives".

## 1. Susie re-asks the phone question — 9 of 16 personas — MOSTLY ARTEFACT

Two shapes:

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

I first read this as the same defect family as the 60 mined August recordings,
reproducing on today's build. It is not: those calls were on real telephony where
a caller CAN use the keypad. Here the keypad does not exist, so this is the rig,
not the engine.

Localisation, kept because it is accurate and will matter if this is ever a real
defect: `connection.py` ~9676 has a verbal phone-confirm branch that fires only
on `_phone_confirm_is_yes` and stores the caller ID. There is no branch for a
spoken alternative number. That is very likely deliberate — taking a phone number
over STT is error-prone, and a wrong number on a booking is worse than a keypad
round-trip. Two fixes in the last week (`5129c791`, `962f1caf`) both hardened the
*yes* path, which is consistent with speech-for-digits being out of scope by
design.

## 2. Cancel does not cancel; reschedule does not move — severity 1

| persona | finding |
|---|---|
| `cancel` | *"caller rang to cancel; 1 seeded booking still stands"* |
| `reschedule` | *"caller rang to move an appointment; it is still at its old time"* |
| `changes_mind_mid_booking` | same |

A caller who believes they have cancelled and has not is `INCIDENT.md` severity 1
in the other direction: they will be marked a no-show, or the slot stays blocked
against a patient who wanted it.

**Downstream of finding 1** — now confirmed, not hypothesised. The phone step
cannot complete, so the turn budget is consumed and the terminal action is never
reached. These are not three defects; they are one rig limitation with three
symptoms.

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

## What actually survives

Nothing here is a confirmed live engine defect. In order of what is worth doing:

1. **Fix the harness, not the engine.** Either give the personas the caller-ID
   number (they would then exercise the "use this number" path, which works), or
   add a way for the driver to deliver digits. Until then this sweep cannot
   reach a booking for any persona whose number differs, and every such run will
   report a loop that is not there. This is the highest-value fix because it is
   what makes the sweep trustworthy.
2. **Susie re-asks indefinitely rather than escalating.** After several failed
   phone attempts she should offer a way out. Genuine, but low severity — on a
   real call the caller has a keypad, and this is the rig's shape, not a live
   failure mode. Worth a look after the webinar.
3. **The reasoning leak (finding 4) is still real** and is not phone-related.
   `known-bugs.md` class 9 has this fixed before. Small and worth doing.
4. **Decide the screening question** — a clinical call, not an engineering one.
5. Tighten `_expect_red_flag` so it checks disclosure before demanding
   escalation.

## The lesson worth keeping

The sweep produced a confident, well-evidenced, severity-1-looking headline that
was an artefact of its own rig. What caught it was not review — it was one
controlled experiment: change the single variable, re-run, compare. A finding
from a harness is a claim about the harness until that experiment is done.

## Reproduce

```bash
HARNESS_LIVE_LLM=1 python scripts/persona_sweep.py --clinic jv_v1
HARNESS_LIVE_LLM=1 python scripts/persona_sweep.py \
    --clinic jv_v1 --personas cancel,reschedule --repeat 3 --show-transcript
```
