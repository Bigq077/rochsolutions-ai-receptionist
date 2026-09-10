# Finishing slot presentation — plan of record, rev. 2 (2026-09-10, PM)

**Goal (owner, 10 Sep):** slot presentation is *finished* by the end of this week.
**Time available:** Friday 11th, weekend buffer 12–13th.
**Author's note:** rev. 2 replaces the morning's ordering. Read §1 and §7 before
touching anything. The re-order is not a change of mind — it is what measurement
did to rev. 1's central assumption. §4.0 says why.

---

## 0. The bar — because "perfect" is not a thing you can prove

Unchanged from rev. 1, and still the exit criteria:

1. **No known defect a caller can hear.** Every item in §2 is closed or has an
   owner decision recorded against it.
2. **The defect class cannot recur silently.** Every producer of a slot sentence
   goes through one owner, and a test fails if a new one does not.
3. **It is measured, not asserted.** Three replay harnesses run over the stored
   corpus and report zero on their gates.
4. **A defined call script sounds right**, twice, on two different diaries.

**What this explicitly does not promise:** that no new slot defect will ever be
found. The change this week is that the next one is found by a harness rather
than by a patient.

---

## 1. Where we are

```
production     337fbd9e   T1 + T1b, deployed 10 Sep 09:4x, all three clinics
latency-eval   20c13378   = 337fbd9e + docs + 40a67ea8 (S-1a). NOT pushed yet.
revert target  fc47e508   the pre-T1 engine. Keep this to hand.
```

> **The local `latency-eval` branch ref is STALE** — measured 10 Sep at
> `0421b72d` against origin's `7654561c`. A parallel session holds it in its own
> worktree. Fetch before you measure anything, and never trust the local ref.

### Landed and call-verified this morning

| | site | anchor | state |
|---|---|---|---|
| T1 | cross-turn: a fresh day read at another day's clock times | `slot_followup.py:3737` `_prefer_unheard_clock_times` | fixed, replay-verified |
| T1b-1 | a multi-day readout opened every day at the same time | `receptionist_tools.py:5790` `_cap_presented_slots` loop | fixed, **call-verified** 09:31 |
| T1b-2 | a named day re-read at the times just heard for it | `slot_followup.py:5460` `speak_one_day_from_payload` | fixed, **call-verified** 09:35 |

Corpus effect, multi-day readouts: repeats 58/59 → 4/59, identical openers
16 → 0, northgate 50 → 0. The four residuals are theorem_v3, whose rota holds too
few distinct times to vary — the documented stand-down, not a defect. Re-measured
this afternoon on a corpus grown to 61 readouts: still 4 repeats, still 0
identical openers, northgate still 0.

### Landed this afternoon, not yet pushed

**`40a67ea8` — S-1 lever (a): the month is said once.** Days two and three of a
multi-day readout now say *"Tuesday the 15th"*. **18.59 s → 17.93 s.**

It carries two things that were not in rev. 1 and are worth more than the 0.66 s:

* a **month-end guard**. "Tuesday the 1st" spoken after "Monday 30th September"
  is heard as September, so a readout that crosses a month keeps the month.
  Decided on the ISO date, never on the prose.
* a **real regression it caused, fixed at the root**. B-111's dedupe matched the
  payload label against the RENDERED SENTENCE, so the moment a producer worded a
  date differently the dedupe went silently inert and Susie offered a date one
  sentence after reading it out. `append_other_dates_offer` now also takes what
  the offer NAMED. See §7.

`20c13378` records the measurement correction in this document's own history.

---

## 2. The register — every open item, with anchors

A row without `file:line` is a lead, not a finding. All of these have one.

### 2.1 Caller-audible

| id | what a caller experiences | anchor | evidence |
|---|---|---|---|
| **S-3** | The filler's first rung fires at **~3.12 s**, just past the published 3-second dead-air bar, so **every slow turn breaches it structurally**. | filler ladder; the constant is not yet located | `[LAT] ttfa_ms` 3118 / 3120 / 3128 on three consecutive turns, CA8214b75c |
| **S-2** | Once the caller names a day, the cross-day preference stops firing for the rest of the call — "twenty to ten" said for Monday and Tuesday **17 s apart**. | `slot_followup.py:3737`, the `today in heard days` stand-down | CA8214b75c, 10 Sep 09:35:46 → 09:36:03, reproduced offline |
| **S-1** | A three-day readout takes **17.93 s** (was 18.59 s). Callers barge in on nearly every turn. | `slot_offer.py:50-52` caps; `build_slot_offer` `:359` | measured — see §4.4. Now a DECISION, not engineering. |
| **S-4** | `last_bot_prompt` blows its 200-char cap and loses its "?" 3–4 times per call, disarming clinical screening's orphan matcher. | `clinical_screening.py:530` `_LAST_BOT_PROMPT_CAP` | both calls today. Improves for free as the readout shortens. **Do not touch the cap** — 34 writers, marked RED. |

