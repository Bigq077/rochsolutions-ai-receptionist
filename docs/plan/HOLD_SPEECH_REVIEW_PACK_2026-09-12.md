# Everything Susie says that is not an answer — for a practitioner to hear

**Rendered from the code at build `dfaa0b02`** (live on all four lines from
12 Sep 2026), not copied from an earlier document. Sources:
`app/hold_speech.py` (`INTENT_HEADS`, `HEADS`), `app/media_streams/llm_stream.py`
(`FILLER_PHRASES`), `app/media_streams/connection.py` (`_BARGE_IN_ACKS`, the
hearing-trouble line), `audio_clips/CLIPS.json` (the recorded clip).

**The ask.** One question per line: *would your front desk say this?* Mark
each ✅ / ✏️ (with your wording) / ❌. Nothing else is needed. Wording comes
back through Quentin; three rules apply to any replacement, listed at the end.

**How a head works.** It is not a filler played in front of the answer. It is
the first clause *of* the answer — the reply is joined onto it so the caller
hears one sentence:

> **"In regards to insurance —** yes, we accept private health insurance referrals."

Two variants per situation, so a caller who asks two questions never hears the
same construction twice. `{subject}` is filled from what the caller said.

---

## 1. When the caller asks a question — no lookup happens

| the caller asks about | Susie opens with |
|---|---|
| price | "In terms of pricing —" · "So, on our prices —" |
| insurance | "In regards to insurance —" · "As for insurance —" |
| opening hours | "In terms of our opening hours —" · "So, on our hours —" |
| parking | "In regards to parking —" · "As for parking —" |
| where you are | "In terms of where we are —" · "So, on where we're based —" |
| what you treat | "In regards to what we treat —" · "As for what we cover —" |
| a first visit | "For your first visit —" · "So, on your first appointment —" |
| who they'd see | "In terms of who you'd see —" · "As for who you'd be seeing —" |

## 2. The human moments

| the caller | Susie opens with |
|---|---|
| describes a symptom or injury | "Sorry to hear that —" · "Oh, sorry to hear that —" |
| wants to cancel | "No problem at all —" · "Yes, no problem —" |
| wants to move an appointment | "Let's get that moved for you —" · "Yes, let's get that moved —" |
| picks a time | "{Monday at ten} it is —" · "That one works —" |
| asks her to repeat | "Sorry about that —" · "Apologies for that —" |
| asks to be put through | "Not a problem —" · "Yes, not a problem —" |
| wants to book | "Let's get you booked in —" · "Yes, let's get that sorted —" |

## 3. While she looks in the diary — the caller named what to look at

| the caller asked for | Susie opens with |
|---|---|
| a day ("what about Saturday") | "Let me see what {Saturday} looks like —" · "Let me have a look at {Saturday} for you —" · "Let me see —" |
| a week ("the week after") | "Let me look at {the week after} for you —" · "Let me see what {the week after} looks like —" · "Let me see —" |
| a time of day ("any afternoons") | "Let me see what I've got in the {afternoon} —" · "Let me have a look at the {afternoon}s for you —" · "Let me see —" |
| a session length | "Let me see where a {sixty-minute} session fits —" · "Let me look for a {sixty-minute} for you —" · "Let me see —" |
| the soonest | "Let me find the soonest I've got —" · "Let me see what the earliest is —" |
| anything at all | "Let me see what we've got —" · "Let me have a look for you —" |

## 4. While the system works — by what it is actually doing

| what is happening | Susie says |
|---|---|
| reading the diary | "Let me see —" · "Right, let's see —" · "Let me have a look —" · "Okay, one sec —" |
| looking the patient up | "Let me find you —" · "Right, pulling you up —" · "Let me look you up —" |
| writing the booking | "Right, booking you in —" · "Popping that in for you —" · "Getting that in the diary —" |
| moving an appointment | "Moving that across —" · "Right, shifting that —" · "Getting that changed —" |
| cancelling | "Taking care of that —" · "Right, sorting that —" · "Getting that sorted —" |
| passing a request to the clinic | "Sending that over to {Marcus} —" · "Putting that request in —" · "Passing that to {Marcus} —" |

## 5. When the wait is genuinely long

These play only when nothing has been said for a while and the system is
still working. Never the same one twice in a turn.

| | Susie says |
|---|---|
| the recorded clip (her own voice, pre-cut) | "Let me just check that for you…" |
| a filler while a lookup runs | "Just getting that for you…" · "Right with you…" · "One moment…" · "Let me just check that…" |
| a stall with nothing to report | "Sorry, still with you —" · "Still with you —" |

## 6. When the line is difficult

| | Susie says |
|---|---|
| the caller spoke over her and she stopped | "Sorry — go ahead." · "Yes, go on." · "Sorry about that — you were saying?" |
| she could not make the caller out | "Sorry — I'm having a little trouble hearing you. Could you say that again?" |

---

## What a replacement has to satisfy

The code checks these when it starts, and refuses a phrase that breaks one:

1. **A head ends in a dash, never a full stop or "…".** The voice renders a
   full stop as a falling tone and a pause — which is the canned-recording
   sound this whole design exists to avoid. (Section 5's fillers are the
   exception: they are complete sentences on purpose, and play alone.)
2. **A question head (section 1) must not promise a lookup.** "So, on where to
   *find* us —" was rejected by the code for the word *find*: nothing is being
   found, the answer is already known.
3. **The two variants of a situation must not be the same sentence in
   different words.** A caller who hears both in one call should hear two
   different things.
