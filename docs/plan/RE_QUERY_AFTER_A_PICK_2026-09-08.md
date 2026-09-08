# The re-query after a pick — what CA4215ab7f actually was

8 September 2026. Written after fixing the wrong layer first, which is the part
worth reading.

---

## 1. What happened

```
01:15:18  The earliest I have is Wednesday 9th September - Number 1, nine in the
          morning. Number 2, ten in the morning. Number 3, three in the
          afternoon. And I've a few others that day.
01:15:32  caller: "yeah three works"
          caller ACCEPTED 2026-09-09T15:00:00+01:00          <- resolved, correctly
01:15:39  "Sorry, still with you -"                          <- the 3.5s hold head
01:15:56  ...16.1 seconds of Friday 11th, Monday 14th, Tuesday 15th
01:16:01  caller: "let me if the last one works for me"
01:16:10  hung up.    outcome=abandoned  score=2  [booking_error, loop, dead_end]
```

The resolver was right. Everything after it was wrong.

---

## 2. I fixed Gate 5 first, and it was nearly worthless

`8b97f1e4` / `88e0df08` / `4efbdc95` are real fixes to real defects. On **this**
call they change almost nothing, and `calls.slot_offers` proves it. Two entries,
**identical 20-day payload**:

| seq | mode | what it presented |
|---|---|---|
| 0 | `single_day` | Wednesday 9th — nine, ten, three |
| 1 | `multi_day` | Friday 11th, Monday 14th, Tuesday 15th |

And seq 1's deterministic chunks are **91% token-identical** to the sentence the
caller actually heard:

```
model  "...Number 1, Friday 11th September ... Any of those suit you?"
det    "...Number 1, Friday 11th September ... Any of those work?"
```

So suppressing the stand-down only chooses **which wording of the wrong list**
gets spoken. By the time anything in Gate 5 runs, the model has already been
handed twenty days and has already built a list out of them.

> **The rule this gives us.** Before fixing anything in Gate 5, check
> `calls.slot_offers` for a repeated payload. If the payload repeats, the defect
> is at or above the tool call and Gate 5 is cosmetic.

---

## 3. What it actually was: a guard wired to the wrong reader

The guard that exists to stop this **already existed**. `_one_streaming_call`
blocks a repeat `check_availability` and returns `status: slot_offer_still_live`,
whose message reads *"Do NOT call check_availability again and Do NOT re-list the
times. If they accepted a specific time, confirm that slot..."*

It did not fire, because it was gated on one of **two complementary readers**:

| caller says | `utterance_accepts_offered_slot` | `slot_accepted_by_caller` |
|---|---|---|
| "that works for me" | **True** | None |
| "yes please" | **True** | None |
| "yeah three works" | False | **15:00** |
| "ten in the morning works" | False | **10:00** |
| "the third one" | False | **15:00** |

`utterance_accepts_offered_slot` is a phrase list over **text alone** — it takes
no session, so it cannot know "three" was one of the times just offered. It
catches the vague acceptances and misses **every specific one** — which are
exactly the cases where the engine already knows which slot was meant and has
already logged it.

**The fix is not another phrase.** Adding "three works" leaves "ten in the
morning works", "the third one", and every wording after those. The rule was
already written two thousand lines above the guard: *"Discriminated on DATA, not
a phrase list."*

---

## 4. A second thing, upstream of all of it

`_accepted_slot_iso` had **no reader in either prompt builder**. The engine
resolved the caller's pick exactly, used it to steer Gate 5 and the hold speech,
and told the model nothing.

The model learns a slot is settled only from `v3_confirmed_slot_phrase`, which is
captured out of its **own** name-request sentence. So it is informed of the
choice only after it has already said so; if it does anything else, nothing
corrects it. That is a circular dependency, and it is the **second** in this
codebase — the first name is likewise only ever learned from Susie's own speech
(`gate5g`).

---

## 5. What shipped

| commit | | layer |
|---|---|---|
| `7aa07f4a` | `chosen_slot_steer` — tells the model, from **both** prompt builders | the cure |
| `7aa07f4a` | `_narrows_to_the_chosen_slot` — a re-query with no new request returns only the chosen day | backstop, all four dispatch sites |
| `197e0bae` | the guard now ORs in `slot_accepted_by_caller` | **the primary fix** |

`197e0bae` is what prevents the round trip — 7.25 s before the caller heard
anything on that turn. The wrapper only fires where the guard does not reach
(`realtime.py`, `conversation.py`).

### Two things deliberately NOT done

* **The wrapper does not narrow `session["available_days"]`.** It did on the
  first draft. That copy outlives the turn, and B-118 records what reads it — a
  later refusal hands it straight back to the model, and
  `try_unspoken_followup_speech` answers "anything later?" out of it. A
  turn-scoped judgement written there becomes permanent.
* **The guard blocks, and two shapes leak through the resolver into it** —
  *"one in the afternoon works but earlier would be better"* and *"is three in
  the afternoon your only option"* both resolve, and neither is a settled
  choice. This is accepted, because `_presentation_for_refusal` spreads the
  **whole** day's availability into the refusal, so the model can still answer
  both correctly. The failure mode is a shorter answer, not a broken turn.

---

## 6. What the call should now test

Turn 4 of `docs/plan/CALL_SHEET_2026-09-08.md` changes meaning. Pick a slot **by
its time** and watch for two things, not one:

* **PASS** — she confirms the slot you named and asks for your name.
* **FAIL, old shape** — she reads a list of other days.
* **What to check in the log either way:**

```
[ms_conn v3] caller ACCEPTED 2026-09-...T15:00:00+01:00 - pinned ... (P6b)
[ms_llm] check_availability BLOCKED - caller is accepting an already-offered slot
```

That second line is `197e0bae` working. If instead you see

```
[ms_tools] availability re-queried after the caller had already chosen ... - narrowed
```

the guard was bypassed and the backstop caught it — worth telling me, because it
means the guard's outer conditions have a hole the corpus did not show.
