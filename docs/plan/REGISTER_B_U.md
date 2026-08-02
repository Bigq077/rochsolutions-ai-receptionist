# B/U register — sweep defects and unverified items

**Opened 2026-08-02.** Branch `latency-eval`, statuses as of `e5a8ee9`.

This register was carried in conversation only until now. Everything below was
re-checked against the code at `e5a8ee9` before being written down; where a row
rests on a claim I could not anchor to a file and line, it says so rather than
implying more confidence than exists.

> ⚠️ **ID collision — read this before citing any ID.**
> `DEFECT_REGISTER.md` uses **`B1` / `B2` / `B3`** (no hyphen) for the 25–29 Jul
> obs sweep: wrong screen for complaint, screen after confirmation, escalation on
> a greeting. Those are **unrelated** to `B-01`…`B-20` here. Always write the
> hyphen, and never merge the two tables.

**Two classes:**

- **`B-nn`** — defects found by reading code and logs. Each is a claim about
  behaviour.
- **`U-nn`** — *unverified* items: code that shipped with a regression test but
  has never been exercised on a live call. Not defects. They are debts that only
  dial time can pay, and they belong to the call suite, not to a code queue.

---

## Status at a glance

| Track | Items | State |
|---|---|---|
| Closed | `U-06`, `B-18` | Shipped 2 Aug with tests |
| Parked | `B-01` – `B-04` | Two provisioning items + the name work — owner decision, not capacity |
| Track A — deterministic, no dial time | `B-13` `B-14` `B-15` `B-17`, then `B-09` | Next up |
| Track B — needs an owner decision | `B-19` / `B-07` | Blocked on filler cadence |
| Track C — prompt-side, needs dial time | `B-06` `B-08` `B-10` `B-11` `B-12` `B-16` | After Track A |
| Track D — verification only | `U-02` – `U-05` | Needs a phone |
| Unclassified | `B-20` | 30-min read before it can be scheduled |
| **Unrecorded** | **`B-05`, `U-01`** | **See "Gaps" below** |

---

## Closed

### `U-06` · reschedule lookup matched phrases instead of judging consent
**CLOSED — `48d9e57`, 2 Aug.**
The last live caller of a predicate empirically proven to accept *"don't use that
one"* as a confirmation. Now routed through `_phone_confirm_verdict`.
Test: `tests/regression/test_reschedule_phone_confirm_verdict.py` (167 lines).

### `B-18` · the same-breath guard discarded the caller's reply during any slow turn
**CLOSED — `ab60809`, 2 Aug.**

Root cause, and the reason this was not a duplicate of `B-19`: the guard dropped
any utterance enqueued before `_last_turn_done_at`, which is set in the `finally`
of the LLM turn — i.e. when *generation completes*, not when audio ends. On a turn
with `llm_ttft_ms=13868`, every second of the caller's experience of that silence
was a second in which their speech was classified as a same-breath straggler and
thrown away. **The slower the system got, the more reliably it discarded the
caller's reaction to the slowness.** `B-19` is the upstream spike and is not ours;
`B-18` was ours, and it converted a spike into a lost utterance and a dead call.

Fix: an age bound. `_SAME_BREATH_WINDOW_S = 2.0` at
[connection.py:1403](../../app/media_streams/connection.py) — a breath is short,
whatever `_last_turn_done_at` says. The overlapping-turn protection at the Spec N
guard is unchanged.
Test: `tests/regression/test_same_breath_window.py` (250 lines).

*Follow-on, not yet done:* the four stacked `_in_name_collection` exemption arms
exist to patch the same over-reach and should now be reducible. Do this only with
the tests above green — it is cleanup, not a fix.

---

## Parked — `B-01` – `B-04`

Two provisioning items plus the name work. Parked by owner decision on 2 Aug, not
for want of capacity. **The name work overlaps `A3` in `DEFECT_REGISTER.md`**
(surname written to a clinical record with no read-back, no confirmation, no audit
trail — four manglings of one caller's name across four calls, three of them
booked to real Acuity events). Whoever unparks `B-01`–`B-04` should read `A3`
first: today the prompt *forbids* a surname read-back
([clinic_template_prompt.py:2057](../../app/prompts/clinic_template_prompt.py)),
so this is a design reversal, not an addition.

