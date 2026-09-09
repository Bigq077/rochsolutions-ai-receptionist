# Scoping — one presentation layer for every booking provider

**Written** 2026-09-09, after a Theorem call read a caller's chosen 6pm slot back
to them as 1pm.
**Question it answers** what has to change so that adding Jane, Carepatron or
Cliniko is *an API call and a set of IDs*, and nothing else.
**Status** scoping only. No code changed. Nothing here is safe to start without
the decisions in §7.

---

> ## ⚠️ CORRECTION, 2026-09-09, same day
>
> **Two rows of the table below were wrong and the headline number does not
> measure what it claims.** Verified with an AST walk per function rather than
> line numbers:
>
> * `other_dates_for_requested_day` — the fall-through (Northgate, JV) and
>   `diary` (Vital Edge) BOTH call `_name_the_other_matching_dates`. Only
>   `published` lacks it, and no live clinic uses `published`. **The claim that
>   B-110 was regressed on Northgate and JV was FALSE.**
> * `day_requested*` and the window feedback are likewise present on the
>   fall-through and diary. Only `published` lacks them.
> * The 83%/14% "deterministic rate" used `slot_offers.presented` as its
>   discriminator. That field only ever exists for **multi_day**, so every
>   single_day offer was miscounted as model-written on every clinic. The
>   number is not usable and is withdrawn.
>
> **The real defect is one branch, and it is confirmed from logs rather than
> inferred.** `_check_availability_acuity` sets `first_day` (line 3736) so
> Theorem's SINGLE-day readouts do get the deterministic builder — seen live at
> 10:04:46, `deterministic single_day offer built: 1 chunk(s)`. It never sets
> `presented_days`, so MULTI-day always fails the gate — seen live twice, on two
> different builds:
>
> ```
> 10:03  NO deterministic offer built — mode='multi_day' has_presented_days=False
> 11:04  NO deterministic offer built — mode='multi_day' has_presented_days=False
> ```
>
> The cause is at line 3599, and the comment above it states the old contract
> out loud:
>
> ```python
> # The <=2-times-per-day cap is enforced by the slot formatter
> # (SLOT_FORMATTER_SYSTEM_PROMPT, multi_day).
> _present_days = days_data[:3] if _presentation_mode == "multi_day" else days_data
> ```
>
> Theorem's multi-day cap is enforced **by the prompt**, not by code. That is the
> pre-`slot_offer.py` architecture — the model writes the sentence and a repair
> layer reverse-parses it — which that module's own docstring says "cannot
> converge" and where "fifteen B-numbers live". Theorem's single_day path was
> migrated; its multi_day path never was.
>
> Everything in §5 (target shape) and §6 (stages) still stands. **Stage 1 is
> deleted** — it was fixing a gap that does not exist. The real first step is
> Stage 2.

---

## 1. The finding in one table

There are **four** availability readers. They do not agree, and it is not a
two-way split.

| | Acuity | fall-through | diary | published |
|---|---|---|---|---|
| **used by** | Theorem | Northgate, JV | Vital Edge | provisional |
| **lines** | **933** | 686 (dispatcher) | 317 | 160 |
| `_cap_presented_slots` | ✗ | ✓ | ✓ | ✓ |
| deterministic slot sentence | ✗ | ✓ | ✓ | ✓ |
| offer record + DTMF map written | ✗ | ✓ | ✓ | ✓ |
| `operational.slot_presentation` caps (D2) | ✗ | ✓ | ✓ | ✓ |
| `other_dates_for_requested_day` | ✓ *(own)* | **✗** | ✓ | ✓ |
| `lead_in` ("the earliest I have is…") | ✓ *(own)* | ✗ | ✗ | ✗ |
| named-weekday contract (`day_requested*`) | ✓ *(own)* | ✗ | ✗ | ✗ |
| window-widening feedback | ✓ *(own)* | ✗ | ✗ | ✗ |
| `band_spent_label` | ✓ *(own)* | ✗ | ✗ | ✗ |

Read the last five rows before concluding Acuity is simply behind. **It is
ahead on five features and behind on four.** Convergence is a merge in both
directions, not a deletion.

