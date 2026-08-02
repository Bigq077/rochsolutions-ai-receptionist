# Plan — deterministic reason capture (Problem 1 of the screening cluster)

**Written 2026-08-02.** Branch `latency-eval` @ `1ac5bdd`. No code changed yet.

**The symptom.** The caller opens with *"I'd like to book an appointment for my
knee"* and Susie asks *"What's the appointment for?"* — sometimes after naming the
complaint herself in the same sentence.

Both 2 Aug knee calls in obs, verbatim:

```
CALLER: um i'd like to book an appointment for my knee
susie : I'm sorry to hear that — knee pain can really get in the way of
        things. What's the appointment for, exactly...
CALLER: oh i just said my knee's just um it's been aching for a couple weeks
susie : Right — and before we look at times, can I ask: is the stiffness at
        its worst first thing in the morning and lasting more than half an
        hour, and is it in several joints...
```

Two defects in four turns. This plan covers the first one only. The unwarranted
screen is `B-20`, tracked separately — but §7 explains why they must be sequenced
together.

---

## 1 · The finding that changes what this plan is

**Deterministic reason capture already exists.** `app/media_streams/first_turn_extractor.py`
has `_BODY_PARTS`, `_INJURY_VERBS`, `_extract_reason()`, and
`apply_first_turn_signals()` which writes `session["reason"]` and mirrors it into
`collected["reason"]`. It has its own test file, `tests/test_first_turn_extractor.py`.

Run against the real openings, it works:

| Opening utterance | captured | reason |
|---|---|---|
| *"um yeah hi i'd like to book an appointment for my knee please"* | ✅ | `'appointment for my knee please'` |
| *"um i'd like to book an appointment for my knee"* | ✅ | `'appointment for my knee'` |
| *"hi i'd like to book for shoulder pain"* | ✅ | `'to book for shoulder pain'` |
| *"hi i'd like to book an appointment please"* | ❌ | `None` (correct — no complaint) |

**But it never runs on this path.** Its only caller is `flow.py:11822`, inside
`DETECT_INTENT` on the legacy FlowEngine path. `jv_v1` runs `prompt_engine=template_v1`,
which is the v3 path through `connection.py`.

Proof from the data rather than from tracing 12k lines: of **54 stored `reason`
values** across jv_v1 calls, every single one is clean semantic prose —
`'knee issue'`, `'ankle pain from running, GP recommended physiotherapy assessment'`,
`'MSK initial assessment'`. **Not one** has the shape `_extract_reason` produces,
which is a raw ±3-word window (`'appointment for my knee please'`). The reason is
always the model's, which is precisely why the model has to ask for it.

> **So this is not "build a deterministic capture."** It is "decide whether to
> wire in the one that already exists." Same shape as `B-14` (a pronunciation
> dictionary present, tested and never active) and the dormant Layer 1. Writing a
> second extractor would create two reason vocabularies free to disagree — the
> §A4 trap this codebase falls into repeatedly.

---

## 2 · The change

Call `extract_first_turn_signals` / `apply_first_turn_signals` on the v3 path's
first caller turn, and expose the result to the prompt as a CALL STATE fact —
*reason known, do not ask* — so rule 1b has something deterministic to obey
instead of a judgement to make.

Rule 1b already says the right thing
([clinic_template_prompt.py:1899](../../app/prompts/clinic_template_prompt.py)):

> *"If the caller has ALREADY said why they are calling — a body part, a symptom,
> an injury... that IS the reason: do NOT ask again."*

**The rule is correct and is not obeyed.** Rewriting it is the weak option; it has
already been written. The fix is to stop asking the model to decide.

---

## 3 · Precondition — the extractor is not safe to enable as-is

`_extract_reason` matches **bare body-part words**, not phrases. Against 967 real
caller turns:

```
shoulder 28 | ankle 22 | knee 13 | back 7 | elbow 5 | neck 2 | foot 1
```

One of the seven `back` occurrences is not a body part — *"hi can you call me
back later"* — and the extractor captures it:

```
"hi can you call me back later"  ->  captured=True  reason='you call me back later'
```

