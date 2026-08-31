# Folding the clinic branches onto canonical

**Status: canonical is ready for Vital Edge. Joint Venture is blocked on one
decision only — see "The JV blocker".**

The point of the fold is to stop a clinic being a branch. Measured 24–28 Aug,
70 commits of engineering on canonical became 199 re-applications across the
clinic branches; at eighteen clinics that model does not work at all.

---

## What the divergence actually is

Measured 2026-08-29 against `origin/latency-eval`:

| | `jv_v2` | `vitaledge-onboarding` |
|---|---|---|
| lines canonical LACKS | 608 | 1,139 |
| **`app/` files canonical lacks** | **0** | **0** |
| test files canonical lacks | 1 | 4 |

Canonical's engine is a **superset**. Nearly all of those "insertions" are the
*older* version of lines canonical has since improved, plus cosmetic drift —
the same fix with its comment moved from docstring to inline body, an artefact
of repeated hand cherry-picking rather than intent.

Every candidate for genuinely stranded work was checked **by code**, not by
commit subject (subjects over-report badly: the diary reader appears "missing"
from canonical under three subjects it already has). Results:

- `LOOKUP_PURPOSE_KEY`, `_check_availability_diary`, `send_reschedule_confirmation`,
  `_reschedule_duration_override` in the diary reader — **all already on canonical**.
- `test_b77_diary_reader_sizes_by_the_appointment.py` — was the only stranded
  work. Ported up; it guards code canonical already had.

---

## The only real differences

### 1. Two environment defaults

| | canonical | live branches |
|---|---|---|
| `_SMS_ENABLED_DEFAULT` | `"false"` | `"true"` |
| `APPOINTMENT_REMINDERS_ENABLED` default | `"false"` | `"true"` |

Both are *env-var defaults*, so **no code change is needed** — set them
explicitly on each live Render service and the code default stops mattering.

Canonical's defaults deliberately stay OFF: the same branch serves the test
line, and a test call must not text a real caller.

Both fail **silently**. That is why `_log_deployment_posture()` now prints, in
the first seconds of every boot:

```
[deploy] SMS_ENABLED=ON (explicit) | APPOINTMENT_REMINDERS_ENABLED=ON (explicit)
[deploy] +447367002651 -> jv_v1 | booking=google_calendar | calendar=…
```

`(DEFAULT)` in that line on a live service means someone forgot.

### 2. ~~The JV calendar — **the blocker**~~ — RESOLVED 2026-08-29

🟢 **Not a blocker. Both branches already point `jv_v1` at
`jointventurephysiotherapy@gmail.com`**, the calendar Carepatron syncs both
ways. The `63bc844e…` "Susie Demo" repoint was replaced on canonical on
2026-08-29 and now survives only on `vitaledge-onboarding` and
`theorem-onboarding`, neither of which serves `jv_v1`. Confirmed in the live
boot banner on 2026-08-31:

    [deploy] +447367002651 -> jv_v1 | booking=google_calendar
             | calendar=jointventurephysiotherapy@gmail.com

The section below is kept for the double-booking history, which is still the
reason the value matters. The "decision needed" at the end of it is done.

### 🔴 The REAL JV prerequisite was the dial target — fixed `cbeb46d9`

Found 2026-08-31 while looking for the calendar problem. Canonical carried
Marcus's **business** number `+447586605462` in both `transfer_phone` and
`call_overflow.dial_phone`; jv_v2 carries `+447478558845`, his second SIM.

That business number is unconditionally diverted to the Twilio line
`+447367002651`, so dialling it FROM Twilio sends the call **straight back into
Susie** and his phone never rings. Folding JV as canonical stood would have
looped every live transfer back to the receptionist the caller was being
escorted away from — silently. `call_overflow.enabled` is false on both, so the
front-desk half is dormant, but `transfer_phone` is live on every escalation.

`digest.email_to` was empty on canonical too, so JV's end-of-day booking email
would have gone nowhere.

Both ported, owner-confirmed. `owner_notification_sms` and `owner_alerts.phone`
deliberately STAY on the business number — GSM diversion does not divert SMS,
and both numbers authorise the OFF/ON toggle. That asymmetry is the easy thing
to get wrong.

Canonical's `jv_v1` config is now functionally identical to jv_v2's, verified by
a structural diff ignoring comment keys. **JV now folds by repointing the branch
and setting the two env vars, exactly like Vital Edge.**

<details><summary>The original section, for the double-booking history</summary>


| branch | `jv_v1` calendar |
|---|---|
| `latency-eval` | `63bc844e…` — **Quentin's "Susie Demo" calendar** |
| `jv_v2` | `jointventurephysiotherapy@gmail.com` — **the real JV diary** |

Pointing JV's service at canonical **as it stands would send every Joint
Venture booking into the demo calendar.**

The split is deliberate and documented in `677883da`, whose note says
canonical "must NOT inherit this value" — because eval calls on canonical used
to reach `jv_v1`. **That premise no longer holds:** since `1885b86b` canonical's
test line `+447366263180` points at `northgate`, so `jv_v1` is unreachable by
phone on canonical.

