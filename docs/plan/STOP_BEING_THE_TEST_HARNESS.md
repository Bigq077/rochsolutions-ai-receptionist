# Stop being the test harness — before the cohort lands

**Status as of 2026-08-29.** Phase 1 done. Phase 2 not started. Phase 3 mostly
done and now blocked on an owner action. Phase 4 not started, both items
re-confirmed live. Two live-call confirmations banked: the fourth clinic booking
from config alone, and Theorem's emergency intercept. **One open engineering
decision — see "The hold-speech decision".**

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
7. **Theorem keeps no triage, and gained an emergency intercept.** Mark declined
   screening for booking speed and that stands. He accepted a deterministic 999
   response — which his prompt already promised — on condition it adds no
   question. Landed `920b318c` / `71d603c7` and **confirmed on a live call**.

   The unlock was a CODE change, not a client one: `detect_emergency` read its
   keywords through `screening_config`, which returns nothing unless `enabled`
   is set, so "no triage" and "no deterministic 999" were ONE switch. Split
   them — the intercept keys on `emergency_red_flags` being configured. Mark's
   own pin still passes untouched, which was the design goal.
8. **Theorem does not fold.** Its prompt is a hardcoded Python module and it is
   Acuity-backed. It stays on its own branch and is ported on demand.
9. **`hold_speech` is opt-in, defaulting to today's behaviour.** OFF is the
   pre-arbiter code verbatim, not silence and not an improvement. `northgate`
   is opted IN (`0d2500c4`) so the arbiter can be evaluated; no patient line
   is.
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
- **`hold_speech=true` was set as a Render ENV VAR and did nothing.** These are
  `clinic.json` keys (`operational.*`), read per clinic — they have to be, since
  one service will host several tenants and a process-wide switch cannot say
  "on for the demo clinic, off for the patient lines". The call sounded
  identical, which is indistinguishable from the feature being broken. The boot
  banner now warns when a clinic key appears in the environment. **Say WHERE a
  key goes, not just that there is one.**

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

### The hold-speech decision — OPEN, and the one piece of engineering judgement left

**The problem.** Two things speak to a caller while they wait, and only one
asks permission:

| | plays | timing | asks the arbiter? |
|---|---|---|---|
| `filler_guard.py` | a recorded clip | 350ms, then again at 2.5s | **no** — it only *reports* |
| `hold_speech.py` | TTS phrases, 4 producers | when a tool runs | it *is* the arbiter |

FillerGuard calls `note_filler_played()`, which sets `_hold_head_spoken`. So it
**tells** the arbiter it spoke, and every gated producer afterwards correctly
stays quiet — but its own 2.5s escalation never consults the one-head-per-turn
rule, because it never calls `decide_hold` at all.

Seen live on `CAc46c00705bc1ad81` (northgate, `hold_speech` ON): clip at 350ms,
clip at 2.5s, then the tool. The arbiter DID work — it suppressed the third
phrase legacy would have added, since `_FILLER_TOOLS["check_availability"]` is
a real list. ON meant two phrases; OFF would have meant three.

`hold_speech.py` claims stacking is "unrepresentable" by construction. That is
now false and should either become true or stop being claimed.

**Why the listen could not settle it.** The arbiter's evidence base is 354 hold
phrases across 98 calls, one call with 17, 175 of 322 promising a lookup that
never happened. That is a distribution, not a moment: on any single
well-behaved call the difference is one suppressed phrase, inaudible by design.
**A corpus-level fix cannot be ear-tested.** More calls will not help.

**Options:**

| | pros | cons |
|---|---|---|
| **A. Leave it, correct the docstring** | zero risk; the escalation predates the arbiter and covers genuinely slow turns | the guarantee stays weaker than it claims; the difference stays unevaluable; the stacking the corpus flagged still ships |
| **B. Make the second clip ASK — gated, so OFF keeps today's behaviour** | one head per turn becomes true; cheap, since `arm(session)` already takes the session; makes a listen finally mean something | >2.5s turns lose reassurance and gain silence, which is a known call-killer; a fourth file joins the switch's surface |
| **C. Move the escalation into the arbiter entirely** | one owner, work-aware wording, keeps slow-turn coverage | biggest change; arguably reinvents "two heads" under a nicer name |

**Recommendation: B, and evaluate by REPLAY rather than by ear.**

