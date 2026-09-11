# Slot presentation — three weeks, stepped back

**Written** 2026-09-11, after the 00:02 demo call (`CA7ebc00839bf773bcf7cbaa52d7c60f7e`,
build `66f7dec3`) and the owner's request to stop patching and find the deeper
problem.
**Covers** 18 Aug → 11 Sep 2026: every slot-presentation plan, register, call
sheet and handover in `docs/plan/` for that window, the git history of the slot
code, the obs corpus, and the RENDERED live prompt.
**Status** analysis only. No code changed. It is the input to the next
deliverable — one specification and one prompt — not a replacement for it.

---

## 0. The answer in one paragraph

**Slot presentation is a dialogue-management problem that has been solved, for
three weeks, as a series of formatting and selection bugs.** There is no
explicit model of the "choosing a slot" sub-dialogue: nothing classifies what
the caller just did (ask, accept, refuse, repeat) once, nothing holds one record
of what is on the table and what was asked, and nothing says which of the
selection rules wins when they disagree. So every rule re-infers the caller's
intent from whatever residue the session holds, each fix is correct for the
exhibit that produced it, and compositions of correct fixes fail on the next
call. On top of that there are **two authors of slot facts with contradictory
specifications** — the deterministic producer and the model — and the live
prompt tells the model to present slots in a format the engine forbids. Every
defect on last night's call was spoken by the model. The correct diagnosis was
written down on 31 August and 3 September; the migration it called for was
never finished, and the eight days since went into exhibit-driven fixes that
roughly doubled the code.

---

## 1. What three weeks actually looked like

### 1.1 The code

| date | `slot_followup.py` | `slot_offer.py` | `llm_stream.py` | `receptionist_tools.py` | regression test files |
|---|---:|---:|---:|---:|---:|
| 18 Aug | 448 | — | 5,042 | 7,902 | 219 |
| 26 Aug | 1,007 | — | 5,844 | 9,099 | 301 |
| 30 Aug | 2,530 | — | 6,579 | 10,089 | 349 |
| 2 Sep | 2,819 | 334 | 7,433 | 10,165 | 374 |
| 5 Sep | 4,370 | 476 | 7,908 | 10,375 | 415 |
| 8 Sep | 4,934 | 522 | 7,985 | 10,630 | 442 |
| 10 Sep | 5,804 | 608 | 8,370 | 11,228 | 471 |
| **11 Sep** | **6,342** | **762** | **8,636** | **11,246** | **488** |

`slot_followup.py` is **14×** its size of 18 Aug. It now holds **119** top-level
functions, **31** module-level regexes, **17** caller-intent predicates, **6**
wrappers stacked on one selector, and at least **seven** code paths that speak
a slot sentence. `_flush_slot_buf` is **819** lines (464 when the 31 Aug
document proposed retiring most of it). `check_availability` has **5** refusal
branches.

Slot-related commits per week: **13 → 60 → 59 → 47** (the last is 4 days).

Numbered defect series spent in the window: B-78…B-151, P1–P12, D1–D12,
S-1–S-14, N1–N6, T1/T1b — **roughly 110 ids**, with **four id collisions**
(P4, B-127, B-149, and N1/N5 across two documents).

### 1.2 The outcomes

Calls that reached a numbered readout, from `calls`:

| week | our test phone | booked | abandoned | mean quality |
|---|---:|---:|---:|---:|
| 17 Aug | 24 | 50% | 33% | 3.29 |
| 24 Aug | 67 | 10% | 88% | 2.42 |
| 31 Aug | 73 | 7% | 90% | 2.54 |
| 7 Sep | 40 | 15% | 82% | 2.36 |

**Read this carefully, because it is easy to over-read in either direction.**
Booking rate is confounded — from about 4 Sep the call sheets say *"don't
complete a booking"* — and every call is an adversarial script aimed at the
last fix. What the table does support is narrower and still important: **three
weeks and ~180 commits did not produce a visible improvement in the judged
quality of calls that reach a readout.** The 3 Sep convergence document said the
same thing about the weeks before it: *"we are not converging, which is worse
than it sounds."*

