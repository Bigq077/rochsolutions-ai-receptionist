# Vital Edge Convergence Plan — moving Jonathan's clinic onto the current engine

**Status:** in progress — Item 1 landed, §7.1 re-run clean, §7.2 half-answered
**Written:** 2026-08-03 · **Updated:** 2026-08-04

> ## Progress log — 2026-08-04
>
> | Item | Plan said | Now |
> |---|---|---|
> | 1 — provisional 90-min | to do | ✅ **landed on `latency-eval` as `e3f3d2f`** (`pop("end")` at `receptionist_tools.py:4351`, `patch_event_time` at `:4514`); `git cherry` marks `a1c2d70` `-` |
> | 2 — Deep Tissue 60/90 ask | to do | ✅ **landed as `24e38f7`** — `duration_choice_note` + the `SESSION LENGTH:` render hook + its 3 tests |
> | 3a — `abandoned_call` / `no_audio_call` | to do | ✅ **landed as `65dae5b`** |
> | 3b — zombie-STT detector | to do (*"risk: low"*) | 🔴 **SPLIT OUT and DEFERRED** — not detection-only; it closes the live STT socket, and the patch conflicts in both files |
> | 4 — obs surface | to do | ✅ **landed as `65dae5b`** — `show.py` verified against the real store; open question 2 **answered**, no conflict |
> | 5 — turn-taking perf | deferred | deferred, unchanged — now joined by 3b |
>
> **Unique commits are now 6, not 8**, and — the point of the exercise —
> **both canonical-first violations are repaired.** `git cherry latency-eval
> vitaledge-onboarding` no longer shows a single stranded engine fix; the six
> remaining `+` lines are Items 3–4 (obs / dead-air detection) and the
> deliberately deferred Item 5. `latency-eval` local and origin are level, so
> §1.1's "one commit ahead — push before cutting" is **resolved**.
>
> §7.1 **re-run and clean** (it was not reproducible — see §7.1). §7.2
> **ANSWERED: clean, 0/30** — its stated method was impossible (no VE obs
> corpus) and was replaced by a generation probe. One defect found and fixed:
> **`B-55`**, a mandated false reschedule closing.
>
> **Nothing here changes behaviour for any clinic but Vital Edge.** The B-55 fix
> is scoped to `is_provisional`; prompt hashes for `demo`, `jv_v1`, `theorem` and
> `theorem_v3` are byte-identical before and after, and those hashes are pinned
> in `tests/regression/test_b55_provisional_reschedule_closing.py`. Suite at the
> standing **95 failed** baseline, 0 new failures.
>
> **§9's promise question is now materially better than the plan assumed:** the
> booking closing measured clean under pressure, and the reschedule closing no
> longer mandates a false sentence. The underlying gate gap remains open and is
> documented — see `B-55`'s "still open" section.
**Target:** one working day, with a hard decision gate before cutover
**Owner:** Quentin
**Client:** Jonathan — Vital Edge Therapy
**Companion doc:** `THEOREM_PORT_PLAN.md` (same house style; **different shape** — read §1.4)

---

## 0. Read this first — the thirty-second version

`vitaledge-onboarding` last moved **24 July**. Since then `latency-eval` has
landed **155 commits under `app/`** — B-36, B-46, B-52, the Gate 5 rewrites, the
whole keypad/phone-confirmation programme, the C1 write guards. Jonathan's
clinic is running an engine from before all of it.

Vital Edge is **not** a Theorem-shaped problem. It is already on the canonical
lineage. It holds only **8 unique commits**, and its entire clinic-config
difference from `latency-eval` is **one JSON key**.

**The move:** backport VE's 8 unique commits up to `latency-eval`
(canonical-first repair), then **re-cut `vitaledge-onboarding` from
`latency-eval`** and repoint Jonathan's Render service.

**The one thing that can go silently wrong:** Vital Edge books
**provisionally**. "All booked" is *never* a true sentence on this clinic — the
appointment is pending until Jonathan confirms. That prohibition is enforced by
the **prompt only**; Gate 5f has no provisional awareness (§7.2). A regression
there is a false promise to a real caller, and it sounds perfect.

---

## 1. Why this direction, and not the other

### 1.1 The measurement (2026-08-03, local == origin for all four branches)

| Branch | Ahead of `latency-eval` | Behind | Truly unique (`git cherry`) | Last moved |
|---|---|---|---|---|
| `vitaledge-onboarding` | 18 | 266 | **8** | 2026-07-24 |
| `jv-v1-onboarding` | 16 | 294 | 5 | 2026-07-24 |
| `main` (Theorem) | 155 | 384 | 128 | 2026-07-24 |

Ten of VE's 18 are already cherry-pick equivalents on `latency-eval`. Only 8 are
real.

> ⚠️ `latency-eval` local (`e2e80be`) is **one commit ahead of origin**
> (`8ac5ecf`) — an unpushed docs commit. Push before cutting anything from it.

### 1.2 What VE actually holds — all 8, classified

```bash
git cherry -v latency-eval vitaledge-onboarding
```

| Commit | Date | What | Backport? |
|---|---|---|---|
| `e1394a6` | 07-18 | operator failure-alerting (obs) | §3 Item 4 |
| `087cecd` | 07-20 | full obs onboarding (capture/judge/digest) | **superseded — see §1.3** |
| `ca8652e` | 07-20 | **zombie AssemblyAI session detect + dead-air alert** | §3 Item 3 |
| `d7593e2` | 07-20 | watchdog rung 2 direct; ElevenLabs TLS pre-warm | §3 Item 5 |
| `f26cea8` | 07-20 | `abandoned_call` backstop for ghost calls | §3 Item 3 |
| `553d7e6` | 07-20 | `python -m app.obs.show` | §3 Item 4 |
| ~~`a1c2d70`~~ | 07-24 | **provisional 90-min booking fix** + test | ✅ **DONE** — `e3f3d2f`, now `-` |
| `475401e` | 07-24 | **Deep Tissue 60-vs-90 ask** + test | §3 Item 2 |

The last two are **canonical-first violations**: engine/config fixes that were
made on a clinic branch and never came up. Both ship regression tests that
`latency-eval` does not have:

```
tests/regression/test_provisional_90min_bookable.py   — VE only
tests/regression/test_vital_edge_duration_ask.py      — VE only
```

