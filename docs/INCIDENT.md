# INCIDENT — a clinic is broken right now

**Restore first. Communicate second. Diagnose third.**

A live clinic broken at 9am is not a debugging session. Real patients are
calling. Every minute spent reading `flow.py` is a minute the phone is broken.

> ⚠️ **Nine blanks below are marked `FILL:`.** They are the facts only Quentin
> has — on-call number, Render service names, clinic contacts. Until they are
> filled in, this page cannot be followed by anyone else, which is the whole
> point of it. Five minutes of work.

---

## 0. On-call

| Role | Who | Reach them |
|---|---|---|
| On-call engineer | Quentin Roch | `FILL: mobile number` |
| Backup | `FILL: name or "none — single point of failure"` | |

If there is no backup, write "none". An honest single point of failure can be
planned around; an imagined second person cannot.

---

## 1. Severity — decide this in the first 60 seconds

| Sev | Definition | Response |
|---|---|---|
| **1** | **A booking the caller believes exists but does not.** Susie said "you're all booked in" and Acuity has nothing. | Immediate. Restore, then find every affected caller and phone them. |
| **1** | Wrong appointment booked — wrong service, wrong modality, wrong site. Caller expects something that will not happen. | As above. |
| **2** | Calls not answered, dead air, or Susie audibly failing. Patients hear a broken clinic. | Restore within the hour. Failover to the clinic line. |
| **3** | Susie answers and functions but degrades — wrong price quoted, clumsy handling, an FAQ she cannot answer. | Same day. No failover needed. |
| **4** | Cosmetic or internal — a log error, an SMS wording issue, an alert that did not fire. | Next working day. |

**Severity 1 is the one this system is built to prevent and most likely to
produce.** 81 of 97 `except` clauses in `app/tools/receptionist_tools.py` are
broad, so a booking can fail silently while the call sounds perfect. If you are
unsure between 1 and 2, it is a 1.

---

## 2. Restore — in this order

### 2a. Is it us or a vendor?

```bash
curl -s https://<service>.onrender.com/health
```

Returns `{"ok": true, "redis": true, ...}`. If `redis` is `false`, sessions are
not persisting — every call will lose state mid-conversation.

Then check the vendor status pages: Twilio, AssemblyAI, ElevenLabs, Anthropic,
Acuity. A vendor outage changes the response — you cannot fix it, so failover and
communicate.

### 2b. Kill switch — back to the legacy path, no dead air

Set on the affected Render service:

```
MEDIA_STREAMS_ENABLED=false
```

Verified in `app/media_streams/router.py:212` — `/ms/incoming` then returns a
TwiML `<Redirect>` to `/twilio/voice` immediately, with no dead air, and the WS
endpoint closes with 1001. This is the **fastest** way to take the new pipeline
out of the call path without touching Twilio.

It moves callers to the older `<Gather>` path, which is less capable but works.

### 2c. Roll back the deploy

Render dashboard → the clinic's service → **Events** → the last known-good
deploy → **Rollback**.

`render.yaml` declares `autoDeploy: true` with no branch pin — **the branch is set
per-service in the Render dashboard**, so know which branch feeds which service
before you touch anything:

| Clinic | Render service | Branch | Status |
|---|---|---|---|
| Theorem Health | `FILL: service name` | `FILL:` | Live, real patients |
| Vital Edge | `FILL: service name` | `FILL:` | Live |
| Joint Venture | `FILL: service name` | `FILL:` | Go-live pending |

⚠️ `autoDeploy: true` means **a push deploys immediately**. During an incident,
do not push to a branch that feeds a live service unless that push *is* the fix.

### 2d. Full failover — hand the phone back to the clinic

If 2b and 2c have not restored service within ~15 minutes, stop trying to fix it
and give the clinic its phone back. In the Twilio console, point the clinic's
number at the clinic's own line, or at voicemail.

`TRANSFER_FALLBACK_NUMBER` (`app/config.py:38`) is the in-call transfer target
and defaults to `+447502211207` — **verify this is the number you want before
relying on it.** A default in code is not a decision.

---

## 3. Communicate

### 3a. Tell the clinic — before they call you

| Clinic | `clinic_id` in the store | Contact | Phone | Email | Writes through | Id field |
|---|---|---|---|---|---|---|
| Theorem Health | `theorem_v3` | `FILL: name` | 07870 166861 | info@theoremhealth.co.uk | acuity | `acuity_booking_id` |
| Joint Venture Physio | `jv_v1` | Marcus (`FILL: confirm`) | +44 7367 002651 | Jointventurephysiotherapy@gmail.com | google_calendar | `calendar_event_id` |
| Northgate Physio | `northgate` | `FILL: name` | +44 7366 263180 | hello@northgatephysio.example | google_calendar | `calendar_event_id` |
| Vital Edge Therapy | `vital_edge` | `FILL: name` | +44 7545 862307 | vitaledgetherapy@gmail.com | google_calendar_provisional | `calendar_event_id` |