---

## Track A — deterministic, testable without a phone

### P3 hygiene batch — `B-13` `B-14` `B-15` `B-17`

All four make logs or config lie. They are cheap, and they are the instruments
every other item on this register will be read through — which is why they come
before the behavioural work rather than after it.

#### `B-13` · a 401 from ElevenLabs is logged as "ready"
**Anchored.** [tts_stream.py:348](../../app/media_streams/tts_stream.py) logs
`"prewarm: connection ready in %.0fms (status=%d)"` at INFO **regardless of
status**. The module already knows a 401 means credits exhausted
(`tts_stream.py:241` sets a flag for exactly that). So the one condition that
silences the assistant entirely reads, in the logs, as a healthy prewarm.
Fix: branch on `resp.status_code`; anything ≥400 is a warning naming the status.

#### `B-14` · the pronunciation dictionary is inactive and the config is the wrong shape
**Anchored.** `config/pronunciation_dict.json` contains:

```json
{ "Alcester": "Awlstuh" }
```

The loader at [tts_stream.py:274](../../app/media_streams/tts_stream.py) reads
`pronunciation_dictionary_id` and `version_id`, finds neither, warns, and leaves
`_PRON_DICT_LOCATOR` unset. **The dictionary has never been active.** The file is
a word→phoneme map that no code path consumes; the loader wants ElevenLabs
locator IDs produced by `scripts/setup_pronunciation_dictionary.py`.
Note also that `Alcester` is Susie/JV vocabulary — the file may be stale as well
as mis-shaped. Fix is to decide which of the two shapes is intended and make the
other side match; do not "fix" the loader without checking whether anyone wants
the dictionary on at all.

#### `B-15` · `capture_phase` mislabelled
**Not anchored.** Carried from the sweep as described. `capture_phase` is
computed via `_lat_capture_phase` at
[connection.py:6828](../../app/media_streams/connection.py),
`:12937` and `:13500`. Which of those sites carries the mislabel was not
established before this file was written — **establish it before fixing**, or the
diff will land in the wrong place.

#### `B-17` · a log line that is a no-op
**Not anchored.** Carried as described; the specific line was not recorded.
Identify it before scheduling — this is the cheapest row here and also the one
most likely to be mis-attributed.

---

### `B-09` · "next Friday" resolves +12 days

**Re-scoped 2 Aug — the original estimate was wrong, and the reason matters.**

It was queued as "pure date arithmetic, ~1 h, fully testable." That assumes there
is arithmetic to fix. There is not:

- `_DOW_RE` and `_DOW_INDEX`
  ([receptionist_tools.py:323-331](../../app/tools/receptionist_tools.py)) are
  **defined and never referenced anywhere in the codebase.** Dead code, both.
- The week-filter resolver handles `"next week"`, `"this week"`, `"week of …"`,
  `"from <date>"` and two-date ranges. It has **no `next <weekday>` branch at
  all.**
- The only thing steering the phrase is prompt text:
  [clinic_template_prompt.py:2292](../../app/prompts/clinic_template_prompt.py)
  hands the model next Monday's literal date as the anchor for *"not this week"*.
  A model counting Friday from that anchor on a Sunday call lands **exactly +12
  days** — the observed symptom.

So `B-09` is **model-side arithmetic with no deterministic floor under it.** The
fix is to add a resolver and wire it into the `date_hint` path, then decide
whether it overrides the model or merely bounds it. **Half a day, not an hour**,
and it should follow the hygiene batch rather than precede it.

> **Build it once.** `A2` in `DEFECT_REGISTER.md` is the same shape from the other
> end: the spoken day-name is never checked against the actual date.
> `v3_confirmed_slot_phrase` is scraped from the model's spoken text
> ([connection.py:10122](../../app/media_streams/connection.py)) and Gate 5 then
> forces every later readback to agree with it — so a wrong weekday is made
> *consistent*, not correct. One weekday↔date resolver should serve `B-09` and
> `A2` both. Building it twice is how we end up with two that disagree, which is
> the standing failure pattern of this codebase (see `DEFECT_REGISTER.md` §A4:
> one affirmative vocabulary maintained in four places).

