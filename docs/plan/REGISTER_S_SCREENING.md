# `S-` · clinical screening register

Companion to `REGISTER_B_U.md`, same rules. Opened 21 Aug 2026 out of the
screening plan (S-1/S-2/S-3), because the `B-` series had grown three separate
screening threads that kept being confused with each other:

| thread | what it is |
|---|---|
| `B-20` | Layer 2 — the model's *conditional* screening authority. **CLOSED**, `ab39553`. |
| `B-74` | the answer classifier had no affirmative path. **FIXED**, all four branches. |
| `S-nn` | Layer 1 — trigger arming and answer handling in `clinical_screening.py`. |

All anchors are on `latency-eval` at **`69a68a5`**.

---

## `S-1` · a caller who volunteered a decisive red flag was **asked it back** — **FIXED `latency-eval`, NOT ported**

**Anchors**
- [`clinical_screening.py:722`](../../app/media_streams/clinical_screening.py) — `_decisive_red_flag`
- [`clinical_screening.py:1246`](../../app/media_streams/clinical_screening.py) — the guard site, `_decisive or _red_flag_hits(...) >= 2`
- [`clinic.json`](../../app/clinics/jv_v1/clinic.json) — `clinical_screening.screens[].decisive_red_flags`

**Live call.** JV `CA4feeeec6f9077d4912eb7d2a7f1d6846`, 21 Aug. The caller opened
with *"really bad back pain … losing feeling in my legs … a bit of trouble
controlling my bladder"*. The already-answered guard required **two** ordinary
red-flag keywords, and he scored one — `bladder`. `"losing feeling"` was not a
configured phrase and `"my legs"` is not `"both legs"`. So Susie asked him
whether he had any changes in bladder or bowel control. He abandoned the call.

**Why two was the right bar and still is, for ordinary keywords.** *"My calf is
painful and swollen"* is a strain, not a DVT. The fix is not a lower global
threshold — it is a short second list of symptoms that are decisive at a count
of one, which bypasses the question entirely and must therefore stay short.
Denied occurrences do not count, exactly as in `_red_flag_hits`.

**Fixed.** `decisive_red_flags` per screen; escalates without asking. Regression:
`tests/regression/test_a_volunteered_red_flag_is_not_asked_back.py`.
Trigger recall for the same call closed separately under **`S-3`** — he did not
arm Layer 1 at all until `168e0d2`.

**`U`-debt: not exercised on a call.** Demo line only, `+447366263180`.

---

## `S-2` · **"er yeah I do"** did not flag a red-flag screen — **FIXED `latency-eval`, NOT ported**

**Anchors**
- [`clinical_screening.py:691`](../../app/media_streams/clinical_screening.py) — `_NOISE_WORDS`
- [`clinical_screening.py:782`](../../app/media_streams/clinical_screening.py) — the leading-noise strip in `classify_screen_answer`
- [`clinical_screening.py:806`](../../app/media_streams/clinical_screening.py) — the `_AFFIRMATIVE_LEAD` test it feeds

A disfluency in front of the answer defeated the affirmative path that `B-74`
had just built: `first_word` was `er`, not `yeah`, so a plain yes to a red-flag
screen returned `unclear` and the safety decision went silently to the LLM.
Two corpus calls answered exactly this way.

**Scope, deliberately narrow.** The strip runs only on the affirmative branch,
after every negative branch has been tried. It can therefore only turn
`unclear` into `red_flag` — never flip a `clear`. It is **not** applied to the
negative branches: those already work, and widening them adds false-clear
surface on the one path where a false clear is the dangerous direction.

Regression: `tests/regression/test_a_plain_yes_flags_a_red_flag_screen.py`
(parametrised over all six screens, disfluency-prefixed variants added).

---

## `S-3` · the cauda equina screen **did not arm** on the phrasings that matter most — **FIXED `latency-eval`, NOT ported**

**Anchors**
- [`clinical_screening.py:246`](../../app/media_streams/clinical_screening.py) — `_screen_triggered`, now `groups OR keywords`
- [`clinic.json`](../../app/clinics/jv_v1/clinic.json) — `cauda_equina.trigger_keywords` 28 → 41, `vbi_neck.trigger_keywords` 0 → 6
- [`stt_stream.py:191`](../../app/media_streams/stt_stream.py) — `_KEYTERM_STOPWORDS`, third dated block

**What was actually wrong.** Not over-triggering — under-triggering. Every one
of these armed **nothing**: *"I've been having trouble controlling my bladder"*,
*"I'm losing feeling in my legs"*, *"my legs keep giving way"*, *"I blacked out
twice this week"*. The last of these needed the engine change: `vbi_neck` gates
on neck **AND** a neuro sign, and `_screen_triggered` used to `return` on the
groups branch, so a screen carrying both keys silently ignored its keywords.

