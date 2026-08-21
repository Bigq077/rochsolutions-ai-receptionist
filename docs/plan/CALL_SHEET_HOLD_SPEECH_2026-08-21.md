# Call sheet — hold speech, 2026-08-21

**Clinic / dial:** `jv_v1` · **`+447366263180`** — the demo line, service
`low-latency-joint-venture`, branch `latency-eval`.

**What you are judging:** whether the hold phrase now sounds like the *start of
the sentence Susie is about to say*, rather than a canned interjection followed
by a pause. Everything else about this change is already proven offline; prosody
is the only thing a test cannot hear.

---

## Pre-flight (do not skip)

1. **Build.** Render → `low-latency-joint-venture` → logs. Find
   `[build_info] running build <sha>` at the end of any call. It must say
   **`f41868b`**. `/health` returns a hardcoded `1.0.0` and proves nothing.
   If the sha is older, **stop** — nothing below proves anything.
   *This service has previously been set to Manual Deploy. If the sha is stale,
   click Deploy rather than assuming the push shipped it.*
2. **Obs capture.** `OBS_CAPTURE_ENABLED=true` and `OBS_DATABASE_URL` set, or
   there is no call record to read afterwards.
3. **`LATENCY_TIMING`.** Currently **unset**, so `no_content` and the tool
   durations collect nothing. Set it to `true` on this service before calling if
   you want the numbers as well as the impression — it is a restart, not a
   deploy, and it is the only way to see whether the timing is right rather than
   just the wording.
4. Call from a **dev handset**, not the clinic's own number.

---

## What changed, in one line each

- One hold phrase per caller turn. Not "usually" — structurally.
- The phrase describes the work that is actually running, or names no work.
- The phrase ends **open** ("Let me see —") so the reply completes the sentence.
- Susie no longer says her own "let me check" on top of it.

---

## The calls

### 1 — A normal booking

*"Hi, I'd like to book a sports massage, sometime next week."*

Listen for: **one** hold phrase before the slots, not two or three. Then the
slots should sound like they *finish* it — "Let me see — Friday the fourteenth at
ten's free" — not two separate sentences with a gap between.

❌ Fail if you hear the phrase twice in different words, or an obvious pause and
a fresh start after it.

### 2 — The one that used to be absurd

Get to any question, then ask: **"Sorry — are you a robot?"**

Before this change: *"Just getting that for you…"* then *"No — I'm Susie."* The
phrase promised a lookup nobody was doing. That was 135 of the 322 stored hold
phrases.

Now: either silence and a straight answer, or a contentless *"Right —"*.

❌ Fail if it says anything about checking, looking, or the diary.

### 3 — Reschedule

Call back and move the appointment from call 1.

Listen for: *"Moving that across —"* / *"Let me find you —"*, not the generic
"one moment". The wording should match what is happening.

### 4 — Cancel

Cancel it again.

Listen for: *"Taking care of that —"*. And it must not apologise or hedge —
the cancel path had its own history there.

### 5 — A long FAQ, then book

Ask four or five questions about the clinic, then say you'd like to book.

This is the FAQ bridge, which used to register in **neither** cooldown and could
stack on anything. One phrase, or none.

---

## What this line CANNOT test

`jv_v1` is not a provisional clinic, so the **Vital Edge** fix — booking writes
becoming *"Sending that over to Jonathan —"* instead of *"Just locking that in
now…"* — cannot be exercised here. It is covered by tests and by construction,
but it wants a Vital Edge call before that branch goes live.

The pre-recorded **clip** still says the old wording (*"Let me just check that
for you…"*) in the old falling contour, and there is still only one of it. It
fires at 350 ms on slot-presentation turns, so on call 1 you may hear the old
clip and then the new joined reply. That is expected at this stage — new clips
need the paid ElevenLabs voice.

---

## Afterwards

```sql
select call_sid, start_utc, latency->'summary'->>'no_content_turns'
from calls
where clinic_id = 'jv_v1' and start_utc > now() - interval '2 hours'
order by start_utc;
```

`no_content_turns` is the count of turns where the caller heard a hold phrase and
no content ever followed — a dead-end. It was 32 across the whole stored corpus
and invisible before this build. It should be **0**. (Populated only if
`LATENCY_TIMING` is on.)

## Rollback

```bash
git push origin ccd765f:latency-eval --force-with-lease
```

`ccd765f` is the commit immediately before this work.