### 2.2 Structural — no caller sees these today, they are how the next one gets in

| id | finding | anchor |
|---|---|---|
| **S-5** | **Five producers call `build_slot_offer`; four honour the selection rule.** Nothing enforces it. | `llm_stream.py:7097`, `:7175`; `slot_followup.py:5212`, `:5295`, `:5535` |
| **S-6** | Stage C's gate watches **one of two** reverse-parse sites. `_record_stood_down_slots` returns silently when it resolves nothing — "named no slots" is indistinguishable from "could not parse". | site A `llm_stream.py:4021` (logs); site B `llm_stream.py:3717` (silent) |
| **S-7** | **13 % of recorded offers were never spoken.** `record_offer` fires where the offer is BUILT, above the P6/P6b stand-downs. **No code needed** — it is a fact every future harness author must know. | `llm_stream.py:7353` |
| **S-8** | `presented_days` is populated on all 53 `multi_day` offers and **empty on all 24 `single_day`** ones, so B-95's presented-vs-bookable split is invisible in the mode a caller reaches by naming a day. | `record_offer` call site, `llm_stream.py:7353` |
| **S-9** | `calls.latency` carries **no tool-result marker**, so the tool-vs-plain latency split cannot be measured at all. One field on `TurnTiming`. | `latency_timing.py` |
| **S-10** | **NEW.** `operational.speak_part_of_day` is **half-wired**. It changes the deterministic labels, but the *rendered* northgate prompt instructs the model to speak the band in **three separate places** — so flipping it makes the READOUT bare while confirmations and read-backs stay banded. | rendered prompt (105 k chars); `SLOT_FORMATTER_SYSTEM_PROMPT` line 36 also still carries a band-form reference table |

### 2.3 Adjacent — real, not slot presentation

| id | finding | anchor |
|---|---|---|
| B-146 | A booking request that never says "book" gets *"Sorry, still with you —"* **on the caller's first sentence**. `classify_intent` cannot see `v3_treatment_mentioned`, which the engine set one line earlier. | `hold_speech.py:619` |
| — | STT drops numerals: `'the 30-minute session'` → `'the 5-minute session'`. Keyterm list carries no numbers. | 6 Sep register, secondary |
| — | `GOOGLE_SERVICE_ACCOUNT_JSON` invalid → Sheets skipped; ElevenLabs 401 on `/v1/models`. **Known-accepted on the demo line**, not on the live lines. | log, every call |

> **B-146 deserves an explicit decision rather than the scope rule making it by
> default.** It is not slot presentation and it does not belong in this week's
> diff. It is also a first-sentence defect on exactly the call being rehearsed
> for a partner. Decide it; do not simply inherit the exclusion.

---

## 3. The three lineage documents — what is left

All three are live. None supersedes the others.

* **`DETERMINISTIC_SLOT_PRESENTATION.md` (31 Aug).** Steps 1–4 done. **Open:
  steps 5 and 6** — delete the ~900-line reverse-parse layer, re-aim the pinned
  tests. Its best claim is now confirmed at scale: `slot_followup` **0.13 s** vs
  `llm` **3.06 s** at p50, n=3,566.
* **`SLOT_PRESENTATION_CONVERGENCE.md` (3 Sep).** Phases 0 and 1 complete.
  **Phase 2 open**: one producer, one record, fewer guards. Its test-risk warning
  was measured and withdrawn — the real surface is **one literal pin** plus one
  negative assertion to preserve.
* **`ONE_PRESENTATION_LAYER.md` (9 Sep).** Stage A and Stage B **done**. **Stage
  C** evidence gathered, gate not met (S-6). **Stages D and E are NOT this week's
  work** — they buy onboarding speed, not correctness, and starting D trades a
  finishable week for an unfinishable one.