**What was proposed and rejected — read before re-proposing it.** Phase 3 of the
plan was to narrow `cauda_equina` and `dvt` to `trigger_all_groups`. Measured,
**13 of 25** and **15 of 16** of those second groups *are the screen question's
own answer*. Gating on the answer converts a screen into a confirmation: it can
only fire once the caller has volunteered the red flag, and the caller the
screen exists for is the one with a bad back and early saddle numbness they have
not thought to mention. It reversed **F-032** (a P1 missed screen) and turned 29
tests red. `trigger_all_groups` stays on `vbi_neck`, the one screen where the AND
bar is the syndrome rather than the answer.

The over-screening complaint behind Phase 3 is real but is a **tone** problem —
routine framing before the question, at no cost to recall. Done under **`S-6`**.

**Evidence limits, stated so they are not overstated.** Replay is a
change-detector here, not a validator: 37 of the 38 screen-touching calls in the
corpus are from a dev handset (correction 24), and the corpus is unchanged by an
additive fix. The arms above are pinned by
`tests/regression/test_a_screen_never_gates_on_its_own_answer.py` (24 tests) and
must be confirmed on the demo line.

**`U`-debt: not exercised on a call.**

---

## `S-6` · the safety questions **apologised for themselves** — **FIXED `latency-eval` `69a68a5`, NOT ported**

**Anchors**
- [`clinic.json`](../../app/clinics/jv_v1/clinic.json) — `screen_question` on cauda_equina / dvt / serious_spinal / vbi_neck
- [`clinical_screening.py:374`](../../app/media_streams/clinical_screening.py) — `_ORPHAN_STOPWORDS`

Phase 4, and load-bearing rather than cosmetic: with the Phase 3 narrowing
rejected under **S-3**, framing is the only remaining lever on "the bladder
question is alarming for someone who just wants a massage".

"Sorry to ask" and "Just to be safe" tell a benign caller there is something
to worry about. The four lead-ins now say the question is routine and asked of
everyone. Clinical content byte-identical; `trauma_fracture` (live polarity fix
`becd7f8`, under review) and `inflammatory` (already neutral, advisory) left
alone on purpose.

**The line held, and why.** The plan's own example ended *"almost everyone says
no to these"*. `classify_screen_answer` is single-polarity — a negative lead is
`clear`, full stop — so priming toward "no" manufactures **false clears** on the
one path where a false clear is the dangerous direction. Measured: `"no"`,
`"no i don't think so"`, `"no nothing like that"`, `"erm no"` all clear and
unblock the booking. **Normalise the asking; never suggest the answer.**

**Two hazards the plan did not know about**, both found by measuring, both now
pinned:

1. `_screen_evidence_words` builds orphan detection from words **unique to one**
   screen question. The first draft made `everyone` and `theres` unique evidence
   for cauda_equina — a false ORPHAN generator against the screen `B-20` is
   scored on, and the `"proper"` collision of 3 Aug repeating. Evidence sets are
   now byte-identical before and after the pass.
2. `B-31`: `last_bot_prompt` truncates at 200 and a long paraphrase loses its
   `?`, switching orphan matching off. The questions land at 178–193; the cap is
   pinned per field.

Prompt hashes re-pinned `243a1be416ea9fc9` → `11fc9c7fcab478d9` in **both**
tables (four tests read them), with containment verified by rendering every
clinic before and after — exactly four `ASK:` lines differ, the other four
clinics byte-identical.

Regression: `tests/regression/test_a_screen_question_is_framed_as_routine.py`
(55 tests), each pin demonstrated to fail on the wording it exists to catch.

**`U`-debt: not exercised on a call.**

---

## Open, not fixed

| # | Defect | Anchor | Note |
|---|---|---|---|
| `S-4` | **21 stranded screens** — armed, asked, never resolved | [`clinical_screening.py`](../../app/media_streams/clinical_screening.py) `SCREEN_TRUNCATED_KEY` / bounded re-ask `e595df5` | The bounded re-ask should cut this. **Unmeasurable by replay** — stored transcripts predate it, so replay shows how many screens would now get a re-ask, never whether the caller answered it. Needs a live call. |
| `S-5` | `vbi_neck`'s AND group is **14/14 the question's own answer** | [`clinic.json`](../../app/clinics/jv_v1/clinic.json) `vbi_neck.trigger_all_groups` | Same structural flaw as the rejected Phase 3, pre-existing. Mitigated but not closed by the `S-3` decisive keywords. Un-grouping it would screen every neck caller for blackouts — an owner call, not an engineering one. |

---

## Rules

Same as `REGISTER_B_U.md`: the code wins; a row without an anchor is a lead, not
a finding; closing a row requires a commit SHA **and** a test path; `U`-debt
closes on a call, not on a test.
