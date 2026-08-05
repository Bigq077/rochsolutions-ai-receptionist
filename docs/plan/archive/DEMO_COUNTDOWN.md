# Demo countdown — Thu 23 → Wed 29 July 2026

Day-by-day plan to a production-ready Susie for the People-to-Hands-On-Money demo
on the `latency-eval` number, **Wednesday 29 July**. Today is **Thursday 23 July**
— 6 days including today. Runs alongside `PRODUCTION_SIGNOFF_SCRIPT.md` (the call
matrix) and `REHEARSAL_RUNBOOK.md` (observability setup).

**Governing principle:** every code change is a deploy that must be re-validated,
so code must stop well before Wednesday. Freeze is **Tuesday**. Wednesday is for
confidence, not changes.

**Critical path:** observability ON today → full sweep Fri → fix over the weekend
→ 3 clean runs → **freeze Tue** → demo Wed. If observability slips today,
everything slips a day — it is the one non-negotiable.

**The bar ("production ready"):** every `BLOCKER` and `PROTECT` call in the
sign-off script green; every `WATCH` item triaged (fixed if demo-visible, else
consciously accepted).

---

## Thu 23 (today) — Instrument & smoke-test
**Focus:** prove the engine + capture are healthy end-to-end. No new code.
- Enable observability: throwaway Postgres → `OBS_DATABASE_URL`,
  `OBS_CAPTURE_ENABLED=true`, `python -m app.obs.migrate`, restart. *(Quentin — infra)*
- Decide SMS posture now (preflight P4): OFF for rehearsals, or ON knowing it
  pings Marcus's real number (`owner_notification_sms`).
- Run a **4-call smoke subset**: C1 (999), C2 (cauda refusal), C6 (happy-path
  booking), C7 (no phantom).
- **Go/no-go:** capture writes a DB row + `[obs.store] captured` in logs, and the
  4 smoke calls reveal no catastrophic regression. If capture doesn't work, fix
  THAT before anything else.

## Fri 24 — Full sign-off sweep #1 + F-021 evidence
**Focus:** discovery. Find everything now, while a weekend can absorb it.
- Run the **entire 14-call matrix** once. Score every call.
- Run **C8 (wrong-service) 2–3×** with different service pairs; capture the
  `check_availability` / `book_appointment` `service` strings and send to
  engineering — that is what makes F-021 fixable.
- Produce a defect list: which BLOCKER/PROTECT failed, current state of each WATCH.
- **Go/no-go:** a scored sweep + a written defect list + F-021 data in hand. This
  is the worst-news day by design — better today than Tuesday.

## Sat 25 / Sun 26 — Fix window (the buffer)
**Focus:** close only what matters. The eval line is a throwaway number, so
time-of-day doesn't matter yet.
- Fix **only** BLOCKER/PROTECT failures + any *demo-visible* WATCH (e.g. F-029
  cauda over-fire on "behind my back", F-034 triple-confirm). Each via the §6 loop,
  each its own deploy, re-validate that specific call.
- **F-021:** if the captured strings show informal-drift → build the bounded
  resolver; if semantic wrong-choice → defer and mitigate by NOT scripting an
  ambiguous multi-service request into the live demo.
- Coordinate with Jules — same branch; fixes must not collide.
- **Go/no-go:** every BLOCKER/PROTECT fix landed and individually re-validated by
  Sunday night. If a *systemic* problem surfaced, Monday is the last code day — a
  1-day buffer still exists.

## Mon 27 — Sweep #2 = clean-run candidate #1
**Focus:** convergence. **Last day code changes are allowed.**
- Re-run the **full matrix**. This is clean-run #1 if it's all green.
- Anything still red → fix today, re-run the affected calls. After today, no code.
- Confirm Jules is done touching `latency-eval`.
- **Go/no-go:** a full green sweep (clean run #1 banked) OR a short punch-list
  clearable Tuesday morning — nothing structural.

## Tue 28 — Clean runs #2 & #3 → FREEZE → fallback
**Focus:** lock it. No code unless a clean run fails (then it isn't a clean run).
- Two more **full clean runs at demo time-of-day** (the hour you'll demo). 3
  consecutive green = freeze criteria met.
- **FREEZE:** tag the frozen commit; no code after the last clean run. Tell Jules.
- **Record a fallback call** — a clean end-to-end booking to play if the live line
  fails Wednesday.
- **Operator rehearsal:** whoever demos runs the demo script themselves, twice.
  Demos fail on human fumbles too.
- **Go/no-go:** 3 clean runs logged · frozen commit tagged · fallback recorded ·
  rollback command in hand · operator comfortable.

## Wed 29 — DEMO
**Focus:** confidence, zero changes.
- One **final smoke call** in the morning — confirm the line's up, deploy green,
  calendar reachable. Not a code change.
- In pocket: the fallback recording, the frozen commit SHA, the rollback command.
- Demo.

---

## Two things that most often blow up a plan like this
1. **Freezing too late.** The temptation Tuesday night is "one more small fix."
   Resist it — an unvalidated change on demo eve is the highest-risk move
   available. The freeze date is the backbone of this plan.
2. **Verifying the system but not the operator.** The engine can be flawless and
   the demo still fumble on a mis-said line or a wrong slot. Tuesday includes
   operator rehearsal, not just system runs.

## Rollback (keep in pocket from freeze onward)
The frozen commit is whatever HEAD `latency-eval` is at freeze. To revert a bad
deploy, force-with-lease `latency-eval` back to the previous known-green SHA
(the same pattern used for every push this cycle). Record both SHAs on Tuesday.
