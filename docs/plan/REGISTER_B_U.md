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
| Closed | `U-06`, `B-18`, `B-13`, `B-14`, `B-15` | Shipped 2 Aug with tests |
| Parked | `B-01` – `B-04` | Two provisioning items + the name work — owner decision, not capacity |
| Track A — deterministic, no dial time | `B-09` | **Next up** |
| Track B — needs an owner decision | `B-19` / `B-07` | Blocked on filler cadence |
| Track C — prompt-side, needs dial time | `B-06` `B-08` `B-10` `B-11` `B-12` `B-16` | After Track A |
| Track D — verification only | `U-02` – `U-05` | Needs a phone |
| Unclassified | `B-20` | 30-min read before it can be scheduled — **do this first, it is the only clinical row** |
| **Deferred to last** | `B-17`, `B-22` (the SMS family) | Owner decision 2 Aug — unprovable on this branch, see below |
| **Unrecorded** | **`B-05`, `U-01`** | **See "Gaps" below** |
| New — plan written | `B-23` (reason re-asked when already given) | `PLAN_REASON_CAPTURE.md`. Owner decision open on F5 |

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

### P3 hygiene batch — `B-13` `B-14` `B-15` (`B-17` deferred, see below)

All of them make logs or config lie. They are cheap, and they are the instruments
every other item on this register will be read through — which is why they come
before the behavioural work rather than after it.

#### `B-13` · a 401 from ElevenLabs is logged as "ready"
**CLOSED — `a5e8415`, 2 Aug.** The prewarm interpolated `resp.status_code` into
its log line but never branched on it, so an auth failure read as
`prewarm: connection ready in 214ms (status=401)` at INFO. The socket really is
warm on a 401 — that is why it looked like success — but the credential is dead,
and the prewarm fires at webhook time, the earliest anything can know.
`synthesise_chunk` already calls the same status an error and switches the
process to the OpenAI fallback; it just does not run until a caller is on the line.

Now: 401 → error naming the cause; other 4xx/5xx → warning naming the status;
2xx → today's INFO line verbatim. That last branch is half the fix — a prewarm
that warns on every healthy start drowns the line it exists to surface.
Unchanged and asserted: the return value (latency accounting, not health) and
`_ELEVENLABS_EXHAUSTED`, which stays unarmed here.
Test: `tests/regression/test_prewarm_status_is_not_ready.py`, 12 cases;
6 verified failing on the parent commit.

> **Still open, deliberately:** should the prewarm arm `_ELEVENLABS_EXHAUSTED`?
> Today a startup 401 still costs the first caller a failed synth before the flag
> flips on the synth path. Arming it at startup moves the fallback decision onto a
> probe — defensible, but a behaviour change needing its own evidence. Not yet
> given an ID; give it one if it is scheduled.

#### `B-14` · the pronunciation dictionary is inactive and the config is the wrong shape
**CLOSED — turned off, 2 Aug.** Owner decision: off.

`config/pronunciation_dict.json` contained `{"Alcester": "Awlstuh"}` — a
word→alias map — while the loader wanted a `{pronunciation_dictionary_id,
version_id}` locator pair. The lookup failed on every startup, `_PRON_DICT_LOCATOR`
stayed `None`, and the request body was never touched. **The dictionary had never
been active.**

Removed rather than repaired: the one word it demonstrably mattered for is
already covered locally and deterministically by `_TTS_SUBSTITUTIONS_ELEVENLABS`,
and enabling it would have put pronunciation in two places free to disagree — the
§A4 pattern. It had never executed, so removal cannot change a call; *enabling*
would have been the risky move three days before a demo.
Test: `tests/regression/test_pronunciation_has_one_owner.py`, 6 cases — five of
them pinning the local Alcester rule, since that is now the only line of defence.
`scripts/setup_pronunciation_dictionary.py` is kept; re-enabling is documented in
the tombstone comment at the removal site.

#### `B-21` · "Redditch" has no pronunciation rule — NEW, opened by the B-14 removal
**Lead, not a finding.** `scripts/setup_pronunciation_dictionary.py` states that
`Redditch` synthesises with a doubled-d artefact and the dictionary carried an
alias for it. `_TTS_SUBSTITUTIONS_ELEVENLABS` covers **only** `Alcester`, so with
the dictionary gone nothing covers Redditch.

Deliberately not fixed alongside `B-14`: adding a substitution changes spoken
output on the strength of a claim in a script docstring of unknown age, and
Redditch may not be live vocabulary on this branch at all (several
`fix/*redditch*` branches redirect it to Alcester). **Confirm by ear before
changing anything** — one call where Susie says the word settles it.