The clinical screening config solved this problem already and did not use bare
words: `cauda_equina` keys on `"my back"`, `"back pain"`, `"sore back"`,
`"bad back"` — always phrases. **The extractor must inherit that discipline
before it is wired to anything that matters.** That is the first commit, and it
is testable entirely offline against the stored turns.

---

## 4 · Regression families

Ordered by what they cost, not by likelihood.

### F1 · The word is not a body part
*"call me back later."* Quantified above: 1 in 967 turns, ~14% of `back`
mentions. **Mitigation:** phrase matching (§3). **Residual: low.**

### F2 · Real body part, but the intent is not a new booking
*"I need to move my knee appointment"*, *"do you treat knee problems?"*,
*"how much is a knee assessment?"*

This is the **"forced down a path that has nothing to do with it"** case. A
captured reason on a reschedule or FAQ turn asserts booking state that the caller
never asked for.

**Mitigation:** gate on `v3_caller_intent`, which already exists and already
distinguishes `booking` from `reschedule`/`cancel` (read at
[connection.py:5284](../../app/media_streams/connection.py)). Never run the
capture on the cancel/reschedule path. **Residual: low once gated — but the gate
is mandatory, not optional.**

### F3 · Real part, real booking, wrong attribution — **CORRECTED 2 Aug, this was wrong**

> The mitigation proposed below (fail open on a third-party complaint) was
> implemented and immediately broke two long-standing tests —
> `test_ankle_body_part` and `test_booking_plus_child` — both of which assert
> that *"my son hurt his ankle"* DOES capture a reason.
>
> They are right. `extract_first_turn_signals` already answers "whose complaint
> is this?" with a dedicated signal, `first_turn_patient_is_caller`, which reads
> `False` on exactly those utterances, and the child policy gate needs the reason
> for a paediatric booking. **Attribution is a consumer question, not an
> extraction one.** The guard was removed; whatever wires this into the v3 path
> must read `first_turn_patient_is_caller` alongside the reason.
>
> Historical mention, negation and multi-part (below) were correct and shipped.

### F3 (original text) · Real part, real booking, wrong attribution
*"book for my daughter's knee"* (third party), *"I did my knee last year but this
is my shoulder"* (historical), *"not my knee — my hip"* (correction), two parts in
one turn.

**Mitigation: fail open.** More than one body part, or a negation before one ⇒
capture nothing and let her ask. An extra question is cheap; a wrong reason is
not. **Residual: low, and the failure direction is the safe one.**

### F4 · The reason is captured but too coarse to choose the service
`jv_v1` has **ten** services at 30–60 minutes — `msk_initial_assessment` 40,
`neuro_assessment` 60, `sports_massage` separate. The prompt is explicit that
*"a 30-minute massage and a 60-minute assessment are not the same slot."*

Today's re-ask sometimes does real work: it turns *"my knee"* into something
mappable. Suppress it and that elaboration is lost.

Evidence, with its limits stated: in both knee calls the follow-up did **not**
change the service — both resolved to `msk_initial_assessment`, and for the MSK
family the service is driven by **new vs returning**, not by body part. That is
n=2 and should not be over-read.

> **This family decides the design.** The risk is not in capturing the reason, it
> is in **what the flag is permitted to suppress.** The flag must suppress only
> the redundant *"what's the appointment for?"* — never a follow-up genuinely
> needed to pick the service. Those are two different questions and today the
> prompt conflates them. **Residual: medium; mitigated by scoping the flag, not
> by improving the extractor.**

### F5 · It weakens a write guard — the one needing your decision
`book_appointment` **refuses without a reason on record**
([receptionist_tools.py:4701](../../app/tools/receptionist_tools.py) —
`[book] BLOCKED — no reason on record (A2)`), and `reason` is in the tool
schema's `required`.

That guard works today because a human had to say something. Auto-populating
`reason` from a stray word means **the guard can be satisfied by accident** — a
check that passes without the thing it was checking for. That is FM-01's shape.

Three options, and this is the only part of the plan with booking-correctness
exposure:

