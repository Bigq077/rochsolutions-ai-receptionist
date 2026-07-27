# Name capture — every exchange from 2026-07-27, and what recurs

27 calls, 25 name exchanges, two callers (`+33617769867` Jules overnight,
`+447502211207` Quentin daytime). Source: `demo_obs.calls`.

**Why this matters more than it looks.** A wrong name is the only defect in this
system that is *invisible to everyone at the time*. The caller hears their real
name read back, the booking succeeds, the calendar event exists — and the clinic
opens its diary to a patient who does not exist. No error, no alert, no
escalation. It is the F-021 failure shape applied to identity.

---

## The headline: the test data was easier than reality

| Caller | Names used | Bookings | Wrong name |
|---|---|---|---|
| Jules (`+33`) | "John Smith", "Tom Green", "Jack Rhinestone" | 11 | **0** |
| Quentin (`+44`) | "Quentin Roche" | 3 | **2** |

Jules's sweep says name capture works. It does — **for two of the most common,
most phonetically distinct names in English, spoken bare**: *"john smith"*,
*"tom green"*. Eleven bookings, eleven correct.

Quentin used a real name with a natural lead-in — *"that would be Quentin
Roche"* — and two of three bookings stored a name the clinic cannot match:

```
17:59  "yeah that would benton"        -> stored "Benton Rock"
18:35  "yeah that would be quinton rock" -> stored "Quinton Rock"  (she SAID "Quentin")
20:32  "here there will be quentin rock" -> stored "Quentin Rock"  ✓
```

**The sweep did not miss a defect; it never exercised the case.** Any conclusion
about name capture drawn from the overnight run is scoped to bare, high-frequency
names. Real callers do not speak that way.

---

## N-1 · The surname is asked twice — the most frequent defect of the day

She asks *"could I take your first name and surname?"*, the caller gives **both**,
she acknowledges only the first, moves to the phone step — and then asks for the
surname again several turns later.

| Call | Caller gave | Re-asked at |
|---|---|---|
| 03:04 `CAe2af4ac70d6a` | tom / green | turn 23 |
| 03:43 `CAfe6a41626d0b` | tom green | turn 33 |
| 03:48 `CAbad8422e3c5e` | jewel / decorps | turn 27 |
| 03:56 `CA847e8406a2b2` | "yeah **john smith**" (both, turn 14) | turn 23 |
| 18:38 `CAa4942bcea465` | "that would be **quinton rock**" (both, turn 8) | turn 17 |

**Five of 25 exchanges.** On 03:56 and 18:38 the caller demonstrably supplied both
names in one utterance and was asked again anyway — the surname is dropped between
capture and the collection gate. This is F-019 (surname dropped from summary) seen
from the other side: not lost at write time, lost at read time.

On 18:38 the re-ask arrives *after* the booking confirmation has already been
asked, which is what turned that call into a six-confirmation loop.

---

## N-2 · Spoken name ≠ stored name

| Call | Caller said | She said | Stored |
|---|---|---|---|
| 18:35 | "quinton rock" | *"Thanks **Quentin**"* | **`Quinton Rock`** |
| 18:38 | "quinton rock" | *"Thanks **Quentin**"* | **`Quentin Rock`** |

**Identical input, different stored value.** Name capture is non-deterministic.

Worse, on 18:35 the caller heard the *correct* name and the record kept the
*incorrect* one. **This defect cannot be caught by listening to the call.** It is
only visible by reading `collected.name` afterwards — which nobody does.

---

## N-3 · The lead-in is the trigger

Every failure clusters on the same shape: a natural preamble before the name.

```
"yeah that would benton"          <- "that would be Quentin" mangled; "be" absorbed into "benton"
"here i'll be quinting"           <- recovered correctly to Quentin
"here there will be quentin rock" <- recovered correctly
"at the quentin"                  <- recovered correctly
```

`app/name_capture.py:281` strips the *"that would be"* family. When STT merges
"be" into the following word — *"that would**benton**"* — the stripper removes
*"that would"* and hands `benton` to the collector as the name.

The bare forms (*"john smith"*, *"tom green"*, *"jack rhinestone"*) never hit this
path. **The lead-in, not the name, is what breaks it.**

