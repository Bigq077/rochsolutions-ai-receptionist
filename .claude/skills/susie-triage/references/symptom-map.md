# Symptom map — check name → flow position → subsystem

Every gating check produced by `tests/auto/evaluator.py`, in the order a call
reaches it, with the subsystem that produces the behaviour it measures.

Two uses:

1. **Ordering.** The "earliest failing check" rule is only mechanical if the
   order is written down. That is the `rank` column.
2. **Attribution.** Once a cluster has an earliest check, the subsystem column
   is the *first place to look* — a hypothesis for `susie-debug`, not a verdict.

The machine-readable copy of the rank column lives in
`../scripts/collect_failures.py` (`FLOW_ORDER`). **Keep the two in sync** — if
you add a check to `evaluator.py`, add it to both.

---

## How rank works

| Band | Rank | Meaning |
|---|---|---|
| Positional | 10–72 | The check can only fail at one point in the call. This *is* a location. |
| Terminal | 80–82 | Fails whenever anything upstream failed. Never a location on its own. |
| Cross-cutting | 90 | Can fail anywhere. Never a location on its own. |
| Unknown | 95 | Check not in the table. Add it. |

A call whose only failing checks are terminal or cross-cutting is marked
`cascade-only` by the collector. For those, localise from the **stall point** —
`flow_step` at end of call, plus the last thing Susie said — not from the check.

---

## Connection and greeting (rank 10–12)

| Check | Rank | Means | Subsystem |
|---|---|---|---|
| `answered_in_time` | 10 | Call answered and Susie spoke at all | `app/routes/twilio.py` (webhook/TwiML), `app/media_streams/connection.py` (WS handshake) |
| `greeting_has_<phrase>` | 11 | Greeting contains a required phrase | greeting template — `app/media_streams/config.py`, `app/phrases.py`, clinic `clinic.json` |
| `greeting_no_<phrase>` | 11 | Greeting leaks something it shouldn't (clinic names, "say one") | same, plus the two-clinic guard |
| `first_turn_contains` / `first_turn_no_<phrase>` | 12 | Same, scoped to turn 1 only | same |

A greeting failure is nearly always **config, not engine** — check `clinic.json`
before opening any Python file.

## Caller audio reaching Susie (rank 20–21)

| Check | Rank | Means | Subsystem |
|---|---|---|---|
| `susie_responded_to_patient` | 20 | Scenario had responses but Susie produced only the greeting | `stt_stream.py`, `utterance_router.py`, `connection.py` audio frame path |
| `flow_continues` | 21 | Fewer than 2 turns, or the call died on silence | same, plus `app/silence_handler.py` |

These two are the **most severe** class in the whole table: the caller spoke and
Susie did not hear them. Everything downstream is meaningless when one fails.

## Silence / re-ask ladder (rank 30–35)

| Check | Rank | Means | Subsystem |
|---|---|---|---|
| `reask_fired` | 30 | First silence produced "didn't quite catch" | `app/silence_handler.py`, `connection.py` |
| `reask_phrase_correct` | 31 | The re-ask used the expected wording | `app/phrases.py`, `app/media_streams/config.py` |
| `second_reask_fired` | 32 | Second silence produced "sorry about that" | `connection.py`, `flow.py` |
| `second_reask_phrase_correct` | 33 | Wording of the second re-ask | `app/phrases.py` |
| `transfer_played` / `transfer_has_<phrase>` | 34–35 | Third silence escalated to a transfer | `connection.py`, `app/routes/twilio.py` |

**Beware:** a single "didn't quite catch" in a transcript is normal in the test
harness — injected caller audio often misses the first endpoint. A *repeating*
re-ask on the same question is the real signal.

## Clinic disambiguation (rank 40–41) — two-clinic tenants only

| Check | Rank | Means | Subsystem |
|---|---|---|---|
| `asked_which_clinic` | 40 | Susie asked which site before acting | `flow.py` location gate, `clinic.json` |
| `location_not_asked` | 41 | Susie wrongly asked during an FAQ call | same |

## Intake (rank 50–54)

| Check | Rank | Means | Subsystem |
|---|---|---|---|
| `new_or_returning_correct` | 50 | New vs returning classified right | `flow.py` `BOOKING_FLOW` step 0, `app/media_streams/session.py` |
| `empathy_response_present` | 51 | Condition acknowledged before moving on | `app/prompts/` |
| `empathy_contains_condition` | 52 | Acknowledgement named the actual condition | `app/prompts/`, `llm_stream.py` |
| `duration_question_asked` | 53 | "How long have you had it" — **no longer in the standard flow**; a failure here usually means a stale scenario | scenario, not Susie |
| `offered_booking` | 54 | After an FAQ, Susie offered to book | `flow.py`, `fast_path.py` |

## Identity collection (rank 60–61)

| Check | Rank | Means | Subsystem |
|---|---|---|---|
| `asked_for_name` | 60 | Full name requested | `flow.py` `COLLECT_NAME`, `app/media_streams/name_collector.py` |
| `number_confirmed_verbally` | 61 | Phone number read back to the caller | `fast_path.py` (`_fmt_phone`), `flow.py` `CONFIRM_PHONE` |

## Availability and slot selection (rank 70–72)

