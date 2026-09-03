# Scope — 62% of stored transcripts are missing caller turns · 2026-08-24

**Status:** scope only. Nothing changed.
**Severity:** observability, **not caller-facing**. No live call behaves differently.
It corrupts measurement, judging, and every evidence-based decision taken from
the corpus — which this week included a clinical-screening decision.

---

## 1. Measured

543 jv_v1 transcripts in the obs `calls` table:

| | count | share |
|---|---|---|
| missing **at least one** caller turn | 336 | **62%** |
| missing the caller's **opening** turn | 71 | **13%** |

Detection is trivial and exact: **two consecutive `assistant` turns** means a
caller turn between them was never recorded.

---

## 2. Root cause — one line, and it is structural

The two halves of the transcript are recorded **at different layers, with
different coverage**:

| | recorder | call sites | coverage |
|---|---|---|---|
| assistant | `record_assistant` | `connection.py:14271` — **the TTS loop** | everything spoken |
| caller | `record_user` | `llm_stream.py:5601` — **inside `_append_history`** | only turns that reach `run_turn` |

`_append_history` has exactly **three** call sites, all inside `run_turn`:
fast-path, unspoken-slot follow-up, and the main LLM turn.

**So a caller utterance is stored only if it was dispatched to a turn.** Anything
`connection.py` handles and returns on is invisible — while Susie's reply to it
is recorded in full, because the TTS loop catches everything.

That asymmetry produces the exact signature observed:

```
susie : Hi there, I'm Susie ... how can I help you today?
susie : I'm sorry to hear that. There's one routine question I ask everyone
        before booking BACK PAIN — do you have any numbness around the saddle...
CALLER: er no not really
```

### Confirmed bypass paths

- **Location answer intercepted** — logs `"ack-only, no run_turn"` verbatim
  (`connection.py:10914`). The caller says "alcester", Susie says "Awlstuh.",
  only Susie's half is stored.
- **Suppression filters** — noise fragments, short fragments,
  `open_availability_suppressed`; these `continue` without dispatching.
- **Discarded DTMF** — `dtmf_digit_discarded`.
- **Barge-in ack+drop** — the "processing directly instead of ack+drop" log
  implies the drop branch exists and consumes the utterance.

Note `_note_utterance_lost` already exists to count exactly this class of event,
but **only one reason code is wired** (`dtmf_digit_discarded`). The instrumentation
was built and then not extended.

---

## 3. What this affects — and what it does not

**Affected (all read `call.transcript`):**
- `app/obs/judge.py` — scores calls from the transcript, and
  `_last(transcript, "user")` reads "what the caller last said". **The judge has
  been grading half-recorded conversations**, which puts every `quality_score` in
  the table in question.
- `app/obs/digest.py` — the daily digest.
- `app/obs/regress.py` — regression scenarios built from stored transcripts.
- Any replay or analysis — including three separate measurements I ran this week.

**NOT affected — deliberately:**
`session["turns"]` is a **separate key** from `obs_turns`, kept assistant-only on
purpose. The comment at `llm_stream.py:5590` is explicit: the owner-facing
actionable summary (`_format_turns`, max 10) and the SMS router (last-8 window)
are tuned to that shape, and adding caller turns there would halve their real
coverage and change a live clinic's summaries as a side effect. **Do not "unify"
these two lists.** That is the trap this scope exists to prevent.

---

## 4. Options

| Option | Assessment |
|---|---|
| **A. Record at the STT seam** — mirror what was done for assistant turns at the TTS loop | The symmetric fix, and it follows the existing precedent. Every final transcript is recorded where it is produced, before any routing decision. |
| **B. Add `record_user` to each bypass path** | Whack-a-mole across ~8 sites in an 16k-line file. This codebase has repeatedly punished the N-copies pattern (location intercept ×4, two SMS template modules, two `notify_owner`). **Reject.** |
| **C. A, plus a disposition tag per turn** (`dispatched` / `suppressed` / `dropped`) | Preserves the distinction between "the conversation" and "everything heard", and makes drops visible to the judge rather than invisible. Strictly better than A if the consumers can be taught to filter. |

**Recommendation: C**, falling back to A if tagging turns out to ripple into the
judge prompt.

### The design question C forces

Recording at the STT seam captures utterances the system deliberately **dropped**
as noise. Is a transcript "the conversation" or "everything heard"? For
observability the second is more useful — you cannot debug a dropped answer you
cannot see — but it changes what the judge is reading. The tag is what lets both
consumers get what they need from one list.

---

## 5. Measurements needed before implementing

1. **Which bypass path dominates the 336?** Classify each gap by the assistant
   turn that follows it (a location ack looks quite different from a suppressed
   fragment). Decides whether one path accounts for most of it.
2. **`turn_count` vs `len(transcript)`** — `turn_count` is recorded independently
   on the same row. The difference is a free, independent estimate of how many
   turns are missing per call, and a cross-check on the 62%.
3. **Judge impact** — do `quality_score`s differ systematically between gapped
   and complete transcripts? This is what converts "the data is incomplete" into
   "the scores are wrong by X", and decides the priority.
4. **Other clinics** — theorem_v3 (50 calls) and vital_edge (39) use the same
   engine and should show the same rate. Confirm rather than assume.

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| **Historical comparability breaks.** Transcripts before and after the fix have different shapes, so `quality_score` trends cross a discontinuity | record the fix's build_sha; treat it as an epoch boundary in any trend |
| **Double-recording.** `build_record` runs **3× per teardown** (see the latency-persistence work) — a recorder that appends on each pass triples the transcript | `record_user` already inserts at a mark rather than appending; keep that property and test it |
| **Ordering.** The current mark-based insert exists because `_append_history` runs at TURN END, after the replies are already in the list. Recording at the STT seam is naturally in order | the mark becomes unnecessary, but removing it is a change in its own right — verify ordering on a real call, not just a unit test |
| **Unifying the two turn lists** | explicitly out of scope — see §3 |

---

## 7. Why this is worth doing

It changes no caller's experience, so it will always lose a priority argument to
a caller-facing bug. But this week it nearly produced a wrong clinical decision:
11 of 12 cauda-equina screens looked like the model interrogating callers who had
described nothing, and the correct reading was that the complaint was real and
the record incomplete. **The next person to run that analysis will not
necessarily catch it.**

Every future decision made from this corpus inherits the defect until it is fixed.