#### `B-15` · `capture_phase` mislabelled
**CLOSED — `779ceda`, 2 Aug.** Fixed as planned below, plus one adjacent defect
found while writing the diff: the phone branch had only hard flags where the name
branch had a prompt fallback, and `v3_awaiting_phone_confirm` is set in exactly
one place (connection.py:5292, the reschedule/cancel DTMF path). On an ordinary
booking the phone step therefore **never** resolved to `"phone"` — so the phone
re-ask wording at connection.py:13535 was unreachable on the booking path. Both
had to move together: reordering alone would have demoted the phone step from
`"name"` to `"conversation"`, trading a wrong label for a worse one.
Test: `tests/regression/test_capture_phase_follows_the_question.py`, 20 cases;
11 verified failing on the parent commit, and the 9 that pass there are exactly
the invariants the fix preserves.

**Behaviour change to watch on the next sweep:** dead air at the phone step now
answers *"is the number you're calling on the best one to reach you? Just say use
this number"* instead of asking for the name again. That branch is pre-existing
and was written for this case; this is the first build on which it can fire.

Original analysis follows.

**Root cause: a sticky flag outranked the live question.**

Not one of the three call sites — the resolver itself.
[`capture_phase()`](../../app/media_streams/latency_timing.py) (latency_timing.py:74-100)
tests in this order: phone flags → `v3_awaiting_surname` → prompt keywords. The
middle test is the defect, because that flag is never cleared by anything outside
name capture.

`v3_awaiting_surname` has **exactly three assignment sites**, all inside
`_v3_try_capture_name` in connection.py — `:1826` and `:1881` set it `False`,
`:1887` sets it `True`. Both `False` sites require a surname to have actually
been found. The code says so itself at connection.py:1790: *"v3_awaiting_surname
is sticky: nothing clears it when the conversation moves on."*

So a caller who gives a first name only leaves the flag `True` **for the rest of
the call**, and `capture_phase()` answers `"name"` on every subsequent turn —
the slot choice, the phone step, the booking confirmation, the closing. The live
question is ignored in favour of a stale flag.

Three consumers, three very different costs:

| # | Consumer | Cost | Live today? |
|---|---|---|---|
| 1 | Dead-air re-ask, [connection.py:13500](../../app/media_streams/connection.py) | **Caller-audible.** `_cap_phase == "name"` selects *"Sorry — could I take your first name and surname again?"* So a caller who goes quiet at the **booking-confirm** step is asked for their name again | **YES** — the site comments *"pure lookup, independent of LATENCY_TIMING"* |
| 2 | `[LAT]` / `[LAT-EP]` lines (latency_timing.py:192; `_ep_prev_phase` at connection.py:6852 carries it into `emit_cutoff`) | Every turn after the stick is bucketed `name`. Phone-capture turns recorded as name turns; any per-phase latency or cutoff analysis is wrong for the whole tail of the call | No — `LATENCY_TIMING` defaults `false` |
| 3 | `_ws_c_apply_endpoint_profile`, [connection.py:12937](../../app/media_streams/connection.py) | Early-returns when the phase is unchanged. Stuck at `name`, the **phone** profile is never pushed and the **conversation** profile is never restored — flatly contradicting its own docstring, *"Leaving capture restores the conversation profile"* | No — `WS_C_SEMANTIC_ENDPOINT` defaults `false` |

