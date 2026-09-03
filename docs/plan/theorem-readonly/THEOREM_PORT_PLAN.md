# Theorem Port Plan — moving Mark's clinic onto the canonical engine

**Status:** ready to execute
**Written:** 2026-08-03
**Target:** one working day, with a hard decision gate before cutover
**Owner:** Quentin
**Client:** Mark Dyer — Theorem Health (Alcester / Redditch)

---

## 0. Read this first — the thirty-second version

Mark's clinic runs from the `main` branch. `main` has **zero** false-confirmation
protection, so it cannot go live as-is. `latency-eval` has that protection plus
two months of safety fixes, and already contains a working Theorem prompt — but
it is missing ~6 weeks of Mark's tuning and, critically, the Redditch
not-bookable guard.

**The move:** copy Theorem's clinic data and prompt delta onto `latency-eval`,
cut `theorem-onboarding`, repoint Mark's Render service, archive `main`.

**The one thing that can go silently wrong:** `latency-eval`'s booking write
gates match **literal strings** tuned to a different clinic's prompt wording.
Theorem's prompt uses different literals. A mismatch means Susie announces a
booking that never reached Acuity — and the call sounds perfect. This is why
§7 (the literal audit) is the load-bearing step, not the phone calls.

---

## 0.5 Re-verified 2026-08-04 — read this before using any line number below

Every claim in this plan was re-checked against the tree at **`a373f31`**. What
follows is what changed. Anything not listed here still holds.

### Base commit

Port from **`a373f31`** or later. Thirteen engine commits landed on
`latency-eval` on the night of 3–4 Aug; several matter to this port and are
listed under §3a and §7 below.

### Numbers that moved

| Claim in this plan | Actual at `a373f31` |
|---|---|
| `main` 155 ahead / **384** behind | 155 ahead / **428** behind |
| `jv-v1-onboarding` 16 / **265** | 16 / **309** |
| `vitaledge-onboarding` 18 / **237** | 18 / **281** |
| Item 4 anchor `latency-eval:2604` | **`2892`** — `llm_stream.py` was edited three times on 3 Aug |
| £75 in `clinic_config.py` at **442, 451** | **428, 429** |
| Suite baseline 95 failures | **95 failed / 3529 passed** |

`main`'s anchor (`1756`) is unchanged, as is `THEOREM_LOCATIONS`
(`clinic_config.py:1064`, redditch block `1072`) and the file sizes
(latency-eval 3,759 / main 4,006).

### Item 1 is DONE

`f12c36a` — `canonical.py` and `caller_concerns.py` are both present on
`latency-eval`. Items 2, 3 and 4 remain.

### Two findings that are new since this plan was written

Both are in §3a and §7. Neither was known when the port list was drawn up, and
**one of them blocks cutover under §9.**

### Things that got cheaper

- **`B-50`** (wrong service booked) **cannot reach Theorem** — a hard whitelist
  at [receptionist_tools.py:3877-3880](../../app/tools/receptionist_tools.py)
  rejects any service not in `_VALID_SERVICES` for
  `theorem`/`theorem_v2`/`theorem_v3`. Measured separately: zero informal
  service strings in 155 obs calls. Do not build the fuzzy resolver.
- **Item 4b is now a two-way reconcile, not three-way.** `B-46` (`80b545b`)
  already merged main's `phone_confirmed` gate with `latency-eval`'s
  `_caller_requests_new_day_or_time` escape and the BUG-14 injection, into
  `_post_collect_readback_due`. Main's contribution to that guard is already in.

### One live defect that reaches Theorem and is NOT fixed

**`B-54`** — a real calendar event was cancelled that the caller did not mean
(`CA156fa25`, 3 Aug). The steering half shipped (`c273475`); **the gate half is
open**. `_note_lookup_name_spoken` satisfies the identity gate the moment a name
reaches TTS, without the caller agreeing and without reference to which
appointment. It is not screening-scoped or clinic-scoped — **Theorem inherits
it**. See `REGISTER_B_U.md`. Consider telling Mark the cancel path is new, or
routing first-week cancellations to the team.