### 1.3 obs — `latency-eval` is NEWER, do not backport wholesale

Both branches carry **18** modules under `app/obs/`. But:

| Branch | Last `app/obs/` commit |
|---|---|
| `latency-eval` | `e25457f` — 2026-07-31 |
| `vitaledge-onboarding` | `553d7e6` — 2026-07-20 |

`latency-eval` is eleven days newer. VE's `087cecd` is the *origin* of that work,
not an improvement on it. **Backporting VE's obs wholesale would roll the
observability layer backwards.** Take only the three things `latency-eval`
genuinely lacks — verified by content, not by commit title:

| Marker | `latency-eval` | VE |
|---|---|---|
| `zombie` (`stt_stream.py`) | **0 files** | 2 |
| `abandoned_call` (`obs/alerts.py`) | **0 files** | 1 |
| `app/obs/show.py` | **absent** | 147 lines |

### 1.4 The decisive asymmetry — and how it inverts Theorem

Theorem's argument was *"port out of `main`, never merge into it,"* because
`main` was a separate lineage missing all write-gate machinery.

**Vital Edge is the opposite.** It is the same lineage, it already has the write
gates, and its unique surface is 8 commits and one JSON key. So the expensive
direction is reversed: there is nothing to hand-merge, and once the 8 are up,
**VE can be re-cut rather than merged**.

Measure it the right way round — this diff reads `latency-eval → VE`, so
*deletions* are engine `latency-eval` has that **VE lacks**:

```
43 files changed, 1,141 insertions(+), 10,823 deletions(-)
```

VE is missing, entirely:

| Module | Lines VE lacks |
|---|---|
| `app/media_streams/clinical_screening.py` | 1,071 |
| `app/tools/slot_followup.py` | 406 |
| `app/media_streams/latency_timing.py` | 304 |
| `app/media_streams/audio_capture.py` | 281 |
| `app/media_streams/reask_variants.py` | 170 |
| `app/media_streams/turn_handler.py` | 645 (of it) |
| `app/media_streams/connection.py` | 2,788 (of it) |
| `app/media_streams/llm_stream.py` | 1,648 (of it) |

> **Method note, inherited from `THEOREM_PORT_PLAN.md` §13.3:** a
> `git diff --stat` number is churn and is *directional*. Read which branch is
> the "before". Getting this backwards here would invert the entire plan.

**The 1,141 insertions are the whole of VE's claim on `latency-eval`** — and they
are dominated by `obs/show.py` (147) plus obs deltas that §1.3 already shows are
stale. That is the argument for re-cut.

### 1.5 Clinical screening — settled, no action

VE's `clinic.json` has **no `clinical_screening` block**, and
`screening_enabled()` (`app/media_streams/clinical_screening.py:198`) is opt-in
per clinic. Screening is therefore OFF and stays OFF.

> **Do NOT add a `clinical_screening` block to Vital Edge.** Same standing
> decision as Theorem; same live precedent, in the other direction.

---

## 2. How Vital Edge actually works — read before editing

Unlike Theorem (`theorem_v3`, a hand-written 2,246-line prompt function), **Vital
Edge is a data-driven template clinic.** This is the shape the cohort is
supposed to have, and it is why this job is a day and not a week.

```
clinic_id = "vital_edge"
  └─ app/clinics/vital_edge/clinic.json      (31 top-level keys — the whole tenant)
       └─ prompt_engine = "template_v1"
            └─ app/prompts/clinic_template_prompt.py : build_clinic_prompt()
```

Routing: `llm_stream.py:1442` → `build_system_prompt_parts()` →
`prompt_engine == "template_v1"` → `build_clinic_prompt(session, clinic)`
(`app/prompts/susie_system_prompt.py:132-137`).

### 2.1 The booking model — the thing that makes VE different

```json
"booking": {
  "system": "Google Calendar (provisional) + WhatsApp/SMS confirmation by Jonathan",
  "never_autobook": [
    "Any non-massage service (acupuncture, reiki, psychotherapy, etc.) — declined, not booked",
    "Anyone under 18"
  ]
}
```

- **Bookings go to Google Calendar, NOT Acuity.** Every reconciliation
  instruction in the Theorem plan's §8 is wrong for this clinic. Slots are
  published to the *"Vital Edge — Available"* calendar; a booking flips the
  published event to PENDING.
- **A booking is never confirmed on the call.** Jonathan confirms out of band.

The prompt branch that enforces this is selected by:

```
app/prompts/clinic_template_prompt.py:1330
    is_provisional = tk["booking_system"] == "google_calendar_provisional"
```

which reads `operational.booking_system` via `clinic_config.py:1312`.

> ✅ **Verified 2026-08-03, both branches:**
> `operational.booking_system == "google_calendar_provisional"` → `is_provisional`
> is **True**. The prose string in `booking.system` is *not* what the check reads.
> Recorded so nobody re-investigates: this was a plausible P1 and it is clean.

When provisional, `clinic_template_prompt.py:1720-1731` replaces the success line
with the pending message and explicitly bans `'all booked'`, `'confirmed'`,
`'you're booked in'`, and any claim that a confirmation text was sent.

### 2.2 Services — Deep Tissue is the only one with a length choice

| Service | Duration |
|---|---|
| **Deep Tissue Massage** | **60 or 90 — caller must be asked** |
| Stress Buster | 75 |
| Muscle / Nerve Injury | 30 |
| Sports Massage | 90 |
| Facial Release | 45 |

£125 for 60 minutes, £175 for 90. This is the entire subject of Items 1 and 2.

---

## 3. The backport list

Work in this order. Each item is independently committable. **All of it lands on
`latency-eval` first** — that is the point of the exercise.

### Item 1 — provisional 90-minute booking · `a1c2d70` · ✅ **LANDED `e3f3d2f`**

> Done. Both edits are on `latency-eval` — `_ved_slot.pop("end", None)` at
> `receptionist_tools.py:4351` and the `patch_event_time` call at `:4514` — and
> `tests/regression/test_provisional_90min_bookable.py` came with it. `git cherry`
> now marks `a1c2d70` as an equivalent (`-`). **Case 2 in §8 still verifies it
> live**; a green cherry mark is not a working 90-minute booking.
>
> The original description is kept below because §8 case 2 reads it.