---

## N-4 · The confirm gate accepts a non-answer — the CONFIRM_PHONE bug again

`CAc0a67a9de8dd`, 12:20:

```
[ 9] SUSIE  | could I take your first name and surname?
[10] CALLER | here i'll be quinting
[11] SUSIE  | Did you say Quentin — is that right?      <- a yes/no question
[12] CALLER | rock                                       <- a surname, not an answer
[13] SUSIE  | Thanks Quentin — I've got you on 07502 211 207 ...
```

The surname was treated as confirmation of the first name, and then **discarded**:
that call ended with `collected.name = None` despite the caller having said both
names.

This is structurally the CONFIRM_PHONE defect closed in `28ff14b` — a yes/no gate
accepting whatever arrives — reappearing on the name gate. It has no FM number.

---

## N-5 · Four different behaviours for the same situation

A garbled name produced four distinct responses across the day:

| Behaviour | Calls |
|---|---|
| Confirm it — *"Did you say Quentin — is that right?"* | 12:20, 03:04, 03:43, 03:48 |
| Accept silently, ask surname | 12:28, 17:59 |
| Generic re-ask — *"I'm sorry, could you say your name again?"* | 03:56 |
| Ask again unchanged — *"Could I take your first name and surname?"* | 05:24, 01:13 |

And the ask itself has two wordings: *"could I take your first name and surname?"*
(24×) and *"What's your full name?"* (18:38).

Non-determinism here is why the defect is hard to reproduce and why it slipped
eleven clean overnight calls.

---

## N-6 · Spelling is unreliable

Callers spell when they have already been misheard — so this path matters most
exactly when it is needed most.

| Call | Caller spelled | Result |
|---|---|---|
| 03:56 | `"m-i-e-l-w-sh"` | **rejected** — *"I'm sorry, could you say your name again?"* |
| 03:43 | `"green g-r-e-e-n like the color"` | worked, after two turns |
| 03:04 | `"like the color"` | not understood; surname re-asked at turn 23 |
| 03:48 | `"the corpse delta echo charlie"` (NATO) | **worked** — stored `Decorps` |

Letter-by-letter is rejected; NATO alphabet works. That is the opposite of what a
caller would guess.

---

## Ranked, with fix risk

| # | Defect | Frequency | Severity | Fix risk |
|---|---|---|---|---|
| **N-1** | Surname re-asked after being given | **5 / 25** | HIGH — drives the confirmation loop | MEDIUM |
| **N-2** | Spoken ≠ stored; non-deterministic | 2 / 3 UK bookings | **HIGHEST** — invisible to everyone | MEDIUM |
| **N-3** | Lead-in absorbed into the name | 1 confirmed, 3 near-misses | HIGH | LOW-MED |
| **N-4** | Confirm gate accepts a non-answer | 1 | HIGH — loses the name entirely | LOW |
| **N-5** | Four inconsistent repair behaviours | across the day | MEDIUM | HIGH |
| **N-6** | Spelled names rejected | 2 / 4 | MEDIUM | MEDIUM |

**None of these is a pre-demo fix.** N-1, N-2 and N-5 are all changes to the
`name_collector` state machine — the file with 36 pre-existing test failures in
the baseline, which is itself a signal that its intended behaviour is no longer
pinned. Touching it with a day to go would be the least defensible change on the
board.

**N-4 is the cheapest real fix** (make the name confirm require an actual yes/no,
exactly as `28ff14b` did for the phone) and it is the natural first task after the
demo, alongside **N-3** (guard the lead-in stripper against a merged token).

---

## What this changes for Wednesday

The demo script constraint was *"give a simple name, clearly"*. This data makes it
sharper and explains why:

> **Say the name bare. No lead-in.**
> *"Tom Green."* — not *"yeah, that'd be Tom Green"*.
>
> The lead-in is the trigger. Bare names went 11 for 11 today; lead-in names went
> 1 for 3.

And a verification step that costs nothing:

> **After any demo or clean-run booking, read `collected.name` from obs.** The one
> defect here that a listener cannot detect is the one where she says the right
> name and stores the wrong one.
