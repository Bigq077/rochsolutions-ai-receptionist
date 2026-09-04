# Call sheet — the four changes made after the 4 Sep morning call

**Under test:** `latency-eval` @ `efe39abe`. Four changes, **none heard on a
call**. `production` unchanged at `4eda31f3`.

Confirm first: `[build_info] running build efe39abe` in the Render log. Demo
line **+447366263180**.

| | change | how it shows up |
|---|---|---|
| `5924a96d` | **B-134** a stood-down sentence records what it spoke | a narrowed time pick resolves |
| `f23c5c35` | **B-135** a silent turn cannot strand a screen | the screen is asked ONCE |
| `70043f89` | **B-136** the reason survives a screening turn | the summary says why they rang |
| `efe39abe` | surname read-back counter | a log line, no behaviour change |

---

## One call covers all four

Run it as close to the 4 Sep morning call as you can — that call is the
reference, and every one of these was written from its log.

**1. Open with a complaint that triggers a screen.**

> "I'd like to book an appointment — I was playing football and rolled my ankle."

Two things must happen on that one turn: `screen … ARMED` in the log, and the
reason captured from THIS utterance rather than a later one.

**2. Answer the screen once, clearly.**

> "No, it's fine — nothing too serious."

**PASS** — `screen … clear` and the flow moves on.
**FAIL** — `screen … STRANDED`, or the screen is put to you a second time.
That is B-135 not holding.

If she says something that asks nothing (a patience line, "take your time"),
answer it normally. That is exactly the shape that broke it before.

**3. Get a multi-day list, then narrow to a time.**

> "Anytime next week." … then, after the three days, "around midday, eleven
> o'clock."

She should come back with two Monday times.

**4. Pick one of THOSE times.**

> "Ten past twelve works."

**PASS** — `caller ACCEPTED 2026-09-07T12:10…` appears, and the read-back names
that time.
**FAIL** — no `caller ACCEPTED` line, or `read-back time NOT in the offer`
repeats. That is B-134 not holding.

Also expect `B-134: the stood-down sentence named N payload slot(s) the record
did not hold` when she narrows.

**5. Give a name and let it reach the booking.**

Then check two things in the log:

* the call summary / Sheets row `reason=` describes the ANKLE, not your reply
  to the screen (B-136);
* whether `SURNAME NOT READ BACK` appears.

---

## About that surname line

**It is a counter, not a failure.** It fires when Susie books a surname she
never said aloud. On the obs corpus that was **34.6% of calls that actually
booked**, so seeing it is expected and is not a regression.

What is worth telling me is the RATE across a few calls. Under ~5% and it can
be promoted to a real gate like the phone's; anywhere near a third and it
cannot.

---

## What is NOT in this build, and why

Three defects from the 4 Sep call are deliberately still open:

* **§2.2 second half** — the deterministic completeness opener,
  `slot_offer.py:418`. It fires on 96.7% of readouts, and it would be a SECOND
  slot-layer change stacked on an unverified B-134.
* **`check_availability` BLOCKED returning the wrong days** — same layer, same
  reason.
* **The B-87 timing re-ask** ("is there a particular day or time" after a slot
  was already chosen) — it interacts directly with the screening changes above,
  so it wants those verified first.

**P8** (a closed day reported as "too soon to book") is Theorem-only and cannot
be exercised from the demo line at all.

---

## If something goes wrong

Revert targets, each the commit BEFORE the fix named:

| back out | revert to |
|---|---|
| surname counter | `70043f89` |
| B-136 reason | `f23c5c35` |
| B-135 screening | `5924a96d` |
| B-134 slot record | `12c5af8b` |

Nothing here has reached `production`. **B-135 is the one to watch hardest** —
it changes when a clinical screen is treated as answered, and its blast radius
was scoped by hand: the double-ask guard keeps the old, stricter reading and
`test_the_double_ask_guard_is_not_widened` pins that. If a screen is ever
skipped entirely rather than asked, back out B-135 first.

---

## Known-accepted noise, do not chase

`last_bot_prompt truncated at 200 chars` (B-31, the fix working) ·
`GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON` (demo line only) ·
ElevenLabs 401 on `/v1/models` · `SMS_ENABLED is off`.
