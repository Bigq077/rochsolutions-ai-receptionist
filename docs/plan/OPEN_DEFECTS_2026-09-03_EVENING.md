# Two defects from the verification call — 3 Sep 2026, 15:40

`CA90ccb117bccbe9d5f344d9b335a1e2a9`, northgate, build **`a66a34371749`**,
82s, judge score 2, `outcome=abandoned`.

**The call was a PASS on what it was run to verify.** `05c3de1c` works:

```
15:41:08  'uh yeah check for tuesday please'
15:41:08  situational head (named_day): 'Let me have a look at Tuesday for you —'
```

"check" is now read as a request, so the lookup head fired rather than
*"Tuesday it is —"*. That is the corpus finding from this afternoon, confirmed
live.

Two other things went wrong, neither of them the thing under test.

---

## D-A — she asks a clinical question, then cancels it in the same breath

Exact, from obs:

> **Susie:** Oh, sorry to hear that —
> **Susie:** Knee pain can have a few different causes — do you have a sense of
> whereabouts on the knee it's bothering you,
> **Susie:** **Or how it came on? Either way, do you have a preference for when
> you'd like to come in?**

The caller is asked *where* the knee hurts, then *how it came on*, then told
**"Either way"** and asked something else entirely. Three questions, and the
first two are withdrawn before they can be answered. `turn_seq=2`,
`tts_finished in 8.8s` for the tail alone.

The consequence is not only awkwardness: the reason captured for the whole call
was **`'um just my knee'`** — no site, no mechanism. She asked for the detail,
cancelled the request, and recorded nothing.

### Two causes, and they compound

**1. The model wrote a self-cancelling turn.** "Either way" discards its own
question. `clinic_template_prompt.py` already forbids the shape it grew out of
— `:1594` says *"Keep it to ONE sentence — do NOT lecture about the
condition"* — and this is three sentences carrying two abandoned questions.
Same non-adherence finding as `OPEN_DEFECTS_2026-09-02.md` §5: the prompt
already forbids it, so more wording is unlikely to help.

**2. The chunker split it mid-sentence and capitalised the remainder.** The
model's clause was *"…bothering you, or how it came on?"*. It was split at the
COMMA and the second chunk was capitalised, so the caller hears a full stop
that was never written:

```
"...whereabouts on the knee it's bothering you,"     <- chunk ends on a comma
"Or how it came on? Either way, ..."                 <- capital O, reads as new
```

🔴 **`fff61547` CONFIRMED, by reproduction.** Feeding the model's exact
sentence through `ResponseChunker` token by token:

```
fast_first=False  -> 1 chunk   (the whole sentence)
fast_first=True   -> 2 chunks, split at the COMMA:
   1  "...do you have a sense of whereabouts on the knee it's bothering you,"
   2  "or how it came on? Either way, do you have a preference for..."
```

`chunk_text_static` on the same text returns ONE chunk, so this is the
streaming path only — which is why it was never seen in a test.

`fff61547` is *"let the first chunk break on a clause, not only a full stop"*,
and it reached production without a verifying call. Its own comment is careful
about what it must not split on — *"NOT the em-dash… NOT the ellipsis"* — and
it guards phone numbers read as words. **A comma inside a question was not
considered**, and a question is exactly where a false full stop is most
audible.

**And something CAPITALISES the continuation.** The chunker emits chunk 2 as
lowercase `"or how it came on?"`; the log shows TTS receiving `'Or how it came
on? …'`. So a mid-sentence clause is handed to the voice as a new sentence.
Not yet located — it is not `chunk_text_static`, and the obvious
`[0].upper()` sites in `llm_stream` are opener-strippers that run on the FIRST
chunk only. **Find it before fixing anything**: if the capitalisation goes, the
split alone is a pause rather than a defect, and that may be the whole fix.

**Order:** locate the capitaliser, then decide between (a) not splitting a
first chunk on a comma that sits inside an unfinished question, and (b) not
capitalising a continuation. (b) is smaller and keeps `fff61547`'s latency win.
Do NOT start with the prompt — prompt edits in this area have been measured
ineffective twice.

