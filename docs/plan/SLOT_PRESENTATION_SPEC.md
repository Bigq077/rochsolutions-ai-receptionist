# Slot presentation — the specification

**Status** AUTHORITATIVE for slot presentation from 2026-09-11. It replaces the
lineage documents listed in §10 and the eight `OPEN_DEFECTS_*` registers as the
source of truth for what Susie should say while a caller is choosing a slot.

**Written** 2026-09-11 (overnight), from
`SLOT_PRESENTATION_ANALYSIS_2026-09-11.md`, the dated owner decisions in the
code, the ~110 numbered defects of 18 Aug – 11 Sep, and the rendered prompts of
all four clinic engines.

**What it is for.** Three weeks and ~180 commits treated slot presentation as a
series of selection and formatting bugs. Each fix was correct for the call that
found it; compositions of correct fixes failed on the next call, because there
was no statement anywhere of what the right answer IS. This document is that
statement. Its decision table is meant to be executed, not read: §8 describes
the offline scorer, and a row that the engine fails is a defect whether or not
anybody has phoned in and found it.

**How to use it.** A proposed change to slot behaviour must name the row or the
invariant it serves. A change that serves none of them is either a new owner
decision — in which case add it to §3 with its date — or it should not ship.

> **Read §4 before §5.** The precedence order is the part that was never written
> down, and every row in the table is an application of it. Reading the rows
> without it invites the same thing that happened in the code: each rule
> inferring its own priority.

---

## 1. Where this fits

```
caller utterance
  │
  ├─ §2.1  ACT        one classification per turn, with its constraints
  │
  ├─ §2.2  LEDGER     one record, one reducer, once per turn
  │
  ├─ §4    PRECEDENCE which rule wins when two apply
  │
  ├─ §5    TABLE      act × ledger → what Susie says and what she records
  │
  ├─ §7    PROMPT     what the model may and may not say
  │
  └─ §6    INVARIANTS what must be true of the sentence, whoever wrote it
                      (invariant 1 is enforced in code: app/tools/slot_fact_guard.py)
```

Today the engine has no ACT and no single LEDGER: 17 predicates each answer a
fragment of the act question and at least ten session keys each claim to say
what is on the table. §5 is therefore written as a specification of BEHAVIOUR,
not of the current structure — it can be scored against the engine as it stands
(§8), and it is also the gate for the migration that introduces the act and the
ledger for real.

---

## 2. Glossary

### 2.1 Caller acts

A slot sub-dialogue has a small closed set of acts. Every caller turn during
selection is exactly one of them, with its constraints extracted once.

| act | constraints | examples |
|---|---|---|
| `ASK_OPTIONS` | `day?`, `date?`, `time?`, `band?`, `sooner?`, `more?` | "what about Monday", "anything around 12", "what else have you got", "what's the soonest", "any evenings" |
| `ACCEPT` | `slot` or `day` | "yeah that one", "number two", "ten past twelve works", "Monday's fine" |
| `REJECT` | `slot` or `day` | "no, not Monday", "neither of those", "that's too early" |
| `REPEAT` | — | "sorry, say that again", "what was the second one" |
| `OTHER` | — | "how much is it", "do you do sports massage", a new symptom |

Three rules about the act, each bought with a defect:

* **One classification per turn.** B-116 could not tell *"what else"* from
  *"what about Monday"* because nothing told it which had been asked; N1's fix
  had to smuggle the act in as a keyword argument from the one caller that
  happened to know it.
* **An act is not a standing preference.** B-90: an afternoon selection was
  banked and filtered the rest of the call. B-138: "stiff every morning" was
  read as a time filter. A constraint belongs to the turn that carried it
  unless the caller restates it (invariants 13, 14).
* **`OTHER` gets no slot facts.** The model handles it, and may not state a
  day, date or time while doing so (§7).

### 2.2 The ledger

Four fields, updated by one reducer per turn. Today these are spread across
`last_offered_slots`, `REQUESTED_TIMES_KEY`, `_slot_presentation_mode`,
`v3_dtmf_slot_map`, `slot_starts_spoken` + its fingerprint,
`_lossy_spoken_days`, `total_days`, `times_not_shown` and more, with different
writers, lifetimes and per-turn wipes. That is the structural cause of S-13, N4,
B-80, P9, P11, B-101 and B-102.

| field | holds | must be true |
|---|---|---|
| `Offer` | the slots on the table: ISO starts, spoken order, keypad map, mode, the day under discussion | equals the last numbered list actually spoken (inv. 10) |
| `Heard` | every ISO start SPOKEN to this caller, in order, per day | contains a slot only if it was spoken, not if it was built (inv. 16) |
| `Asked` | named day(s), named time(s), band, `sooner`, refused day(s)/slot(s) | written on every turn type, including payload-answered ones |
| `Payload` | the last availability result — bookable truth — with `found` distinguishable from `presented` | presented ≠ found always distinguishable (inv. 19) |

### 2.3 Modes

| mode | shape | source |
|---|---|---|
| `multi_day` | up to 3 days, 2 times each, one numbered option per day | owner 1 Sep (times/day), 9 Sep (week = menu of days) |
| `single_day` | 3 numbered times, plus the more-times tail when the day holds more | owner 24 Aug (three times), 9 Sep (named day) |
| `one_slot` | a single time, no numbering | falls out of a day holding one |

---

## 3. Owner decisions

Each with its date and, where recorded, the call that prompted it. These are
inputs to the table, not conclusions from it. **Where a decision has been
superseded the supersession is shown, because a superseded decision left in a
test becomes a defect pin — that is what happened to B1 and to N1.**