*Contacts read from clinic config; `clinic_id` and `booking_system` verified
against the live obs store on 2026-09-15.*

**Two traps in that table.**

1. **The live Theorem clinic reports as `theorem_v3`, not `theorem`.** It is
   built in `clinic_config.py`, not `app/clinics/`, so anything that resolves a
   clinic by looking for a `clinic.json` directory silently misses it.
2. **`booking_system` is not `booking.system`.** The knowledge base says JV and
   Northgate use "Carepatron" — that is what they tell patients. The code writes
   through **google_calendar** for both. Verify a booking against the
   integration, not the description.

**Every live clinic returns a booking id**, so a confirmation with no id is
checkable on all four.

### 3b. Holding message — send it, then go back to fixing

> Hi [name] — heads up that Susie is having a problem this morning and I'm on it
> right now. I've [taken her off your line / switched her to the backup] so your
> calls are [going to your usual phone / going to voicemail] in the meantime.
> Nothing is being lost at your end. I'll message you the moment she's back, and
> I'll tell you exactly what happened once I know.

Three rules: say it yourself before they notice; say what you have already done;
promise an explanation but **do not** promise a cause you have not found.

### 3c. If it is Severity 1 — find the affected callers

A booking that does not exist will not fix itself. The caller is expecting an
appointment nobody knows about.

**Find them mechanically — do not read transcripts looking for this.** A call
where the booking silently failed reads as a *perfect* call: Susie confirms, the
judge scores it 5/5, the suite passes it. The only reliable signal is the
disagreement between `booking_confirmed` and the provider id:

```bash
python .claude/skills/susie-triage/scripts/collect_failures.py --obs --days 7
```

`PHANTOM_BOOKING` findings are exactly this class. Then:

1. Get the window — when did the bad deploy go out, when was it restored?
2. Cross-check against Twilio call logs for the clinic's number.
   `tests/auto/results/` is **not** production.
3. For each, confirm against the real calendar (Acuity / Google Calendar), or
   Carepatron for a portal clinic.
4. **Phone every caller whose booking is missing.** Not SMS. Phone.
5. Tell the clinic exactly who was affected and what you told them.

Going forward the `booking_not_written` alert (critical, immediate SMS) fires on
this condition live, for calendar-backed clinics, whenever `OBS_ALERTS_ENABLED`
is on. It cannot fire for Carepatron clinics — there is no id to check.

---

## 4. Only now — diagnose

Restored and communicated? Now it is a debugging session. Use the `susie-debug`
skill: reproduce as a scenario, localise from the earliest failing check, search
`git log` for prior fixes, fix with the smallest diff, prove with a regression
test.

`susie-debug/references/known-bugs.md` lists nine classes that have broken
before. Check it before theorising — several have regressed more than once.

---

## 5. Write it down

Same day, while it is fresh. Append to this file under "Incident log":

```
### YYYY-MM-DD — <one line: what the caller experienced>
Severity:    1-4
Detected:    how, and how long after it started
Duration:    first bad call → restored
Clinics:     who was affected
Callers:     how many, how many contacted
Restore:     what actually fixed it (kill switch / rollback / failover / vendor)
Cause:       what was actually wrong
Fix:         commit sha
Prevention:  the test or guard that would have caught it — and whether it exists now
```

The last line is the only one that changes the future. If the honest answer is
"nothing would have caught this", say that — it is a finding.

---

## Known gaps in this plan

Be honest about what is not covered, so nobody discovers it at 9am:

- **Booking integrity is alerted on all four live clinics.**
  `booking_not_written` (critical) fires when Susie confirms a booking and no
  calendar id came back. Every live clinic writes through an id-returning
  integration, so the check is meaningful everywhere.
- **`final_state` is unusable.** All 929 stored calls report `GREETING`,
  including completed bookings. The column is never advanced past its initial
  value, so it cannot be used to find where a call stopped. Use `turn_traces`
  or the transcript instead.
- **Monitoring.** Observability is built — **22 modules** on
  `origin/latency-eval` (`git ls-tree --name-only origin/latency-eval app/obs/`)
  — but gated. `app/obs/__init__.py` states capture is "gated behind
  `config.OBS_CAPTURE_ENABLED` (**default OFF**)". Whether it is switched on in
  any Render environment is **not verifiable from this repo — check the
  dashboard.** If it is off, **you find out a clinic is broken because the clinic
  tells you.** That is the single biggest gap on this page.
- **`origin/jv-v1-onboarding` carries 2 of those 22 modules** — `__init__.py` and
  `alerts.py` only. It can page an operator on hard failure and captures nothing
  else: no call record, no quality score, no digest.
- **No status page and no second responder.** If Quentin is unreachable, there is
  no documented path at all.
- **No documented Acuity reconciliation.** Step 3c is a manual process; there is
  no script that answers "which calls in this window have no matching booking".
  That script is worth writing before the cohort arrives.

---

## Incident log

*(none recorded yet)*