B because the asymmetry favours it — the arbiter's whole claim is one decision,
one latch, stacking unrepresentable, and a producer that reports but never asks
makes that a slogan. The silence risk is bounded: the watchdog still exists, and
if 2.5s proves too long the arbiter can allow a second head past a longer
threshold. That is a tuning decision inside one module, which is where it
belongs.

Then measure it: `app/obs/regress.py` and `to_scenario.py` both work, are
unit-tested, and have **never been run**. Replay the 742-call corpus and count
hold phrases per call, before and after. Objective, free, and the same Phase 2
work that would reduce calling for everything else.

**Do not** leave it as A and call the arbiter done — that ships a guarantee that
is not one, with no way to tell whether it helped.

**Separate question for the owner:** the standing rule is "the recorded filler
belongs only where slots are about to be read out". Two clips in 2.5 seconds may
already breach that, independently of any of this. If the second clip should not
exist on that turn at all, B stops being a trade-off and becomes a straight fix.

### Calls owed

- ~~Theorem: *"I've got chest pain"*~~ — **placed 2026-08-29, clean.** The
  emergency intercept works on Mark's live line, and it did not cost a booking
  caller anything. Confirms `71d603c7` end to end.

  The log line that distinguishes the deterministic path from the model simply
  behaving well is `[clinical_screening] EMERGENCY detected`. Worth knowing for
  the next one: a clean-sounding call is necessary evidence, not sufficient —
  the model could always have produced the right answer by itself, which is the
  behaviour this change replaced.
- ~~Northgate: set `hold_speech: true` and listen~~ — **attempted twice, both
  inconclusive, and the method is wrong.** The first call ran before any flag
  was committed (an env var had been set instead, which nothing reads) and
  contained no tool call at all. The second ran on `0d2500c46210` with the flag
  live and did reach a lookup — and produced the FillerGuard finding above
  rather than a verdict. See "The hold-speech decision": evaluate by corpus
  replay, not by ear.

---

## Where things live

| artefact | what it is |
|---|---|
| `docs/ONBOARD_A_CLINIC.md` | **how to add a clinic.** The three things needed, the keys that decide behaviour, and the mistakes that make no sound. Read before onboarding anyone. |
| `docs/FOLD_THE_CLINIC_BRANCHES.md` | the fold audit and the cutover runbook, including the correction that a fold is NOT behaviour-neutral |
| `tests/harness/` | the in-process driver — text in, text out, against the live turn loop. `netfence.py` is what stops it reaching a real calendar. |
| `tests/tenancy/` | the Phase 3 gate: a clinic stood up from config alone, and the AST check that `app/` never learns its name |
| `scripts/port.py` | the interim tourniquet. Throwaway by design; delete it the day the fold is done. |
| `app/clinic_config.py` → `validate_clinic_config()` | the onboarding checklist as code. Run it before pointing a number at a tenant. |
| `app/main.py` → `_log_deployment_posture()` | what a deployment actually IS, printed in its first seconds |
| `app/clinics/northgate/` | the fourth clinic — the demo tenant, and the thing that freed jv_v1's calendar to be real |

Regression tests worth knowing by name, because each encodes a defect that was
live: `test_bank_holidays_are_not_bookable`,
`test_the_service_shown_is_the_service_booked`,
`test_hold_speech_is_opt_in_per_clinic`,
`test_theorem_emergency_intercept_costs_no_question`,
`test_theorem_declined_clinical_screening`.

---

## Branch state, 2026-08-29

| branch | tip | note |
|---|---|---|
| `latency-eval` | `58451682` | canonical; also serves Northgate on the test line |
| `jv_v2` | `b1a71242` | live — Joint Venture |
| `vitaledge-onboarding` | `4330d1b8` | live — Vital Edge |
| `theorem-onboarding` | `71d603c7` | live — Theorem; **314 behind**, stays separate by decision |

### Smaller things noticed and deliberately not acted on

- `northgate` inherited jv_v1's **six clinical screens**. Harmless — it is a
  fictional physio clinic and no patient calls it — but it was not a decision.
- The demo line's Sheets append fails on an invalid `GOOGLE_SERVICE_ACCOUNT_JSON`
  escape. Known-accepted on that line; it is NOT accepted on a patient line.
- `[clinical_screening] last_bot_prompt truncated at 200 chars` still fires and
  falls back correctly (B-31). Working as designed, noted so it is not
  re-diagnosed.

**Standing habit to keep:** state a Theorem verdict — *applies / inert / N-A* —
in every commit, at the time. Theorem was repeatedly left out of proposals by
default, and the emergency-intercept gap on Mark's line was found only because
the owner asked why.