`other_dates_for_requested_day` is the sharpest example: `_name_the_other_matching_dates`
is called at two sites only (7:6924 diary, 7:7340 published). Northgate and JV —
the two clinics on the "good" path — never get it. So a JV caller asking about
Tuesdays is told about two of four and never hears the rest exist. That is B-110
regressing on the one path nobody checked.

---

## 2. What it cost, measured

**Deterministic sentence rate**, offers recorded since 25 Aug, discriminated by
whether the record carries a capped payload (only `_cap_presented_slots` writes
one):

    northgate    47 offers, 39 deterministic   (83%)
    theorem_v3   14 offers,  2 deterministic   (14%)

**The live damage**, Theorem `CAba2e3d8eb282bb`, 9 Sep 11:05, build `ba315832`:

```
11:05:09  Susie : "Wednesday the 16th — I've got one o'clock or six in the evening."
11:05:22  caller: "uh yeah 6 in the evening works"
11:05:24  [ms_gate5] read-back time corrected: 'six in the evening' ->
          'one in the afternoon' (the offer had it right; the model crossed two options)
11:05:30  Susie : "So that's Wednesday the 16th of September at one in the afternoon…"
```

The model read back **correctly**. The guard overrode it. Its input is the offer
record, and on Acuity that record is never written —
`slot buf: could not resolve spoken option(s) … offer record left unchanged` —
so it compared against a stale projection and corrupted a correct read-back.
Caller abandoned; judge score 2.

**A guard that only has trustworthy input on three of four paths is worse than
no guard on the fourth.** That is the argument for doing this, and it is
stronger than the consistency argument.

---

## 3. Where the seam actually is

The dispatch tree today:

```
_exec_check_availability                          app/tools/receptionist_tools.py:6240
├─ uses_acuity()                    → _check_availability_acuity        :2966-3899
│                                     then _filter_same_day_slots ONLY  :6377
├─ booking_system == "google_calendar_provisional"
│   ├─ availability_mode "diary"    → _check_availability_diary         :7025-7342
│   └─ else                         → _check_availability_published     :7345-7505
└─ fall-through (generate_candidate_slots / filter_free_slots)          :6538,6605,6712
```

Line 6377 is the whole defect in one line: the Acuity branch returns after
`_filter_same_day_slots` and never reaches `_cap_presented_slots`.

**The contract the other three already honour** — a provider returns:

```python
{"available_days": [...], "total_days": N}      # plus error/message on failure
```

`_check_availability_diary` and `_check_availability_published` emit exactly
that: `available_days, total_days, slots, start, error, message`. Nothing else.
Then the shared layer adds `presentation_mode`, `first_day` | `presented_days`,
`more_times` and `sparse_rota_note`.

**Acuity emits 22 fields and builds its own payload.** The seam is
**line 3522**, where `days_data` is complete after the same-day drop. Above it:
fetch, the 90s cache, lead-time, working hours, bank holidays, error returns.
Below it: 376 lines that are presentation.

That is the cut. Everything below 3522 has an owner elsewhere or should have.

---

## 4. Classifying the 933 lines

| block | lines | verdict |
|---|---|---|
| 2966–2998 args, appointment-type / calendar id resolution | 32 | **provider** — stays |
| 2998–3138 90s availability cache | 140 | **generic**, Acuity-only today |
| 3138–3220 B-86 window widening | 82 | **generic**, Acuity-only today |
| 3220–3499 lead-time, working hours, bank holidays, error returns | 279 | **mixed** — see below |
| 3499–3522 `_build_days_data`, same-day drop | 23 | **the seam** |
| 3523–3545 session writes + cache populate | 23 | **generic** |
| 3546–3600 `presentation_mode`, `days_data[:3]` | 54 | **presentation — delete, it is `_cap_presented_slots`** |
| 3601–3700 `day_requested*`, `guidance`, window feedback | 99 | **promote** to the shared layer |
| 3703–3899 `first_day`, `more_times`, `band_spent_label`, `other_dates`, `lead_in` | 196 | **mixed** — some duplicate, some promote |

Roughly **250 lines delete**, **300 promote**, **380 stay**. Those are estimates
from section boundaries, not a line-by-line audit — the audit is step 1 of §6.

