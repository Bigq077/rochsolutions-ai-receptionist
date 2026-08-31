# Stop being the test harness — before the cohort lands

**Status as of 2026-08-30, early afternoon.** Phase 1 done. **Phase 2 DONE —
all three parts. Phase 4 DONE.** Phase 3 ready and blocked on an owner action.
**Nothing is owed to the patient lines.** The four findings of the 2026-08-30
demo call, B-125/B-125b, and BOTH items the owner raised by ear (O-1 and O-2)
are landed, and every branch that can carry each one has it. All four live
branches were pushed and their suites diffed at zero regressions.

> 🔴 **START HERE IF YOU ARE PICKING THIS UP COLD.** Read
> "**HANDOVER — the state at 2026-08-30 14:10**" immediately below. It says
> what is done, what the 12:41 verification call did and did NOT prove, and
> the three things it surfaced that nobody has acted on yet.

Two live-call confirmations banked: the fourth clinic booking from config
alone, and Theorem's emergency intercept. The
hold-speech decision is SETTLED — and the measurement that settled it says the
filler architecture was aimed at the wrong latency source. See Phase 2.

The adaptive caller exists and has been run: sixteen personas, 11.8 minutes, no
phone. **It found three engine defects on its first full run, and a fourth came
from the live call that verified them.** All four are fixed. See "The adaptive
caller".

This document is the plan and its running record. It is written to be picked up
cold: if you have not read anything else, read this.

---

## HANDOVER — the state at 2026-08-30 14:10

Written to be picked up in a fresh session with no other context.

### Branch tips

| branch | tip | note |
|---|---|---|
| `latency-eval` | `89a52fe0` | canonical + the Northgate demo line (**+447366263180**). Build `2d72f87a935c` was verified on a live call at 12:41 on 30 Aug; `89a52fe0` on top of it is tests only. |
| `jv_v2` | `2088c668` | live — Joint Venture |
| `vitaledge-onboarding` | `6c3dd2b4` | live — Vital Edge |
| `theorem-onboarding` | `aad9f677` | live — Theorem |

Deploy proof is `[build_info] running build <sha>` in the Render log at call
cleanup. `/health` returns a hardcoded 1.0.0 and proves nothing.

### ✅ DONE — the port to the patient lines, and both items from the ear

Everything the 11:00 handover listed as pending or not-started is now landed
and pushed. Nothing in this block is owed.

**The three-fix port (finding 2 `e1ebcf9a`, finding 3 `5ab3fcc5`, B-125 +
B-125b `0ea44018` / `61d65180`)** went to all three patient branches in one
pass. Findings 1 and 4 were confirmed NOT portable and are not a gap: they are
defects *inside* the situational hold-head feature, and `git grep` finds zero
files for `app/hold_speech.py`, "situational head", `_hold_head_spoken`,
`_CONFIRM_Q` and `join_after_head` on all three branches. No head producer
means no defect to port — not a fix that was skipped.

**O-1 — the duration question now lands before the timing question.**
`2d72f87a` on canonical, ported to `jv_v2` and `vitaledge-onboarding`.
**Theorem: N-A** (no multi-length service). The root cause was NOT a missing
instruction: the prompt has carried "DURATION QUESTION FOR <SERVICE>" with the
right lengths and prices for months. It had no POSITION in the numbered ladder
the model actually follows, so the only thing that ever forced it was the
tool-time `duration_choice_gate` — unreachable until the caller has already
given a day. Late by construction. Fixed by adding rung **`1c. SESSION
LENGTH`** between `1b. REASON` and the timing rung, gated on
`_spine_has_duration_choice(clinic)`. **`duration_choice_gate` is deliberately
untouched** and remains the backstop; a test asserts it still blocks.

**O-2 — a head is no longer followed by the model's own hold phrase.**
`1c43c46e`, canonical only. **No port: N-A on all three patient lines**, same
evidence as findings 1 and 4 — no head producer exists there, so there is no
first phrase for the model's line to duplicate.

### 🔬 What the 12:41 verification call proved, and what it did not

`CA8e688605a9460906840840ed561246ac`, build `2d72f87a935c`, 96s, demo line.

**O-1: confirmed.** The ladder ran `1b → 1c → 2` in order —
`"Right — what's the appointment for?"` → `"Would you like a 30-minute session
at thirty-eight pounds, or a 60-minute one at sixty-two pounds?"` → *length
captured: 60 minutes* → `"Do you have a preference for when you'd like to come
in?"`. **`check_availability blocked` never appears in the call**: the gate did
not have to fire, which is the defect gone at source rather than caught.

**O-2: confirmed, but by its SECOND half only — read this before trusting it.**
Four `head=True` phrases were synthesised and none of the eleven `head=False`
chunks is a hold phrase, so no duplicate reached the caller. But on both
lookups the model happened to say *"Just a moment while I check what's
available."*, which Gate 5 deletes anyway — so the duplicate-stripper was not
the operative mechanism. What actually did the work, twice, is the **latch
ownership fix**:

    [ms_gate5] the preserved pre-tool line IS a hold phrase (...) but a head
    had already spoken — the latch is not ours and stays NON-revocable

Without it the revocation would have cleared a latch belonging to the head
producer (Gate 5 had removed the model's sentence, so `_spoken_this_turn` held
no hold phrase), the tool-time producer would have stopped standing down, and
it would have spoken the second phrase. **The half the owner originally
reported — the model's line SURVIVING Gate 5 and being spoken — did not occur
on this call and remains unconfirmed live.** It depends on the model's wording
and cannot be forced.

### 🟠 Seen on the verification call, not yet acted on

Three things in `CA8e688605`, in priority order. None blocks anything above.

