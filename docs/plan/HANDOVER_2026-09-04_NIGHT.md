# Handover — 4 September 2026, night

Continues `HANDOVER_2026-09-04_EVENING.md`. Read that one first for the state
of the world; this records only what changed after it, and why the remaining
items were left where they are.

---

## 0. State in one screen

| | |
|---|---|
| Canonical branch | `latency-eval` @ **`c47dd71c`** — three fixes on top of the evening handover |
| Production | `production` @ **`4eda31f3`** — **still NOT promoted** |
| Suite | **97 failed / 8,295 passed / 22 skipped / 1 xfailed** |
| Failing set | **byte-identical to the morning baseline**, verified after every commit |
| Revert target | `4eda31f3` — unchanged, write it down before any push to production |
| Working tree | `C:/Users/quent/AppData/Local/Temp/claude/b127-latency-eval` |

Captured count matched the reported total on every run (97 = 97).

---

## 1. What shipped

| SHA | What a caller would notice |
|---|---|
| `b4f2b4db` | **B-125c** — she no longer says "The very tomorrow, Saturday the 5th", and no longer deletes a true "that's the soonest we have" |
| `190c9821` | **B-31 residual** — a clinical screen that cannot be graded now leaves a log line instead of nothing |
| `c47dd71c` | **B-141** — a day with ten of twelve times unspoken is no longer called finished |

Each was proven by **neutering the fix and watching the tests go red**, part by
part — four neuters for B-125c, one for B-31, one for B-141.

### B-125c (§3.1 of the evening handover) — `b4f2b4db`

Both bad moments of `CAe5c2f6e00d58` traced to this one guard, and both are
fixed. Four changes, not the two the handover proposed:

1. **The intensifier is absorbed** in both frames, through one shared
   `_INTENSIFIER` constant.
2. **The dangling-seam rule.** A strip that would leave a determiner or
   intensifier standing at the cut drops that SENTENCE instead of speaking the
   fragment. Only the offending sentence; any slot readout beside it survives.
   This is the part that catches the next intensifier nobody thinks of.
3. **Day-level vs time-level.** A claim naming no clock time is about the DAY
   and is judged against the day. Judging it against the day's first spoken
   time is a category error it could never pass — which is how a true sentence
   was deleted.
4. **The day is identified by weekday + day-of-month**, not only by
   `day_label`. *This was not in the handover and is the second half of the
   same live defect:* the model said "Saturday the 5th" where the label read
   "Saturday 5th September", so the day was never identified at all. Both
   signals are required — a weekday alone repeats every seven days, and a bare
   number appears inside every clock time on the payload.

Also added: a later day can no longer be called the soonest while an earlier
one sits on the payload. Previously the question was only ever asked *within*
one day.

> **The first draft declared a second `_SENTENCE_SPLIT_RE`.** The module's own
> `test_only_one_sentence_splitter_is_defined_in_the_module` caught it, along
> with the reasoning leak it caused — someone made this exact mistake before
> and left a test for the next person. It worked.

### B-31 residual (§3.5) — `190c9821`

The handover called this "noise rather than damage". **Measured, it is
neither** — but the fix is not the one implied.

Over the last 400 stored calls (4,821 assistant turns): **523 turns run past
the 200-char cap, and 160 lose the '?' — 137 of the 400 calls, better than one
in three.** One measured example was a fracture screen cut at
`"...does it look out of shape| at all?"`.

So the fallbacks are **load-bearing, not belt-and-braces**. The warning that
fires constantly is a correctly-recovered screen announcing itself, and
`test_the_truncation_fallback_is_logged` pins it at WARNING deliberately —
demoting it would have been wrong.

The real gap was the *other* branch: truncated **and** `last_question` unusable
returned None in total silence, which is precisely the shape B-31 exists to
prevent ("its cost was never a wrong answer, it was an empty log"). That branch
now logs. Gated on the length, so the ~20 short deterministic writers in
`connection.py` stay quiet.

**The cap was deliberately NOT raised.** Every known reader has a fallback:
`_cta_asked` covers all nine write gates, and the two BLOCKED log lines read
the capped string only for their preview. Widening what a write gate can see
opens a booking gate — that needs a live call, not a green suite.

