# Slot presentation: stop circling, then finish the migration

**Status 2026-09-03, 02:10.** Phase 0 items 1 and 2 are **DONE** — `f93a4d2a`
and `021c0fc0` on `latency-eval`, both red-then-green proven, neither promoted.
Item 3 (F1) turned out to be **already implemented**. Item 3b is the real gap
and replaces it. Item 4 and all of Phase 1 are open.

**Verified live on `2a8a6ee6`, 01:58:32:** `'yeah monday at 8 am works'` ->
`caller ACCEPTED 2026-09-07T08:00:00+01:00` -> `situational head (slot_picked)`
-> `"So that's Monday the 7th of September at eight in the morning"`. The
meridiem guard correctly ALLOWS an agreeing `am`.

**Still unverified live: the DECLINING case.** No call has yet said `8 pm`
against an offer holding only `08:00`. That is the half that is wrong on
patient lines today, and it is the one call that matters most.

Supersedes nothing. Sits alongside `DETERMINISTIC_SLOT_PRESENTATION.md`
(31 Aug), which it finishes rather than replaces, and takes its live defect
rows from `OPEN_DEFECTS_2026-09-03.md`.

---

## 1. Context

Three clinics are live on `production`. Slot presentation is the part of the
call that decides whether a booking happens, and it is the part that keeps
being re-opened — roughly 130 numbered defects, most of them here.

**Are we going backwards? No — but we are not converging, which is worse than
it sounds.** Judged calls that reached a numbered readout, from the obs corpus:

| week beginning | calls | booked | abandoned | mean judge score |
|---|---|---|---|---|
| 3 Aug | 16 | 12% | 50% | 3.19 |
| 10 Aug | 31 | 29% | 58% | 2.58 |
| 17 Aug | 195 | 21% | 70% | 1.97 |
| **24 Aug** (the B-102/103 week) | 69 | **12%** | **86%** | 2.43 |
| 31 Aug | 22 | 18% | 77% | 2.55 |

The week remembered as "quite good" is the **worst** in the table on both
booking rate and abandonment.

> ⚠️ **Do not use this table as a baseline.** These are mostly our own
> adversarial test calls, so the absolute numbers are not clinic performance,
> and `n` runs from 16 to 195 across weeks with different call mixes. It
> supports "no trend" directionally. It does not support any specific
> comparison between two weeks.

### The actual diagnosis

**Seven things decide what Susie says about slots, and six records claim to say
what she said.**

Producers: `build_slot_offer` (primary readout, deterministic, 31 Aug) · the
model's own presentation plus the regex repair layer in `_flush_slot_buf`
sections 2–6 ([llm_stream.py:3470](app/media_streams/llm_stream.py:3470)) ·
`choose_presented_indices`/`_cap_presented_slots` (how many, primary) ·
`all_remaining_on_next_day`/`next_slot_batch` (how many, follow-up — **a
different answer**) · `remaining_unspoken_on_current_day` (which day,
follow-up) · Gate 5a-d/e/f in `turn_handler` (post-hoc corrections to whatever
came out) · `hold_speech.classify_intent` (what she says *before* any of it).

Records: `last_offered_slots` · `slot_labels` · `v3_dtmf_slot_map` ·
`slot_starts_spoken` (+`_fp`, `_loc`) · `v3_last_offered_day_iso` ·
`_slot_presented_day`, plus `_slot_more_times`, `_slot_other_dates`,
`_slot_presentation_mode`, `LOSSY_SPOKEN_DAYS_KEY`.

Nothing enforces agreement between them. Every guard we add repairs one
disagreement, and rules interact.

**The 31 Aug determinism work was right and is half-applied.** Verified:
`build_slot_offer` is called at exactly three sites — `llm_stream.py:6616`,
`:6671`, and `slot_followup.py:3342`/`:3423`. It owns the primary readout. The
follow-up path, the model-fallback path and the repair layer still run the old
rules. That seam is the defect class.

**And we validate with n=1.** We ship, make one phone call, and that call finds
a different defect. There are 807 stored calls used only forensically.

> The two defects fixed below were both found by a phone call, not by the
> suite, and both were guards whose safety argument rested on a premise that
> was **false**. That is the third and fourth instance in a week (`dc58d3b5`
> was the second). A comment asserting why a guard is safe is not evidence
> that it is.

---

## 2. Phase 0 — the four small ones