| # | date | decision | where it lives |
|---|---|---|---|
| D-a | 2026-06-15 | ASAP shows the ONE soonest day as it is | `receptionist_tools.py:3651` |
| D-b | 2026-08-24 | a single day speaks THREE times — the most a caller holds at once | `_MAX_PRESENTED_TIMES_SINGLE_DAY` |
| D-c | 2026-08-24 | *superseded by D-e* — one time per day on multi_day | `_MAX_PRESENTED_TIMES_MULTI_DAY` history |
| D-d | 2026-08-28 | B-109: the ASAP rule stops short of … (see call site) | `receptionist_tools.py:3882` |
| D-e | 2026-09-01 | multi_day speaks TWO times per day, across up to three days: each day is one numbered option, so the caller holds three choices and not six | `_MAX_PRESENTED_TIMES_MULTI_DAY` |
| D-f | 2026-09-01 | two times in one readout must SPAN the day — fifty minutes apart is not a choice | `_spread`, `slot_followup.py:4154` |
| D-g | 2026-09-02 | after a multi-day readout, "what else have you got" means THREE MORE DAYS, not a second helping of Monday | `choose_presented_days`, `slot_followup.py:4306` |
| D-h | 2026-09-09 | a week is a MENU OF DAYS; a named day is three numbered times plus "and I've a few others that day" | `slot_offer.py`, D9 |
| D-i | 2026-09-09 | keep the part-of-day suffix ("in the morning") — LAT-1 reversed | `speaks_part_of_day`, default true |
| D-j | 2026-09-09 | soonest-first opener, option B: say that the soonest IS the soonest, rather than changing the mode rule | `_set_earliest_lead_in`, `lead_in="soonest_first"` |
| D-k | (standing) | caps are per clinic, in `clinic.json` | D2 |
| D-l | (standing) | never volunteer prices | template prompt |
| D-m | (standing) | a readout claims completeness only when it is true | B-97…B-99, D10, P10 |
| D-n | 2026-09-11 | **the engine is the only author of a slot day, date or time; the model states none** — CONFIRMED by the owner 2026-09-11 after two demo calls | §7, `clinic_template_prompt` Steps 5–7, `slot_fact_guard.py` |
| D-o | 2026-09-11 | `REPEAT` re-speaks the last SPOKEN numbered list verbatim; with no offer on the table it rebuilds from the last spoken record, **never by re-running the selector** (that applies novelty and changes the times — N3) and never by re-query; with no spoken record at all it says it is checking and re-queries | DT-30, DT-31 |
| D-p | 2026-09-11 | an `ACCEPT` that resolves to no offered slot is never confirmed: when exactly ONE offered time is a plausible match (≤10 min, or a known STT confusion — "10 to 12" ↔ 12:10, "eight" ↔ 08:00/20:00) ask a targeted yes/no naming that one time; otherwise re-read the list and ask | DT-21 |
| D-q | 2026-09-11 | *"what about <day>"* means *"tell me about <day>"* whatever the count heard: re-speak the last readout for that day verbatim if there was one; if the only times heard for it came from the week menu, keep them and fill to three (DT-4 as it stands). Novelty is reached by *"what else"*, never inferred from the count | DT-4, DT-4b |
| D-r | 2026-09-12 | **a caller who names a time gets ONE slot** — the nearest bookable on the day they chose, said as the nearest when it is not exact, no numbering, no keypad — **on every path**: the payload producer, the lookup (tool) path, and whether or not the time has already been heard. Heard-ness is novelty (level 4); a named time is relevance (level 3). Distance is unbounded for a round time on a day the caller chose (ties decline; non-round times never drift); on a day that merely leads a multi-day menu, a closer time on another day wins | DT-7, DT-8, `bookable_on_current_day_anchored`, `build_named_time_offer`, `resolve_requested_time(far=)` |
| D-s | 2026-09-12 | **the booking read-back names the slot the engine recorded the caller accepting** — pinned for the CALL with the payload's own spoken label, not recovered from the model's earlier speech; and **an existence question is never an acceptance**, whether it names a band or a time | DT-18, `ACCEPTED_SLOT_RECORD_KEY`, `readback_slot_phrase`, `_ASKS_IF_A_TIME_EXISTS_RE` |
| D-t | 2026-09-12 | **a timing preference heard in an EARLIER turn is echoed and confirmed before any lookup; the `date_hint` is the caller's answer to the echo, in their words** — *"You mentioned Tuesdays and Thursdays after four — shall I look at those, or is there another time that suits?"* Timing given in the SAME BREATH as the booking request, or urgency wherever it was said, still skips the question (CA3e342642 stays fixed). CONFIRMED by the owner 2026-09-12 evening, twice (rule, then the same-breath carve-out) | `clinic_template_prompt.py` BOOKING STEPS 2–3 + TIME PREFERENCE GATE; `test_a_timing_heard_turns_ago_is_echoed_not_reused.py` |

> **D-n was taken by the author overnight and confirmed by the owner the same
> day.** It is not a new policy so much as the removal of a contradiction:
> `SLOT_FORMATTER_SYSTEM_PROMPT` and theorem_v3's prompt already said numbered
> options from `slot_times_spoken` verbatim, and only the `template_v1` prompt
> said otherwise. Its consequence is that every act during selection MUST have
> a producer — the model can no longer paper over a gap. The two gaps found by
> the 2026-09-11 morning calls (CA778651b7, CA34942aee) are DT-30 (`REPEAT`
> served by the model, both calls) and DT-7/8 (*"around 12"* served by the
> model on call 2, its sentence stripped by Gate 5, the caller left with
> *"Does that work?"* about nothing).
>
> **D-o, D-p and D-q were the owner's answers to §9.2, 2026-09-11**, taken on
> the recommendation recorded there. They apply from the commits that name them.

---

## 4. Precedence

**When two rules apply to one turn, the earlier level wins.** This order is the
one a caller would recognise, and its inversion is the single biggest cause of
the last three weeks: today novelty is the default and relevance is a set of
exceptions added one exhibit at a time.

| level | rule | why it is above the next one | exhibits |
|---|---|---|---|
| **1** | **Never speak a slot the diary does not hold.** | A false slot cannot be repaired by anything below it. A caller who books it gets no appointment. | 11 Sep 00:04 (11:40); B-102 |
| **2** | **The slot they accepted.** | Withdrawing an accepted slot loses a booking that was already won. | P6, P6b |
| **3** | **What they asked for** — the named day; the nearest bookable time to a named time; the band; "sooner". | This is the caller's actual goal. A relevant answer ends the call in one turn. | D8, S-13, N1, N2, N4, N6, B-137, B-142, D11, D12 |
| **4** | **Novelty**, for *"what else"* only — never repeat a time heard on that day while unheard ones remain. | Novelty serves relevance ("bring me new times") and must not override it. | B-116, B-119, S-2, T1, T1b |
| **5** | **Spread** — two times should span the day. | A presentation preference, real but the cheapest to give up. | D-f |

Worked example, the owner's own from 11 Sep: *"as close as possible to 12."*
Level 3 says the answer is **12:10, the nearest bookable time**. Level 4 — which
is what the engine actually applied — asked instead "which times has this caller
not heard?" and produced 14:40 and 16:20. *A person would have started at 12.*

---

## 5. The decision table

Each row: the act and the ledger state that select it, what Susie says, and what
the ledger records. `Ph` = payload, `H` = Heard, `A` = Asked, `O` = Offer.

**Confidence column.** `owner` = a dated decision in §3. `defect` = fixed in
response to a numbered exhibit, i.e. the behaviour is already agreed.
`judgement` = **my engineering call tonight, needing your yes/no** — these are
listed again in §9.2 so they are not buried.

### 5.1 `ASK_OPTIONS`

