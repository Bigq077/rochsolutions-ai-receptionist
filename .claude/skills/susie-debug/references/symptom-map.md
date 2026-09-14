# Symptom map — shared with `susie-triage`

**The canonical copy lives at
`.claude/skills/susie-triage/references/symptom-map.md`.** Read it there. It maps
every gating check in `tests/auto/evaluator.py` to its flow position and the
subsystem that produces it, and explains how to read `turn_traces`.

It is not duplicated here on purpose. Two copies of a 36-row mapping drift, and a
drifted symptom map sends a debugging agent to the wrong module with confidence.
The machine-readable ordering lives in one place too:
`.claude/skills/susie-triage/scripts/collect_failures.py` (`FLOW_ORDER`).

---

## What debugging needs on top of triage

Triage asks *which cluster is this?* Debugging asks *which line?* Three
differences in how you read the same map.

### 1. The earliest failing check is a starting point, not the bug

Triage stops at "the earliest failing check is `slot_confirmed`". Debugging must
then decide **which of three things** produced it:

| | Evidence | Goes to |
|---|---|---|
| Susie never heard the turn | `user_text_raw` empty in `turn_traces` | `stt_stream.py`, audio path |
| Heard, not matched | `user_text_raw` populated, `state_before == state_after` | `flow.py` matcher for that state |
| Matched, not acted on | state advanced but `selected_slot`/`extracted_*` still null | the extractor, or the tool layer |

The check name alone cannot separate these. `turn_traces` can, in one look.

### 2. `handled_by` names the code that ate the turn

This is the fastest localisation signal in the record. It tells you which handler
claimed the utterance — and a handler claiming a turn in a state it does not own
is a bug you can name before reading any logic.

A worked example from `docs/triage/TRIAGE_2026-04-07.md`: `COLLECT_NAME` turns
showing `handled_by=slot_ordinal_selection`. The slot handler was running in a
name state. No reading of `flow.py` was needed to know that is wrong.

### 3. Cross-cutting checks are where the *class* hides

Triage deliberately refuses to treat `no_question_asked_twice` and
`no_state_corruption` as locations, because they can fail anywhere. For debugging
they are the opposite — once you have localised a defect, ask whether the
cross-cutting check was failing **for the same reason** across other scenarios.
That is how you kill the class rather than the instance.

---

## Mapping a check to the code, not just the module

Once the symptom map gives you a subsystem, these get you to the symbol:

```bash
# what produced this exact check
grep -n "<check_name>" tests/auto/evaluator.py

# what the flow does at the state you stalled in
grep -n "\"state\": \"<STATE>\"" app/media_streams/flow.py

# the handler that claimed the turn
grep -rn "<handled_by value>" app/ | grep -v __pycache__

# has this been fixed before
git log -S"<symbol>" --oneline
git log -i --grep="<state or symptom>" --pretty='%ad %h %s' --date=short
```

`flow_step` is an index into `BOOKING_FLOW` in `app/media_streams/flow.py`, and
the numbering **has changed and will change again**. Read the list on the branch
under test; never carry a step number between branches. Reschedule and cancel
have their own shorter flows with their own numbering, so a bare step number is
ambiguous — corroborate with the state name from `turn_traces`.
