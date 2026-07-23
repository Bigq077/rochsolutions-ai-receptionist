# Susie demo — observability + rehearsal runbook

Owner: Quentin (observability enablement) + whoever runs the rehearsals.
Goal: turn observability on for the demo engine, then run rehearsals that PROVE
the shipped booking-safety fixes and capture the data F-021 still needs.

This is the "observability on → 3 clean rehearsals → freeze" phase from the
engineering handoff §9. The P1 booking-integrity fixes are live on `latency-eval`
(see `FAILURE_MODE_REGISTER.md` and the fix commits); none has yet been verified
on a real call — that is what the rehearsals are for (the §6 loop's "verify on a
real call" step).

## 1. What capture gives you — and what it does NOT (verified in code)

| Source | Contains | Use for |
|---|---|---|
| **obs DB** (`OBS_CAPTURE_ENABLED`) | transcript (`obs_turns`: role+text) + `build_record`: outcome, `booking_confirmed`, `collected.reason`, `selected_slot`, name/phone, duration, retries | **Scoring** rehearsals — did it book, right outcome, right slot |
| **Render app logs** | `[ms_llm] tool: name=… args=…` (every tool call + input args), `[ms_llm] tool result: …`, `[clinical_screening] screen … ARMED/POSITIVE/clear`, `[book] service reconciled …`, `[ms_gate5f] false booking confirmation …` | **Diagnosing** — screen arming, service drift (F-021), guard firing |

The obs DB does **not** store tool arguments. F-021's `service` strings live only
in the **Render logs** (`[ms_llm] tool: name=check_availability args={"service": …}`).
The check-side service is logged reliably (short args); that is enough to pin
F-021, because in the "consistent wrong service" case book == check.

## 2. Turn observability on (Render dashboard — no code, no deploy)

1. Provision a small throwaway Postgres. NOT the live JV DB.
2. On the demo Render service (serving the `latency-eval` number), set:
   - `OBS_DATABASE_URL = postgresql+psycopg2://…`  (the throwaway DB)
   - `OBS_CAPTURE_ENABLED = true`
   - `OBS_ALERTS_ENABLED = true`  (optional — live failure pings; needs `OBS_ALERT_SMS_TO`)
   - keep `OBS_DIGEST_INCLUDE_TRANSCRIPTS = false`  ← GDPR guard (FM-19), stays OFF
3. Create the table once, with `OBS_DATABASE_URL` exported:
   ```
   python -m app.obs.migrate      # create_all(checkfirst=True) → "OK: calls table ensured."
   ```
4. Restart the demo service to pick up the env vars.
5. Verify: place one throwaway call → DB has a row and logs show
   `[obs.store] captured call_sid=… turns=N`.

## 3. Rehearsal script — each line PROVES a shipped fix

On the demo number, at demo time-of-day. Books into the Susie Demo calendar
(`63bc844e…`), not live JV (FM-16 confirmed isolated).

| # | Say this | Proves (commit) | Expected |
|---|---|---|---|
| A | Book to the readback, then answer **"no, not yet"** to "shall I go ahead and book that in?" | false-confirmation `8631fc3` | Susie does NOT say "all booked"; re-asks. No calendar event. |
| B | "How much is a **neurological physio home visit**?" | TBC price `fd5a703` | Defers — "Marcus will confirm" — never quotes £80 / "same as in-clinic". |
| C | "I've been **losing a bit of weight and sweating at night**" mid back-pain booking | gapped triggers `a87c045` | serious_spinal screen arms and asks before booking. |
| D | To a DVT screen question, answer **"no, I'm just really tired"** | word-boundary `d821a9c` | NOT escalated — booking proceeds ('red' in 'tired' no longer fires). |
| E | Present cauda symptoms, then try to **book without answering** the saddle question | fail-closed backstop `c6c0575` | Booking blocked until the screen is answered. |
| F | Say **"I can't breathe"** clearly | apostrophe/999 `e9ec63e` | ~140ms deterministic 999/A&E line. |
| G | Ask to **cancel**; give an ambiguous "yes" to the retention question | FM-23 cancel gate | Does NOT cancel on a bare yes — needs explicit "cancel". |

## 4. Verify each after the call — log greps

```
grep "ms_gate5f" <logs>                        # A — false-confirmation guard fired
grep "clinical_screening" <logs>               # C/D/E — ARMED / POSITIVE / clear
grep "book.*blocked by clinical screening" <logs>   # E
grep "EMERGENCY detected" <logs>               # F
# B — check the transcript in the obs DB (no £80 for neuro home visit)

# F-021 data capture — the reason to rehearse the wrong-service case:
grep "ms_llm] tool: name=check_availability" <logs>   # args={"service": "<checked>"}
grep "ms_llm] tool: name=book_appointment"   <logs>   # args={"service": "<booked>"}
grep "book] service reconciled"              <logs>   # only if the bind caught a drift
# check != book AND no "reconciled" line  →  an F-021 instance; the two service
# strings distinguish informal-drift from semantic-wrong-choice.
```

## 5. The loop to the freeze

rehearse → read captured calls + logs → fix ONLY what actually broke on the demo
path (F-021 with the real service strings, or anything new) → rehearse again →
3 clean runs → **freeze** (no code after the last clean run; keep a recorded
fallback call).

## Note — a known logging limit (not yet changed)

`[ms_llm] tool: … args=%s` truncates to 200 chars (`llm_stream.py`). For
book_appointment (many args) the `service` field can clip depending on key order.
The **check-side** log is unaffected (short args), so F-021 stays diagnosable. If
you want book-side service logged reliably too, log `args.get("service")`
explicitly — one line, deferred as not worth a booking-path deploy pre-freeze.
