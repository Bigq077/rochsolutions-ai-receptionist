# Call sheet — 2026-09-04 morning

**Under test:** `latency-eval` @ `22772cc8` — five engine fixes, **none heard on
a call**. Nothing is on `production` (`4eda31f3`, unchanged).

**Before the first call:** confirm the Render log says

```
[build_info] running build 22772cc8
```

That line at call cleanup is the ONLY proof of what is running — `/health`
returns a hardcoded 1.0.0. If it says anything else, stop: you are testing code
that is not this.

**Lines.** Demo = **+447366263180** (northgate). Theorem = its own live number,
out of hours only.

**SMS is OFF.** No confirmation texts will arrive. That is your change, not a
defect — do not chase it.

---

## Order, and why

**Run 4 first, then 1.** Call 4 can RETIRE a fix, so learn that early while you
still have the session. Call 1 tests the half that was wrong on patient lines.
Calls 2, 3 and 6 can be folded together if time is short — see §7.

| # | fix | line | can it fail badly? |
|---|---|---|---|
| 4 | B-133 watchdog hold | demo | **yes — back it out** |
| 1 | declining meridiem | demo | no |
| 2 | B-132 barge-in replay | demo | no |
| 3 | D-A false full stop | demo | unprovable by test |
| 5 | §2.2 completeness | demo | no |
| 6 | Theorem booking WRITE | Theorem | live diary |

---

## 1. The declining meridiem — the one that matters most

`f93a4d2a`. The AGREEING case (`8 am` against `08:00`) is verified live. This is
the half that was wrong on patient lines and has never been called.

**Script**
1. "I'd like to book an appointment"
2. answer the reason question naturally
3. ask for **Monday**
4. **"yeah Monday at 8 pm works"**
5. then, same call: **"OK, 8 am then"**

**PASS** — step 4 is refused or re-asked. Step 5 pins `08:00` and reads back
"eight in the morning".

**FAIL** — the log shows `caller ACCEPTED 2026-09-07T08:00:00+01:00` on step 4.
That line appearing is the failure; its absence is the pass.

> ⚠️ Step 5 is not optional. If 4 passes and **5 fails**, the fix is
> over-rejecting and that is a regression, not a win.

---

## 2. B-132 — a torn-down answer goes again

Fires on a path that ran on **both** calls of 2 Sep, so it should be easy to
reproduce.

**Script** — describe a symptom that earns a long reply ("my ankle's been
giving me trouble for a few weeks"), let her get ~5 seconds in, then say a flat
**"okay"** over the top.

**PASS** — you hear the ANSWER again. Log:

```
barge-in #N tore down a N-chunk answer at playback … re-speaking the last N chunk(s)
```

**FAIL** — you get the closing question alone ("do you have a preference for
when you'd like to come in?") with the answer lost. That is the original defect.

**Also check** she does not replay a *slot readout* through this arm — that has
its own (B-120) path and should log `tore down a slot readout`.

---

## 3. D-A — the false full stop

`57202217`. **This is the one I cannot prove by test.** The false sentence is
gone from the text; whether you still hear a hard stop depends on ElevenLabs
prosody across two synthesis requests. Your ear is the instrument.

**Script** — describe a joint problem and listen to the first long reply.

**PASS** — any pause mid-sentence sounds like a breath.

**FAIL** — it sounds like a full stop and a new sentence starting, especially
before a word like "or"/"and". Report the exact wording you heard.

If it still sounds wrong the fallback is already scoped: stop splitting a first
chunk on a comma inside an unfinished question. One line.

---

## 4. B-133 — the noisy call. **Do not skip.**

This fix INTRODUCES a failure mode, and this is the only call that tests it.

**Script** — put a television or radio on in the room, audible but not
shouting. Get to any question. **Say nothing for ~10 seconds.**

**PASS** — the re-ask still arrives. It may be up to ~1.5s later than before;
that is the fix working.

**FAIL** — no re-ask, silence until the call falls over. That is the watchdog
suppressed by room noise.

**Then grep the log for:**

```
WATCHDOG_VOICE_HOLD_CAP
```

* **absent, or once** — healthy.
* **repeatedly** — the noise floor is the real problem and the timing never
  was. **Tell me and I will back B-133 out rather than tune it.** That is the
  pre-agreed response, not a judgement call on the day.

**Second half, quiet room:** ask her a question, then start answering *the
instant* she stops. You should NOT get talked over. This is the Alcester defect
from yesterday.

---

## 5. §2.2 — no false completeness

Fold into any call that reaches a slot list.

**Script** — after she reads slots out, ask **"is that all you've got that
day?"**

**PASS** — she does not claim the list is complete; she offers more if the data
holds more.

**FAIL** — "the slots I have that day are X or Y" when the diary holds more.

> Note: this is a prompt rule, not enforcement. One clean call is weak evidence.

---

## 6. Theorem — the BOOKING WRITE

**Still untested.** Yesterday's Theorem call exercised the READ path fully
(111 raw slots, bank holidays, week filter, offer, follow-up, acceptance,
read-back — all correct) and stopped before the write, which was the whole
point of it being on the list.

**Out of hours. Book far out, then cancel through Susie on the same call — not
in the calendar.** (The reminder path keys on the calendar title; deleting the
event by hand leaves it armed.)

**Watch for**
1. greeting says **Theorem**, not another clinic
2. she does **not** ask what the appointment is for — Theorem-only rule
3. `event created` — the END time matches the duration she quoted
4. the read-back names the right person and time
5. **cancel completes first time**, no loop
6. bonus: ask about a bank holiday and listen for "too soon to book" — that is
   **P8**, still open, Theorem only

---

## 7. If you are short of time

Two calls cover most of it:

* **Noisy demo call** — §4 both halves, then §5 on the slot list.
* **Theorem call** — §6, with §2 and §3 folded in on the symptom turn.

§1 cannot be folded into anything. Run it on its own.

---

## 8. Known-accepted noise — do not chase these

* `GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON` → Sheets skipped. **Demo line
  only.** Theorem's Sheets works.
* ElevenLabs `401` on `/v1/models` — benign, self-documented.
* `last_bot_prompt truncated at 200 chars and lost its '?'` — fires ~twice per
  call, recovers every time via `last_question`. This is B-31 and it is the fix
  WORKING. Examined 3 Sep and deliberately left alone.
* `[sms] SMS_ENABLED is off` — your change.
* 4 pre-existing `test_filler_guard.py` failures — unrelated.

---

## 9. What to send me afterwards

For each call: the **`call_sid`** and the log. Without the sid I cannot pull the
obs turns, and the Render log rolls.

Specifically useful greps:

```
build_info | WATCHDOG_VOICE_HOLD | WATCHDOG_FIRE | tore down
caller ACCEPTED | event created | slot buf | LAT turn_seq
```

Also worth capturing if you notice it: any turn where she talks for more than
~10 seconds (verbosity, §2.8) and any `content_ttfa_ms` over 4000 (LAT-1). Both
are open and both need live numbers rather than more analysis.

---

## 10. If something goes badly

Revert targets in order — each is the commit BEFORE the fix named:

| back out | revert to |
|---|---|
| B-133 watchdog | `57202217` |
| D-A capitalisation | `591133d9` |
| §2.2 completeness | `bea61a7f` |
| length-rule scoping | `d7097886` |
| B-132 barge-in | `bf85f647` |

Nothing here has reached `production`, so a bad result costs a demo call, not a
patient. **Do not promote anything until the calls pass** — and per CLAUDE.md,
make a real call AFTER any production push as well.