The 279-line filter block is the awkward one. Lead-time, working hours and bank
holidays are *clinic policy*, not Acuity policy, and the other three readers
handle them differently or not at all. Whether they move above the seam is a
correctness question about the other three clinics, and it is the part of this
work most likely to change behaviour somewhere nobody is looking.

---

## 5. The target shape

```
provider adapter                 ← the ONLY thing a new clinic adds
  fetch_free_slots(window, ids) -> [(start, end)]
        │
        ▼
shared availability pipeline     ← one owner, four clinics, every future one
  _filter_same_day_slots         same-day policy
  clinic policy filters          lead time, working hours, closed dates
  _build_days_data               grouping, spoken forms, band handling
  _cap_presented_slots           how many, and which          (D2 lives here)
  _name_the_other_matching_dates the dates held back          (B-110)
  build_slot_offer               THE SENTENCE                 (one owner)
  apply_offer_to_session         the record + the DTMF map
        │
        ▼
   speech, keypad, read-back guard — all reading one record
```

Adding Jane becomes: write `fetch_free_slots`, put the IDs in `clinic.json`,
done. That is the outcome you asked for, and it is not reachable by patching
Acuity to emit `presented_days`.

---

## 6. Staged plan — as decided 2026-09-09

Owner decisions are in §7. Stage 1 of the original plan is **deleted**: it fixed
a gap that did not exist (see the correction at the top).

Each stage ships alone and reverts alone. Work happens on `feat/one-presentation-layer`,
lands on `latency-eval`, and reaches the live branches later by fast-forward.

**Stage A — Acuity multi_day joins the deterministic builder.** The narrow fix,
first and on its own, because it is what a caller actually heard and it is
testable against two stored Theorem calls without touching the architecture.
`_check_availability_acuity` stops capping and stops deciding
`presentation_mode`; the dispatcher routes its result through
`_cap_presented_slots` like the other three.

*Contract change to watch:* Acuity today puts the **trimmed** days in the
result's `available_days` and the **full** set in `session["available_days"]`.
Every other reader puts the full set in both and lets `_cap_presented_slots`
derive `presented_days`. Stage A aligns Acuity to that, so anything reading the
result's `available_days` as "what was spoken" changes meaning.

Gate: `deterministic multi_day offer built` on a Theorem call; the read-back
correction misfire unreproducible on the stored calls; suite clean.

**Stage B — `lead_in` everywhere. DONE, and it hit a ceiling worth reading.**
Owner decision 2. `_cap_presented_slots` now sets `lead_in="earliest"` for the
three readers that come through it, guarded by two conditions rather than one:
the caller asked for the soonest (`caller_wants_soonest`, the same predicate
B-137 and B-142 already steer by — not a fourth ASAP matcher), AND the day being
read out is the soonest in the payload (`_earliest_available_date`). Acuity is
untouched, and the containment is structural: stage A routes it through this
function only on multi_day, and multi_day never carries a lead-in (B-125).
`earliest_lead_in_is_true` still has the last word on the within-day half.

**THE CEILING.** Verified against both real backends, no call and no deploy:

```
northgate  "as soon as possible"  -> multi_day, no first_day, no lead-in
theorem    "as soon as possible"  -> single_day, first_day, lead_in=earliest
```

The two paths decide `presentation_mode` by different rules — Acuity from the
CALLER'S REQUEST, the shared layer from the DATA — which stage A documented and
deliberately did not converge. A lead-in is a claim about ONE day, so it can only
ride on `single_day`; and on the shared readers an ASAP request stays multi_day
whenever more than one day survives the filters. **So decision 2 is delivered as
far as the current mode rule allows, and no further.**

Measured, offers in the 21 days to 9 Sep: northgate reaches `single_day` on
**17%** (8 of 48), theorem on **80%** (12 of 15), JV n=1. So on the busiest of
the three the opener can fire on at most one offer in six — and only the subset
of those where the caller asked for the soonest.

**RESOLVED the same day — owner chose option B, and the mode rule stays put.**

The decision put to the owner was narrower than "converge the mode rule". Acuity
decides the mode in two arms, and only ONE of them disagrees with the data rule:

