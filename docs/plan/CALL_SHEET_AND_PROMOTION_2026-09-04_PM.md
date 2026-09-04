# Call sheet + production promotion — 4 September 2026, afternoon

**Demo line: +447366263180** (northgate). `latency-eval` auto-deploys here.
Before dialling, confirm the build is up: the Render log prints
`[build_info] running build 42a4bcb2` at call cleanup. That line is the only
proof of what is running — `/health` returns a hardcoded 1.0.0.

Everything below was written from the two calls you made this morning,
`CA9c39d09f` (07:47) and `CAdd64c466` (11:33). Every fix is verified red-then-green
against a neutered build and the full suite's failing set is byte-identical to
this morning's baseline: **97 failed / 8,258 passed**, unchanged failing set.

---

## Part 1 — the four calls

Run them in this order. Each is short. The point of each is one line, in bold.

### Call 1 — the wrong-day acceptance (B-138) and the dead pick (B-139)

This is the call that matters most: it reproduces `CAdd64c466` turns 8–17,
where a question about Wednesday was booked as Thursday and a pick on the
offered times died.

| You say | What must happen |
|---|---|
| "Hi, I'd like to book an appointment — I was playing football and I rolled my ankle" | Screening question about the ankle. Answer it truthfully-negative. |
| "Anytime next week" | A numbered readout: Number 1 / 2 / 3, three days with times. |
| "Yeah the 10 past 12 time works" | She should say she doesn't have that on the day she offered, and either offer the nearest day that does have it or ask which day you mean. |
| **"Do you have a 10 past 12 for Wednesday, for example?"** | **She must NOT read this as accepting a slot.** Before today she answered by confirming a THURSDAY appointment. She should treat it as a question about Wednesday. |
| She then says Wednesday doesn't have 12:10 but offers e.g. three other Wednesday times | Listen to the three times and note them. |
| **Pick one of those three by name** — "oh yeah, twenty to ten will work" | **She must confirm THAT time and move on to your name.** Before today the pick resolved to nothing and she re-read the day with a *different* set of times. |

FAIL if: she confirms a day you did not choose; or she re-reads the day after
you have already picked; or she offers times she has just said she does not have.

### Call 2 — the spoken fragment (B-140)

Harder to force deliberately; it fires when a hold phrase plays and the model
then opens with "Let me check…". The reliable way to provoke a hold phrase is
to make her do a second, slower lookup.

| You say | What must happen |
|---|---|
| "Can I book an appointment" | Reason / screening as normal. |
| "Next week please" | The numbered readout. |
| "Actually, what about Wednesday specifically?" | She may play a hold phrase — "Sorry, still with you —" or similar. |

**Listen to the sentence immediately after any hold phrase.** It must be a
whole sentence. Before today the caller heard
*"Sorry, still with you — wednesday's availability properly for you."*
A sentence with no verb is a FAIL. Silence after the hold phrase, followed by
her actual answer, is the CORRECT new behaviour.

### Call 3 — the reason and the screening turn (B-136 / B-137)

| You say | What must happen |
|---|---|
| **Open with the complaint, unprompted:** "Hi, I was playing football and I rolled my ankle, I'd like to book in" | The screening question arms on that sentence. |
| Answer the screen | Booking proceeds. |
| Complete the booking | **The confirmation SMS and the call record must carry the reason** ("rolled my ankle"), not blank. On `CAdd64c466` the record's reason was `None` on a call whose first sentence *was* the reason. |

### Call 4 — the ASAP / sparse-rota fix (7eb61dd2)

| You say | What must happen |
|---|---|
| "I need an appointment as soon as possible" | Treated as a valid answer to "when suits?", not re-asked. |
| "I can't wait a week" | **The days she offers must move CLOSER, not further away.** |

---

## Part 2 — promotion to `production`

Do this only after Call 1 and Call 2 both pass. Calls 3 and 4 are desirable,
not gating — their fixes touch record-keeping and day selection, not the
booking write.

**Record the revert target first.** Write it down somewhere outside this
machine:

```
production before promotion = 4eda31f3
```

`git log origin/production ^origin/latency-eval` is **empty** — production holds
nothing canonical does not, so this is a genuine fast-forward and not a merge.

```bash
git fetch origin && git log --oneline origin/latency-eval ^origin/production | wc -l
```

That should print **31**. Then:

```bash
git push origin origin/latency-eval:production
```

`autoDeploy` is on, so this reaches Vital Edge, JV and Theorem within minutes.
Three services, three clinics, real patients.

### After the push

1. Watch for `[build_info] running build 42a4bcb2` in each service's log.
2. **Make one real call to one live clinic line** and take it to a booking.
   An engine change is not verified until a live line has answered.
3. If anything is wrong:

```bash
git push --force-with-lease origin 4eda31f3:production
```

### What is NOT in this promotion

* `SMS_ENABLED` and `APPOINTMENT_REMINDERS_ENABLED` are per-service env vars
  and are untouched. The code defaults stay OFF on both branches.
* `SMS_TEST_NUMBERS` must never be set on a live service. The cost guard is
  wired at the single send choke point; the free test path is `GET /dev/sms`
  and it is double-gated behind `ADMIN_KEY` *and* a non-empty
  `SMS_TEST_NUMBERS`.
* Nothing from `jv_v2`, `vitaledge-onboarding` or `theorem-onboarding`. Those
  branches are legacy and were superseded, not merged.

---

## Part 3 — what is still open, and why it was left

| Item | Why it is not in this promotion |
|---|---|
| Keypad clobber during a readout | Same code path as the ASAP fix that just landed. Two changes to one readout path in a day, with only one round of calls between them, is how a regression ships. Next session, after these calls prove the path. |
| STT hears "10 past 12" as "a temperature" | **Measured and deliberately not touched.** `temperature` is a boosted keyterm because it is a red-flag answer word for northgate. Zero occurrences across 4,795 stored caller turns — there is no corpus evidence of frequency, and the change would weaken a safety screen's vocabulary to fix a defect seen once. Needs the stored wav and a reproduction, not a guess. |
| Two calls in a row ended at "shall I go ahead and book that in?" | You said you hung up deliberately on `CAdd64c466`. Nothing to fix until it happens on a call you did not end. |
| `flow.py` refactor | Frozen. Post-meeting project. |

---

## Appendix — the five engine commits since your last call

| SHA | What a caller would notice |
|---|---|
| `42a4bcb2` | She no longer speaks a sentence fragment after a hold phrase. |
| `69d30b5d` | A pick on times she offered mid-conversation now lands; a time she has just refused is never recorded as offered. |
| `9e4dc3b3` | Asking about another day is no longer read as accepting this one. |
| `b24b1154` | A call that opens with the complaint keeps that complaint as its reason. |
| `7eb61dd2` | "I can't wait a week" moves the offered days closer. |
