# Stop being the test harness — before the cohort lands

**Status as of 2026-08-29.** Phase 1 done. Phase 2 not started. Phase 3 mostly
done and now blocked on an owner action. Phase 4 not started, both items
re-confirmed live. Two live-call confirmations banked: the fourth clinic booking
from config alone, and Theorem's emergency intercept.

This document is the plan and its running record. It is written to be picked up
cold: if you have not read anything else, read this.

---

## The verdict that started it

Quentin asked: *"all these tests through OBS and through calls wouldn't really
find what I found by testing — tell me if I'm wrong."* And: *"I'm spending hours
a day just calling and fixing small bugs."*

**Right about the symptom, wrong about the cause.** Real calls genuinely do find
the bugs — the suite has an unstable red baseline, the live free-form path had
zero behavioural coverage, and OBS explains calls rather than detecting. But
calling is not where the hours went.

Measured 24–28 Aug: **70 commits of engineering on `latency-eval`, 199
re-applying them to the clinic branches, 72 `port/*` branches in five days.**
74% of activity was re-applying finished work. One clinic = one branch = one
Render service; the tax is 2.84× at three clinics and 17× at the eighteen the
end-of-September webinar implies.

**The decision: stop fixing slot bugs faster; make a clinic stop being a
branch, and build the safety net that makes that safe.** That decision has held
all the way through and nothing since has challenged it.

---

## Phase status

| Phase | State |
|---|---|
| **1 — Headless free-form driver** | ✅ Done. Found a live defect on day one. |
| **2 — Adaptive caller + harvest the corpus** | ❌ Not started. All three parts open. |
| **3 — Collapse the tenancy** | 🟡 New-clinic path proved and live. Fold groundwork done. **The fold itself is blocked on an owner action.** |
| **4 — Two contained slot fixes** | ❌ Not started. Both re-confirmed live on 29 Aug. |

---

## What was done

### Phase 1 — the harness (`ac2d68e1`, `f83af3f0`)

`tests/harness/` drives the **live** free-form turn loop in-process: text in,
text out, no phone, no ngrok, no deployed server, **no calendar writes**. LLM
tokens only. Tool executors stubbed at the `receptionist_tools` boundary.

It earned itself immediately: it found **F-021** (`f2ba13d8`) — the caller asks
for a sports massage and the diary says Deep Tissue — a defect the register had
carried as *"reproduced 4/4, still open"* under *"the worst failure class in
this system"*, and which the agreed response at the time had been to script
demos around rather than fix.

Its network fence was then audited and found to cover httpx and requests but
**not** httplib2 (Google Calendar) or websockets (AssemblyAI, ElevenLabs) — the
paid and diary-writing providers. Fixed, with a test that scans `app/` and fails
if the engine grows a transport the fence cannot close.

### Phase 3 — tenancy

**The config-only path already largely worked**, which was the biggest surprise
of the whole exercise. `get_clinic` already falls back to `clinic.json`;
`is_freeform_clinic` already keys off `prompt_engine`. Copy a tenant, rewrite
every trace of them, render the prompt: 107,985 characters, **zero leakage**.

- `3c0a638d` — `tests/tenancy/` proves it, plus `validate_clinic_config()`: the
  onboarding checklist as executable code.
- `b3c7cce1` / `1885b86b` — **a fourth clinic, Northgate Physiotherapy, built
  entirely from a `clinic.json`**, now answering **+447366263180**. A real call
  on 28 Aug booked an 8am weekday slot into its own calendar — a time JV's
  evenings-only config could not produce, which is what makes it evidence.
- `9784bdc8` — `scripts/port.py`, the interim tourniquet.
- `0f6d1396` — `_log_deployment_posture()`: the boot banner that states what a
  deployment actually *is*.
- `a4c5a9c3` / `d6110a50` — canonical's `jv_v1` repointed to the real JV diary.
- `9287bb1e` — `hold_speech` gated per clinic, so a fold is audibly neutral.

### Defects found and fixed along the way

| | |
|---|---|
| **F-021** wrong service booked | `f2ba13d8`, VE `30947273` |
| **Bank holidays** bookable on 3 of 4 clinics | `ad10bc84`, all live branches |
| **Cross-tenant practitioner leak** — VE's prompt said "Marcus", JV's said "Jonathan" | `b3c7cce1` |
| **Emergency intercept missing** on VE and Theorem | `baad8ab3`, `920b318c` |
| **JV pointed at the demo calendar** on canonical | `a4c5a9c3` |
| **Netfence blind to Google Calendar + websockets** | `f83af3f0` |

---

## Decisions made

1. **`latency-eval` is canonical. Every fix lands here first.** Unchanged.
2. **A clinic is a config file.** Behaviour differs by `clinic.json` key —
   `booking_system`, `availability_mode`, `prompt_engine`,
   `open_on_bank_holidays`, `hold_speech` — never by clinic id. Enforced by an
   AST test.
3. **Northgate is the demo tenant.** It absorbed the role `jv_v1` used to play
   on canonical, which is what freed JV's calendar to be the real one.
4. **Bank holidays default CLOSED.** The mistakes are asymmetric: wrongly closed
   costs a caller one day of options; wrongly open sends someone to a locked
   door. Marcus and Jonathan work them (opted in); **Mark does not** (Theorem
   was already correct and needed no port).
5. **Jonathan does not work Sundays.** VE's two hours blocks disagreed and the
   diary reader took the wrong one, so an unworked Sunday read as *wide open*.
6. **Vital Edge gets no clinical triage.** It is a massage clinic; physio
   red-flag screening is mismatched and UK practice takes contraindications at
   the appointment. It has the **emergency intercept only**.
