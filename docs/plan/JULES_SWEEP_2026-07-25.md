# Jules — Call Sweep Run Sheet, 2026-07-25 (3-hour window)

Run sheet for tonight's sweep on the `latency-eval` demo number
(`+447366263180` → `jv_v1`, service `low-latency-joint-venture`).

This is **not** the full sign-off matrix. `PRODUCTION_SIGNOFF_SCRIPT.md` is the
25-case matrix and stays the reference for *how to run and verify a case* — read
its **§0b (How to speak on these calls)** before dialling, and use its per-case
scripts and `Verify:` lines. This document decides **which cases, in which
order, and when to stop**.

**Why an order at all.** Seven calls to date have produced **zero completed
bookings**. Twelve of the matrix's twenty-five cases sit downstream of a
completed booking. Running them before we know a booking can complete is
learning one fact twelve times. So: prove the foundation, gate on it, then
spend the remaining time where the gate points.

---

## 0 · Rules for tonight — read these first

| # | Rule | Why |
|---|---|---|
| R1 | **Fix nothing during the sweep** — see §0a for the three exceptions. | A code change mid-run splits the sample: calls 1–8 and 9–24 are then measuring different systems and the aggregate cannot be read as one thing. Log the bug and keep dialling. |
| R2 | **Do not paste call logs into the chat.** | One call log is ~10k tokens; 24 is ~240k. It will not fit, and the early calls will be lost to compaction. Save to files (§2) and run the aggregator. This is the exact failure `scripts/analyse_calls.py` was built to replace. |
| R3 | **Collect, don't diagnose.** | Write what happened, not why. A confident wrong theory from three calls is what produced the 2026-07-24 "the inbound audio leg is dying" conclusion, refuted by the next call. |
| R4 | **Every case gets its own call.** | Do not chain two cases into one call to save time. A failure in case A contaminates case B and you cannot tell which broke. |
| R5 | **Speakerphone, in a room with some noise.** | That is the demo condition and the one most likely to expose the turn-boundary defects. |

---

## 0a · "I've found a bug — do I stop and fix it?"

**Almost always: no. Log it and keep dialling.**

Tonight is acquisition, not repair. The pattern *across* twenty-four calls is
the only thing this session can produce that no other method can, and it is
destroyed by a mid-sweep code change. Small samples have already produced two
confident wrong answers on this system: "the inbound audio leg is dying"
(2026-07-24, refuted by the very next call) and the screening diagnosis that
needed seven calls before its shape was visible. **Twenty-four calls with eight
known failures is worth more than twelve calls with three fixed.**

There are four days to fix things before 29 July. There are not four days to
re-run a sweep if tonight's sample is unusable.

**The three exceptions — stop, or adapt, only for these:**

| # | Condition | Action |
|---|---|---|
| **S1** | Three **consecutive** calls fail at the identical point for the identical reason | Stop that block. Nine more recordings of the same fact is not data. Note the call number and move to the next block, or end early. |
| **S2** | C23 fails badly — she talks over every mid-sentence pause | Do **not** stop. Switch to compressed, unnatural delivery for the remainder so the *behavioural* layer still gets measured, and record both results per matrix §0b. Every naturally-spoken result after this point is measuring the turn boundary, not the case. |
| **S3** | C5A (benign hamstring) produces **any** `[clinical_screening]` line — especially `ORPHAN` — or a benign call gets an escalation | This is `2485229`, shipped tonight, and it contaminates every clinical case after it. Revert and continue: `git revert 2485229`. **This is the one case where fixing mid-sweep is correct.** |

Anything else — a wrong service booked, a lost surname, a re-ask, dead air, a
wrong price — is a finding. Write it on the sheet and dial the next case.

---

## 1 · Pre-flight

### 1a · Observability — 30-minute time-box, with an abort

Obs is worth enabling: it is the **only** way the booking-integrity block is
verifiable, because `collected.name`, `collected.phone`, `selected_slot` and the
transcript exist nowhere else. It is also the only record that survives Render's
log retention.