### 1.3 How the work was validated

One phone call per fix, on one clinic (northgate), whose diary is a uniform
50-minute grid. Each call reaches a different path, so each call finds a
different defect. Documented instances where that loop misled us:

* a step passed without its mechanism firing (D8 on 10 Sep: S-2 happened to
  deliver midday);
* a step failed without a defect (19:36, 10 Sep: a test booking from the
  previous call removed 12:10);
* tests pinned a defect as the rule (N1 was asserted as correct by the replay
  gate, two T1b tests, two S-13 tests, and the call script's own step 3);
* the corpus lied by construction (13% of recorded offers were never spoken;
  obs transcripts are intent-to-speak; the offer corpus is forward-only);
* last night's call sheet could not reach the mechanism it was written for (N4
  needs a refused tool call; a caller cannot script that).

---

## 2. The findings, compiled by what they are about

Not by id. Each group lists the representative exhibits; the ids are there so
the source can be found.

### 2.1 Which slots to read, and how many

The rules that exist today, each with its reason:

| rule | owner/exhibit | what it protects |
|---|---|---|
| never read a time the caller heard on that day, while unheard ones remain | B-116, B-119 | "what else have you got" must bring new times |
| never pad a short unheard list back with heard times | B-119 | "I've given you all the mornings" then "nine in the morning" |
| two slots must span the day, not sit 50 minutes apart | `_spread`, owner 1 Sep | a real choice |
| prefer clock times not heard on another day | T1, T1b, S-2 | "eight in the morning" four times |
| "sooner" means the earliest, repeats and all | B-137, B-142, D11, D12 | "not soon enough" answered with later days |
| pin the time the caller named | D8, S-13, N2, N4 | "around 12" answered with 08:00 |
| pin the slot the caller accepted | P6b | an accepted slot deleted from the re-read |
| a named day keeps the times offered for it | N1 | "what about Monday" withdrew both Monday times |
| week is a menu of days (2/day), a day is 3 + "a few others" | owner 9 Sep, D9 | completeness through drill-down |
| every completeness claim must be true | B-97, B-98, B-99, D10, P10 | "that's all we have" over twelve slots |

**Every one of these rules is correct.** The defects came from how they
compose:

* S-2 (novelty across days) spent midday one turn before the caller asked for
  it, which is what turned a dormant D8 into a hung-up call.
* B-116 (novelty within a day) guaranteed that the two times which made the
  caller ask about Monday were the two withheld (N1, 6 of 6).
* P6b exists because B-116 guaranteed the accepted slot was the one missing
  from a re-read.
* B-137's "lead with unheard days" answered a request about Monday with
  Thursday (N4) — and, pre-existing, answers "what else on Monday" with other
  days (N6).

**The pattern: the optimisation target is novelty — "don't repeat" — while the
caller's goal is relevance: "answer what I asked".** When the caller says *"as
close as possible to 12"*, a receptionist says *"ten past twelve on Monday"*.
The system instead asks "which times has this caller not heard?", and relevance
survives only where a pin was added for that one question shape.

### 2.2 Understanding what the caller did

The same utterance can be a request, an acceptance, a pick, a refusal or a
repeat, and which producer answers is decided by wording:

| shape | exhibit | what went wrong |
|---|---|---|
| accept in words | P6 (VE, 1 Sep), P6b (2 Sep) | read the list again, accepted slot gone |
| pick by ordinal on a single day | P12 | resolved nothing, ever |
| "8 pm" against an offer holding 08:00 | f93a4d2a (3 Sep) | pinned 8 am |
| accept a day | B-145 (6 Sep) | no offer on the table |
| refuse a day | B-147 (6 Sep) | read that day back |
| specific acceptance ("three works") | CA4215ab7f (8 Sep) | re-query guard gated on the reader that misses every specific pick |
| request phrased as acceptance | S-13 fix went through B-145, not D-B (10 Sep) | a fix on one door looks wrong |
| "say that again" | N3 (10 Sep) | dropped as a fragment, 19 s |
| a selection read as a standing preference | B-90 (22 Aug) | afternoons filtered for the whole call |
| a reason read as a time filter | B-138 | "stiff every morning" → mornings only |
| urgency vocabulary | day_preference (9 Sep) | B-137/B-142 live only for "as soon as possible" |
| **"yeah the monday slot works"** | **11 Sep 00:04** | accepting a model-offered 14:40 was read as a day request; the Monday list withheld 14:40 |
| **"10 to 12 works"** | **11 Sep 00:04** | confirmed "twenty to twelve" — not a slot |
| "what else have you got on Monday" | N6 (pre-existing) | answered with Thursday–Saturday |

