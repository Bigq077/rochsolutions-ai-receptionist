# Call suite — `B-36` verification, 3 Aug 2026

Verifies **both causes** of `B-36` on a live call. Everything in `e387aac` and
`d5b257c` is proven in tests only; this is the dial-time debt.

| | |
|---|---|
| Service | `low-latency-joint-venture` (the `latency-eval` branch) |
| Number | `+447366263180` |
| Clinic | `jv_v1` |

**Read the SHA from the Render log, not `/health`.** `/health` returns a
hardcoded `"version": "1.0.0"` and cannot report a commit. The only proof of what
is running is `[build_info] running build <sha>` at call cleanup.

**Do not hardcode the expected SHA here** — this sheet has already been through
three builds, and a stale value tells you to stop when nothing is wrong. Get the
expected value at dial time:

```bash
git rev-parse origin/latency-eval | cut -c1-12
```

If the log shows an older SHA the deploy has not landed yet — wait and re-dial.

**As of the last push, that is `0577827d6837`** — which carries `B-36` cause 2
(`e387aac`, `d5b257c`) *and* the `B-37` slot-guard fix (`38dd929`), so calls 1–3
below and the `B-37` re-check can all be taken in one pass.

This is the same service and clinic the original `CA23199d089` ran on, so these
calls hit the exact configuration that produced the bug.

---

## Dial in this order. Call 1 first, and it is not optional.

Call 1 is the **over-fire** check. It matters more than the phantom tests,
because over-firing is the direction that has already caused real harm: Gate 5c
stripped a legitimate confirmation and abandoned a **completed** booking on
2026-06-12. If call 1 fails, stop the suite and revert — a guard that eats real
confirmations is worse than the bug it replaced.

### Call 1 — reschedule that SUCCEEDS · the over-fire check

You need an existing appointment to move. Ask to reschedule, give the number when
asked, and when Susie asks the move confirmation question answer plainly —
*"yes, go ahead"*.

**Expect:**
- the move to actually happen (verify in Acuity)
- the confirmation spoken **normally and in full** — *"that's you moved to…"*
- **no** `[ms_gate5f]` line anywhere in the log

**FAIL if:** you hear *"Sorry — before I confirm anything, would you like me to
move it for you?"* after the move succeeded, or any `[ms_gate5f]` line appears.
That is the guard arming on a successful write. Revert `d5b257c` and stop.

### Call 2 — reschedule that stays REFUSED · the phantom check

Same setup, but answer the move confirmation question ambiguously — *"umm, I
guess so, maybe"* — or talk over it so the gate cannot read a clear yes.

**Expect:**
- `reschedule_appointment BLOCKED` in the log
- `[ms_llm] reschedule_appointment did not succeed … guard ARMED for the
  reschedule family this turn`
- **nothing moved in Acuity**
- and if the model claims success, `[ms_gate5f] false reschedule confirmation …`
  followed by the caller hearing *"…would you like me to move it for you?"*

**The specific thing to listen for:** you must **not** hear *"shall I go ahead and
book that in?"*. That is the R5 leak — a booking CTA landing after a phantom
reschedule, where your next "yes" would book a brand-new appointment. If you hear
it, say **no**, hang up, and check Acuity for a stray booking.

**Also acceptable:** the model may simply not claim anything, because `e387aac`
now tells it the move did not happen. That is the steering layer working and is a
pass — Gate 5f is the backstop, not the first line.

### Call 3 — cancel that stays REFUSED

Ask to cancel. When Susie asks the retention question, answer with a bare
*"yes"* rather than the word "cancel".

**Expect:** `cancel_appointment BLOCKED`, **nothing cancelled in Acuity**, and if a
claim is made, the caller hears *"…would you like to keep this appointment, or
cancel it altogether?"*

**FAIL if:** the appointment is actually cancelled, or Susie says it has been.

---

## If it goes wrong

Revert is `git revert d5b257c` (the guard) and, if needed, `e387aac` (the
steering). They are independent: `e387aac` alone is safe to keep — it only adds
text to already-failed tool results and cannot suppress a real write.

## After a clean run

Record the result under `B-36` in `REGISTER_B_U.md`, then the fix is ready to
cherry-pick to `jv-v1-onboarding` and `vitaledge-onboarding`. Note **both**
branches also lack cause 1 (`fe97b82`), so the port is three commits, onto files
236–264 commits diverged — it is a conflict-resolution exercise with its own test
run per branch, not a clean cherry-pick.

---

## Call 4 — `B-37`, added after call 1 exposed it

Take this on any of calls 1–3, or on its own. At the move confirmation
(*"Shall I go ahead and move it for you?"*) answer with **"go ahead"** — no
"yes", no "yeah". Then try **"go for it"** on another call.

**Expect:** the reply is heard first time. In the log you should see

```
[ms_conn] write CTA outstanding — bypassing slot guard for 'uh go ahead'
```

immediately followed by `[ms_llm] iteration=1` on the same utterance.

**FAIL if:** `[ms_conn] slot fragment ignored — re-arming` appears for the reply,
or the watchdog re-asks *"which of those would you like?"* — a slot re-ask when
the outstanding question is the move CTA. That is B-37 unfixed.

Note `"go for it"` takes a different path from `"go ahead"`: L1 returns
`unsure` and the L2 classifier decides. Slightly slower, and worth hearing
once — it is the phrase that lost a booking on `CA7e389a47`.
