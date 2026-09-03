# Call sheet — verify the night ports · 2026-08-26

Verifies the 6 fixes pushed to `theorem-onboarding` (`f05c59f7`) and
`vitaledge-onboarding` (`324174dc`).

**Do Theorem first** — it is the more divergent branch and the higher risk.

| Line | Number | Build to expect |
|---|---|---|
| Theorem (Mark) | **+447380841468** | `[build_info] running build f05c59f7…` |
| Vital Edge (Jonathan) | **+447426779875** | `[build_info] running build 324174dc…` |

`/health` returns a hardcoded `1.0.0` — ignore it. Wait ~2–3 min after the push
for Render to build.

**Hang up at the name request** on every call unless the step says otherwise.
That exercises everything without writing to a real clinic calendar. If you do
complete a booking, **cancel it by calling Susie**, never by deleting the
calendar entry.

---

## What each call is actually testing

| Fix | How you trigger it | What proves it |
|---|---|---|
| Scarcity (`that_is_the_only`) | Get to a day with ONE slot, ask "any other slots that day?" | She says "that's the only one on…" instead of re-offering the same time as a question |
| Multi-day offer record (B-88) | Get a 2-day numbered readout, then ask "what else have you got?" | Log: `spoken options span 2 days — recorded as heard` |
| Weekday/date guard | Name a weekday, listen to the date she speaks | The weekday she says matches the date she says |
| Echo barge-in | Stay **completely silent** through a long readback | No "Yes, go on." to nothing. Log line if it fires |
| Keypad containment | Press **1** on a numbered readout | She takes option 1 normally (I changed the resolver, not the map) |

---

# THEOREM — +447380841468

Susie asks **which clinic** before availability (Alcester or Redditch).
She should **NOT** ask why you're coming in — that gate is Theorem-only and
asking would be a regression.

## T1 — multi-day record  ← the most important call

```
Susie: (greeting)
You:   I'd like to book an appointment please
Susie: which clinic?
You:   Redditch
Susie: what service / who for?
You:   a physio assessment
Susie: when would suit?
You:   as soon as possible
Susie: (reads "Number 1, <day> — <time>. Number 2, <day> — <time>")
You:   actually I can't do that week, have you got anything the week after
Susie: (reads two more numbered options)          <-- CHECK THE LOG HERE
You:   what else have you got?
Susie: (should offer times you have NOT already been given)
You:   (hang up)
```

**Log check** — the single line that reads the whole slot fix:

* `spoken options span 2 days — recorded as heard`  → **working**
* `could not resolve`                                → gate 1 open
* `span 2 days — not recorded`                       → gate 2 open

**Ear check:** the "what else have you got?" answer must not contain a time she
already read out.

## T2 — weekday/date + echo

```
You:   I'd like to book an appointment
Susie: which clinic?
You:   Redditch
You:   a physio assessment
Susie: when?
You:   have you got anything on Tuesday?
Susie: (names a date)      <-- is the weekday she says correct for that date?
       ... then SAY NOTHING AT ALL for 15 seconds ...
You:   (hang up)
```

**Ear check 1:** if she says "Tuesday the 1st of September", check that date
really is a Tuesday. A wrong pairing means the guard did not catch it.

**Ear check 2 (the echo test):** during your 15 seconds of silence she must not
say "Yes, go on." / "Sorry — go ahead." to something you never said.

**Log check:** `barge-in #N was Susie's own audio (partial=…) — resuming instead
of acking`. This has **never fired on a real call**, so its absence proves
nothing — but if you hear a phantom ack and there is no such line, tell me.

## T3 — scarcity + keypad

```
You:   I'd like to book an appointment
Susie: which clinic?
You:   Alcester
You:   acupuncture
You:   what's the soonest you have?
Susie: (numbered options)
You:   press 1 on the keypad                <-- keypad containment
Susie: (should take option 1 normally)
You:   do you have any other slots on that day?
Susie: (if that day has one slot: "that's the only one on <day>")
You:   (hang up)
```

**Ear check:** she must answer the question. Being re-asked "Does that work for
you?" instead is the old `that_is_the_only` bug.

---

# VITAL EDGE — +447426779875

One location (Kingston). Jonathan publishes slots ~1–2 weeks ahead;
**Monday and Friday are generally unavailable**, so don't read a missing Monday
as a defect. She asks **60 or 90 minutes** for sports and deep tissue.

## V1 — multi-day record

```
You:   I'd like to book a massage please
Susie: which treatment?
You:   deep tissue
Susie: sixty or ninety minutes?
You:   sixty
Susie: when would suit?
You:   as soon as possible
Susie: (numbered options)
You:   actually I can't do that week, anything the week after?
Susie: (numbered options)                     <-- CHECK THE LOG HERE
You:   what else have you got?
You:   (hang up)
```

Same three log outcomes as T1.

## V2 — weekday/date + echo + the 90-minute duration

```
You:   I'd like to book a sports massage
Susie: sixty or ninety minutes?
You:   ninety
You:   have you got anything on Tuesday?
Susie: (names a date)     <-- weekday correct for that date?
       ... SAY NOTHING for 15 seconds ...
You:   (hang up)
```

**Optional, only if you want the duration re-checked end to end:** complete this
booking instead of hanging up, then confirm the diary entry is **90 minutes**,
not 60. If you do, **cancel it by calling Susie back**, not from the calendar.

## V3 — scarcity

```
You:   I'd like to book a massage
Susie: which treatment?
You:   neck, back and shoulders
You:   what have you got on <a day she has already mentioned>?
Susie: (if one slot) ...
You:   do you have any other slots on that day?
Susie: "that's the only one on <day>"
You:   (hang up)
```

---

## After the calls

Send me the Render log for any call that sounded wrong. The lines worth grepping:

```
spoken options span 2 days
could not resolve spoken option
kept scarcity sentence
spoken weekday corrected
was Susie's own audio
build_info
```

**Rollback** if either line misbehaves — whole branch, or one fix by its own SHA
(see `PORT_HANDOVER_2026-08-26.md` §7):

```bash
# Theorem
git revert --no-commit f05c59f7 c6d0eddc f97a954f 9d68670b d000e486 29b2e14f

# Vital Edge
git revert --no-commit 324174dc 3d604d93 35cc7057 3513333a c59f1562 17a0ac9c
```
