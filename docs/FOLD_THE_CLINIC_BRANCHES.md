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

### 2. The JV calendar — **the blocker**

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

### 3. Four live-default guard tests

`test_live_branch_sends_by_default.py`, `test_live_branch_defaults.py`,
`test_appointment_reminders_default_on.py`, `test_sms_enabled_default_theorem.py`
assert the switches are ON, which is false on canonical by design. They are
branch artefacts, not stranded work. After the fold the thing worth asserting is
the *deployment*, not the source — which is what the boot banner does.

---

## Cutover

Do Vital Edge first: it has no blocker, and it proves the procedure.

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
