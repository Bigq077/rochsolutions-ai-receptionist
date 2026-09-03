# SMS Call-Mode Toggle — Build Plan

**Status:** scoped, not started
**Written:** 2026-08-04
**Branch:** build on `latency-eval`, cherry-pick to `jv-v1-onboarding` / `vitaledge-onboarding`
**Depends on:** nothing to build; see §2 for the one runtime dependency

---

## 1. What this builds

A clinic texts `OFF` to their own Susie number. Inbound calls then ring the
practitioner's phone first; Susie picks up only what they miss. They text `ON`
to go back to Susie-answers-everything. The override expires automatically at
the next London midnight.

**The call-routing behaviour already exists and is fully implemented.**
`/ms/incoming` reads `call_overflow.enabled` at
[app/media_streams/router.py:274](../../app/media_streams/router.py#L274) and
emits a human-first `<Dial>` with a "press 1" whisper leg (`/ms/screen`,
`/ms/screen-gather`, `/ms/after-dial`). Config lives at
[app/clinics/jv_v1/clinic.json:57](../../app/clinics/jv_v1/clinic.json#L57),
`enabled: false`.

The only gap is that `enabled` is read from a repo file through an mtime-keyed
cache in `get_clinic()`, so changing it is a commit plus a Render redeploy. The
`_call_overflow_note` in that JSON claiming a flip is "instant" is **wrong**.

So this build is three things:

1. A Redis override layer in front of that read.
2. A one-line change at the gate.
3. A command branch in the inbound-SMS webhook that writes the override.

### Non-goals

- DTMF control surface (separate build, shares §4.1 unchanged)
- Web dashboard — belongs with runtime multi-tenancy, `PRODUCTION_READINESS_PLAN.md` Phase 4
- Schedule-based auto-switching
- A "human only, no Susie" mode — it turns every missed call into indefinite
  ringback, failing criterion 3 of production-ready. Excluded deliberately.
- Any change to `/ms/screen`, `/ms/screen-gather`, `/ms/after-dial`. They are
  only reachable via the `<Dial>` the gate emits and need nothing.

---

## 2. Runtime dependency: `SMS_ENABLED`

`send_sms` returns `None` — it does not raise — when `SMS_ENABLED` is off
([app/notifications/sms.py:75](../../app/notifications/sms.py#L75)). On
`latency-eval` it defaults `false` deliberately.

A toggle that silently succeeds is worse than no toggle: the clinic does not
know whether their phone is about to ring. **Resolve this inside the feature
rather than as an external gate:**

> The override is applied only if the confirmation SMS returns a Twilio SID.
> No SID → delete the key, log an error, leave routing unchanged.

This makes the feature self-disabling wherever SMS is off, needs no separate
blocker, and matches the failure already documented in `smart_sms_router.py`
lines 336–347, where a latch was set on a send that never happened.

**Redis is also required.** `redis_set_json` degrades to a per-process
`_MEM_JSON` dict when `redis_client` is `None`
([app/storage/redis_store.py:203](../../app/storage/redis_store.py#L203)).
With multiple Render workers that produces a toggle that appears to work
intermittently — the worst possible bug class here. §4.1 must refuse to write
when `redis_client is None`.

---

## 3. Redis key contract

```
key:   call_mode:{clinic_id}
type:  JSON string, TTL-bearing
value: {
         "mode":     "human_first" | "ai_first",
         "set_by":   "+447586605462",     # E.164 sender
         "set_at":   "2026-08-04T14:22:11+01:00",
         "expires_at": "2026-08-05T00:00:00+01:00"
       }
```

TTL = seconds until the next `Europe/London` midnight, capped at 12h and
floored at 60s. `LONDON_TZ` already exists at
[app/booking/booking/utils.py:17](../../app/booking/booking/utils.py#L17) — use
it, do not construct another `ZoneInfo`.

Expiry is enforced by Redis `SETEX` alone. No scheduler, no cleanup job, and
if Redis is lost the clinic falls back to the `clinic.json` default
automatically. `expires_at` in the payload is for the reply copy only.

---

## 4. Build steps

### 4.1 — New module: `app/clinic_call_mode.py`

Roughly 70 lines. No imports from `flow.py`, `connection.py`, or any route.

```python
async def resolve_overflow(clinic_id: str, clinic: dict) -> tuple[bool, str]:
    """Return (human_first_enabled, reason). Never raises."""
```

Resolution order:

1. `redis_get_json(f"call_mode:{clinic_id}")` → `mode == "human_first"`,
   reason `"override"`
2. Else `bool((clinic.get("call_overflow") or {}).get("enabled"))`,
   reason `"config"`
3. On **any** exception, or when `redis_client is None` → fall through to (2),
   reason `"config:redis_unavailable"`

This sits on the critical path of every inbound call. It must be wrapped so
that nothing it does can propagate — a broken toggle must degrade to current
behaviour, never to a failed webhook.

```python
async def set_mode(clinic_id: str, mode: str, set_by: str) -> dict | None:
    """Write the override. Returns the stored payload, or None if Redis is
    unavailable (caller must then not claim success)."""

async def clear_mode(clinic_id: str) -> None:
    """Delete the override — used to revert a toggle whose confirmation SMS
    failed to send."""

async def current_mode(clinic_id: str, clinic: dict) -> dict:
    """For STATUS: {"mode", "source", "expires_at"}."""
```

`set_mode` returns `None` rather than raising when `redis_client is None`, so
the SMS handler can refuse to confirm.

### 4.2 — Gate change: `app/media_streams/router.py`

Line 274, currently:

```python
if _overflow.get("enabled") and _dial_phone:
```

becomes a call to `resolve_overflow`, with the reason folded into the existing
log line at 281 so the Render log answers "why did Susie answer?" without a
Redis read.

`ms_incoming` is already `async`. Keep the `try/except` at 265–271 wrapping the
resolver call too.

**This is the entire engine change.** One conditional and one log string.

### 4.3 — Command branch: `app/routes/twilio.py`

Insert inside `sms_inbound` (line 1531), **after** the clinic resolve at line
1566 and **before** the `try:` at 1568.

Placement is load-bearing in both directions:

- **After** the idempotency lock at 1558 — a Twilio webhook retry must not
  double-send the confirmation.
- **Before** the pending-name lookup and `_handle_general_inbound_sms` — a
  command must never be forwarded to the practitioner as a patient text.

```python
_cmd = _parse_call_mode_command(body)
if _cmd and _sender_is_authorised(sender, _clinic):
    return await _handle_call_mode_command(
        cmd=_cmd, sender=sender, clinic=_clinic, clinic_id=..., request=request,
    )
```

The authorisation check runs **before** the command dispatch, not after, so an
unauthorised sender's `ON` falls through to the existing patient path unchanged
and is forwarded to the practitioner as it is today. A patient who happens to
text "on" sees no behaviour change.

#### Command grammar

Case-insensitive, whitespace-stripped, exact match on the whole body:

| Input | Action |
|---|---|
| `OFF`, `SUSIE OFF`, `FRONT DESK` | `mode = human_first` |
| `ON`, `SUSIE ON` | `mode = ai_first` |
| `STATUS`, `SUSIE STATUS` | read-only |

⚠️ **Do not use `STOP` as the off command.** `_SMS_OPT_OUT` at
[app/routes/twilio.py:1349](../../app/routes/twilio.py#L1349) is
`{"stop", "stopall", "unsubscribe", "quit"}` — carrier-level opt-out keywords
that Twilio itself intercepts. Reusing one would be both broken and a
compliance problem.

Substring matching is banned. `"can you turn it off for tomorrow"` from an
authorised number must reach the practitioner as a normal text, not toggle
anything.

#### Authorised senders

Compare `normalize_phone(sender)` (from `app.flows.triage_legacy`, line 1463)
against, in order:

```
clinic["call_overflow"]["dial_phone"]
clinic["transfer_phone"]
clinic["operational"]["owner_notification_sms"]
```

Precedent for this exact candidate-list shape:
[app/media_streams/connection.py:1581](../../app/media_streams/connection.py#L1581).
Empty and missing values must be skipped, not matched.

No PIN. On the SMS route the sender number is the credential, and the blast
radius is who answers the phone — not booking or patient data. (The DTMF
surface, if built later, does need a PIN: caller ID is meaningfully easier to
spoof than an SMS origination.)

### 4.4 — Reply copy

State the resulting condition in plain English. Never acknowledge the command —
the clinic must not have to remember which way the switch points.

```
OFF     → "Front desk mode on — calls will ring your phone first, and
           I'll pick up anything you miss. Back to normal at midnight,
           or text ON."

ON      → "Back on — I'm answering all calls now."

STATUS  → "Front desk mode, until midnight tonight."
        → "I'm answering all calls."

failure → "Sorry — I couldn't change that just now. Calls are still
           being answered as normal. Please try again shortly."
```

Send with `send_sms(to=sender, message=..., from_number=<clinic's To number>)`
— reply to the **sender**, not to a configured clinic number, or a locum
toggling from their own phone gets no confirmation.

Then apply §2: no SID → `clear_mode()` (or restore the prior value for `ON`),
log at `error`, and do not report success anywhere.

**Do not text at expiry.** The expiry is stated in the confirmation; a midnight
text to a physio's personal phone is a support ticket.

Return `PlainTextResponse("ok")` in all paths, matching the rest of the handler.

---

## 5. Failure modes and required behaviour

| Condition | Required behaviour |
|---|---|
| Redis unavailable | `resolve_overflow` returns the `clinic.json` value; `set_mode` returns `None`; clinic gets the failure copy; routing unchanged |
| `SMS_ENABLED` off | Override written then reverted; failure copy is itself unsendable, so the clinic gets nothing — acceptable **only** because routing is provably unchanged. Log at `error`. |
| Clinic has no `call_overflow` block (theorem, demo — legacy `CLINICS` dict) | `resolve_overflow` → `False`. Commands from that clinic reply "not available on your line." Never crash. |
| Twilio retries the webhook | Idempotency lock at line 1558 absorbs it before the command branch |
| Authorised sender has a pending name confirmation | Command wins. Rare and acceptable; note it in the code comment. |
| Override says `human_first` but `dial_phone` is empty | Gate falls through to Susie — the existing `and _dial_phone` clause already handles this. Keep it. |
| Unrecognised text from an authorised number | Falls through to `_handle_general_inbound_sms` unchanged |

---

## 6. Tests — `tests/regression/test_sms_call_mode_toggle.py`

Resolver:

- `test_no_override_uses_config_true`
- `test_no_override_uses_config_false`
- `test_override_human_first_beats_config_false`
- `test_override_ai_first_beats_config_true`
- `test_expired_override_falls_back_to_config`
- `test_redis_none_falls_back_to_config`
- `test_redis_raising_falls_back_and_does_not_propagate`
- `test_clinic_without_overflow_block_returns_false`

Command parsing:

- `test_off_on_status_synonyms_parse`
- `test_stop_is_not_a_toggle_command` — guards the `_SMS_OPT_OUT` collision
- `test_substring_does_not_toggle` — "turn it off tomorrow" must not match
- `test_unauthorised_sender_falls_through_to_patient_path`
- `test_authorised_match_across_all_three_config_keys`
- `test_empty_config_number_never_matches`

End-to-end through the webhook (mock `send_sms`):

- `test_off_writes_override_and_confirms`
- `test_no_sid_reverts_override` — the §2 rule
- `test_duplicate_message_sid_does_not_double_send`

TwiML shape:

- `test_gate_emits_dial_when_override_human_first`
- `test_gate_emits_connect_stream_when_override_ai_first`

Per CLAUDE.md §5, every behavioural fix ships with a regression test that fails
before and passes after. The suite baseline is red by design (**95** failures) —
verify by diffing the failing set, not by looking for green.

---

## 7. Live verification

Unit tests cannot prove the Twilio leg. Required before this is called done, per
clinic:

1. Text `STATUS` → expect "I'm answering all calls."
2. Text `OFF` → expect the front-desk confirmation.
3. Call the clinic number from an unrelated phone → practitioner's phone rings;
   **do not** press 1 → expect fall-through to Susie with the `call_overflow.greeting`.
4. Repeat step 3, **press 1** → expect a normal human call, and Susie silent.
5. Text `ON` → expect "Back on."
6. Call again → Susie answers immediately, no ring delay.
7. Confirm `[build_info] running build <sha>` in the Render log matches the
   commit under test — `/health` returns a hardcoded `1.0.0` and proves nothing.

Vital Edge has no obs corpus, so replay verification is impossible there; live
call only.

Set `ring_timeout` to **12–15s**, not the current 20. The caller hears ringback
rather than dead air so it does not breach the p95 target, but 20 seconds before
a receptionist speaks is too long.

---

## 8. Deploy sequence

1. Build and land on `latency-eval` (not a live line — push freely).
2. Full §7 verification against the `latency-eval` service.
3. Cherry-pick to `jv-v1-onboarding`, then `vitaledge-onboarding` — live
   clinics, so out-of-hours timing, a revert commit prepared in advance, and
   coordination with the clinic.
4. Fix the stale `_call_overflow_note` in `jv_v1/clinic.json` in the same
   commit as the resolver — it currently tells the next reader that flipping
   `enabled` is instant.
5. Theorem: needs a `call_overflow` block adding to the legacy `CLINICS` dict in
   `clinic_config.py` before the toggle means anything there. Out of scope for
   this build — track separately.

---

## 9. Setup and network requirements (onboarding)

Not build steps. These are the per-clinic checks that decide whether the toggle
is usable on that line at all, and they must be on the onboarding checklist —
at cohort scale this conversation happens 230 times.

### 9.1 Call topology — decide this first

The toggle only governs calls that arrive at the Twilio number. How patients get
there determines whether it is useful.

| Topology | What it is | Toggle usable? |
|---|---|---|
| **A — publish the Twilio number** | Clinic advertises the Twilio number directly (what JV does: `phone` = `+44 7367 002651`). Susie owns the front door. | **Yes.** Nothing required from their provider at all. |
| **B1 — keep their number, unconditional divert** | Clinic keeps their published number, diverts *all* calls to Twilio. Patients never see the Twilio number. | **Yes.** Behaves identically to A. Expected to be the common case. |
| **B2 — keep their number, divert on no-answer** | Their carrier already rings the desk first and only forwards misses. | **No — leave `call_overflow` off.** |

**B2 must not have our overflow enabled.** The carrier is already doing
human-first; stacking ours on top gives ~20s of carrier ring, then ~15s of
Twilio ring, then Susie — 35s+ before anyone speaks. The toggle is also
meaningless there: the thing the clinic would want to switch is the carrier's
divert rule, which no text of ours can reach. B2 clinics already have what they
are asking for.

### 9.2 The loop rule — the one hard constraint

> **`dial_phone` must never be a number that diverts back to the Twilio number.**

Otherwise: patient → Twilio → rings `dial_phone` → no answer → that line
diverts to Twilio → new inbound call → rings `dial_phone` → … Twilio bills
every leg.

In topology B1 this is the easy mistake: divert the **landline**, ring the
**mobile**. Two different devices, and the mobile must not carry its own divert
to the Susie number. Clinics frequently set that up themselves before onboarding
and won't think to mention it — ask explicitly.

### 9.3 SMS capability — check before promising the feature

⚠️ The Twilio number must be **SMS-capable**. UK mobile-format Twilio numbers
(`+447…`) are; UK geographic/landline-format numbers generally are not.

JV's `+447367002651` is fine. **A clinic that wants a local-looking `01`/`02`
number for their area cannot have the SMS toggle** and needs the DTMF surface
instead. Establish this at number selection, not after.

No US A2P/10DLC registration applies — that is US-only. UK-to-UK number
messaging has no registration step and therefore no lead time.

### 9.4 What we need from the clinic — four items

1. The mobile number(s) permitted to control the toggle (usually one, max three).
2. The number to ring first (`dial_phone`) — usually the same mobile.
3. Explicit confirmation that **that mobile does not divert to the Susie
   number** (§9.2).
4. Topology choice (§9.1). If B1, they set the divert with their provider —
   we cannot do it for them.

No new hardware, SIM, phone system, or provider contract.

### 9.5 What we configure

- Inbound SMS webhook on the number: Twilio console → number → Messaging →
  "A message comes in" → `POST https://<service>/twilio/sms/inbound`.
  **Per-number dashboard config, not in the repo** — it will be missed unless
  it is on the checklist.
- `SMS_ENABLED=true` + Twilio credentials on that Render service (§2).
- `call_overflow` block in the clinic's config: `dial_phone`, `ring_timeout`
  (use **12–15**, not the current 20), `whisper_text`, `greeting`.

### 9.6 Consequences to brief the clinic on

- **The practitioner must save the Twilio number as a contact.** Toggle texts go
  to the Twilio number; call diverts forward calls, not texts, so texting their
  own published number does nothing.
- **Patient texts to their published number are unaffected** — they stay on the
  clinic's existing phone and never reach Susie's inbound-SMS handling.
- **Susie's outbound texts come from the Twilio number**, which patients will
  not recognise. The templates name the clinic; there is no fix short of
  publishing the Twilio number.
- **Press 1 or the patient hears nothing.** If the practitioner answers and
  starts talking without pressing 1, the whisper leg is still open — the patient
  hears silence and is then handed to Susie. Brief every practitioner once.
- **Voicemail cannot steal the call.** Voicemail cannot press 1, so a call that
  hits it screens through to Susie. This is why the accept-flag, not
  `DialCallStatus`, is authoritative in `/ms/after-dial`.
- **Caller ID on diverted lines** may be rewritten by the carrier to the
  forwarding number. `_is_clinic_own_number`
  ([connection.py:1563](../../app/media_streams/connection.py#L1563)) already
  detects and suppresses this, so Susie asks the patient for their number rather
  than prefilling the practitioner's. Longer call, nothing broken.

### 9.7 Costs to disclose

- Front-desk mode adds an outbound leg to every inbound call — those calls bill
  roughly double, but only while it is switched on, which is why it expires
  nightly.
- **Topology B1: their provider bills the forwarded leg** as an outbound call
  from the clinic's line. Usually pennies, but it is their bill. A clinic
  discovering it on an invoice is an avoidable bad conversation.

---

## 10. Effort

| | |
|---|---|
| `app/clinic_call_mode.py` + resolver tests | 3 h |
| Gate change + TwiML tests | 1 h |
| SMS command branch + parsing/auth tests | 3 h |
| End-to-end tests | 1 h |
| Live verification, 2 clinics | 1 h |
| **Total** | **~1.5 days**, plus deploy ceremony on the two gated branches |

---

## 11. Open decisions

- **Cap length.** Next-midnight expiry assumes the toggle is a "today" decision.
  A clinic covering reception all week has to re-text daily. Confirm with a
  clinic before adding a `FOR 3 DAYS` form — the daily re-text is a feature if
  it stops a clinic drifting into a state they've forgotten about.
- **Enabling before the toggle exists.** `"enabled": true` in
  `jv_v1/clinic.json` is a one-line config change giving demonstrable
  human-first overflow now, provided Marcus is briefed to press 1. Independent
  of this build.
