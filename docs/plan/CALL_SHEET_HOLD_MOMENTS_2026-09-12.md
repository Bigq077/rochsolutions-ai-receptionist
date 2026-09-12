# Call sheet — hold moments (no generic hold phrase)

**Build under test:** `c117c0b4` on `latency-eval` → demo line **+447366263180** (northgate).
**Not on production.** `production` = `7cc67f31`; that is also the revert target.

## Before you dial

1. Render dashboard → demo service → log shows `[build_info] running build c117c0b4`. If it shows anything else, stop — you are testing the old build.
2. Have the Render log open and tailing. Everything below is read from it.

## What you are listening for

A **head** is the first clause of Susie's reply, spoken ~0.6 s after you stop talking, with the answer joined onto it: *"Let's get you booked in — what's the appointment for?"* It should sound like one sentence.

**Instant fail — hang up and tell me — if you ever hear any of these at any point:**
"Sorry, still with you" · "Still with you" · "one sec" · "one moment" · "bear with" · "Nearly there" · "Won't be long" · "Almost got it" · "Right with you" · "Just getting that for you".

Two things are **allowed** and are not the bug: "Got that —" (only on a turn where nothing else fitted, ~2.75 s in) and "Sorry, this is taking a moment —" (only on a genuine 7–10 s stall, never first).

---

## Call 1 — the booking path (every answer-moment in order)

| # | you say | you should hear her open with | log line to find |
|---|---|---|---|
| 1 | *(after greeting)* "hi, I've got a bit of a problem with my left ankle, it's nothing serious" | **"Sorry to hear that —"** | `situational head (symptom)` |
| 2 | *(she offers to book)* "yes please" | **"Let's get you booked in —"** | `situational head (book_new)` |
| 3 | *(preference question)* "yeah anytime next week" | **"Let me look at next week for you —"** | `situational head (named_week)` |
| 4 | *(she reads slots)* "ten past twelve works" *(or whichever time she offered — say the TIME, not the day)* | **"That one works —"** — and she must NOT say the time in the head; the read-back after it names the slot | `situational head (slot_picked)` |
| 5 | *(name question)* "Quentin Roch" | **"Thank you —"** | `situational head (name_given)` |
| 6 | *(is that the best number?)* "yes it is" | **"Thanks for that —"** | `situational head (number_confirmed)` |
| 7 | *(shall I book that in / put that one in?)* "yes" | **"Right, booking you in —"** *(this one comes at ~2.75 s and is the write head, not a situational one)* | `filler phrase triggered … 'Right, booking you in` |
| 8 | let her confirm, then "thanks, bye" | *nothing extra* — a closing gets no head | `no hold phrase: the caller is closing` |

Then cancel the test booking however you normally do.

## Call 2 — the awkward moments (the ones that used to get the apology)

| # | you say | you should hear | log |
|---|---|---|---|
| 1 | *(after greeting)* "um, to book an appointment mate" | **"Let's get you booked in —"** | `situational head (book_new)` |
| 2 | *(reason question)* "my knee" | **"Sorry to hear that —"** | `situational head (symptom)` |
| 3 | *(preference)* "what have you got" | **"Let me see what we've got —"** | `situational head (avail_query)` |
| 4 | *(after the slot readout)* "anything around twelve?" | **"Let me see what I've got around twelve —"** | `situational head (time_around)` |
| 5 | *(next readout)* "that's not soon enough" | **"Not to worry —"** | `situational head (refusal)` |
| 6 | *(next readout)* "sorry, say them again" | **"Sorry about that —"** | `situational head (repeat_ask)` |
| 7 | **say nothing for ~4 s, then** "hello?" | **"Yes, I'm here —"** once. You must NOT then hear her say "still here" again on top — if you hear "I'm here … still here", that is the echo stripper failing; tell me | `situational head (check_in)` |
| 8 | "actually can you just let Priya know I'll call back tomorrow" | **"Not a problem —"** | `situational head (message_req)` |
| 9 | "thanks, bye" | nothing | — |

## Call 3 (optional, 60 s) — the fallback itself

Goal: make her have nothing to say at 0.6 s and check the 2.75 s phrase is the receipt, not the apology.

1. After the greeting say something that fits no shape, e.g. "um, so, the thing is, last Tuesday" *(a fragment)*.
2. You should hear either the answer straight away, or **"Got that —"** at ~2.75 s and then the answer.
3. Log: `[LAT] … hold=none` on that turn and, if it spoke, `filler phrase triggered … 'Got that`.

---

## After the calls — the one-line check

Every `[LAT]` line now carries `hold=<intent>` or `hold=none`. Paste me the `[LAT]` lines (or the two call SIDs) and I will score every turn in a minute. What I am looking for:

- `hold=` matches the table above on each numbered turn;
- **zero** `filler phrase triggered` lines containing "still with you";
- no turn where a head played and then nothing followed for >7 s without a `second filler phrase` line.

## If it fails

- A wrong or missing head on one turn = a classifier gap → I fix the shape and re-test; no rollback needed.
- Any banned phrase heard = something reached TTS outside the arbiter → tell me the exact words and the time; that is a rollback candidate before promotion.
- Nothing here touches production, so a failure costs one more demo call.