| # | ledger state | Susie says | records | conf. | exhibit |
|---|---|---|---|---|---|
| DT-1 | first ask, no constraint | `multi_day`: up to 3 days, 2 times each, numbered | O, H | owner D-e/D-h | — |
| DT-2 | first ask, `sooner` | the soonest day, and SAYS it is the soonest | O, H, A.sooner | owner D-j | CA1c6c836 |
| DT-3 | `day` named, day is in Ph | that day, 3 numbered times, + more-times tail if it holds more | O, H, A.day | owner D-h | N6 |
| DT-4 | `day` named, day already offered | **the same times offered for that day, kept** — novelty must not withdraw them; once a full readout has been heard for that day, that readout verbatim (day-scoped `REPEAT`) | O (unchanged times), H | owner D-q — **built** `speak_one_day_from_payload` → `_day_readout_to_repeat`, per-day record `LAST_READOUT_BY_DAY_KEY` | N1 (6 of 6); DT-4b scorer 4 pass / 0 FAIL |
| DT-5 | `day` named, day not in Ph, day is closed | "we're not open on <day>" — CLOSED, not "fully booked" | A.day | defect | northgate CAf4e4a3a6 |
| DT-6 | `day` named, day open but empty | "<day> is fully booked" + alternatives, alternatives presented per DT-1 | O, H, A.day | defect | requested_day_empty |
| DT-7 | `time` named, Ph holds it on the day under discussion — **heard or not** | **that time, alone**: "Yes — ten past twelve on Monday is free. Shall I book that in for you?" The offer becomes that slot; the keypad map is superseded | O (one slot), H, A.time | owner D-r | D8, S-13, N2; Theorem CAf87ed571 12 Sep 12:11 |
| DT-8 | `time` named, Ph does not hold it | the NEAREST bookable time on that day, **alone**, and says so ("the nearest I've got to midday is ten past twelve") — whatever the distance for a round time on a day the caller chose; on the tool path too, never a three-slot readout with it pinned in | O (one slot), H, A.time | owner D-r | D8, N2; demo CA778651b7 11 Sep 08:44 |
| DT-9 | `time` named, tie for nearest | decline to pick: read both | O, H | defect | `nearest_time_index` |
| DT-10 | `band` named, band has times | times in band only | O, H, A.band | defect — **built** 11 Sep: `speak_one_day_from_payload` narrows to the band; the more-slots branch filters its unspoken pool to it | B-90; CAf80eb02d; scorer DT-10 2 pass / 0 FAIL |
| DT-11 | `band` named, band exhausted on that day | say the band is spent, then open the day | O, H | defect — **built** 11 Sep: empty band → "I've nothing in the <band> on <day>"; all heard → B-117's sentence; either way the REST of the day (heard band times are not re-admitted). The preface is spoken, not recorded | B-98; scorer DT-11 4 pass / 0 FAIL |
| DT-12 | `more` after a single-day readout, unheard times remain **that day** | the next unheard times on that day, numbered, no padding with heard ones | O, H | defect | B-116, B-119 |
| DT-13 | `more` after a multi-day readout | **three more DAYS**, not more times on the same day | O, H | owner D-g | demo 2 Sep 09:15 |
| DT-14 | `more` + `day` ("what else on Monday") | more times **on Monday** | O, H, A.day | defect | N6 — closed 11 Sep; CAf80eb02d |
| DT-15 | `more`, nothing unheard remains on the day | say so truthfully, then offer other days | O, H | defect | D10, P10 |
| DT-16 | `more`, nothing unheard remains anywhere in Ph | say so truthfully — once — and offer to look further out | — | defect | B-97…B-99 |
| DT-17 | `sooner`, nothing is earlier than what they have heard | say that nothing is sooner, naming what the earliest is | — | defect | B-137, B-142, D11, D12 |

### 5.2 `ACCEPT`

| # | ledger state | Susie says | records | conf. | exhibit |
|---|---|---|---|---|---|
| DT-18 | `slot` resolves in O | confirm THAT slot — day, date, time — from the ENGINE's durable record of the acceptance, never from the model's memory of the call; then ask the next question | O.accepted (for the call) | owner D-s | Step 7; Vital Edge CAea24df48 12 Sep 12:52 |
| DT-18c | **precondition on every `ACCEPT` row** — the utterance does not affirm, name a time, a day or a list position | it is **not an acceptance**, whatever is on the table — even a ONE-slot offer, where nothing is left ambiguous. A check-in, a name, a phone number or a fragment of the reason resolves nothing; the turn falls to its own handler | — | proposed 12 Sep (defect A); owner to confirm | northgate CA5c69c585 12 Sep 17:36 — *"hello you still there"* → `caller ACCEPTED 16:20` → read back as the booking; `utterance_can_accept_a_slot` |
| DT-19 | `slot` named by ordinal ("number two") | the slot at that position in the LAST spoken numbered list | O.accepted | defect | P12, B-80, P9, P11 |
| DT-20 | `slot` named by time, ambiguous 12-hour ("8") | resolve against the day's real times; if both are real, ask which | — | defect | f93a4d2a 3 Sep |
| DT-21 | `slot` named by time, matches NO offered time | **do not guess** — if exactly one offered time is a plausible match, ask *"did you mean <that time>?"*; otherwise re-read the list and ask | — | owner D-p | 11 Sep 00:04 ("10 to 12" → 11:40) |
| DT-22 | `day` accepted, that day has an offer on the table | keep it and present that day per DT-3 | O, H, A.day | defect | B-145 |
| DT-23 | `day` accepted, no offer on the table for it | look it up, then DT-3 | O, H | defect | B-145 |
| DT-24 | a later readout would drop the accepted slot | **it is pinned back in** | O | defect | P6b |

### 5.3 `REJECT`

| # | ledger state | Susie says | records | conf. | exhibit |
|---|---|---|---|---|---|
| DT-25 | `slot` refused | next unheard times, never the refused one | A.refused | defect | B-119 |
| DT-26 | `day` refused | **never that day again this call** | A.refused | defect | B-147 |
| DT-27 | everything on the table refused | next two times or the next week, by absolute date | O, H | defect | POST-REJECTION |
| DT-28 | refusal, and the tool call is refused (cached payload) | answer on the REQUEST's own day and time, not the cache's | O, H | defect | N4, B-118 |
| DT-29 | refusal with no reason given | never ask why | — | owner D-l adj. | POST-REJECTION |

### 5.4 `REPEAT`

| # | ledger state | Susie says | records | conf. | exhibit |
|---|---|---|---|---|---|
| DT-30 | O is on the table | **the same offer, verbatim** — same times, same numbers, same order, spoken by a PRODUCER, not the model | nothing changes | owner D-o | N3 (dropped as a fragment, 19 s); 11 Sep 08:44 and 08:52 (served by the model, 2.0–2.4 s) |
| DT-31 | O is empty | the last SPOKEN list, re-spoken — never the selector re-run, never a re-query; with no spoken record, say so and re-query | O, H | owner D-o | — |