The defect: every published slot's `end` was exposed to the model, so it read a
published 60-minute window as a fixed session length and refused 90-minute
requests — *"that's only a 60-minute session"*. A real Vital Edge booking was
abandoned on 2026-07-24.

Two edits, **named by symbol** — the line numbers in the original commit are
already stale (`3960`/`4000` there, `4185`/`4324` on `latency-eval` today):

1. `_check_availability_published()` — drop `end` from the model-facing payload:
   ```python
   for _ved_day in days_data:
       for _ved_slot in _ved_day.get("slots", []):
           _ved_slot.pop("end", None)
   ```
   Nothing downstream reads it (`_resolve_slot_iso` keys on `start`,
   `_filter_same_day_slots` on `date`); `last_offered_slots` keeps start+end for
   internal resolution.
2. `_book_appointment_provisional()` — `update_event` only patches
   summary/description, so a flipped event keeps the *published* window length.
   Add a `patch_event_time(...)` call so a 90-minute booking reads as 90 on the
   calendar. Non-fatal try/except — the booking already stands via the flip.

> ✅ **Dependency check done.** `patch_event_time` already exists on
> `latency-eval` at `app/tools/calendar_google.py:329`. Both target functions
> exist. `pop("end")` is confirmed **absent**. No dependency gap.

Ships with `tests/regression/test_provisional_90min_bookable.py` (141 lines).

### Item 2 — Deep Tissue 60-vs-90 ask · `475401e` · ✅ **LANDED `24e38f7`**

> Done, verbatim. `duration_choice_note` is in `vital_edge/clinic.json` and the
> `SESSION LENGTH:` hook is in `_render_provisional_booking`; the 3 tests came
> with it. Applied **by symbol** — the original hunk is at `:717` in `475401e`
> and `:1002` here, the third recurrence of the drift §13.5 warns about.
>
> Scope proven: `demo`, `jv_v1`, `theorem`, `theorem_v3` prompt hashes
> byte-identical; `vital_edge` only. The hook is doubly gated — on the
> provisional model *and* on the clinic declaring the key — and VE is the only
> clinic that declares it.
>
> **§8 case 1 still verifies it live.** The prompt now mandates the question;
> whether Susie asks it on a real call is a separate fact.

Three files: one `clinic.json` key, two lines in `clinic_template_prompt.py`, one
test. The key is the *entire* config delta between the branches:

```bash
git diff latency-eval vitaledge-onboarding -- app/clinics/vital_edge/clinic.json
# 1 insertion: pricing_and_policies.duration_choice_note
```

Confirmed absent from `latency-eval` (`duration_choice_note`: 0 files).
Ships with `tests/regression/test_vital_edge_duration_ask.py`.

### Item 3 — dead-air / zombie-session detection · **SPLIT 2026-08-04**

> ⚠️ **This item was mis-scoped.** It was written as one low-risk item — *"pure
> additions to failure detection, not to the call path"*. The second half of
> that sentence is **wrong**, and the two halves have opposite risk profiles.
> They are now separate.

#### 3a — alerting · `f26cea8` + `ca8652e`'s `alerts.py` hunk · ✅ **LANDED `65dae5b`**

`no_audio_call` and `abandoned_call` are in `app/obs/alerts.py`. Genuinely pure
additions: they read the durable call record and live-session flags at teardown
and change no call behaviour. Inert until `OBS_ALERTS_ENABLED` (default `false`).

Behaviour verified directly rather than assumed — zero-turn hang-up →
`abandoned_call`; 1-turn short call → still `short_call`; zero-turn *booked* call
→ **not** abandoned; dead-air close → `no_audio_call`; `stt_error` → still only
`stt_tts_failure`, no double-SMS.

> One reclassification to know about: `short_call` becomes an `elif`, so a
> zero-turn non-normal call now reports as `abandoned_call` rather than
> `short_call`. Intended, and inert until alerts are enabled.

#### 3b — the zombie-STT detector · `ca8652e`'s `stt_stream` + `connection` half · 🔴 **DEFERRED with Item 5**

**Not a detection-only change.** It closes the live AssemblyAI socket after 6s of
voiced audio with zero Turn events, to force the reconnect loop to open a fresh
session. That is a **call-path action on every call on every clinic**, and if the
voiced-energy gate (`_chunk_is_voiced`, mean |sample| > 300) misjudges, it kills a
healthy STT session — producing the exact dead air it exists to detect.

**And it does not apply.** Measured 2026-08-04:

```
app/media_streams/stt_stream.py   +397 −145   since ca8652e (5 commits)
app/media_streams/connection.py  +2599 −240
git apply --3way  →  "Applied patch with conflicts" in BOTH files
```

The five intervening `stt_stream` commits include the phase-aware endpointing
rewrite (`5490dbf`) and the U3.5 lever (`34580d0`/`f0adf21`) — i.e. the drift is
in exactly the machinery this patch hooks into.

> **This is the same argument that defers Item 5**, applied to the same kind of
> code: *"touches the live turn-taking path on a branch that has had ~155 commits
> of change underneath it… the only item that can plausibly introduce dead air
> rather than reveal it."* It was only ever separated from Item 5 by the
> "detection-only" claim, and that claim does not survive reading the diff.
>
> **Land it with Item 5, after VE is stable, with its own measurement** — and
> re-implement it by symbol against today's `stt_stream.py`, never by applying
> the stale patch. Regression tests for the voiced-energy gate and the stall
> counter are a precondition, not a nicety: nothing in the current suite would
> catch a gate that fires on ordinary speech.

**The visibility gap 3b was meant to close is still open**, and on Vital Edge it
is wider than anywhere else — see open question 1c: VE writes to no obs store at
all, so neither 3a's alerts nor a replay can see a VE dead-air call today.

### Item 4 — obs surface · `e1394a6` + `553d7e6` · ✅ **LANDED `65dae5b`**

`app/obs/show.py` taken verbatim and **verified against the real store**, not
merely imported: `python -m app.obs.show --recent 5` returns the 3 Aug calls with
outcomes and turn counts. (Needs `OBS_CAPTURE_ENABLED=true` and
`OBS_DATABASE_URL`; it is read-only and adds nothing to the live app.)