| arm | Acuity | shared readers | agree? |
|---|---|---|---|
| caller NAMES a day | `single_day` | `single_day` — the filter leaves one day | ✅ |
| caller asks for the SOONEST | `single_day` | `multi_day` | ❌ |

So the whole question was: *when a caller asks for the soonest, do they hear one
day with three times, or three days with two times each?*

**The evidence looked stronger for switching than it was, and it nearly went in
the write-up that way.** Five northgate ASAP calls, three tagged
`caller_frustration` or `loop`, mean score 2.4. But four of the five are from
4 Sep — the day `040baa47` and `7eb61dd2` landed, which are precisely the
B-137/B-142 fixes for "not soon enough" being answered with slots FURTHER AWAY.
The frustration in those transcripts is that loop:

```
caller: that's not soon enough can you give me a slot that's sooner
Susie : [reads the identical three days back, verbatim]
```

The one ASAP call after the fix (`CA8d5b2e3e`, 7 Sep) shows no loop at all.

**What survived is a sentence, not a structure.** B-137 already puts the
earliest day first when the caller asks for the soonest — the list IS the
answer — and Susie simply never says so. On `CA1c6c836` the caller had to push
three times before hearing *"those are the soonest available"*.

**Option B, shipped:** keep the three days, and make the ordering explicit.
`_cap_presented_slots` sets `lead_in="soonest_first"` on multi_day when the
caller asked AND day one really is the earliest, and the opener becomes
*"Starting with the soonest —"* instead of *"Here's what we've got coming up —"*.
Nobody's choice set changes, so the blast radius is a phrase.

The wording names no date on purpose. Both candidates that did —
*"The soonest I have is Monday 14th September — Number 1, Monday 14th
September — ..."* — repeat the label in the very next clause, and a date said
twice in one breath is worse than a date not ranked.

`soonest_first` is a SEPARATE TOKEN from single_day's `earliest`, because the
payload reaches the model as well as the deterministic builder and
SLOT_FORMATTER maps `earliest` onto the single-day opener. B-125 is unchanged.

**This does move Theorem**, unlike stage A: its single_day lead-in is still
Acuity's own and unreachable from here, but its MULTI-day readouts come through
`_cap_presented_slots`, so a Theorem caller whose `day_preference` means
"soonest" now hears the ordering opener too. That is decision 2 being delivered,
not a leak.

**Option A — ASAP → `single_day` everywhere — is NOT dead, it is deferred.** If
callers still push back on the soonest after B, it is the answer and stage A's
`mode` seam already carries it. It was declined now because it changes what
7–9% of callers hear on two live patient lines to fix something that was mostly
fixed on 4 September, and because one day with three times is a genuine
take-it-or-leave-it risk that Theorem accepts by an owner decision of 15 June
and the other two clinics have never been asked about.

**Stage B, the follow-up — the opener did not fire, and the reason was not the
opener.** Demo call, 9 Sep 13:11, build `b5f5c9975949` confirmed in the log:

```
13:11:11  caller: "um what's the soonest available slot you have"
13:11:15  tool: check_availability  date_hint="as soon as possible"
13:11:15  chunk 1/3: "Here's what we've got coming up — Number 1, Wednesday 9th Se"
```

The model understood perfectly — it said *"Let me find the soonest I've got"*
and put the right hint in the tool args. What never happened was the SESSION
capture: `_extract_day_preference` in `connection.py` knew the literal phrases
"as soon as possible" and "asap" and **nothing else** — not "soonest", not
"earliest", not "sooner" — so `day_preference` stayed empty and
`caller_wants_soonest` was False.

**This was never really a stage B defect.** `caller_wants_soonest` gates three
things and the lead-in is the least of them:

| consumer | fix | what a missed capture costs |
|---|---|---|
| `choose_presented_days` | B-137 | leads with the UNHEARD days instead of the earliest ones |
| `choose_presented_indices` | B-142 | reads each day from times the caller has not heard, not its earliest |
| `_cap_presented_slots` | stage B | no ordering opener |

So a caller who said *"sooner"* rather than *"as soon as possible"* got the
**pre-B-137 behaviour**: asked for something sooner, answered with days further
away. B-137 was only ever armed for callers who used its one phrase — and
`CA5685a2ab`, the call it was written for, says *"that's not soon enough"*,
which the matcher could not see. The 4 September fix has been half-live for five
days.