| | Approach | Consequence |
|---|---|---|
| **F5-a** | Extractor writes `reason` exactly as today | Simplest. A1 guard silently weakens; a false capture books with a junk reason |
| **F5-b** *(recommended)* | Extractor writes to a **separate** key (`reason_inferred`); `reason` stays model-set. CALL STATE reports the inferred value; the A2 guard keeps requiring the canonical one | Guard strength unchanged. Slightly more plumbing. The suppression still works, because CALL STATE is what stops the re-ask |
| **F5-c** | Guard learns to distinguish inferred from given | Most correct long-term, touches a write gate — **not three days before a demo** |

### F6 · The counter-intuitive one — this fix can make `B-20` worse
Layer 1 screening runs on the caller's **raw utterances**. When Susie re-asks and
the caller elaborates (*"it's been aching for a couple weeks"*), that is extra
text Layer 1 gets to inspect for triggers. Suppress the re-ask and the caller
says less, so **Layer 1 has fewer words to match against.**

Nothing was lost in these two calls — Layer 1 fired nothing either way. But
structurally the annoying question is also a trigger-harvesting opportunity, and
`vbi_neck` / `inflammatory` already have **zero** arms in 104 calls.

**This does not argue against the fix. It argues that Layer 1's coverage work is
a sibling of this change, not a follow-up to it.** See §7.

---

## 5 · Scope

**In:**
1. Phrase-based matching in `_extract_reason` (§3), with the stored-turn corpus as
   the false-positive test set.
2. Fail-open on multiple parts / negation (F3).
3. Wire the extractor into the v3 first caller turn, gated on `v3_caller_intent`
   (F2).
4. A CALL STATE line asserting the reason is known, so rule 1b becomes an
   observation rather than a judgement.
5. `reason_inferred` kept distinct from `reason` (F5-b).

**Out, deliberately:**
- Any change to `book_appointment`'s A2 guard (F5-c).
- Any change to `flow.py`'s legacy call site — it works there; leave it.
- A second extractor or a second body-part vocabulary, under any circumstances.
- The screening-authority decision (`B-20` A/B/C). Different defect, different
  change, must not ride along.

---

## 6 · Tests

- **Offline corpus replay.** Every one of the 967 stored caller turns through the
  new `_extract_reason`: assert no capture on the known false positives, and
  capture preserved on the true ones. This is the test that makes §3 safe, and it
  needs no phone.
- **F2:** reschedule/cancel/FAQ utterances containing a body part capture nothing.
- **F3:** third-party, historical, corrective and multi-part utterances capture
  nothing.
- **F5-b:** a capture populates `reason_inferred` and leaves `reason` unset;
  `book_appointment`'s A2 guard still blocks.
- **Fails-before** on the two knee openings, as with every fix this week.
- The existing `tests/test_first_turn_extractor.py` must stay green — it pins the
  legacy path's behaviour, which is not in scope to change.

---

## 7 · Sequencing, and the one thing I would not do

`B-20`'s over-screening and this fix touch the same four turns of the same calls.
They are separate commits, but **F6 means shipping this one alone slightly
narrows Layer 1's input while Layer 1's coverage is already known-thin.**

Recommended order:
1. **This plan, steps 1–2** (phrase discipline + fail-open) — pure hardening of an
   inert module. Cannot change a call, since nothing calls it on this path.
2. **Layer 1 coverage** — `vbi_neck`'s same-utterance `AND`, `inflammatory`'s long
   phrases. Zero arms in 104 calls is the real Layer 1 gap.
3. **Wire it in** (steps 3–5) — the first commit here that changes a live call.
4. **`B-20` screening authority**, once you have chosen A/B/C.

**What I would not do:** ship steps 3–5 before step 2. That is the combination
that trades a visible annoyance for an invisible gap, and invisible is the
failure mode this system is worst at.

---

## 8 · Open questions for the owner

1. **F5 — a, b or c?** I recommend **b**. It is the only one that fixes the
   symptom without touching a booking write guard this week.
2. **Does the demo need this at all?** The symptom is one extra question. It is
   the most *audible* item on the register, but `B-09` silently books the wrong
   day. If time is short before Wednesday, this yields to `B-09`.