**There are 17 predicates doing this, each deny-by-default, consulted in a
dispatcher order, and several of them encode the same distinction twice**
(`utterance_accepts_offered_slot` vs `slot_accepted_by_caller`; `named_day_speech`
vs `day_acceptance_speech`). The recorded lesson — *"the matcher shape is the
bug, not the phrase"* — was learned four separate times (screening triggers,
`_HURT`, B-146, the acceptance readers). The system has no single place where a
turn becomes a typed caller act.

### 2.3 What is on the table

The 3 Sep convergence document named **ten session keys** that each claim to
say what was offered or heard. Their writers do not cover every turn type:

| key | defect when a turn type had no writer |
|---|---|
| `last_offered_slots` | always true mid-turn, so P6 read every first lookup as a standing offer (dc58d3b5) |
| `REQUESTED_TIMES_KEY` | dead on payload turns (S-13), stale on refusals (N4) |
| `_slot_presentation_mode` | stale on payload turns — slowed the wrong readout (4.9) |
| `v3_dtmf_slot_map` | stale after follow-ups (B-80, P9, P11) |
| spoken record + fingerprint | wiped every day on any lookup (B-101), reader distrusted absent days (B-102) |
| offer record on multi_day | a projection read as a transcript (B-126) |
| `total_days`, `times_not_shown` | presented counted as found (B-95, 102/102 days) |

Every one of these was a correct fix. The shape underneath is the same each
time: **state is written where it is computed, not where the turn is decided, so
each new turn type silently inherits the previous turn's state.**

### 2.4 Two authors, two specifications

This is the finding that explains last night.

**The engine's spec** (`build_slot_offer`, `SLOT_FORMATTER_SYSTEM_PROMPT`,
owner decisions of 1 and 9 Sep): three days, two times each, **numbered**,
"parsed for keypad selection"; a named day reads **three** numbered times plus
*"And I've a few others that day"*.

**The model's spec** — northgate's rendered prompt, 104,763 characters,
verified by rendering it, not by reading the module:

> *"Offer exactly TWO times: the earliest, and one materially different (a
> different day, or a clearly different time of day). TWO — not three, not six.
> Say them as ONE natural sentence with **no numbered list** and no count
> announced."*
>
> *"6. WHEN THE CALLER PICKS A DAY … Two times, same as Step 5: 'That day I've
> got [time] or [time] — which suits?'"*
>
> *"7. SLOT CONFIRMATION → NAME. … 'So that's [day] the [date] at [time]'"*

`Number 1` appears **0** times in that prompt. `a few others` appears **0**
times.

So whenever no producer claims a turn, the model presents slots in a format the
engine's own tests forbid, from whatever times are in its context, and confirms
whatever time it believes was agreed. **Last night's three defects are this
prompt, verbatim in shape:**

| turn | what the model said | which rule it followed |
|---|---|---|
| "as close as possible to 12" | *"the nearest to twelve o'clock is twenty to three on Monday the 14th, or twenty past four on Tuesday the 15th"* | Step 5: two times, different days, one sentence, unnumbered — from times already in context |
| "10 to 12 works" | *"So that's Monday the 14th of September at twenty to twelve"* ×3 | Step 7: confirm, no data check |

How often does the model get the turn? Since 3 Sep, of turns that followed a
producer readout in the same call, **74 went to the model and 30 to a
producer** (approximate — the heuristic cannot see tool turns). And the
instrument built to measure exactly this (Stage C, `record_model_readout`) is
called only inside `_flush_slot_buf`, i.e. on turns where `check_availability`
ran. **Model readouts on turns with no tool call — both of last night's — leave
no row.** It has recorded zero model readouts since it shipped.