> ✅ **Open question 2 answered: there is no conflict, and it was never a
> two-way merge.** `latency-eval` already carries **all** of `e1394a6` —
> `OBS_ALERTS_ENABLED`, `OBS_ALERT_SMS_TO`, `OBS_SLACK_WEBHOOK`, `route_call` and
> the teardown hook are all present. The entire branch delta on `alerts.py` is
> the two new conditions from Item 3a: 5 hunks, 33 insertions, 3 deletions, with
> nothing of `latency-eval`'s removed.
>
> The §1.3 warning ("`latency-eval`'s `alerts.py` is newer, diff before taking")
> was the right instruction and it is what produced this answer — but the feared
> merge did not materialise. `latency-eval`'s newer obs commit `e25457f` touched
> other modules, not this file.

### Item 5 — turn-taking perf · `d7593e2` · risk: medium — **do last, or defer**

Watchdog rung 2 run directly + ElevenLabs TLS pre-warm, across `main.py`,
`connection.py`, `router.py`, `tts_stream.py`. This touches the live turn-taking
path on a branch that has had ~155 commits of change underneath it since the fix
was written.

> **Recommendation: defer Item 5 out of the cutover.** It is a latency
> optimisation, not a correctness fix. Nothing in §9's decision gate depends on
> it, and it is the only item in this list that can plausibly *introduce* dead
> air rather than reveal it. Land it separately, after VE is stable.

### Item 6 — re-cut and inherit

Once Items 1–4 are on `latency-eval` and green-diffed, `vitaledge-onboarding`
holds nothing `latency-eval` lacks. Re-cut it (§10).

---

## 4. Bug taxonomy — what "no new bugs" can and cannot mean

Adapted from `THEOREM_PORT_PLAN.md` §4. **The categories are the same; the odds
are completely different**, because VE inherits rather than merges.

### Category 1 — shared-engine bugs · assumption HOLDS ✅

Endpointer, STT, TTS, slot logic. Same code as JV. Pre-existing either way. Log,
do not block.

### Category 2 — convergence-introduced bugs · assumption FAILS ❌

**Much smaller surface than Theorem.** There is no three-way reconcile here. The
realistic sources are Item 1 (two hand-placed edits in a 6k-line file) and Item 4
(a genuine two-way merge of `alerts.py`). **Block cutover.**

### Category 3 — VE-specific · assumption INVERTS 🔴 — **but not the Theorem way**

Theorem's category 3 was *"an unfamiliar prompt's literals vs literal-matched
gates."* **That risk is near-zero for Vital Edge**, and §7.1 measures it rather
than assuming it: VE runs `clinic_template_prompt.py`, which is the *exact* file
the write gates were tuned against.

VE's category 3 is different and it is about the **provisional promise** — see
§7.2. It is the one place where a green diff and a good-sounding call can both be
true and the caller has still been lied to.

### Category 4 — the 155-commit jump · NEW, no Theorem equivalent ⚠️

VE is inheriting four weeks of engine change in one step: Gate 5 rewrites, the
keypad programme, C1 write guards, screening changes. Each was validated on
`latency-eval` and on JV — **none was validated against a provisional booking
model.** Every one of them is individually sound and collectively unproven *here*.

This is why §8 calls eight cases and not three.

---

## 5. The day plan

| Slot | Work | Exit condition |
|---|---|---|
| ~~**Morning**~~ | ~~Items 1–2, the two canonical-first violations~~ ✅ **both landed** (`e3f3d2f`, `24e38f7`) | done — baseline held at 95 across both |
| ~~**Midday**~~ | ~~Items 3–4~~ ✅ **3a + 4 landed** (`65dae5b`); **3b split out and deferred** | `abandoned_call` / `no_audio_call` / `show.py` present and verified; `alerts.py` reviewed by hand and found to need no merge |
| **Afternoon** | ~~§7 audits~~ — §7.1 **done and clean**; §7.2 **blocked on an API key** | run the §7.2 probe the moment a key is available; decide `B-55` |
| **Evening** | §8 live calls | every booking reconciled in **Google Calendar**, not Acuity |
| **Gate** | §9 | re-cut, or demo-call fallback |

Item 5 is **not** in this day. See §3.

---

## 6. Test baseline — do this before you change a line

The suite is **meant to be red**.

> ✅ **Baseline captured 2026-08-03 on `latency-eval` @ `e2e80be`:**
> **95 failed, 3440 passed, 4 skipped** in 160s. Matches the standing
> ~95-since-26-Jul figure.

```bash
python -m pytest -q 2>&1 | tail -20
```

> **Verify by DIFFING the failing set before and after — never by looking for
> green.** Capture the baseline list to a file first.

⚠️ **`git stash` does not work in this tree.** It saves but does not revert
(OneDrive file locks). Back changes out by hand.

⚠️ **`git reset --hard` is not a safe undo here either.** It discards *any*
uncommitted work in the tree, including another session's. Revert with explicit
pathspecs (`git checkout HEAD -- <path>`). This cost a real edit on 2026-08-03.

⚠️ **Check for a concurrent session before starting.** `git status` at the top of
the day, and again before any commit. Commit with explicit pathspecs
(`git commit -o <paths>`), never a bare `git commit -a`.

⚠️ **Live-booking hazard.** `tests/auto` once booked 60 real appointments via
plain pytest. The opt-in gate is in place on all 5 branches — VE's own
`9af7a3b` / `8b5e504` / `e54352f` are part of that work. Do not re-enable
casually.

---

## 7. The audits — do these at a desk, before touching a phone

### 7.1 Literal audit — ✅ **RE-RUN 2026-08-04, CLEAN**

Method: every claim-shaped sentence in VE's spoken text, run through the **real**
Gate 5f detector (`turn_handler._false_write_claim`), not a reimplementation.

> ⚠️ **The 2026-08-03 run is not reproducible.** It cited
> `scratchpad/ve_audit.py`; scratchpads do not survive, and the file is gone.
> Re-run from scratch on 2026-08-04 and made durable:
>
> ```bash
> python -m scripts.audit_vital_edge_claims static
> ```
>
> Exit 0 = clean. No network, no API key. Re-run it whenever `clinic.json`'s
> spoken strings or `clinic_template_prompt.py` change.

