# `S-` · clinical screening register

Companion to `REGISTER_B_U.md`, same rules. Opened 21 Aug 2026 out of the
screening plan (S-1/S-2/S-3), because the `B-` series had grown three separate
screening threads that kept being confused with each other:

| thread | what it is |
|---|---|
| `B-20` | Layer 2 — the model's *conditional* screening authority. **CLOSED**, `ab39553`. |
| `B-74` | the answer classifier had no affirmative path. **FIXED**, all four branches. |
| `S-nn` | Layer 1 — trigger arming and answer handling in `clinical_screening.py`. |

All anchors are on `latency-eval` at **`168e0d2`**.

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
routine framing before the question, at no cost to recall. Not yet done.

**Evidence limits, stated so they are not overstated.** Replay is a
change-detector here, not a validator: 37 of the 38 screen-touching calls in the
corpus are from a dev handset (correction 24), and the corpus is unchanged by an
additive fix. The arms above are pinned by
`tests/regression/test_a_screen_never_gates_on_its_own_answer.py` (24 tests) and
must be confirmed on the demo line.

**`U`-debt: not exercised on a call.**

---

## Open, not fixed

| # | Defect | Anchor | Note |
|---|---|---|---|
| `S-4` | **21 stranded screens** — armed, asked, never resolved | [`clinical_screening.py`](../../app/media_streams/clinical_screening.py) `SCREEN_TRUNCATED_KEY` / bounded re-ask `e595df5` | The bounded re-ask should cut this. **Unmeasurable by replay** — stored transcripts predate it, so replay shows how many screens would now get a re-ask, never whether the caller answered it. Needs a live call. |
| `S-5` | `vbi_neck`'s AND group is **14/14 the question's own answer** | [`clinic.json`](../../app/clinics/jv_v1/clinic.json) `vbi_neck.trigger_all_groups` | Same structural flaw as the rejected Phase 3, pre-existing. Mitigated but not closed by the `S-3` decisive keywords. Un-grouping it would screen every neck caller for blackouts — an owner call, not an engineering one. |
| `S-6` | Screening framing is **alarming for a benign presentation** | [`clinic.json`](../../app/clinics/jv_v1/clinic.json) `screen_question` | Phase 4. Constrained by `test_screen_wording_no_body_part_assertion.py` (the escalation must not assert a symptom the caller denied — this cost two rewrites) and `test_screen_cauda_lay_phrasing.py`. |

---

## Rules

Same as `REGISTER_B_U.md`: the code wins; a row without an anchor is a lead, not
a finding; closing a row requires a commit SHA **and** a test path; `U`-debt
closes on a call, not on a test.
