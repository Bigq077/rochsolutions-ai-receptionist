# Latency Eval — Harness Setup (step by step)

**Goal:** stand up an isolated place to take real calls on the JV clinic and
capture `[LAT]` timing — a **new Twilio number → new Render service (branch
`latency-eval`) → `jv_v1`**, with its own Redis, touching nothing live.
**Branch base:** `jv-v1-onboarding` (95c4fdb). **Live JV line/number/service unaffected.**
**Date:** 2026-07-13

```
 NEW test number ──▶ NEW Render service ──▶ branch: latency-eval ──▶ clinic_id = jv_v1
 (+44… you buy)      (Frankfurt, autoDeploy OFF)   (JV code + premium tone)   (JV clinic.json)
                              │
                              └─▶ OWN Redis, OWN calendar, owner-SMS redirected to you
```

Most steps are dashboard actions only you can do (Twilio/Render accounts). The
one in-repo step (number→clinic map) I can commit for you once you have the number.

---

## Isolation invariants — verify ALL before the first real call

A JV latency test call runs the *real* JV booking flow. Left unchecked it would
write to JV's live Google Calendar and text Marcus. Neutralise every outbound
side-effect on the eval service:

1. **Separate Redis** — a dedicated Redis instance/URL. Never share the live
   `REDIS_URL` (session-state collisions with real calls).
2. **No live calendar writes** — point the eval at a throwaway Google Calendar
   (test `calendar_id`), or simply don't complete bookings during timing runs.
3. **No owner SMS to Marcus** — redirect the JV owner-alert recipient to *your*
   number for the eval service (confirm the field/env before first call).
4. **Test callers only** — you/known testers. Redact any transcript before it
   becomes a committed fixture (UK GDPR — health data). `[LAT]` logs are
   timings + enums only, no PII by design.
5. **Frankfurt region** — same as live, for EU data residency.

---

## Step 1 — Provision a new Twilio number

- In the Twilio Console, buy a **new** UK number (or reuse a spare **test** number — never the live JV line `+447367002651`).
- Leave its Voice webhook unset for now (Step 6 sets it once the service URL exists).
- Note the E.164 number (e.g. `+447XXXXXXXXX`).

## Step 2 — Map that number to `jv_v1` (in-repo, on `latency-eval`)

Clinic is resolved from the dialled number via `TWILIO_TO_CLINIC` in
`app/clinic_config.py:23`. An unmapped number falls through to `demo`, so the eval
number **must** be added:

```python
TWILIO_TO_CLINIC = {
    "+447XXXXXXXXX": "jv_v1",     # LATENCY-EVAL test line — isolated, not live
    "+447367002651": "jv_v1",     # (live JV — leave as-is)
    ...
}
```

- This is a one-line commit **on `latency-eval` only** — never cherry-picked to live.
- Belt-and-braces: also set `MEDIA_STREAMS_CLINIC_ID=jv_v1` (Step 5). Note it's only a *last-resort* fallback (fires only if the number resolves empty), so the map entry above is the real mechanism.
- **I can make this commit for you** — just give me the number.

## Step 3 — Provision isolated Redis

- Create a new Render Redis (or any Redis) in **Frankfurt**. Copy its internal URL for `REDIS_URL` in Step 5.
- Do not reuse the live instance.

## Step 4 — Create the Render service

Create a **new Web Service** in the Render dashboard (do **not** edit the committed
`render.yaml` blueprint — the live service `rochsolutions-ai-receptionist` is
defined there; the VE service was likewise created standalone in the dashboard):

