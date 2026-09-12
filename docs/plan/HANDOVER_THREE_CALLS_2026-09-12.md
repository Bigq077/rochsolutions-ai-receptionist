# Handover — the three demo calls of 12 Sep 2026 (evening)

**For a fresh session.** Everything here is anchored to `latency-eval` at
`a09fd2bb` (pushed; demo line +447366263180 = `northgate`). `production` is
`7cc67f31` and has NONE of the day's work. Line numbers were read from that
tree on 12 Sep; re-grep before trusting them.

The three calls were scripted from `CALL_SHEET_HOLD_MOMENTS_2026-09-12.md` to
prove the hold-phrase change. The hold phrases passed. What they surfaced
instead is a **P1 booking-correctness defect chain** on the slot path — the
kind CLAUDE.md §6 puts first: *the caller believes one booking was made and
another exists.* Nothing on this list may reach `production` unfixed, and the
demo line should not be shown to anyone until A and B are closed.

| call | SID | build | scripted purpose |
|---|---|---|---|
| 1 | `CA021557c4b891cfe6bceb6fc6fe771d14` | `35f06eb6` | the booking path, every answer-moment |
| 2 | `CAe541a6a9efa70f5fec074a642d381435` | `35f06eb6` | the moments that used to get the apology |
| 3 | `CA5c69c585325d9004d5f859f76b7d67a6` | `35f06eb6` | the fallback rungs — and it auto-picked a slot |

---

## 0. Read this before touching anything

**Where to work.** A worktree of `latency-eval`, never the main checkout
(`vitaledge-onboarding`, legacy). `git worktree add … origin/latency-eval`,
copy the slotspec `.env` in (it has `OBS_DATABASE_URL`, live and readable).
See CLAUDE.md §2 for the branch model; `production` is a fast-forward target,
promoted **by SHA** after a demo call, out of hours, revert SHA recorded.

**Verification is now obs, not a pasted log.** Since `05acf306`/`b4a4d339`/
`a09fd2bb` every call on the demo line stores its own log lines:

```bash
python -m app.obs.call_log <CALL_SID> [substring]
```
or `SELECT lines FROM call_logs WHERE call_sid=…`. The `calls` row has the
transcript, `slot_offers` (payload + presented + offer sentence), `collected`
(what the read-back will use) and `latency.turns[].hold_reason`. Ask the
owner for a Render paste only if there is no `call_logs` row — and then the
`calls.build_sha` tells you why (pre-`a09fd2bb` build, or capture off).

**The spec is the authority on anything slot-shaped.**
`docs/plan/SLOT_PRESENTATION_SPEC.md`: §3 owner decisions (D-a…D-s), §5 the
decision table (DT-n rows), §6 the 20 invariants, §9 open rows. Owner's
standing rule: *name a row or don't ship.* The two decisions this handover
leans on most: **D-r** (a named time = ONE slot, every path) and **D-s** (the
read-back names the ENGINE's accepted-slot record, and an existence question
is never an acceptance).

**Test baseline is red, not green.** `pytest tests/regression -q` in this tree
= 8,970 passed with **six** standing failures: `b84` ×3, the multi-day count,
scarcity multiple-days, and `test_stored_turns_round_trip_through_lat_parse`
(fails on `ead2d713` too). Diff failing sets; do not look for green. Some
tests send real SMS and one auto-suite booked 60 real Acuity appointments —
see the memory notes before running anything outside `tests/regression`.

**Two traps that cost time today.** (1) Never write a regex through a bash
heredoc — Python's `\s`/`\?` warnings are the tell; use the Edit tool. (2)
`app/media_streams/connection.py` is CRLF; a whole-file rewrite with LF
produces a 19k-line diff. Normalise before committing.

---

## 1. Defect register

Severity: P1 = booking correctness / patient-facing lie; P2 = promised work
or dead air; P3 = flow quality; P4 = cosmetic.