1. **Two guards had to correct the model in 96 seconds.**
   `[slot_followup] CORRECTED a false 'the only time is' claim — the day holds
   more times (B-100)`, and `read-back time corrected: 'five past nine in the
   morning' -> 'eight in the morning' … the model crossed two options`. Both
   caught it, nothing wrong reached the caller. But that is two near-misses on
   one short call in the failure class that matters most here, and it is worth
   asking why the model is producing them at that rate.
2. **Latency on the slot turns is over the bar.** `content_ttfa_ms=5990` and
   `4150` against the 1.5s p95 target in CLAUDE.md §6. First audio is fast
   (`ttfa_ms=740`) because the head covers it — so the hold speech is masking
   a real 4–6s to content rather than the number having improved.
3. **Turn 2 was 9.3s of continuous speech** — empathy, then the knowledge
   sentence, then the duration question. If the flow still feels long, that is
   where to look, not at the ordering.

### ⚪ Still open, previously recorded, unchanged

- The T-3 nudge arms "Anything else you'd like to know?" after Susie's own
  "Take care — bye!" — seen on every call in this series.
- `[clinical_screening] last_bot_prompt truncated at 200 chars` (B-31) still
  fires and still falls back correctly.
- The demo line's Sheets append fails on an invalid
  `GOOGLE_SERVICE_ACCOUNT_JSON` escape — known-accepted THERE, not on a
  patient line.
- The ElevenLabs `/v1/models` 401 at prewarm is a `models_read` scope thing and
  does not predict synthesis failure. The log line says so itself.
- Phase 3, the fold: blocked on the owner, not on engineering.

### How the four demo calls map to the fixes

| | call 1 `CA7182…` 07:32 | call 2 `CA4fff…` 10:11 | call 3 `CAd1bc…` 10:26 |
|---|---|---|---|
| finding 1 — heads after a readout | ✅ both turns | ✅ 4 heads | ✅ 4 heads |
| finding 2 — sentence twice | not triggered | not triggered | not triggered |
| finding 3 — length re-asked | ✅ | ✅ | ✅ |
| finding 4 — latch vs Gate 5 | ✅ revoked ×3 | ✅ revoked ×3, **held ×1** | ✅ revoked ×2, **held ×2** |
| B-125 — false "earliest" | 🔴 FOUND | ✅ stripped | ✅ stripped |
| B-125b — trailing frame | — | 🔴 FOUND | not triggered |
| clip pool | — | ✅ variant 4/5 | ✅ variant 1/5 |

Finding 4 holding correctly (the latch NOT revoked when the phrase survived
Gate 5) is the strongest single piece of evidence in the set — it shows the
guard is conditional in both directions rather than simply always firing.


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

## 🔴 `latency-eval` IS NOW A LIVE LINE

**2026-08-31, 01:57 UTC.** Vital Edge's Render service (`vitaledge`,
`srv-d8va6cbtqb8s73fbpvag`) was repointed from `vitaledge-onboarding` to
`latency-eval`, went Live at `3a3af7e`, and served a real call five minutes
later. **A push to canonical is now a deploy to real patients.** CLAUDE.md's
"push whenever" block has been corrected; do not act on a cached memory of it.

Rollback is to point that service back at `vitaledge-onboarding`, which stays
deployable — **do not delete it until VE has run a full week on canonical.**

The demo line (+447366263180, `northgate`) is still on this branch, which is the
design working: `SMS_ENABLED` is per-SERVICE, so the demo service keeps
canonical's `false` default and sends nothing while Vital Edge sets it
explicitly. **Canonical's code defaults must stay OFF.**

### What the first folded call proved, and what it did not

Call `CAa52bd716fafa245c8959a4b476aacff7`, build `3a3af7e`, VE's own line:

- **O-1** — the ladder ran `1b REASON → 1c SESSION LENGTH → 2 TIMING`, and
  `check_availability blocked` never appeared: the tool-time gate did not have
  to fire. First confirmation of O-1 on a patient line.
- **O-2** — one hold phrase (`[filler] primary suppressed`).
- `session length captured: 60 minutes` — the pre-dispatch capture works live.
- The diary reader correctly blocked Jonathan's personal calendar entries.
- Sheets appends fine here (it is broken on the demo line).

**NOT proven: the booking write and the owner notification to Jonathan.** The
call was deliberately abandoned at the readback so he was not texted at 2am.
That is the one remaining leg of the Vital Edge cutover.

---

## Phase status

| Phase | State |
|---|---|
| **1 — Headless free-form driver** | ✅ Done. Found a live defect on day one. |
| **2 — Adaptive caller + harvest the corpus** | ✅ **DONE.** Corpus harvested, detectors re-armed, hold speech settled, adaptive caller built and run. |
| **3 — Collapse the tenancy** | 🟢 **VITAL EDGE IS FOLDED — 2026-08-31 01:57 UTC.** Its Render service tracks `latency-eval` (Live at `3a3af7e`), verified by a real call. JV and Theorem remain. |
| **4 — Two contained slot fixes** | ✅ **DONE.** Both landed; the first was mis-scoped here and is corrected below. |

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

### 🔴 The harness ran the engine with its pre-dispatch safeguards removed

**The most important thing found on 30 Aug, and it is about the tool this whole
plan is built on.** Fixed in `0e9ad6c5`.

`run_turn` is not a whole turn. connection.py's transcript handler runs
`capture_duration_choice` and `capture_under_age` against the raw utterance
BEFORE dispatch. `ConversationDriver._pre_turn` mirrored only the verbal phone
confirm, so **every harness run since Phase 1 reported the model unaided.**

Wrong in both directions:

