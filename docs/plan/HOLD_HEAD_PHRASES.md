# Every hold phrase Susie can say, and why

**For review.** This is the complete set after the rewording of 2026-08-29,
prompted by the first live call to hear one (`CAe9eba9192c50c500c95b7e7a2a187729`,
Northgate): *"spoke too quickly compared to how Susie speaks"* and *"lacks the
human feel — a receptionist doesn't say 'on insurance', she'd say 'in regards to
insurance'."*

The phrases live in `app/hold_speech.py` → `INTENT_HEADS`. This document is a
reading copy; **the code is the source of truth**, and an import-time check
rejects a head that breaks any of the rules at the bottom.

---

## How to read a row

A head is **not** a filler played in front of the answer. It is the opening
clause **of** the answer — the reply is decapitalised and joined onto it, so the
caller hears one sentence:

> **"In regards to insurance —** yes, we accept private health insurance
> referrals."

That is why every head ends in a dash and never in a full stop or an ellipsis:
ElevenLabs renders a terminal ellipsis as a falling contour plus a pause, and
that contour *is* the canned-filler sound.

Two variants per situation. A caller who asks two questions does not hear the
same construction twice.

---

## 1. Topic heads — questions, where no tool runs at all

These name the topic and nothing else, so they cannot promise a lookup. This is
the group that was reworded: *"On price —"* reads like an index entry, not a
person.

| the caller asks about | Susie opens with | then answers |
|---|---|---|
| **price** | "In terms of pricing —" / "So, on our prices —" | *…an initial assessment is fifty-eight pounds for forty-five minutes.* |
| **insurance** | "In regards to insurance —" / "As for insurance —" | *…yes, we accept private health insurance referrals.* |
| **opening hours** | "In terms of our opening hours —" / "So, on our hours —" | *…we offer evening appointments Monday to Friday and Saturday mornings.* |
| **parking** | "In regards to parking —" / "As for parking —" | *…there are around eighty spaces at the leisure centre.* |
| **where you are** | "In terms of where we are —" / "So, on where we're based —" | |
| **what you treat** | "In regards to what we treat —" / "As for what we cover —" | *…musculoskeletal physio, neuro rehab, sports injuries…* |
| **a first visit** | "For your first visit —" / "So, on your first appointment —" | |
| **who they'd see** | "In terms of who you'd see —" / "As for who you'd be seeing —" | |

> "So, on where to find us —" was written first and **rejected by the code**:
> the word *find* trips the guard that stops a topic head claiming a lookup.
> Reworded rather than the guard weakened.

## 2. Register heads — the human moments

These were **not** reworded, and deliberately so: every one is already the
model's own wording, lifted verbatim from stored calls. They are what a person
says because a person said them.

| the caller | Susie opens with |
|---|---|
| describes **pain or an injury** | "Sorry to hear that —" / "Oh, sorry to hear that —" |
| wants to **cancel** | "No problem at all —" / "Yes, no problem —" |
| wants to **move** an appointment | "Let's get that moved for you —" / "Yes, let's get that moved —" |
| says **you misheard me** | "Sorry about that —" / "Apologies for that —" |
| asks for **a person / a callback** | "Not a problem —" / "Yes, not a problem —" |

> "Apologies —" was written first and **rejected by the code**: a one-word head
> is a bare discourse marker, and those failed on live calls in three separate
> ways.

## 3. Diary heads — where a lookup really is happening

These name back the caller's own words. `{day}` etc. is only ever something they
actually said; if they named nothing, the third phrase is used and nothing is
invented.

| the caller says | Susie opens with |
|---|---|
| **a day** — "would you have next Saturday" | "Let me see what **Saturday** looks like —" / "Let me have a look at **Saturday** for you —" |
| **a week** — "the week after that" | "Let me look at **the week after** for you —" / "Let me see what **the week after** looks like —" |
| **a time of day** — "afternoons" | "Let me see what I've got in the **afternoon** —" / "Let me have a look at the **afternoon**s for you —" |
| **a length** — "a 60-minute session" | "Let me see where a **sixty-minute** session fits —" / "Let me look for a **sixty-minute** for you —" |
| **soonest possible** | "Let me find the soonest I've got —" / "Let me see what the earliest is —" |
| **anything free?** | "Let me see what we've got —" / "Let me have a look for you —" |
| **wants to book**, nothing specified | "Let's get you booked in —" / "Yes, let's get that sorted —" |
| named nothing at all | "Let me see —" |

---

## When Susie says nothing

Silence is the default, and **71% of caller turns still get it** — exactly as
before this work. A head is spoken only where one of the situations above is
positively recognised. Three cases are silenced outright:

1. **Answering a clinical screen.** Checked two ways — what Susie asked last,
   *and* the session's own flag. A hold phrase in front of a red-flag answer is
   the worst place in the call to guess.
2. **Answering a confirm question.** "Five in the evening" after *"did you
   mean…?"* is a selection, not a request, so no diary head. An apology or
   sympathy still fires — being misheard during a readback is exactly when one
   is owed.
3. **A bare answer** — "yes", "no", a name, a phone number. Nothing for a head
   to stand in front of.

---

## Delivery

Heads are now synthesised at **0.88** rather than the call default of 1.0
(`ELEVENLABS_HEAD_SPEED`, overridable from the Render dashboard without a
deploy). A head is a short fragment synthesised on its own, seconds before the
reply, so ElevenLabs has no sentence to pace it against and rushes it.

This costs nothing: the head fills a wait that is already happening. On the call
that prompted the change, the first turn's time-to-first-audio was **5.4
seconds** — the head covered nearly five seconds of silence.

The phone-number rate (0.8) is deliberately slower still; that is careful
articulation the caller checks digit by digit. A head is ordinary conversation.

---

## The rules the code enforces at import

A head that breaks any of these fails at startup, in CI, rather than reaching a
caller:

1. **Ends open** — a dash or comma, never a full stop, never an ellipsis.
2. **Survives the gates that police model speech.** A phrase the engine deletes
   from the model must not be spoken by the engine. *This rejected "Of course —",
   which Gate 5b strips.*
3. **At least two words.** A bare marker is not a head.
4. **A topic head names no work** — it stands in front of an answer, not a lookup.
5. **Every pool that can name something also has a phrase that names nothing**,
   so a caller who specified nothing never hears a gap where the word should be.
6. **A provisional clinic never claims a write.**

## Changing a phrase

Edit `INTENT_HEADS` in `app/hold_speech.py`. Then:

```bash
python -m scripts.replay_situational_heads --sentences 40
```

That replays all 733 stored calls and prints the new phrase joined onto the
**real** replies that followed, so the wording can be read in context before
anyone rings the clinic.
