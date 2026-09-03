# Session record — 2026-09-03, late

Sits alongside `OPEN_DEFECTS_2026-09-03_EVENING.md` (the 15:40 verification
call, D-A and D-B), which it does **not** supersede. See §4 — one of the fixes
below touches D-B's territory and does **not** close it.

**Branch state at close:** `latency-eval` = `591133d9` · `production` =
`4eda31f3`, unchanged.
**Pushed to `latency-eval` only.** The demo line autodeploys. Nothing promoted.
**Revert targets in order:** `bea61a7f`, `d7097886`, `bf85f647`.
**Test baseline:** `98 failed / 7,984 passed` at `bf85f647` →
`98 failed / 8,024 passed` at `591133d9`. Failing set **byte-identical at every
step**, diffed as sets with digits kept in the filter (`[a-zA-Z_./-]` hides
`test_b84_*`). The +40 are the 40 new tests.

---

## 1. Shipped

| commit | defect | tests |
|---|---|---|
| `d7097886` | **B-132** — an ordinary answer torn down at playback | 14 |
| `bea61a7f` | the twenty-word rule never reached the turn that broke it | 10 |
| `591133d9` | **§2.2** — two of twelve times, described as all of them | 16 |

All three red-then-green **proven by neutering the fix**, not assumed. Every
edit is an insertion; no existing line of engine code was changed.

### 1.1 `d7097886` — B-132

CA91020004, northgate, 2 Sep 16:43. Three chunks, ~20s of audio. A partial of
`'okay'` tore the turn down 5.2s in, inside chunk 2. The teardown discarded the
rest of chunk 2 **and** chunk 3, then spoke chunk 3 — a sentence the caller had
never reached.

B-120 applied to ordinary content: the fourth commit running to change the
**recovery** rather than the trigger (B-67, B-107, `c65f2a1c`, B-120).

**The scope gate is the part worth keeping.** The existing arm's premise —
`last_question` is *"by construction equal to the chunk just spoken"* — is
**true** for a single-chunk turn. On a multi-chunk turn it is the LAST chunk,
which here is exactly the sentence nobody heard. The new arm therefore requires
`len >= 2` and leaves the single-chunk case alone.

Replay is a **suffix**, capped at `_CONTENT_REREAD_MAX_CHARS = 400`: lost audio
is by construction a suffix of what was queued, so trimming from the front is
principled rather than arbitrary.

### 1.2 `bea61a7f` — the length rule was scoped to the wrong block

**`OPEN_DEFECTS_2026-09-02.md` §5 called this non-adherence. It was not.** The
rules governing a condition acknowledgement cap sentence **COUNT**:

* BOOKING STEPS 1, condition-led exception — *"ONE short turn: one or two sentences"*
* BOOKING STEPS 2, clinical complaint — *"one or two sentences of SPECIFIC understanding"*

One 35-word sentence satisfies both. The ~20-word **SENTENCE-LENGTH** rule is
real but lives in `_render_faq`, under the heading `FAQ`, and a condition
acknowledgement inside the booking flow is not an FAQ answer. **Every rule that
reached that turn was obeyed.**

`_render_faq` had already recorded why count is not enough — *"one live answer
was only three sentences and still ran twenty seconds, on a single
138-character middle clause."* That is this defect, one block over.

So the fix is **scoping, not wording**, and §5's instinct to tighten the FAQ
prose further would have bought nothing.

Confirmed as §5 asked: **northgate ships `treatment_guidance`**, so
`_condition_families` renders empty and `clinic_template_prompt.py:1602` is
**dead config for this clinic**. The block the defect docs pointed at could not
have been the cause.

⚠️ **This is the same finding `OPEN_DEFECTS_2026-09-03_EVENING.md` D-A cause 1
relies on** — it cites §5's non-adherence conclusion to argue "more wording is
unlikely to help". The conclusion was right; the reason was not. D-A's own
citation, `:1594` *"Keep it to ONE sentence"*, is `_condition_families` —
**dead config on northgate**, which is the clinic D-A was observed on. D-A
cause 1 needs re-scoping before anyone acts on it.

### 1.3 `591133d9` — §2.2, and the conflict that was not one