| Setting | Value |
|---|---|
| Name | `rochsolutions-latency-eval` |
| Repo | same GitHub repo |
| Branch | **`latency-eval`** |
| Region | **Frankfurt** |
| Build | `pip install -r requirements.txt` |
| Start | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --timeout-keep-alive 75 --log-level info` |
| Health check | `/health` |
| **Auto-Deploy** | **OFF** — deploy manually so a push never surprises a running eval |

## Step 5 — Environment variables

Set on the eval service. Group A can copy the live values; **Group B MUST differ**;
Group C is eval-specific.

**A. Shared-safe (copy from live):**
```
ANTHROPIC_API_KEY, ASSEMBLYAI_API_KEY, OPENAI_API_KEY,
ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID,
TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
SESSION_SECRET, SENTRY_DSN (optional), ENV=production
```

**B. Must be separate / eval-specific (isolation):**
```
REDIS_URL                  = <the new eval Redis from Step 3>
GOOGLE_SERVICE_ACCOUNT_JSON / DEFAULT_CALENDAR_ID = <test calendar, NOT live JV>
BASE_URL / RENDER_EXTERNAL_URL = <this service's own https URL>
<JV owner-alert recipient> = <your number, so Marcus is never paged>
```

**C. Eval controls:**
```
MEDIA_STREAMS_ENABLED   = true          # the v2 pipeline all levers live in
MEDIA_STREAMS_CLINIC_ID = jv_v1         # last-resort clinic fallback
LATENCY_TIMING          = false         # OFF for the first boot (Step 7), then true (Step 8)
WS_A_FAST_FIRST_CHUNK   = false         # all lever flags default OFF
WS_A_MIN_WORDS_FIRST    = 6             # (used only when WS-A is ON)
```

## Step 6 — Point Twilio at the service

- In the Twilio number's Voice config, set **A Call Comes In** → Webhook (HTTP POST) →
  `https://<eval-service-url>/ms/incoming`
  (the media-streams TwiML entry — `app/media_streams/router.py:197`).
- Signature verification is on (`_verify_twilio_signature_ms`), so `TWILIO_AUTH_TOKEN` in Step 5 must be correct.

## Step 7 — Smoke test (flags still OFF)

Deploy manually, then place one test call to the eval number and confirm:

- Render logs show `[ms_conn] clinic_id resolved: jv_v1 (to=+447XXXXXXXXX)` — routing is correct.
- Susie greets as **Joint Venture** and the **premium tone** is present (persona/trust-the-silence) — confirms the JV base + tone shipped.
- Call is on the media-streams pipeline (v2), not legacy.
- **No** live-JV side effects: nothing in the real calendar, no SMS to Marcus.

If routing lands on `demo`, Step 2's map entry didn't deploy — recheck the branch/number.

## Step 8 — Baseline capture

- Set `LATENCY_TIMING=true`, redeploy.
- Run **≥30 conversational turns** across a few calls (mix of question, booking, name/phone capture).
- Pull the `[LAT]` lines from Render logs → offline parser → **p50/p90/p95** of `ttfa_ms`, `chunk_gate_ms`, `tts_first_byte_ms` (per `LATENCY_MEASUREMENT_SPEC.md`).
- **This all-levers-OFF baseline is the number every lever is measured against.** Do not touch a lever until it's recorded.

---

## Rollback / teardown

- Nothing here can affect live: separate number, service, Redis, calendar.
- To pause: set Auto-Deploy OFF (already) and suspend the Render service.
- To tear down: delete the eval Render service + Redis, release the Twilio number, delete `origin/latency-eval`. No live config references any of it.

---

## Prerequisites checklist

- [ ] New Twilio test number (Step 1)
- [ ] Number→`jv_v1` committed on `latency-eval` (Step 2) — *hand me the number*
- [ ] Separate Redis (Step 3)
- [ ] Render service on branch `latency-eval`, Frankfurt, autoDeploy OFF (Step 4)
- [ ] Env vars, Groups A/B/C, isolation invariants confirmed (Step 5)
- [ ] Twilio Voice webhook → `/ms/incoming` (Step 6)
- [ ] Smoke test: routes to `jv_v1`, JV tone, no live side-effects (Step 7)
- [ ] `LATENCY_TIMING=true`, baseline p50/p90/p95 recorded (Step 8)