| # | sev | one line | call | state |
|---|---|---|---|---|
| **A** | **P1** | A non-affirmative caller turn ("hello you still there") was resolved as ACCEPTING a one-slot offer | 3 | **open** |
| **B** | **P1** | A one-slot offer pins `selected_slot` when BUILT, before the caller hears it; the pin survives the guard retracting the sentence | 3 | **open** |
| **C** | P2 | The one-slot (D-r) arm fired on a **window** ("after four") taken from the **model's tool argument**, two turns after the caller said it — not on a time the caller named that turn | 3 | **open** |
| **D** | P2 | The offer builder and the fact guard parse *different inputs* for "what the caller asked": builder = model's `date_hint`, guard = caller's words. The builder's own sentence was scored as a lie and retracted | 3 | **open** |
| **E** | P2 | After a guard retraction the turn asks nothing, and the watchdog's re-ask is dropped by the same guard → 15 s of silence until the caller spoke | 3 | **open** |
| **F** | P3 | "last Tuesday" (past tense) banked as `day_preference: tuesday` | 3 | **open** (head side fixed, uncommitted) |
| **G** | P2 | Write head "Getting that in the diary —" spoken on a yes to the one-slot OFFER, before the name — a write claimed, none made | 2 | **fixed, uncommitted** |
| **H** | P2 | Check-in head then the model's own "still here —" on top; "hello you still there" not read as a check-in | 2,3 | **fixed `e669ef45`** |
| **I** | P3 | Read-back cut mid-sentence: "so that's Jackamo Zanetti," → "Before I do that — is 0 7 5 … the best number?" | 1 | open, by design (see §2.I) |
| **J** | P3 | Name capture took three turns on bad STT ("jackhammer"); confirm loop wording | 1 | open, mostly STT |
| K | P4 | "As for parking —" head joined onto the model's sympathy sentence | 3 | open, cosmetic |
| — | — | Offline-replay artefact: `hold_reason` recorded the 600 ms candidate, not what was spoken | — | fixed `e669ef45` |
| — | — | `call_logs` table not created on a live store; redaction ate timestamps/SHAs | — | fixed `b4a4d339`, `a09fd2bb` |

A, B, C, D, E are **one chain**. Fixing any one of A or B stops the wrong
read-back; fixing only A leaves a phantom `selected_slot` the model can see.

---

## 2. The defects in full

### A — a check-in resolved as an acceptance  (P1)

**What the caller heard.** Turn 24 ended with *"Sorry — let me just
double-check that one for you."* and nothing else (see E). The caller said
*"hello you still there"*. The next thing Susie did was take their name, and
the read-back was *"Tuesday the 15th of September at twenty past four"* — a
slot the caller had never heard and never chose.

**Evidence (call 3 log, now in `call_logs`):**
```
17:36:35,796 [ms_conn v3] caller ACCEPTED 2026-09-15T16:20:00+01:00 ('hello you still there') — pinned into any readout this turn (P6b)
17:36:35,796 [slot_followup] accepted slot pinned for the CALL: 2026-09-15T16:20:00 ('Tuesday 15th September at twenty past four in the afternoon')
```
`calls.collected.selected_slot = 2026-09-15T16:20`.