A `REPEAT` must never be treated as `ASK_OPTIONS`: re-deriving the offer applies
novelty (level 4) and silently changes the times, which is how "say that again"
became a new readout.

### 5.5 `OTHER`

| # | ledger state | Susie says | records | conf. | exhibit |
|---|---|---|---|---|---|
| DT-32 | mid-selection | acknowledge briefly, capture it, guide back to the choice — **the offer survives untouched** | note only | defect | MID-SLOT NEW INFORMATION |
| DT-33 | mid-selection, red-flag symptom | the urgent-care net takes priority over booking | screening | owner | standing |
| DT-34 | any `OTHER` | the model answers, and states **no** day, date or slot time | — | owner D-n | §7 |

---

## 6. Invariants

Condensed from ~110 numbered defects to the 20 that are actually distinct. Each
is a property of the OUTPUT, so each is checkable without knowing which code
path produced it — which is the point, since the same act reaches different code
today (analysis §2.5).

| # | invariant | enforced by | status |
|---|---|---|---|
| 1 | No day, date or time is spoken that the payload does not hold. | `app/tools/slot_fact_guard.py`, in `_tts_loop` | **shipped 2026-09-11, mode `log`** |
| 2 | No confirmation names a slot that is not on the table. | DT-21, §7, inv. 1 | prompt only |
| 3 | An accepted slot is never withdrawn by a later readout. | `_pin_accepted_index` | code |
| 4 | A named day is answered with that day; a named time with the nearest bookable time on that day (ties decline; no drift on non-round times). | DT-3/4/7/8/9 | partial — N6 open |
| 5 | "Sooner" is answered with the earliest, and says so when nothing is earlier. | DT-2, DT-17 | code |
| 6 | "What else" never repeats a time heard on that day while unheard ones remain, and never pads with heard ones. | `choose_presented_indices` | code |
| 7 | "What about <day>" keeps the times already offered for that day. | DT-4 | code (N1 fixed 11 Sep) |
| 8 | A refused day or slot is not offered again in the same call. | DT-25/26 | code |
| 9 | Completeness is claimed only when true — of days and of times. | `exhaustion_claim_is_supported` | code |
| 10 | The keypad map always equals the last spoken numbered list. | `_supersede_slot_map` | code |
| 11 | Two times in one readout span the day. | `_spread` | code |
| 12 | A clock time is not repeated across days in one readout when avoidable. | `_prefer_unheard_clock_times` | code |
| 13 | A selection is never banked as a standing preference. | B-90 fix | code |
| 14 | A reason is never banked as a time filter. | B-138 fix | code |
| 15 | Every turn that presents slots records what was presented, with its producer. | `apply_offer_to_session`, `obs/slot_offers` | code; model turns partial |
| 16 | Anything about slots is recorded as heard only if it was SPOKEN. | `mark_offer_spoken` | code (13% gap measured) |
| 17 | A turn whose tool call is refused is answered on the request's own day and time. | DT-28 | code (N4 fixed 11 Sep) |
| 18 | Parsing masks dates before reading hours; durations and ages are not times. | `_mask_dates`, `requested_clock_times` | code |
| 19 | Presented ≠ found is always distinguishable in the record. | `times_not_shown`, `total_days` | code |
| 20 | The same act reaches the same answer whichever path or reader serves it. | — | **NOT enforced; four readers, five refusal branches** |

Invariants 1 and 20 are the two that are about the SYSTEM rather than a rule.
1 is now enforced. 20 is the migration.

---

## 7. What the model may and may not say

One prompt section, replacing the three that disagreed. Since 2026-09-11 the
`template_v1` template carries this; `SLOT_FORMATTER_SYSTEM_PROMPT` and
theorem_v3's section already did.

**May**
* call `check_availability`, with the timing gate observed, a named day as
  `after_date` + `day_window=1`, and a named clock time inside `date_hint`;
* say it is checking;
* read labels out of `slot_times_spoken` **verbatim**, numbered;
* handle an `OTHER` turn and return the caller to the choice;
* name a time **the caller** asked for, in order to say what is available
  instead of it.

**May not**
* convert a 24-hour time into words itself, or re-word a label;
* state a day, date or clock time that is not in the result for the day being
  named;
* confirm a time it did not read out of the data and say to this caller;
* present two or more options as a flat sentence — the numbering is what the
  keypad map is parsed from;
* claim or deny that more times exist — the engine adds that sentence;
* say a day is "fully booked" when the clinic is closed that day.

The three prompts must be **rendered and diffed per clinic** on any change, and
the hash pins in `test_b55_provisional_reschedule_closing` and
`test_b57_theorem_cancel_gate` recomputed — in **both** tables, since jv_v1
appears in each under a different name.

---

## 8. Verification — table-first, phone-last

The loop that produced the last three weeks was: one phone call per fix, on one
clinic whose diary is a uniform 50-minute grid. Each call reaches a different
path, so each call finds a different defect, and the fix is written against that
exhibit. Documented cases where that misled us are in the analysis §1.3 — a step
that passed without its mechanism firing, a step that failed without a defect,
and three separate tests that pinned N1 as correct.

The order is inverted here:

1. **The decision table as generated tests**, over synthetic diaries: uniform
   grid, sparse rota, single-slot day, band-filtered, month-end, closed day,
   day with one time, day with twelve.
   `scripts/score_slot_spec.py` — see §8.1.
2. **Corpus replay** for regression: `calls.slot_offers` (payload + offer per
   lookup, forward-only from 3 Sep), the three replay harnesses, the
   528-diary synthetic sweep.
3. **One call per clinic diary shape** — to CONFIRM rows, not to find them.

Known blind spots, recorded rather than assumed: `replay_slot_decisions` cannot
see selection; `replay_presented_times` cannot see the loop or D8;
`replay_multi_day_spread` cannot see the named-day producers; Stage C
(`record_model_readout`) cannot see no-tool turns and has recorded zero; nothing
replays the refusal path; 13% of recorded offers were never spoken.

### 8.1 The scorer

`scripts/score_slot_spec.py` runs the table's rows against the engine as it
stands and reports pass / fail / unreachable per row.

```bash
python scripts/score_slot_spec.py                 # all rows, all diaries
python scripts/score_slot_spec.py --row DT-14     # one row
python scripts/score_slot_spec.py --md            # markdown, for this file
```

**The rows that fail are the backlog.** The ~110 numbered ids are not: they are
a history of which calls happened to be made. A row failing on a diary shape no
clinic has is a lower priority than one failing on the uniform grid, and the
scorer reports the shape.

### 8.2 First run — 2026-09-11, build `fa4dca45`

```
87 checks: 58 pass, 0 FAIL, 29 unreachable
```