---

## Track B — blocked on an owner decision

### `B-19` / `B-07` · the filler is one-shot, so an upstream spike becomes bare silence

`B-19` is the upstream LLM latency spike — not ours to fix. What makes it a dead
call rather than a slow one is that the filler fires **once**. A 14 s spike
therefore produces ~14 s of silence. Fix is to re-arm the filler on a timer while
a turn is genuinely in flight.

This subsumes most of `B-07` (filler inconsistency), because the inconsistency is
partly the one-shot flag being consumed by an earlier turn.

**Decision needed from the owner — how chatty:** one filler then a second at ~5 s,
or a continuing "still with you" cadence? Everything else about the fix is
determined. **Do not pick this default silently** — it is the most audible change
on the register and it is a judgement about the clinic's voice, not about code.

---

## Track C — caller-visible, prompt-side, no deterministic test

Needs dial time *between* changes; prompt edits cannot be regression-tested the
way Track A can, and two prompt changes shipped together cannot be attributed.

| ID | Defect |
|---|---|
| `B-06` | 11.8 s openers |
| `B-08` | Asks for information the caller has already given |
| `B-10` | CTA false positive |
| `B-11` | Turn-end fires on a statement |
| `B-12` | Wrong dead-air wording |
| `B-16` | `time_of_day_preference` inferred from a slot pick |

---

## Track D — verification only, needs a phone

Not defects. Code that shipped with a regression test and has never met a real
caller. These belong in the call suite.

| ID | What needs proving |
|---|---|
| `U-02` | C5 rung-3 termination — the ladder cannot loop forever |
| `U-03` | C6 lookup-key scope — no read-back where the number is a search key |
| `U-04` | Rung-2 verbal fallback |
| `U-05` | The `dc5c89d` bound — two unsettled answers go to the keypad |

> `CALL_SUITE_2026-08-02.md` names build `7610f9a` and is **eight commits stale**
> as of `e5a8ee9`. Its Call 1 tests a date guard that `7b698f6` has since
> rewritten. Refresh it to HEAD before dialling, or the sweep scores the wrong
> build — and confirm the Render service/branch pairing first, which per
> `README.md` correction 15 is not knowable from this repo.

---

## Unclassified — needs a read before it can be scheduled

### `B-20` · `clinical_screening` ORPHAN, twice

Logged twice. **Do not assume a model-side mistake.** Layer 1 was dormant for a
long stretch behind a broken STT keyterm boost, and the arming path is the first
thing to rule out — an orphan is exactly what a screen that armed and never
matched would look like. ~30 minutes of reading
`app/media_streams/clinical_screening.py` (1011 lines; note
`FAILURE_MODE_REGISTER.md` FM-04 still says 299) before it can be sized.

This is the only row on the register that touches a clinical path. It outranks
everything in Track C on impact if the arming path turns out to be the cause.

---

## Gaps in this register

Recorded honestly rather than filled in:

- **`B-05` has no entry.** It was not carried in the 2 Aug update and I do not
  know what it was. Either it exists and is lost, or the numbering skipped. Do
  not reuse the ID until that is settled.
- **`U-01` has no entry.** Same. The `U` series as carried runs `U-02`–`U-06`.
- **`B-15` and `B-17` have no file:line anchor** — see their rows above.

If you find the source these came from, fold it in here and delete this section.

---

## Rules for this file

1. **The code wins.** If this file and the tree disagree, fix the file and note
   it. These plan documents have been wrong fifteen times; the count is in
   `README.md`.
2. **A row without an anchor is a lead, not a finding.** Say which it is.
3. **Closing a row requires a commit SHA and a test path**, both named in the row.
4. **`U-nn` rows close on a call, not on a test.** A green test is what put them
   in the `U` series in the first place.