- **It manufactures defects.** A probe of the CA86c320ef guarantee — the caller
  says *"the ninety minute one please"* and the diary must not say 60 —
  reported WRONG twice in four runs, on `vital_edge`, hours after O-1 had been
  pushed to two patient branches. It read as a live P1 in a flow change already
  on live clinics. It is not a defect: with no capture,
  `_resolve_duration_minutes` has nothing to prefer and the model's argument
  wins by default — the failure the capture exists to prevent, reproduced by
  omitting the capture. The same probe after the fix: **captured=90 on 4 of 4,
  every booking 90 minutes, zero WRONG.**
- **It hides real ones, which is worse.** `capture_under_age` is the only
  under-age enforcement on the template clinics. Without it a run shows an
  under-age caller sailing through, and a test written against that output pins
  the wrong behaviour as correct.

**The rule this leaves behind: read `_pre_turn` before trusting any harness
result.** Anything it does not mirror is missing from every run, and a missing
safeguard does not present as "missing" — it presents as the ENGINE failing.
`tests/harness/test_pre_dispatch_captures.py` now derives the capture list from
connection.py's source and fails when one is added and not mirrored, because a
hand-kept list has to be remembered by whoever adds the next one and that is
precisely the step that was missed.

⚠️ **And the reverse is worth stating.** `tests/harness/` is canonical-only. It
does not exist on `jv_v2`, `vitaledge-onboarding` or `theorem-onboarding`, so
**none of the three patient lines has any free-form behavioural coverage at
all.** Nothing landed on 30 Aug changes that.

### The owner's record of a call was built from what the model generated

`750a23d1`. `_append_history` kept the RAW generation in `session["turns"]`,
which `actionable_summary` renders into the summary LLM's context. So a
sentence Gate 5 corrected or deleted on the way out was still reported to the
owner as spoken. On CA8e688605 that was a time the caller was never offered; the
Gate 5f case is worse — a "you're booked in for Thursday" that the caller never
heard was reported as though it had been.

`text` is now what was spoken; the raw moves to a `raw` key. **The trap is the
consumer that still needs the raw:** connection.py's Gate 5g name recovery. It
read `turns[...]["text"]` back when that WAS the raw generation, and swapping
the key alone would have disarmed it silently — the caller asked their name
until they hang up (CA041352eb, four times). `test_name_survives_the_cta_holdback`
does NOT cover it: it passes the raw text straight to `_v3_try_persist_name` and
never exercises the round trip through `session["turns"]`.

Recorded, not fixed: on the media-streams path `session["turns"]` carries **no
caller lines at all** — `_format_turns` labels a turn "Patient" from
`turn["user"]`, which only `twilio.py`'s legacy flow and `brain.py`'s FlowEngine
write, and neither is the live path. So the summary LLM sees one side of every
conversation. `call_summary` is unaffected — its fields come from `collected`
and session keys, not the transcript.


### The recorded post-port baselines were WRONG — measure, do not inherit

The 11:00 handover said **VE 119, JV 109, Theorem 114**. Measured on 30 Aug,
each from two clean sequential runs with both `.env` files copied and
`COLUMNS=250`, the real figures at the same commits were:

| branch | recorded | measured |
|---|---|---|
| `vitaledge-onboarding` | 119 | **105** |
| `jv_v2` | 109 | **103** |
| `theorem-onboarding` | 114 | **106** |
| `latency-eval` | — | **107** |

`pytest-randomly` is not installed, so ordering is not the explanation. The
numbers were stale or taken differently; the code wins (CLAUDE.md §7). **A
recorded count is not a baseline.** Take your own two sequential runs at the
branch tip and diff the failing SETS — every port on 30 Aug was verified that
way and all four came back identical.

### A canonical test file that names a clinic will break on every port

Three instances in two days, and the third was self-inflicted:

1. The ported 2026-08-30 findings file hardcoded `get_clinic("northgate")`.
2. The new O-1 file hardcoded `CHOICE_CLINICS = ["northgate", ...]`.
3. The same O-1 file pinned northgate's `"2. MODALITY THEN TIMING"` wording and
   reported vital_edge — which renders plain `"2. TIMING"` — as badly ordered
   when the rung was in exactly the right place.

`northgate` is the DEMO LINE's clinic and no patient branch ships it. Worse,
`get_clinic` on an unknown id does not raise: it returns a shape whose
`services` is a list of strings, and the renderer dies with `AttributeError:
'str' object has no attribute 'get'` deep inside `clinic_template_prompt`,
which reads as a broken engine rather than a broken test. **Discover the clinic
from `app/clinics/` at run time using the same predicate the code under test is
gated on**, and add a test that the discovery is non-empty — an empty list
makes every parametrized case pass by running zero of them.

### A missing module is a COLLECTION error, not a red test

`5ab3fcc5`'s test file imports `app.hold_speech` at module scope and covers
findings 1, 2 and 3 together. On a patient branch that module does not exist,
so pytest interrupts the ENTIRE run and reports zero failures. The first
post-port run died in 17 seconds looking like a catastrophe and measuring
nothing. Split the unportable section out rather than skipping it, so the
absence is a recorded fact about the branch.

### One prompt edit moves TWO pin tables, and their hashes are not interchangeable

`test_b55_provisional_reschedule_closing` pins `jv_v1`; `test_b57_theorem_cancel_gate`
pins `jv_v1` AND `vital_edge`. They hash differently, and **every value is
branch-local** — the same commit produced `d4fb03b5e5b56c7e` on canonical,
`6c304fe4952d23a1` on vitaledge-onboarding and `20fca990587548ee` on jv_v2.
Recompute with each file's own `_sha`, on the branch you are on. Never copy.
The tables are worth the trouble: they are what proved rung 1c stayed off
demo, theorem and theorem_v3.

### An ad-hoc harness script bypasses the conftest that makes it honest