`_extract_day_preference` is the ONLY writer of `day_preference`; the two other
"soonest" vocabularies in the repo (`_SCHEDULING_SINGLES`, `_VACUOUS_DATE_HINTS`)
already know these words but feed routing and hint-classification, not this
capture.

**Fixed by vocabulary plus a guard, not vocabulary alone.** Adding the words
bare re-creates the B-138 family: *"what's the earliest on Thursday"* would bank
"as soon as possible" and `choose_presented_days` would then lead with the
globally earliest days, dropping the only day the caller asked about. So a
concrete weekday or "next week" in the same utterance wins. Bare "soon" is
deliberately absent — *"see you soon"* is not a slot request and this capture
persists for the whole call.

**One pre-existing bug fixed with it.** *"Saturday is not soon enough"* banked
**saturday** on `b5f5c997` too, via the bare-weekday arm — a rejection read as a
request, pinning every later readout to the day the caller had just turned down.
It is the same call and the same sentence, so it ships here rather than as a
separate row. Only the NEGATED frame: *"is Thursday soon enough?"* is a caller
ACCEPTING Thursday and still resolves to thursday.

Executable surface: three module-level regexes and two branches. Test:
`tests/regression/test_soonest_vocabulary_arms_the_soonest_rules.py`, which
asserts through `caller_wants_soonest` rather than the matcher's return value —
a test that checks only the string cannot see `_SOONEST_DAY_PREFERENCES` drift
out from under it.

**D10 and D11 — the two missing sentences. BOTH FIXED 9 Sep 2026.**

Neither is a slot-selection defect. In both the structure was already right and
the honest sentence was absent, which is the same shape stage B turned out to
have — so all three belong together.

**D10 — the completeness hedge was dead code.** `build_slot_offer` picks
between a claim about the diary and a hedge:

| opener | means |
|---|---|
| `"Here's what we've got coming up —"` | this is the diary |
| `"I've got a few days —"` | there are more than these |

and it chose from `len(days) > len(days[:max_days])`. That can only be true of
an UNTRIMMED list, and every live path hands it `presented_days`, already capped
to three by `_cap_presented_slots`. The hedge was unreachable; the confident
sentence went out unconditionally. Proved on the 9 Sep 12:20 northgate call —
**7 days found, 3 spoken, opener "Here's what we've got coming up"** — the exact
claim its own comment forbids, caller-facing on all four clinics.

**The first cut of the fix was wrong and the suite caught it.** It published a
new `days_not_shown` from `_cap_presented_slots`. That name already exists,
already means this, and is written by `_check_availability_acuity` alone —
`max(days_found − days_SPOKEN, 0)`, B-94. `_cap_presented_slots` is *forbidden*
to write it, because post-processing owning that name lets the truncated view
overwrite the honest count; `test_the_honesty_fields_are_not_clobbered_
downstream` failed within a minute of the change. Worth recording because the
same collision is available to anyone who reaches for the obvious name.

The shipped fix is one pure function, `days_were_held_back(result)`, with two
arms in priority order: the retrieval layer's `days_not_shown` where it exists
(Acuity/Theorem — and strictly better, because it sees days a `single_day`
presentation hides, where a local comparison reads 0), and found-versus-spoken
otherwise. That second arm is what covers the google_calendar fall-through
(northgate, JV), `diary` (Vital Edge) and `published` — which emit no honesty
fields at all, and are where the defect was observed. `build_slot_offer` gains
`more_days`, the exact twin of the `more_times` contract B-97 established.

Stage B's `soonest_first` opener makes no completeness claim, so it was never
affected either way — which is why D10 did not fire on the 13:43 call.

**D11 — "that's not soon enough", answered with the same slots.** 9 Sep 13:43,
northgate, build `38709d5fbecb` — the call that verified the soonest-capture
fix. The opener was right:

```
Susie : Starting with the soonest — Number 1, Wednesday 9th September —
        half past three in the afternoon, or ten past five in the evening. ...
caller: um that's not soon enough
Susie : I've got today — Wednesday the 9th of September — at half past three in
        the afternoon, or ten past five. Do either of those work?
```

