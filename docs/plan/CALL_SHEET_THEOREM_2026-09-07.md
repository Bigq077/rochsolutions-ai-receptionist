# Theorem call sheet — 7 September 2026

`latency-eval` = **`f5ec636c`** · `production` = **`08e99fab`** (revert target
for anything already live).

**This is now the most important call outstanding.** Theorem's Acuity path has
had no live call since 2 September, and it has since accumulated three separate
changes — two already on production, one waiting.

---

## Why this call carries more than it did this morning

| change | where | verified by a call? |
|---|---|---|
| **P8** — closed-day vs lead-time | `_check_availability_acuity`, **Theorem's path only** | ❌ shipped to production 7 Sep |
| **read-back steer** | needed its own call site in `_build_theorem_v3` | ❌ on production, only ever exercised on northgate |
| **Phase A** — provider routing | the condition that sends Theorem to every Acuity executor | ❌ on `latency-eval` |

Four calls today all went to **+447366263180**, which is northgate:
`google_calendar`, single-site, SMS off, template prompt. It shares **none** of
those with Theorem.

### What Phase A did, and why it needs this call specifically

Eight dispatches changed from

```python
if _resolve_clinic_id(session) in ("theorem", "theorem_v2", "theorem_v3"):
```

to

```python
if uses_acuity(session):        # reads booking_system from clinic.json
```

Behaviour is identical by test — `booking_system == 'acuity'` selects exactly
those three ids and nothing else, asserted over every clinic. But **every
Acuity operation Theorem performs now reaches its executor through a different
condition**, and no phone call has taken that path.

Booking, lookup, cancel, reschedule, availability and patient lookup are all
behind it.

---

## The call

Ring **+447380841468** (theorem_v3).

| turn | say | what it exercises |
|---|---|---|
| 1 | "Hi, I'd like to book an appointment." | greeting, `_build_theorem_v3` |
| 2 | answer the location question — **"Alcester"** | the location ladder, Theorem-only |
| 3 | "What have you got?" | **P8 + `uses_acuity` → `_check_availability_acuity`** |
| 4 | pick a slot | slot resolution on the Acuity payload |
| 5 | give a name — **"Quentin, surname R-O-C-H, Roch"** | surname parse + the read-back steer |
| 6 | confirm the number | |
| 7 | **"Yes, book it"** | **`uses_acuity` → `_book_appointment_acuity` — a REAL write** |

Then, on the same call or a second one:

| 8 | "Actually, can you cancel that?" | **`uses_acuity` → `_cancel_appointment_acuity`** |

**Cancel through Susie, not in the calendar.** The delete path is part of what
is being verified, and cancelling in Acuity directly would prove nothing about
it.

---

## What to check afterwards

In the Render log for the Theorem service:

```
[build_info] running build 08e99fab        <- or f5ec636c if you promote first
Row built — ... name=Quentin Roch          <- not "Quentin R-O-C-H"
```

and, on the booking turn, that it reached the **Acuity** executor rather than
the Google one. If `uses_acuity` had gone wrong, the symptom would be a booking
attempt against a Google calendar Theorem does not have — a refusal, not a
wrong write, because `_ACUITY_CREDENTIALLED_CLINICS` and the Google guards both
fail closed. Loud, not silent, by design.

Listen for:

* the read-back naming **"Quentin Roch"** in full;
* a **closed day** reported as closed rather than "too soon to book", if you can
  find one — that is P8, and Sunday is the easiest test;
* the times still carrying **"in the morning"** — Theorem keeps the suffix, only
  northgate drops it.

---

## Order I would do it in

1. **This call, against production as it stands** (`08e99fab`). That clears P8
   and the read-back steer on the path they have never been exercised on, with
   no new code in the way.
2. Then promote `f5ec636c` and **repeat turns 3, 7 and 8** — the three that go
   through `uses_acuity`.

Doing it that way separates "was the existing work sound on Theorem" from "did
the routing change break anything", which one call cannot.

If you would rather do it once: promote first, make the call, and accept that a
failure has two candidate causes.
