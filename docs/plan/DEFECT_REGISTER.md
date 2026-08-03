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
| **A4** | Confirmation loop | **31 Jul 16:17Z** | **`f0adf21`** | **FIXED 1 Aug** | **Gate 1 vocabulary — adjective slot + filler runs** |
| **B1** | Wrong screen for complaint | 28 Jul 02:23Z | `b405017` | **OPEN** | High |
| **B2** | Screen after confirmation | 28 Jul 03:04Z | `b405017` | **OPEN** | Medium — 1 call |
| **B3** | Escalation on a greeting | — | — | **withdrawn** | Detector artifact |
| **C2a** | Denies a slot that exists ("later") | 28 Jul 01:04Z | `2d553b6` | **FIXED** | High — `368b4e0`, proven |
| **C2b** | Rejection ("none of those") bypasses cap | 28 Jul 02:23Z | `b405017` | **OPEN** | High — code-confirmed |
| **C5** | Chunk-join drops space | 29 Jul 02:04Z | `b405017` | present | **Transcript artifact — not confirmed audible** |

---

## Cross-reference to `REGISTER_B_U.md` — added 3 Aug 2026

**Read the ID-collision warning at the top of `REGISTER_B_U.md` first.** `B1`/`B2`
here are **not** `B-01`/`B-02` there. Nothing below merges the two tables; this
records only where 3 Aug work on the `B-nn` side touches a row on this side.

| Row here | Touched by | Effect on this row |
|---|---|---|
| **A1** — reasoning spoken aloud | `B-41` (`6901c27`), `B-43` (`91524c4`) | **Narrowed, not closed.** `B-41` closed third-person narration (*"Their choice is to cancel."*); `B-43` closed the first-person write-path family across all nine phrasings, on cancel and reschedule as well as booking. A1 as scoped here is broader than both — the 200-word monologue and the markdown-read-aloud instances are untouched. **Leave OPEN** |
| **A2** — day-name ≠ date | `B-09` (`00ae6df`) | **One source removed.** The Sunday off-by-one-week that fed the model a seven-day-late anchor is fixed. A2's own mechanism is unchanged and still open: `v3_confirmed_slot_phrase` is scraped from the model's spoken text and Gate 5 forces later readbacks to **agree** with it, so a wrong date is made *consistent* rather than corrected — which makes it **less** likely a caller notices. **Leave OPEN** |
| **A3** — wrong identity written | `B-33` (`c5210a2`), `B-42` (`0dc510d`) | **Partly addressed from an unexpected direction.** `B-42` added a name read-back before destructive writes — which is precisely the read-back this file's closing note says A3 needs *before it can be regression-tested at all*. That read-back now exists on the cancel/reschedule path, though not on booking. `B-33` removed three ways a name could be invented outright. **Leave OPEN, but the detector blocker is half-lifted** |
| **B1**, **B2**, **C2b**, **C5** | — | **Untouched.** No 3 Aug work went near them |

> **Standing caveat on the four untouched rows.** `B1`, `B2`, `C2b`, `C5` are
> one-line rows with a symptom and a call ID, and **none has been re-anchored to
> a file and line since it was written on 29 Jul.** Every `B-nn` row investigated
> on 2–3 Aug turned out to be materially different from its one-line description
> — `B-09` twice, `B-33` in every detail, `B-38` from "lead" to reproduced,
> `B-24`/`B-29` withdrawn as my own errors. Treat these four as **leads with a
> symptom, not findings with a mechanism**, until someone reads the code.
> `C2b` says "code-confirmed" and is the one most likely to survive that read.

---

## A4 · confirmation loop — root cause, 31 Jul 2026

Nineteen calls of evidence, and the register carried only the symptom. This is
the mechanism. It is **not** a new defect and **not** related to the U3.5 lever —
the matcher below is model-agnostic and the same phrase fails it on either STT.

### Evidence — `CA587b103bd4a116b0a34b590f1893dbc4`

31 Jul 16:17Z · build `f0adf21` · clinic `jv_v1` · 176 s · **`success=True`,
`booking_confirmed=True`**. The call *succeeded*. That is the point: A4 is
invisible to every outcome metric we have.

```
17 Susie   Thanks Quentin — and I've got you on 07502 211 207
           — is that the best number for the booking?
18 caller  um yes that's a good number
19 Susie   So that's Quentin, Tuesday the 4th of August at half past six
           — shall I go ahead and book that in?
20 caller  yeah go ahead
21 Susie   I've got you on 07502 211 207 — is that the best number...   ← verbatim 17
22 caller  yes that's the best number
23 Susie   So that's Quentin, Tuesday the 4th of August...              ← verbatim 19
24 caller  yes book it please
25 Susie   All booked
```

Four wasted turns. The caller answered the same two questions twice.

### Mechanism — one predicate, empirically confirmed

`connection.py:1089` `_is_use_this_number()` gates whether `phone_confirmed` is
set (`connection.py:6202-6248`). Without it, `book_appointment`'s A1 gate refuses
the write and the model re-asks. Run against the actual turns:

```python
_is_use_this_number("um yes that's a good number")   # turn 18 -> False
_is_use_this_number("yes that's the best number")    # turn 22 -> True
```

**Turn 18 fails, turn 22 passes, and the loop is exactly the turns between
them.** The caller escaped only by accidentally saying the magic words.

Why turn 18 fails, both branches:

1. `_USE_THIS_NUMBER_SIGNALS` has `"best number"`, `"correct number"`,
   `"right number"` — but not `"good number"`.
2. The bare-affirmative fallback is capped at **≤3 words**. Turn 18 is six
   (`um / yes / that's / a / good / number`), so a plain "yes" wrapped in one
   filler and one adjective is out of reach. Note `"um"` alone costs a word;
   no filler-stripping is applied here, unlike `_resolve_barge_in`.