**Ordering matters and is not arbitrary.** Item 3 feeds
`slot_accepted_by_caller`'s output into what Susie says out loud. That function
was returning wrong slots, so it had to be corrected *first* or item 3 would
have propagated the error into speech, where no read-back guard covers it.

### ✅ 1. DONE — `f93a4d2a` — a named time is never swapped for another

`slot_accepted_by_caller` pins the accepted slot, and the read-back is
generated **from** the pin — so a wrong pin sounds correct every time it is
read aloud, all the way to the diary. Same family as the 90-minute booking
written as 60. Two live calls, both abandoned:

```
00:46:26  "uh yeah monday at 8 pm works"              -> pinned 08:00
01:29:14  "yeah monday the 7th at 10 in the morning"  -> pinned 08:00
```

Two branches, one root: a clock time the caller stated is discarded and
something else matched. The bare-label branch folds "eight in the morning" to
the digit `8`, which "8 pm" contains. The band fallback chose the only
*morning* slot for a caller who asked for ten.

Both exits now ask `_time_contradicts` (band **and** meridiem — "pm" is not a
band word, which is why the existing band check could not see the 8pm case),
and the band fallback declines when the caller named a clock time of their own.
`_clock_time_named` masks "one" in pronoun positions first, or "the morning
one" folds to "morning 1" and reads as a stated time.

### ✅ 2. DONE — `021c0fc0` — a short day-pick gets its head

`74ad7c73` fired on `'yeah monday the 7th at 10 in the morning'` and **not** on
`'uh yeah monday works'`. The discriminator was word count: the `<= 4`
bare-answer early return in `classify_intent`
([hold_speech.py:640](app/hold_speech.py:640)) fired before the `SLOT_PICKED`
arm could be reached, on a comment claiming "a bare answer names no day". It
can. Short picks are the ordinary case.

The exemption requires both of `SLOT_PICKED`'s own conditions — the engine's
B-90 selection verdict and a named day — so nothing new reaches the rules
below it. Band-only and positional picks keep their silence; the 30 Aug
decision in `test_choosing_a_slot_still_gets_silence` is untouched and now
asserted from the new test rather than left to inspection.

### ✅ 3. ALREADY DONE — F1 is implemented, and this plan was wrong to list it

`slot_accepted_by_caller`'s result is **already** fed into `slot_selection`, at
[llm_stream.py:4757](app/media_streams/llm_stream.py:4757):

```python
if not _hs_picking:
    from app.tools.slot_followup import ACCEPTED_SLOT_KEY
    _hs_picking = bool(session.get(ACCEPTED_SLOT_KEY))
```

Verified live 2026-09-03 01:58:32 on build `2a8a6ee6`: `'yeah monday at 8 am
works'` set `ACCEPTED_SLOT_KEY` and the head fired — `situational head
(slot_picked): 'Monday it is -'` — followed by a correct read-back.

**Same lesson as §1, aimed at ourselves: check the code before writing a task
for it.** This one cost nothing because it was checked before being built.

### 3b. OPEN and REPLACES F1 — a pure DAY-pick produces no pick signal at all

The real gap, and it is the case `"Monday it is -"` exists for.

`'yeah monday works'` (01:56:49, build `2a8a6ee6`) got **no head**:
`LAT turn_seq=3 ttfa_ms=2097 content_ttfa_ms=2097` — equal, so nothing spoke.
Both inputs to `_hs_picking` decline, each correctly:

* `utterance_is_slot_selection` is containment against the spoken labels, and a
  bare weekday matches none of them;
* `slot_accepted_by_caller` sees **two** heard times on Monday (08:00, 17:10)
  and the caller named neither, so it declines — which is exactly the contract.

A caller who has chosen a DAY but not yet a TIME has picked something real, and
nothing in the system says so.

> ⚠️ **Note what armed the head before.** On 2026-09-02 01:29:14 it fired for
> `'yeah monday the 7th at 10 in the morning'` — because the band fallback
> wrongly pinned 08:00 and that set `ACCEPTED_SLOT_KEY`. **The head was being
> armed by the defect `f93a4d2a` fixed.** Correcting the resolver removed a
> signal that was wrong to exist. No regression — the same utterance shape got
> no head on 09-02 01:26:49 either — but it means item 2's live-fire case is
> narrower than it looked.

