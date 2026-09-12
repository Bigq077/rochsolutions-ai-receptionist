# Open defects after the 2026-09-09 convergence session

**Written** 2026-09-09, overnight, working `docs/plan/HANDOVER_CONVERGENCE_2026-09-09.md`.
**Branch** `latency-eval` (the demo line only — `production` untouched).
**Baseline** `f9793204` → 98 failed / 9303 passed. **Now** 97 / 9349+.

> The one entry that left the failing set is D6's own fix. Nothing else moved,
> at any step, and each step was measured against the same `before.txt`.

---

## 1. What closed

| ID | Defect | Commit | How it was verified |
|---|---|---|---|
| **D3** | `lookup_patient` ran as a reschedule on a cancel | `ba37ea29` | regression test + full suite |
| **D4** | The watchdog armed small talk over an outstanding question | `f0d30d86` | regression test + full suite |
| **D5** | The head rotation handed back an opener the model had just used | `9cff40aa` | regression test + full suite |
| **D6** | A regression case pinned to the real clock | `e780d2ff` | the case now passes on any date |
| **D1** | The phonetic spelling of Alcester left the TTS layer | `70860840` | prompt render-diff + 1,527-line corpus replay |
| **D2** | Slot-presentation caps were engine constants | `58d5706c` | regression test + full suite |
| **N2** | `can't` was boosted over `cancel`, so a cancel was misheard | `dfd8f907` | **verified on a live call** |
| **N4** | Two wordings of one wait apology, back to back | `e2e1d859` | regression test + full suite |

### D4's unresolved question, answered

The handover said: *"Something clears or diverges between 03:28:24.46 and
03:28:30.41 and I do not know what."*

Nothing cleared. `on_question_asked` never stored the question in the first
place. `_is_question_worth_storing` ends with `t.endswith("?")`, and
`_V3_PHONE_CONFIRM_Q` ends on the instruction that qualifies its question —
`"...just say 'use this number'."` — so the predicate answered "no question
here" about a question, `_sh_w.last_question` stayed empty, `_outstanding_q_w`
was `""`, and the T-3 branch was reached by construction rather than by
judgement.

Three predicates in `connection.py` had the same shape and all three now read
the whole turn's speech. `_LOC_RUNG2_CONFIRM` is the second instance of the
same string shape and had already been worked around at the arming site with a
`v3_awaiting_use_this_clinic` special case — that special case is deliberately
left in place.

---

## 2. Two corrections to the handover, both measured

### 2.1 D2 is not "slots drip-fed two at a time" any more

The handover ranked D2 as the biggest failure cluster on `booking_error 64` and
the judge's drip-feed complaints. Both halves of that had already been fixed on
this branch before the handover was written:

* `7624b1a7` (1 Sep 10:44) — three days at two times, and single-day at three;
* `fee6e67a` (2 Sep 12:07) — a continuation says *"I also have — Number 1, …"*.

Every judge complaint about drip-feeding predates both. Since the second landed
there is **exactly one** readout complaint in the entire corpus, and it is not
about batch size. Measured 2026-09-09:

| period | readout complaints |
|---|---|
| August | 11 |
| 1–2 Sep (builds at or before `fee6e67a`) | 3 |
| after `fee6e67a` | **1** |

And more is not better: one of the 2 Sep failures is a **nine-slot** readout the
caller hung up on.

**So the caps were made clinic-configurable with the current values as
defaults, and no number was raised.** That is step 2 of the handover's Phase 3,
and steps 3–4 are deliberately not done — the evidence for them is gone.

### 2.2 The obs corpus reproduces in full

Phase 0's gate is closed. 939 calls, the four clinic counts and their date
ranges, 105/134 Theorem transcripts carrying the phonetic form, and the
failure-tag table (`booking_error 64, dead_end 42, loop 34,
caller_frustration 24, wrong_info 10`) all reproduced exactly.

So did §3.3: **104** calls reached the clinic question, and the ladder resolves
on the first answer **91%** of the time (6% rung 2, 3% rung 3) against the
handover's 92/8/3. One warning for whoever re-measures it — a rung-3 matcher
loose enough to catch `press 1` anywhere also catches the GREETING
(*"to speak to Mark directly press 1"*), which moved the split to 84/6/11 on the
first attempt. Anchor it on `press 1 for <town>`.

The conclusion is unchanged: **do not rebuild the ladder.**

---

## 3. Still open

### D8 — a caller who names an exact time is read the day's default times ✅ FIXED 9 Sep