7. **Theorem keeps no triage, and gains an emergency intercept.** Mark declined
   screening for booking speed and that stands. He accepted a deterministic 999
   response — which his prompt already promised — on condition it adds no
   question. It cannot, and that is asserted.
8. **Theorem does not fold.** Its prompt is a hardcoded Python module and it is
   Acuity-backed. It stays on its own branch and is ported on demand.
9. **`hold_speech` is opt-in, defaulting to today's behaviour.** OFF is the
   pre-arbiter code verbatim, not silence and not an improvement.
10. **Baselines are judged by diffing failing SETS, from two clean sequential
    runs.** Never counts, never concurrent.

---

## The traps — what cost time, so it does not cost it again

- **A local branch was 164 commits behind origin.** Always base off
  `origin/<branch>`.
- **Running two suites concurrently reported 171 failures; 45 were phantom.**
  Sequentially it was 126, and the 6 real ones sat in one file. Counts under
  contention are worthless.
- **`scripts/port.py` had two bugs, both of its own making** (`6ed3b321`,
  `912dbbf0`): it deleted the target branch's own test file from the baseline,
  and it silently discarded a content conflict while reporting "absent on
  target". Both made the two sides of a comparison different — the one thing it
  exists to prevent. Caught by arithmetic that did not add up (+45 and −9).
- **Tests pinned to a clinic id or a fixed date rot silently.** A fixture named
  `northgate` broke the moment that clinic became real; `DAY = "2026-08-27"`
  rotted the day it passed, and 13 tests were passing or failing on coincidence.
- **A text scan cannot tell coupling from prose.** `jv_v1` appears all over
  `app/` in comments. Use the AST.
- **Commit subjects over-report divergence badly.** The diary reader looked
  "missing from canonical" under three subjects it already had. Audit by code.
- **A client decision pinned on ONE branch is invisible to canonical.** Mark's
  "no screening" pin lived only on `theorem-onboarding`, so a change
  contradicting it went fully green on canonical. Now mirrored.
- **A `json.dumps` rewrite turned a 2-line change into 1,371 lines.** Edit
  clinic files as text.

---

## What is left

### Phase 2 — not started. The cheapest value left.

- **Adaptive caller.** Replace the fixed script with an LLM caller given a
  persona and a goal. The engine does not ask the same questions in the same
  order twice, so a fixed list cannot track it. Do not re-order the scripts a
  third time.
- **Mine the obs corpus.** `app/obs/to_scenario.py` and `regress.py` both work,
  both are unit-tested, **neither has ever been used**.
  `tests/auto/scenarios/regressions/` is still **empty**. This is free coverage
  on the floor.
- **Re-arm `scripts/detect_defects.py`.** Still **7 detectors**, untouched since
  31 Jul; defects have run to B-119.

### Phase 3 — the fold, blocked on you

Canonical is ready. Per service, out of hours (full runbook:
`docs/FOLD_THE_CLINIC_BRANCHES.md`):

1. Set `SMS_ENABLED=true` and `APPOINTMENT_REMINDERS_ENABLED=true` on the
   service.
2. Point its Render branch at `latency-eval`.
3. Check the boot log: `(explicit)` on both switches, the **right calendar**, no
   `⚠️ CLINIC CONFIG` line.
4. One real call, and confirm the booking lands in that clinic's own diary.

**Do Vital Edge first** — it has no blocker and proves the procedure.

⚠️ **The fold is not behaviour-neutral.** It hands a clinic ~3,900 lines at once
(28 commits for JV, 43 for VE). `hold_speech` is gated; the screening wording,
availability phrasing and reason-question scoping are **not**. Gate them the
same way — default to what the clinic runs today — or accept the delta
knowingly.

### Phase 4 — not started, both re-confirmed live 29 Aug

- **`_check_availability_published` calls neither `_cap_presented_slots` nor
  `_sync_last_offered_to_spoken`** — the one producer of seven that skips both.
  A latent defect on Vital Edge.
- **`test_offer_record_matches_what_was_spoken.py` still pins one call site via
  `inspect`.** Re-aim it at the invariant across all seven producers.

The `last_offered_slots` three-contract restructure remains **post-webinar**.
Written down; do not start it.

### Calls owed

- ~~Theorem: *"I've got chest pain"*~~ — **placed 2026-08-29, clean.** The
  emergency intercept works on Mark's live line, and it did not cost a booking
  caller anything. Confirms `71d603c7` end to end.

  The log line that distinguishes the deterministic path from the model simply
  behaving well is `[clinical_screening] EMERGENCY detected`. Worth knowing for
  the next one: a clean-sounding call is necessary evidence, not sufficient —
  the model could always have produced the right answer by itself, which is the
  behaviour this change replaced.
- Northgate: set `hold_speech: true` and listen. It is the only clinic with no
  patient at risk, and it is the evidence needed before offering the arbiter to
  Marcus or Jonathan.

---

## Branch state, 2026-08-29

| branch | tip | note |
|---|---|---|
| `latency-eval` | `920b318c` | canonical; also serves Northgate on the test line |
| `jv_v2` | `b1a71242` | live — Joint Venture |
| `vitaledge-onboarding` | `4330d1b8` | live — Vital Edge |
| `theorem-onboarding` | `71d603c7` | live — Theorem; **314 behind**, stays separate by decision |

**Standing habit to keep:** state a Theorem verdict — *applies / inert / N-A* —
in every commit, at the time. Theorem was repeatedly left out of proposals by
default, and the emergency-intercept gap on Mark's line was found only because
the owner asked why.