The doc: *"B-97 and B-99 are in direct conflict and multi_day currently resolves
it by saying nothing. Needs a decision, not a patch."*

**It dissolves.** `more_times` has two consumers and B-99 objects to only one:

| consumer | B-99's objection | outcome |
|---|---|---|
| the SPOKEN tail, *"…a few others that day"* | genuinely day-less after a three-day readout | **valid — left suppressed** |
| the model's licence to claim completeness | none; never the target | **restored, as a rule** |

The multi_day payload was **not short of information**: `_present_days`
([receptionist_tools.py:3376](app/tools/receptionist_tools.py:3376)) carries
each day's FULL `slot_times`, because the truncation at `:3480` sits inside the
`single_day` branch. The model held all twelve and contradicted its own payload.

What was missing was a rule, and the gap is an **asymmetry**: BOOKING STEPS 5
forbade the over-promise (B-97's failure, which once looped a caller into
hanging up on judge score 1) and said nothing about the opposite error. Both
halves now stand, two sentences apart.

**No extra spoken words**, deliberately — the readout already runs 17.9s on a
call where Susie speaks 60% of the time (§2.8).

---

## 2. NEW — the payload's incompleteness fields never reach the model

> **P2. Anchored. Not fixed.**

`check_availability` computes **five** signals meaning "there is more than I am
showing you". Each was written for a named live defect. **Not one appears
anywhere in the rendered prompt.**

| field | written at | written for |
|---|---|---|
| `days_found_in_window` | [receptionist_tools.py:3431](app/tools/receptionist_tools.py:3431) | B-94 |
| `days_not_shown` | [:3432](app/tools/receptionist_tools.py:3432) | B-94 |
| `more_times` | [:3500](app/tools/receptionist_tools.py:3500), [:3512](app/tools/receptionist_tools.py:3512) | B-97 / B-98 |
| `times_not_shown` | [:463](app/tools/receptionist_tools.py:463) | B-116 |
| `band_spent_label` | [:3518](app/tools/receptionist_tools.py:3518) | B-117 |

Verified by **rendering** northgate's prompt (103,958 chars) and grepping each
name — zero hits for all five. Rendered, not read from source, per
[config-keys-that-never-reach-the-model].

`:3504` states the contract the code believes it has:

> *"`more_times` is what the formatter reads to decide whether it may use a
> COMPLETENESS opener"*

**"The formatter" is the whole truth of it.** `more_times` reaches
`build_slot_offer` and decides a spoken tail. It never reaches the model as a
fact about the diary — so on any turn the model composes itself, including
every day-pick answered from history (which BOOKING STEPS 6 *requires*), the
signal is absent.

**Fourth instance of the same pattern, and the largest: five fields, five
defects, all mute.**

**Do not schedule this as "add the fields to the prompt".** `more_times` and
`times_not_shown` are only ever set on the `single_day` branch, so on multi_day
they would be *absent* rather than false — and absence reading as "nothing
hidden" is precisely B-94. Decide the semantics first.

---

## 3. Corrections to the defect documents

### 3.1 B-127 is already taken

`OPEN_DEFECTS_2026-09-02.md` §4 numbers the barge-in defect **B-127**. That
number was spent on 1 Sep for *a spoken ordinal reads the keypad's table* —
[connection.py:9418](app/media_streams/connection.py:9418),
[slot_followup.py:619](app/tools/slot_followup.py:619), and
`tests/regression/test_b127_a_spoken_ordinal_reads_the_keypad_map.py`.

Shipped as **B-132**; a note at the arm points both ways. **§4 of the 09-02 doc
still says B-127.**

### 3.2 §5's "non-adherence" conclusion is wrong

See §1.2, and the warning there about D-A.

### 3.3 `test_b57` carries a second pin table

It pins `vital_edge`; `test_b55`'s does not. A re-pin comment written from the
b55 table was wrong about this and **the test caught it**. Both now corrected.
Hashes this session: jv_v1 `35b2ba94` → `d1c15e3d` → `45365575`; vital_edge
`f92b8c5f` → `9ad7cf7c`. demo / theorem / theorem_v3 byte-identical throughout.