`tests/harness/conftest.py` loads `ANTHROPIC_API_KEY` from `tests/auto/.env`
and hard-SKIPs without it, because an unauthenticated run does not fail — the
engine's broad except turns it into "Sorry, I had a bit of a blip there", and a
probe scores that as a clean negative result. A script run outside pytest gets
none of that. The first O-1 ordering probe reported 5/5 "NEITHER" for exactly
this reason. Load the key the same way the conftest does, or run it under pytest.


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

### Phase 2 — DONE, 2026-08-29. Branch `feat/situational-hold-speech`.

All three parts landed, plus the hold-speech decision they were meant to settle.

**Mine the obs corpus.** `scripts/harvest_regressions.py` is the driver
`to_scenario.py` was missing — it takes one call_sid, and the corpus has 320
calls judged 2 or worse. Dedups by failure signature, caps per signature: **60
scenarios across 32 distinct signatures**, zero PII, checked twice (generator
and again over the committed files). `tests/auto/scenarios/regressions/` is no
longer empty and `app/obs/regress.py` runs in the ordinary suite.

> ⚠️ **Know what a green run there means.** `regress.py` re-checks a STORED
> transcript; it does not re-drive the engine. It catches a banned phrase coming
> back into a fixture. It cannot tell you a fix worked, and because a scenario
> embeds a historical call with its defects intact, an assertion can only pin
> something that was already true of it. `tests/harness/` re-drives the turn
> loop; `scripts/replay_situational_heads.py` measures a change across the
> corpus. Those are the instruments.

**Re-arm `detect_defects.py`.** Five new detectors, each with a real SID:
B-120 dead-end hold phrase (17), B-121 two hold phrases back to back (27),
B-122 opener welded to the payload (71), B-123 subordinate clause spoken as a
sentence (5), B-124 provisional clinic claimed a write (3). Baseline frozen;
`--check` exits 0.

**Adaptive caller.** Built and run — see "The adaptive caller" below for what
it is, how to drive it, and the four defects it produced.

---

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
(28 commits for JV, 43 for VE). `hold_speech` is gated — and as of 2026-08-29 it
gates a great deal more than it did: the whole situational-head taxonomy, the
head pacing and the clip suppression all sit behind it, so a folded clinic still
sounds exactly as it does today until someone sets `operational.hold_speech`.
The screening wording, availability phrasing and reason-question scoping are
**not** gated. Gate them the same way — default to what the clinic runs today —
or accept the delta knowingly.

Two changes in that branch are NOT behind the flag and reach a folded clinic
immediately. Both are fixes, but know them before you fold:
`_ORPHAN_LEAD`'s widened word list (stops "What's available for Saturday." being
spoken as a sentence) and the deletion of FillerGuard's 2.5s second clip.

### Phase 4 — DONE, 2026-08-29. `4dfd5ecf`, `c7392b4a`

Both items, and the first one was mis-scoped in this document.

- **The published reader now runs the same ladder as the other six** —
  `_cap_presented_slots(_filter_same_day_slots(…))` then
  `_sync_last_offered_to_spoken`. It was returning every published day at once
  and leaving the offer record holding three days while speech named two. That
  record is indexed BY POSITION, so **"the third one" resolved to a date the
  caller was never read out** — B-108b through the seventh door, and the
  ordinal test fails before the fix, so it was a real route rather than a
  cosmetic mismatch.

  ⚠️ **This row said "a latent defect on Vital Edge". It is DORMANT, not
  latent, and not on Vital Edge.** VE moved to `availability_mode: "diary"` on
  8 Aug and its own `clinic.json` says the published mode must not be restored,
  so no clinic reads that path today. It stays worth fixing because
  **`published` is the DEFAULT for a provisional clinic** — the dispatch falls
  through to it whenever `availability_mode` is unset — so the next provisional
  clinic onboarded lands there, and VE's documented fallback is one config key.

  Known gap, named rather than half-wired: `_name_the_other_matching_dates`
  (B-110) is still absent from that reader. It needs `_pref_weekdays`, which
  the reader does not compute — it filters on a preference string only.

- **`test_offer_record_matches_what_was_spoken.py` is re-aimed at the
  property.** The two `inspect` tests read the Acuity body for a string and
  asserted its indentation was 12. Now: an AST walk requiring that every
  function writing `session["last_offered_slots"]` also calls the aligner, plus
  a second test stating the producer count out loud (seven, named) so nobody
  counts them from a grep again — which is how B-110 was got wrong.

The `last_offered_slots` three-contract restructure remains **post-webinar**.
Written down; do not start it.

### The hold-speech decision — SETTLED, and it grew

The doc asked whether FillerGuard's 2.5s second clip should ask the arbiter.
The owner's answer was to **delete it**: the standing rule is that the recorded
filler belongs to the one moment before slots are read out, so two clips in 2.5
seconds breached it independently of the arbiter. One clip cannot stack with
itself, so "one head per turn" is now structural rather than negotiated.

Then the question got much bigger, and the reason is a measurement that changes
how the whole subsystem should be read.

> 🔍 **The dead air is the model, not the providers.** Measured over the 753-call
> corpus: `check_availability` p50 **319ms** / p90 607ms; `lookup_patient` p50
> 210ms; `book_appointment` p50 362ms. Turn time-to-first-audio over the same
> calls is p50 **1,938ms**, p75 2,519ms, p90 3,171ms.
>
> The entire filler architecture was aimed at the provider round-trip.
> `with_filler`'s 4-second secondary escalation is guarding a p90 of 607ms and
> can almost never have fired. **Every turn has ~2s of dead air, not just tool
> turns** — which is why a price question, a cancel request and a symptom got
> either silence or a phrase that lied: `WorkKind` is keyed to five tool names,
> so the arbiter could only speak when a tool ran.

