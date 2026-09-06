# Defects from the three demo-line calls, 5 Sep 2026 23:05–23:14

Branch state: `origin/latency-eval` == `origin/production` == `e2c3c2c5`.
Both directions of `git log A ^B` are empty — everything is already live on all
three patient lines. The 4 Sep sheet's "hold the production push" is overtaken.

Calls:

| sid | time | script | verdict |
|---|---|---|---|
| `CAfa5b520e640e411ceeebc07eca15e85b` | 23:05:31 | Call 1 — D-B, "check for Tuesday" + press 1 | **PASS** |
| `CAa0389cae74d3ba76e220ab0280972101` | 23:09:39 | Call 2 — "yeah Monday works" | accepted, then **B-145** |
| `CA5c65cb4b83091a538c915f5b234a4e8e` | 23:13:00 | Calls 3/4/5 — pick a time, "what else", full booking | picks and more-slots **PASS**, **B-146** |

## What passed

**Call 1 — D-B works end to end.**

```
23:06:24.787 [slot_followup] 'what about Tuesday 8th September' answered from
             the payload -- 3 of 11 bookable times spoken, offer and keypad
             recorded, no tool call needed (D-B)
23:06:24.790 slot map active — time_selection: {'1': 'ten to nine in the
             morning', '2': 'one in the afternoon', '3': 'ten past five'}
23:06:31.932 slot DTMF digit='1' → injecting 'ten to nine in the morning'
23:06:31.936 caller ACCEPTED 2026-09-08T08:50:00+01:00
```

Three times not two, the tail *"And I've a few others"* spoken, keypad
renumbered to TIMES, and `1` selected the first time — not Monday.

**Call 2's acceptance** — `situational head (slot_picked): 'Monday it is —'`,
no re-read of the days. `74ad7c73` holds.

**Call 3 (time pick)** — `'oh yeah thursday at half past 6 works'` →
`caller ACCEPTED 2026-09-10T18:30:00+01:00` → `'Thursday it is —'`. Holds.

**Call 4 (more slots)** — `[slot_followup] 'what else' answered with 3 day(s)
he has not heard`. Holds.

---

## B-145 — P1. A day ACCEPTANCE leaves no offer on the table

**One root cause, three symptoms, all in call 2.** The owner spotted all three.

After `'Monday it is —'` the model — not a producer — narrowed the day in prose:

```
23:10:14  "that day I've got eight in the morning or ten past five in the evening"
23:10:14  slot map active — day_selection: {'1': 'Monday 7th September', ...}
```

Monday's payload held **twelve** times (`08:00 … 17:10`). Two were spoken.

`named_day_speech` — the D-B producer that got call 1 right — **deliberately
declines an acceptance** (`slot_followup.py:4185`):

```python
# And a caller ACCEPTING a day has picked too. "yeah monday works" is
# not "what about monday" ... Intercepting it here would be a REGRESSION
# ... This producer answers REQUESTS. What happens after an acceptance is
# already settled and is not ours to change.
if day_accepted_by_caller(session, user_text):
    return None
```

Declining the *readout* was right. But **nothing else builds the single-day
offer**, so after an acceptance:

1. **2 of 12 times spoken** — the model answers from the two Monday slots it had
   already read out in the multi_day offer;
2. **no *"and I've a few others that day"* tail** — that tail is
   `single_day`-only by construction (`slot_offer.py:445`), and no single_day
   offer was ever built;
3. **the keypad stays on DAYS** — pressing `1` there re-picks Monday, not a time.

### The same root then broke the next turn's filler

```
23:10:24  'um 10 past 5 in the evening suits'
23:10:25  situational head (time_band): "Let me see what I've got in the evening —"
23:10:26  "so that's Monday the 7th of September at ten past five in the evening"
```

She promised a lookup and then confirmed. This is the *exact* defect the 3 Sep
fix at `slot_followup.py:2303` was written for — its comment quotes the same
sentence off the 13:07 call. It only fires when the offer holds **one** date:

```python
if len(_dates) == 1:
```

Its comment assumes *"the shape after Susie narrows"* leaves a single-day offer.
It does not — the narrowing was prose. Three dates were still on the offer, the
resolver declined, `_hs_picking` stayed False (`llm_stream.py:4939-4970`), and
the TIME_BAND diary head fired.

**Fix direction:** after `day_accepted_by_caller` resolves, build the
deterministic single-day offer for that day and let it follow the head —
`build_slot_offer` + `apply_offer_to_session`, the same two functions call 1
used. `'Monday it is —'` survives; what changes is that the times behind it come
from a producer instead of the model. That closes all four symptoms at once,
because it puts the state at `2303` into the shape its own comment assumes.

Do **not** fix this by making the resolver guess across three dates — that is
the deny-by-default guard, and B-138 is what happens when it is relaxed.

---

## B-146 — P2. A booking request that never says "book" gets an apology

Call 5, opening turn:

```
23:13:07  'yeah can i have a good sports massage please'
23:13:07  treatment mention (FAQ, no booking intent) — v3_treatment_mentioned
          set, booking_flow_active left False
23:13:10  filler phrase triggered: 'Sorry, still with you —'      ← no head
23:13:13  LAT turn_seq=19 llm_ttft_ms=4606 content_ttfa_ms=5328
```

`Intent.BOOK_NEW` (`hold_speech.py:619`) triggers on
`\b(?:book|booking|appointment)\b`. The caller named a **service** and a want
verb and never said "book", so `classify_intent` returned `[]`, nothing fired at
600ms, the LLM took 4.6s, and `UNKNOWN_SLOW` apologised for a wait on the
caller's very first sentence.

This is the matcher-shape lesson for the third time in this file — the same one
`_HURT`/SYMPTOM and the screening bigrams already carry. Adding "massage" to the
trigger is the trap: the corroborator `_WANT` is already right, and the missing
half is that **naming a service the clinic sells is a booking request**. The
engine already decided that one line earlier and wrote it to the session
(`v3_treatment_mentioned`); `hold_speech` cannot see it.

Note the classification itself is also arguable — *"can I have a sports massage
please"* is a booking request, not an FAQ — but that is a separate call to make
and it changes `booking_flow_active`, which reaches the write gates. Head first.

---

## Secondary, from the same logs

- **STT: `'the 30-minute session'` → `'the 5-minute session'`** (23:13:22). She
  recovered by re-asking the 30/60 question, at the cost of a second
  `'Still with you —'` and ~10s. The keyterm list carries **no numerals** — same
  100-term call-scoped list as §2.6 of the 3 Sep register. `'quentin rock'` →
  `'quite generous'` again at 23:06:45.
- **Reason `None` reached the record on call 5** —
  `pre-summary reason: collected=None session=None`. Northgate never asked, and
  the service name is not a reason. Harmless on the demo line; on JV that is the
  A2 gate (`4fc3676b`'s subject).
- **B-31 fires 3–4 times per call** on both calls, recovering via
  `last_question` each time. Known, §2.7.
- **Readout length**: 17.0s, 18.9s and 16.8s for the three-day readouts, and
  both callers barged in on nearly every turn. Known, §2.8.
- Sheets `GOOGLE_SERVICE_ACCOUNT_JSON` invalid and ElevenLabs 401 on
  `/v1/models` — both known-accepted on the demo line.
