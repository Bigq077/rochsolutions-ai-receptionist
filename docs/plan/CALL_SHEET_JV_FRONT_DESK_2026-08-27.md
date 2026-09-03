# Call sheet — JV front-desk toggle, live verification · 2026-08-27

The one thing unit tests cannot prove: the Twilio leg. 23 regression tests pass
and have never once dialled a phone.

| | |
|---|---|
| **Clinic / dial** | `jv_v1` on `jv_v2` · **+44 7367 002651** |
| **Build must read** | `[build_info] running build c64c7fc8` |
| **Toggle from** | Marcus's Susie SIM **+447478558845** *or* his business number **+447586605462** — both authorised |
| **Rings** | the Susie SIM **+447478558845**, for **12s**, then falls through to Susie |
| **Run after** | **20:30** — Thursday clinic hours are 16:30–20:30 |
| **Revert** | `git revert c64c7fc8`, or redeploy `7cfc8425` |

Verified on the dashboards 27 Aug: messaging webhook, `REDIS_URL`,
`SMS_ENABLED` on, `EVAL_STAFF_SMS_TO` unset.

**Before you start — the one question for Marcus:** does the Susie SIM carry
any divert back to +44 7367 002651? It must not. Business number → Twilio is
correct and intended; SIM → Twilio is an infinite loop that Twilio bills every
leg of.

---

## The pass — 6 steps, ~10 minutes

Use a second, unrelated handset for the calls. Text from Marcus's phone.

**1. `STATUS`** → expect *"I'm answering all calls."*
- [ ] Arrived. Nothing else arrived with it — **no second text reading `ok`**
- [ ] SID: `____________`

**2. `OFF`** → expect *"Front desk mode on — calls will ring your phone first,
and I'll pick up anything you miss. Back to normal at midnight, or text ON."*
- [ ] Arrived
- [ ] **FAIL** if it reads *"Sorry — I couldn't change that just now."* That is
      the Redis path refusing to write. Routing is unchanged; stop and tell me

**3. Call the line, do NOT press 1.**
- [ ] Marcus's SIM rings, and the whisper **speaks the calling number** back to
      him ("Business call, from oh seven…"), not just "Business call"
- [ ] After ~12s the call falls through to **Susie**, opening with
      *"…Marcus isn't free to take your call right now, but I can help."*
- [ ] The caller hears **ringback throughout**, never silence

**4. Call again, and DO press 1.**
- [ ] A normal human call. **Susie stays completely silent** — she must not
      join, greet, or speak over the conversation
- [ ] Digits are accepted **during** the whisper, not only after it

**5. `ON`** → expect *"Back on — I'm answering all calls now."*
- [ ] Arrived

**6. Call again.**
- [ ] Susie answers **immediately**. No ring on Marcus's SIM, no 12s delay

**7. Build check.** Render log for the JV service at the end of any call:
- [ ] `[build_info] running build c64c7fc8`. `/health` returns a hardcoded
      `1.0.0` and will lie to you

---

## Two things worth trying while you are there

- Text **"can you turn it off tomorrow"** from Marcus's phone → must arrive as a
  normal patient-style text and toggle **nothing**. Matching is exact on the
  whole message; substring matching is banned precisely so this stays a text.
- Text **`OFF`** from the unrelated handset → must fall through to the ordinary
  patient path and change no routing. The sender number is the credential.

## If it does not work

| Symptom | Look at |
|---|---|
| No reply at all to `STATUS` | Messaging webhook on the number — and whether it points at the service that is actually running this build |
| *"Sorry — I couldn't change that"* | `REDIS_URL` on that service. `set_mode` returns `None` rather than faking a write |
| Reply arrives, routing does not change | `[call_mode]` and the mode reason in the `/ms/incoming` log line — it says whether the gate read `override` or `config` |
| Toggle confirms, then silently reverts | `[call_mode] confirmation SMS returned no SID` — the deliberate revert. `SMS_ENABLED` / Twilio credentials |
| Marcus's phone never rings | The loop rule above, and `dial_phone` |

An override expires at the next **London midnight** by Redis TTL alone. If you
run this close to midnight, the state you leave behind clears itself anyway.