The second finding is what made deterministic wording safe: **the model already
writes the right opener, it just arrives 1.9s late.** Stored payloads open with
"I'm sorry to hear that —", "No problem at all.", "Let's get that moved for
you.", "Got it —", "Apologies for that —". So a head is not an invented filler
phrase. It is the opener the model was going to say anyway, said earlier, with
its duplicate stripped — which is what makes it part of the sentence rather than
a phrase in front of one.

`app/hold_speech.py` gained `Intent` (20 situations), `classify_intent`,
`subject_for`, `INTENT_HEADS`, `render_intent_head` and `strip_head_echo`, all
pure. Wired into `_delayed_filler`, gated on `hold_speech`, so only `northgate`
is affected.

Measured by `python -m scripts.replay_situational_heads` over all 733
transcripts:

| | before | after |
|---|---|---|
| hold phrases / heads | 601 | 1255 |
| calls containing at least one | 172 | 607 |
| that CLAIM a lookup or a write | 379 (63%) | 751 (60%) |
| **dead ends — nothing behind them** | **47 (7.8%)** | **30 (2.4%)** |
| model's duplicate opener removed | — | 496 |
| turns left silent (unchanged) | — | 3101 |

The totals are **not** an improvement claim: a legacy phrase was an extra
utterance, a head is the opening clause of the reply itself. The dead-end rate
is the like-for-like number.

**Timing.** `HOLD_HEAD_DELAY_MS = 600`, not the 3000ms the contentless head
waits for. 3000ms is the price of a *guess* — an empty marker has to earn its
place by the caller having waited. "On price —" is correct the moment they ask
the price, so it does not.

**The clip now stands down instead of chaining.** The pre-head design was chosen
when the head was produced at tool detection (~2.2s), leaving 1.85s only the
recording could cover. Reading the head from the caller's words moved it to
**600ms**, so the clip's lead is ~370ms — and chaining would give "Let me just
check —" then "Let me see what Saturday looks like —", two *let me* clauses a
third of a second apart. So on a turn with a situational head the clip is
suppressed; on a turn without one it still fires, which is what it is for.
Gated on `hold_speech`: on a clinic with no arbiter, suppressing the clip is
silence, not a better phrase.

**The clip pool is cut — five recordings, not one.** `audio_clips/` held a
single `filler_checking.ulaw` despite the rotation machinery shipping weeks
earlier, so every hold moment on every clinic was the identical waveform, to the
byte. That is the owner report of 2026-08-08 — *"latency is great but it sounds
quite robotic"*. Variant 1 is byte-identical and was skipped, so the phrase a
caller hears most often is unchanged. `test_each_pool_has_more_than_one_variant`
had been red since it was written and is now green.

`audio_clips/CLIPS.json` records each clip's wording and whether it ends open;
the shipped clip is CLOSED and a test pins it. The second clip (`filler_moment`)
is deleted — its files stay on disk so a rollback does not go silent, but
nothing loads them.

### Defects found on the way

| | |
|---|---|
| **`join_after_head` deleted the full stop AND the capital after it** — `re.sub(r"([\.!?])([A-Z])", r" ", body)` passes a bare space, so both groups are discarded. "available.The available slots" → "available he available slots" | fixed, `6812a1c3` |
| **`_ORPHAN_LEAD` stopped one word short.** It guards against an opener strip leaving a dangling clause and listed only the adverbial words, so the commonest opener in the corpus — "Let me check what's available for…" — fell through and was spoken as "What's available for Saturday." | fixed, `580be4cb` |
| **FillerGuard's second clip never asked the arbiter** | deleted, `6812a1c3` |

> ⚠️ **`_ORPHAN_LEAD` is canonical-only; `_strip_interim_opener` is on all four
> branches.** So the three live clinics run that strip with **no guard at all**
> and can speak "While I look that up." today. A commit message in this branch
> says the guard is shared — that is wrong, and the truth is worse. This is a
> port worth making.

### Traps this added to the list

- **A text scan cannot tell coupling from prose** — third instance.
  `test_filler_guard_reports_to_the_latch_but_does_not_ask_it` scanned
  `filler_guard.py` for the string `decide_hold` and was broken by a *comment*
  explaining why the call is absent. Replaced with a signature check and an AST
  count.
- **A detector that fires on healthy calls is worse than none.** B-123's first
  version accepted any terminator and matched 103 calls, every one an ordinary
  question ("What's the appointment for?"). A dangling clause ends in a full
  stop. Requiring that took it to 5, the real figure.
- **A shape-based stripper ate a phone number.** "A short leading clause with no
  digits is an acknowledgement" removed "I've got you on oh three three" — the
  digits were spelled as words. What is being stripped is one speech act *we*
  generate, so it is a closed set: use an allow-list.
- **`load_dotenv()` inside `main()` is too late.** `app.config` reads
  `DATABASE_URL` at import and `app.obs.store` reads it from there, so the first
  harvest wrote nothing and reported success — the same failure mode as the
  empty directory it was written to fix.
- **The import-time self-check earns its keep.** It rejected "Of course —" as a
  head: Gate 5b strips "Of course," from model speech, and a phrase the engine
  deletes from the model must not be spoken by the engine.

---

### The adaptive caller — built, run, and earning its keep

`tests/harness/caller.py` + `personas.py` + `verdicts.py`, driven by
`scripts/run_call_suite.py`. Sixteen personas drawn from the obs corpus rather
than imagined: the intent counts across 4,356 stored caller turns decide where
the suite spends its calls. **11.8 minutes, 2,592 caller output tokens, no
Twilio, no phone.**

    python -m scripts.run_call_suite --list
    ANTHROPIC_API_KEY=... python -m scripts.run_call_suite --out logs/suite
    ANTHROPIC_API_KEY=... python -m scripts.run_call_suite --only cancel --show