---

## 1. Why this direction, and not the other

The instinct was to merge `latency-eval` into `main`. Don't.

### 1.1 What `main` actually is

`main` is **not** dead history. CLAUDE.md §2 calls it "a separate historical
lineage — leave it alone." **That is wrong**, and it has been steering sessions
away from a paying client's deployment branch.

| Evidence | Location |
|---|---|
| `CLINIC_NAME=Theorem Health` | `main:.env.example` |
| `"owner_name": "Mark Dyer"` | `main:app/clinics/theorem/canonical.py:57` |

`main` is the **fourth deployment branch**.

### 1.2 Branch divergence (measured 2026-08-03 — CLAUDE.md's 189/142 is stale)

| Branch | Ahead of `latency-eval` | Behind |
|---|---|---|
| `main` | 155 | 384 |
| `jv-v1-onboarding` | 16 | 265 |
| `vitaledge-onboarding` | 18 | 237 |

Of `main`'s 155, **~128 are truly unique** (`git cherry latency-eval main`); the
rest are cherry-pick equivalents with different SHAs. Of the 128: 74 touch
engine files, 14 clinic files, 33 docs/tests.

`main` last moved 24 Jul. Theorem clinic work stopped 12 Jul.

### 1.3 The decisive asymmetry

**`main` has no write-gate machinery at all:**

| | `main` | `latency-eval` |
|---|---|---|
| `booking_write_confirmed` | **0 refs** | 9 |
| Gate 5e / 5f | **0 refs** | 21 |
| false-confirmation guard | **absent** | `app/call_logger.py`, `app/media_streams/turn_handler.py` |
| `clinical_screening.py` | absent | 1,071 lines |
| `latency_timing.py` | absent | present |
| test files | 98 | 177 |

`main` has nothing guarding the failure CLAUDE.md §4 names as the worst in the
system: *the call sounds perfect and the booking silently never happened.*

**Merging into `main` would mean** hand-resolving `connection.py` +4,653,
`llm_stream.py` +5,878, `receptionist_tools.py` +2,278 — the three danger-zone
files — against 74 divergent engine commits, with 98 test files as the net.

**Porting out of `main`** is bounded and touches none of `connection.py` or
`flow.py`. That is the whole argument.

### 1.4 Clinical screening — settled, no action

Mark does not want clinical screening; he wants fastest-possible booking. **This
requires zero work.** `screening_enabled()`
(`app/media_streams/clinical_screening.py:198`) requires
`clinic["clinical_screening"]["enabled"]` — it is **opt-in per clinic**, and
Theorem has no such block. `vital_edge` already runs this way: live precedent.

> **Do NOT add a `clinical_screening` block to Theorem.**

Get Mark's decision in writing regardless — Marcus's equivalent sign-off is
recorded in-config at `app/clinics/jv_v1/clinic.json:130`, and the Hands On Money
cohort will ask why one clinic screens and another does not.

---

## 2. How Theorem actually works — read before editing

This is counter-intuitive and cost an hour to establish.

**Theorem is not driven by `app/clinics/theorem/`.** It runs as
`clinic_id == "theorem_v3"`, matched as a **literal string**:

- `app/prompts/susie_system_prompt.py:127` → `_build_theorem_v3(session)`
- `get_clinic("theorem_v3")` has no clinic.json and **falls back to `demo`**
  (`app/clinic_config.py:1378`, documented at `:1388`)

There is **no `prompt_engine` key and none is needed.**

`is_freeform_clinic()` (`app/clinic_config.py:1383`) returns True for
`theorem_v3` **and** every `template_v1` clinic — both run the same free-form LLM
loop with no FlowEngine.