### B-141 (§3.2, first half) — `c47dd71c`

`exhaustion_claim_is_supported` gates *"I don't have any further times on that
day"* and its own comment calls it "POSITIVE proof of completeness". It was
not. It proved only that no time-of-day **band** filtered the day.

```
times_not_shown  =  how many times a preference FILTER removed
the claim needs   =  how many bookable times the caller never HEARD
```

**Measured against every `slot_offers` row in the obs store: 102 of 102
day-entries held bookable times the caller never heard, and `times_not_shown`
read 0 on all 102.** Not an edge case — the universal case for a multi-day
offer. Rebuilt from the stored payload of `CA9c39d09fe12bfc1e`, the predicate
returned True for a Monday with **twelve bookable and two spoken**.

Now measured as SPOKEN versus BOOKABLE, via `spoken_starts_for_offer`, which
already held the answer. Fails closed like everything else in the module.

`offer_day_hides_times` is **deliberately unchanged.** It asks whether a band
hid times, in order to decide whether to yield to a real lookup — and since
`available_days` holds the whole day, the session genuinely can answer.
Widening it buys a tool round trip for nothing.

---

## 2. §3.3 — MEASURED, and it is NOT a defect. Do not fix it.

The evening handover's §3.3 said the accepted slot "is not always pinned",
citing `collected.selected_slot` and `chosen_day` both null on
`CAc6d7a34e275e`, and pointed at `connection.py:9878` / `:12168`.

**Both fields were reading exactly what they should.**

On the free-form (v3 tool) path, `session["selected_slot"]` has only four
writers, all in `receptionist_tools.py`, and **every one of them runs after
`book_appointment` has already succeeded** — it exists to give
`build_sms()` its 📅/⏰ lines. It is a post-booking field, not a
slot-selection field. `chosen_day` is written only in `flow.py`, which is
bypassed on every live clinic.

Measured over the last 300 stored calls:

| | |
|---|---|
| BOOKED, `selected_slot` present | **23** |
| BOOKED, `selected_slot` missing | **0** ← would have been the defect |
| unbooked, `selected_slot` missing | 276 ← expected |
| any call with `chosen_day` set | **0 of 300** |

`CAc6d7a34e275e` reached the booking CTA and never booked, so a null
`selected_slot` is the correct value. This saved an edit to `connection.py` —
12,000 lines, the highest-risk file in the repo — that would have fixed
nothing.

Sixth for six: **every one-line defect row carried between sessions has been
mis-scoped.** The residual worth an eye is narrower and harmless to bookings:
no `caller ACCEPTED` line, i.e. the P6b turn-scoped readout pin did not fire.
It affects only pinning a slot into a re-query readout within the same turn.

---

## 3. §3.4, the keypad clobber — investigated, NOT changed, and here is why

The evening handover gave this no file:line, so it was a lead. Traced to
`OPEN_DEFECTS_2026-09-04_THEOREM_0954.md` §3: at 09:54:15 a `time_selection`
map held Thursday 10 September's times; at 09:54:36 a `day_selection` map
replaced it with the 17th, 24th and 1st October.

**A supersede mechanism already exists** — B-80's `_supersede_slot_map` /
`v3_slot_map_superseded` (`slot_followup.py:3580`), read at
`connection.py:7124`. It marks rather than clears, because the map OWNS the
slot window and popping it would let the next turn wipe `last_offered_slots`.
It covers the real case it was built for: the offer moving on with **no** new
numbered readout.

It does not cover this one, and correctly so — here a new numbered readout
*was* produced, so the new map is legitimate and the flag is rightly cleared.

What is left is an **in-flight keypress**: a digit dialled against map A
arriving after map B is armed. Discriminating it needs to know whether the
caller could have heard B before pressing — playback-completion timing on the
DTMF path. Note that:

- the mis-book is **hypothetical** ("would have been booked onto 17 September"),
  not observed. What was observed is that the 10th became unreachable by any
  keypress;
- the DTMF path is documented-fragile — a digit cancels the speech watchdog
  speculatively, and an early return that does not re-arm strands the call;