That same commit fixed a **real double-booking** (11 Aug): Susie booked over an
existing patient because she was reading a calendar Carepatron does not watch.
So the value matters, and getting it wrong is not cosmetic.

**Decision needed:** set canonical's `jv_v1.operational.calendar_id` to
`jointventurephysiotherapy@gmail.com`, and let `northgate` be the demo tenant it
was built to be. I have deliberately not done this unattended.

Vital Edge needs no equivalent decision — `vitaledgetherapy@gmail.com` on every
branch.

</details>

### 3. Four live-default guard tests

`test_live_branch_sends_by_default.py`, `test_live_branch_defaults.py`,
`test_appointment_reminders_default_on.py`, `test_sms_enabled_default_theorem.py`
assert the switches are ON, which is false on canonical by design. They are
branch artefacts, not stranded work. After the fold the thing worth asserting is
the *deployment*, not the source — which is what the boot banner does.

---

## Cutover

Do Vital Edge first: it has no blocker, and it proves the procedure.

### ⚠️ THEOREM's two switches are NOT both `true` — and one of them is a trap

**`APPOINTMENT_REMINDERS_ENABLED=false` on Theorem. Owner decision, 2026-08-31:
Mark already has his own reminder system**, and two sets of reminders to the
same patient is the failure being avoided. Set it EXPLICITLY false rather than
leaving it unset: canonical's default is already `false` so unset behaves
correctly, but the banner then reads `OFF (DEFAULT)`, which is
indistinguishable from someone having forgotten. Checked, not assumed: there is
no reminder promise anywhere in `theorem_v3`'s rendered prompt.

**`SMS_ENABLED=true` on Theorem is NOT optional.** Its closing lines promise a
confirmation text three times — on booking, reschedule and cancel — and that
promise is **hardcoded into `_build_theorem_v3`, not gated on `sms_enabled()`**.
Measured: the line renders identically with the variable unset, `false` and
`true`. Unlike the template clinics, where prompt and sender share one owner.

And **the default flips on the fold**: `theorem-onboarding` has
`_SMS_ENABLED_DEFAULT = "true"`, canonical has `"false"`. So Theorem's SMS works
today whether or not anyone set it, and stops the moment its service is
repointed — Susie then tells every caller a text is on its way while none is
sent. Mark's `owner_alerts` (`+447870166861`) ride on the same switch.

    SMS_ENABLED=true                      # required
    APPOINTMENT_REMINDERS_ENABLED=false   # explicit, owner decision

    [deploy] SMS_ENABLED=ON (explicit) | APPOINTMENT_REMINDERS_ENABLED=OFF (explicit)

If SMS is ever genuinely wanted off here, **the closing lines must change
first** — the switch alone turns a true statement into a lie told to every
caller.

**Per service, out of hours:**

1. Note the current branch and commit — that is the rollback target.
2. Set on the service's environment:
   - `SMS_ENABLED=true`
   - `APPOINTMENT_REMINDERS_ENABLED=true`
3. Point the Render service's branch at `latency-eval`.
4. Watch the deploy log for:
   - `[build_info] running build <sha>` — the deploy landed
   - `[deploy] SMS_ENABLED=ON (explicit) | APPOINTMENT_REMINDERS_ENABLED=ON (explicit)`
   - `[deploy] <the clinic's number> -> <clinic_id> | calendar=<the RIGHT one>`
   - no `⚠️ CLINIC CONFIG` line for that clinic
5. Place one real call: book, and confirm the event lands in that clinic's own
   calendar. Per the standing rule, a deploy plus a real call is the only thing
   that confirms a live change.

**Rollback:** point the service back at its own branch. The branch is untouched
by any of this and stays deployable — do not delete it until a clinic has run a
full week on canonical.

---

## What the fold does not do

It does not make one service host several clinics. Each clinic keeps its own
Render service and its own number; they simply stop having their own *branch*,
which is where the 199-to-70 tax came from. Sharing a service is a later step
and needs the per-clinic Google token cut-over
(`/auth/google/start?clinic_id=<id>`) finished first, because a shared Redis
with a legacy global token key is one authorisation away from writing one
practice's bookings into another's calendar.

---

## ⚠️ Correction: the fold is NOT behaviour-neutral

The section above measures what the BRANCHES have that canonical lacks, and
concludes the fold costs two env vars. That is true and it is not the whole
picture. Canonical is ~200 commits ahead, so folding also hands a live clinic
every one of those changes at once:

| | inherits |
|---|---|
| `jv_v2` | 28 app/ commits — **3,891 insertions, 163 deletions** |
| `vitaledge-onboarding` | 43 app/ commits — **3,839 insertions, 224 deletions** |

A superset is not automatically safe. Two consequences point in opposite
directions, and both are real.

### ~~It is how Vital Edge gets clinical safety work it does not have~~

🔴 **THIS SECTION WAS WRONG. Owner-corrected 2026-08-29 and again 2026-08-31.**
Left in place, struck through, because it was acted on twice and deleting it
would let a third session re-derive it from the same line count.

