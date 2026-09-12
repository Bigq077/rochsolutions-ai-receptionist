# Susie elaborates symptoms the caller never described

> **FIXED `8e838f0f`, 9 Sep 2026 19:30 — 21 minutes after this document was
> written, and never recorded here.** The "WHOSE SYMPTOM IS IT" rule in
> `_render_condition_fluency` names the construction ("that kind of stiffness
> you're getting" presupposes they said stiffness) and narrows "THEIR specifics"
> to what they actually stated. Ships with
> `tests/regression/test_a_hallmark_belongs_to_the_condition.py` and re-pinned
> prompt hashes; promoted to `production` the same night; **call-verified on
> `CA5e14516b` (9 Sep 23:08)**: "ankles can be tricky to get fully right —
> worth having Priya take a proper look." The rule renders wherever the
> condition library does (northgate, jv_v1) — the only prompts that carry the
> hallmark-feature instruction that produced the exhibit; vital_edge and
> theorem_v3 never had it. Carried as open by the 12 Sep audit because of this
> header's absence — corrected 12 Sep. Everything below is the diagnosis as
> written on 9 Sep.

**Found** 2026-09-09 18:06, northgate `CAeedecabfd099658cb5d486d5fbd63d7a`,
build `1bc1dfe2`, judge **2**, tag `hallucination`.
**Not a regression.** Pre-existing, and the oldest instance in the corpus is
2026-08-24. Surfaced only because the slot chain stopped masking it.

## The exhibit

    caller : "um just for the left ankle and achilles tendons"
    Susie  : "Oh, sorry to hear that — that kind of stiffness and soreness in the
              Achilles, especially that first-few-minutes-in-the-morning feeling,
              responds really well once it's properly assessed."

The caller reported a body part. Susie reported **stiffness**, **soreness** and
**morning symptoms** — three clinical findings nobody gave her. The judge's own
words: *"Susie invented symptoms."*

Same shape, northgate 2026-08-29 (`7f0ebd032699`): *"The caller only ever asked
about a sports massage, yet Susie said 'I'm sorry to hear that…'"*.

## Why it matters more than a judge score

This is a physiotherapy clinic. A receptionist who narrates symptoms back to a
patient is (a) inventing clinical content, and (b) doing it in a register that
sounds like assessment. The failure-mode register's bar is that Susie never
produces a confident statement she cannot source; this is that, in the clinical
layer rather than the booking layer.

It also interacts with screening: `_CLINICAL_Q_SIGNALS` and the orphan matcher
key off what was SAID. Speech that invents symptoms is speech the screening
layer may later treat as caller-supplied.

## Scope, measured

`hallucination` is on **37 of 788** judged calls (~5%), but the tag is a mixed
bag — it also covers stalling, Susie answering her own question, and the
self-reversal at `9385a0d6969c`. The invented-symptom shape needs its own count
before anyone estimates a rate.

## Why it is NOT fixed here

1. It is caller-facing clinical copy. The owner has reserved that class of
   decision before (N5's third stall wording) and the empathy line is
   deliberate — an unhedged "Oh, sorry to hear that —" head is what B-142 and the
   situational-head work exist to produce.
2. It is a **fifth** change in one afternoon, in a session that already produced
   a four-commit chain where each fix broke the next. `SLOT_TIME_CHAIN_2026-09-09.md`
   documents exactly that pattern. Starting an unverifiable prompt change on
   clinical language at the end of it would be the same mistake again.
3. Nothing here can be verified without a call.

## The shape a fix should take

A constraint, not a rewrite: the model may acknowledge what the caller SAID and
must not add findings they did not state. That is one prompt rule plus a
regression test over the stored openers, and it belongs on its own branch with
its own call. Do NOT widen the situational heads to fix it — the head
("Oh, sorry to hear that —") is not the problem; the sentence after it is.