---

## 4. The work, in the order I now recommend

### 4.0 Why this order changed

Rev. 1 put S-1 first, explicitly because *"it needs no owner decision if you take
(a) and (b)"* and bought a 44 % cut in half a day. **Measurement falsified both
halves of that claim.** On a readout reproduced at 18.59 s, calibrated at
18.2 chars/sec against CAb5b52d95's own chunk timings:

| lever | rev. 1 estimate | **measured** | result |
|---|---|---|---|
| (a) month after day one | ~4.5 s | **0.66 s** | 17.93 s |
| (b) suffix on the 2nd time | ~3 s | **2.69 s** | 15.89 s |
| (a)+(b) | ~7.5 s | **3.35 s** | **15.23 s** |
| `speak_part_of_day: false`, all times | — | **5.17 s** | **12.76 s** with (a) |
| (c) `MULTI_DAY_MAX_DAYS` 3 → 2 | ~5.5 s | ~6 s | owner decision |

(a) was over-credited about sevenfold: it drops ONE word of the six rev. 1 counts
in a date. **So (a)+(b) reaches 15.2 s and cannot meet this item's own "under
12 s" gate.** The floor for three days at two times, keeping the part-of-day
suffix, is **14.85 s** — with no opener at all, weekday-only dates and minimal
punctuation. Time labels are **47 %** of the readout; the suffix alone is
**27.8 %**, which independently reproduces the LAT-1 measurement in
`speaks_part_of_day` ("4.7 s of 17.3 s"). The calibration is corroborated twice:
by `SLOWEST_REAL_CHARS_PER_SEC = 17.9`, already in the repo, and by that 27 %
figure derived from a different call.

**Consequence: S-1 is decision-blocked, not work-blocked.** There is no
engineering left in it — the remaining lever is one key in a JSON file. Queuing
real work behind a wording decision is how a week gets spent waiting. So S-1
moves to §4.4 as a decision takeable at any time, and the engineering queue is
re-ordered by **audible damage per hour of work**.

**The ranking principle, stated once:**

> **Dead air > repetition > length.** A caller who hears a long list interrupts —
> annoying, recoverable, and they still book. A caller who hears the same time
> offered for two different days concludes the system is broken and asks for a
> human. A caller who hears three seconds of nothing thinks the line dropped.

---

### 4.1 · S-3 — the filler fires AT the dead-air bar, not before it **← start here**

`ttfa` of 3118 / 3120 / 3128 ms on three consecutive turns is a constant, not a
coincidence. The published bar is *no dead air over 3 s without a filler*, and
the first rung lands just the wrong side of it, so **every slow turn breaches the
bar by construction**.

Cheapest fix in the register, worst failure mode behind it, and it needs nothing
from the owner.

**Scope it before changing it.** Find the constant, confirm it is a single
threshold, and enumerate what else reads it. This is a number, not a redesign —
but it sits on the hot path and it interacts with barge-in, which this codebase
has already been bitten by: the teardown fires on the PARTIAL and the noise
filter on the FINAL, and a caller's "uh" cut Susie off
([[barge-in-tears-down-before-the-noise-filter]]).

**Gate:** one call; `ttfa` p95 under 3 s in the next corpus pull.
**Est:** ¼ day of change, plus scoping.

---

### 4.2 · S-2 — the cross-day preference stands down too early

Once a day has been heard, `_prefer_unheard_clock_times` returns unchanged and
B-116 owns the readout — and B-116's rule is "unheard **on this day**". So from
the moment a caller names their first day, the cross-day preference never fires
again.

This is the **worst-sounding** defect left. It is also the same class as T1/T1b,
so the verification path is already built and understood.

**The refinement, and it is a strict one:** keep B-116's pool exactly as-is —
never offer a time already heard on that day — but *within* that pool prefer
clock times not heard on **any** day. Same all-or-nothing guard as T1: apply it
only when the fresh-anywhere subset can fill the readout alone, otherwise stand
down. Checked against the live call: Tuesday's unheard-on-Tuesday pool held six
clock times unheard anywhere, so the rule would have applied and given roughly
10:30 / 12:10 / 14:40 instead of 08:00 / 09:40 / 15:30.

