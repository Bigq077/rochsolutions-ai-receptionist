# Defect audit — 7 September 2026

Every open row across the defect documents, checked against the tree rather than
read. `production` = `4286bb81`, `latency-eval` = `eb3fed2d`.

**Headline: almost nothing on the open list is open.** Of the rows carried into
today, one is genuinely open and undiagnosed, one is a decision, and the rest are
already fixed and shipped. One new defect was found by the audit itself.

The lists were costing more than they were worth. This replaces them.

---

## 1. GENUINELY OPEN

### 1.1 A second callback lead in one call is silently dropped — **NEW, found by this audit, now FIXED**

`receptionist_tools.py:9665`. The owner-SMS dedup is keyed **per CALL**, not per
LEAD:

```python
if session.get("_waitlist_pinged"):
    return True          # <- "already queued", and nothing is sent
```

A caller who asks for a callback and then asks for a **second, different person**
to be rung back has that lead dropped. `return True` means the tool reports
success, so the model says *"Clinic notified"* and the call ends normally.

Flagged as an adjacent defect inside the P4 write-up on 1 Sep — *"Pre-existing
and untouched by this fix"* — and never given a row of its own, so it was never
carried forward. **Still live, on patient lines**, verified by reading the code
today.

Same family as the wrong-surname defects: nothing sounds wrong on the call, and
it surfaces only when somebody is not rung back.

**Severity: MEDIUM–HIGH.** Rare shape, silent, and a missed patient.

> **FIXED 7 Sep, `9461388c`**, on `latency-eval` and awaiting a call. The dedup
> is now per LEAD and reads a list, sharing `callback_lead_matches` with
> `_same_callback_lead` so the two halves of the rule cannot disagree again.
> Bounded at `_MAX_CALLBACK_LEADS = 3`, because the latch that was removed was
> also the only thing bounding an owner-billed SMS on a path a model can
> re-enter.

### 1.2 §2.5 — the surname has no code gate

The phone has one (`receptionist_tools.py:7565`, `phone_confirmed is not True`
→ `[book] BLOCKED`). The surname has only a prompt instruction, whose own text
records that *"speech-to-text has written the wrong surname to a real calendar
twice"*.

**Today's read-back steer narrows this** — the stored surname is now spoken back,
so a wrong one is correctable — but a caller who does not correct it still gets
it written. The open question is unchanged and is yours: **warn-only, or block
the write.**

### 1.3 Theorem's Acuity booking path — verification, not code

Five days unexercised, with this morning's P8 fix live on it. Process, not a
defect. Top of the list until a call clears it.

---

## 2. CLOSED — carried as open, actually shipped

Each verified as an ancestor of `production` today.

| row | claimed | actually |
|---|---|---|
| **B-127** | "substantial engine work" | shipped 3 Sep as **B-132**, `d7097886`. The number covers two defects; both fixed, both tested |
| **§2.2** false completeness (B-97 vs B-99) | "awaiting your decision" | **fixed 3 Sep**, `591133d9`, with `test_s22_…`. The doc that raised it says the conflict "dissolves" |
| **`fix/asap-sparse-rota`** | "needs triage" | superseded; all three parts plus its test are on production |
| **P1–P7, P9–P11** | mixed | all **FIXED** per the 09-01 doc's own status table, and all nine commits confirmed on production |
| **P8** | open, Theorem only | **fixed today**, `3de15b2c` |
| **P12** | live P1 on three lines | **fixed today**, `1067602a` |
| **§2.8 / LAT-1** | 17–19 s readouts | **largely fixed today**: 12.7 → 10.2 s everywhere, 12.7 → 7.9 s on the demo line |
| **§2.7 / B-31** | 200-char cap fires 3–4×/call | the **read-out shape** is fixed (197 chars); the cap itself still exists for other turns |
| **`_slot_presentation_mode`** | "no producer writes it" | **wrong** — written at `llm_stream.py:6842`. The staleness claim needs re-deriving before it can be a row |

---

## 3. NOT BLOCKED — the blocker was imaginary

