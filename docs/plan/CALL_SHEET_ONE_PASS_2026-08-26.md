# Call sheet — one pass · 2026-08-26 (late evening)

Supersedes `CALL_SHEET_FOUR_FIXES_2026-08-26.md`. That sheet settled two of its
four fixes and could not settle the other two, because both calls missed the
condition the bug needs. It also turned up two new defects. This sheet closes
what is testable tonight in one pass.

**Joint Venture is dropped tonight.** `jv_v2` carries the same B-92 fix
(`d89b1c18`) and is converged with the others, but Marcus's line is not being
called this evening.

| Line | Number | Build to expect |
|---|---|---|
| Theorem (Mark) | **+447380841468** | `c00e4a4c` |
| Vital Edge (Jonathan) | **+447426779875** | `e090908f` |

**Hang up at the name request on every call.** The read-back
("So that's Tuesday the 8th at nine — could I take your name?") happens BEFORE
anything is written. Hanging up there means no appointment reaches a real
clinic calendar.

`/health` returns a hardcoded `1.0.0`. Ignore it. The only deploy proof is
`[build_info] running build <sha>` at call cleanup.

---

## What each call settles

| Call | Line | Bug | Why it is on this sheet |
|---|---|---|---|
| **T-C** | Theorem | 2 | **RE-RUN** — last attempt asked about a one-slot day, so nothing could be re-offered and nothing was proved |
| **T-D** | Theorem | B-92 | **NEW** — a dead-end refusal must still offer another day |
| **V-A** | Vital Edge | 1 (P1) | **RE-RUN** — last attempt's two options had different times, which is not the collapse condition |

Bugs 3 (wrong weekday) and 4 (a slot pick narrowing later searches) are
**settled** and are not retested here.

---

## Not deployed to these lines — do not report as a failure

**B-91** — the spoken slot pick that still armed a mornings filter, and the
time band named inside a question — is fixed on `latency-eval` only
(`900b8bbd`, `c0363d65`). It is **not** on Mark's or Jonathan's build tonight.

If you see this line on either clinic line, it is expected:

```
[ms_conn v3] time_of_day_preference captured: mornings (from utterance '... any
             other slots than the 10 in the morning ...')
```

`time_of_day_preference` is also still never cleared. A preference stated out
loud early in a call still persists, which is correct; the residual is that a
later "is that all you have that day" can be answered from a filtered view.
The honest fix is payload disclosure and is deliberately not in this batch.

---

# T-C — Theorem · a second time inside one option

**Why the last run proved nothing:** the options were right — Number 2 and
Number 3 each carried two times — but the follow-up named **Tuesday**, which
had a single slot. A day with one time cannot re-offer a second one.

**This time: do not name a day.**

```
You:   I'd like to book an appointment
You:   Alcester
You:   a physiotherapy assessment
You:   anytime next week
Susie: "... Number 2, <day> — <time A>. Or <time B>. Number 3, ..."
                                  ^^^^^^^^ you need an option with TWO times
You:   what else have you got?          <-- no day named
```

**PASS** — the list she reads back contains **neither** time A **nor** time B.

**FAIL** — time A or time B comes back, seconds after she read it out.

If no option carries two times, this call proves nothing — say "anytime the
week after" and try again. Mark's Acuity returns 7–10 slots on a good day,
which is what makes the two-times-in-one-option shape reproducible on his line
and not on Jonathan's.

**Log:**

```
[ms_gate5] slot buf: spoken options span N days — recorded as heard
```

---

# T-D — Theorem · the dead end  (NEW — B-92)

This is the call that hung up on us at 18:33 today. Tuesday 1 September had one
slot; the clinic had 95 across the month. The caller asked three times and was
told "No, that's the only slot" with **no question behind it** — the offer to
look at another day had been deleted by a guard that mistook it for a false
availability claim.

You need a day Susie reports **a single time** for. Take whichever one she
gives you; do not plan the date in advance.

```
You:   I'd like to book an appointment
You:   Alcester
You:   a physiotherapy assessment
You:   have you got anything on <a day>?
Susie: "The available slot for <day> is <one time>."       <-- ONE time only
You:   do you have any other slots on that day?
```