The 31 Aug document already said the model should not write the sentence:
*"`SLOT_FORMATTER_SYSTEM_PROMPT` is already a pure function … That is Python's
job."* The primary readout moved to Python. The prompt that teaches the model
its own slot format was never changed, and every follow-up turn the producers
decline still falls back to it.

### 2.5 The same question reaches different code

* **Four availability readers** (Acuity, Google fall-through, diary, published)
  that did not agree on nine features (`ONE_PRESENTATION_LAYER.md`). Theorem's
  multi-day cap was enforced by the prompt, not code, until Stage A.
* **Five `check_availability` refusal branches**, each re-presenting a cached
  payload (B-118, N4).
* **Two named-day producers** sharing one function, reached by wording
  (D-B vs B-145).
* **Payload-answered turns run no tool**, so anything written only inside a
  tool is dead there (S-13, `_slot_presentation_mode`, Stage C).
* **Every rule must therefore be implemented per path** — D10's hedge needed a
  copy in `more_days_speech`; D12 existed because `choose_presented_days`
  answered the wrong question inside a different producer.

### 2.6 Measurement

Things the project built that genuinely work and must survive: the
`calls.slot_offers` corpus (payload + offer, per lookup), the three replay
harnesses, the synthetic diary sweep (`sweep_slot_offer.py`, 528 diaries), the
S-5/S-14 AST censuses, the frozen-worktree failing-set discipline.

Their recorded blind spots: `replay_slot_decisions` cannot see selection;
`replay_presented_times` cannot see the loop or D8; `replay_multi_day_spread`
cannot see named-day producers; `day_by_position` has raised on every turn
since it was written; Stage C cannot see no-tool turns; nothing replays the
refusal path; every phone verification is on a uniform grid.

---

## 3. The deeper problem, as a voice engineer sees it

Five structural causes. Each defect in §2 is an instance of at least one.

### 3.1 There is no typed caller act

A slot sub-dialogue has a small, closed set of caller acts:

```
ASK_OPTIONS   { day?, date?, time?, band?, sooner?, more? }
ACCEPT        { slot | day }
REJECT        { slot | day }
REPEAT
OTHER         (anything that is not about slots)
```

Production voice systems classify a turn into this set **once**, extract the
constraints **once**, and hand every downstream decision the typed result. Here,
17 predicates each answer a fragment of that question, deny-by-default, in
dispatcher order — and the selection rules then re-derive the act from session
residue. B-116 could not tell *"what else"* from *"what about Monday"* because
nothing told it which was asked. N1's fix had to smuggle the act in as a
keyword argument from the one caller that happened to know it.

**Consequence:** every new question shape is a new predicate, a new ordering
dependency, and a new wrapper. That is the growth curve in §1.1.

### 3.2 Two authors of slot facts, with contradictory specs

A time or a day spoken to a caller is a **fact about a real diary**. In a
reliable voice system exactly one component may author such facts, and it is
code. Here, the model authors them on every turn a producer declines, following
a prompt that describes a different presentation. Gate 5, the reverse-parse
layer (~900 lines) and the read-back guards exist to repair model-authored slot
facts after the fact. They cannot be complete: last night the model invented
11:40 and nothing stood between that sentence and the caller.

**Consequence:** the invariants hold on the producer path and are hopes on the
model path, and the model path is the majority of follow-up turns.

### 3.3 There is no single ledger

"What is on the table", "what has this caller heard", and "what has this caller
asked for" are three pieces of dialogue state. They live in at least ten keys
with different writers, lifetimes and per-turn wipes. A reducer that runs once
per turn — *given the act and the last ledger, produce the next ledger* — would
make S-13, N4, B-80, P9, B-101/102 and the `_slot_presentation_mode` staleness
unrepresentable, instead of individually fixed.

### 3.4 Rules without a precedence

The selector is a chain: B-116 → S-2 → N1 → D8 → accepted. The chain order is a
priority, but nobody wrote the priority down, so each wrapper's author inferred
it. The priority a caller would recognise is:

1. **never speak a slot the diary does not hold**;
2. **the slot they accepted**;
3. **what they asked for** — the named day, the nearest time to a named time,
   the band, "sooner";
4. **only then**, for *"what else"*, novelty;
5. **then** spread.

Today novelty (4) is the default and relevance (3) is a set of exceptions added
one exhibit at a time. That inversion is the owner's observation about last
night exactly: *"I asked for 12; a person would have started at 12."*

### 3.5 Validation discovers; it should confirm

Because behaviour is spread across paths, a phone call is an exploration, not a
test — it exercises whichever path its wording reaches, on one diary shape. The
fix is written against that exhibit, verified against that exhibit, and the next
call reaches a neighbouring path. The corrective is a **decision table** —
caller act × ledger state × diary shape → expected answer — run as generated
tests and as corpus replay, with the phone confirming rows rather than finding
them.

### 3.6 Why each round of fixes felt like regression

Three recorded mechanisms, all visible in this window:

* **A fix removes a coincidence that was doing real work** (S-2 → D8).
* **Enabling a dormant path exposes guards that were never load-bearing**
  (`SLOT_TIME_CHAIN_2026-09-09.md`: four commits in one afternoon).
* **A correct test of an earlier decision becomes a defect pin** when a later
  decision supersedes it (N1 in three measurements).

None of these is carelessness. They are what happens when correctness is the sum
of locally-correct rules with no global specification to check the sum against.

---

## 4. What already exists and should be kept

The redesign is not a rewrite. These pieces are right and are the foundation:

* `build_slot_offer` + `apply_offer_to_session` — one function returns the
  speech and the record. **No defect has been filed against that pairing.**
* Payload-answered producers — **0.13 s vs 3.06 s** p50 for the model path,
  n=3,566. The honest sentence and the fast sentence turned out to be the same
  sentence.
* `nearest_time_index` (tolerance on round times only, ties decline),
  `requested_clock_times` (dates masked first, durations and ages excluded),
  meridiem handling.
* The owner decisions, each dated: week = menu of days (9 Sep); spread (1 Sep);
  keep the part-of-day suffix (9 Sep, LAT-1 reversed); soonest-first opener
  (9 Sep, option B); caps per clinic in `clinic.json` (D2); never volunteer
  prices; a readout claims completeness only when true.
* `calls.slot_offers`, the replay harnesses, the synthetic sweep, the AST
  censuses, and the frozen-worktree baseline discipline.

---

## 5. What the clean design is (specification, not code)

**One classifier.** Each caller turn during slot selection becomes one act from
§3.1, with its constraints. `OTHER` goes to the model with no slot facts
available to it.

**One ledger**, updated by one reducer per turn:

```
Offer   : slots on the table (ISO), spoken order, keypad map, mode, day under discussion
Heard   : ISO starts spoken to this caller, in order, per day
Asked   : named day(s), named time(s), band, sooner, refused day(s)/slot(s)
Payload : the last availability result (bookable truth), with found vs presented
```

**One policy**, `answer = f(act, ledger, payload)`, with the §3.4 precedence
written as a decision table. Each row is an owner decision or an invariant, and
each row is a test.

**One author of slot facts: code.** The model never produces a day, date or
clock time for a slot. It may say *"let me check"*, may handle `OTHER`, may
ask the next question. Its prompt carries no presentation format — only *"slot
facts come from the engine; never state a time or day that the engine has not
given you"*. Confirmations (*"So that's Monday at ten past twelve"*) are
generated from the ledger.

**One verifier before TTS**, on every path: any clock time or day in outgoing
speech must exist in `Offer` or `Payload`. This alone would have stopped
*"twenty to twelve"*.

**Verification** moves from phone-first to table-first: the decision table as
generated tests over synthetic diaries (uniform grid, sparse, single-slot day,
band-filtered, month-end, closed day), corpus replay for regression, and one
call per clinic diary shape to confirm.

---

## 6. The next deliverable — one document, one prompt

