# Stop being the test harness — before the cohort lands

**Status as of 2026-08-29 (evening).** Phase 1 done. Phase 2 done bar the
adaptive caller. Phase 3 mostly
done and now blocked on an owner action. Phase 4 not started, both items
re-confirmed live. Two live-call confirmations banked: the fourth clinic booking
from config alone, and Theorem's emergency intercept. The hold-speech decision is
SETTLED — and the measurement that settled it says the filler architecture was
aimed at the wrong latency source. See Phase 2.

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
| **2 — Adaptive caller + harvest the corpus** | ✅ Corpus harvested, detectors re-armed, hold speech settled. Adaptive caller still open. |
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

**Adaptive caller.** Still not started. It is the one Phase 2 item left.

---

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
