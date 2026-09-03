# Four-branch discrepancy audit — 25 Aug 2026

Content-based audit of `latency-eval`, `theorem-onboarding`, `vitaledge-onboarding`
and `jv_v2`. Written to be read cold.

## Heads audited (origin, fetched 25 Aug)

| Branch | Head | Serves |
|---|---|---|
| `latency-eval` | `cce7ba1f` | test line +447366263180 (loads `jv_v1`) |
| `theorem-onboarding` | `8486c942` | Mark — +447380841468 (`theorem_v3`) |
| `vitaledge-onboarding` | `4c0bc322` | Jonathan — +447426779875 (`vital_edge`) |
| `jv_v2` | `e7177b2a` | Marcus — +447367002651 (`jv_v1`) |

The local worktree is on `vitaledge-onboarding` at `f46dd24c`, stale vs origin.
Everything below is measured against `origin/*`.

## Method

`git log` and `git cherry` cannot answer this — the branches are joined by
cherry-picks, so patch-ids differ and rev-lists lie. Instead, for every commit on
one branch and not the other, the distinctive added lines (>=30 chars, non-comment,
non-boilerplate) were tested for literal presence in the target branch's `app/`
tree. PORTED = >=90% of lines present, PARTIAL = 15-90%, MISSING = <15%.
Every finding below was then read in source and confirmed by hand.

| Direction | Ported | Partial | Missing |
|---|---|---|---|
| `latency-eval` -> `theorem-onboarding` | 115 | 18 | 36 |
| `latency-eval` -> `vitaledge-onboarding` | 133 | 16 | 22 |
| `latency-eval` -> `jv_v2` | 87 | 11 | 13 |

## CORRECTIONS — applied 25 Aug, after implementation

Five findings below are WRONG and have been withdrawn. All were produced the
same way: the line-presence audit matched each commit's ORIGINAL added lines,
and in each case those literals were superseded by later retuning that landed on
every branch. Anchor on the current symbol, never on a commit's literals.

| Withdrawn | Verified by |
|---|---|
| §3.1 play-duration clamp | `connection.py` lines 80–215 are byte-identical on all four branches; `_clamp_play_secs` present everywhere. Constants were retuned 28.0/6.0/5.0 → 34.0/10.0/4.0. **There is no dead-air P1.** |
| §3.2 write-CTA slot clear | `_write_cta_outstanding` defined once, called twice, on each branch |
| §3.3 dtmf telemetry | `dtmf_slot_no_mapping` present on all four |
| §3.4 keypad wording | present at equal counts on all four |
| §4.2 caller-ID read-back | present on all four via `28c40ed2`; the Theorem commit is a native rephrasing |

