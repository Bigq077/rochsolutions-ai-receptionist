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

## Corpus — and why the split the plan specified does not work

199 calls replayed, 19 (10%) touch a screen. Default floor is 2026-07-26, when
screening capture landed; earlier columns are blank by instrumentation, not by
absence, so replaying further back measures nothing.

The plan called for splitting by `build_sha` branch membership. **That axis
cannot answer the question.** Measured across the 214 stored jv_v1 calls:

- 77 (36%) carry no `build_sha` at all, so they cannot be placed on any branch;
- 50 of the 58 shas present *are* on a JV live branch — because the demo line
  runs the same builds. "This sha is on `jv_v2`" says nothing about who rang.

The number dialled fails too: both Susie lines are rung by the same handsets,
including one calling at 03:00 UK time.

**The caller is the discriminator.** Two dev handsets (`+33617769867`,
`+447502211207`) account for 204 of 214 calls.

| split | all calls | of which armed |
|---|---|---|
| test (dev handsets) | 189 | 18 |
| real (everything else) | 10 | 1 |

Run it yourself:

```bash
python scripts/replay_screening.py --audience real
```

### The finding that matters for Phase 3

**There is no real-traffic screening corpus.** Of 38 calls touching a screen,
37 are from a dev handset. `--audience real` leaves 10 calls, 1 armed screen —
an `inflammatory` that cleared. Zero cauda equina, zero DVT, zero trauma.

So the six spurious arms below are all from our own test calls, and the
after-table cannot demonstrate a real-traffic improvement, because there is no
real-traffic baseline to improve on. That does not invalidate S-3 — `"please
book that in"` arming cauda equina is a defect whoever said it — but it does
mean **Phase 3 cannot be validated by replay alone.** The harness now prints
the split on every run and warns when every armed call is a test call, so this
cannot be misread as measured evidence later.

`--sha-on-branch <ref>` is kept for the occasions branch membership is the real
question; it reports the calls it drops for having no sha rather than silently
shrinking the corpus by a third.

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
   separately so the two are not credited to each other). **Caveat:** replay
   cannot actually show this. The stored transcripts predate the re-ask, so
   replaying them can show how many screens would now *get* a re-ask, never
   whether the caller then answered it. That needs a live call.

All four are measured on the TEST corpus, because that is the only one with
armed screens. State that plainly in the after-table rather than quoting the
numbers bare.

## What would make this a real gate

The harness is sound; the corpus is not. To put Phase 3 on real evidence:

- Get real calls through the live line. Ten real calls, one armed screen, is
  not a base to narrow triggers against — and a narrowed trigger fails
  *silently*, in the `clear`-when-it-should-flag direction.
- Until then, treat Phase 3's after-table as a **change-detector**, not a
  validation: it proves the six known-spurious utterances stopped arming and
  the two genuine ones did not, which is worth having and is not the same as
  proving the new triggers are right.
- Layer 2 remains the backstop, and the arm-path data supports leaning on it:
  `orphan` (model-asked, Layer 1 never armed) already outnumbers `trigger` on
  every screen except cauda_equina. Narrowing Layer 1 removes less coverage
  than the trigger-list sizes suggest.