**This changes a rule that was explicitly protected by a test**
(`test_the_same_day_rule_is_untouched`). That test must be re-aimed
**deliberately**, with the reason recorded — it is not a stale test, it is a
superseded decision.

**Watch `_spread`.** The owner decided on 1 Sep that two slots fifty minutes
apart are not a choice. T1b's first cut filled a short pool back up and produced
08:00 + 08:50. Any new selection rule must be all-or-nothing against it.

**Anchor:** `slot_followup.py:3737`, the `today in heard days` early return.
**Gate:** replay diff read **by direction**; failing-set diff empty; call script
step 4. **Est:** ½ day.

---

### 4.3 · S-5 — enforce the trim contract, and close the harness blind spots

*Make today's defect class unrepresentable. This is the stage that stops you
spending hours correcting.*

**S-5.** Five producers, four correct, nothing enforcing it. Two options:

* **(i) `build_slot_offer` performs the trim itself**, taking the session. One
  owner, no contract to violate. Cleanest, larger diff.
* **(ii) `build_slot_offer` refuses a day it can tell is untrimmed** — assert or
  log-and-trim. Smaller, and it turns a silent violation into a loud one.

**Recommendation: (ii) this week, (i) inside Phase 2**, where the signature is
changing anyway.

Add a **census test** that walks the module for callers of `build_slot_offer` and
asserts each is reached through the owner. A census test is the only thing that
catches producer number six.

> **A contract written in a docstring is not a contract.** `build_slot_offer`'s
> docstring states the trim contract explicitly. It was violated at one of five
> call sites (T1b-2) and nothing noticed. This item exists solely for that.

Then the three harness blind spots, each a one-line-ish change:

* **S-6** — give `_record_stood_down_slots` a failure line. **Amend the Stage C
  gate wording too:** a gate that cannot observe half of what it covers is not a
  gate.
* **S-8** — pass `presented_days` on the single_day path.
* **S-9** — carry a tool-call count on `TurnTiming`, so T2's central question
  becomes answerable.

**S-7 needs no code.** Already handled in `replay_presented_times.py`
(`_was_spoken`).

**Gate:** the deliberately-untrimmed case is refused in a unit test; each blind
spot verified **from the corpus**, not from the diff. **Est:** ¾ day.

---

### 4.4 · S-1 remainder — an owner decision, takeable at any time

**Not queued. Not blocking. Two minutes of work whenever it is decided.**

Lever (a) is landed. Everything further changes what the caller hears, so it is
the clinic's call, not the engine's — which is precisely why the codebase put it
behind a per-clinic flag rather than an engine default.

#### What `speak_part_of_day: false` actually does

| readout | today (with (a)) | flag off | saving |
|---|---|---|---|
| multi_day, 3 days × 2 | 17.93 s | **12.76 s** | −5.17 s (−29 %) |
| single_day, 3 times | 10.23 s | **7.64 s** | −2.59 s (−25 %) |

#### What provably does NOT change — verified 10 Sep, not taken from the docstring

* **Caller resolution is identical.** Seven utterances A/B'd through the real
  `slot_accepted_by_caller`, on a session built by `apply_offer_to_session`: same
  ISO start in both modes, **7/7** — including "monday at eight in the morning"
  resolving against a bare "eight".
* **The SMS is unaffected.** `sms_templates.py:126` builds the time from the
  datetime as `%I:%M%p` → "8:00am". The patient still gets **am/pm in writing**
  whatever the flag says. This is the strongest mitigation of the ambiguity
  worry.
* **The booking record, the keypad map and all three harnesses.** Wording only.

#### What the risks actually are

* **S-10, the half-wiring.** See §2.2. The readout goes bare; the model's
  confirmations stay banded. That is arguably the *best* outcome — a fast offer,
  a precise confirmation — but nobody decided it, and it is not what the flag's
  name claims.
* **The bare-number collision is real, known, and already fixed where it bit.**
  `payload_slots_named_in` carries a comment saying bare labels made a 3 pm slot
  "named" by any list containing "Number 3", and that guarding only the fallback
  *"left exactly those clinics exposed"*. The other five `_time_named_in` sites
  scan **caller** text, which never says "Number 3", and the bare form is already
  tried as a fallback at all of them today. **The flag promotes bare from
  fallback to primary; it does not open a new class.**
  `_offered_time_named_without_its_band` becomes a silent no-op under the flag —
  correctly, since it is then redundant.