The same two times, restated as though new. It was 13:43, so **half three today
genuinely was the first slot in the diary** — the content was correct and the
sentence saying so was missing. B-137 fixed WHICH slots a push-back gets; one
turn later, nothing said "this already is the earliest".

Three reasons nothing caught it, all worth keeping:

* `utterance_requests_more_slots` matches "later", "else", "other", "another",
  "instead" — every one a request to move AWAY. This caller is asking to move
  NEARER, so no matcher fired and the turn fell to the model.
* No `check_availability` ran (`slot cache kept — awaiting slot selection`), so
  the whole `_flush_slot_buf` apparatus — `sparse_rota_note`, `band_spent_label`
  and the deterministic builder — was dormant. **Everything stage A and B built
  governs the FIRST readout; the push-back turn was ungoverned.**
* `_sparse_rota_note` is the nearest existing thing and cannot fire here: it
  needs the earliest day several days out (today is 0) and a second clinic site
  (northgate is single-site).

Fixed as a deterministic producer, `nothing_sooner_speech`, in
`try_unspoken_followup_speech` — the pre-LLM dispatcher that already answers
"what else have you got" from the cached payload with no tool call. Whether the
diary holds anything earlier is a fact, and per `_sparse_rota_note`'s own rule
a fact about the diary is decided from the payload and never guessed.

> *"I wish I had something sooner — Today at half past three in the afternoon is
> genuinely the first slot we've got, nothing before it. Does that one work for
> you?"*

Four conditions, each closing a way the sentence could be a lie:

1. the caller asked for something sooner **on this turn** — not
   `caller_wants_soonest`, which is a standing preference and would apologise
   for an offer nobody objected to;
2. no day is a band-filtered view (B-97) — where `times_not_shown` is positive
   this payload's earliest is merely the earliest that survived the filter;
3. the earliest slot in the payload has **already been spoken**. If something
   earlier sits unspoken the honest answer is to read it, which the producers
   below already do;
4. it has not been said about that same slot before — a repeated push-back
   answered identically is the going-in-circles shape this exists to end.

It touches no offer state: the keypad, `last_offered_slots` and the slot window
still describe the live offer, so the caller can still take one of the other
days. 159 characters, ending in a question — inside the 200-char
`last_bot_prompt` cap, so B-31 cannot strip the "?" and disarm clinical
screening's orphan matching.

**D12 — a fix's own bill, found by the call that verified the fix before it.**

9 Sep 14:32, northgate, build `909a90ad3752`. D11 fired perfectly (and in
**132 ms** — `path=slot_followup`, against 2.7–9.5 s for every LLM turn on that
call, because it never reaches the model). Then:

```
caller: okay then what else have you got this week
[slot_followup] every day in the sweep has been offered -- falling through
Susie : this week I've also got Thursday at eight in the morning, or ...
        Friday at eight in the morning, or half past three ...
```

Thursday and Friday read straight back — Numbers 2 and 3 from ninety seconds
earlier. The going-in-circles shape B-137 and D11 both exist to end.

`more_days_speech` picked its candidates with `choose_presented_days`, which
answers a **different question** — which days should LEAD a fresh readout — and
short-circuits on `caller_wants_soonest` to `days[:max_days]`, the three
earliest. For this caller those were the three just heard, so the unheard filter
emptied the list, the producer declined, and the model circled.

**Latent until `38709d5f`.** `day_preference` was previously set only by the
literal "as soon as possible"/"asap", so `caller_wants_soonest` was almost never
true here. Teaching the capture the words people actually use made the
short-circuit reachable — a fix in one place switching on a dormant branch in
another, which is worth recording as a shape and not just an incident.

Paid by selecting the unheard days directly in that one producer, NOT by
narrowing the capture: the soonest ordering is right on a fresh readout and
wrong only where the question is "what have I not heard". Genuine exhaustion
still declines, so the honest end-of-week sentence is untouched.

**Verification status of the three, as of 9 Sep evening:**

| | live call | tests |
|---|---|---|
| D11 — nothing-sooner concession | ✅ 14:32:58, verbatim | 51 |
| D12 — "what else" after a soonest request | ❌ not yet | 7 |
| D10 — the completeness hedge | ❌ **cannot** be reached by a soonest request — `soonest_first` makes no completeness claim, so the hedge is only live on a readout the caller did NOT ask to be soonest-ordered | 21 |

