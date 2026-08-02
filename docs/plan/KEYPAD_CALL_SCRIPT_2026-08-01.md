# Keypad Phone Path — Call Test Script

**Build under test:** `4c21557` (`latency-eval`, pushed 1 Aug 2026). Confirm
Render has finished deploying **before** the first dial — a call against
`2b4b7dc` will pass K1 for the wrong reason.

**Number:** `+447366263180` → `jv_v1`, service `low-latency-joint-venture`.
⚠️ That is carried over from the 29 Jul handover sheet;
`docs/plan/DEPLOYMENT_INVENTORY.md` is still a blank template, so **confirm the
number and service in the Render dashboard before dialling.**

**What is under test:** three commits that together own the keypad phone path.

| Commit | What it does |
|---|---|
| `2b4b7dc` A3 | booking uses the number that was *confirmed*, not the one the model typed |
| `09cea10` C3 | one definition of "complete"; an invalid buffer is never queued to the model |
| `4c21557` C2/C4 | typed number is read back at the moment it is typed; prompts updated to match |

**Rollback, in order of preference — write these down before dialling:**

| If | Revert | Consequence |
|---|---|---|
| Read-back is annoying / adds a bad turn | `git revert 4c21557` | drops C2+C4 only. C3 and A3 stay — fabrication stays closed. **Preferred.** |
| Valid numbers being rejected (false re-ask) | `git revert 4c21557 09cea10` | back to A3 only. Fabrication possible again but A3 still corrects the booking. |
| Keypad path broken outright | `git reset --hard 2b4b7dc` + force-push | last resort; loses both fixes |

---

## 0 · Rules

| # | Rule |
|---|---|
| R1 | **UK mobile, real handset.** The caller-ID path is what these fixes branch on. Do not test from a `+33` line. |
| R2 | **One case per call.** Never chain two. Hang up between cases. |
| R3 | **Type at a natural pace** — about one digit per second, the way someone reads a number off a screen. Machine-gunning the keypad tests a timing path no caller uses. |
| R4 | **Fix nothing during the run.** Log it and keep dialling. |
| R5 | **Nobody deploys during the window.** A push mid-run invalidates every call before it. |
| R6 | **Note the wall-clock time of each call** — you need it to find the call in Render logs. |
| R7 | **Your ear is a channel the logs do not have.** Note dead air, talk-over, and anything that sounded wrong even if the log looks clean. |

**Reference number to type where a case says "a valid mobile":** use a real
mobile you can check, ideally not the one you are calling from — several cases
only prove something if the typed number ≠ the caller ID.

---

## How to score

**Channel 1 — Render logs.** These fixes emit distinctive lines. Filter on
`[ms_conn` and look for:

| Line | Means |
|---|---|
| `keypad phone committed — <num> + phone_confirmed=True (typed, not caller ID)` | number accepted and committed |
| `keypad number read back for confirmation: <num>` | C2 read-back fired |
| `DTMF buffer '<digits>' is not a UK mobile — re-ask attempt N, keypad_armed=…, digits NOT queued to the model` | C3 caught a bad entry |
| `keypad read-back rejected: '<utterance>'` | caller said no |
| `keypad read-back REJECTED by caller — number cleared, phone_confirmed=False, keypad re-armed (attempt N)` | teardown ran |
| `verbal phone confirm SKIPPED — keypad number already on record` | the load-bearing guard held |
| `[book] A3 — booking phone corrected: model passed X, confirmed number is Y` | **the model tried to book a different number.** Expected to be ABSENT on every case below — if it fires, note it, the fix worked but something upstream is still wrong |

**Channel 2 — the booking itself.** For any case that books, open the calendar
entry and check the phone on it **digit for digit** against what you typed. This
is the only check that cannot be faked by a clean-looking log.

> Note: the 29 Jul sheet references `obs_scorecard.py`. That script is not in the
> repo, and `OBS_CAPTURE_ENABLED` defaults to `false` — so unless obs has been
> switched on in Render, the database channel is not available and Render logs +
> the calendar entry are your two channels.

---

## Block K — the cases

Priority: **BLOCKER** cases must pass before this build goes near a demo.
**SHOULD** cases are correctness checks that can be logged and fixed after.

### K1 · Nine digits, then stop — `BLOCKER`

The call that caused all of this (`CA3590527b`).

1. Book normally until she asks for your number.
2. Decline the caller ID — *"no, it's a different number"*.
3. She says the keypad line. **Type exactly nine digits, then stop and stay silent.**

| Expect | |
|---|---|
| ✅ | after ~5 s: *"That doesn't look like a complete number — could you double-check it and type it again on your keypad?"* |
| ✅ | log: `is not a UK mobile — re-ask attempt 1, keypad_armed=True, digits NOT queued` |
| ❌ **FAIL** | she reads back or books **any** 11-digit number. That is the fabrication bug. Note the number she said — it will not appear in any DTMF log line. |
| ❌ **FAIL** | silence longer than ~8 s |

Then type the remaining digits to complete a valid number and let it book —
confirms recovery works, not just the refusal.

### K2 · Eleven valid digits, confirm — `BLOCKER`

The happy path, and the one every other keypad call depends on.

1. Decline the caller ID, type **a full valid mobile** (different from the one you are calling from).
2. She reads it back: *"Thanks — I've got …. Is that correct?"* Say **"yes"**.

