# Vital Edge — live call session brief (Jules)

**Written:** 2026-08-04 · **Build under test:** `7d1b02b` · **Owner:** Quentin

---

## What this is

Jonathan's clinic (Vital Edge Therapy, Kingston upon Thames) has just moved from
a 24-July engine to the current one — **294 commits in one step**. Your calls are
the gate that decides whether that was safe.

**You are not testing whether Susie sounds good.** You are testing five things,
in this order of seriousness:

1. **Every booking the caller believes was made exists on the calendar** — with
   the right service, the right **duration** and the right time.
2. **Susie never claims a booking is confirmed.** Vital Edge is *provisional*:
   she books a PENDING event and Jonathan confirms personally. "All booked",
   "you're confirmed", "you're in" are **failures**, not wording nits.
3. Nothing that should be declined gets booked.
4. No dead air over ~3 seconds without a filler.
5. Every call leaves a record.

---

## Before your first call

**Everything is live. Nothing is suppressed.** Real SMS, real alerts, real
bookings on Jonathan's own calendar, real end-of-day email to him. That is
deliberate — a test that silences the notification path cannot prove the
notification path works.

**Jonathan has been told test calls are coming.** Bookings from your number are
not real. If Susie ever transfers you to a human, that dials **Jonathan's
mobile** — no case below asks for a human, so don't.

**Call from one consistent number** and tell Quentin which. The calendar event
description carries `Phone: <number>` verbatim, so that is how Jonathan filters
your bookings from real ones. Do **not** rely on saying a fake surname — the
surname is never read back and has been mis-transcribed on live calls.

**Delete your test events afterwards**, or a published slot stays blocked
against a real caller.

---

## The clinic, as of today

Three services. This changed **today** — anything older is wrong.

| Service | Length | Price |
|---|---|---|
| Neck, Back and Shoulders | **30 min, fixed** | **£65** |
| Sports Massage | **60 or 90** | **£125 / £180** |
| Deep Tissue Massage | **60 or 90** | **£125 / £180** |

- **90 minutes is £180.** If you hear £175, that is a failure — write it down.
- **Both** Deep Tissue and Sports have a length choice. Susie must **ask**.
- Neck/Back/Shoulders is fixed — she must **not** ask about length.
- **Withdrawn:** Stress Buster, Muscle / Nerve Injury, Facial Release.
- **ANF (Amino Neural Therapy):** coming soon. Never bookable, never priced.
- Massage only — no physio, reiki, acupuncture, psychotherapy.
- **Minimum age 18.**

---

## The cases

Work `VITALEDGE_PORT_PLAN.md` §8's must-cover table. Cases **3a, 3b, 6a, 6b, 11
and 12 are new today** and have never been exercised on a live call.

The three that matter most, if you only get through a few:

- **Case 2** — Deep Tissue, ask for **90 minutes**. Must not be refused, must
  quote **£180**, and the calendar event must be **90 minutes long**. This is the
  single most important call of the session.
- **Case 10** — book successfully, then push back: *"so I'm all booked in then?"*
  Susie must hold the line that Jonathan confirms.
- **Case 9** — book, hang up, ring back and reschedule it.

---

## What to record for every call

| Field | Where it comes from |
|---|---|
| Time and number you called from | you |
| What you asked for, verbatim | you |
| What Susie said at the closing, **as close to verbatim as you can** | you |
| Did an SMS arrive? What did it say? | your handset |
| Calendar event: present? service? **duration**? time? name? | Google Calendar, *"Vital Edge — Available"* |

**Reconcile against the Google Calendar, not Acuity.** Acuity is not Vital
Edge's booking system.

**A call is not a pass because it sounded fine.** It is a pass when the calendar
agrees with what Susie said.

---

## Stop conditions — ring Quentin, don't keep testing

- A booking Susie described as made that is **not on the calendar**
- A calendar event with the **wrong duration** (90 booked, 60 on the calendar)
- Susie saying a booking is **confirmed** rather than pending
- A 90-minute Deep Tissue **refused**
- Anything under-18 or non-massage getting booked

Rollback exists and takes under a minute, so stopping early costs almost nothing.
Carrying on past a write-path failure costs a real patient.

---

## For whoever reads the logs afterwards

```bash
[build_info] running build 7d1b02b   # must match, or the deploy did not land
[ms_stt] init — stt_variant=u3.5-pro
[obs.store] async capture failed     # must be ABSENT — capture was broken until today
```

Stored calls read back with:

```bash
python -m app.obs.show --clinic vital_edge --last 1
```
