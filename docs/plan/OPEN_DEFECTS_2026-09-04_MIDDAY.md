# Call record — CAdd64c466dc13978306e5558817ce147e, 4 Sep 2026 11:33

northgate, 197s, 28 turns, judge score 2, `outcome=reached_confirmation`.
**The caller hung up at "shall I go ahead and book that in?". Nothing booked.**

> ⚠️ **Second call in a row to end exactly there.** CA9c39d09f (07:47) did the
> same: 246s, `reached_confirmation`, no booking, hang-up on the final
> confirmation question. Two for two. See §7 — this is now the most valuable
> open question on the list and it is not one of the defects below.

**Branch state at close:** `latency-eval` = `3b88d9d4` (B-134 reverted).

---

## 1. VERIFIED on this call

| | |
|---|---|
| **B-136 log fix** | `scripted response … "I'm sorry to hear that…" (in reply to: "um yeah hi i'd like to book…")` — both named, was previously the caller's words under the response label |
| **screen asked ONCE** | `screen trauma_fracture clear: "uh no it's fine nothing too serious"` at the first attempt |
| **P6b pin** | `pinned the accepted slot back into the readout -- 2026-09-09T09:40:00 was heard, so B-116 had dropped it` |
| **slot LLM skip** | `slot LLM call SKIPPED — deterministic offer already built` |
| **multi_day producer** | `deterministic multi_day offer built` twice |

## 2. NOT verified — do not record these as passed

* **B-135** was **not exercised**. The screen was answered 4.5s after the
  question with no intervening bot turn, so the answer window never had a
  chance to close. The outcome was right; the fix was not tested.
* **B-133** fired and hit the cap again (`WATCHDOG_VOICE_HOLD_CAP`, once).
  Two calls, two caps, and on both the caller genuinely was not speaking —
  it re-asked ~5s before they answered. Not harmful, watchdog always arrived,
  but **it has never yet prevented a wrong re-ask.** Value still unproven.
* **The surname counter** never ran — `book_appointment` was never reached.

---

## 3. 🔴 REGRESSION — B-134, REVERTED in `3b88d9d4`

```
11:34:28.710  B-134: recorded 2026-09-10T12:10        (THURSDAY)
11:34:44.936  FINAL: 'um do you have any do you have a 10 past 12 for
                      wednesday for example'
11:34:44.951  caller ACCEPTED 2026-09-10T12:10:00+01:00    <- THURSDAY
```

A QUESTION about Wednesday resolved as an ACCEPTANCE of Thursday.

**The real bug is in the resolver, and B-134 only made it reachable.**
`slot_accepted_by_caller`'s last-resort branch fires on `len(_dates) == 1`:

> *"this only fires when the offer holds exactly ONE date, so it cannot guess
> between days"*

That premise is false when the caller **names a day the offer does not hold**.
There is no check for it. Before B-134 the record held three dates, so the
branch was unreachable.

**Fix when this is re-approached: the day check belongs in that fallback**, not
in B-134. It is wrong today, independently, and any future single-day record
re-exposes it.

### 3b. Second flaw, also B-134's: no notion of NEGATION

```
model: "Wednesday 9th September doesn't have ten past twelve available — but
        it does have ten to nine in the morning, twenty to ten…"
11:34:50.239  B-134: recorded ['2026-09-09T12:10:00']
```

`payload_slots_named_in` matched the label inside a negative clause, so a time
the caller had just been told was unavailable became acceptable in
`last_offered_slots`.

> Note the model was also WRONG: Wednesday's payload contains `12:10`. It
> claimed a real slot did not exist. That is a separate model defect.

---

## 4. 🔴 P6b has the same divergence P6 had — OPEN

B-134 addressed the P6 branch only. P6b stands down for a different reason and
leaves the record untouched in exactly the same way:

```
11:35:15.207  deterministic offer STOOD DOWN — the model names the slot the
              caller just accepted … (P6b)
              model='Wednesday 9th September — Number 1, twenty to ten in the
                     morning. Number 2, half past ten. Number 3, twenty past
                     eleven…'
11:35:27.835  FINAL: 'yeah 20 past 11 works'
              <- NO caller ACCEPTED line
```

She read out **three numbered times**; the record held one. The caller picked
Number 3 and nothing resolved. She recovered by read-back, but the pick never
landed.

**P6b is arguably the stronger case for recording**, because it speaks a
NUMBERED list — the caller can press a digit that resolves against a record
that does not contain it.

---

## 5. 🔴 The reason was `None` for the whole call — B-136 is PARTIAL

No `[first_turn]` line appears anywhere. At close:

```
pre-SMS reason: collected=None session=None → None
Row built — outcome=reached_confirmation name=Quentin Rook phone=yes dur=197s
```

The complaint was in the OPENING utterance — *"i'd like to book an appointment
essentially i was playing football um and i rolled my ankle"* — which armed the
screen and was consumed by the `ask_screen` short-circuit.

B-136 added `commit_reason_answer` to that path. That helper only fires when
the reason QUESTION was asked and its flag armed. **On this call Susie never
asked it** — the caller volunteered everything — so the opening path
(`commit_opening_reason` / `apply_first_turn_signals`) was the one needed, and
it is still not called there.

Same root cause, different helper. The previous call needed one; this call
needed the other.

---

## 6. Smaller, all anchored

* **A keyterm actively corrupted STT.** `'um do you have any do you have a
  temperature'` for "10 past 12". `temperature` is one of the 100 keyterms —
  the module's own comment says common words are wasted slots; here it was
  worse than wasted.
* **Name capture failed a third time.** `"oh yeah that'll be quite"` for
  "Quentin Rook". Recovered on the second attempt via a re-ask.
* **The keypad never moved off the original three days.** From `11:34:02` to
  the final read-back it stayed `{'1': Monday, '2': Tuesday, '3': Wednesday}`
  while three separate Wednesday time-lists were read out. Pressing 1 at any
  point would have selected Monday.
* **Latency.** `content_ttfa_ms=9114` on the offer turn (cold cache),
  `chunk_gate_ms` 3624 and 3892 on two others; the 3-day readout ran **16.3s**.
* **A prior 18-second call** flushed at `11:33:21` as
  `outcome=abandoned name=None dur=18s` — worth a glance, it is not this call.

---

## 7. The question worth more than any of the above

**Two consecutive calls reached `shall I go ahead and book that in?` and the
caller hung up.** Both `reached_confirmation`, both ~200s+, neither booked.

Nothing in §3–§6 explains that. The read-back on this call was correct —
`"so that's Quentin Rook, Wednesday the 9th of September at tw…"` — and the
caller left anyway. Before spending more on the slot layer, that is the thing
to understand: what does the caller hear between the phone confirmation and the
booking question, and how long does it take? On this call, from the name
request to the final question was **63 seconds**.

---

## 8. Ordered, for whoever picks this up

1. **The resolver's one-date fallback** (§3). Wrong today, cheap to fix, and it
   gates any future attempt at B-134.
2. **The two-hang-up pattern** (§7). Measurement before code.
3. **P6b** (§4), then B-134 rebuilt on top of 1.
4. **The opening-reason path** (§5) — completes B-136.
5. Keyterms (§6) — a wav now exists in `logs/audio/`, so §2.6 is unblocked.
