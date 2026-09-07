# Call sheet — evening of 7 September 2026

`latency-eval` = **`deb479a8`**  ·  `production` = **`1a8d19c6`** (revert target
`7e4bd30c`).

Confirm the build before you start: `[build_info] running build deb479a8` in the
Render log. `/health` returns a hardcoded 1.0.0 and will not tell you.

Three changes are waiting on this call. Two are audible, one is silent.

---

## What is on the demo line and not on the clinics

| | change | how you hear it |
|---|---|---|
| 1 | read-back names the caller from the RECORD | the surname is in the read-back |
| 2 | the read-out no longer claims completeness when it is capped | the opener is shorter |
| 3 | the read-out is ~20% shorter | 12.7 s → ~10.2 s |

Already **on production** and verified this morning: the spelled-surname parse
fix (`Quentin R-O-C-H` → `Quentin Roch`).

---

## The call — one call covers all three

Ring **+447366263180**.

| turn | say | what to listen for |
|---|---|---|
| 1 | "Hi, I'd like to book an appointment for my ankle." | — |
| 2 | "Do you have any availability Tuesday please?" | **the read-out.** See below |
| 3 | pick one: "ten to nine works" | she confirms that slot |
| 4 | "That'll be Quentin, and my surname is R-O-C-H, Roch." | she takes the name |
| 5 | "Yes it is" (to the number) | **the read-back.** See below |

Hang up at "shall I go ahead and book that in?" — nothing is written until you
answer that, so there is nothing to cancel.

### Turn 2 — the read-out

Expect roughly:

> Tuesday 8th September — Number 1, ten to nine in the morning. Number 2,
> twenty past four in the afternoon. Number 3, ten past five in the evening.
> And I've a few others that day. Any of those work?

**It should NOT begin "The available slots for Tuesday 8th September are —".**
That opener now renders only when the list really is the whole day.

Two things worth noticing rather than measuring:

* does it still sound like a receptionist, or does the bare day label sound
  clipped? The multi-day read-out has always used this shape per day, so it is
  in the product's voice already — but this is the first time it opens a reply.
* do you still feel the need to interrupt? On CA8b40d1ed you barged in while
  the old tail was playing.

### Turn 5 — the read-back

Expect **"So that's Quentin Roch, Tuesday the 8th of September at …"**

The thing being tested is the **surname**. Twenty-five per cent of stored
read-backs name only the first name, and that is what the steer is for. If she
says "So that's Quentin, Tuesday …" the steer did not take.

---

## Log lines that settle it

```
[build_info] running build deb479a8
Row built — name=Quentin Roch                     <- not R-O-C-H, not "Quentin"
```

and on the read-out turn, the absence of:

```
last_bot_prompt truncated at 200 chars and lost its '?'
```

That warning fired on both calls today. The read-out is now 197 characters
against a 200 cap, so on this shape it should be gone. It may still appear on
other turns — it is the read-out shape that was fixed, not the cap.

---

## What is NOT covered by this call

* **Theorem's Acuity path.** Still exercised by no call, and the P8 change from
  this morning is live on it. A real Theorem booking, cancelled through Susie,
  remains the largest piece of unverified work.
* **The multi-day read-out** (17.3 s). Untouched. Its lead-in — "Here's what
  we've got coming up —" — is 1.8 s and, unlike the single-day opener, it makes
  a soft completeness claim with no disclaimer behind it, because B-99
  suppresses the tail where "that day" has no referent. Worth a decision, not a
  quiet edit.
* **Dropping "in the morning / afternoon / evening"** — a further 4.7 s on
  multi-day, 2.4 s on single-day. Evidence says it is safe (zero collisions in
  236 real day-offers, no clinic with a 12-hour ambiguous pair, and the resolver
  already strips the band before matching). It changes how Susie sounds, so it
  is your ear and not a measurement.