> **Consequence:** Theorem's runtime architecture already matches what
> `latency-eval` runs. No conversion needed. `latency-eval` already carries a
> complete, working 2,008-line `_build_theorem_v3`. **Theorem would boot on
> `latency-eval` today** — it would just be the pre-12-Jul prompt.

### 2.1 Do NOT convert Theorem to `template_v1`

It is the right long-term shape and the cohort-scale path, but it means
rewriting 2,246 lines of Mark's tuned prompt into clinic.json. That is not a
one-day job and it puts his call quality at risk for a benefit he is not paying
for. **Defer to `PRODUCTION_READINESS_PLAN.md` Phase 4 (multi-tenancy).**

### 2.2 The wiring that makes this a merge, not a copy

The Theorem clinic modules are pulled in by **module-level imports into two
diverged engine files**:

| Import | Site on `main` | Consumed at |
|---|---|---|
| `from app.clinics.theorem import canonical as theorem_canonical` | `clinic_config.py:11` | `:1242` (`get_price`) |
| `from app.clinics.theorem.caller_concerns import build_concern_handling_block` | `susie_system_prompt.py:15` | prompt build |
| `build_condition_injection` (lazy) | `susie_system_prompt.py:3998` | per-turn injection |

**Neither import exists on `latency-eval`.** Copying the directory alone gets you
two dead files.

---

## 3. The port list

Work in this order. Each item is independently committable.

### Item 1 — clinic data files · ~1,738 lines · risk: none

```bash
git checkout main -- app/clinics/theorem/canonical.py app/clinics/theorem/caller_concerns.py
```

Both files are **absent** from `latency-eval` — no conflict surface.
Also review `app/clinics/theorem/clinic.json` (+26) and `knowledge.md` (+17).

### Item 2 — `app/clinic_config.py` · 3 edits · risk: low

1. Add `from app.clinics.theorem import canonical as theorem_canonical`
   (mirrors `main:11`).
2. Add the `get_price` call site (mirrors `main:1242`) so prices live in one
   place.
3. **Add `"bookable": False` to the `redditch` entry** in `THEOREM_LOCATIONS`
   (`latency-eval:app/clinic_config.py:1064`, redditch block begins ~`:1072`).
   This is the single toggle Item 4's guard reads.

### Item 3 — `app/prompts/susie_system_prompt.py` · ~400 lines · risk: low–medium

`_build_theorem_v3` diffs **+319 / −81** (main 2,246 lines vs latency-eval 2,008).
All of main's additions are **content, not engine**:

- Redditch redirect block (reads `THEOREM_LOCATIONS['redditch']['bookable']`)
- `PERSONA CHARACTER` block
- `LANGUAGE — SIGNAL EXPERTISE AND CARE` block
- condition-knowledge injection

Plus 2 imports (§2.2), plus these shared-prompt rules that `main` has and
`latency-eval` lacks:

- the **PACING** rule (full stop between times for a slow, clear readout)
- "never improvise a phone question" prohibition
- pricing-objection brevity (max three sentences)
- "don't end every answer with *is there anything else*"

#### 🔴 3a. PRICE IS STALE — £75 vs £85 · **RESOLVED 2026-08-04, and it is a CONFIRMED defect**

This was carried as "unverified — establish which sites Theorem reaches." It has
now been traced end to end, and the answer is worse than the row assumed.

**The prices are inside `_build_theorem_v3` itself**, which is the one place the
plan assumed they were *not*:

| | `latency-eval` | `main` | `canonical.py` |
|---|---|---|---|
| PRICING QUESTIONS line | **£75** new patient | £85 | — |
| PRICES block, new patient assessment | **£75** / 50 min | £85 / 50 min | **£85.00** (`:182`) |
| PRICES block, follow-up | **£75** / 40 min | £85 / 40 min | **£85.00** (`:196`) |

**Ported as-is, Susie quotes £75 for an £85 appointment on every pricing
question**, on a paying client's line. Not a risk — a certainty.