> [OK] **FOUND AND FIXED -- `57202217`, 3 Sep 2026. Cause 2 only; cause 1 stands.**
>
> The capitaliser is `sanitise_response`'s last statement in
> `app/media_streams/turn_handler.py`, labelled **"Fix A: re-capitalise after
> opener strip"**:
>
> ```python
> if result:
>     result = result[0].upper() + result[1:]
> ```
>
> Its comment scopes it to a banned-opener strip. **The code ran on every chunk
> unconditionally.** Reproduced directly: `'or how it came on? ...'` ->
> `'Or how it came on? ...'`.
>
> Third instance this week of the shape B-120 and B-132 share -- an operation
> whose safety rests on a premise, here *"this chunk starts a sentence"*, that
> is false for a continuation. `fff61547` made continuation chunks reachable on
> the streaming path on 1 Sep; this line then gave them a capital.
>
> Fixed as **(b)**, as this document recommended: Fix A now fires only when
> something was actually removed from the front, so `fff61547`'s latency win is
> untouched. The check is case-insensitive over 12 characters rather than a
> first-character comparison, because a strip can leave a word starting with
> the same letter (*"Wonderful -- we've got"*) and a naive check would read
> that as untouched and silently regress Fix A.
>
> **What this does NOT settle.** The false sentence is gone from the TEXT.
> Whether the caller still hears a hard stop depends on ElevenLabs prosody
> across two separate synthesis requests, and no test can decide that. This
> document's hypothesis -- *"the split alone is a pause rather than a defect"*
> -- is now the thing under test, and it needs a call. **If it still sounds
> wrong, the fallback is option (a):** do not split a first chunk on a comma
> that sits inside an unfinished question.
>
> **Cause 1 is untouched and needs re-scoping before anyone acts on it.** It
> argues from `:1594` *"Keep it to ONE sentence"* -- that is
> `_condition_families`, which renders **empty** on northgate because northgate
> ships `treatment_guidance`. It is dead config on the very clinic this call
> was made to. See `SESSION_2026-09-03_THREE_FIXES.md` for the same finding
> arriving from the other direction: §5's "non-adherence" reading was wrong for
> this reason, and the live brevity rule for that turn now sits in CONDITION
> FLUENCY (`bea61a7f`).

---

## D-B — a named day gets a promised lookup, no lookup, and no record

> **Susie:** Let me have a look at Tuesday for you —
> **Susie:** That day I've got ten to nine in the morning, or ten past five in
> the evening — which suits?

**No `check_availability` call ran on that turn.** There is no `[ms_llm] tool:`
line between `15:41:08` and `15:41:12`, no `deterministic … offer built`, and
no `slots_presented`. The model answered from the multi-day offer already in
its context.

Three separate failures follow from that one omission.

### 1. The head promised work that did not happen

The head was CORRECT to fire — "check for Tuesday" is a request, a lookup
*should* have followed. The promise was broken by the model, not by the
classifier. That is the promised-work defect arriving from the opposite
direction to every previous instance: not a head in front of no work, but no
work behind a justified head.

### 2. The caller was offered 2 of Tuesday's times, not Tuesday's diary

"ten to nine in the morning" and "ten past five in the evening" are exactly the
two slots already read for Tuesday in the multi-day offer — `08:50` and
`17:10`. Monday's payload that same call held **twelve** bookable times;
Tuesday's will be comparable. A caller who explicitly asked for one day got the
same two times back, with no indication there were more.

This is §2.2 — the false completeness claim — reached through a new door. The
earlier instances were the model over-claiming about a deterministic readout.
Here there is no deterministic readout at all.

### 3. The keypad still pointed at DAYS

```
15:41:12  slot map active — day_selection:
          {'1': 'Monday 7th September', '2': 'Tuesday 8th September',
           '3': 'Wednesday 9th September'}
```

She had just read out two **times**, and the map still held three **days**.
Pressing `1` would have selected Monday. Speech and record disagreed for the
rest of the call, which is the exact class
`SLOT_PRESENTATION_CONVERGENCE.md` exists to end.

**`calls.slot_offers` recorded ONE entry for this call** — the multi-day offer.
The Tuesday reply produced no offer record because it went through no producer.
The new obs column made that visible in one query; before today it was
invisible.

### Why the deterministic path did not catch it

`build_slot_offer` runs after `check_availability` returns. No tool call, no
producer, no record. Every guard downstream reads a record that was never
written, so none of them can fire. **The plan's §9 says a re-query is harmless
because the deterministic producer makes it so — this is the case it does not
cover: not a re-query, but no query.**

---

## Ranking

**D-B is the more serious.** It under-offers a day the caller explicitly asked
about, it breaks a promise, and it leaves the keypad pointing somewhere else —
three defects from one missing tool call, on the commonest follow-up in the
corpus.

**D-A is the more visible.** A caller hears it on every symptom turn where the
model writes a long clause, and it is the second time this week that a turn has
read as two sentences the model never wrote.

Neither is a regression from today's batch: `05c3de1c`, `1a54dd23` and the rest
behave as designed on this call. D-A may be a regression from `fff61547`
(1 Sep), which reached production unverified.

---

## Suggested order

1. **Check `fff61547` against D-A's split.** Cheap, and it decides whether this
   is a chunker fix or a prompt fix. Prompt edits in this area have been
   measured ineffective twice.
2. **D-B.** The honest fix is not to make the model call the tool — that is the
   trigger-side approach this codebase has been wrong about three times. It is
   to make a named-day follow-up go through a PRODUCER, the way
   `more_days_speech` already does for "what else have you got". The machinery
   exists; this path does not use it.
3. Neither blocks promoting `a66a3437` — both are present on `ae97af1e` too.
