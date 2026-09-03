# Call sheet — the four fixes · 2026-08-26 (evening)

Verifies the four bugs found during port verification and fixed the same day.

| Line | Number | Build to expect |
|---|---|---|
| Theorem (Mark) | **+447380841468** | `1a711a54` |
| Vital Edge (Jonathan) | **+447426779875** | `98993df8` |
| Joint Venture (Marcus) | **+447367002651** | `24584b83` |

**Hang up at the name request on every call.** The confirmation read-back
("So that's Tuesday the 8th at nine in the morning — could I take your name?")
happens BEFORE anything is written, and it is the proof you need. Hanging up
there means no appointment is created on a real clinic calendar.

`/health` returns a hardcoded `1.0.0`. Ignore it.

---

## The five calls, and what each one settles

| Call | Line | Bug | Settles |
|---|---|---|---|
| **T-A** | Theorem | 4 (P1) | A slot choice no longer filters every later search |
| **T-B** | Theorem | 3 | A wrong weekday is corrected even with no availability |
| **T-C** | Theorem | 2 | A second time in one option is not re-offered |
| **V-A** | Vital Edge | 1 (P1) | "The second one" books the day you meant |
| **J-A** | Joint Venture | sanity | Nothing regressed on Marcus's line |

---

# T-A — Theorem · the P1 false refusal

This is the exact call that found it. **Do this one first.**

```
You:   I'd like to book an appointment please
Susie: which clinic?
You:   Alcester
You:   a physiotherapy assessment
Susie: any particular day or time?
You:   do you have anything next Wednesday?
Susie: "The available slots for Wednesday Xth are — Number 1, <a MORNING time>.
        Number 2, <an AFTERNOON time>."          <-- you need BOTH, morning + afternoon
You:   [press 1 on the keypad]
Susie: confirms that slot, asks for your name
You:   actually, on that Wednesday, do you have any other slots than the
       <morning time>, or is that all you have that day?
```

**PASS** — she offers the afternoon slot, or says how many the day has.
**FAIL** — *"That's all we have on Wednesday the Xth — just the &lt;morning time&gt;"*
while the afternoon one is still on the clinic's Acuity page.

**Log — the line that proves it:**

```
[ms_conn v3] 'ten in the morning' is a slot SELECTION, not a time preference
             — soft context not set (B-90)
```

and, crucially, **no** `time_of_day_preference captured` line after the keypress,
and the next `check_availability` args must **NOT** contain `"morning"` in
`date_hint`.

If you want independent ground truth, open Mark's Acuity page for that Wednesday
before you call. That is how this bug was found — it is invisible in a
transcript, because Susie's sentence is TRUE about the payload she was given.

---

# T-B — Theorem · the wrong weekday

You have to say the wrong weekday **on purpose**. Pick tomorrow's date and
attach the wrong day name to it — if tomorrow is Thursday the 27th, say
"Wednesday the 27th". Note there is deliberately **no month** in what you say;
that is the shape that escaped last time.

```
You:   I'd like to book an appointment
You:   Alcester
You:   a physiotherapy assessment
You:   have you got anything on Wednesday the 27th?     <-- wrong weekday, no month
```

**PASS** — she says **Thursday** the 27th.
**FAIL** — she repeats "Wednesday the 27th" back at you.

**Log:**

```
[ms_gate5] spoken weekday corrected: 'Wednesday' -> 'Thursday' for 2026-08-27
```

Works whether or not that day has availability — that was the gap. If the day
happens to be full you should hear "Nothing on **Thursday** the 27th, I'm
afraid".

---

# T-C — Theorem · a second time inside one option

Acuity returns 7-10 slots a day, so the model packs two times into one numbered
option. That is what makes this reproducible on Mark's line and not on JV's.

```
You:   I'd like to book an appointment
You:   Alcester
You:   a physiotherapy assessment
You:   anytime next week
Susie: "... Number 1, <day> — <time A>. Or <time B>. Number 2, ..."
                                        ^^^^^^^^^^ you need an option with TWO times
You:   what else have you got?
```

**PASS** — the list she reads back contains **neither** time A **nor** time B.
**FAIL** — time B comes back, seconds after she read it out.

If no option carries two times, this call proves nothing — try a different day
or say "anytime the week after".

---

# V-A — Vital Edge · the wrong-day booking

The one that could actually book the wrong day. You need two options on
**different days sharing the same spoken time** — Jonathan's diary produces this
readily ("Monday 7th — nine in the morning. Number 2, Tuesday 8th — nine in the
morning").

```
You:   I'd like to book a massage please
You:   deep tissue
Susie: sixty or ninety minutes?
You:   sixty
You:   anytime next week
Susie: "Number 1, <day A> — <time>. Number 2, <day B> — <the SAME time>."
You:   the second one please
Susie: "So that's <day B> at <time> — could I take your name?"
You:   [hang up]
```

**PASS** — she reads back **day B**.
**FAIL** — she reads back **day A**. That is the wrong-day booking; hanging up
at the name request means nothing was written.

**Log — check the two dates are DIFFERENT:**

```
[ms_gate5] slot buf: 2 spoken option(s) recorded as offered —
           ['2026-09-07T09:00:00+01:00', '2026-09-08T09:00:00+01:00']
                        ^^ day A                    ^^ day B — must differ
```

Before the fix this printed **the same date twice**, which is what made "the
second one" resolve to day A.

If the two options happen to have different times, this call does not test the
bug — the collapse needed a shared spoken time. Ask for another week.

---

# J-A — Joint Venture · sanity

Marcus's line is live to patients. One call, nothing exotic.

```
You:   I'd like to book an appointment
You:   a physio assessment
You:   anytime next week
Susie: reads numbered options
You:   [press 1]
You:   is that all you have that day?
You:   [hang up]
```

**PASS** — she answers the question, and does not claim a filtered view is the
whole day.

---

## After the calls

Grep the Render log for these. Each maps to exactly one fix:

```
is a slot SELECTION, not a time preference     <- bug 4 firing (good)
time_of_day_preference captured                <- must NOT follow a slot pick
spoken weekday corrected                       <- bug 3 firing (good)
spoken option(s) recorded as offered           <- bug 1: the dates must DIFFER
spoken options span N days — recorded as heard <- the multi-day record
build_info                                     <- confirms which build answered
```

## Rollback

```bash
# Theorem
git revert --no-commit 1a711a54 bac29e67 bee94b83 5f3177b5

# Vital Edge
git revert --no-commit 98993df8 d2baf482 32f23fed 834388be

# Joint Venture
git revert --no-commit 24584b83 e5a703b7 9d8d97ff 7c0ce366
```

## Known and NOT fixed — do not report as new

`time_of_day_preference` is still never cleared. That is now correct for a
genuine "mornings please", which SHOULD persist. But if you **state** a
preference out loud early in a call and later ask "is that all you have that
day", you can still be told a filtered view is complete. The honest fix is for
the payload to disclose that a time filter removed slots; it is a separate
change and is deliberately not in this batch.