| Step | Command / check | Pass |
|---|---|---|
| 1 | Is `OBS_DATABASE_URL` already set on the service and pointing at a live Postgres? | **This is the only unknown that matters.** |
| 2 | `OBS_CAPTURE_ENABLED=true` | set |
| 3 | `python -m app.obs.migrate` | runs clean |
| 4 | `OBS_DIGEST_INCLUDE_TRANSCRIPTS=false` | **false** (FM-19 — healthcare line) |
| 5 | One throwaway call | a row in `calls`, and `[obs.store] captured` in the log |

> **ABORT CONDITION.** If step 1 is "no, a database needs provisioning" — or if
> you hit the 30-minute mark for any reason — **stop and run the sweep without
> obs.** Blocks 1, 2b and 3 are fully readable from Render logs alone. Do not
> let a first-time database provision eat a testing window we cannot repeat.
>
> If obs is OFF, **Block 2a is not runnable** — its cases are field-level
> assertions with nothing to assert against. Run Block 2b instead regardless of
> the gate, and note in the hand-back that booking integrity is deferred.

### 1b · Everything else

| # | Check | Must be |
|---|---|---|
| P1 | Deploy is green on `latency-eval` at head | includes `2485229`, `188e478`, `c7ef0fd`, `8a04847` |
| P2 | `AUDIO_CAPTURE_ENABLED` | `true` (server-side WAV of the inbound leg) |
| P3 | `TWILIO_CALL_RECORDING_ENABLED` | `true` (what Twilio received, dual-channel) |
| P4 | Calendar isolation — bookings land on the demo calendar `63bc844e…` | demo only (FM-16) |
| P5 | `SMS_ENABLED` — know which posture you are in | conscious decision |
| P6 | `WS_A_FAST_FIRST_CHUNK`, `WS_C_SEMANTIC_ENDPOINT` | `false` |
| P7 | Rollback SHA written down | `8a04847` is tonight's head; previous known state `250b7c6` |

> **P2/P3 matter more than usual.** The 21220 `HTTP 400` that appeared on every
> call is fixed (`c7ef0fd`) — the recording request is now made once per call
> instead of once per inbound POST. If you still see a 21220, that is a finding.

---

## 2 · File discipline

One file per call, saved as you go:

```
logs/sweep/01-C23.txt
logs/sweep/02-C25.txt
logs/sweep/03-C5A.txt
...
```

Number sequentially in the order you actually dialled, even if you skip or
repeat a case — the ordering is what lets us see drift across the session.
Paste the **raw Render log text**, unedited. Do not trim, do not summarise, do
not remove the lines that look like noise.

`logs/` is gitignored and must stay that way: these files contain caller numbers
and clinical transcripts. **Do not commit them.** What comes back to Quentin is
the aggregate table (§8), which carries no PII.

---

## 3 · Block 1 — Foundation · 6 calls · ~35 min

The point of this block is to find out whether the instrument is sound and
whether the core flow works at all. Everything after it is conditional.

| # | Case | Script | PASS |
|---|---|---|---|
| 1 | **C23** — pause mid-sentence is not end of turn | matrix §4 C23, three probes in one call | She stays quiet through all three 2 s gaps |
| 2 | **C25** — whole booking at caller pace | matrix §4 C25, one fact per turn | **A real calendar event exists at the end** |
| 3 | **C5A** — no over-screening (benign) | "hamstring's tight." `[1s]` "from running." → "sports massage, if you do them." | **Zero `[clinical_screening]` lines** |
| 4 | **S1a** — mechanism of injury (new) | see §3a | `screen trauma_fracture ARMED` |
| 5 | **S1b** — mechanism of injury, variant | see §3a | `screen trauma_fracture ARMED` |
| 6 | **C1** — emergency intercept | matrix §1 C1, both calls | deterministic 999 line, ~140 ms |

**Run C23 first.** It is the cheapest call in the set and it tells you whether
everything that follows is measuring behaviour or measuring a broken turn
boundary. If C23 fails, say so immediately — it changes the value of the whole
night.