> **Consumer 1 probably explains some of `B-08`** ("asks for information already
> given"). Investigate `B-08` with this in mind before treating it as a prompt
> problem — a dead-air re-ask that asks for the name again is exactly that
> symptom, and it is deterministic, not model behaviour.

**Fix plan** (~30 min + test):

1. Reorder `capture_phase()` so the **live question** is judged before the sticky
   flag: prompt keywords first, `v3_awaiting_surname` only as a tiebreaker when
   the prompt says nothing either way. Phone flags stay first — they are set and
   cleared tightly.
2. **Do NOT clear `v3_awaiting_surname` instead.** Its stickiness is load-bearing:
   branch 3 of `_v3_backfill_surname` depends on it to accept a bare straggler
   word as the surname. Clearing it early re-breaks surname capture — the exact
   defect `96417a9` and `aa0b3bd` were fixed to close.
3. Test: parametrised sessions asserting a stuck `v3_awaiting_surname` no longer
   turns a phone-step or booking-confirm turn into `"name"`, plus the
   false-negative half — a genuine name turn must still resolve to `"name"` when
   the prompt is the name question.
4. `capture_phase()` is a pure function with three consumers and no other
   callers, so blast radius is exactly the table above.

**Verify before writing the diff:** that reordering cannot lose a real name turn
whose `last_bot_prompt` has already moved on. That is the case the sticky flag
was presumably added for, and it is the one thing the fix could regress.

#### `B-17` · booking SMS reports success it never checked
**ANCHORED 2 Aug — and larger than "a no-op log".**

[`booking_sms.py`](../../app/notifications/booking_sms.py) calls `send_sms` at
**eleven** sites across nine functions — lines 103, 174, 183, 191, 244, 290, 338,
369, 395, 427, 455. **Not one captures the return value.** Every path then logs a
success line and `return True`.

`send_sms` returns `None` in three distinct cases: the kill switch is off
([sms.py:76](../../app/notifications/sms.py)), the number fails E.164 validation,
or Twilio raises. All three are reported as sent.

The correct pattern already exists in this codebase, in the neighbouring module —
[owner_alert.py:120-128](../../app/notifications/owner_alert.py) captures the sid,
logs `"send returned no SID — not sent"`, and returns `False`. `booking_sms` is
the copy that drifted.

**Severity is branch-dependent, and that is the point:**

- **On `latency-eval`:** log-only. `SMS_ENABLED` defaults `false`, so the line is
  the known "SMS is lying on every call" noted in `CALL_SUITE_2026-08-02.md` §0.1.
  The return is consumed by nothing on the call path — only a debug route
  ([twilio.py:1312](../../app/routes/twilio.py)).
- **On `jv-v1-onboarding` and `vitaledge-onboarding`, where `SMS_ENABLED`
  defaults `true`:** a genuinely failed booking confirmation — bad number, Twilio
  outage — is logged as sent and returns `True`. A patient silently gets no
  confirmation and nothing anywhere says so. That is **FM-15 territory**, not P3.

**Fix plan** (~45 min + test):

1. Capture the sid at all eleven sites; mirror `owner_alert` exactly rather than
   inventing a second shape.
2. A function with multiple sends (`send_24hr_reminder`, sites 174/183/191) must
   decide what a partial success returns. Suggest: the primary send governs the
   return, extras are logged individually — but **make it explicit**, because
   today it is an accident.
3. Test: `send_sms` patched to return `None`, asserting every public function
   returns `False` and logs no success line; and the mirror case with a sid.
4. **Canonical-first applies and matters here.** Fix lands on `latency-eval`,
   then cherry-picks to both onboarding branches — which is where it actually
   pays, since this branch cannot send an SMS at all.

---

## Deferred — the SMS family, `B-17` and `B-22`

**Owner decision, 2 Aug: all SMS work goes to the end of the queue.**

The reasoning is sound and worth writing down so it is not relitigated. SMS
cannot fire on this branch at all (`SMS_ENABLED` defaults `false` and must stay
that way here), so nothing in this family is provable by any call we can place
before the demo. Every hour spent on it is an hour of unverifiable work while
`B-09` silently books wrong days and the by-ear backlog grows.

**What deferring costs, stated plainly** so the decision can be revisited with
open eyes: on the two live clinic branches, a booking SMS that genuinely fails
stays invisible for as long as this sits in the queue. That is a real patient
getting no confirmation with nothing in the record to say so. It is the right
trade against a demo three days out, and it stops being the right trade the
moment the demo is behind us.

**Do them as one batch when they come up.** `B-17` and `B-22` are the same
subsystem, they want the same test scaffolding, and they cherry-pick to the same
two branches in the same operation.

### `B-17` · booking SMS reports success it never checked
Full analysis above. Anchored, plan written, ~45 min. **Deferred.**

### `B-22` · Susie promises a text before anyone knows it was sent — NEW
**Lead, opened 2 Aug while scoping `B-17`. Deferred with it.**

The closing line is built from the `SMS_ENABLED` **env var at prompt-construction
time**, not from the send's outcome —
[clinic_template_prompt.py:1710](../../app/prompts/clinic_template_prompt.py):
`_text_promise = " I've just sent you a confirmation text." if _sms_on else ""`
(and `:2133` for the reschedule closing).

So on a live branch with the flag on, Susie tells the caller the text is sent
before the send has been attempted, let alone confirmed. **`B-17` does not fix
this** — it makes the failure visible in logs; this one is what the caller hears.
Of the two, this is the one with a caller-facing consequence, and it needs a
different shape of fix: defer the closing, or condition it on the write result.

Harmless on `latency-eval` (flag off ⇒ the promise is never spoken). Matters only
where SMS is live, which is the same place `B-17` matters — hence one batch.

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
- ~~`B-15` and `B-17` have no file:line anchor~~ — **both anchored 2 Aug**, and
  both turned out to be different from their one-line descriptions. `B-15` is not
  a mislabel at any of the three call sites but a resolver that lets a sticky flag
  outrank the live question, with one caller-audible consequence. `B-17` is not a
  no-op log but eleven unchecked return values that, on the two live clinic
  branches, report a failed booking SMS as sent.
  **Worth noting as a pattern:** a one-line defect description carried across
  sessions had, in both cases, the wrong scope. Anchor before scheduling.

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