**The trap, and why this is not a two-line change.** The obvious rule — "names
exactly one offered day" — also matches `"what about monday"`, which is a
**request**. Making `slot_selection` true for it would suppress the correct
`NAMED_DAY` diary head and put `"Monday it is -"` in front of a lookup that
really is happening. That is the promised-work defect inverted, and this family
has been wrong in that direction three times. `utterance_requests_more_slots`
and `utterance_requests_different_day` already draw the acceptance/request line
inside `slot_accepted_by_caller` and should be reused, not re-derived.

### 4. OPEN — F2: "what else have you got" after a multi-day offer widens to DAYS

Owner decision, 2026-09-02. `remaining_unspoken_on_current_day`
([slot_followup.py:712](app/tools/slot_followup.py:712)) rule 3 scopes an
unnamed "what else" to `last_offered_slots[0]`'s day. `all_remaining_on_next_day`
then applies an owner rule written 24 Aug for *single-day* offers — a caller
told "I've a few others that day" gets ALL of them — so after a multi-day offer
it answers a promise nobody made, on a day nobody named, with nine times in a
20-second breath.

When `_slot_presentation_mode == "multi_day"` and the caller named no day and
no position, scope to the days **not yet offered** and build through
`build_slot_offer` at the same 3 × 2 shape. Rules 1 (named day), 1b (position)
and 2 (conflicting days) unchanged.

F2 is also a down-payment on Phase 2: it routes a second path through the
single producer.

### 5. OPEN — capture the payload into obs *(new, and it gates Phase 1)*

See §3. Do it here, not later, so weeks 2–3 accumulate a replayable corpus
while the migration work happens.

⚠️ [one-missing-obs-column-kills-the-whole-row] — `session.merge` SELECTs every
mapped column, so an unmigrated store silently stops **all** capture. This must
go through the self-healing path at engine build.

---

## 3. Phase 1 — replay before phone

The thing that ends the circling: turn "one phone call found a new defect" into
"800 calls agreed, and here are the 6 that changed".

> 🔴 **The original single-harness design cannot run.** `obs_turns` stores
> **speech text only** — role and fragments, see `app/obs/turns.py`. Our own
> [replay_slot_readouts.py](scripts/replay_slot_readouts.py) says so in its
> first paragraph: *"The obs store keeps transcripts, not availability
> payloads, so a payload-in / sentence-out replay of `build_slot_offer` is not
> possible from it."* The Render log does not rescue it either — the
> `tool result:` line truncates the payload mid-array at ~200 chars.

Against the five intended checks:

| check | inputs | replayable on the 807? |
|---|---|---|
| `classify_intent` | text + prev_assistant + 2 flags | ✅ |
| `slot_accepted_by_caller` | `last_offered_slots`, `available_days`, `slot_starts_spoken` | ❌ |
| `remaining_unspoken_on_current_day` | the session records | ❌ |
| `choose_presented_indices` / `all_remaining_on_next_day` | the availability payload | ❌ |
| `reconcile_readback_time` | the offer record | ❌ |

So Phase 1 splits in three. All three are cheaper than the original.

**1a — utterance replay.** Real caller utterances × the text-only predicates.
Extends `replay_hold_speech.py` (163 lines) and `replay_situational_heads.py`
(260), which already do exactly this.

**1b — payload synthesis.** `build_slot_offer`, `choose_presented_indices` and
`slot_accepted_by_caller` are **pure**. They do not need historical payloads,
they need representative ones — a clinic diary has finite structure, so
generate the payload space (N days × M times, bands, gaps, single-slot days,
bank holidays) and cross it with the utterance shapes the transcripts supply.
This is property-based testing, and it is **stronger** than replay: it covers
shapes the corpus never happened to contain. Tonight's 8pm case is one the
corpus did not contain until it did.

**1c — capture the payload and the `Offer` into obs.** Phase 0 item 5. Makes
every future call fully replayable and closes the gap permanently. Forward-only
— the existing 807 stay text-only.

**Gate: no change to the slot layer ships without a report from 1a and 1b.**

---

## 4. Phase 2 — one owner, one record (weeks 2–3)

Finish the migration. Target state:

* **One producer.** `app/tools/slot_offer.py` builds *every* sentence Susie
  says about slots — primary readout, follow-up, "what else", the more-days
  answer. `_flush_slot_buf` sections 2–6 are **deleted**, not extended; with a
  deterministic producer on every path there is no model text left to repair.
  Sections 1, 2, 7 (inhibit tracking) and 8 (send to TTS) survive.