**The rule that keeps it honest: the caller may not decide whether the call
passed.** It generates the conversation and nothing else; every verdict is a
pure function of the transcript. A test asserts `verdicts.py` never imports
anthropic. An LLM that both drives a test and marks it is not a test, and the
failure is silent — the suite goes green because the caller was in a good mood.

Cost: two model calls per turn. **Zero Twilio cost** — the netfence allows
`api.anthropic.com` and nothing else, and it covers all four transports the
engine can reach the world through, including the `requests` that Twilio's SDK
uses. Across the whole 16-call run exactly one outbound request was blocked
(gov.uk bank holidays).

### What the suite found

Three engine defects on the first full run, each reproduced from a saved
transcript without a phone. All three now have verdicts that catch them
automatically, verified by replaying the transcript that produced them.

1. **A qualified yes read as a refusal — FIXED.** The caller said "I don't think
   I gave you that, but yes, that's my number" and `_phone_confirm_verdict`
   scored it `no`. So `phone_confirmed` stayed False, the PHONE STEP OUTSTANDING
   steer kept rendering, and the model obediently re-asked the phone question
   AFTER the caller had agreed to the booking — until they said "you've already
   asked me that twice". **That is the A4 confirmation loop, 144 instances in
   the obs corpus, reproduced without a phone for the first time.**

   The negation-before-affirmative ordering is a safety property and is
   unchanged. A negation with a plain YES after it now yields `unsure`, which
   cannot satisfy the A1 book gate and routes into the keypad ladder.

   Two things fell out that matter more than the fix: `_PHONE_YES_RE` matches
   "use that one" and therefore "don't use that one"; and `_NO_PATTERNS`
   contains the bare token "no", matched as a SUBSTRING, so "a-**no**-ther"
   scored as a refusal. **Third instance of the substring-negator family here** —
   the screening triggers had it when "know" matched "no".

2. **A hold phrase in front of a goodbye — FIXED.** On the red-flag call the
   caller said "Alright. I'll ring 111 then. Thanks." and heard "Sorry, still
   with you — Take care of yourself." `is_closing()` is checked BEFORE the
   UNKNOWN_SLOW fallback. Deny-by-default in the right direction: "Thanks, could
   you check Thursday?" keeps its head.

3. **A second transfer — FIXED, verified live.** A courtesy "Cheers, thanks"
   after "Putting you through to Priya now" opened a whole new turn that called
   `transfer_to_human` again and repeated the line. Fixed in two halves: the
   executor is idempotent (no second leg, no second alert), and
   `transfer_placed` now stops the turn loop entirely once a leg is placed.

   ⚠️ **The harness cannot verify the upstream half.** It drives
   `LLMStream.run_turn` directly with no `on_transfer` callback, so
   `_on_transfer_request` never runs there. The verdict is split accordingly: a
   repeat carrying `already_in_flight` is a NOTE, not a defect, and the note
   says so in its own text. **Confirmed on a live call instead** — build
   `12db001d1356`, `transfer_to_human` fired exactly once.

4. **Susie dialled the caller back to themselves — FIXED.** Found by that same
   verification call (`CAe825216dd5a03ca5`). `northgate` carries no
   `transfer_phone`, so `TRANSFER_FALLBACK_NUMBER` answered — and the fallback
   is the owner's number, which was also the number the test call came FROM.
   The leg rang out (their line was busy on that very call), Twilio reported
   `no-answer`, and the transfer-miss handler played the voicemail prompt to
   someone who had just been told they were being put through.

   `resolve_transfer_target` now returns None when the target is the caller,
   which routes to the existing no-dial-target recovery — keeps them with Susie
   and offers to take a message. **Any clinic whose fallback is its own owner
   has this the moment that owner rings their own line.**

   ✅ **Closed 2026-08-29 (`deb0dc76`).** `northgate` now names its own
   `transfer_phone`, and the owner's decision was that the demo line's target
   is his own mobile — not a third party, because a real person fielding a call
   from a demo audience with no brief is a worse outcome than no transfer.
   That number was already the target via the hardcoded fallback; naming it
   changes nothing operationally and silences the boot warning.

   ⚠️ **Before the next test call:** a call FROM that number is NOT
   transferred — the guard refuses to dial the caller back to themselves and
   Susie takes a message. Exercising the transfer needs a second handset.

### The harness's own bugs — four, and they rhyme

Worth its own heading because the pattern matters more than the instances. Every
one was a FAKE diverging from the real thing, and every one produced a
convincing false finding:

- `Booking.start` is an ISO string; the verdict read `.hour` off it and silently
  skipped every real booking. **The test passed** — it built its own lookalike
  Booking with a datetime.
- `lookup_patient` was an inert `{"found": False}`, so `cancel`, `reschedule`
  and `changes_mind_mid_booking` rang about an appointment nothing could see.
  The engine correctly said so and the suite called all three CLEAN. **Three of
  sixteen personas were testing nothing.**
- The seed was behind `hasattr(diary, "seed_booking")` and FakeDiary had no such
  method, so the guard silently did nothing.
- The reschedule stub looked for `new_start`; the real schema requires
  `new_slot_iso`. Every move failed, and the engine honestly reported the failed
  write — which is the only reason it was not read as an engine defect.

So the fix is not four fixes.
`test_the_fakes_accept_what_the_real_schemas_require` builds each call from the
SHIPPED `TOOL_*` schemas, and the ack-filler marker, the Booking shape and the
seed are each pinned. **A stub that cannot succeed makes its persona vacuous,
and a vacuous persona reports CLEAN.**

### What is next

In the order I would take it:

1. ~~**Phase 4 — the two contained slot fixes.**~~ **Done** — see Phase 4
   above. The engine correctness backlog from this exercise is now empty.