§6's two open questions are closed by evidence: Mark has `deposit_required:
False`, so the deposit commit is a true N/A; and `jv_v1/clinic.json` carries
`dial_phone: +447586605462`, so Marcus has a bypass target. **§1.4 is a real gap
after all** — Mark's minimum age is 7, not "no policy".

Three findings the audit MISSED, found during implementation and now fixed on
`latency-eval`:

* **`_reason_already_known` tests `soft.get("reason")`, a key nothing writes.**
  The extractor writes `condition_notes`. A caller who opens with their
  complaint is asked for it again. Clinic-gated to `prompt_facts.reason_question`
  — so `jv_v2` and `vitaledge-onboarding`, N/A for Theorem.
* **Canonical pointed Vital Edge at a diary reader it did not have.** VE's
  `clinic.json` on `latency-eval` says `availability_mode: "diary"` and two
  comments name `_check_availability_diary`, but the function and the dispatch
  were both absent — so a provisional clinic fell through to the published
  reader, the Ibiza-slot bug. Worse than "stranded"; fixed in `f51d21e3`.
* **Mark's minimum age had FIVE sources, not four** — the fifth being
  `faq.children_policy`, the answer a caller gets when they ask outright.

## Answer to the question

**No, it is not just clinic-facing.** Clinic-facing divergence is real and mostly
correct. But there are four *engine* subsystems missing from live branches, one
safety layer that only ever ran for one clinic on any branch, a class of fixes
that ported half-way, and three defects that are live on all four branches at
once.

---

# 1. Engine features absent from every live branch

These exist on `latency-eval`, are unconditionally live there (no env flag), and
are absent from all three clinic branches.

### 1.1 The hold-speech arbiter — `app/hold_speech.py` (393 lines)

The file exists only on `latency-eval`, where four call sites in `connection.py`
and `llm_stream.py` import it. It replaced six independent filler producers that
shared no state. Its own corpus measurement (323 stored calls, 25 Jul-21 Aug):
354 hold phrases across 98 calls, one call with 17; **175 of 322 were followed by
a question rather than looked-up data** — the phrase promised work that never
happened; 32 were dead-ends.

The three live clinics still run the six-producer behaviour. Supporting deltas
that travel with it and are equally absent: `filler_phrases.note_filler_played`
has no `text=` parameter (so no duplicate-opener stripping, no `join_after_head`),
`filler_guard` does not register the clip's wording, `config.py` still carries the
1800 ms ack threshold rationale rather than the 3000 ms one, and the template
prompt lacks the NEVER OPEN A REPLY WITH A HOLDING PHRASE block (`fc583462`).

The feature is coherent — it is missing as a unit, not half-wired. But note
`note_filler_played`'s signature differs, so any partial cherry-pick breaks.

### 1.2 Prompt-cache and per-turn latency accounting

`latency_timing.py` on live branches has no `cache_read_tokens` /
`cache_write_tokens` fields and no `no_content` outcome; `call_logger.py` does not
persist `lat_tools`. So the question "was that hold phrase justified?" cannot be
answered from stored data on any live clinic — only on the eval line
(`858ee459`, `89866132`).

### 1.3 `last_audio_sent_at` debug field — `routes/admin.py`

Present on `latency-eval` only. The automated call-test harness polls it to know
when Susie has stopped speaking; without it the harness sleeps a flat 25 s, Susie
answers in 3-6 s, and her own 10 s watchdog fires — every scenario turn earns a
spurious "Sorry, I didn't quite catch that". **The automated suite cannot
correctly exercise any live branch.**

### 1.4 The age gate is missing from `theorem-onboarding`'s engine entirely

`minimum_age_years` is read by four engine files on `latency-eval`, `jv_v2` and
`vitaledge-onboarding`, and by **zero** files on `theorem-onboarding`.

**CORRECTED 25 Aug: not inert.** This section originally said Mark has no age
policy. He has one — patients aged 7 and over — so the gate is missing from the
clinic that needs it. It was stated five different ways (7 / 15 / 15 / 18 / 15)
and the only one a caller ever heard, the prompt line "Children under fifteen
not seen", was wrong: Susie was turning away the 7-14 year olds the clinic sees.
Settled at 7 and fixed on canonical (`cbdf37e2`, `07227720`); the engine port to
`theorem-onboarding` is Wave 2.

That branch also ships `vital_edge/clinic.json` carrying `"minimum_age_years":
18` as dead config, and any clinic cut from it inherits no enforcement.

---

# 2. Clinical screening — the largest single gap

### 2.1 It runs for one clinic, on every branch

`screening_enabled()` returns true only when the clinic contract carries a
`clinical_screening` block. Across all four branches that block exists in exactly
one file: `app/clinics/jv_v1/clinic.json`.

* `vital_edge/clinic.json` — no block.
* `theorem` — `get_clinic("theorem_v3")` returns `CLINICS["theorem_v3"]` from
  `clinic_config.py`, which has no `clinical_screening` key on any branch
  (grep count: 0).

So for Mark and for Jonathan the deterministic Layer-1 red-flag path is a no-op,
**including `detect_emergency`**, which is config-driven from the same block. All
clinical safety on those two lines rests on the prompt and the model.

Whether those clinics *should* screen is an owner call, not an engineering one —
but right now the answer is "they do not", and nothing in the code says that was
decided rather than inherited.

### 2.2 Theorem and Vital Edge are 309 lines behind on the screening engine

`clinical_screening.py`: `latency-eval` and `jv_v2` are **byte-identical at 1426
lines**; `theorem-onboarding` and `vitaledge-onboarding` are **byte-identical at
1117 lines**. Missing from the latter pair:

* the `hedged` verdict, `_UNSURE_PHRASES`, `_HEDGE_LEAD`, `_HEDGE_PHRASES` — so
  "a bit", "maybe", "I don't know" return `unclear`, which leaves the screen
  pending and hands a safety decision to the LLM (`2af9e347`, `6757fa6b`);
* `screen_probe_question` — no single clarifying probe after a hedge;
* `SCREEN_REASKS_KEY` / `screen_reask_question` — **a pending safety screen is
  never re-asked and can sit unanswered for the rest of the call** (`e595df59`);
* `_decisive_red_flag` / `decisive_red_flags` — an unprompted description needs
  two independent keyword hits to escalate, with no single-keyword override;
* the trigger-shape fix for injuries described in ordinary words (`59fe74d2`);
* the "screen must not gate on its own answer" fix (`168e0d21`);
* the inverted fracture screen fix (`becd7f84`) and the wording de-priming
  (`69a68a54`, `0bce1c45`);
* the matching AssemblyAI keyterm boosts in `stt_stream.py` for the new lay
  phrasings.

Because 2.1 makes the layer inert on those two clinics, this is currently latent
— but it means **enabling screening for Vital Edge or Theorem by adding config
alone would arm a known-defective version of the layer**, including the screen
that was live-inverted on the JV patient line until it was ported. The engine must
be ported first, config second, in that order.

`jv_v2` is fully current on screening.

---

# 3. Half-ported fixes — the dangerous class

These read as done in the log and are not.

### 3.1 The 19-seconds-of-silence watchdog fix is half-ported to all three

`80b9e9cc` and `4e40dd82` were meant to stop the TTS byte counter producing an
impossible play duration that arms the silence watchdog far too late. All three
live branches took the surrounding change and **not the clamp**:

```
MISSING on theorem-onboarding, vitaledge-onboarding, jv_v2:
  _MAX_CHUNK_PLAY_SECS: float = 28.0
  _MIN_SPEECH_CHARS_PER_SEC: float = 6.0
  _PLAY_SECS_HEADROOM: float = 5.0
  max_plausible = len(text) / _MIN_SPEECH_CHARS_PER_SEC + _PLAY_SECS_HEADROOM
  "[ms_silence] IMPOSSIBLE play duration %.1fs for %d chars ..."