**Mechanism.** `connection.py:12688-12706` runs `slot_accepted_by_caller`
on every caller utterance while an offer is on the table, and pins whatever
it returns (`_ACCEPTED_SLOT_KEY`, then `_note_accepted_slot` → the durable
`ACCEPTED_SLOT_RECORD_KEY` that D-s's read-back trusts).
`slot_followup.py:3301 slot_accepted_by_caller` is documented *"DENY BY
DEFAULT, and every step here can decline"* — but its steps are all about
**which** slot, never **whether** the utterance is an acceptance at all:

1. `:3332-3336` — declines only "more times", "different day", and
   `utterance_is_a_request_not_a_pick`. A check-in is none of those.
2. `:3429` — *the offer names ONE day, so the caller naming only a time has
   left nothing ambiguous* → `date` = that day. Nothing about the utterance is
   consulted (`1a54dd23`, 3 Sep — added for "um 10 past 5 in the evening
   works" after Susie had narrowed to Monday).
3. `:3450` — *one time on the day is normally the whole answer* → return it.
   Only `_time_contradicts` can decline, and "hello you still there" names no
   time to contradict.

So after ANY one-slot offer, ANY utterance that is not a request resolves to
that slot. The one-slot offer (D-r, `e161c435`, 12 Sep) made this reachable on
every named-time turn; before D-r a single spoken slot was rare.

Note B-138 (`:3435`) and `_ASKS_IF_A_TIME_EXISTS_RE` (D-s) both patched
*specific* non-acceptance shapes into this function. This is the third. The
shape is the bug: the resolver answers "which" before anyone has asked "is
this a yes".

**Fix scope (recommended).** Add a single **acceptance gate** at the top of
`slot_accepted_by_caller`, or in the caller at `connection.py:12688`, that
returns None unless the utterance *positively* accepts: an affirmative
(`hold_speech._AFFIRM` is the same shape, already tested), an acceptance word
(`hold_speech._ACCEPTS`: works/suits/take/go for/that one…), an ordinal, a
day name, or a clock time. A check-in, a fragment, a question, a name, a
phone number all fail it. Put the gate in `slot_followup` so the
`utterance_is_slot_selection` / `_hs_picking` consumers see the same verdict.

**Implications / risk.**
- `test_a_time_only_pick_after_the_day_is_settled.py`,
  `test_a_single_day_pick_resolves.py`, `test_dt18_…`, `test_dt7_…`,
  `test_a_day_pick_without_a_time_is_still_a_pick.py` pin the *positive*
  cases; run them first and make sure every fixture utterance passes the new
  gate (they all contain an accept word or a time — check, don't assume).
- The gate must NOT require a clock time: "yes please" / "go for it" to a
  one-slot offer is the common acceptance and must still pin.
- `hold_speech._answer_moment` step 3 (`hold_speech.py` ~`:1180`) uses the
  same predicates for the pick HEAD; keep the two aligned or the head will say
  "That one works —" on a turn the engine did not accept.
- Spec row: this is **DT-18's precondition** — propose adding the sentence
  *"an utterance that does not affirm, name a time, a day or a position is not
  an acceptance"* to §5.2, with call 3 as the exhibit. Owner has not yet
  agreed to a spec change (decision 1 below).

**Acceptance test.** Fixture from call 3's `slot_offers` payload: one-slot
offer on Tue 16:20 on the table → `slot_accepted_by_caller(session, "hello
you still there")` is None; `"yes go for it"` returns 16:20; `"the accident
was"` None; `"quentin rock"` None.

### B — the offer pins `selected_slot` when built  (P1)

**Evidence.** `calls.slot_offers[0]`: mode `one_slot`, `spoken: true`,
offer sentence *"The nearest I've got to four in the afternoon is twenty past
four… Shall I book that in for you?"* — while the transcript for that turn
holds only the guard's recovery line. `collected.selected_slot` = 16:20.

**Mechanism.** `slot_followup.py:5720-5750 apply_resolved_time_to_session`
writes `last_offered_slots`, `slot_labels`, **`selected_slot`**,
`F_SELECTED_SLOT`, `record_spoken_slots([slot])` and `LAST_READOUT_KEY` —
all at build time, before TTS. Comment says *"Mirror fast-path slot
selection so the LLM / booking path sees it."* Then the fact guard replaced
the sentence at TTS (`connection.py:15928-15940`) and nothing unwound any of
it: the slot is "spoken" (inv. 16 violated — it was not), "selected", and on
the table for A to resolve against.

Two distinct faults:
1. **"Offered" and "selected" are one variable.** `selected_slot` is read by
   the model (prompt state), the booking path and the read-back. A one-slot
   offer is still an offer; the caller has to say yes.
2. **Retraction has no unwind.** `slot_fact_guard.check_outgoing` sets
   `_BLOCKED` and replaces the text; the session state written by the
   producer stays. `mark_offer_spoken` (inv. 16) exists for exactly this
   distinction and the one-slot path bypasses it by calling
   `record_spoken_slots` directly.

**Fix scope.** (1) In `apply_resolved_time_to_session` stop writing
`selected_slot`/`F_SELECTED_SLOT`; write `last_offered_slots` only and let the
acceptance path (A) promote to selected. Find every reader of
`selected_slot` set by this function first — `grep -n '"selected_slot"'` in
`llm_stream.py`, `receptionist_tools.py`, `turn_handler.py`,
`connection.py` — the fast-path pick sets the same key legitimately and
those readers must keep working. (2) On a guard replacement in `_tts_loop`,
call a new `slot_followup.retract_offer(session)` that clears
`last_offered_slots`/`slot_labels`/`LAST_READOUT_KEY` and un-records the
spoken slot — or, simpler and safer, have the guard's `Verdict` carry
`retracted=True` and have the producer's `mark_offer_spoken` never run.
Check `apply_offer_to_session` (multi-day path) for the same build-time
`selected_slot` write before assuming it is one-slot-only.

**Implications / risk.** `test_a_time_only_pick_after_the_day_is_settled.py`
and `test_unspoken_slot_followup.py` exercise this function; the DT-7/DT-8
scorer rows (`SLOT_PRESENTATION_SPEC.md` §8) replay one-slot offers. The
prompt's step F4/F5 logic reads "slot is locked in" from session — verify
what key it reads (`susie_system_prompt.py:930`) so removing the build-time
write does not make the model re-offer. This is a **slot-presentation
change** the owner parked as "tedious"; it needs decision 1.

**Acceptance test.** Build the one-slot offer from call 3's payload →
`session["selected_slot"]` is absent; simulate `check_outgoing` returning
`RECOVERY_SENTENCE` → `last_offered_slots` empty, nothing recorded as
spoken; then `"hello you still there"` → no acceptance; `"yes go for it"`
after a *spoken* offer → selected.

### C — a window from the model's tool argument drove one-slot mode  (P2)

**Evidence.**
```
17:36:16,301 [ms_llm] tool: name=check_availability args={… "date_hint": "Tuesdays and Thursdays after 4pm"}
17:36:16,597 [ms_gate5] the caller named a time (['16:00']) -- ONE slot, not a readout with it pinned in (DT-7/8)
```
The caller's turn was *"uh yes go for it"* — `requested_clock_times("uh yes
go for it") == []`. The caller's earlier words *"only after 4"* also parse to
`[]`. `"Tuesdays and Thursdays after 4pm"` — the MODEL's paraphrase — parses
to `['16:00']`.

**Mechanism.** `receptionist_tools.py:6654` `_pref = args.get("date_hint")`;
`:6665` `session[REQUESTED_TIMES_KEY] = requested_clock_times(_pref)` (D8,
`a08778f9`, 9 Sep — comment: *"Resolve the clock time the caller named ONCE,
here, where the hint arrives"*). The hint is the model's, not the caller's.
`llm_stream.py:1148-1185 _named_time_offer_for_tool_path` (DT-7/8 on the
tool path, `e161c435`, 12 Sep) reads that key and builds ONE slot whenever it
is non-empty. Neither checks that the time came from THIS turn's caller
words, and neither distinguishes a **bound** ("after four") from a **point**
("four").

**Fix scope (recommended: readout, not one-slot, for bounds).**
1. `requested_clock_times` or a sibling should return the *kind* of mention
   — point vs after/before/between — and the DT-7/8 arm should fire on points
   only. A bound should filter the readout (the D8 pin already knows how to
   keep the asked time in a list) — i.e. DT-3-style readout of the day's
   after-four slots, numbered.
2. Source: on the tool path, `REQUESTED_TIMES_KEY` should be written from the
   caller's utterance of the turn (`user_text`), with the model's `date_hint`
   as a fallback only when the utterance parses to nothing — and it should be
   cleared when the turn's utterance is a plain affirmative. The comment at
   `slot_followup.py:6533-6541` argues for "written on every payload turn,
   empty included" for the follow-up path for exactly this staleness reason.

**Implications / risk.** D-r is an owner decision two days old with scorer
rows DT-7/DT-8 (`test_dt7_a_named_time_is_one_slot_on_every_path.py`,
`test_dt8_…`). A "bound is not a point" rule is a *refinement* of D-r, not a
reversal — get the owner to say so (decision 2) and add it as DT-8b/8c. The
parser leads in spec §9.1 ("eight in the morning" → `[]`, "half three" →
`[]`) live in the same function; do not widen its output without re-running
`test_a_spoken_slot_time_exists_in_the_diary.py` and the DT-21 tie logic.

### D — builder and guard disagree about what the caller asked  (P2)

**Evidence.**
```
17:36:16,609 [slot_guard] SPOKEN SLOT FACT NOT IN THE DIARY (enforce): '4 in the afternoon' reads as 16:00, and no payload on this call holds any of them on 2026-09-15. clause="The nearest I've got to four in the afternoon is twenty past four …"
17:36:16,609 [ms_conn] slot_guard REPLACED a slot fact: … -> 'Sorry — let me just double-check that one for you.'
```

**Mechanism.** The guard permits a non-diary time only if the caller named it:
`slot_fact_guard.py:216 note_caller_speech` folds
`requested_clock_times(user_text)` into `_ASKED`, called from
`llm_stream.py:3382` on the caller's words. Same parser as the builder — but
the builder was fed the model's `date_hint` (C), so `_ASKED` never held
16:00 and the builder's D-r "nearest to X" sentence
(`slot_followup.py:5665 format_time_available_speech`) named an X the guard
had no record of. **The guard was right by its own rules.** Same fact, two
inputs.

**Fix scope.** Falls out of C(2): if `REQUESTED_TIMES_KEY` is written from
the caller's words, `_ASKED` and the builder agree by construction. Belt and
braces: `format_time_available_speech` could call `note_caller_speech` /
fold `asked` into `_ASKED` itself, since it is about to SPEAK that time as
the caller's — one line, and it makes the sentence self-consistent whatever
fed it. Do not "fix" this by relaxing the guard: inv. 1 is the one shipped
guarantee (`enforce` on the demo line since 11 Sep 10:10, 3 clean calls;
`log` on production).

**Owner decision 3:** keep the demo line on `enforce` while A–E are open
(it correctly stopped a false slot being spoken — the retraction was the
guard working), or drop to `log` to avoid E's silence. Recommendation: keep
`enforce`, fix E.

### E — a retracted turn leaves the caller in silence  (P2, dead air)

**Evidence.**
```
17:36:19,353 [ms_watchdog] BACKSTOP armed — turn asked nothing ('Sorry — let me just double-check that on') but a question is still outstanding: 'Before I do that — could I take your first name and surname?'
17:36:30,865 [ms_watchdog] WATCHDOG_FIRE prompt="Sorry, I didn't catch that. Before I do that — could I take your first name and " attempt=#1
17:36:30,865 [ms_conn] slot_guard dropped the tail of a retracted offer: "Sorry, I didn't catch that. Before I do that — could I take your first name and "
17:36:34,092 caller: 'hello'
```
15 s from the recovery line to the caller giving up and speaking.

**Mechanism.** `check_outgoing` (`slot_fact_guard.py:558-561`): while
`_BLOCKED` is set and mode is `enforce`, **every** later chunk returns
`""` — *"the sentence this chunk belongs to was already replaced; speaking its
tail would put the caller halfway through a retracted offer."*
`connection.py:15928-15935` drops it. `_BLOCKED` lapses only at
`turn_boundary` (`:539`), which `_tts_loop` calls on the `DEDUP_RESET`
sentinel (`connection.py:15568`) — i.e. on the **next caller turn**. The
watchdog re-ask (`connection.py:5995`) is emitted inside the same turn, so it
is "the tail of a retracted offer" by definition, and the name question with
it. Two safety mechanisms, each correct alone, jointly produce the one
outcome both exist to prevent.

**Fix scope.** Narrow the tail-drop to chunks that *contain a slot fact*
(`spoken_time_mentions`/`offer_clauses` non-empty) or that arrive within the
same LLM generation; a chunk that is a **question with no time in it**
("could I take your first name and surname?") is exactly what the guard's own
docstring says the recovery hands the turn to, and must be spoken. And the
watchdog's re-ask must clear or bypass `_BLOCKED` (it already logs "cleared
tts_inhibit before re-ask" at `:5992` — extend that to the guard latch).
Owner of the two silences: the ladder covers "we are slow", the watchdog
"caller quiet" — see the memory note; this is a third case, "we retracted",
and it needs a named owner.

**Acceptance test.** Session with `_BLOCKED=True`: `check_outgoing(session,
"could I take your first name and surname?")` speaks it;
`check_outgoing(session, "or ten past five on Tuesday")` still drops.

### F — "last Tuesday" banked as a day preference  (P3)

`connection.py:12866-12880 _extract_day_preference` on the reason answer
path, gated by `_reason_answer` and `_day_preference_supersedes`. B-138 ("i
did my back in on saturday") is the same class and was patched by the reason
gate; a past-tense marker ("last", "since", "on … when") is the general
shape. Head side is fixed in the uncommitted `hold_speech.py` change
(NAMED_DAY blocker `last|since`); the engine side should use the same
predicate — put it in one place both can import.

### G — write head on the one-slot offer  (P2, **fixed, uncommitted**)

`hold_speech._CONFIRM_CTA` matched "book that in" alone, so a yes to
`slot_followup`'s offer sentence *"Shall I book that in for you?"* (spoken
BEFORE the name) got "Getting that in the diary —" then "could I take your
first name?". The prompt's write CTA is *"shall I go ahead and book that in?"*
(`susie_system_prompt.py:965-971`, hard precondition: name + phone in hand).
The uncommitted diff keys WRITE_BOOK on "go ahead and book/put", moves the
offer CTAs to `_PICK_Q` (→ "That one works —"), and drops the offer sentence
from `_BOOK_OFFER_Q`. Tests green. **Owner has not said ship.** It is
`git diff app/hold_speech.py` in the `hold-moments` worktree; if the worktree
is gone, re-derive from this paragraph.

### H — the model's "still here" on top of the check-in head  (**fixed `e669ef45`**)

Greeting-prefixed check-ins classify as `CHECK_IN`; "still here" added to
`ACK_OPENER_RE` so `strip_head_echo` removes the model's duplicate. Unproved
on a call since; the call on `e669ef45` was a two-turn hang-up.

### I — the cut read-back and the digit-spelled number  (P3, by design)

`turn_handler.py:2672-2695` (Gate 5g): the model spoke the F5 summary with
the CTA before the phone step; the gate substituted the CTA sentence with
the phone question (`:709`). Its own comment documents the blast radius:
when read-back and CTA are joined by a dash, `_BOOKING_CTA_SENTENCE_RE`
(`:632`) eats the read-back too — hence *"so that's Jackamo Zanetti,"*. Root
is the model violating the prompt precondition; the gate is the net. The
"0 7 5 0 2 …" in the transcript is **pre-substitution text** — the obs
transcript records `_obs_chunk_text` before `_apply_tts_subs`; TTS's
`_spell_phone` (`tts_stream.py:146`) should have spoken "oh seven five oh
two". The `[ms_tts] synthesise_chunk … phone=` flag in `call_logs` settles it
for the next call; it was `phone=True` on the model-written number in call 3.
Owner decision 4: in scope now, or after A–E.

### J — three-turn name capture  (P3)

STT gave "jackhammer is a netty" for a name; the confirm loop did its job
("did you say Jackhammer?") and the caller corrected twice. Booked under the
corrected name. The wording of the loop is the owner's to judge; nothing
engineering-shaped here beyond keyterms.

### K — head/sympathy join  (P4)

"As for parking —" + *"sorry to hear you're dealing with both of those"*:
SYMPTOM needs a `_HURT` word and "knee thing" has none; FAQ_PARKING won.
Reads oddly, claims nothing false. Leave.

---

## 3. Already landed today (all on `latency-eval`, none on `production`)

| SHA | what |
|---|---|
| `c117c0b4` | hold phrases: receipt at 2.75 s, apology only at rung 2 (`LONG_WAIT`), answer-moment heads, 4 new intents, `hold=` on `[LAT]` |
| `e0e66041` | the call sheet |
| `35f06eb6` | rung-2 deadline 10000 → **7000** (owner) |
| `e669ef45` | H; `hold_head` records what was queued |
| `05acf306` | obs: the call carries its own log (`call_logs`) |
| `b4a4d339` | obs: create the table on a store that already has `calls` |
| `a09fd2bb` | obs: redact per line; timestamps and hex ids survive |

Memory notes exist for each (`hold-phrases-no-generic-2026-09-12`,
`rung2-deadline-awaiting-owner`, `obs-call-logs-table`).

## 4. Open owner decisions (asked 12 Sep, unanswered)

1. **A + B are slot-presentation changes** — add the acceptance precondition
   to spec §5.2 (DT-18) and fix under it? The owner called slot presentation
   "tedious" and parked changes; A/B cannot ship without reopening it.
2. **C** — "after four" as a filtered readout (recommended) or keep one-slot?
   Refines D-r; needs a DT row.
3. **D/E** — keep the demo line on `SLOT_FACT_GUARD=enforce` while fixing E
   (recommended), or drop to `log`?
4. Ship the uncommitted G and F(head) fixes, or hold?
5. Is I in scope now?

## 5. Suggested order and gates

A → B → E → D (falls out of C) → C → F → I. One commit and one regression
test each, the test built from call 3's `slot_offers` payload (it is in
obs; `python -c` it out). After A+B: one demo call replaying call 3's script
(*"free Tuesdays and Thursdays but only after 4"* → *"yes go for it"* → say
nothing → *"hello?"*) and read `call_logs` for `caller ACCEPTED` (must be
absent) and `selected_slot` (must be absent until a yes). Only then promote
by SHA, out of hours, with `7cc67f31` as the revert.

## 6. What to grep in `call_logs` for this family

`caller ACCEPTED`, `accepted slot pinned for the CALL`, `the caller named a
time (`, `slot_guard REPLACED`, `dropped the tail of a retracted offer`,
`BACKSTOP armed`, `WATCHDOG_FIRE`, `booking CTA held back`,
`situational head (`, `filler phrase triggered`, `hold=`. A query across the
corpus for `caller ACCEPTED` whose quoted utterance has no accept word, time,
day or ordinal will size A beyond one call — do that before writing the gate,
so the fixture set is real.