2. ~~**`northgate.transfer_phone`**~~ — **done**, `deb0dc76`.
3. ~~**The held port to the live clinics**~~ — the **two ungated items are
   done**, 2026-08-30. See "The port — DONE" below.
4. ~~**The four findings of the 2026-08-30 demo call.**~~ **Done** on canonical,
   verified over four demo-line calls.
5. **PORT findings 2, 3 and B-125/B-125b to the three patient lines.**
   **Owner-approved 2026-08-30, this is the next action.** Full instructions
   and the evidence standing behind each are in the HANDOVER block at the top.
6. **O-1, the duration question's position**, and **O-2, two hold phrases in a
   row.** Both owner-reported by ear on 2026-08-30, neither started. See the
   HANDOVER block — O-2 in particular is a real defect with log lines, not a
   wording preference.
7. **Phase 3, the fold.** Blocked on you, not on engineering.

Not started and deliberately so: the `last_offered_slots` three-contract
restructure stays post-webinar.

### The port — the two ungated items are DONE, 2026-08-30

Owner decision 2026-08-29 held everything gated on `hold_speech` until a suite
of test calls. The two items that are **not** gated — they reach a patient line
whether or not anyone ever sets the flag — were released on the owner's
go-ahead and are live on all three patient branches.

| branch | commits |
|---|---|
| `vitaledge-onboarding` | `6ece8afd`, `3d2486ee` |
| `jv_v2` | `cb2683d2`, `945e371c` |
| `theorem-onboarding` | `d692e943`, `1afa95d2` |

- ✅ **`_ORPHAN_LEAD`, widened.** Reproduced on each branch before the fix:
  "Bear with me while I look that up. Thursday at ten is free." was spoken as
  *"While I look that up. Thursday at ten is free."*

  ⚠️ **It does not port as a straight copy, and the reason is worth keeping.**
  On canonical the stripper is reached ONLY through `join_after_head`, which
  returns the original chunk when the strip leaves nothing. The live branches
  call `_strip_interim_opener` DIRECTLY at two sites with no such fallback, so
  the guard — which may legitimately consume the whole chunk — would have
  turned "Let me check. When would you like to come in?" into silence. The
  fallback ported with it as `... or chunk`, and an AST test fails if a third
  call site is ever added without one.

- ✅ **The clip pool.** The four new recordings, byte-identical variant 1
  skipped. Pure audio: the rotation code was already on all three branches with
  nothing to draw from. One consequence in code — the play-duration bound moves
  2.60s → 2.93s, because the longest of the five is 1.72s against the
  original's 1.39s. `_PLAY_SECS_HEADROOM` is 4.0s. All three occurrences of that
  number moved together.

Each branch: two clean sequential baseline runs of the full suite, then one
after. **Exactly one test changed state on each, and it went GREEN** —
`test_each_pool_has_more_than_one_variant[filler_checking]`, red since it was
written. VE 120→119, JV 110→109, Theorem 115→114.

Still HELD, and still waiting on the calls:

- **FillerGuard's second clip.** Still live on all three.
- **The phone-confirm verdict** and **the transfer latch.** Both APPLY to all
  four branches; the A4 loop they fix is the one `detect_defects` counts 144
  times across the corpus.
- **The self-dial guard** in `resolve_transfer_target` — applies wherever a
  clinic has no `transfer_phone`, which today is Theorem as well as northgate.

### The 2026-08-30 call — four findings, all fixed on canonical

The call that licensed the port. All four of the 29 Aug fixes held on build
`9e8d22bcc8fa`, with a log line for each; criterion 1 of the call sheet (zero
stacked pairs) passed, having been the one that failed. Two bonuses: the
booking wrote `duration_minutes: 60` and the diary agreed, and the slot
follow-up opened hidden times instead of one slot per day.

What it surfaced that was new — canonical `f936624c`, `a560f5dc`, `e1ebcf9a`,
`5ab3fcc5`, full suite 123 → 123, **identical failing set**, 22 new tests.

1. **After a numbered readout, no diary head could ever fire again.**
   `_CONFIRM_Q` listed `\bnumber \d\b` to catch a confirm question, and a slot
   readout always says "Number 1, ... Number 2, ...", so from the first offer
   onwards every turn read as answering one. 244 suppressions in the corpus,
   186 from that token.

   **Deleting it would have been wrong**: I read the 186 and most are genuine
   selections that should stay silent. It was a PROXY for a question the engine
   already answers on data — B-90's "is this utterance one of the labels just
   offered?" — so `classify_intent` now takes that verdict as `slot_selection`,
   the same shape as `screen_pending`. That rule had THREE definitions and now
   has one (`utterance_is_slot_selection`): the inline B-90 site, a
   hand-written mirror in B-90's own test, and this proxy.

   **Measured, not argued.** `scripts/replay_situational_heads` over the same
   758-call corpus, run on `origin/latency-eval` and on the fix the same
   morning:

   | | before | after |
   |---|---|---|
   | heads | 1280 | **1466** |
   | calls containing at least one | 609 | 611 |
   | that claim a lookup or a write | 777 (60.7%) | 882 (60.2%) |
   | **dead ends — nothing behind them** | **30 (2.3%)** | **31 (2.1%)** |
   | turns left silent | 3116 | 2930 |

   **+186 heads**, against the 186 suppressions the analysis attributed to
   `number N` — the fix reached the population it was aimed at and no other.
   The dead-end RATE, which is the like-for-like number, went DOWN. One extra
   dead end in absolute terms against 186 more heads.

   `scripts/detect_defects --check` still exits 0, the frozen baseline is
   unchanged, and the newest build still shows no occurrences.