**The claim was:** `clinical_screening.py` is 1,426 lines on canonical and jv_v2
against 1,117 on vitaledge-onboarding, so VE is "a live patient line running an
older safety layer" missing five red-flag fixes.

**The line count is true. The conclusion is not.** VE's screening gap is
DELIBERATE and it is a clinical decision, not an engineering one:

- Vital Edge is a **massage** clinic. Its `clinical_screening` block says so in
  its own `_note`: physio-style red-flag triage (cauda equina and the rest)
  would be *"both clinically mismatched and a conversion killer"*, and UK
  practice is that massage contraindications are taken at the appointment on an
  intake form by the therapist, not by a receptionist on the phone.
- The block is **EMERGENCY INTERCEPT ONLY**. `screens` is deliberately ABSENT,
  and that absence is the whole design: with no screens the prompt renderer
  emits nothing and `update_screening_state` can never arm one. Every fix in
  that 340-line gap governs the GRADING of screens VE never runs.
- Theorem is the same decision, for the same reason.

**Check reachability, not line counts** — and re-derive it rather than quoting
this, because the reason has already changed once. On 2026-08-29 the gap was
inert because VE had no `clinical_screening` block at all. As of 2026-08-31 it
HAS one (1757B against jv_v1's 25432B) and `screening_enabled(vital_edge)` is
now **True**; what makes the gap inert is `screens` being absent.

    python -c "from app.clinic_config import get_clinic;       print(list(get_clinic('vital_edge')['clinical_screening']))"
    # ['_note', 'enabled', 'emergency_red_flags']   <- no 'screens'

**The one thing this section got right has since been built.** The real gap was
the emergency intercept: `detect_emergency()` could not return True on VE
because its keywords came from the absent block, so a volunteered "I'm having
chest pain" rested entirely on the model. It now works — verified 2026-08-31 on
VE's own branch against its own 1,117-line module: chest pain and stroke signs
both intercept, an ordinary massage complaint does not.

Still open, and Jonathan's clinical call rather than ours: **DVT, narrowly** —
the one contraindication more dangerous for massage than physio, whose
presenting words are ordinary massage-booking words ("tight, swollen calf after
a long flight"). Worth flagging to the owner rather than blocking a booking.

**So the fold has no clinical-screening prerequisite for Vital Edge.**

### The same decision covers THEOREM — owner + Mark, 2026-08-31

Reaffirmed by the owner: **Theorem runs an emergency intercept only, and no
clinical screening. That is a decision Quentin took WITH Mark**, not a gap, and
it is the same call as Vital Edge's. Do not propose porting the physio screening
to Mark's line; if it is ever raised it is his clinical decision, not an
engineering one.

⚠️ **The two clinics reach it by DIFFERENT config shapes, and the flag lies.**
Measured on canonical 2026-08-31:

| clinic | block | keys | `screening_enabled` | `detect_emergency` |
|---|---|---|---|---|
| `theorem` / `_v2` / `_v3` | 700B | `emergency_red_flags` only | **False** | **works** |
| `vital_edge` | 1757B | `_note`, `enabled`, `emergency_red_flags` | **True** | **works** |

`screening_enabled()` needs the block AND `enabled: true`; Theorem's block omits
`enabled`. But `detect_emergency()` reads `emergency_red_flags` directly and
does NOT gate on that flag, so the intercept fires either way. **Reading
`screening_enabled(theorem) == False` as "Theorem has no emergency cover" is
wrong.** Verify with `detect_emergency`, never the flag.

Neither clinic declares `screens`, which is what actually keeps the screening
layer inert.

**Checked before the Theorem fold:** canonical carries the same 700B block for
all three theorem ids, and both chest pain and stroke signs intercept there. The
fold does not cost Mark the intercept.

### It would also change what live callers hear — RESOLVED for hold speech

`app/hold_speech.py` existed **only on canonical** with **no flag** — it was
unconditional, so a fold would have changed what callers hear while waiting,
immediately and unchosen.

**Gated in `9287bb1e`.** It reads `operational.hold_speech`, defaulting FALSE,
and OFF is the pre-arbiter behaviour verbatim — not silence, not a tidied
version. No clinic has opted in, so the fold is now audibly neutral on this
axis. Turning it on afterwards is one key per clinic, after someone has
listened to it on the demo line.

This is the pattern for the rest of the caller-audible inheritance: default to
what the clinic runs today, opt in deliberately. It is not yet done for the
other items in the list above.

### What follows

Fold ≠ "repoint and go". Either:

1. **Make the fold behaviour-neutral by construction** — put the canonical-only
   caller-audible work (hold speech first) behind a clinic.json or env flag
   defaulting to today's live behaviour, so folding changes nothing audible and
   each change is then switched on deliberately, per clinic, with a real call.
   This is the option that scales to eighteen clinics.

2. **Port the safety work the old way** and defer the fold. Correct, and it is
   the 199-commit treadmill this plan exists to end.

Recommended: (1). ~~with the Vital Edge screening gap handled first and on its
own, because it is a live clinical gap~~ — struck 2026-08-31: there is no Vital
Edge screening gap to handle, see the correction above. Nothing blocks the Vital
Edge fold on clinical grounds.