```

A 49-character phrase (~3 s of speech) can still be scheduled as ~19 s of playout
on every live clinic, with no watchdog armed behind it. **This is dead air on a
live call and it is a P1.**

### 3.2 Write-CTA slot-window clear — missing on all three (`3c40057a`)

`_write_cta_outstanding` and the `v3_awaiting_slot_selection` clear are absent, as
is the "Still with you — shall I go ahead?" phrase. The slot window is not
cleared when the write CTA is spoken.

### 3.3 `dtmf_slot_no_mapping` telemetry — missing on all three (`e781ac44`)

The rearm fix is present; `self._note_utterance_lost("dtmf_slot_no_mapping", digit)`
is not. Observability only — the call no longer strands.

### 3.4 Reschedule keypad wording drifts on `vitaledge-onboarding` and `jv_v2`

Two literal lines of `28c40ed2` are missing — the exact "go ahead and type the
number on your keypad / press the star key to reset" sentence. Cosmetic *unless*
any gate matches that literal; nothing currently does, but this repo has been
bitten three times by code matching one literal of model speech.

---

# 4. Stranded on clinic branches — canonical-first violations

Work that exists only on a live branch and has never reached `latency-eval`.
CLAUDE.md's rule is that this strands safety fixes at convergence.

### 4.1 `theorem-onboarding` — Acuity book-failure escalation (`86d3458a`)

`_alert_owner_acuity_book_failed` in `receptionist_tools.py` exists **only** on
`theorem-onboarding`. Canonical has no owner alert on the Acuity write-failure
path: the `manual_followup` alert lives only in the Google-Calendar branch, and
Theorem short-circuits past it. Inert on the other three (they do not run Acuity),
but any future Acuity clinic cut from canonical ships the original bug — a caller
tries to book, fails, and nobody is told while Susie is still on the line.

`owner_alert.py`'s docstring on `latency-eval` still reads "Clinics without the
block (e.g. Theorem)" — stale on canonical, corrected only on Theorem.

### 4.2 `theorem-onboarding` — ~124 lines of clinic config in `clinic_config.py`

`clinic_config.py` is 1681 lines on Theorem and 1557 on the other three. The
Redditch guard, Leanne's Thursday-evening rota, canonical prices and the location
ladder live only there. Deliberate as clinic data — but it is *code*, in a shared
engine file, and canonical has no equivalent.

Also stranded: the caller-ID number read-back (`76cef3d1`, Theorem prompt only —
zero equivalent on the other three), the primary-site location default
(`a233a5c2`), joint injections, and the model-driven reschedule flow.

### 4.3 `vitaledge-onboarding` — the diary reader (`ddd53185`, `3f286212`)

The fix for "the calendar held his BOOKED work and every event was offered as
free", plus naming every diary entry that removes a slot. ~447 lines of
`receptionist_tools.py` divergence. Not on canonical, not on `jv_v2`.

`jv_v2` also reads a Google calendar and had a **real double-booking on 11 Aug**
because `freebusy()` queries a single calendar id. Whether the diary reader would
have helped there is worth deciding deliberately rather than by omission.

### 4.4 `jv_v2` — nothing of concern

Only `74942ce3` (branch cut) and `677883da` (the Carepatron `calendar_id`
repoint). The latter must **never** go to canonical — canonical deliberately
points at the demo calendar.

---

# 5. Defects live on all four branches

Found while cross-referencing; not discrepancies, but they surfaced here.

### 5.1 Nine of the ten SMS senders return `True` for a text that was never sent

`booking_sms.py`, identical on all four branches:

| Sender | Returns | Called? |
|---|---|---|
| `send_reschedule_confirmation` | `bool(_sid)` — correct | **no caller anywhere** |
| `send_booking_confirmation` | `True` — wrong (log made honest, return left) | yes |
| `send_cancellation_confirmation` | `True` — wrong (log not fixed either) | yes |
| the other seven | `True` — wrong | yes |

`send_sms` returns the Twilio SID, or `None` for a **suppressed** send
(`SMS_ENABLED` off) as well as a failed one. Callers latch
`session["confirmation_sms_sent"] = True` on that return, and
`smart_sms_router.py:305` stands down on the latch. So on a suppressed or failed
send the caller gets **no confirmation and no follow-up**, and the log reads
healthy.

`c4b5b0c5` fixed exactly this — in the one function that has no callers. Theorem
has that commit; Vital Edge and `jv_v2` do not. Since the function is dead, the
port gap is harmless and the *fix* is what is misplaced.

### 5.2 The SMS default is split against itself on all three live branches

```
app/notifications/sms.py               SMS_ENABLED default "true"   <- the sender
app/prompts/clinic_template_prompt.py  SMS_ENABLED default "false"  <- the prompt
```

With `SMS_ENABLED` unset in a Render service, **the text is sent while Susie tells
the caller it will not be**. Set it explicitly on every service; do not rely on
the default. (Theorem's prompt is `theorem_v3` and gates on nothing at all here.)

### 5.3 Seven dependencies are still unpinned, identically on all four

`openai>=1.50.0`, `websockets>=13.0`, `sentry-sdk[fastapi]>=2.0.0`,
`audioop-lts>=0.2.1`, `rapidfuzz>=3.0.0`, `pytest>=8.0.0`, `pytest-asyncio>=0.23.0`.
A `>=` on `anthropic` already took all four clinics down once. A revert does not
fix it; only a pin does.

---

# 6. Divergence that is correct — do not "fix" these

* `SMS_ENABLED` defaults `false` on `latency-eval`, `true` on the three live
  branches. Deliberate, documented in-code.
* `APPOINTMENT_REMINDERS_ENABLED` defaults `false` on `latency-eval`, `true` on
  the three live branches. Deliberate. Note `.env.example` on canonical still
  says reminders are off "by owner decision" and to leave the var unset — stale
  guidance for anyone cutting a new clinic branch.
* `jv_v1/clinic.json` `calendar_id` differs on `jv_v2` (see 4.4).
* Theorem's whole prompt lineage (`susie_system_prompt._build_theorem_v3`) vs the
  template clinics' `clinic_template_prompt`. Most template-prompt commits missing
  from Theorem (home visits, FAQ length, AI disclosure, reason-once, condition-led
  opening) have Theorem-native equivalents already; **`deposit` is the one
  template concept with no Theorem counterpart** — confirm Mark has no deposit
  policy and it is a genuine N/A.
* The Acuity commits missing from Vital Edge and `jv_v2` (`e4d07a3b` short-notice
  cancel, `4a337b0f` cancelled-id reporting, `2048582d` failed-cancel duplicate
  alert) are N/A — both are Google-Calendar clinics. Latent, not live.
* `79a2ea06` (provisional owner text marks a failed write) is present on Vital
  Edge and missing on Theorem/`jv_v2` — correct, Vital Edge is the only
  `google_calendar_provisional` clinic.
* `5cba8b95` bypass target: Vital Edge and Theorem each have their own; `jv_v2`
  does not, and has no `test_every_live_clinic_has_a_bypass_target`. Worth
  confirming Marcus has an emergency bypass.

---

# 7. Ranked next moves

1. ~~Port the play-duration clamp~~ — **withdrawn, see CORRECTIONS. It is
   already ported everywhere.**
2. **Decide screening scope for Vital Edge and Theorem.** If yes: port
   `clinical_screening.py` + the `stt_stream.py` keyterms **before** adding any
   `clinical_screening` config, or you arm the inverted fracture screen.
3. **Fix `send_booking_confirmation` / `send_cancellation_confirmation` to return
   `bool(_sid)`** on canonical, then port. Two lines each; closes a silent
   "confirmed but never texted" path on every clinic.
4. **Set `SMS_ENABLED` explicitly on all four Render services.**
5. **Pin the seven remaining dependencies** on canonical, then port.
6. **Back-port the stranded work** (4.1, 4.3) to `latency-eval` so the next
   clinic branch does not re-ship fixed bugs.
7. **Port `hold_speech.py`** as a unit — the biggest quality win available and the
   corpus evidence behind it is strong. It is a 393-line new module plus four call
   sites in `connection.py`; treat it as its own change, not a ride-along.
8. **Port `last_audio_sent_at`** so the automated suite can measure live branches.
9. Confirm `jv_v2` has an emergency bypass target.

---

*Method note: the port-audit script and per-commit line-presence reports are in
the session scratchpad. Re-run against fresh `origin/*` before acting — a
parallel session pushes to these branches.*