- `v3_slot_map_armed_turn` cannot discriminate, because a legitimate press
  arrives in the same turn as the arming.

**This is a live-call change, which is why the evening handover held it. It is
still held.** Do not write it against a green suite.

---

## 4. Not touched, deliberately

- **§3.2's second half** — at 15:31 the model answered "what about Wednesday"
  from the trimmed readout in its own context with **no tool call**, so none of
  the guards ran at all. Forcing a single-day lookup when a caller narrows to a
  named day is a routing change, and it wants a live call.
- **B-125d, a third syntactic frame** — `"Monday the 7th is the soonest we
  have"`, copula before the superlative. Neither pattern fires. Found while
  fixing B-125c and pinned as a `strict=True` xfail in
  `test_b125c_earliest_claim_fragment_and_day_level.py`. Not fixed in the same
  pass: widening a strip guard is what over-stripped a live caller this
  afternoon, and its repair is *not* the trailing one — replacing the match
  with a full stop would delete the day along with the ranking.
- §3.6 (STT "temperature"), §3.7 (known-accepted noise), §3.8 (already fixed) —
  as the evening handover directs.

---

## 5. Verification — what was run, and what it is worth

```bash
python -m pytest -q -p no:randomly --tb=no -rf > /tmp/X_full.txt 2>&1
grep -E "^FAILED |^ERROR " /tmp/X_full.txt | sed 's/ - .*//' | sort -u > /tmp/X_set.txt
diff /tmp/BASELINE_set.txt /tmp/X_set.txt
```

Run after every commit. Failing set byte-identical each time; captured count
checked against the reported total each time.

Slot-layer gates, run for B-141:

```
replay_day_picks.py          840 calls   — identical before/after
sweep_slot_offer.py          528 diaries — no invariant violations
replay_slot_resolutions.py               — identical before/after
replay_slot_readouts.py                  — identical before/after
```

> **All four are BLIND to B-141** — none of them calls
> `exhaustion_claim_is_supported`. Per the evening handover's own rule 2, a
> harness that cannot see the change it is being run for is not evidence. The
> proof is the test file: **five of its ten tests flip when the fix is
> disabled.** Do not read those four "identical" lines as safety.

### Reading the obs corpus

`find_dotenv()` returns empty in this worktree, so plain `load_dotenv()`
silently loads nothing and `OBS_DATABASE_URL` reads as unset. Pass the path:

```python
from dotenv import load_dotenv; load_dotenv(".env")
```

`slot_offers` rows carry `payload` (all bookable), `presented` (what was read
out) and `offer`. `presented` days may be absent for days not read out — treat
a missing entry as zero spoken, not as unknown.

---

## 6. Suggested order for the next session

1. **One verification call.** This is the gate on everything below. Say *"I
   need an appointment as soon as possible"*, then *"that's not soon enough"*
   twice. Listen for a whole sentence naming the earliest — and for her **not**
   offering to look further ahead. That re-tests B-125c and the ASAP path
   together, which is what the evening handover asked for.
2. **Promote to `latency-eval` → `production`**, then a live-clinic call.
   Revert target `4eda31f3`. Every open defect is already live on production;
   promoting fixes three more and makes none worse.
3. §3.2's routing half, then §3.4 — both want a call in hand, in that order.
4. B-125d, the third frame, with its own substitution.

---

## 7. Discipline notes earned tonight

1. **A test that pins a past mistake pays for itself.** The duplicate
   `_SENTENCE_SPLIT_RE` was caught in seconds by a test written by whoever made
   it last time, including naming the downstream failure it would cause.
2. **`git checkout <file>` after a neuter destroys the fix**, not just the
   neuter. It happened here on `clinical_screening.py` and cost a re-apply.
   Copy the file aside and restore from the copy.
3. **Measure the field before believing the row.** §3.3 read a post-booking
   field as a slot-selection field; three SQL queries closed it.
4. **Bash heredocs and regex do not mix.** Backslashes in the anchors for a
   scripted edit were mangled twice — once by the shell, once by Python's
   non-raw strings. Use raw strings, normalise CRLF→LF for matching, and write
   back CRLF. Every anchor asserted `count == 1`.