* **What is genuinely lost:** the caller's *spoken* confirmation that eight means
  morning. The corpus behind the flag (236 offers, 2262 labels) found zero
  ambiguous pairs, because a clinic day spans under twelve hours so a clock face
  names exactly one time. The information survives; the reassurance does not.

#### Why lever (b) as specified was NOT taken

`_pick_times_for_day` **deliberately** picks the second slot in a DIFFERENT part
of the day (measured: 3 of 4 representative rotas). So lever (b) strips the band
from exactly the slot whose band the caller cannot carry over from the first. It
is also half of a wording decision this codebase already assigned to
`clinic.json`, for **less than half** the saving of doing it properly.

#### Recommendation

Take it on **northgate only** — the demo line, no patient can hear it — push to
`latency-eval`, and judge 12.76 s by ear. One key, no engine change, revertible
in seconds. If it sounds right, the next question is whether to fix the prompt's
three banded-time instructions so confirmations match, or deliberately keep them
banded.

**Note:** even with the flag, 12.76 s. Under 12 s additionally needs lever (c),
`MULTI_DAY_MAX_DAYS` 3 → 2 (`slot_offer.py:50`).

> **The prompt already asks for this.** The rendered northgate prompt contains
> *"Keep the whole slot offer under about eight seconds."* The 18 s readout has
> been violating a standing instruction the whole time.

---

### 4.5 · Phase 2 — weekend buffer, or next week

*Only if §4.1–4.3 are closed and called.*

Take `SLOT_PRESENTATION_CONVERGENCE.md` Phase 2 in its own order — **one record
before deleting guards**, never the reverse. Then step 5 of the 31 Aug document:
delete the reverse-parse layer.

**Do not delete the repair layer on a clean Render grep.** Site A is reachable
only when no deterministic offer was built, and `slot_offers` records only the
turns where one *was* — so the population that reaches it leaves no row.
Instrument that first. (`STAGE_C_EVIDENCE_2026-09-10.md` §5.)

Most likely to slip past Friday. **That is acceptable.** §4.1–4.3 deliver the
goal; this is what stops it decaying.

---

### Explicitly not this week

Stage D (provider interface) · Stage E (clinic policy) · the STT numeral gap ·
anything touching `_LAST_BOT_PROMPT_CAP` · general latency work beyond S-3.
B-146 is excluded **pending an explicit decision**, not by default.

---

## 5. Verification protocol — non-negotiable

**The suite is red on purpose. Do not look for green; diff the failing sets.**

Baseline at `337fbd9e` / `7654561c`, measured 10 Sep: **98 failed**, 9688 passed.

> **EXCLUDE `tests/auto/test_acuity_live.py` from the failing-set diff.** It hits
> the live Acuity API, which is returning `ProviderUnavailable` today, and its
> failing set **varies run to run on the untouched baseline** — measured three
> consecutive times, three different sets. Excluding it, the baseline is
> **96 failed**, and that number is stable. It is read-only (0 write call sites),
> so running it books nothing.

```bash
# BASELINE — a SECOND worktree at the base commit, never touched
git worktree add --detach /tmp/base <base-sha>
cp .env /tmp/base/.env && cp tests/auto/.env /tmp/base/tests/auto/.env
cd /tmp/base && python -m pytest -q -p no:randomly > /tmp/base.txt 2>&1
grep -E "^FAILED " /tmp/base.txt | sed 's/ - .*//' | grep -v test_acuity_live | sort > /tmp/B.txt

# CANDIDATE — freeze the tree, then run
md5sum <every file you changed> > /tmp/md5.before
find . -name __pycache__ -type d -prune -exec rm -rf {} +
python -m pytest -q -p no:randomly > /tmp/head.txt 2>&1
md5sum -c /tmp/md5.before                 # MUST all say OK
grep -E "^FAILED " /tmp/head.txt | sed 's/ - .*//' | grep -v test_acuity_live | sort > /tmp/H.txt
diff /tmp/B.txt /tmp/H.txt                 # must be EMPTY
```

**Never run the suite in a worktree you are editing** — ~55 `inspect.getsource`
tests fail as an artefact and it reads as catastrophe.

### The three harnesses, and what each is blind to