### 6.1 `SLOT_PRESENTATION_SPEC.md` — to replace the lineage documents

1. **Glossary** — the caller acts, the ledger fields, the modes.
2. **Owner decisions** — each with its date and the call that prompted it.
3. **The decision table** — act × ledger state → what Susie says and records.
4. **Precedence** — §3.4, with the exhibit behind each level.
5. **Invariants** — condensed from ~110 defects into the ~20 that are actually
   distinct. Draft list in §6.3.
6. **What the model may and may not say.**
7. **Open items** — N6, the 9 Sep N1 (invented slot), last night's three.
8. **Retired documents** — `DETERMINISTIC_SLOT_PRESENTATION`,
   `SLOT_PRESENTATION_CONVERGENCE`, `ONE_PRESENTATION_LAYER` (presentation half),
   `SLOT_PRESENTATION_FINISH`, the eight `OPEN_DEFECTS_*` registers, the call
   sheets — kept in `archive/`, no longer authoritative.

### 6.2 The prompt

Two prompts currently describe slot presentation and they disagree with each
other and with the engine: the template prompt's Steps 5–7 (all three
template_v1 clinics) and `SLOT_FORMATTER_SYSTEM_PROMPT`. Theorem's hardcoded
builder is a third. The unified prompt section should be short and contain only:

* when to call `check_availability` and what to pass (the timing gate, urgency,
  named day as `after_date` + `day_window=1`, named clock time in `date_hint`);
* that slot readouts, follow-ups and confirmations are spoken by the engine,
  and the model must never state a slot day, date or time itself;
* what to do with an `OTHER` turn mid-selection.

It must be rendered and diffed per clinic, and the prompt-hash pins recomputed
per branch.

### 6.3 Draft invariants (to be checked against the decision table)

1. No day, date or time is spoken that the payload does not hold.
2. No confirmation names a slot that is not on the table.
3. An accepted slot is never withdrawn by a later readout.
4. A named day is answered with that day; a named time with the nearest bookable
   time on that day (ties decline; no drift for non-round times).
5. "Sooner" is answered with the earliest, and says so when nothing is earlier.
6. "What else" never repeats a time heard on that day while unheard ones remain,
   and never pads.
7. "What about <day>" keeps the times offered for that day.
8. A refused day or slot is not offered again in the same call.
9. Completeness is claimed only when true (days and times).
10. The keypad map always equals the last spoken numbered list.
11. Two times in one readout span the day.
12. A clock time is not repeated across days in one readout when avoidable.
13. A selection is never banked as a standing preference.
14. A reason is never banked as a time filter.
15. Every turn that presents slots records what was presented, with its producer.
16. Anything spoken about slots is recorded as heard only if it was spoken.
17. A turn that asks the tool and is refused is answered on the request's own
    day and time.
18. Parsing masks dates before reading hours; durations and ages are not times.
19. Presented ≠ found is always distinguishable in the record.
20. The same act reaches the same answer regardless of which path or reader.

---

## 7. What I recommend, in order

1. **Stop shipping per-exhibit slot fixes** until the spec exists. Keep
   `production` where it is; nothing on `latency-eval` since `2658f727` needs to
   reach a patient this week.
2. **Write the decision table first** (§6.1 item 3) from the owner decisions and
   the invariants, and **score today's engine against it** offline — synthetic
   diaries plus the corpus. The rows that fail today are the real backlog; the
   ~110 ids are not.
3. **Close the two-author gap before anything else changes in selection**: the
   pre-TTS slot-fact verifier (§5), and the prompt contradiction (§6.2). These
   are the two changes that would have prevented every defect on last night's
   call, and neither touches a selection rule.
4. **Then** the act classifier and the ledger reducer, behind the existing
   producers, one act at a time — the migration `SLOT_PRESENTATION_CONVERGENCE`
   Phase 2 already scoped, with the decision table as its gate.
5. **Phone calls confirm table rows**, on at least two diary shapes.

The honest risk: steps 2–4 are days, not hours, and the webinar is 30 Sep or
2 Oct. The alternative — another week of exhibit fixes at the current rate —
has three weeks of evidence against it.