~~The parallel gate `flow._HG_YES` (flow.py:10614) **also** misses this phrase, so
this is not only the known list-divergence — it is a genuine vocabulary gap in
every copy.~~

**CORRECTED 1 Aug — this was wrong.** `_HG_YES` is one of *three* accept routes
at that gate. `_hg_bare_yes` is a word-bounded regex for `yes|yeah|yep|yup`
anywhere in the turn, so it matches the "yes" in turn 18 and the deterministic
gate **accepts**. Verified by running both predicates against the literal
transcript. The defect was confined to the LLM path — and the implication is the
reverse of the one drawn above: the deterministic gate already had the right
shape, and `connection.py` was the copy that had drifted. What flow genuinely did
miss is the same phrase with no affirmative word at all ("that's a good number").
See `docs/plan/README.md` correction 14.

### Why it keeps coming back

Fourth patch to the same hand-maintained phrase list:

| Date | Phrase added | Triggering call |
|---|---|---|
| 07 Jul | `best one` | *"yeah that's the best one"* fell through |
| 26 Jul | (Step 8 reworded; `connection.py` copy missed) | phone step stopped matching entirely |
| 27 Jul | `it is` | live verify call |
| 30 Jul | `correct number`, `right number` | `CA3145c15f` — looped until the caller **hung up** |
| **31 Jul** | **`good number`** ← this entry | `CA587b103b` |

An open-ended set of English affirmatives is being recognised by substring-matching
a literal list maintained in **three** places (`connection.py`,
`llm_stream.py`, `clinic_template_prompt.py`) plus a fourth local tuple in
`flow.py`. Each fix adds the one phrase that call happened to use. The next
caller says *"yeah that's fine for me"* and we are back here.

### Fix — SHIPPED 1 Aug

**What landed** — one step beyond "minimal", deliberately. Adding `"good number"`
would have been patch five of five, and the table above is the argument against
doing that again. Instead `connection._POSITIVE_NUMBER_RE` covers the **adjective
slot** — `(good|best|right|correct|fine|great|perfect|ideal|only|usual|main|
current)\s+(number|one)` — because step 8 asks *"is that the best number?"* and
callers answer by echoing that noun phrase with whatever adjective comes to mind.
Leading filler runs (`um`, `uh`, `erm`, …) are stripped before the ≤3-word count,
per the plan below. `flow._SEMANTIC_YES_PHRASES` gains the two members that reach
the deterministic gate.

**What deliberately did NOT change: the ≤3-word cap.** The structural plan below
proposes replacing it with "contains an affirmative token and no negative token",
and that would be a regression here. The cap is what rejects *"yes, but call me on
my work phone instead"* — a turn containing a clean affirmative whose intent is
the opposite. A miss costs a re-ask; a false accept books an unreachable patient,
which is a §6.1 correctness failure. The cap stays until the structural fix has a
negative-intent model better than a substring list. Asserted in
`test_the_word_cap_still_blocks_a_long_turn_containing_yes`.

**Structural, post-demo:** one shared, tested affirmative vocabulary consumed by
all four sites, with the ≤3-word cap replaced by *"contains an affirmative token
and no negative token."* The negative guard already works this way
(`connection.py:1103`) — it is only the positive side that is length-capped.

**Regression test** — `tests/regression/test_phone_confirm_adjective_slot.py`,
41 cases. All five historical phrases are parametrised as specified, plus the
false-negative half that matters more (ten negative-intent phrases, the word cap,
filler-stripping unable to hide a negative, and dictated digits not reading as a
confirmation). It also asserts the two gates **agree**, since their divergence is
the standing hazard rather than either one's vocabulary.

Full-suite failing set unchanged: 95 before, 95 after, identical set.

### Resolved from the Render log — it is TWO gates, not one

The open question above ("turn 20 passes the predicate, so why did it still
loop?") is answered. The log shows the loop is two *independent* write-gates
firing in sequence, each demanding a question the caller had already answered:

```
16:19:04  FINAL "um yes that's a good number"
          → no `booking verbal phone confirm` line: _is_use_this_number = False
16:19:18  [book] BLOCKED — phone not confirmed (A1) phone_confirmed=None
          → tool result instructs the model to re-ask ⇒ turn 21
16:19:30  FINAL "yes that's the best number"
          → `booking verbal phone confirm — phone_confirmed=True`
16:19:33  book_appointment BLOCKED — booking confirmation question not yet asked
          (last_bot_prompt="…is that the best number for the booking?")
          → model must ask "shall I go ahead…" ⇒ turn 23
16:19:50  book_appointment → success, event semqra91h0son…
```

**Gate 1** is `_is_use_this_number` as analysed above. **Gate 2** is the
booking-confirmation check in `llm_stream.py`: it requires *"shall I go ahead and
book that in?"* to have been asked **in the current turn**. Because gate 1 forced
the previous turn to be the phone question, gate 2 could not be satisfied either —
so a single missing phrase in gate 1 costs **four** turns, not two.

Turn 20 (`"yeah go ahead"`) did *not* wrongly fail: `_bk_phone_step` correctly
declined to treat a booking-confirm answer as a phone confirmation. That guard is
working as designed. **Gate 1's vocabulary is the whole defect.**

Both gates are correct in isolation — they exist to stop silent mis-bookings, and
they did their job. The defect is that gate 1's *recogniser* is too narrow, and
the cost of a miss is multiplied by gate 2.

### Not related to the U3.5 lever — confirmed, not assumed

Every `[LAT]` line on this call reads `stt_model=universal-streaming-english`.
The call ran on the **old** model. A4 is independent of the STT work.

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