**§2.6, STT numerals** (*"the 30-minute"* heard as *"the 5-minute"*). Carried as
*"blocked on pulling a wav off Render — every replay harness works on
transcripts, not audio."*

Every call already writes one:

```
[audio_capture] CA9cc6934b…: wrote 84.7s of inbound audio to logs/audio/CA9cc6934b….wav
```

It needs somebody to fetch a file off the disk, not a new capability. It has sat
behind a false blocker since at least 3 Sep.

**CORRECTED, same day — the wav was not the only constraint.** The keyterm list
has a hard API cap and northgate is already at it:

```python
_KEYTERMS_MAX = 100          # "jv_v1: 100 terms, at the cap"
[ms_stt] keyterms_prompt: 100 terms for clinic='northgate'
```

So adding numerals EVICTS existing terms, and the incumbents include `cancer`,
`bladder`, `bowel`, `numb`, `saddle` — red-flag screening vocabulary. This is a
zero-sum allocation with a safety dimension, not a missing file. Anyone who
"just adds numerals" will silently weaken the screens.

---

## 4. Open, small, unglamorous

* **multi-day lead-in** — ~1.8 s, and the last untouched piece of LAT-1.
* **`UNKNOWN_SLOW` apologises on a slow turn that ANSWERED** the question. A
  change to the fallback itself, not a matcher.
* ~~**B-31's cap**~~ — **not a defect, corrected 7 Sep.** The warning is the
  FALLBACK SUCCEEDING: `match_asked_screen` recovers from `last_question`
  (`raw = _q`) and logs loudly on purpose. The cap was deliberately not raised —
  "every known reader now has a fallback, and widening what the write gates can
  see is a change that opens a booking gate, which needs a live call rather than
  a green suite." Measured there over 400 calls: 523 turns run past the cap and
  160 lose the '?', so the fallback is load-bearing and working.

---

## 5. Not defects, but they decide the webinar

* **Hold speech** is live on all four lines and **no practitioner has heard it**.
  Longest-standing un-reviewed audible change in the product.
* **The obs digest worker is off** — `OBS_DIGEST_ENABLED not set`. Capture and
  judge run, so the data exists, but nothing *tells* anyone. That is the
  visibility leg of the production bar, and it is pull-only.
* **Multi-tenancy** — tenant identity is per-SERVICE (env vars, plus clinic names
  hardcoded in `fast_path.py`, `brain.py`, `booking/utils.py`,
  `providers/acuity.py`). Fine for three; it does not survive 230–250. Not a
  pre-webinar item, and the only thing that matters if the webinar goes well.

---

## 6. Why the lists went wrong, so the next one does not

**Eight open-defect documents**, none superseding the others by name, and
`ls -t` does not order them — a cherry-pick touched the 1 Sep file today and put
it first.

**The defect NUMBERS collide across documents.** The code's `(P4)` at
`turn_handler.py:2577` is a repeated-FAQ-CTA strip; the 1 Sep document's P4 is a
Vital Edge hold phrase after the sign-off. Different defects, same label, and a
grep for `P4` returns both. `B-127` has the same problem and the code says so
outright — *"that number was already spent … numbered B-132 here so a grep for
either returns one defect."*

**Rows were carried forward without re-checking.** Four of today's corrections
were fixed days before they were last written down as open.

**Residuals were buried inside closed rows.** §1.1 above is a live patient-line
defect that has been sitting in a block quote under a FIXED heading since 1 Sep.

### Three rules that would have caught all of it

1. **A row is a claim about the tree.** Before carrying it, grep for the fix.
   `git log` and the plan both lie here; the code does not.
2. **One number, one defect, forever.** If a number is spent, take the next one
   and say so in the commit — as B-132 did.
3. **A residual gets its own row**, or it is not tracked. "Adjacent defect, NOT
   fixed" inside a FIXED write-up is invisible within a week.

---

## 7. What I would do with this

1. **Fix §1.1.** It is small, it is silent, and it is on patient lines. Key the
   dedup on the lead (name + number), not the call.
2. **Decide §2.5.**
3. **Delete or archive the eight defect documents** and let this be the list.
   They are now actively misleading — that is not a tidiness point, it is why
   four rows were worked twice.