### 3.4 `OPEN_DEFECTS_HOLD_AND_SLOTS_2026-09-01.md` does exist

`OPEN_DEFECTS_2026-09-02.md` opens by saying it does not. It is on
`latency-eval` under `docs/plan/`; the claim was made from a worktree without
it. **Its P8 is still open** — a closed day reported as "too soon to book",
Theorem only.

---

## 4. What `591133d9` does NOT close

**D-B is untouched.** `OPEN_DEFECTS_2026-09-03_EVENING.md` §D-B reaches §2.2
"through a new door": a named-day follow-up answered from context with **no
`check_availability` call**, therefore no producer, no offer record, and a
keypad still pointing at days.

`591133d9` forbids the model *characterising* a partial readout as complete. It
does nothing about a turn that never queried in the first place, and a rule
cannot supply times the model did not fetch. **Do not mark §2.2 closed on the
strength of this commit** — the claim half is addressed, the under-offering
half is D-B and still open, with its fix already scoped there (route the
named-day follow-up through a producer, as `more_days_speech` already does).

**D-A is also untouched**, and its first action is unchanged: find what
capitalises the continuation chunk before deciding anything.

---

## 5. Owed before promotion

`production` is **five commits behind** and none of this has been heard on a
call.

1. **The declining meridiem.** `"8 pm"` against an offer holding only `08:00`.
   The agreeing case is verified live; this half was wrong on patient lines.
2. **One Theorem call.** Theorem short-circuits to the Acuity executor, so a
   week of booking-path work has never touched it.
3. **B-132** — interrupt a long answer with a backchannel mid-sentence; look
   for `tore down a %d-chunk answer at playback`. It fires on a path that ran
   on **both** calls of 2 Sep.
4. **`bea61a7f` and `591133d9` are nudges, not enforcement.** `_render_faq`
   records that its own wording *"was present, and ignored, on every call of a
   seven-call review."* If a call still produces a thirty-word sentence or a
   completeness claim, the answer is enforcement — and that collides with the
   rejected Option 6, so it needs a decision.

---

## 6. Blocked, and on what

| row | blocked on |
|---|---|
| §2.5 surname gate | a decision: warn-only or block |
| §2.4 band-only / positional picks | a decision: reopening the 30 Aug silence contract |
| §2.6 STT name capture | a wav off Render; `logs/audio/` is empty and every replay harness works on transcripts |
| §2 above (payload fields) | semantics, per the warning there |

**B-31 examined and deliberately left alone.** `last_bot_prompt` has ~149
references in the live media-streams path and **four write gates** keyed on it
(`_booking_confirmation_asked`, `_cancel_retention_asked`, `_direct_cancel_cta`,
`_move_confirmation_asked`). Both proposed fixes — raise the cap, or
sentence-align it — can only ADD text to a set of substring predicates, which
loosens all four. A write gate opening too easily is the B-58 family. The
`last_question` fallback works. Revisit inside Phase 2's "one record" work,
where those gates are being touched anyway.

---

## 7. Method notes earned this session

**Check whether the rule you are about to tighten is even in scope.** Two of
three fixes here were filed as the model ignoring instructions. In both, the
instruction existed and did not reach the turn. A prompt is not one document —
it is a stack of blocks with headings, and a rule under `FAQ` does not govern
the booking flow.

**A conflict between two rules may be a conflation of two consumers.** §2.2 was
filed as needing a decision between B-97 and B-99. It needed neither: the flag
fed two things and only one was what B-99 objected to.

**Grep the ID before you use it.** B-127 was live in three files and a test.

**Read a file before writing it, even one you think you are creating.** This
record was nearly written over `OPEN_DEFECTS_2026-09-03_EVENING.md`, which
already existed from `4eda31f3` and holds D-A and D-B.

**A trailing newline is part of an inserted block.** A scripted insertion that
omitted it concatenated the following line into a `SyntaxError` — loud here,
silent if it had landed inside a comment.

**`git checkout -- <file>` is the cheapest neuter** once the rest is committed:
it reverts exactly the fix under test and the re-apply is one script. Used for
all three fixes.
