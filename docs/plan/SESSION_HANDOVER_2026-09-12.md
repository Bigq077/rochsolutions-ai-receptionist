# Session handover — Saturday 12 September 2026

Read this and you are where the previous chat was. Written at ~14:00 BST.

**Worktree** `AppData/Local/Temp/claude/slotspec`, branch
`feat/slot-spec-and-verifier`, clean, at `b20c029b`.
**Frozen baseline** `AppData/Local/Temp/claude/promobase`, detached at
`2658f727`, with its own `.env` — this is the "before" tree for a failing-set
diff. Do not touch it.

---

## 1. Where the branches are

| | SHA | serves |
|---|---|---|
| `origin/production` | **`dfaa0b02`** | the three patient lines — Vital Edge, JV, Theorem |
| `origin/latency-eval` | **`b20c029b`** | the demo line only, +447366263180 (`northgate`) |

`production` is an ancestor of `latency-eval`; promotion is a fast-forward.

```bash
git push origin latency-eval:production          # promote
git push --force-with-lease origin dfaa0b02:production   # roll back
```

**Six commits are waiting to be promoted** (`production..latency-eval`):

```
b20c029b fix(slots): the read-back names the slot the caller accepted (D-s; DT-18)
e161c435 feat(slots): a named time gets ONE slot, on every path (D-r; DT-7/8)
81a8c81e fix(tests): the alert-router 'disabled' test read the ambient .env
fdc82e85 feat(deploy): the boot banner states the OBS posture (Gate 2)
0a19c4f2 docs(plan): the invented-symptoms defect was fixed on 9 Sep
7c4d950f docs(plan): three owner decisions — N5, surname gate, hold speech
```

---

## 2. What happened today, in order

### 03:00 — promoted `2658f727` → `dfaa0b02`
27 commits: the slot spec + scorer, the slot-fact guard, the REPEAT /
time-request / clarify / band producers, D-q, N6, the phone-"yes" detector,
the B-151 watchdog fix, prompt Steps 5–7. Verified afterwards by a whole-suite
failing-set diff against `2658f727`: **zero new failures, one fixed.**

### The open-items audit
Everything dated in `docs/plan/` checked against the tree. Result: almost
nothing on the lists was actually open. One correction to my own audit —
**the invented-symptoms defect was already fixed** on 9 Sep (`8e838f0f`,
"WHOSE SYMPTOM IS IT"), tested, promoted and call-verified; the doc simply
carried no status line. Header added (`0a19c4f2`).

### Three owner decisions (`7c4d950f`, `OWNER_DECISIONS_2026-09-12.md`)
* **DEC-1 — no N5 third stall rung.** Branch `feat/n5-third-rung` deleted.
  The corpus argued against it twice; the real lead is the ladder NOT arming
  on 14–21 s silent turns with a fast first token.
* **DEC-2 — the surname write gate stays warn-only** until
  `[book] SURNAME NOT READ BACK` measures under ~5 %.
* **DEC-3 — hold speech goes to a practitioner.**
  `HOLD_SPEECH_REVIEW_PACK_2026-09-12.md`, rendered from the code, plus a
  published page: https://claude.ai/code/artifact/9ee279b1-bc88-41dc-b8f7-41b88648da60

Also deleted as superseded: `fix/asap-sparse-rota`,
`feat/one-presentation-layer`, `feat/stage-b-lead-in`.

### Gate 2 (`fdc82e85`, `81a8c81e`)
The `[deploy]` banner now prints each service's OBS posture at boot —
`OBS_CAPTURE_ENABLED`, `OBS_JUDGE_ENABLED`, `OBS_ALERTS_ENABLED`,
`OBS_DIGEST_ENABLED`, and presence-only for `OBS_DATABASE_URL`,
`OBS_ALERT_SMS_TO`, `OBS_DIGEST_EMAIL_TO`. Also fixed a standing failure on
Gate 2's own path: `test_dispatch_disabled_sends_nothing` was asserting
against the ambient `.env` rather than the state under test.

### Three live calls, and what they found

| call | line | build | verdict |
|---|---|---|---|
| `CAf87ed571` 12:11 | **Theorem** | `dfaa0b02` | tenant verified. Producers 123–127 ms, guard announcing `mode=log`, **OBS capture + judge ON**. Found: "anything around 12" went to the model because 12:00 had already been read → **D-r** |
| `CA4c197e00` 12:41 | demo | `e161c435` | **D-r proved live**: *"The nearest I've got to midday is ten past twelve…"*, 132 ms, guard `enforce`, no Gate 5 warning at the confirmation |
| `CAea24df48` 12:52 | **Vital Edge** | `dfaa0b02` | tenant verified (diary reader named 8 busy blocks, producers 114–121 ms, **OBS ON**). Found the worst defect of the day → **D-s** |

### D-r (`e161c435`) — a named time is ONE slot, on every path
Owner's call, after hearing the model give one slot on Theorem and preferring
it to the demo line's three-slot D8 readout. Three changes:
1. the resolver searches the day's **bookable** times, heard or not
   (`bookable_on_current_day_anchored`), above the exhaustion gate;
2. the **tool path** builds a one-slot offer (`build_named_time_offer`,
   `_named_time_offer`) instead of pinning the asked time third in a readout;
3. `resolve_requested_time(far=True)` — the nearest whatever the distance for
   a round time, on a day the caller **chose**; ties decline, non-round times
   never drift, and a day that merely leads a menu is not a choice.

### D-s (`b20c029b`) — the read-back names what the caller accepted
**The most serious finding of the day, and it was live on all three patient
lines.** On Vital Edge the caller accepted **midday**; three turns later the
confirmation said **"five in the evening"**. 17:00 was a real Monday slot
spoken two turns earlier, so **Gate 5 passed it** (the time was in an offer)
and **the slot-fact guard passed it** (the time is in the diary — invariant 4,
not invariant 1). Two guards passing a wrong-time confirmation is not a guard
failure: invariant 2 had no enforcement at all.