D10 needs a call that asks **without** urgency ("what have you got?") with four
or more days in the sweep, and the tell is `"I've got a few days —"` in place of
`"Here's what we've got coming up —"`.

**Stage C — one offer record everywhere.** The mode rule was raised here by
stage B and then **removed again** by the owner choosing option B: the ranking
claim no longer needs `single_day`, so nothing is blocked on converging it. The
divergence stays documented, contained, and out of scope until something else
needs it. With Stage A in, `build_slot_offer`
and `apply_offer_to_session` run on Theorem, so the reverse-parse and its repair
layer go dead there. Gate: no `could not resolve spoken option(s)` on any clinic.

**Stage D — extract the provider interface.** Only now is `fetch_free_slots` a
small function. Four acquisition strategies, one signature — see §7 decision 5
for why they are all legitimate.

**Stage E — clinic policy above the seam, values per clinic.** Owner decision 3.
Lead-time, working hours and closed dates get ONE mechanism applied at one
place, reading each clinic's own values from config. Explicitly NOT unified
behaviour: clinics keep different hours and different closed dates. Same shape
as D2's caps — shared code path, per-clinic numbers. Last, because it is the
stage that can change what a clinic offers.

## 7. Decisions — ANSWERED 2026-09-09

1. **Ship the fix.** ✅ Go ahead. (Moot as originally posed — the gap it
   addressed did not exist. Stage A is the real first step.)
2. **`lead_in` everywhere?** ✅ **Yes, wanted on every clinic.** Stage B —
   delivered in two halves: `earliest` on single_day (the three shared readers),
   and `soonest_first` on multi_day (all four, Theorem included) after the owner
   chose **option B** over switching the mode rule. See the stage B entry.
3. **The filter block.** ✅ **Stays per clinic** — clinics have different hours
   and different closed dates. Read as: *one mechanism, per-clinic values.* The
   code path is shared; the numbers come from each clinic's own config. Not
   "make every clinic behave identically".
4. **Where does it run?** ✅ Own working branch → `latency-eval` → live branches
   later. No direct work on `production`.
5. **Four acquisition strategies are legitimate and stay.** Recorded because it
   was asked and the answer shapes Stage D:

   | clinic | `booking_system` | how availability is obtained |
   |---|---|---|
   | Northgate, JV | `google_calendar` | generate candidates from working hours, subtract freebusy |
   | Vital Edge | `google_calendar_provisional` + `diary` | working envelope MINUS everything in the diary |
   | Theorem | `acuity` | ask the provider API for free slots |

   Northgate and JV are genuinely identical. Vital Edge differs because the
   practitioner's calendar records work he is DOING rather than slots he is
   offering — the inversion that once offered a caller his flight to Ibiza.
   All four reduce to one `fetch_free_slots() -> [(start, end)]`, which is why
   the seam works.

## 8. What I would not do

- **Not patch Acuity to emit `presented_days`.** It removes the symptom, leaves
  the seam wrong, and makes the next provider harder rather than easier.
- **Not start with the provider interface.** Extracting an interface around
  logic that is about to move is wasted work.
- **Not touch the 279-line filter block early.** It is the highest-risk part and
  the least related to what you actually heard on the call.
- **Not do this without the replay harness.** 136 stored Theorem calls and 119
  Northgate ones are the only test that means anything for slot presentation —
  the 9,400-test suite pins engine logic and almost none of it pins what a
  caller hears.

---

## 9. Honest note on how this was missed

The `NO deterministic offer built — branch=none-matched` line was in the
2026-09-09 10:03 Theorem log and I read it, and reported it as *"pre-existing on
the Acuity path rather than something D2 caused"* — using it to clear my own
change and then filing it. It was in front of me twice before the read-back
misfire made it unignorable.

It also means D2, shipped that morning as a clinic-configurable cap, **does not
reach Theorem at all**. I wrote a test asserting Theorem could not opt in and
read that as a config-loader quirk. It was this, and the test is now evidence
for the case rather than against it.