**Zero failures, and that is a weaker result than it looks.** Read it with §8.3.
Every row the scorer can actually reach, the engine satisfies — including on the
five diary shapes no phone call has ever used (sparse rota, single-slot day,
twelve-slot day, band-filtered view, single-band day). That is real: it says the
selection rules are not grid-shaped, which was an open worry.

What it does not say is that slot presentation is correct. **29 of 87 checks are
unreachable**, and the reasons are the architecture, not the harness:

| reason | checks | what it means |
|---|---|---|
| the defect is in the ROUTING, not the producer | DT-14 ×6 | N6 is invisible offline (§8.3) |
| the diary shape cannot exercise the rule | 17 | e.g. a one-slot day cannot be spread |
| the rule declines by documented design | DT-4 ×3 | N1's boundary — see DT-4b |
| divergence, needing an owner decision | DT-4b ×2 | §9.2 |

**Four of the first run's eight "failures" were the scorer's own bugs**, and each
one looked exactly like a finding: a non-round probe time that invariant 18
declines *by design*; a bare string where `nearest_time_index` requires a list
*by contract*; three invented session keys where `choose_presented_days` reads
the spoken record; and DT-4 asserting past the documented boundary of N1's fix.
Recorded here because it is the same failure mode as the last three weeks —
measuring your own setup and calling it the engine's behaviour. **Reproduce any
FAIL by hand before filing it.**

### 8.3 What the scorer cannot see, and why it matters most

It scores **producers**, not the **dispatcher**. Each row calls the engine
function that should serve that act, so it measures what that function does once
reached, and says nothing about *whether it is reached*.

N6 is exactly that gap: *"what else have you got on Monday"* never arrives at
the named-day producer — B-137's "lead with unheard days" takes it and answers
with Thursday. The named-day producer, called directly, behaves perfectly. So
DT-14 reports UNREACHABLE even though the producer passes, because a PASS there
would retire the only thing currently catching N6, which is a phone call.

The router is `handle_transcript`, one 15,734-line method, and nothing can drive
it offline. **Making the dispatcher drivable is the highest-value thing missing
from this harness**, and it is the same statement as invariant 20: the reason the
same act reaches different code is that nothing owns the routing decision. That
is the migration, and this is the argument for it — not elegance, measurability.

> **Amendment, 12 Sep 2026 — the 38 UNREACHABLE rows are NOT this problem.**
> Re-run at `4470555e`: **147 checks, 109 pass, 0 FAIL, 38 unreachable.**
> Grouped by reason, **every one of the 38 is an inapplicable diary shape** —
> "needs 2+ times", "needs 4+ times so a readout can drop one", "the day holds
> one band only". **None says it needs the dispatcher.** DT-14's own
> unreachability was closed when the row was rewired through
> `try_unspoken_followup_speech`, so the N6 example above is historical.
>
> The paragraph above still stands as the argument for the migration — but the
> unreachable **count** is not its evidence, and must not be read as 38
> unverified behaviours. (`DOC_AUDIT_2026-09-12_EVENING.md` §C9; README
> correction 36.)

---

## 9. Open items

### 9.1 Known defects, as rows