**And the divergence is wider than the price.** `main`'s PRICES block carries
entries `latency-eval`'s does not have at all: standalone shockwave / Class IV
laser (£130), the shockwave-or-laser surcharge (£45), the four-session package
(£468, six-month validity), acupuncture (£85), psychotherapy (£85), and the
wellness massage with in-light therapy (£85). Port the **whole block**, not the
two numbers.

**The two generic-path sites are NOT reachable and need no action.** Traced:
the live path is [llm_stream.py:1542](../../app/media_streams/llm_stream.py) →
`build_system_prompt_parts()`, which branches to `_build_theorem_v3` at
`susie_system_prompt.py:127` before anything else; `build_system_prompt()`
branches at `:152`, ahead of site `470`. Site `1735` lives in
`get_system_prompt()`, which is called only from `app/flows/conversation.py:219`
and `app/routes/realtime.py:560` — the legacy pipelines, not `media_streams`.

**`clinic_config.py:428-429` DOES sit inside the `"theorem"` block** (nearest
preceding top-level key is `"theorem": {` at `:154`) and also says £75. Whether
`theorem_v3` reaches it is doubtful — `get_clinic("theorem_v3")` falls back to
`demo` — but Item 2 wires pricing to `canonical.get_price` anyway, so fix it
there rather than leaving two contradictory numbers in Theorem's own config.

#### ⚠️ 3b. Do NOT port the "Lovely" relaxation blindly

`latency-eval` bans the word outright — *"it sounds patronising and triggers
name-echo bugs."* `main` relaxes it to allow `"Lovely — "` as an opener before a
dash. **Which is newer is unestablished.** Porting main's version backwards could
reintroduce a bug `latency-eval` deliberately guards against. Leave
`latency-eval`'s ban in place unless you can date main's change as later.

#### 3c. Do NOT port main's PHONE HAND-OFF duplicate

`main` duplicated the phone hand-off contract into the shared
`SLOT_FORMATTER_SYSTEM_PROMPT`. `latency-eval` already has it — 9 files, 7× in
`susie_system_prompt.py`, **4 inside `_build_theorem_v3`** (lines 1945, 3192,
3199, 3213). Theorem's hand-off is intact. Porting the duplicate risks two
divergent copies of a literal-matched contract.

#### 3d. Things `latency-eval` has that `main` lacks — Theorem GAINS these