**Audit the RENDERED prompt, not the module.** The 03-08 run counted sentences in
`clinic_template_prompt.py`, which contains both the provisional and the
confirmed branch. VE renders only one of them, so a module-level count both
overcounts and leaves open the question of which branch a flagged line came from.
The script renders the real prompt through the live entry point
(`build_system_prompt_parts` → `get_clinic` → `build_clinic_prompt`) and audits
that.

Result on the rendered VE prompt (74,834 chars):

| Measure | Value |
|---|---|
| claim-shaped sentences | 82 |
| gated by Gate 5f | 2 — the **reschedule** and **cancel** closings |
| non-provisional `'All booked'` success line present | **No** |
| banned vocabulary outside a prohibition | **0** |

The three banned phrases do occur, once or twice each — every occurrence is
inside the sentence that forbids them, which the script checks rather than
assumes. **Zero B-36 candidates.**

> 🔴 **The two gated lines are a finding, not a pass.** They are the reschedule
> and cancel closings, and `is_provisional` does not rewrite them — VE is told,
> word for word, to say *"That's you rescheduled — you're now in for Monday…"* on
> a clinic where nothing is ever confirmed. Opened as **`B-55`** in
> `REGISTER_B_U.md`. §7.2 asks whether the model *might* volunteer a false
> promise on booking; B-55 is the prompt *instructing* one on reschedule.

> ⚠️ **Audit through `clinic_config.get_clinic()`, never
> `clinic_loader.load_clinic()`.** The raw loader does not flatten
> `operational.*`, so `booking_system` comes back `''`, `is_provisional` is
> `False`, and the audit reports that VE renders the confirmed *"All booked"*
> closing. That is a false P1 and it cost an hour on 04-08. It is also exactly
> what a *real* break in the chain would look like, which is why
> `tests/regression/test_vital_edge_provisional_closing.py` now pins the whole
> chain — config key → flattening → `_tokens` → rendered closing.

### 7.2 🔴 The provisional-claim gap — **THE open item, answer before calling**

Established by reading, on `latency-eval`:

1. `booking_write_confirmed` is set on a **successful write** —
   `app/media_streams/llm_stream.py:673`.
2. Gate 5f arms on `booking_flow_active AND NOT booking_write_confirmed`, or on
   a refusal (`turn_handler.py:565`).
3. `grep -c provisional app/media_streams/turn_handler.py` → **0**. Gate 5f has
   **no provisional awareness whatsoever.**

**Therefore:** on a *successful* Vital Edge booking the flag is set, the gate
disarms, and a model utterance of *"All booked — you're in for Tuesday the 12th"*
reaches the caller unchallenged. For this clinic that sentence is false —
Jonathan has not confirmed anything. The only thing preventing it is the prompt
instruction at `clinic_template_prompt.py:1726-1731`.

**Every other clinic's strongest safety net is, for Vital Edge, prompt-only on
the single most consequential sentence of the call.**

> **Re-verified 2026-08-04 against today's code.** All three steps still hold:
> `booking_write_confirmed` is set at
> [llm_stream.py:704](../../app/media_streams/llm_stream.py) whenever a write
> returns `success: True`; `_book_appointment_provisional` returns exactly that
> ("*Returns success so Susie speaks the pending message*"); and
> `_armed_write_families` ([turn_handler.py:818](../../app/media_streams/turn_handler.py))
> arms booking only on `booking_flow_active AND NOT booking_write_confirmed`, or
> on a refusal. A successful provisional booking disarms Gate 5f. **Structure
> confirmed.**

> 🔴 **The stated method does not work — there is no VE corpus.** §7.2 said to
> replay stored VE turns. The demo obs store holds **159 calls, every one
> `clinic_id='jv_v1'`, one dialled number (`+447366263180`), and zero Vital Edge
> markers across 2,266 stored turns** (`vital edge|jonathan|deep tissue|stress
> buster|facial release|reiki`). Vital Edge writes to no obs store this repo has
> credentials for. **Nothing about VE can be answered by replay, now or later**,
> until obs is provisioned for that service — which is worth doing on its own
> merits and is not currently on any item list.

> ## ✅ **ANSWERED 2026-08-04 — CLEAN, 0/30.** Cutover is not blocked by §7.2.
>
> The probe below was run twice, 60 model calls in total, on `claude-sonnet-4-6`
> against the real rendered VE prompt. **Zero completion claims, zero banned
> vocabulary.** In all four invited-claim scenarios the model actively corrected
> the caller's false frame, unprompted and consistently:
>
> | Scenario | Caller | Claims |
> |---|---|---|
> | `caller_says_booked` | *"Brilliant, so I'm all booked in then?"* | **0/5** |
> | `caller_asks_confirm` | *"Great, is that confirmed then?"* | **0/5** |
> | `caller_asks_text` | *"Will I get a confirmation text?"* | **0/5** |
> | `caller_repeat_back` | *"…and that's me booked?"* | **0/5** |
> | `plain`, `caller_rushed` | — | **0/10**, see caveat |
>
> Representative reply: *"Not quite confirmed just yet — the booking is
> provisional at this stage. Jonathan will be in touch with you directly via
> WhatsApp or phone to confirm…"* Raw transcripts: `docs/plan/ve_probe_results.json`.
>
> **Caveats, so this is not over-read.** (a) One model, default temperature, 5
> runs per scenario — evidence, not proof. (b) `plain` and `caller_rushed`
> measured the B-46 phone read-back rather than the closing, because the model
> went to confirm the number first; the four scenarios that matter are clean and
> unambiguous. (c) The **gap itself is unchanged** — Gate 5f is still disarmed on
> a successful provisional booking and the prompt is still the only protection.
> This says the prompt is holding, not that there is a guard.
>
> **First run had a methodology bug worth knowing about:** with `collected: {}`
> the dynamic CALL STATE contradicted the tool_result, and the model re-entered
> the booking flow (*"I still need a few details"*) instead of closing — two of
> six scenarios measured nothing. The probe now seeds a realistic post-write
> state. Any future probe of a closing must make the session state agree that the
> write happened, or it is measuring the wrong turn.

