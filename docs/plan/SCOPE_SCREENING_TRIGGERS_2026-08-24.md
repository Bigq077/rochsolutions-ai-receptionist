# Scope — clinical screening triggers · 2026-08-24

**Status:** scope only. No code or config changed.
**Evidence:** the real `match_screen_trigger` replayed over **582 stored calls**
with transcripts from the obs corpus (`calls`, 665 rows, 582 with caller turns).
Numbers below are measured, not estimated.

---

## 1. What is actually wrong

**Layer 1 arms on 9 of 582 calls (1.5%).**

```
armed today: {'trauma_fracture': 2, 'dvt': 3, 'cauda_equina': 3, 'inflammatory': 1}
```

The gap is concentrated in **one screen**. Of calls where the caller described an
injury mechanism anywhere in the call: **13 shaped, 11 missed (85%)**.

### It is NOT the utterance boundary

My first hypothesis was that triggers are evaluated per-utterance
(`update_screening_state(text, …)` takes one utterance) so a body part in turn 1
and a mechanism in turn 3 never combine. **Measured: a rolling window of 3 adds
exactly ZERO arms.** That hypothesis is wrong on its own.

### It IS the trigger shape

`trauma_fracture` carries **35 contiguous literals** of the form *VERB my BODYPART*:

```
twisted my ankle · rolled my knee · done my wrist · turned my ankle · sprained …
```

Callers do not talk like that. From the corpus, verbatim:

| What the caller said | Why it missed |
|---|---|
| "just my left ankle nothing serious just **rolled it** yesterday" | pronoun — the bigram cannot form |
| "my left ankle **i twisted it** while I was going…" | pronoun, and split across clauses |
| "i've **hurt my ankle**" | "hurt" is not in the 35 literals at all |
| "my left **angles** in a lot of pain … i just twisted it" | STT rendered *ankle* as *angles* |

So there are two distinct defects: a **vocabulary** gap ("hurt my X" absent) and a
**structural** one (the verb and the body part must be adjacent, and callers
pronominalise).

---

## 2. The fix mechanism already exists

`_screen_triggered` supports two config forms, OR-ed:

- `trigger_keywords` — ANY keyword arms. *(what trauma uses)*
- `trigger_all_groups` — EVERY group must have a hit. *(what `vbi_neck` uses:
  neck AND a dizziness signal)*

**`trauma_fracture` should use the second form.** One group of mechanism verbs,
one group of body parts. That decomposes the bigram and the pronoun problem
disappears, because "it" is a legitimate member of the body-part group once a
part has been named.

This is a **config** change, not a code change. `vbi_neck` proves the schema works.

---

## 3. Measured proposal

Two groups + a 3-utterance rolling window, with **acute-injury verbs only**:

```
mechanism: twisted rolled "went over" "gone over" turned sprained fell fallen
           tripped slipped landed banged knocked "did my" "done my" "hurt my"
           injured
bodypart : ankle foot knee shin calf leg wrist shoulder elbow hip thigh hand
           arm neck back it
keywords : (kept, decisive on their own) "heard a crack" "heard a snap"
           "can't put weight" "can't walk on it" "car accident" "collision" …
```

| Variant | trauma arms | Verdict |
|---|---|---|
| today | 2 | 85% miss rate |
| **acute verbs only** | **9** | **6 of 7 new arms clearly correct** |
| broad (adds bare "hurt", "hurting", "sore") | 15 | **rejected** |

The broad variant newly arms on *"my neck's been hurting a bit recently"*,
*"my left ankle's a little bit sore"*, *"my calf's been very sore lately"* —
gradual-onset complaints with no injury. That is precisely the over-screening
B-20 ruled out (screening is conditional, not a checklist).

**The distinction that matters: `"hurt my"` as a phrase is in; bare `"hurt"` /
`"hurting"` / `"sore"` are out.** "I hurt my ankle" is an event. "My neck's been
hurting" is not.

---

## 4. What I would NOT do

- **Do not add more literal phrases.** That is the trap this defect is made of —
  35 of them already exist and the corpus walks straight past them.
- **Do not widen DVT.** Its triggers are calf/leg only and omit ankle/foot/knee,
  which looks like a gap — but adding lower-limb parts scores **+0 on 582 calls**.
  No stored caller says "swollen ankle". Widening it would be speculation.
  *(Corollary: on CA164a737897b8, Layer 1 not arming DVT for "left ankle nothing
  serious" was arguably CORRECT — there was no swelling signal. The model asked
  a DVT screen off a bare ankle mention, which is the model over-screening.)*
- **Do not touch the grader.** It is single-polarity — "yes" must mean concerning —
  and every screen's question is already worded to that contract.
- **Do not gate triggers on the answer.** Requiring a red-flag signal to arm makes
  the screen a confirmation of what the caller already volunteered. That was
  tried, reversed a P1, and reddened 29 tests.

---

## 5. Risks

| Risk | Why it matters | Mitigation |
|---|---|---|
| **Over-screening** | A benign caller asked a fracture screen sounds alarming and is the B-20 failure | acute-only verb list; judge every newly-armed call in the replay before shipping |
| **Arming turns on enforcement** | An arm sets `pending_screen` → the SCREEN REQUIRED steer drives → `booking_blocked_reason()` becomes live. Today it is inert. **More arms = real booking blocks.** | verify the block-and-release path on a call before porting; this is the biggest behavioural change here |
| **Per-clinic config** | jv_v1, theorem_v3 and vital_edge each carry their own `screens` block | scope one clinic first; the three lists are not identical |
| **`"it"` in the body-part group** | Broadens matching considerably | it only ever fires alongside a mechanism verb in the same window; measured precision above is with `it` included |
| **Rolling window changes other screens** | window=3 applies to all six | measured: no change to dvt / cauda / inflammatory counts |

---

## 6. Work breakdown

1. **Config (jv_v1 only):** rewrite `trauma_fracture` triggers as two groups.
   Code-free. *Small.*
2. **Code:** evaluate triggers over a short rolling window of caller utterances.
   `update_screening_state` currently receives one utterance. *Small, but touches
   the entry point every screen uses.*
3. **Replay harness as a test:** pin the corpus result — trauma arms rise from 2
   to 9 and none of the six gradual-onset calls arm. *Medium.*
4. **Verify enforcement end-to-end:** one call that arms, answers "yes", and must
   be blocked from booking. This is the step that actually matters clinically and
   has never been exercised, because Layer 1 has been dormant.
5. Port to theorem_v3 / vital_edge only after 1–4 hold on jv_v1.

**Isolation caveat:** items 1 and 2 were measured *together* (two groups + window
of 3). Item 1 alone catches the single-utterance cases
("…just rolled it yesterday"); item 2 is needed for the split ones
("my left angles… || …i just twisted it"). Implement and measure them separately.

**One loose end:** one newly-armed call —
*"yeah i just went to book of cv offered acupuncture…"* — I could not explain
from the verb list. Establish why before shipping; it may indicate gap-tolerant
matching (`_TRIGGER_MAX_GAP = 3`) being looser than expected.

---

## 7. What this does not fix

Arming is detection. On CA164a737897b8 the screen was asked, graded and cleared
entirely by the **orphan** path — the safety net — while `pending_screen`,
the steer and the booking backstop were all inert. Item 4 above is the part that
converts screening from observability into enforcement, and it is the reason to
do this work at all.