| Check | Rank | Means | Subsystem |
|---|---|---|---|
| `asked_for_availability` | 70 | Days/times asked | `flow.py` `PRESENT_DAYS` |
| `slot_confirmed` | 71 | Chosen slot read back before moving on | `flow.py` `PRESENT_TIMES`, `app/tools/slots.py`, `receptionist_tools.py` |
| `confirmation_contains` | 72 | Read-back named the slot the caller picked | same |

`slot_confirmed` failing with `selected_slot=None` in the record means **no slot
was ever selected** — the caller's choice was never matched. That is a selection
bug (or an ASR bug at that turn), not a read-back bug.

## Terminal (rank 80–82) — never a location

| Check | Rank | Means | Subsystem |
|---|---|---|---|
| `booking_confirmed` | 80 | `flow.py` set the session field after the booking tool confirmed | `receptionist_tools.py` → `app/booking/booking/providers/acuity.py` |
| `reschedule_confirmed` | 80 | Reschedule intent confirmed | same |
| `cancel_confirmed` | 80 | Cancellation confirmed | same |
| `flow_completed` | 81 | Call ended cleanly with enough progress | `connection.py`, Twilio status callback |
| `graceful_end` | 82 | Early hang-up handled cleanly | `flow.py`, `connection.py` |

`booking_confirmed` is read from the **session field**, not the transcript
(`evaluator.py`, `_rule_checks`). So it is authoritative about whether the
booking happened — and completely silent about *why* it didn't.

**This is the single most important row in the table.** `booking_confirmed`
will be the largest cluster in almost every run and it means nothing on its own.
Split it by stall point before reporting it.

## Cross-cutting (rank 90) — never a location

| Check | Means | Subsystem |
|---|---|---|
| `flow_order_correct` | Questions asked in the documented order | `flow.py` `BOOKING_FLOW` |
| `no_question_asked_twice` | A question repeated (excluding silence re-asks) | `flow.py` state advance; often a **symptom of the re-ask loop**, not a separate bug |
| `no_state_corruption` | Flow resumed correctly after interrupt/silence/FAQ | `flow.py` `_handle_mid_flow_interrupt` |
| `not_said_<phrase>` | A banned phrase appeared | `app/prompts/` |
| `no_technical_error` | Susie said "technical issue" / "I apologise" | any `except` path; see the broad-exception hazard |
| `no_dead_air` | Gap over the scenario's limit | `tts_stream.py`, `llm_stream.py`, provider latency |
| `no_crash` | `end_reason == "error"` | anywhere |

`no_question_asked_twice` and `no_state_corruption` are judged by Claude in
`evaluator.py`, so a handful of false positives per run is expected. Two or
three scattered instances are noise. A phase-wide pattern is not.

---

## `flow_step` → state

`flow_step` in a result record is the index into `BOOKING_FLOW` in
`app/media_streams/flow.py`. **Read that list on the branch under test** — the
step numbering has changed before and will change again. On
`jv-v1-onboarding` as of 2026-09-14 the booking flow runs:

```
0 NEW_OR_RETURNING      5 RETURNING_PLAN_LOOKUP   10 COLLECT_NAME      15 CONFIRM_BOOKING
1 RETURNING_RECENCY     6 COLLECT_REASON          11 CONFIRM_PHONE
2 RETURNING_TREATMENT   7 COLLECT_NAME_RETURNING  12 COLLECT_PHONE
3 RETURNING_PLAN_PHONE  8 CONFIRM_PHONE_RETURNING 13 PRESENT_DAYS
4 RETURNING_PLAN_COLLECT 9 COLLECT_PHONE_RETURNING 14 PRESENT_TIMES
```

Reschedule and cancel have their own shorter flows with their own numbering.
A step number alone is therefore ambiguous — always corroborate with the last
Susie turn, which the collector prints for you.

---

## `turn_traces` — the strongest signal in the record

Each result record carries `turn_traces`, one entry per caller utterance:

| Field | Use |
|---|---|
| `user_text_raw` / `user_text_normalized` | What Susie actually received. **If this is populated, the caller was heard** — any "didn't quite catch" after it is a matching failure, not an audio failure. |
| `state_before` → `state_after` | Whether the turn advanced the flow. Equal values mean the turn was consumed for nothing. |
| `handled_by` | Which handler claimed the turn. A handler claiming a turn in a state it does not own is a bug you can name without reading any logic. |
| `yes_detected`, `no_detected`, `ordinal_detected`, `extracted_name`, `extracted_phone` | What was parsed out. A `None` here next to a clear utterance localises the matcher. |
| `booking_confirmed_before/after`, `error_if_any` | Where the booking actually happened, or didn't. |

The collector turns this into the stall table. Two rules that follow from it:

- **"Heard but not understood" ≠ "not heard".** Repeated re-asks with populated
  `user_text_raw` are a flow/matcher defect. Repeated re-asks with empty
  utterances are an audio/STT defect. They live in different subsystems and the
  transcript alone cannot tell them apart.
- **Count non-advancing turns in passing calls too** before calling one a cause.
  A state that consumes a turn without advancing in 90% of *passing* calls is
  normal load, not the defect — the defect is whatever cannot absorb it.