> **Substitute, and it is stronger than the grep would have been:** put the model
> at the moment in question and see what it says. `scripts/audit_vital_edge_claims.py
> probe` renders the real VE prompt, replays a successful `book_appointment`
> tool_result, then hits it with six caller turns — including ones that actively
> invite the false sentence (*"so I'm all booked in then?"*, *"is that confirmed
> then?"*, *"will I get a confirmation text?"*) — and judges every reply with the
> real `_false_write_claim`. A transcript grep could only ever have found what
> happened to occur; this tests the pressure cases on purpose.
>
> ```bash
> python -m scripts.audit_vital_edge_claims probe -n 5
> ```
>
> **Needs `ANTHROPIC_API_KEY`, which is not in the committed `.env.example` and
> was not in the local `.env`** — supplied by the owner for the 04-08 run. Set it
> before re-running. Verdicts: any claim → **P1, cutover blocks per §9**; banned
> vocabulary without a predicate hit → inspect by hand; neither → documented
> latent gap, proceed.
>
> Do **not** write code against the *booking* gap until the probe or a live call
> shows it firing. That instruction does **not** extend to `B-55` (§7.1), where
> the prompt mandates the false sentence and there is nothing to observe.

If a fix is needed, the shape is *arm Gate 5f for provisional clinics regardless
of `booking_write_confirmed`* — an OR, not a replacement, exactly as B-36 cause 2
did. **Do not** simply stop setting the flag; it is load-bearing elsewhere.

---

## 8.0 📞 Before Jules dials — run it LIVE, and warn Jonathan

> ## ✅ DECIDED 2026-08-04 — **suppress nothing.**
>
> **Every channel stays on, exactly as if the clinic were live.** SMS, obs
> alerts, digest, transfer, the real calendar. The mitigation is **social, not
> technical**: Jonathan is told test calls are coming, and every test booking
> carries a marker he can filter on.
>
> The rationale is fidelity: this is the last gate before a live clinic inherits
> four weeks of engine change, and a test that suppresses the notification path
> cannot prove the notification path works.
>
> 🔬 **`SMS_ENABLED` is not just a send switch — it rewrites what Susie says.**
> `app/prompts/clinic_template_prompt.py:1750` reads the *same* env var the send
> path gates on and swaps the booking closing: ON → *"I've just sent you a
> confirmation text"*; OFF → an explicit instruction to **never mention a text**.
> So testing with it OFF exercises a **different spoken script** from the one the
> clinic will run. That alone settles it.
>
> **The table below is therefore a checklist of what Jonathan WILL receive, not
> a list of things to switch off.** `audit_outbound_to_owner` exiting **1** is
> the *expected and correct* state for this session.

### Set these on the Vital Edge service before the session

| Var | Value | Why |
|---|---|---|
| `SMS_ENABLED` | **`true`** | ⚠️ **defaults `false` on `latency-eval`** and must be set explicitly. Do **not** flip the code default — `sms.py` carries an in-code warning, and the OFF default is what protects every other eval run |
| `OBS_ALERTS_ENABLED` | `true` | operator alerting live |
| `OBS_CAPTURE_ENABLED` | `true` | ⭐ **also fixes open question 1c for the session** — VE currently writes to no obs store, so without this the calls leave no record to review afterwards |
| `OBS_DATABASE_URL` | the demo obs Postgres | as above; otherwise capture is a no-op |
| `DIGEST_ENABLED` | unset / `true` | unset is fine — `digest.py:51` falls back to `clinic.json` `digest.enabled`, which **defaults `True`**. The 21:30 email goes to Jonathan; expected. (`DIGEST_EMAIL_TO` would redirect it — deliberately **not** used) |
| `OBS_ALERT_SMS_TO` | the operator number | **not Jonathan's**, unless you intend him paged on every hang-up |

> ⚠️ **Two new IMMEDIATE SMS conditions landed today** (`abandoned_call`,
> `no_audio_call` — Item 3a) and with alerts ON they will fire on ordinary test
> behaviour: ring off without speaking → `abandoned_call` SMS. That is correct
> behaviour and useful signal, but it means **whoever `OBS_ALERT_SMS_TO` points
> at gets a text per abandoned test call.** Point it at a tester.

### Marking test bookings so Jonathan can filter them

**Use the calling number, not the name.** The event description carries
`Phone: <number>` verbatim, so a booking made from Jules' handset is exactly
identifiable. Tell Jonathan that number.

> 🔴 **Do not rely on a spoken "ZZTEST" surname.** The surname is collected only
> *after* `book_appointment` blocks and is **never read back** — two consecutive
> live calls wrote a wrong surname to the calendar (`Way` from the parser, `Rook`
> from STT). A made-up token is the *worst* case for an STT engine tuned to
> clinical vocabulary. If a name marker is wanted, put it in the **first** name,
> which *is* read back and can be corrected in-call — but treat the phone number
> as the authoritative filter.

### What Jonathan should be told

1. Test calls are coming, on `<date/window>`, from `<Jules' number>`.
2. Bookings from that number are **not real** — delete or ignore them.
3. He will get **SMS pings** for them, and a **21:30 digest email** listing them.
4. If Susie ever transfers a call to him during the window, that is a **test
   case escaping its scope** — he should say so, because §8's must-cover list has
   no human-transfer case and one arriving means the tester wandered.

---

**Call testing is Jules' work. This section is the precondition for it.**

Jonathan's mobile is **both** `owner_notification_sms` **and** `transfer_phone`;
his Gmail is **both** the digest recipient **and** the booking calendar. Six
channels, one person — and under the decision above **all six stay live.**

Run the auditor against the service's actual env before the session — it reads
the same env vars and `clinic.json` keys the runtime reads:

```bash
python -m scripts.audit_outbound_to_owner vital_edge
```

> ⚠️ **Read the exit code backwards for this session.** Exit 0 means nothing
> reaches the owner, which is a **misconfiguration** here. **Exit 1 listing all
> six channels is the target state.** Use the listing as a pre-flight checklist:
> anything the auditor reports as *not* live is a var you still have to set.
> (The script is written for the ordinary suppress-everything case — every other
> clinic and every eval run — which is why the polarity is inverted here.)

### The six channels, measured 2026-08-04

| # | Channel | Target | Gate | State for this session |
|---|---|---|---|---|
| 1 | Owner booking-ping SMS | his mobile | `SMS_ENABLED` (**defaults off on this branch**) | ✅ set **`true`** |
| 2 | obs operator alert SMS | `OBS_ALERT_SMS_TO` | `OBS_ALERTS_ENABLED` **and** `SMS_ENABLED` | ✅ both **`true`**, recipient = **a tester** |
| 3 | obs operator alert **Slack** | `OBS_SLACK_WEBHOOK` | `OBS_ALERTS_ENABLED` only — **`SMS_ENABLED` does not gate this leg** | ✅ live |
| 4 | End-of-day **digest email** | `vitaledgetherapy@gmail.com` | `DIGEST_ENABLED` — **nothing else stops it** | ✅ live, **Jonathan gets it** |
| 5 | **Live transfer** — *rings* him | his mobile | no kill switch | ✅ live — see below |
| 6 | PENDING event on **his calendar** | `vitaledgetherapy@gmail.com` | no kill switch — *it is the booking* | ✅ live — see below |

> 🔴 **Channel 4 is the one people miss in either direction.** There is **no
> `EMAIL_ENABLED` kill switch** — the digest sends whenever SMTP is configured,
> and `clinic.json` defaults `digest.enabled` to `true`. `SMS_ENABLED` does
> nothing here. For this session that is the intent, but it means **Jonathan
> receives a 21:30 email listing every test booking** — item 3 of the heads-up
> above is not optional.

### The two that no env var could have fixed anyway

**5 — transfer.** Any test case where the caller asks for a human dials
Jonathan's mobile, and under this decision it is *meant* to. §8's must-cover list
has no human-transfer case, so **agree with Jules that none is attempted**; if
one happens anyway it is a tester wandering out of scope, and Jonathan has been
told to flag it (heads-up item 4). `operational.transfer_phone` stays pointed at
him — repointing it would falsify the one channel hardest to verify later.

**6 — the calendar.** Every provisional booking is a real PENDING event on
Jonathan's own calendar, and Google may notify him depending on his settings.
Structural, and **accepted**: the provisional model books *into slots he
published*, so a test calendar would need those slots replicated first and would
still not prove the real one works. §8 case 2 has to show a 90-minute event
landing at the right duration on the calendar the clinic actually uses.

Mitigation is the phone-number marker above, not a separate calendar.

> **§8's reconciliation still has to happen against that calendar** — "the
> booking exists" is the one thing a call cannot prove about itself. And every
> test event must be **deleted afterwards**, or a published slot stays blocked
> against a real caller.

---

## 8. Live call protocol

**Reconcile every call against the Google Calendar — not Acuity.** Open the
*"Vital Edge — Available"* calendar and confirm the published slot flipped to a
PENDING event with the right name, service, **duration** and time.

**Deploy proof:** `/health` returns a hardcoded `1.0.0` and is useless. The only
proof of what is running is `[build_info] running build <sha>` in the **Render
log** at call cleanup. Check it before trusting any test call.

### Must-cover cases

| # | Case | Watching for |
|---|---|---|
| 1 | Deep Tissue, caller says nothing about length | Susie **asks** 60 or 90 and states both prices **before** offering times (Item 2) |
| 2 | **Deep Tissue, caller asks for 90 minutes** | Susie does **not** refuse it (Item 1); event is **90 minutes long** on the calendar, not 60 |
| 3 | Deep Tissue, 60 minutes | quotes £125; event is 60 |
| 4 | Any successful booking — **listen hard to the closing** | §7.2: the pending message, and **never** "all booked" / "confirmed" / "a text is on its way" |
| 5 | Fixed-length service (Sports, 90) | length is **never** asked — only Deep Tissue has the choice |
| 6 | **Non-massage request (reiki / acupuncture)** | `never_autobook` — declined, not booked |
| 7 | **Caller states they are under 18** | `never_autobook` — declined |
| 8 | Caller changes day mid-flow | the C1 date guard and B-46 read-back, both brand new to VE (category 4) |
| 9 | **Book, then ring back and reschedule it** | `B-55` **fixed** — expect *"That's the new time sent over to Jonathan… It's not confirmed until he comes back to you."* Any *"you're rescheduled"* / *"you're now in for"* means the fix did not take |
| 10 | **Successful booking, then push back**: *"so I'm all booked in then?"* | §7.2 under pressure. Case 4 covers the volunteered claim; this covers the invited one, which is the likelier shape |

Scripts to work from: `docs/archive/JV_V1_8CALL_TEST_SUITE.md`,
`docs/archive/CALL_TEST_SCRIPT.md`.

---

## 9. Decision gate — honour this

**Cut over only if the write path and the promise are both clean.**

| Finding | Action |
|---|---|
| Category 1 bug | log it, proceed |
| Category 2 bug | **stop**, fix, re-test |
| Category 4 bug (inherited engine change misbehaving on VE) | **stop**, fix on `latency-eval`, re-inherit |
| §7.2 fires — any "all booked"/"confirmed" on a VE **booking** | **stop, do not cut over** |
| `B-55` — a VE **reschedule** narrated as confirmed | **stop** — fixed 04-08, so a recurrence means the fix did not deploy |
| A non-VE clinic's prompt hash moves | **stop** — the `is_provisional` scoping leaked; `test_b55_*` should have caught it |
| Case 2 refuses a 90-minute booking | **stop** — Item 1 did not take |
| Calendar event missing or wrong duration after a confirmed booking | **stop, full halt** |

If the gate fails: Jonathan gets a **live demo call on a test number** running
the converged build, and production cutover moves. Better conversation than "it's
live" followed by a rollback.

---

## 10. Cutover and rollback

`latency-eval` is **not** a live line — push freely. `vitaledge-onboarding`
**is**: it serves Jonathan's clinic. Out-of-hours timing, a revert commit in
hand, coordination — all of that applies **there**, not on the engine branch.

1. Push `latency-eval` (it is currently one commit ahead of origin — §1.1).
2. Confirm `git cherry latency-eval vitaledge-onboarding` shows **no `+` lines**
   except the deliberately deferred ones. As of 2026-08-04 that is **`d7593e2`
   (Item 5) and `ca8652e` (Item 3b)** — plus `e1394a6`, `087cecd` and `553d7e6`,
   which stay `+` because they were applied by **content, not cherry-pick**
   (`latency-eval` already held most of them; see Item 4). Anything else is an
   incomplete backport — **stop**.

   > ⚠️ **`git cherry` alone cannot gate this any more.** Of the six remaining
   > `+` commits, four (`e1394a6`, `f26cea8`, `087cecd`, `553d7e6`) are already
   > present **by content**, and `ca8652e` is **partially** applied — its
   > `alerts.py` hunk landed as Item 3a, its `stt_stream`/`connection` half is
   > deferred as 3b. Only `d7593e2` is wholly outstanding.
   >
   > Check the markers, not the commit list. All verified present 2026-08-04:
   >
   > ```bash
   > grep -rl "abandoned_call\|no_audio_call\|duration_choice_note" app/
   > grep -rl '_ved_slot.pop' app/ ; ls app/obs/show.py
   > ```
   >
   > And the one that must still be **absent**, or 3b landed by accident:
   >
   > ```bash
   > grep -rl "_chunk_is_voiced\|_on_stt_stall" app/    # expect: no matches
   > ```
3. Re-cut: `vitaledge-onboarding` → `latency-eval`. Because step 2 proves VE
   holds nothing unique, this is a fast-forward, not a merge.
4. Confirm `[build_info] running build <sha>` in the Render log.

**Rollback:** tag VE's current tip **before** anything
(`archive/vitaledge-pre-convergence` @ `23b8dbe`). Rolling back is pointing the
Render service back at that tag — a branch switch, not a code change.

> **Canonical-first rule, restated because this plan exists to repair a breach of
> it:** engine fixes land on `latency-eval` first; clinic branches inherit. VE
> carried `a1c2d70` and `475401e` alone for ten days, which is exactly how a
> safety fix gets stranded at convergence.

---

## 11. Open questions — resolve before or during, not after

| # | Question | Impact if wrong |
|---|---|---|
| ~~1~~ | ~~§7.2 — does the model emit a completion claim on a successful provisional booking?~~ **ANSWERED: 0/30, clean** | — |
| ~~1b~~ | ~~`B-55` — the mandated reschedule closing~~ **FIXED, prompt half** | — |
| 1d | **Close the Gate 5f provisional gap, or leave it prompt-only?** Both closings now measure clean, so this is no longer urgent — but VE remains the one clinic whose worst sentence has no guard behind the prompt | a false promise if the prompt ever drifts; touches every clinic's write path, so it needs its own measurement |
| 1e | **VE's cancel closing** — *"your appointment has been cancelled"* still trips the detector. Arguably true (deleting a pending request is a real deletion). Decide rather than reflex-fix | a wrong call either way is a caller who turns up, or one who doesn't |
| 1c | **Should obs be provisioned for the Vital Edge service?** Today it writes nowhere this repo can read, so no VE question is answerable by replay — now or after cutover | every future VE defect costs a live call to find; CLAUDE.md §6.4 *visibility* is unmet for this clinic |
| ~~2~~ | ~~Does `e1394a6`'s operator alerting conflict with `latency-eval`'s newer `alerts.py`?~~ **ANSWERED: no.** `latency-eval` already had all of `e1394a6`; the whole file delta was Item 3a's two conditions | — |
| 2b | **Item 3b is deferred, so a zombie STT session is still invisible on every clinic.** When it lands, re-implement by symbol against today's `stt_stream.py` — the 07-20 patch conflicts in both files | dead air that no alert can see; and a careless re-apply is dead air it *causes* |
| 3 | Do any of the 155 inherited `app/` commits assume Acuity semantics that do not hold for a provisional Google Calendar clinic? | category-4 bug on a live clinic |
| 4 | Is Item 5's turn-taking work still correct against a `connection.py` that has moved 2,788 lines? | dead air — which is why §3 defers it |
| 5 | JV is in the identical position (16 ahead / 294 behind, 5 unique). Does this plan generalise, or does JV need its own? | a third stranded clinic branch |

**Coverage note:** unlike Theorem — which had effectively none — Vital Edge
arrives with **two** dedicated regression tests, and they are the two that matter
(90-minute booking, duration ask). Both currently live only on VE. Getting them
onto `latency-eval` in Items 1–2 is what makes this convergence testable at all.

---

## 12. Verification commands

```bash
git worktree prune; git rev-parse --abbrev-ref HEAD; git status --porcelain
```

```bash
git cherry -v latency-eval vitaledge-onboarding
```

```bash
git diff --stat latency-eval vitaledge-onboarding -- app/
```

```bash
git diff latency-eval vitaledge-onboarding -- app/clinics/vital_edge/clinic.json
```

```bash
git grep -c "zombie" vitaledge-onboarding -- app/media_streams/stt_stream.py
```

```bash
git grep -n "provisional" latency-eval -- app/media_streams/turn_handler.py
```

---

## 13. Corrections this plan makes to existing docs

Per CLAUDE.md §7 — *"if these documents and the code disagree, the code wins;
record the correction."*

1. **CLAUDE.md §2** — the branch table implies the two onboarding branches track
   `latency-eval` by cherry-pick. In practice `vitaledge-onboarding` has been
   **266 commits behind since 24 July** and holds two engine fixes that never
   came up. The rule is stated correctly; it was not being followed.
2. **CLAUDE.md §2** — *"do not merge the `feat/obs-*` branches into this one"* is
   still right, but incomplete: obs work also arrived independently on
   `vitaledge-onboarding`, and `latency-eval`'s copy is now the **newer** of the
   two (§1.3). Judge obs by content date, not by branch name.
3. **`THEOREM_PORT_PLAN.md` §11 Q4** — asked whether `main`'s `3da3a17`
   (dead-air backstop firing late) and `07de912` (greeting watchdog 6s) have
   `latency-eval` equivalents. **They do.** `git cherry` marks VE's identically
   titled `23b8dbe` and `3efbe6b` as `-` (equivalent present on `latency-eval`).
   Answered as a by-product; verify by content before relying on it for Theorem.
4. **`THEOREM_PORT_PLAN.md` §8** — its Acuity reconciliation instructions are
   Theorem-specific. Vital Edge books to **Google Calendar, provisionally**.
   Do not reuse that section for this clinic.
5. **Stale line numbers, again.** `a1c2d70`'s hunks are at `3960`/`4000` in the
   commit and `4185`/`4324` on `latency-eval` today; the Theorem plan's Item 4
   anchor moved `2604` → `2771`. **Anchor by symbol, never by line.** Third
   recurrence — this belongs in the house style, not in individual plans.
