# Screening replay — before-table (2026-08-21)

The Phase 1 gate for the screening workstream. Phase 3 (trigger specificity)
does not start without this, and the after-table must be produced from the same
command against the same corpus.

Produced by:

```bash
python scripts/replay_screening.py > screening_before.txt
```

Pure functions only — `match_screen_trigger`, `classify_screen_answer`,
`_red_flag_hits`. No engine, no network, no LLM.

## Corpus

199 calls replayed, 19 (10%) touch a screen. Default floor is 2026-07-26, when
screening capture landed — earlier columns are blank by instrumentation, not by
absence, so replaying further back measures nothing.

Not yet split by `build_sha`. The two escalating calls below are the 2026-08-21
test calls, so the genuine-arm count for **real traffic** is currently zero and
every trigger-path arm in this table is spurious. Worth re-running with
`--build-sha` before the after-table so the two populations are not compared
against each other.

## Counts

| screen | arm | esc | esc/noask | clear | unclear | stranded |
|---|---|---|---|---|---|---|
| cauda_equina | 5 | 2 | 2 | 4 | 2 | 15 |
| dvt | 0 | 0 | 0 | 4 | 0 | 0 |
| inflammatory | 1 | 0 | 0 | 2 | 1 | 6 |
| vbi_neck | 0 | 0 | 0 | 3 | 0 | 0 |

`stranded` = pending but not gradable; Susie has since said something else, so
the answer window is shut and the screen never resolves. 21 of them. That is
what `e595df5` (bounded deterministic re-ask) addresses; this table predates
measuring its effect.

## Arm paths

| screen | paths |
|---|---|
| cauda_equina | orphan 13, trigger 15 |
| dvt | model_asked 1, orphan 3 |
| inflammatory | model_asked 1, orphan 9 |
| vbi_neck | orphan 3 |

`orphan` dominates everywhere except cauda_equina. Layer 2 is asking screens
Layer 1 never armed — which is the backstop working, but it means the trigger
lists are carrying less of the load than their size suggests.

## Arming utterances — marked

**6 of 8 are spurious.** This is S-3, evidenced rather than argued.

| call | screen | utterance | verdict |
|---|---|---|---|
| CA76bc921fe6 | cauda_equina | "for my back" | **spurious** — a body part, no symptom |
| CA76bc921fe6 | cauda_equina | "please book that in" | **spurious** — no clinical content at all |
| CA85b1f4cc63 | cauda_equina | "no not really it's my back" | **spurious** — arms on a *denial* |
| CA85b1f4cc63 | cauda_equina | "um yeah it's been going on for a few weeks" | **spurious** — chronicity, no red flag |
| CA2ffb1cd0d1 | inflammatory | "um thursday please" | **spurious** — a day of the week |
| CAb0ff51e012 | cauda_equina | "no sorry it's been up for weeks so" | **spurious** — arms on a denial |
| CA6246ecb88d | cauda_equina | "yeah i do" → escalate [no-ask] | **genuine** — B-74 affirmative fix |
| CA4feeeec6f9 | cauda_equina | "er yeah i do" → escalate [no-ask] | **genuine** — 2a disfluency fix |

"please book that in" and "um thursday please" are the two that matter most:
neither contains a symptom, a body part or a mechanism. They arm because the
trigger lists are bare keyword mentions with no severity or neuro gating, which
is exactly the two-signal problem Phase 3 exists to fix.

Both genuine arms are the *answer* path (`arming_utterance` → escalate without
asking back), not the trigger path. The deterministic escalation now fires on
the logged call — that half works.

## Success criteria for the after-table

1. The six spurious arms above no longer arm.
2. Both genuine arms still escalate, still without asking back.
3. No utterance that armed before fails to arm unless it is on the spurious
   list.
4. `stranded` materially down (that is `e595df5`, not Phase 3 — measure it
   separately so the two are not credited to each other).
