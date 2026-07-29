# Defect register — evidence-attributed, 95 calls × 68 commits

**Method.** Every call in obs (25–29 Jul) run through a per-defect detector, then
bucketed against **commit times normalised to UTC**. A defect is "fixed" only if
a commit plausibly targets it **and** it stops appearing with adequate sample.
Otherwise it is "not observed" — which is not the same thing.

**Deploy lag caveat.** Commit time ≠ deploy time. Render takes ~3–6 min to build
and deploy. Any call within ~6 minutes of a boundary is ambiguous and is flagged.

**Confound: the test calls created 27 real appointments**, consuming the very
slots later test calls were offered. Any availability analysis must be checked
against that ledger before being called a defect.

---

## Corrections to the 29 Jul audit

**C2 — I had it backwards.** The audit said the B1 cap *caused* the false
"that's everything available" claim. It did not. Both instances are on
**`2d553b6`**, *before* `b405017` (28 Jul 02:09Z). `368b4e0` — *"offer unspoken
times from session"* — **fixed** the "anything later" path. Proof: `CA41ac1e38`
(28 Jul 02:32Z, live build) — caller asks *"anything later than that"* and gets
*"Yes, I do — we've also got quarter to seven and half past seven."*

**B3 — probably my detector, not a defect.** The three "escalation on a
greeting" calls all have `[NO TRANSCRIPT — scripted path]`. Safety escalations
are a scripted path that writes no transcript, so the clinical content is simply
absent from the record. **Withdrawn pending audio.**

---

## Status table

| ID | Defect | Last seen | Build | On live? | Confidence |
|---|---|---|---|---|---|
| **A1** | Model reasoning spoken aloud | 28 Jul 02:42Z | `b405017` | **OPEN** | High — 5 calls |
| **A2** | Day-name ≠ date | 27 Jul 23:53Z | `2d553b6` | **not observed** | **Low — no commit targets it** |
| **A3** | Wrong identity written | 27 Jul 18:35Z | `2d553b6` | **not observed** | Low — no read-back exists |
| **A4** | Confirmation loop | 29 Jul 02:04Z | `b405017` | **OPEN** | High — 19 calls |
| **B1** | Wrong screen for complaint | 28 Jul 02:23Z | `b405017` | **OPEN** | High |
| **B2** | Screen after confirmation | 28 Jul 03:04Z | `b405017` | **OPEN** | Medium — 1 call |
| **B3** | Escalation on a greeting | — | — | **withdrawn** | Detector artifact |
| **C2a** | Denies a slot that exists ("later") | 28 Jul 01:04Z | `2d553b6` | **FIXED** | High — `368b4e0`, proven |
| **C2b** | Rejection ("none of those") bypasses cap | 28 Jul 02:23Z | `b405017` | **OPEN** | High — code-confirmed |
| **C5** | Chunk-join drops space | 29 Jul 02:04Z | `b405017` | present | **Transcript artifact — not confirmed audible** |

---

## Detail on the two that shrank

### A1 · reasoning leak — severity fell, defect did not

| When | Build | What was said |
|---|---|---|
| 27 Jul 02:28Z | `17d90e7` | ~200-word monologue, *"Looking at the call state…"* |
| 27 Jul 02:43Z | `17d90e7` | *"Wait, that's the wrong screen — that's for back pain."* |
| 27 Jul 02:48Z | `17d90e7` | Markdown read aloud: *"**Patient name:** Jewel…"* |
| 28 Jul 01:28Z | `2d553b6` | *"That's a soft affirmative to the booking offer — good."* |
| **28 Jul 02:42Z** | **`b405017`** | *"I need to book this in now — I have everything I need."* |

No commit targets this. The three worst instances cluster on `17d90e7` — a single
bad window, not a fix. **Still open, 1 in 21 on the live build.**

### A2 · day-name ≠ date — the dangerous "not observed"

Three occurrences, each on a different build, **each one booked or nearly booked
the wrong date**:

- `CAcd8b36e1` — *"Wednesday the 30th of July"* (a Thursday) → booked `30 Jul 17:30`
- `CAfe6a4162` — *"Friday the 1st of August"* (a Saturday) → booked `01 Aug 18:00`
- `CAaf76d3b0` — *"Saturday the 9th of August"* (a Sunday) → not booked

**Nothing in 68 commits targets this.** Absence from 21 live-build calls is
consistent with a ~4% rate and proves nothing. **Treat as open.**

---

## What the commits actually achieved

| Commit | Claim | Verdict from calls |
|---|---|---|
| `f302ddb` `28ff14b` `4c95c95` `de426a6` | phone/confirm gate | **Worked** — 2+ confirm asks 35% → 9.5% |
| `368b4e0` | unspoken follow-up | **Worked** — C2a fixed, proven by `CA41ac1e38` |
| `b405017` | cap spoken offer at two | **Worked** — 6 options → 2; but C2b path untouched |
| `91bb11b` | record service | **Worked** — `service == checked_service` 12/12 |
| `29e3f9b` `83699c3` `0fd1961` `188e478` | screening | **Partial** — B1 and B2 still reproduce |
| **`17d90e7` window** | F-023 phantom catch | ⚠️ **The three worst A1 leaks all occur on this build** |

### The revert window — 27 Jul 01:26:53Z → 01:49:58Z

Four fixes were reverted and restored 23 minutes later: `de426a6`, `f302ddb`,
`073e563`, `28ff14b`. Calls in that window ran without them. **No conclusion
should be drawn from calls between 01:26Z and 01:56Z on 27 July** (allowing
deploy lag).

---

## Regression protection — the non-negotiable part

The detectors used to build this table **are** the regression harness. Before any
fix ships this week:

1. Run the detector suite over all 95 historical calls → record the baseline
   counts in this file.
2. After the fix, re-run over new calls. **Any detector that fires on a build
   where it previously did not is a regression — revert, do not debug.**
3. Every fix additionally needs a `tests/regression/` test and a
   `tests/auto/` scenario.

Detector coverage today: A1, A2, A4, B1, B2, C2, C5. **Not yet covered: A3
(needs surname read-back before it is detectable at all).**

> A3 cannot be regression-tested until the read-back exists. That is the argument
> for shipping the read-back **first** — not because it fixes the defect, but
> because it makes the defect measurable.