| row | defect | state |
|---|---|---|
| DT-14 | **N6** — "what else have you got on Monday" answered with Thursday–Saturday | **closed** (11 Sep, after CAf80eb02d 10:47 anchored it): the more-slots branch counted a day as named only through `day_named_by_caller` (full label); a bare weekday now counts too, via `_payload_day_by_weekday`. Scorer DT-14 driven through the dispatcher — reproduced offline first try, now 1 pass / 0 FAIL |
| DT-3 (band) | 11 Sep 10:48 (CAf80eb02d) — *"what about monday morning"* read as a PICK: `caller ACCEPTED 2026-09-14T08:00 ('what about monday morning')` — `slot_accepted_by_caller` resolved "morning" to the one morning time on the offer; the model then said "I've got eight in the morning — does that work?" (D-n) while Monday held five mornings | **closed** 11 Sep: the band fallback now defers to `_DAY_REQUEST_RE` — a question is not an acceptance; "monday morning works" still picks, "what about monday at eight" still names its slot. The question then needed a producer that honours the band; none did (DT-10/11, built in the same change). Unproved on a call |
| DT-21 | 11 Sep 00:04 — "10 to 12 works" confirmed as "twenty to twelve" (11:40) | **closed** `1bc7fc52` (D-p): `accept_clarify_speech` asks "did you mean ten past twelve?" and narrows the offer to it; scorer row DT-21 4 pass / 0 FAIL. Prompt fixed 11 Sep; guard catches any leak in `enforce` |
| DT-7/8 | 11 Sep 00:04 — "as close as possible to 12" answered with 14:40 and 16:20; 11 Sep 08:51 "around 12" reached no producer | **closed** `d585450a`: the resolver runs on the day under discussion first, the sweep second; scorer row DT-8b |
| DT-30/31 | 11 Sep 08:44, 08:52 — "say that again" served by the model | **closed** `a4a383e8` (D-o): `repeat_speech` above every selector; scorer rows DT-30/31 |
| B-114 (loose) | found 11 Sep building DT-21 — "yeah that one works" resolved to 13:00 through the loose after-marker "works" | **closed** `9a55cdac`: a pointer word before the number ends the question |
| N4 (component) | found 11 Sep by scorer row DT-21 on `uniform50` — "five past eight in the morning works" also read as 08:00 and answered with **Thursday's** eight | **closed** `6944ffd9`: relative-time phrases removed before the bare-hour and label passes |
| parser | Re-run by direct execution 12 Sep at `4470555e`, all four confirmed still live: `"half three"` → `[]` (UK 15:30 — yields no candidate, so the model answers) and `"half two"` → `[]`; `"at ten to twelve"` → `['11:50','23:50','10:00','22:00']`, the extra 10:00 making the resolver decline as a tie; `"from nine to five"` → `['04:51','16:51']` | open, leads — none reproduced on a call |
| parser (new, 12 Sep) | **`requested_clock_times("eight in the morning")` → `[]` — and that is the engine's OWN spoken idiom.** Every readout says "eight in the morning", so a caller who asks for a time in the exact words Susie just used gets no candidate on the **request** path and falls through to the model. The **accept** path is unaffected: `slot_accepted_by_caller` (`slot_followup.py:3301`) resolves against the spoken labels, which is why N4's fix holds. So it bites on *"have you got eight in the morning on Tuesday?"* and never on *"eight in the morning works"* | open; the worst of the parser leads, because the wording is one we teach the caller. Found by execution, not by a call |
| inv. 20 | four availability readers, five refusal branches, two named-day producers; `fetch_free_slots` exists nowhere in the repo (Stage D never started) | open; the migration. **D-r is its first proven caller-facing cost** — see the 2026-09-12 correction-log entry: "the tool path was never aligned to DT-7/8" |
| inv. 16 | 13% of recorded offers were never spoken | open; instrumented by S-7 (`mark_offer_spoken`), **never re-measured since 10 Sep** — needs the obs corpus |
| DT-18c (A) | 12 Sep 17:36 (CA5c69c585, northgate, `35f06eb6`) — *"hello you still there"* after a one-slot offer the fact guard had RETRACTED: `caller ACCEPTED 2026-09-15T16:20 ('hello you still there')`, then read back as the booking. `slot_accepted_by_caller` decides WHICH slot and declines on ambiguity; after a one-slot offer there is none, so its sole-date / single-time shortcuts (3 Sep `1a54dd23`, reachable on every named-time turn since D-r `e161c435`) returned the slot for any utterance that was not a request. | **fix on `latency-eval`, unproved on a call**: `utterance_can_accept_a_slot` gates the resolver (step 0) — affirmative, accept word, position, weekday, clock time or band, else None; `tests/regression/test_a_check_in_after_a_one_slot_offer_is_not_an_acceptance.py` on the call's stored payload. Sibling B (the one-slot producer writes `selected_slot` at BUILD time, before TTS, and a guard retraction does not unwind it) is still open — see `HANDOVER_THREE_CALLS_2026-09-12.md` §2.B. |
| inv. 16 (B) | 12 Sep 17:36 (CA5c69c585) — the one-slot producer `apply_resolved_time_to_session` wrote `selected_slot`, the spoken record and the D-o readout at BUILD time; the fact guard replaced the sentence at TTS and nothing unwound it. Obs: `slot_offers[0].spoken: true` beside a transcript holding only the recovery line; `collected.selected_slot = 16:20` with no yes. | **fix on `latency-eval`, unproved on a call**: the producer no longer writes `selected_slot` (an offer is not a choice; the booking tools and the fast-path pick still set it on a real one); `retract_offer(session, text)` is called from `_tts_loop` on a guard REPLACED verdict and, when the replaced sentence belongs to the current readout, clears the table, un-records the heard starts, drops the D-o readout and the keypad map, and flips the obs row to `spoken: false, retracted: slot_fact_guard`. A replaced MODEL sentence retracts nothing. `tests/regression/test_b_a_retracted_offer_is_not_on_the_table.py`. |
| inv. 1/4 (L) | 12 Sep 20:52 (CAddd98ce0, northgate, `8461c4bd` — the call that PROVED A and B: no `caller ACCEPTED`, `offer RETRACTED`, `selected_slot` null, row `spoken: false`) — and the read-back still said *"Tuesday the 15th of September at twenty past four"*. The model wrote it from `conversation_history`, which held the retracted offer: `_append_history` stores the post-Gate-5 text and the fact guard runs one seam later in `_tts_loop`. The read-back passed the guard because 16:20 IS in the diary (inv. 4, not inv. 1). | **fix on `latency-eval`, unproved on a call**: `slot_fact_guard.note_replacement` records every replacement / dropped tail; `_append_history` stores `rewrite_as_heard(...)`; the REPLACED and tail-drop sites call `apply_replacements_to_history` for an entry already appended (the two race). `tests/regression/test_l_history_holds_what_the_guard_let_the_caller_hear.py`. Same shape as the 1 Aug Gate 5f/history defect (CA7d46c2bc). |
| inv. 1 latch (E) | 12 Sep 17:36 (CA5c69c585, 15 s) and 20:52 (CAddd98ce0, ~30 s) — after a guard retraction `check_outgoing` dropped EVERY later chunk of the turn (`_BLOCKED`): the name question, then the watchdog's re-ask of it, then the safety net's. Two safety mechanisms, each right alone, jointly produced silence. | **fix on `latency-eval`, unproved on a call**: the latch drops only what refers to the retracted offer — a time mention, a weekday, a position, "that one / book that in / those" (`_refers_to_the_offer`); a question naming none of them is spoken. `test_e_a_retracted_turn_still_asks_its_question.py` on the two calls' verbatim chunks; `test_a_spoken_slot_time_exists_in_the_diary.py` re-pinned (it pinned the name question being dropped). |
| D-t (C/D at the root) | 12 Sep 17:36 and 20:52 — both calls: the caller said *"only after 4"* in the SYMPTOM turn; two turns later, on *"yes go for it"*, the model called `check_availability(date_hint="Tuesdays and Thursdays after 4pm")` — its own paraphrase from memory, as the prompt's Step 2 SKIP rule told it to. "after 4pm" parsed to a 16:00 clock time; D-r built a one-slot offer on it; the guard retracted it (16:00 in no payload — right by its rules; the builder and the guard were fed different inputs); A, B, E, L followed. | **owner decision D-t, prompt on `latency-eval`, unproved on a call**: echo-and-confirm for an earlier-turn preference; the hint is the caller's fresh words, so the builder and the guard parse the SAME utterance by construction. The engine-side C (bound vs point in `requested_clock_times`) and D (`note_caller_speech` from the caller's words on the tool path) remain as belt-and-braces candidates, unscheduled — the prompt fix removes the trigger. |

### 9.2 Rows needing your yes/no — CLOSED 2026-09-11

All four were answered by the owner on 2026-09-11, on the recommendations
below, and are now D-n, D-o, D-p and D-q in §3. Kept here so the reasoning is
not lost; the rows in §5 carry `owner` confidence from that date.

| row | decided | the reasoning that carried it |
|---|---|---|
| **D-n** | confirmed | The engine is the only author of slot facts. Its consequence is that every act during selection must have a producer; the two gaps the morning calls found (DT-30, DT-7/8) are the price, and they are findable offline once. |
| **DT-31** → D-o | rebuild from the last SPOKEN list | Re-running the selector applies novelty against `Heard` and changes the times — which is how "say that again" became a new readout (N3). Re-query is dead air plus the five refusal branches. With no spoken record, say so and re-query. |
| **DT-21** → D-p | do not guess; targeted confirm | An `ACCEPT` that does not resolve is the act that makes a wrong booking. When one offered time is a plausible match, "did you mean ten past twelve?" is a two-second turn naming a real time; otherwise re-read. Invariant 2. |
| **DT-4b** → D-q | "what about <day>" is a day-scoped `REPEAT` | Neither "leave it" (zero overlap — N1 one step on) nor "keep one" (a mixed list: "is half past ten gone, then?"). The most recent thing Susie said about that day, verbatim; novelty is reached by "what else", which already works. DT-4 and DT-30 become one rule. |

Implementation order, each one commit with a row-named regression test on
`latency-eval` only: D-n (in), D-o `REPEAT` producer, DT-7/8 time-request route,
D-p targeted confirm, D-q day-scoped repeat.

---

## 10. Retired documents

Superseded by this file. Keep for history; they are no longer authoritative and
should not be cited in a commit message.

* `DETERMINISTIC_SLOT_PRESENTATION.md`
* `SLOT_PRESENTATION_CONVERGENCE.md`
* `SLOT_PRESENTATION_FINISH_2026-09-10.md`
* `ONE_PRESENTATION_LAYER.md` — the presentation half; stages A/B remain a
  record of what shipped
* `SLOT_TIME_CHAIN_2026-09-09.md`
* `OPEN_DEFECTS_*` — all eight registers, 2026-08-22 to 2026-09-06
* the call sheets, 2026-08-09 to 2026-09-06

`SLOT_PRESENTATION_ANALYSIS_2026-09-11.md` is **not** retired: it is the
evidence this specification rests on, and the place to look when a row here
seems arbitrary.

---

## Appendix — the correction log

This document's ancestors were wrong in a specific, repeating way: they stated a
POSTURE as if it were a standing truth when it was really a fact about that
week's code. `CLAUDE.md` §2 records the same failure three times over about the
branch topology.

So, for the record: **if this document and the code disagree, the code wins** —
and the correction belongs here, dated, not in a commit message.

| date | correction |
|---|---|
| 2026-09-12 (eve, D-t) | **The chain had a root, and it was a prompt rule.** After the call on `8461c4bd` the owner ruled that a yes to *"shall I get you booked in?"* must go down the ordinary path — ask when — not straight to a lookup on a preference the model remembered from the symptom turn. Step 2's *"SKIP … if the caller has ALREADY given …"* was written for CA3e342642 (JV Bolton, 24 Jul: re-asking "as soon as possible" cost 33 s), and it was right for that call; it was wrong when the signal was two turns old, because the model's paraphrase became the `date_hint`, and the guard — reading the caller's actual words — could not agree with it. D-t keeps the same-breath/urgency skip and echoes everything older. Five prompt-hash pins re-pinned (jv_v1, vital_edge); theorem/theorem_v3/demo unchanged. |
| 2026-09-12 (eve, E) | **A retracted turn still asks its question.** The guard's docstring says the recovery line hands the turn to the next question; its latch then dropped that question. Narrowed to chunks that refer to the offer. The third silence owner (after the ladder's "we are slow" and the watchdog's "caller quiet") is now named: "we retracted" is the guard's, and it resolves it by speaking the turn's own question rather than by a new mechanism. |
| 2026-09-12 (eve, L) | **History must hold what the guard let the caller hear.** The demo call on `8461c4bd` proved A and B on every engine record and the model still read back 16:20 — from its own history of a sentence the guard had replaced. Every guard that rewrites speech AFTER `_append_history` has this hole; Gate 5f had it on 1 Aug and history was moved to the post-Gate-5 text; the fact guard sits one seam further down. Now the guard records its replacements and both the append and the replacement site apply them. Note for the register: the read-back guard passing a wrong-time confirmation is inv. 2 having no enforcement (the D-s note again) — the engine record was right and nothing made the model use it. |
| 2026-09-12 (eve, B) | **Inv. 16 is "heard", and a retracted offer is nothing.** Same call as DT-18c. Two faults in one producer: (1) `apply_resolved_time_to_session` mirrored the one-slot OFFER into `selected_slot` ("so the LLM / booking path sees it") — but nothing on the v3 path reads that key to decide what to say; its readers are the SMS templates, the call summary, obs `collected` and the admin view, every one a claim that a slot was CHOSEN. Removed. (2) `slot_fact_guard.check_outgoing` replaced the offer sentence and set `_BLOCKED`; the session state the producer had written stayed, so the slot was on the table, recorded as heard and repeatable by D-o — for a sentence the caller never heard. `retract_offer` now unwinds it at the REPLACED site in `_tts_loop`, gated on the sentence belonging to the current readout so the 11 Sep 00:04 shape (a MODEL sentence with a stray time) does not wipe a list the caller genuinely heard. Direction of error chosen: un-recording a heard slot costs one re-ask; leaving an unheard one recorded is the read-back of a time never chosen. Still open from the call: E (the retracted turn asks nothing and the watchdog's re-ask is dropped by the same latch), C/D. |
| 2026-09-12 (eve) | **DT-18c: an utterance with no accepting signal is not an acceptance.** Northgate CA5c69c585 17:36, build `35f06eb6`, the third of the hold-phrase calls. The D-r arm built a one-slot offer on 16:20 from the MODEL's `date_hint` ("after 4pm" → 16:00, the caller's turn was *"uh yes go for it"*); the fact guard replaced the sentence (16:00 was in no payload — right by its rules); the session kept `last_offered_slots`, `selected_slot` and the spoken record anyway; the turn asked nothing and the watchdog's re-ask was dropped as "the tail of a retracted offer"; 15 s later the caller said *"hello you still there"* and the resolver returned the slot — the sole-date step ("the offer names ONE day, so a time alone is unambiguous") and the single-`heard` return both fire on a one-slot offer with nothing about the utterance consulted. B-138 and `_ASKS_IF_A_TIME_EXISTS_RE` had each patched one non-acceptance SHAPE into the resolver; this is the general rule the other way round: the resolver now asks *is this a yes at all* before *which slot*. Row DT-18c (§5.2) is **proposed**, not owner-confirmed — the owner parked slot-presentation changes on 11 Sep and this reopens them; the fix is on `latency-eval` only. Still open from the same call, in order: B (build-time `selected_slot` + no unwind on retraction), E (retraction → silence), C/D (the model's `date_hint` fed the one-slot arm and the guard read the caller's words — same parser, two inputs). |
| 2026-09-12 (VE) | **D-s: a read-back named a time the caller never accepted, on a live patient line.** Vital Edge CAea24df48, 12:52, build `dfaa0b02`. The caller accepted MIDDAY; three turns later: *"So that's Quentin Rook, Monday the 14th of September at five in the evening — shall I put that request through to Jonathan?"* 17:00 was a real Monday slot, spoken two turns earlier, so **Gate 5 passed it** (the time was in an offer) and **the slot-fact guard passed it** (the time is in the diary — invariant 4, not invariant 1, exactly as its docstring warns). Three holes: (1) the accept reader took *"do you have anything around 12 on midday"* as a PICK, because the utterance contains the spoken label and `utterance_is_slot_selection` is containment against labels — the 11 Sep band fix deferred only the BAND fallback to a question pattern; (2) `ACCEPTED_SLOT_KEY` is per-TURN by design (P6b) and was gone; (3) neither speech-derived phrase key was set — the model's reply carried no slot phrase, and *"shall I put that one in for you?"* is not a `_SPOKEN_COMMITMENT_RE` marker — so the read-back prompt fell to its last branch, which asks the model to fill the time in from memory. Closed: `_ASKS_IF_A_TIME_EXISTS_RE` (narrower than `_DAY_REQUEST_RE` on purpose — *"can I have midday"* is an acceptance), a durable `ACCEPTED_SLOT_RECORD_KEY` written from the payload's own label and cleared when a booking lands, and `readback_slot_phrase` which the read-back prefers over every speech-derived key. Scorer rows DT-18, DT-18b; whole scorer 109 / 0 / 38. **Note for the register: two guards passing a wrong-time confirmation is not a guard failure — it is invariant 2 having had no enforcement at all.** |
| 2026-09-12 | **D-r: a named time is ONE slot on every path.** Theorem CAf87ed571 (12:11, build `dfaa0b02`, first clinic-line call of the promoted build): every producer at 123–127 ms, guard announcing `mode=log`, OBS capture+judge on — and *"anything around 12 on that day"* went to the model because 12:00 had already been read ("midday") and the resolver searched only UNSPOKEN times. The model's "twelve o'clock" went through no producer; the keypad map still held {1: one, 2: three} — a keypress would have booked one o'clock (inv. 10). The owner, hearing the model's one-slot answer, preferred it to the demo line's three-slot D8 readout and asked why they differ: invariant 20, the tool path was never aligned to DT-7/8. Three changes, one commit: (1) the named-time resolver searches the day's BOOKABLE times (`bookable_on_current_day_anchored`) and sits above the exhaustion gate; (2) the tool path builds a one-slot offer (`build_named_time_offer`, `_named_time_offer`) instead of pinning the time into a readout; (3) `resolve_requested_time(far=True)` takes the nearest whatever the distance for a round time inside the day's span, on a day the caller CHOSE — not on a day that merely leads a menu. A one-slot answer now records itself as heard and repeatable (D-o). Scorer rows DT-7b, DT-8c, DT-8d; whole scorer 98 / 0 / 37. Two DT-21 expectations updated: the resolver sits above the clarifier, and "quarter to eleven" (15 vs 35 min) is not a tie. |
| 2026-09-11 | created; §3 D-c and the B1 prompt rule recorded as SUPERSEDED rather than deleted, because a superseded decision left in a test becomes a defect pin (B1 in `test_collection_sequence_prompt`, N1 in five measurements) |
| 2026-09-11 (after N6) | **"what about monday morning" closed; DT-10/11 built.** The accept reader's band fallback picked the one offered morning without asking whether the caller was choosing or asking — it now defers to `_DAY_REQUEST_RE`. Declining the pick exposed that no deterministic producer honoured a band: the named-day producer read the whole day, the more-slots branch read the next unheard times whatever the band, and on a single-day offer or a two-slot rota (everything heard) the request reached no producer. All four now answer the band; an empty or spent band says so and opens the rest of the day. Scorer rows DT-10 and DT-11 added (previously unscored): whole scorer 84 / 0 / 33. Regression file 21 tests, 14 failed before. The full suite caught one regression of my own on the way — N1's D8 test: "monday at one in the afternoon" was narrowed to afternoons and evicted the kept 08:00; a band that qualifies a clock time is now D8's (`_band_requested`), with the same exclusions D-q uses. Bare "8 in the morning" is read as a band request by both, because no parser reads a bare hour (the parser lead in §9.1). Note for whoever tests this: 17:10 is `evening` to `part_of_day`, so "Monday evening" on the 10:48 table is the spent case, not the empty one. |
| 2026-09-11 (11:xx) | **N6 closed.** The scorer's "only a call can see N6" was wrong: the routing that took the turn is `try_unspoken_followup_speech`'s more-slots branch, which the scorer already drives (DT-8b, DT-21, DT-4b). Driven that way, DT-14 reproduced the 10:47 call on the first run. Fix: a bare weekday counts as a day named in that branch. §8's "routing is invisible offline" claim should be read as "the model's routing is" — the deterministic dispatcher is not. The N1-file `xfail(strict)` pin XPASSed and was retired. Scorer 77 / 0 / 28. |
| 2026-09-11 (10:47) | **D-q proved on the line** — CAf80eb02d, build `83f47aad`: "what about monday" after Monday's readout → the same three verbatim (135 ms); "what else on monday" → Thursday/Friday/Saturday (**N6, now anchored**: `more_days_speech` — `'what else' answered with 3 day(s) he has not heard` — takes the turn before any day check); "and what about monday" → step 2's three verbatim again (139 ms). Phone "yes" confirmed once (`5129c791`). **All four owner decisions are now proved live.** `SLOT_FACT_GUARD=enforce` has been on the demo service since ~10:10 per the owner — three calls, no `[slot_guard]` finding, nothing replaced; the `mode=enforce` announce line not yet sighted in a pasted window. New row: *"what about monday morning"* was read by `slot_accepted_by_caller` as a PICK of 08:00 (the one morning time on the offer) — a question became an acceptance and the model authored the slot sentence; Monday holds five mornings. |
| 2026-09-11 (late) | D-q (DT-4b) built: "what about <day>" after that day has been read out is the readout again, verbatim, from a per-day record written beside every other record of an offer and keyed on the date the slots share (not `day_iso`, the anchor). Declines on a band word, a clock time, or a slot that has since left the diary. Also answers on a single-day offer, which previously went to the model. Scorer DT-4b rewritten from "divergence" to a real row: 4 pass / 2 unreachable; whole scorer 76 / 0 / 29. Demo call CAcae592ce (10:10, build `2dfc9b01`) proved DT-3/4, DT-30, DT-7/8 and DT-21 live at ~130 ms each, guard clean, and found the phone question asked three times (`5129c791`, not a slot row). All four owner decisions are now built. |
| 2026-09-11 (evening) | D-o (DT-30/31), DT-7/8 and D-p (DT-21) implemented — `a4a383e8`, `d585450a`, `1bc7fc52` — each with a scorer row and a regression file; §9.1 brought up to date (DT-7/8 had been left "open" after its fix). Two defects found by the work itself and closed on the way: the B-114 loose path (`9a55cdac`) and the hour-inside-"five past eight" read (`6944ffd9`), the second found by the new DT-21 scorer row on `uniform50` and answering a Monday caller with Thursday. Scorer 72 pass / 0 FAIL / 33 unreachable. D-q (DT-4b) not started. Nothing pushed. |
| 2026-09-11 | §9.2 closed: D-n confirmed, D-o/D-p/D-q added from the owner's answers. Two morning demo calls (CA778651b7, CA34942aee) on build `a590abaa`: guard clean on 15 slot sentences; DT-1/4/12/28 verified live; DT-30 served by the model on both; DT-7/8 served by the model on the second with its sentence stripped by Gate 5 (`closest_ive_got`), leaving *"Does that work?"* about nothing. N6 shape not exercised. |