| Expect | |
|---|---|
| ✅ | digits spoken individually and grouped — *"oh seven seven double-oh, nine …"* — not run together |
| ✅ | after "yes" she goes **straight to the warm readback** (name, day, date, time) |
| ✅ | log: `verbal phone confirm SKIPPED — keypad number already on record` |
| ✅ | booking carries **the number you typed**, not the caller ID |
| ❌ **FAIL** | she asks about the number a second time after "yes" |
| ❌ **FAIL** | `[book] A3 — booking phone corrected` appears |

> This is the one link proved by construction rather than by a real call: the
> digits are no longer queued to the model, so it proceeds from the read-back in
> history plus `phone_confirmed`. If she re-asks for the number here, that is the
> finding — report it.

### K3 · Reject the read-back — `BLOCKER`

1. Decline caller ID, type a valid mobile.
2. At the read-back say **"no, that's wrong"**.
3. Type a **different** valid mobile. Confirm it. Let it book.

| Expect | |
|---|---|
| ✅ | *"No problem — go ahead and type the number on your keypad. You can press the star key to reset at any time."* |
| ✅ | log: `read-back REJECTED by caller — number cleared, phone_confirmed=False, keypad re-armed (attempt 1)` |
| ✅ | booking carries the **second** number |
| ❌ **FAIL** | booking carries the first number — the teardown did not run |
| ❌ **FAIL** | she proceeds to the booking readback without re-collecting |

### K4 · Dropped digit — ten digits starting `0` — `BLOCKER`

Pre-C3 this was padded into a number nobody typed.

Type `0` + nine more digits (i.e. drop one from a real 11-digit mobile), stop.

| Expect | |
|---|---|
| ✅ | re-ask, same as K1 |
| ❌ **FAIL** | she reads back a number starting `00…`, or an 11-digit number you did not type |

### K5 · Omitted leading zero — ten digits starting `7` — `SHOULD`

Type a valid mobile **without** its leading zero (`7…`, ten digits), stop.

| Expect | |
|---|---|
| ✅ | **accepted** — read back in `07…` form, no re-ask |
| ❌ **FAIL** | re-asked. This is a real number typed a common way; rejecting it is a false reject |

K4 and K5 are the pair that matters — the old code could not tell them apart and
padded both. They must now behave differently.

### K6 · Landline — `SHOULD`

Type a valid 11-digit landline (`01527…` or `0121…`), stop.

| Expect | |
|---|---|
| ✅ | re-asked — landlines are excluded deliberately, because the confirmation and both reminders are SMS |
| ⚠️ | if a real caller would reasonably book on a landline, **this is the decision to revisit**, not a bug. Note it and we change the predicate. |

### K7 · Ladder terminates — `BLOCKER`

Proves the re-ask cannot loop forever. Deliberately fail three times.

Type 9 digits → stop. Then 9 digits → stop. Then 9 digits → stop.

| Expect | |
|---|---|
| ✅ rung 1 | *"That doesn't look like a complete number — could you double-check it and type it again…"* |
| ✅ rung 2 | *"I'm still not getting a full number. I can use the number you're calling from instead — just say 'use this number', or type it again on your keypad."* |
| ✅ rung 3 | *"I'm still not getting a full number — could you read it out to me instead?"* — and the keypad is now closed |
| ❌ **FAIL** | rung 1 wording repeats a fourth time — unbounded loop |
| ❌ **FAIL** | dead silence at any rung |

### K8 · Escape hatch at rung 2 — `SHOULD`

Fail once (9 digits, stop). At rung 2, say **"use this number"**.

| Expect | |
|---|---|
| ✅ | caller ID accepted, booking proceeds on it |
| ✅ | booking carries the number you are calling from |

### K9 · Reschedule lookup is untouched — `BLOCKER`

Scope regression check — the read-back must **not** fire where the number is a
search key.

1. Call and say you want to **reschedule** an existing appointment.
2. When asked for the number it was booked under, type a valid mobile.

| Expect | |
|---|---|
| ✅ | she looks the booking up — **no** *"Thanks — I've got … is that correct?"* |
| ✅ | log: **no** `keypad number read back for confirmation` line |
| ❌ **FAIL** | she reads the number back before looking it up |

Repeat once for **cancel** if time allows.

### K10 · Ambiguous answer does not wipe a good number — `SHOULD`

The one-shot flag check.

1. Type a valid mobile. At the read-back say something unrelated — **"hang on"** or **"sorry, what?"**.
2. Then continue the booking normally.

| Expect | |
|---|---|
| ✅ | the number survives — she does not clear it or re-arm the keypad |
| ✅ | booking carries the number you typed |
| ❌ **FAIL** | number cleared, or keypad re-armed |
| ❌ **FAIL** | a later "no" (e.g. answering a different question) clears the number |

---

## Minimum set if time is short

`K1 · K2 · K3 · K9` — the reproduction, the happy path, the rejection path, and
the scope guard. K4 and K7 next.

---

## Recording

One file per call, `logs/sweep/K<N>-<case>.txt`, in dial order. `logs/` is
gitignored and stays that way — real numbers. **Never paste raw logs into chat.**

Summarise each call as one row:

| Case | Pass/Fail | Number typed | Number on booking | Notes |
|---|---|---|---|---|

The two columns that settle everything are **number typed** and **number on
booking**. If those differ on any call, that is the finding, whatever the audio
sounded like.