Three holes, all closed:
1. the accept reader read an existence QUESTION as a pick —
   `_ASKS_IF_A_TIME_EXISTS_RE`, deliberately narrower than `_DAY_REQUEST_RE`
   ("can I have midday" is an acceptance);
2. `ACCEPTED_SLOT_KEY` is per-TURN (P6b) — `ACCEPTED_SLOT_RECORD_KEY` pins it
   for the CALL, phrase built from the payload's own label, cleared on a
   landed booking;
3. neither speech-derived phrase key was set, so the read-back prompt asked
   the model to fill the time in from memory — `readback_slot_phrase` now
   feeds it from the engine.

**Evidence:** scorer **109 pass / 0 FAIL / 38 unreachable** (new rows DT-7b,
DT-8b/c/d, DT-18, DT-18b); `tests/regression` **8,895 pass** with the same
**5 standing failures** (`b84` ×3, the multi-day count, scarcity multiple-days).

---

## 3. What to do next

### Immediately
1. **One demo call on `b20c029b`** — the gate for promoting:
   "book an appointment" → "ankle" → "anytime next week" →
   "tell me about Monday" → **"do you have anything around midday"** →
   "yes" → give a name → confirm the number.
   **Watch the final read-back names MIDDAY**, not another time.
2. **Then promote.** Out of hours, revert target `dfaa0b02` in hand:
   ```bash
   git push origin latency-eval:production
   ```
   Confirm `[build_info] running build b20c029b` in each clinic service's log.
   The promoted build also prints `[deploy] obs: …` — that is the Gate 2 read.
3. **`SLOT_FACT_GUARD=enforce` per clinic service**, only after that service's
   own clean call. Demo has been on `enforce` since 11 Sep; the three clinic
   services are at the `log` default.

### One decision waiting on the owner
**"what about midday", with midday on the offer, still reads as CHOOSING it.**
That is the 11 Sep rule (a day plus an *explicit time* names its slot; a day
plus a *band* does not) applied consistently, and D-s means the read-back no
longer drifts — but a caller who was only asking is moved a step. Flagged in
the code and the spec, deliberately not decided.

### Substantially open, ranked
1. **Latency — the biggest gap against the readiness bars.** p95 caller-
   perceived 8.28 s against a published 1.5 s; 86 % of turns over it; 18.7 %
   with >3 s of silence. Seen again today: the week-lookup turn was 7.1 s to
   content with 4.1 s of silence after the filler, while every producer turn
   on the same call was 131–135 ms. Ranked plan in
   `LATENCY_DISTRIBUTION_2026-09-10.md` §8 — items 1 (ladder arming), 2
   (`chunk_gate`) and 4 (`llm_ttft`) untouched.
2. **Readiness gates 1, 2 and 5 have no recorded pass.** Gate 2's capture leg
   is now proven on Theorem and Vital Edge (both log `[obs.store] captured`
   and `judged`); the **alerts** leg is unproven on any service and the
   **digest worker is off everywhere**. Gate 1 needs its own test: a
   deliberately broken Acuity credential producing an honest caller outcome
   plus an operator alert, demonstrated on a live call.
3. **Small and anchored:** Stage C's `_record_stood_down_slots`
   (`llm_stream.py:3717`) fails silently and needs its own line;
   `presented_days` is never recorded in `single_day` mode; nothing per turn
   records whether a tool ran, so the tool-vs-plain latency split cannot be
   measured; the multi-day lead-in (~1.8 s); `UNKNOWN_SLOW` apologises on a
   turn that answered.
4. **Parser leads, none reproduced on a call:** "half three" reads as
   nothing; `requested_clock_times("at ten to twelve")` also emits 10:00;
   "from nine to five" → 04:51; a bare "8 in the morning" is unread.
5. **Invariants 16 and 20** — 13 % of recorded offers were never spoken; four
   availability readers, five refusal branches (the migration, Stage D, never
   started).
6. **Housekeeping:** `docs/plan/README.md` is stale (ADR-002 still says
   "inert", the "this week" table is from August, corrections stop at 31).
   The main worktree sits on `vitaledge-onboarding` (legacy) with ~45
   untracked docs already tracked on `latency-eval`, plus an older
   `sms_guard.py` draft that should be deleted.
   `fix/p8-availability-cause` carries one test not on `latency-eval`
   (`test_a_closed_day_is_not_called_too_soon.py`).

---

## 4. Rules that cost time when forgotten

* **The test suite is meant to be red.** 5 standing failures in
  `tests/regression`, ~98 across `tests/`. Never look for green — diff the
  failing SET against `promobase`.
* **`[build_info] running build <sha>`** in the Render log is the only proof
  of what is deployed. `/health` returns a hardcoded 1.0.0.
* **Never write a regex through a bash heredoc** — it mangles the escapes.
  Use `Edit`/`Write`. (Cost time again today.)
* **A demo call validates the ENGINE, not the tenants.** `northgate` proxies
  `jv_v1`; `theorem_v3` and `vital_edge` are not covered by it.
* **The three clinic services send real SMS.** Abandon a test call before the
  phone step. The demo service has `SMS_ENABLED` off.
* **Never propose fixing Google Sheets** — OBS is a strict superset and the
  owner has ruled on it.
* **Before calling any dated defect doc "open", grep the tree for its fix.**
  Diagnosis docs outlive their fixes here; that happened twice today.
* `SLOT_PRESENTATION_SPEC.md` is the authority for slot behaviour. A change
  must name a row (DT-nn) or an invariant. Owner decisions are D-a … **D-s**
  in §3.