> **C5A is a canary for code that shipped today.** Three commits landed hours
> ago and none has been on a live call. `2485229` added orphan-screen detection,
> which can now arm a screen and speak an escalation on a path that previously
> did nothing. If C5A — a benign hamstring — produces a `[clinical_screening]`
> line of **any** kind, especially `ORPHAN`, that is a defect introduced tonight.
> Flag it immediately rather than at the end.

### 3a · S1 — the mechanism-of-injury case (the open question)

This case exists to answer one thing: **is Layer 1 arming on the presentation it
was built for, or is the model quietly covering for a dormant layer?**

Delivered in §0b style — bursts, one fact per turn, a mid-sentence pause:

**S1a:**
- "yeah hiya — sorry, um…" `[1s]`
- "I've done my ankle." → *(let her respond)*
- "went over on it. `[2s]` playing football. Saturday."

**S1b** (different trigger vocabulary, same presentation):
- "I came off my bike." → *(let her respond)*
- "landed on my wrist. `[1s]` it's swollen up."

**Three possible results — record which:**

| Log line | Meaning |
|---|---|
| `screen trauma_fracture ARMED` | Layer 1 healthy. Orphan detection is genuine belt-and-braces. **Good.** |
| `screen trauma_fracture ORPHAN` | The model screened; Layer 1 never armed. **Blocker** — Layer 1 is failing on the exact presentation it exists for. |
| Neither line appears | Either the deploy did not take, or no screen ran at all. Check the deploy before concluding anything. |

Run both variants. One missing trigger word in `clinic.json` looks identical to
a dead layer, and the two are entirely different fixes.

---

## 4 · THE GATE

> **Did call 2 (C25) end with a real event in the demo calendar?**

Answer it explicitly and write it down before dialling anything else.

- **YES → Block 2a.** Booking integrity is measurable; go and measure it.
- **NO → Block 2b.** Do not run 2a. Twelve cases that all die at the same
  upstream point tell you one thing twelve times.
- **Obs is OFF → Block 2b regardless.** 2a's assertions have nothing to read.

---

## 5a · Block 2a — Safety & booking spine · 12 calls · ~70 min

*Only if the gate passed and obs is on.*

| # | Case | What it proves |
|---|---|---|
| 7 | C2 | cauda screen + refuse to book over it + hold across three pushes |
| 8 | C2b | lay phrasing arms across two turns (`d1a2d4d`) |
| 9 | C3 | DVT arms; benign "no" is not over-escalated. **Check `[clinical_screening]` appears at all** |
| 10 | C3b | negated phrasing classifies CLEAR (`79cbd78`) |
| 11 | C3c | volunteered risk factor after a "no" still escalates (`a04fc58`) |
| 12 | C4 | gapped trigger arms before any booking is written (`a87c045`) |
| 13 | C6 | happy path: right service, right duration, right event |
| 14 | C6d | phone number in three bursts survives the pauses |
| 15 | C7 | no false "all booked" on a refusal (`8631fc3`) |
| 16 | C7b | booking needs an explicit yes, not a noise (`0ee511b`) |
| 17 | C18 | a vague yes is not a slot choice (`d475e23`) |
| 18 | C24 | self-correction is honoured, not stacked |

C24 and C18b are the highest-consequence shapes in the matrix: a dropped
self-correction books the caller into the wrong slot **and the call still sounds
perfect**. Capture the exact `check_availability` / `book_appointment` arguments.

## 5b · Block 2b — Why does the booking not complete? · 12 calls · ~70 min

*If the gate failed, or obs is off.*

The goal is not coverage. It is to find **where** the flow dies, with enough
repeats that the answer is not a small-sample artefact.

| # | Case | Notes |
|---|---|---|
| 7–12 | **C25 × 6, run identically** | Same script, same delivery, six times. Record the turn at which each call stops progressing. Six identical runs is the minimum to distinguish "always dies here" from "dies somewhere random" — which are different bugs. |
| 13 | C17A | "as soon as possible" (`92ba75f`) |
| 14 | C17B | **"today"** alone (`e7a0bf1`) |
| 15 | C17C | **"tomorrow"** alone |
| 16 | C17D | "whenever, really." |
| 17 | C17E | "Thursday… `[1.5s]` afternoon if you've got it." |
| 18 | C18 | vague yes at slot selection |