* **One record.** A single serialisable `Offer` on the session replacing the
  ten keys in §1: slots, spoken order, labels, keypad map, cumulative
  heard-set, mode, day under discussion. Written only by the producer.
* **Fewer guards.** Gate 5a-e (`reconcile_readback_time`) and the B-116
  withholding logic exist because two records could disagree. Several become
  unreachable and should be **removed**, not left as dead rules — each is a
  future false alarm, which is exactly what P7 was.
  `reconcile_readback_time` itself **stays**: the read-back is still
  model-composed.

Incremental, one consumer at a time, each step gated on a clean Phase 1 report
and the existing suite.

⚠️ `tests/auto/scenarios/regressions/` pins presentation text in ~30 files.
**Read them, never bulk-edit — some pin a DEFECT's wording.** Classifying which
is which is a closed ~60-minute task and good work for a tired session.

---

## 5. Phase 3 — verification and promotion (week 4)

A scripted call set against the demo line covering the shapes the corpus says
occur: pick by ordinal, by number, by keypad, by named day, by band word;
"what else" after single-day and after multi-day; "different day"; a genuine
reschedule; a caller who changes their mind mid-readout.

Promotion stays `latency-eval` → demo call → fast-forward `production`.

---

## 6. What this does to the calendar

| | |
|---|---|
| Phase 0 (2 done, 3 open) | this week |
| Phase 1a/1b/1c | this week, alongside |
| Phase 2 | weeks 2–3 |
| Phase 3 | week 4 — **w/c 29 Sep** |

**Hands On Money: propose w/c 6 October — Tue 7 or Thu 9 Oct.** Phase 3 lands
w/c 29 Sep, and demoing the week a migration finishes promoting is the thing
this plan exists to prevent. One clear week of the new engine running on three
clinics before he sees it, and that week is also the only slack if Phase 2 runs
long — a deletion refactor across ~30 pinned test files usually does.

---

## 7. Critical files

* `app/tools/slot_offer.py` — the good pattern; becomes the single producer
* `app/tools/slot_followup.py` — the follow-up path, the records, B-116
* `app/media_streams/llm_stream.py` — `_flush_slot_buf` (`:3470`), the repair
  layer, the `classify_intent` call site (`:4763`)
* `app/media_streams/turn_handler.py` — Gate 5a-d/e/f
* `app/hold_speech.py` — `classify_intent`
* `app/media_streams/connection.py` — the pick site (`:11974`) and session wiring

## 8. Verification

* **Replay report** (Phase 1) on every change — the primary gate.
* `pytest` — the failing SET byte-identical to a **same-day** baseline taken in
  a separate worktree with `.env` copied in. Baseline at `699dfc9f`:
  **98 failed / 7,840 passed**. Diff the set, never the count, and keep digits
  in the filter — `[a-zA-Z_./-]` hides `test_b84_*`.
* Regression test per defect, red-then-green **proven by neutering the fix**,
  not assumed.
* A real demo-line call before any `production` fast-forward, and a real call
  after it.

⚠️ `test_absence_is_not_unavailability` is date-dependent and moved the baseline
24 → 25 at midnight on 2 Sep with **no code change**. Fix its clock pinning
early or it produces phantom regressions all month.

## 9. What is NOT proposed

No change to `flow.py` — it is bypassed on all live deployments. No change to
the model's tool-calling to suppress the re-query; the deterministic producer
makes a re-query harmless, which is the more robust place to solve it.

## 10. Method notes earned this session

**A guard's comment is not evidence.** Four defects this week were guards that
were safe only under a stated premise that was false — *"on a FIRST lookup
`last_offered_slots` is empty"* (it never is), *"a bare answer names no day"*
(it can). When a comment argues why a guard is safe, test the premise.

**`\b` is a backspace in a non-raw string.** Inserting regex source through a
heredoc silently wrote `\x08` into two compiled patterns. Same family as the
awk `\b` note. Write generated code from a real file, never from an escaped
string literal.

**`slot_followup.py` and `receptionist_tools.py` are CRLF.** A scripted edit
matching on `\n` silently matches nothing and reports success — it made a
"neutered" run come back green, which read as the test being weak rather than
the edit having failed. Assert the pattern was found.