2. **The same sentence twice, six seconds apart.** The deterministic
   exhaustion sentence — "I don't have any further times on that day" — is a
   completeness claim about one offer, so it carries its information once and
   nothing at all the second time. Now earned once per OFFER (not per day: a
   real lookup that puts new times on the same date is a new fact). The second
   ask falls through to a real lookup, which asserts nothing.

   ⚠️ **This one APPLIES to all four branches** and is the next port.

3. **The session length was asked twice**, two minutes after the caller
   answered. The engine half was right throughout — `_service_duration_choice`
   latched and the booking was written at a real 60 minutes. The model simply
   had no way to see it. It now rides in CALL STATE's `already known (do NOT
   re-ask)` list, in BOTH template producers. Deliberately not a suppression
   rule: "suppression cannot beat an instruction", third instance.

4. **My own latch read text Gate 5 may delete.** The pre-tool hold latch is set
   from `full_text`, the raw tokens. At 23:59:19 it latched on "Just a moment
   while I check what's available." — a sentence `_BANNED_SENTENCE_RE` had
   removed one line earlier. Harmless there; the general case is the tool-time
   producer standing down for speech nobody heard. **Revoked rather than
   predicted**, at the end of the streaming call, and the revocation asks
   `_spoken_this_turn` (post-Gate-5) the same question the latch asked
   `full_text`. `_any_tts_emitted` is NOT that question — the gate can delete
   the hold sentence while another sentence of the same reply survives.

**Three existing tests were re-aimed, and all three were text scans.** That is
now instances four, five and six of the recorded trap, and the shape has
changed: these did not fail to tell coupling from prose, they failed to tell
coupling from an EXTRACTION and a revocation from a reset. Each was replaced
with an AST or structural check and each was verified still to catch the defect
it was written for — the arbiter one by injecting an unconditional latch reset
and watching it name the line number.

### B-125 — "the earliest I have is" was not the earliest

Found by the verification call itself, `CA7182593819eac0a8e87a22928f137eb7`,
2026-08-30 07:32. Fixed on canonical, `0ea44018`.

    07:31:58  tool -> Tuesday 1st September, slot_times 08:00 09:05 11:15
                      12:20 13:25 14:30 15:35 16:40
    07:31:59  Susie: "Tuesday 1st September - Number 1, eight in the morning..."
    07:32:15  caller: "uh actually what's the soonest that you've got"
    07:32:18  check_availability BLOCKED - slots already retrieved this turn
    07:32:20  Susie: "The EARLIEST I have is Tuesday 1st September -
                      Number 1, five past nine in the morning."

Eight in the morning was bookable and had been read out twenty seconds before.

**09:05 is not the defect.** Offering the day's unspoken remainder is the
follow-up path working. The SUPERLATIVE on it is the defect — the B-92/B-97
family, a ranking claim the payload does not support.

The read-back guard saw it and correctly declined to act (`read-back time NOT
in the offer and not safely correctable`) because from its point of view this
is a new offer rather than a bad read-back. That warning has been this shape's
only trace.

The clause is stripped, not the sentence — the sentence carries the readout, so
banning it would trade a false ranking for silence.

> ⚠️ **Sized at 25, and I could not tell how many were WRONG.** The pattern
> occurs 25 times across 25 of 760 stored calls, on `jv_v1`, `vital_edge` and
> `theorem_v3` alike — so all three live clinics say it. Most look like the
> first slot of a fresh lookup, which is TRUE, and the guard is conditional so
> those keep their sentence. **How many of the 25 were false cannot be
> determined from obs**: it stores what was SAID and not the tool payload, so
> there is nothing to check the ranking against. The one confirmed false case
> is the one above, and its signature is a SECOND readout on a day already
> offered — which is also the only shape the demo call produced.
>
> That is the honest limit of the evidence. It is a reason to want a detector
> that runs at speech time, not a reason to assume the other 24 were fine.

**Theorem: APPLIES**, along with findings 2 and 3. Three items now owed to the
patient lines, all held for a demo-line call first.

### Calls owed

- **The hold-speech suite that gates the port** —
  `docs/plan/CALL_SHEET_HOLD_SPEECH_2026-08-29.md`. Eight calls on
  `+447366263180` with a stated pass and fail each, the five things being
  verified (two of which are NOT gated and port regardless), and the verdict
  that licenses the port. Not started.

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
| `tests/harness/caller.py`, `personas.py`, `verdicts.py` | the adaptive caller. The caller generates; the verdicts are pure functions of the transcript and a test forbids them importing anthropic. |
| `scripts/run_call_suite.py` | drives all sixteen personas. `--list`, `--only <persona>`, `--show`, `--out <dir>`. One command, ~12 minutes, no phone. |
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
`test_theorem_declined_clinical_screening`,
`test_the_fakes_accept_what_the_real_schemas_require` (a stub that cannot
succeed makes its persona vacuous, and a vacuous persona reports CLEAN), and
`tests/regression/test_the_suite_findings_of_2026_08_29.py` — the four defects
of 29 Aug, three from the suite and one from the call that verified it.

---

## Branch state, 2026-08-30

| branch | tip | note |
|---|---|---|
| `latency-eval` | `61d65180` | canonical; also serves Northgate on the demo line **+447366263180**. Carries the four findings of the 2026-08-30 call plus B-125/B-125b. |
| `jv_v2` | `945e371c` | live — Joint Venture; has the orphan guard and the clip pool |
| `vitaledge-onboarding` | `3d2486ee` | live — Vital Edge; same two |
| `theorem-onboarding` | `1afa95d2` | live — Theorem; same two. Stays separate by decision. |

All three patient branches were pushed on 2026-08-30 after a full-suite
baseline diff each; Render auto-deploys, so the next call on each line runs it.
The build SHA is only visible in the Render log — `[build_info] running build
<sha>` at call cleanup — so that is the deploy proof, not `/health`.

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