| harness | covers | blind to |
|---|---|---|
| `replay_slot_decisions.py` | slot decisions, 1,851 turns scored | **the whole selection** — `choose_presented_indices`, `remaining_unspoken_on_current_day`, `all_remaining_on_next_day`. It said `CHANGED: 0` on both T1 and T1b. |
| `replay_presented_times.py` | the per-day selection, 416 day-readouts | the **loop** around it. It reported 0 changed while the live readout was broken. |
| `replay_multi_day_spread.py` | the multi-day loop, 61 readouts | the named-day producers |

```bash
python scripts/replay_slot_decisions.py  --out BASE.json   # then --diff BASE.json CAND.json
python scripts/replay_presented_times.py --out BASE.json   # then --diff BASE.json CAND.json
python scripts/replay_multi_day_spread.py                  # before / after
```

Both `--diff` flags take **two** arguments, base and candidate.

Gates that must read 0: `lost_a_slot`, `invented_a_slot`, `changed_a_heard_day`.
Read the rest **by direction** — a pick changing to a *different* slot is
dangerous; a pick *lost* is usually a guard working.

**No single harness covers slot presentation.** Run all three, every time. That
sentence is the whole lesson of 9–10 September.

### Measuring a readout without a phone

Reproducible in synthesis, and it agrees with the live chunk timings:

* calibration **18.2 chars/sec** at `ELEVENLABS_PHONE_SPEED`, from CAb5b52d95's
  quoted day-line (100 chars in ~5.5 s);
* corroborated by `SLOWEST_REAL_CHARS_PER_SEC = 17.9` in
  `tests/regression/test_o_absolute_play_cap.py`, derived independently from a
  93-char greeting in 5.2 s.

Build the payload with `slot_times` + `slot_times_spoken` — **not** `slots` with
a `spoken` key, which `flatten_bookable_slots` silently drops, giving you a
readout of raw "08:00" labels and a flatteringly short measurement.

### The call script — the exit criterion

Two calls, two different clinics, at least one on a **non-grid diary** (Vital
Edge or JV — neither has been called since T1b, and northgate's uniform grid is
the easy case).

1. *"I'd like to book an appointment — my ankle."*
2. *"Anytime next week."* → three days, no shared clock time
3. *"Tell me about Monday."* → **nothing you already heard for Monday**
4. *"And what about Tuesday?"* → nothing you heard for Tuesday, **and no time
   repeated from step 3**
5. *"Anything around midday on Tuesday?"* → **midday is offered** (D8)
6. Take a slot, and check the diary entry matches what you were told.

Step 4's second clause is S-2. Step 6 is the only step that proves the readout
and the booking agree.

**Build SHA is the only proof of what ran:** `[build_info] running build <sha>`
at call cleanup. `/health` returns a hardcoded 1.0.0 and always has.

---

## 6. Deploy discipline

Promotion is **fast-forward, one direction**, `latency-eval` → `production`. A
merge commit here means someone fixed something in the wrong place.

```bash
git log --oneline origin/production ^origin/latency-eval        # MUST be empty
git rev-parse origin/production                                 # WRITE THIS DOWN
git diff origin/production..origin/latency-eval -- app/ | \
  grep -E "^[+-].*(SMS_ENABLED|APPOINTMENT_REMINDERS_ENABLED|SHEETS_ENABLED|OBS_.*_ENABLED)"
git push origin origin/latency-eval:production
```

That last grep is not optional. Those code defaults **must stay OFF on both
branches**, or a test call texts a real patient.

A push to `production` reaches three live clinics with `autoDeploy` on — real
call after any engine change. **`latency-eval` serves the demo line
(+447366263180) and nothing else; it is the safe place to push.**

---

## 7. Traps — every one of these cost real time

### Carried forward

**A change can ship completely inert.** Three times in one day on 9 Sep: a `\b`
written as a literal backspace byte; a hold phrase silently refused by
`_second_filler_text`; config constants defined but never imported, raising
`NameError` inside a background task where it may never reach a log.

**Test BEHAVIOUR, never presence.** `assert "X" in source` proves nothing. All
three of the above passed a presence-style check. Drive the real function.

**Verifying the function is not verifying the system.** T1 was correctly verified
against `choose_presented_indices` and reported as fixed. The live call went
through two other sites and heard the identical defect. **Enumerate a rule's
callers before claiming coverage.**