**PASS** — she says that is the only slot on that day **and asks you
something** — "would one of the other days work better for you?", "shall I look
at another day?", or similar. The turn must not end on a full stop.

**FAIL** — "No, that's the only slot on <day> — just the <time>." and then
silence, with nothing to answer.

**Log — three lines, and the middle one is the tell:**

```
[ms_gate5] kept scarcity sentence (that_is_the_only)      <- correct, expected
[ms_gate5] REMOVED unfounded extra-availability claim ... <- must NOT appear
                                                             for a sentence
                                                             about DAYS
[ms_watchdog] BACKSTOP armed — turn asked nothing         <- must NOT appear
```

"That's the only slot on the 1st" is TRUE and must stay sayable — that sentence
was restored by an earlier fix and deleting it again is its own regression.
What must survive alongside it is the way out.

---

# V-A — Vital Edge · the wrong-day booking

**Why the last run proved nothing:** the two options were Monday (slots
12:00–16:00) and Tuesday (nine in the morning). Different spoken times, so "the
second one" had an unambiguous key. The collapse this fix repairs needs **two
days sharing one spoken time**.

The offer also went down the multi-day branch, where the position-indexed offer
record is deliberately not written — so the mechanism was never exercised at
all.

**This time: ask for afternoons.** Jonathan's diary ran 12:00–16:00 on Monday,
so an afternoon request across a week is the likeliest way to land the same
spoken hour on two different days.

```
You:   I'd like to book a massage please
You:   deep tissue
Susie: sixty or ninety minutes?
You:   sixty
You:   have you got anything in the afternoons next week?
Susie: "Number 1, <day A> — <time>. Number 2, <day B> — <the SAME time>."
You:   the second one please
Susie: "So that's <day B> at <time> — could I take your name?"
You:   [hang up]
```

**PASS** — she reads back **day B**.

**FAIL** — she reads back **day A**. That is the wrong-day booking; hanging up
at the name request means nothing was written.

If the two options land on different times, this call does not test the bug.
Ask for another week rather than accepting the result.

**Log — the two dates must DIFFER:**

```
[ms_gate5] slot buf: 2 spoken option(s) recorded as offered —
           ['2026-09-07T14:00:00+01:00', '2026-09-08T14:00:00+01:00']
                        ^^ day A                    ^^ day B
```

If you get `spoken options span 2 days — recorded as heard, offer record left
unchanged` instead, the multi-day branch ran and the ordinal path was not
exercised. The read-back may still be correct, but it proves nothing — say so
in the report rather than marking it PASS.

---

## After the calls

Grep the Render log for these:

```
build_info                                     <- confirms which build answered
spoken options span N days — recorded as heard <- T-C, and the V-A miss case
spoken option(s) recorded as offered           <- V-A: the dates must DIFFER
REMOVED unfounded extra-availability claim     <- T-D: not for a DAYS sentence
BACKSTOP armed — turn asked nothing            <- T-D: must not appear
kept scarcity sentence (that_is_the_only)      <- T-D: correct, expected
```

## Rollback

Tonight's port is one commit per branch — one engine file plus its test,
nothing else. No prompts, no clinic config.

```bash
git revert --no-commit c00e4a4c   # Theorem
git revert --no-commit e090908f   # Vital Edge
git revert --no-commit d89b1c18   # Joint Venture (not called tonight)
```

Pre-port tips, if a hard reset is preferred: Theorem `1a711a54`, Vital Edge
`98993df8`, Joint Venture `24584b83`.

## Still open after this sheet

- **Joint Venture** has the fix and no verification call. One short booking
  call on Marcus's line closes it.
- **B-91** is on canonical only and needs porting to all three once these calls
  are clean.
- **The named-weekday chain** (`81da8da4`, `ecd7d60d`, `28245401`) is on
  `latency-eval` and `jv_v2` but **not** on Theorem or Vital Edge. Symptom:
  "I can only do Tuesdays" gets one Tuesday examined and reported as every
  Tuesday — the caller on that call abandoned after asking four times. Not
  covered by any call on this sheet.
- **`theorem-onboarding` declares `minimum_age_years: 7` twice** in the same
  dict (`clinic_config.py:463` and `:488`). Both say 7 so behaviour is right
  today, but an edit to the first is silently ignored.
