# Call sheet — evening of 4 September 2026

## Where the code actually is before you dial

| | SHA | Serves |
|---|---|---|
| `origin/latency-eval` | `25c18f44` | demo line **+447366263180** (northgate) |
| `origin/production` | `25c18f44` | Vital Edge, JV, Theorem |

**They are byte-identical** — `git rev-list --count` is 0 in both directions.
Your big port landed clean and there is nothing stranded on either side.

Everything below marked **NEW** is on `fix/db-named-day-producer` and is **not
deployed anywhere yet**. Calls in Group A test what is already live; Group B
only works after a push to `latency-eval`.

Proof of what you are actually calling: `[build_info] running build <sha>` in
the Render log at call cleanup. `/health` lies — it returns a hardcoded 1.0.0.

---

## Group A — already live, worth confirming (demo line)

### A1. The named-day request (the D-B defect, NOT yet fixed live)
This is the one from the 15:41 call. Expect it to still be **wrong** unless you
push Group B first — call it anyway so you have a clean before/after.

> "Hi, I'd like to book an appointment."
> — let her read out the days —
> **"Uh yeah, check for Tuesday please."**

Listen for: she says *"Let me have a look at Tuesday for you"* and then offers
**only two times**. Log check: `grep check_availability` — on the broken build
there is **no tool call at all** after that head.

### A2. Straight booking, end to end
> "Can I book a sports massage?" · "Wednesday morning" · pick a time · give a
> name and number.

Listen for: the time she reads back matches the diary. Log check:
`event created` and the `duration=Nm` on the same call — a 90-minute booking
written as 60 is a defect that survives every verbal read-back.

### A3. Cancel
> "I need to cancel my appointment."

Listen for: she does **not** apologise for a success, and does **not** ask you
to confirm five times.

---

## Group B — the new work (needs a push to `latency-eval` first)

### B1. D-B — "what about Wednesday" answered from the payload  **NEW**
Same script as A1.

Now expect: **more than two times**, drawn from the twelve the payload holds,
and the keypad renumbered to those TIMES rather than still pointing at days.
Then immediately test the keypad:

> — after she reads Tuesday's times — **press `1`**

Listen for: `1` selects the first **time she just said**, not Monday.
Log check: `answered from the payload` in `[slot_followup]`, and
`calls.slot_offers` gains a **second** row for the call.

### B2. The things D-B must NOT have broken
Three separate calls, because each is a path that already worked:

> a) — after a readout — **"yeah Monday works"** → she must ACCEPT ("Monday it
>    is"), not re-read Monday.
> b) — after a readout — **"Tuesday at ten past five works"** → an acceptance,
>    not a readout.
> c) — after a readout — **"what else have you got"** → the more-slots answer.

Any of those turning into a day readout is a regression and is worth stopping for.

### B3. JV hold speech  **NEW — see the flag below before you push**
Only audible on JV, so only on a `production` push. What changes: her waiting
phrases come from the arbiter instead of six independent producers. She should
say **"Right, booking you in —"** on a write, and must **never** say
*"Sending that over to <practitioner> —"* (that is Vital Edge's wording, for a
clinic that makes a request rather than a booking).

### B4. P8 — a closed day is no longer called "too soon"  **NEW, Theorem only**
Hard to trigger deliberately. If Theorem gives you a day it cannot fill, the log
should now read `the clinic is closed` rather than
`all within 2h lead-time window`, and she should **not** silently re-run a
second availability fetch.

---

## The one decision I need from you

**JV's hold speech is switched on in my working tree and not pushed.**

The holiday plan's item 4 was "port filler phrase work to joint venture and
vital edge", so it is in scope and it is done — but there is a standing
regression test (`test_hold_speech_is_opt_in_per_clinic.py`) whose whole purpose
is to stop this reaching a live patient line without a recorded decision. It
required me to write down who chose it and when, so I have, honestly: **you, on
2026-09-04, with the JV practitioner not having heard it** — the same standard
Vital Edge was taken on three days earlier, which that file already records as
"a weaker standard than this list was written to expect".

It changes what JV's real callers hear while they wait. It reaches them **only**
on a `production` fast-forward, not on the `latency-eval` push. So:

- push `latency-eval` tonight and test everything → **no JV patient is affected**;
- push `production` → **JV patients hear it**, and reverting is one key plus a
  redeploy, not instant.

My recommendation: push `latency-eval`, work Group B, and hold the `production`
push until you have heard B1/B2 yourself.