> **Closed on `latency-eval`.** The fix shape below was right and was followed:
> `_pin_requested_time_index`, a sibling of `_pin_accepted_index`, on the same
> displace-don't-add contract, with `choose_presented_indices` left untouched as
> the single owner of "how many, and which".
>
> **The resolver was built against the corpus, not against imagination.**
> `requested_clock_times` was replayed over **2,509 unique stored caller turns**.
> It resolves the 202 that name a time and invents one for **none** of the
> remaining 2,307. Four utterances resolve that `_has_explicit_clock` does not
> gate — all four are correct readings the old detector misses ("at five in the
> evening", "11 in the morning"), so the resolver is strictly better than the
> gate in front of it.
>
> **Three false positives were found by that replay and closed**, and each is a
> class rather than a case:
>
> | corpus utterance | naive reading | why it is not a time |
> |---|---|---|
> | "knee pain for about **3 weeks**" | 03:00 / 15:00 | a DURATION — and it is the *opening reason* on a booking call |
> | "he's **18 17** i mean he's turning 18" | 18:17 | an AGE, on the one clinic with an under-age gate |
> | "monday at **8 am**" | 08:00 **and** 20:00 | the meridiem answered it; the loose arm asked again and invented a real bookable hour |
>
> **Dates are masked before a single digit is read as an hour**, which is the
> whole B-126 lesson one layer down. The corpus is full of callers naming both in
> one breath — "monday the 7th at 10 in the morning", "half past 4 on the 24th",
> "the 10th of august at 5 in the evening" — and every one now resolves to the
> TIME only.
>
> **Two readings decline.** "at 5" is 05:00 or 17:00 and both are returned; if
> the day holds both, neither is pinned. A band word the caller actually said
> ("at 5 in the evening") collapses the pair upstream, so the common case still
> pins.
>
> Published at the **three** `check_availability` entry points, which between
> them cover all four clinics — not at the one that carries the honesty fields,
> which is the D10 trap. A test asserts all three publish it, and that the write
> is unconditional so a time named earlier in the call cannot pin a later
> readout.
>
> Verified: 51 regression tests; the existing suite **provably unchanged** —
> re-run with the new file excluded it reports 94 failed / 9555 passed / 26
> skipped, byte-identical to the baseline. No call yet.

<details><summary>The original entry, kept because its anchors and reasoning were correct</summary>



`theorem_v3`, **7 Sep 2026 21:33**, `CA7d48a879ed6cb0554a3738dee8941380`,
judge score 3, tag `loop`. Verbatim:

```
caller : wednesday the 9th of september at 12 pm
Susie  : Wednesday 9th September — Number 1, ten in the morning.
         Number 2, eleven in the morning.
         Number 3, three in the afternoon. And I've a few others that day.
caller : no what else have you got that day
Susie  : On Wednesday 9th September I also have — Number 1, midday. …
caller : number 1 midday
```

Midday was bookable the whole time. The caller named it, was read three other
times, had to ask again, and the judge tagged the call `loop`.

**Mechanism.** `_filter_same_day_slots` → the band filter already handles this
correctly: `_has_explicit_clock` skips the coarse morning/afternoon band
precisely so a named time is never filtered out. But surviving the filter is not
the same as being *spoken*. `_cap_presented_slots` then hands the day to
`choose_presented_indices`, whose rule (B-116) is "unheard times first,
chronologically" — which has no notion of a time the caller asked for, so it
picked 10:00, 11:00, 15:00 and dropped the 12:00.

**Anchors**

* `app/tools/receptionist_tools.py:176` — `_EXPLICIT_CLOCK_RE`, already
  detects a named clock time
* `app/tools/receptionist_tools.py:190` — `_has_explicit_clock`
* `app/tools/slot_followup.py` — `_pin_accepted_index`, **the template**: a
  wrapper that forces one specific ISO back into a readout B-116 dropped,
  displacing the last chosen rather than adding to them
* `app/tools/slot_followup.py` — `choose_presented_indices`, the one owner of
  "how many, and which", four readers, **not to be widened in place**

**Fix shape.** A sibling of `_pin_accepted_index` — `_pin_requested_time_index`
— pinning the slot that matches a clock time the caller named, on the same
displace-not-add contract. The detector exists; what does not exist is a
resolver from `"wednesday the 9th of september at 12 pm"` to `12:00`, and the
requested time is not currently carried to the readout layer. Carry it on the
payload the way `band_label` and `sparse_rota_note` already are — *"hand the
decision up rather than letting the readout re-derive it"*.

**Why it is not done here.** It needs a new clock-time parser on the live
booking readout for all four clinics, and this repo's date parsing has already
produced `"September 19th" → 19 August`. Writing one overnight with no call to
verify it is the wrong trade. It is small, well-anchored and evidence-backed —
it should be the next thing done, with a phone call behind it.

</details>

### D9 — CLOSED, and it was two defects wearing one coat ⚪ corrected 9 Sep

**The headline was wrong. The exhibit disproves it, and the exhibit is quoted
in full below because the first version of this entry stopped reading it too
early.**

Demo line, 9 Sep 10:32, `CA0b217e710b9a3957a384186794af4149`, build
`c21db7d589a6`. Booking call, judge score 3, tag `caller_frustration`.

#### What D9 claimed

That offering two times per day at the extreme ends — 08:00 and 17:10 out of
twelve bookable slots — leaves the caller unable to reach anything between, and
that `_pick_times_for_day` at `limit == 2` is the bug.

#### What actually happens, from the stored transcript

```
Susie : Here's what we've got coming up — Number 1, Monday 14th — eight, or ten past five.
        Number 2, Tuesday 15th — eight, or ten past five.
        Number 3, Wednesday 16th — eight, or ten past five. Any of those work?
caller: oh yeah tuesday works
Susie : Tuesday it is — Number 1, eight. Number 2, one. Number 3, ten past five.
        And I've a few others that day. Any of those work?
caller: oh yeah 10 past 5 works
Susie : all booked — you're in for Tuesday the 15th at ten past five in the evening.
```

**The week is a menu of DAYS, not of times.** Two per day is a sample that lets
the caller pick a day; picking one opens it to `times_single_day` (three) plus
the `more_times` tail that says out loud there are others. The caller booked.

This is the owner's stated intent, confirmed 9 Sep: *"presents week with two
slots, then if you ask for a day it'll say let me look at Monday, I have 1/2/3
and a few others that day."*

It is not northgate-only. `CA1736b441372dad9949bc289665e63b1c` (theorem_v3,
8 Sep, **before** Stage A) does the same thing — *"Number 1, nine in the
morning. Number 2, eleven in the morning. Number 3, four in the afternoon. And
I've a few others that day."* The drill-down lives in `slot_followup`, not in
the availability executor, so it has never depended on which reader ran.

**Do not change `_spread` or `_pick_times_for_day`.** Both were about to be
edited on the strength of this entry, on all four clinics, to fix a caller
experience that already works.

#### The real defect in the same exhibit — and it is a config flag

The judge's words were *"garbled and ambiguous"*, and the quote it gave was
`"Number 1, eight. Number 2, one. Number 3, ten past five."` That is not
selection. That is **LAT-1**: `operational.speak_part_of_day: false`, set on
northgate only in `10dad81d` (7 Sep), which strips *"in the morning"* from every
label. "eight" could be either end of the day and "one" is barely a time at all.
Theorem, on the same code, says *"nine in the morning"* — because it never
opted out.

LAT-1 predicted this and said it could not be measured:

> *"What is lost is the caller's CONFIRMATION that eight means the morning, and
> no corpus can price that — which is why this is a clinic's decision behind a
> flag rather than an engine default."*

The judge priced it. **Reversed 9 Sep**: `speak_part_of_day` is back to `true`
on northgate, the note in `clinic.json` records why, and the flag itself is
untouched and still tested from a stub so it can be turned on again as one key.

Honest scope: **n=1 of 13** offer-carrying northgate calls since `10dad81d`. A
signal, not a verdict. It is reversed anyway because the demo line is the most
expensive place in the estate to run a wording experiment, and the latency it
bought (4.7s of a 17.3s read-out) is better taken out of the read-out's length
than out of the words that disambiguate it.

#### What survives as an open question, downgraded

The three days did read out an **identical pair** — same positions on every day,
because those days share a rota template. Varying the second time across days
(8am Monday, 1pm Tuesday, 5pm Wednesday) would still be nicer from the same
two-per-day budget. But it is cosmetic, not a completeness failure, and it is
not worth an engine change across four clinics. Filed, not scheduled.

**Lesson, recorded because it is the third time**: this entry read one turn of a
transcript and diagnosed the mechanism behind it. The next turn contained the
answer. Read the call to the end — and to the booking — before naming a defect.

### D10 — the multi-day opener's completeness hedge is dead ✅ FIXED + LIVE 9 Sep

> **Closed `909a90ad`, verified live 14:52:25 on `1489a02c4331`, on all three
> patient lines since `19fc6cac`.** The fix is NOT the `more_times` candidate
> proposed below — that name is already owned. `days_not_shown` exists, means
> exactly this, and is written by `_check_availability_acuity` ALONE;
> `_cap_presented_slots` is forbidden to touch it and a regression test enforces
> that. Shipped as `days_were_held_back()` in `slot_offer.py`, which prefers that
> field and falls back to found-versus-spoken for the three readers that emit no
> honesty fields at all — northgate, JV and Vital Edge, i.e. where it was
> observed. See ONE_PRESENTATION_LAYER.md. Two siblings shipped with it: **D11**
> (a push-back on the earliest slot is answered with why, not the same list) and
> **D12** (a soonest request no longer disables "what else have you got").

`build_slot_offer` picks between two multi-day openers:

```python
_lead = ("I've got a few days —" if _more_days
         else "Here's what we've got coming up —")
```

and `_more_days` is `len(days) > len(days[:max_days])`. But `llm_stream` hands
it `result["presented_days"]` — a list `_cap_presented_slots` has ALREADY capped
to three. So `more` is always `False` on the live path and the confident opener
always fires, including when four or more days were found and held back.

Its own comment states the rule it is no longer keeping:

> *only claim to be showing everything when everything is what is being shown.*
> *"Here's what we've got coming up" reads as the diary's upcoming days; with a*
> *fourth day sitting unnamed behind it, that is not what it is.*

Confirmed live: northgate 9 Sep 12:20 returned **7** available days, presented
3, and opened *"Here's what we've got coming up"*. The hedge has probably never
fired from this path.

**Not the same defect as D9.** D9 was about which TIMES are spoken and closed as
not-a-defect. This is a completeness claim about DAYS, and it is the B-99 / P10
concern the comment was written for, arriving through the caller rather than the
callee.

**Anchors**: `app/tools/slot_offer.py` `_more_days` (assigned from `more` just
below `spoken_days = days[:max_days]`); the call site in
`app/media_streams/llm_stream.py`'s multi_day branch, which passes
`result["presented_days"]`.

**Superseded — see the box above.** The proposal below was to decide the hedge from something that still knows
what was held back — `session["_slot_more_times"]` is already carried into this
function as `more_times` and is the obvious candidate — but it changes a
caller-facing sentence on all four clinics, so it wants an owner decision and a
call, exactly like stage B's opener did. Note that stage B's `soonest_first`
opener makes NO completeness claim, so it is unaffected either way.

### N5 — the stall itself, and the two-rung filler ladder 🟠 needs an owner decision

Five samples now, across both lines and both builds. `llm_ttft` on a turn that
performs a WRITE spans the tool round-trip, so those turns are structurally
slower — last turn of a call that cancelled: n=7, p50 2.2s, **3 over 10s**;
booking completions p50 5.3s; every other turn p50 1.6s, 1% over 10s.

The recovery, not the latency, is the fixable part. The ladder has two rungs and
stops: on the demo line at 09:29 the caller heard nothing for 14s, and at 10:02
nothing for 13s after the second phrase. N4 closes the case where the second
rung says the same thing twice, but it does not add a third rung, and
`UNKNOWN_SLOW` has no third wording to give it. **That is caller-facing copy —
an owner decision.** "Bear with me" is not available: Gate 5 strips it as a
banned phrase.

### D7 — demo-service Sheets is broken ⚫ CLOSED 2026-09-12, WON'T FIX

Unchanged from the handover. `GOOGLE_SERVICE_ACCOUNT_JSON` is malformed on
`low-latency-joint-venture`. Re-paste it in Render; no code change, and nothing
here can do it.

> **Closed 2026-09-12 as WON'T FIX, with the scope measured.** Still reproducing
> — demo call `CA1ef288f1` at 13:29:59 logged
> `GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON: JSONDecodeError('Invalid
> \escape: line 5 column 46')`. It was being carried as open on three separate
> lists.
>
> **Blast radius, verified by grep:** that variable is read in exactly two
> places — `app/tools/handoff.py:80` and `app/integrations/sheets.py`. Both are
> Sheets. **Google Calendar does not touch it**: `calendar_google.py` builds its
> client from OAuth `stored_tokens` (`creds_from_stored` →
> `get_calendar_service`, :230). Confirmed live the same day — the demo and
> Vital Edge availability lookups both succeeded on gcal while this warning was
> firing.
>
> Sheets is superseded by OBS by owner ruling, so the only consequence is one
> WARNING per call. **Do not re-open this and do not propose fixing Sheets.**
> (`DOC_AUDIT_2026-09-12_EVENING.md` §C6.)

### The engine's location constants are still written phonetically

Deliberate, and now harmless to the record: `_LOC_RUNG1_OPEN`,
`_LOC_RUNG2_CONFIRM` and `_LOC_RUNG3_DTMF` keep `"Awlstuh"` because the DTMF
handler and the clinic binding key off that wording, and D1b canonicalises the
obs transcript instead. Rewriting the constants would mean changing strings
several guards match on, for no gain the inverse does not already give.

`_CLINIC_Q_SIGNALS` keeps **both** spellings for one release, per the
handover's own instruction.

---

## 3b. D1's Theorem gate — CLOSED by observation, 9 Sep

The handover asked for a Theorem call confirming Alcester is still pronounced
correctly after the prompt stopped teaching the phonetic spelling. Two owner
calls to `+447380841468` on 9 Sep supplied it without needing a new one, because
**they ran the OLD build** (`f97932045fe7`) and still demonstrate the mechanism
end to end:

    obs transcript (pre-substitution) : [assistant] Alcester.
    synthesise_chunk (post-)          : text='Awlstuh.'

`connection.py` records `_obs_chunk_text` BEFORE `_apply_tts_subs`, so the model
wrote the canonical spelling and the caller heard the phonetic one. Across the
corpus that is **90 of 136** Theorem calls, on every build back to August.

So D1 does not introduce the substitution path — it makes it the ONLY path
instead of one of two. The remaining risk is the 43 prompt lines themselves,
and those were render-diffed line by line with the other four clinics
byte-identical.

## 4. Gates this session could not close

All three need a phone, and one needs the Render dashboard:

1. ~~**Phase 1** — one demo-line cancel call.~~ **CLOSED 9 Sep 09:28 and
   10:01.** `purpose="cancel"` on every lookup, no T-3 nudge over an
   outstanding question, and N2 verified: "cancel it altogether" transcribed
   correctly with no `BLOCKED` line, judge 4 against 3.
2. ~~**Phase 2** — one Theorem call.~~ **CLOSED by observation** — see §3b.
3. ~~**D7** — re-paste the service-account JSON in Render.~~ **CLOSED 12 Sep as
   WON'T FIX** — Sheets-only, and Sheets is superseded by OBS. See §3's D7 entry
   for the measured blast radius.

> ⚠️ **SUPERSEDED 2026-09-09 evening. This section said "nothing has been
> promoted" and named `f9793204` as the revert target. Both are now wrong, and
> a stale revert target is worse than none.**
>
> `production` was promoted twice on the owner's green light and is now at
> **`8e838f0f`**, identical to `latency-eval`:
>
> | step | production | revert to |
> |---|---|---|
> | promotion 1 | `5f1003c9` → `c5d24da6` | `5f1003c9` |
> | promotion 2 | `c5d24da6` → `8e838f0f` | `c5d24da6` |
>
> `5f1003c9` undoes the whole day; `c5d24da6` undoes only the clinical fix.
> The three live clinics now carry D8, the named-day guard, the nearest-time
> matcher, the Monday fix and the WHOSE SYMPTOM IS IT rule.
>
> Verified on the demo line at 23:08 on build `8e838f0f`: the clinical
> acknowledgement is general ("ankles can be tricky to get fully right — worth
> having Priya take a proper look"), a named weekday returns that weekday, and
> ten past twelve is offered to a caller who asked for twelve.

---

## 5. Phase 4 and Phase 5 — not started, and why

Phase 4 (converge Theorem onto `template_v1`) is explicitly gated in the
handover on "Phases 0–3 shipped **and stable**". Stable means live calls, which
have not happened. It is also the largest change in the document and its own
first step — extracting Theorem's facts into `clinic.json` — runs straight into
four hazards already on record (hours have four sources, the age policy had six,
`theorem_v3` does not even read `clinic.json`, and the Acuity short-circuit
means a config change may never run). Starting it on top of six unverified
commits would make a rollback ambiguous.

Phase 5 (close the coverage gap) is the handover's own "single highest-value
structural change", and it needs a decision this session cannot make: either a
safe two-site test tenant on `latency-eval` or a `locations = 2` fixture clinic
on the demo service. Both are provisioning, not code.

One thing D2's work makes cheaper: `operational.slot_presentation` is the first
piece of "how this clinic speaks" to move out of engine code and into
`clinic.json`, which is the shape Phase 4 needs for everything else.