- **REQUESTED DAY FULL** — `requested_day_empty` handling ("Tuesday 4th August is
  fully booked, I'm afraid —" + alternatives). `main` has no such block.
- **`presented_days` cap** — speaks a capped subset on multi-day; `main` reads the
  full `available_days` list aloud.

### Item 4 — `app/media_streams/llm_stream.py` · ~60 lines, 2 sites · risk: medium

Originally scoped HIGH. Downgraded after reading it: the 3,592-line divergence is
nowhere near the insert point.

#### 4a. The guard itself — easy

`_location_not_bookable()` at `main:app/media_streams/llm_stream.py:295-323` is
~30 self-contained lines. Dependencies: `tool_name`, `args`, `session`, and a
lazy `THEOREM_LOCATIONS` import inside a try/except.

> **It hard-gates on `session.get("clinic_id") != "theorem_v3"` — it is a NO-OP
> for `jv_v1` and `vital_edge`. Porting it cannot regress the live clinic
> branches.**

**Insert point matches structurally:**

| Branch | Anchor |
|---|---|
| `main` | `1756` — `_col = session.get("collected") or {}` |
| `latency-eval` | `2604` — identical line |

Port = add a leading `if _location_not_bookable(...)` branch, demote the existing
`if` to `elif`. Same shape as `main:1757`. `transfer_to_human` exists on
`latency-eval` (`receptionist_tools.py`, 6 refs), so the redirect message
resolves.

#### 4b. ⚠️ The sibling guard has THREE fixes across two branches — a merge

| Fix | Branch | Detail |
|---|---|---|
| gate on `session["phone_confirmed"]` | `main` only | see §4 / the open P1 below |
| `_caller_requests_new_day_or_time` escape | `latency-eval` only | from a live call: caller asked for Wednesday, was re-read Tuesday, hung up unbooked |
| BUG-14 name/location injection | `latency-eval` only | readback dropped the surname and the whole slot |

**All three must survive.** This is the single most likely place in the whole
port to create a novel bug.

#### 4c. ⚠️ The companion fix that actually lost a booking

`main:1923-1933` syncs `session["selected_location"]` to the location just
checked (`_av_loc`). **`latency-eval` has ZERO trace of it.**

main's comment records the live failure verbatim: caller switched
Redditch→Awlstuh, was shown and agreed Alcester slots, but `book_appointment`
fired with `location='redditch'` → blocked → *"caller bounced after a full
booking."*

> **The Redditch guard without this sync reproduces exactly that failure. They
> must land in the same commit.**

Probably Theorem-only (`jv_v1` and `vital_edge` are single-site and auto-confirm
via `single_location_template()`) — **unverified**.

### Item 0 — ~~the open P1 that must land FIRST~~ · **DONE `80b545b`, 3 Aug**

> **Closed before the port started.** Now tracked as **`B-46`** in
> `REGISTER_B_U.md` — this was the last defect living only in this untracked
> file, which is how it stayed open while both live clinics ran it.
>
> The line reference below (`llm_stream.py:2604-2612`) was **stale** — it points
> at the Gate 5 fallback. The guard was found by symbol. The fix is main's gate
> with both `latency-eval`-only protections kept, extracted to
> `_post_collect_readback_due` so it is testable without a connection.
> 36 regression tests; failing set unchanged.
>
> Original text follows.

#### Item 0, as written

`latency-eval:app/media_streams/llm_stream.py:2604-2612` gates the readback on
`_col.get("phone")`. **Caller-ID pre-fills `collected["phone"]` at connect, so it
is always present** — under name-first the guard fires before any slot is
offered, forcing a premature readback that skips surname and phone confirmation.

`main` already fixed this (`main:1748-1755` comment, `main:1786-1793` gate, which
uses `session.get("phone_confirmed")`).

**This is a live defect on `jv_v1` and `vital_edge` today**, independent of
Theorem. And Item 4 has to merge against this exact `if` statement — so fixing it
first means the Theorem work lands on settled code instead of racing it.

Ships with a regression test in `tests/regression/`.

---

## 4. Bug taxonomy — what "no new bugs" can and cannot mean

The working assumption *"any bug we find tonight is a `latency-eval` bug, not a
port bug"* is **true for two of three categories.** Know which is which before
triaging anything at 9pm.

### Category 1 — shared-engine bugs · assumption HOLDS ✅

Endpointer, STT, TTS, slot logic, booking providers. Theorem hits the same code
as `jv_v1` and `vital_edge`, which exercise it live daily. Pre-existing, on the
backlog either way. Finding one tonight is a gift, not a regression. **Log it,
do not block cutover on it.**

### Category 2 — port-introduced bugs · assumption FAILS ❌

The Item 4b three-way reconcile above all. These are genuinely new. **Block
cutover.**

### Category 3 — Theorem's prompt × `latency-eval`'s write gates · assumption INVERTS 🔴

**This is the real risk and it is not hypothetical — it is B-36 by definition:**
*a reworded CTA blocks the write and Susie announces success anyway.*

The gates match **literals**. Every `latency-eval` write gate was written and
tuned against `clinic_template_prompt` wording. Theorem's 2,246-line prompt is a
different set of literals from a different lineage and has **never** been run
past those gates.

Two things make it worse:

1. **Silent by construction.** The call sounds perfect. You cannot hear it.
2. **Gate 5f has never fired live** — not even on the clinics it *was* designed
   for. You would be pointing an unproven gate at unfamiliar wording.

> A category-3 bug passes a listening test 100% of the time. **If tonight's
> testing is judged by ear, it will return a false clean.**

---

## 5. The day plan

| Slot | Work | Exit condition |
|---|---|---|
| **Morning** | Item 0 (readback guard + regression test), then Items 1–3 | code committed, suite diffed against baseline |
| **Midday** | Item 4 — guard **and** location sync in one commit | Redditch unbookable, clinic-switch sync verified |
| **Afternoon** | **§7 literal audit — the load-bearing step** | every Theorem CTA matched to a gate pattern |
| **Evening** | live calls, §8 protocol | every booking reconciled in Acuity |
| **Gate** | §9 decision | cutover, or demo-call fallback |

**You will know by ~18:00 whether the evening is verification or debugging.** That
is the whole point of putting the audit before the phone.

---

## 6. Test baseline — do this before you change a line

The suite is **meant to be red**: ~95 failures since 26 Jul.

```bash
python -m pytest -q 2>&1 | tail -20
```

> **Verify by DIFFING the failing set before and after — never by looking for
> green.** Capture the baseline list to a file first.

⚠️ **`git stash` does not work in this tree.** It saves but does not revert
(OneDrive file locks). Back changes out by hand or your baseline is a lie.

⚠️ **Live-booking hazard.** `tests/auto` once booked 60 real Acuity appointments
via plain pytest. The opt-in gate is in place on all 5 branches — do not
re-enable it casually.

---

## 7. The literal audit — the step that makes a one-day port safe

> ## ✅ RUN 2026-08-04 — results below. One category-3 gap found.
>
> Every quoted utterance in the **built** prompt (84,695 chars, 514 quoted
> strings) was extracted and run through the real `_false_write_claim` for all
> three write families.
>
> | Family | Theorem's taught closing | Gate 5f |
> |---|---|---|
> | **Booking** | `All booked — you're in for…` | ✅ **CAUGHT** |
> | **Reschedule** | `I've rescheduled to [date/time]…` | ❌ **MISSED** |
> | **Cancel** | `That's all done — your appointment has been cancelled.` | ✅ caught |
>
> **The booking path is clean** — the highest-traffic path, and Mark's priority.
>
> ### 🔴 The reschedule gap — CATEGORY 3, blocks cutover per §9
>
> `_FALSE_RESCHEDULE_CLAIM_RE` **requires an object after the verb** — `moved
> you/that/it to…`, never a bare `moved to…`. That is deliberate: it is what
> keeps *"we've moved to a new building"* from being stripped as a false
> confirmation.
>
> `clinic_template_prompt.py` was shaped to fit that gate. It mandates *"That's
> you rescheduled — you're now in for…"* (**caught**) and at
> [clinic_template_prompt.py:2321](../../app/prompts/clinic_template_prompt.py)
> explicitly warns against *"A bare 'I've rescheduled to [date]'"*.
>
> **Theorem's prompt teaches exactly the form the template forbids.** A refused
> reschedule, narrated in Theorem's own mandated wording, passes the guard
> silently.
>
> **Fix = a prompt edit, folded into Item 3:** bring Theorem's reschedule
> closing onto the template's wording. **Do NOT widen the regex** — that
> re-opens the false positive the object requirement exists to prevent.
>
> ### Not a Theorem problem — a shared one, for the record
>
> Both prompts end the cancel closing with *"Is there anything else I can help
> with?"*, and `_false_write_claim` stands down on any `?`. In the live pipeline
> that sentence never reaches Gate 5f — Gate 5b strips it first as a banned
> phrase (`is_there_anything_else`), confirmed on `CA156fa25`. So it is not the
> exposure it first appeared to be. See the correction in `REGISTER_B_U.md`.
>
> ### ⚠️ A carry-over the port list does not have
>
> **The `A3` surname read-back does not reach Theorem.** `914cda3` fixed it in
> `clinic_template_prompt.py`; `theorem_v3` runs `susie_system_prompt.py`. The
> A3 commit says so in its own scope note: *"Does not reach theorem/demo … The
> Theorem port must carry this by hand."*
>
> Ported as-is, Mark's clinic launches with the defect `jv_v1` fixed on 3 Aug:
> the surname written to a real calendar, never spoken aloud, no chance for the
> caller to correct it — three real events already carry a wrong surname. **Add
> to Item 3.**

**Do this at a desk, before touching a phone.** It catches the entire category-3
class in about an hour.

**Goal:** every confirmation / CTA literal Theorem's prompt can emit must be
matched by the write-gate patterns in `latency-eval`.

1. Extract every confirmation and CTA string from `_build_theorem_v3` — anything
   Susie says at or near a booking write.
2. Extract the literal patterns the write gates match. Gate 5 entry point:
   `app/media_streams/turn_handler.py:874` — `sanitise_response()`.
3. Diff the two sets. **Any Theorem literal with no matching gate pattern is a
   B-36 waiting to happen.**
4. Pay special attention to the phone hand-off contract — `"use this number"` and
   `"keypad"` are parsed downstream; dropping either means the caller's number is
   never captured and the booking is lost.

**Tooling:** `scripts/audit_gate5_blast_radius.py` (320 lines, on `latency-eval`)
measures Gate 5 blast radius.

> ⚠️ **Read its docstring before trusting it.** It replays against *recorded*
> calls — **Theorem has none on this engine**, so it cannot give Theorem a clean
> bill. Use the pattern/firings side, not a replay result. Its own header
> documents it reporting *"5 changed turns of 740, 0 emptied — clean"* on the
> same day Gate 5 was rewriting callers' booking days, and having done so for
> three weeks. **Read the FIRINGS table as carefully as the diffs.**

---

## 8. Live call protocol

**Reconcile every single call against Acuity.** Not "did it sound right" — open
Acuity and confirm the row exists with the right name, clinic, day and time.

Scripts to work from:
- `docs/archive/JV_V1_8CALL_TEST_SUITE.md`
- `docs/archive/CALL_TEST_SCRIPT.md`
- `docs/archive/JV_V1_TEST_CALL_SCRIPT.md`

### Must-cover cases

| # | Case | Watching for |
|---|---|---|
| 1 | Straight booking, Alcester | write lands in Acuity |
| 2 | **Redditch requested** | redirect fires, **nothing reaches Acuity** |
| 3 | **Redditch → Awlstuh switch** | §4c — booking lands on **Alcester**, not redditch |
| 4 | Caller changes their mind on day/time | Item 4b escape survives; no premature readback |
| 5 | Returning patient | `lookup_patient` before re-collecting |
| 6 | Pricing question | quotes **£85**, not £75 (§3a) |
| 7 | Phone hand-off, caller-ID present | `"use this number"` path captures the number |
| 8 | Phone hand-off, no caller-ID | keypad path captures the number |

**Deploy proof:** `/health` returns a hardcoded `1.0.0` and is useless. The only
proof of what is running is `[build_info] running build <sha>` in the **Render
log** at call cleanup. Check it before trusting any test call.

---

## 9. Decision gate — honour this

**Cut over only if the write path is clean.**

| Finding | Action |
|---|---|
| Category 1 bug | log it, proceed |
| Category 2 bug | **stop**, fix, re-test |
| Category 3 bug (any CTA/gate mismatch) | **stop, do not cut over** |
| Acuity row missing after any confirmed booking | **stop, full halt** |

If the gate fails: Mark gets a **live demo call on a test number** running the
ported build — he phones it, books, sees it land in Acuity — and the production
cutover moves to Thursday. That is a materially better conversation than "it's
live" followed by a rollback.

---

## 10. Cutover and rollback

`latency-eval` is **not** a live line — push freely. The gated branches are the
deployment branches, and `theorem-onboarding` becomes one the moment Mark's
service points at it.

1. Cut `theorem-onboarding` from `latency-eval`.
2. Repoint Mark's Render service — branch is set **per-service in the Render
   dashboard**, not in `render.yaml` (which has `autoDeploy: true` and no branch
   pin).
3. Confirm `[build_info] running build <sha>` in the Render log.
4. Archive `main` as `archive/theorem-pre-consolidation`.

**Rollback:** have the revert commit written *before* cutover. Rolling back = point
the Render service back at `main`. `main` still works — it is simply the older
engine — so rollback is a branch switch, not a code change. Do the cutover
out-of-hours.

> **Canonical-first rule, unchanged:** engine fixes land on `latency-eval` first;
> clinic branches inherit by cherry-pick. Never fix on a clinic branch and port
> up — that strands safety fixes at convergence.

---

## 11. Open questions — resolve before or during, not after

> **Updated 2026-08-04.** Question 1 is **CLOSED** — and it turned out to be a
> confirmed defect rather than an open risk, plus a whole diverged PRICES block.
> Question 5 is **CLOSED**: the stale-`selected_location` bug cannot bite `jv_v1`
> or `vital_edge`, because a lookup that could clobber the confirmed number was
> hardened on 3 Aug (`2a146dd`) and the path is Theorem-only by construction
> (`receptionist_tools.py:6148` returns early for every other clinic).
> Questions 2, 3 and 4 remain open.
>
> **Two questions to add:**
>
> | # | Question | Impact if wrong |
> |---|---|---|
> | 6 | Does `_build_theorem_v3` need the `A3` surname read-back carried by hand? (§7) | **Yes — confirmed.** Mark launches with a wrong surname on real calendar events |
> | 7 | Does Theorem's reschedule closing pass Gate 5f? (§7) | **No — confirmed.** A refused reschedule is narrated as done, silently |

| # | Question | Impact if wrong |
|---|---|---|
| 1 | ~~Which of the four £75 sites does Theorem actually reach?~~ **CLOSED — see §3a** | £10 undercharge on every pricing question |
| 2 | Is `latency-eval`'s "Lovely" ban newer than main's relaxation? (§3b) | reintroduces a name-echo bug |
| 3 | Do main's 74 unique engine commits have `latency-eval` equivalents? | unknown regressions; titles suggest yes but that is a **lead, not a finding** |
| 4 | Specifically: `3da3a17` (dead-air backstop firing up to 10s late) and `07de912` (greeting watchdog 6s vs 4.5s) | dead air on live calls |
| 5 | Can the stale-`selected_location` bug (§4c) bite `jv_v1`/`vital_edge`? | live defect on two more clinics |

**Theorem has effectively no test coverage** — one file
(`tests/test_theorem_canonical.py`) on `main`, zero on `latency-eval`. This port
cannot be regression-tested. That is why §7 and §8 carry the weight.

---

## 12. Verification commands

```bash
git worktree prune; git rev-parse --abbrev-ref HEAD
```

```bash
git cherry latency-eval main | grep -c '^+'
```

```bash
git diff --stat main latency-eval -- app/clinics/theorem app/clinic_config.py app/prompts/susie_system_prompt.py
```

```bash
git grep -c "booking_write_confirmed" latency-eval -- app/
```

```bash
git grep -n -i "redditch" latency-eval -- app/media_streams/llm_stream.py
```

---

## 13. Corrections this plan makes to existing docs

Per CLAUDE.md §7 — *"if these documents and the code disagree, the code wins;
record the correction."*

1. **CLAUDE.md §2** — "`main` … a separate historical lineage — leave it alone"
   is **wrong**. `main` is Mark's live deployment branch.
2. **CLAUDE.md §2** — "189 commits ahead of `main` and 142 behind" is **stale**.
   Measured 2026-08-03: 384 ahead, 155 behind.
3. **Method note** — a `git diff --stat` number is **churn** (insertions +
   deletions), not a size delta. Reading `susie_system_prompt.py`'s "497" as
   "497 lines deleted" produced a false alarm that cost a round of investigation.
   Check actual file line counts before concluding anything was removed.