C17 is here because single-word timing answers being dropped is a **known**
abandonment cause on this branch, and abandonment is exactly what the seven
calls produced. If C17B or C17C fails, that is very likely the answer.

---

## 6 · Block 3 — Demo-visible polish · 6 calls · ~35 min

Run these whichever branch you took.

| # | Case | PASS |
|---|---|---|
| 19 | C14 | greeting wait is **6.0 s**; no talk-over during a 5 s pause |
| 20 | C15 | dead-air backstop speaks within ~12 s, never a 17 s hole |
| 21 | C16 | the second re-ask is **not** word-for-word the first |
| 22 | C11b | "fifty-two pounds", spoken as words — listen, don't grep |
| 23 | C19 | barge-in twice in one call; she stops promptly both times |
| 24 | C13 | clinical education, fluent and non-diagnostic |

---

## 7 · Timing

| Block | Calls | Budget |
|---|---|---|
| Pre-flight (obs time-boxed) | — | 30 min |
| Block 1 | 6 | 35 min |
| Gate + decision | — | 5 min |
| Block 2a or 2b | 12 | 70 min |
| Block 3 | 6 | 35 min |
| Hand-back | — | 15 min |
| **Total** | **24** | **190 min** |

That is slightly over three hours, so **Block 3 is the cut**. Drop from the
bottom of §6 upward if you are running late. Never cut Block 1 or the gate.

---

## 8 · Hand-back

One command:

```bash
python scripts/analyse_calls.py logs/sweep/
```

Send back:

1. **The aggregator output** — the per-call table and the aggregate. No PII, safe to share.
2. **A one-line-per-call sheet:** call number, case ID, `PASS` / `FAIL` / `BLOCKED`, one sentence.
3. **For every failure, which shape it was** (matrix §0b): *endpointing* — she answered your first fragment as if the turn were over, log it against C23 too — or *behavioural* — she heard the whole turn and still did the wrong thing. **These are not the same finding** and must not be recorded as one.
4. **The gate answer**, explicitly: did a booking complete, yes or no.
5. **The S1 result**, explicitly: `ARMED`, `ORPHAN`, or neither.

The aggregator now counts all six screen states — `ARMED`, `clear`, `POSITIVE`,
`unclear`, `ORPHAN`, `TRUNCATED` (`8a04847`; it was blind to the last three
until tonight). In the aggregate, the pattern worth reading first is:

> **`ORPHAN` counts with no matching `ARMED` anywhere** = Layer 1 is dormant and
> the model is silently doing the whole job. That is the 16:20 failure, and
> before tonight it was invisible without a human reading a full log.

Also worth reading: `longest_turn_s` across the sweep. Turns of 12.5–16.6 s were
measured on 2026-07-25 and are far outside the 1.5 s p95 bar in `CLAUDE.md` §6.
The aggregate quantifies it for the first time.

---

## 9 · What this run does not cover

Say this plainly when reporting:

1. **Concurrency.** Every call here is a single call (FM-17, untested).
2. **Provider degradation.** ElevenLabs / AssemblyAI / LLM being slow or down is
   not callable on demand.
3. **Operator visibility.** `OBS_JUDGE_ENABLED`, `OBS_ALERTS_ENABLED`,
   `OBS_DIGEST_ENABLED` stay false. Capture is not alerting — a failure tonight
   reaches no human automatically. See matrix C21 / FM-02.
4. **Accent and register range.** One voice, one night. C3's "calf" → "car" was
   one voice on one day.
5. **A green sweep is not sign-off.** It is evidence. The sign-off gate is
   `PRODUCTION_SIGNOFF_SCRIPT.md` §7, which requires three consecutive clean
   full runs at demo time-of-day.