**A mock of the loop cannot see the loop's bug.** Drive the real entry point.

**Built is not spoken.** 13 % of recorded offers were never said out loud.

**A safety property can live in an idiom.** `len(hits) == 1` also silently meant
"decline when the same time sits on several days".

**`_spread` outranks new preferences.** Two slots fifty minutes apart are not a
choice.

**Prompt hashes live in TWO tables under different names**, each with its own
`_sha`. Recompute per table; never copy a value across.

**`git stash` does not revert here** (OneDrive locks) — back changes out by hand.
Use `git -C <path>`, never `cd X; cmd`. Run `git worktree prune` and
`git rev-parse --abbrev-ref HEAD` before you trust a single number: there are
160+ registered worktrees.

**`receptionist_tools.py` is CRLF** — and so are `slot_offer.py` and
`slot_followup.py`. A whole-file rewrite lands as a 16k-line diff. Preserve line
endings when scripting an edit.

### New, 10 Sep PM

**A dedupe that reads the RENDERED SENTENCE is a wording dependency, not a
record.** B-111's *"I've also got another Tuesday, the 15th"* check matched the
payload label against the readout text. Shortening one label made it silently
inert, and Susie offered a date one sentence after reading it out. Key such
checks on **what the offer named**, never on how it worded it.

**A negative assertion can go vacuous under a wording change.**
`assert "Tuesday 8th September" not in spoken` cannot see a day re-read as
"Tuesday the 8th". Two existing tests would have passed while the defect
occurred. **The failing-set diff structurally cannot catch this class** — they
pass either way. When you change wording, grep the suite for negative assertions
on the old form and re-aim them to a wording-independent comparison.

**The record is not the speech, and `dtmf_map` must stay canonical.**
`day_selected_by_position` matches the map's value against
`available_days[].day_label` by **containment**. Shorten the map and "the second
one" resolves to `None` — indistinguishable from a caller who named nothing.
Verified both ways.

**Shortening a date must be guarded on the month end.** "Tuesday the 1st" after
"Monday 30th September" is heard as September. Decide on the ISO date, never on
the prose.

**A wording change that reaches a caller belongs in `clinic.json`.** The engine
already has the flag (`operational.speak_part_of_day`). Re-deciding it in engine
code is the CLAUDE.md violation, and doing it by halves is worse than both.

**Check what the RENDERED prompt says, not the module.** The "Always say the FULL
spoken time" instruction in `susie_system_prompt.py` does **not** reach
northgate's rendered prompt; three other places instruct the band instead.
`build_system_prompt_parts` is the authority.

**The live Acuity tests are externally nondeterministic.** See §5.

**The local `latency-eval` ref goes stale** because a parallel session holds the
branch in its own worktree. Fetch first, or diagnose an already-fixed bug.

---

## 8. Order of work, on one page

| # | item | gate | est. |
|---|---|---|---|
| 1 | **S-3** filler fires at the dead-air bar | `ttfa` p95 < 3 s, one call | ¼ day + scoping |
| 2 | **S-2** cross-day preference stands down | replay by direction, call script step 4 | ½ day |
| 3 | **S-5** trim contract + census test | untrimmed day refused in a test | ½ day |
| 4 | **S-6/S-8/S-9** harness blind spots | verified from the corpus | ¼ day |
| 5 | **Phase 2** one record, then delete the guards | its own plan's gates | ≥ 2 days |
| — | **S-1 remainder** | **owner decision, not queued** — §4.4 | 2 min |

Items 1–4 are the week. Item 5 is what stops the week decaying.

**If you have time for exactly one thing: item 1.** Cheapest fix in the register,
worst failure mode behind it, and it needs nothing from the owner.

**Landed already:** S-1 lever (a), `40a67ea8` — not pushed, not call-verified.

---

## 9. Handover rules

* **Commit messages carry the exhibit** — call SID, what the caller said, what
  Susie said. Rules get re-softened once the call behind them is lost.
* **Every behavioural fix ships with a regression test** in `tests/regression/`.
* **Clinic-specific behaviour belongs in `clinic.json`**, never in engine code.
* **If these documents and the code disagree, the code wins.** Record the
  correction here. Rev. 2 exists because rev. 1's central estimate was wrong by
  sevenfold, and the one thing that has consistently worked on this codebase is
  measuring rather than believing.
