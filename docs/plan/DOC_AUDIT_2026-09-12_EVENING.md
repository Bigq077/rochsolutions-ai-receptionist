# Documentation audit — 12 September 2026, evening

Every dated `.md` in `docs/plan/` touched since 7 Sep, read and then **checked
against the tree**. Nothing here is quoted from a doc's own status line: the
standing rule is that diagnosis docs outlive their fixes, and that has now
happened enough times to be the default assumption rather than a caveat.

**Measured on** `feat/slot-spec-and-verifier` @ `4470555e` (= `origin/latency-eval`),
clean worktree `AppData/Local/Temp/claude/slotspec`.
`origin/production` @ `dfaa0b02`; seven commits await promotion.

**Headline:** of the five items the 12 Sep handover called "small and anchored",
**four are already fixed**. Of the genuinely open work, the largest single item
is latency, and the second largest is invariant 20 — which is no longer abstract
debt, because D-r was caused by it.

---

## A. Genuinely open — each verified against the code

### A1. Latency — the only published bar that is breached, and badly

p95 caller-perceived `content_ttfa` **8.28 s** against a published **1.5 s**;
86 % of turns over the bar; 18.7 % carrying >3 s of silence
(`LATENCY_DISTRIBUTION_2026-09-10.md`). §8 items 1, 2 and 4 untouched.

**New this session — the §6 lead is now a finding.** `LATENCY_DISTRIBUTION` §6
and `OWNER_DECISIONS` DEC-1 both park the arming question as *"a lead, not a
row — three calls, no `file:line`, and the arming path has not been read."*
The path has now been read, and today's call (`CA1ef288f1`, 13:28) is a fourth
instance with a timestamp:

* turn 2 `content_ttfa_ms=10990`, `llm_ttft_ms=8313`;
* the filler head *"Let me see what next week looks like —"* finished at
  13:28:30.116; next audio 13:28:38.0. **7.9 s of dead air.**
* **No `WATCHDOG_START` was logged after that head** — and the same on turn 1.
  Arming is withheld while `_llm_busy` is true and handed to `on_tts_finished()`
  ([connection.py:4899](../../app/media_streams/connection.py:4899),
  `WATCHDOG_DEFERRED_CLEAR reason=tts_still_playing`), so a turn whose only
  audio so far is a **non-question filler head arms nothing at all**.

Consequence: post-filler silence has no upper bound — it is exactly however long
the model takes. This is a bounded fix on a live path, and it is §8 item 1.

> Distinction worth keeping: today's turn had a *slow* first token, where
> `LATENCY_DISTRIBUTION` §5's two exhibits had *fast* ones. The dead air has two
> causes; the **absence of any stall speech during it** is the same defect in
> both.

### A2. Readiness Gate 1 — never demonstrated
A deliberately broken Acuity credential producing, on a live call, an honest
caller outcome + an operator alert + zero false confirmations
(`PRODUCTION_READINESS_PLAN.md`:165). No recorded pass.

### A3. Readiness Gate 2 — one leg of three proven
Capture is proven on Theorem and Vital Edge (both logged `[obs.store] captured`
and `judged` on 12 Sep; today's demo call logged `captured … turns=21`).
**The alerts leg is unproven on any service**, and the digest worker is off
everywhere. Gate 2 also wants p95 within 50 ms of baseline — unmeasured.
The new `[deploy] obs:` banner (`fdc82e85`) makes the posture readable per
service; it has not yet been read on the three clinic services.

### A4. Readiness Gate 5 — rollback never rehearsed
Rollback rehearsed *and timed*, bypass tested on a live number. Neither done.
Today's promotion attempt does not count — the push did not run.

### A5. Invariant 20 / Stage D — never started, and it is now causing defects
`fetch_free_slots` **does not exist anywhere in the repo** (grepped `*.py`,
zero hits). Four availability readers, five refusal branches, two named-day
producers; spec invariant 20 reads *"NOT enforced"*.

This stopped being tidiness on 12 Sep. `SLOT_PRESENTATION_SPEC.md`'s own
correction-log entry for D-r says the Theorem call's defect existed because
**"invariant 20, the tool path was never aligned to DT-7/8"** — one act, two
paths, two answers, and a keypad map that would have booked one o'clock. Every
future divergence of that shape is the same root.

It is also the onboarding blocker: `ONBOARD_A_CLINIC` at cohort scale needs
`fetch_free_slots(window, ids)` plus IDs in `clinic.json`.

### A6. Invariant 16 — 13 % of built offers were never spoken
Instrumented (S-7 `mark_offer_spoken`, `spoken` field on every offer row) and
**never re-measured since 10 Sep**. Anything reading `calls.slot_offers` as
"what the caller heard" is still wrong by that margin until it is.

### A7. Parser leads — four confirmed live, and a fifth found
Run directly against `app.tools.slot_followup.requested_clock_times` at
`4470555e`:

| input | returns | expected |
|---|---|---|
| `"half three"` | `[]` | 15:30 (UK) |
| `"at ten to twelve"` | `['11:50','23:50','10:00','22:00']` | 11:50 only — the extra 10:00 makes the resolver decline as a tie |
| `"from nine to five"` | `['04:51','16:51']` | nonsense |
| `"half two"` | `[]` | 14:30 |
| **`"eight in the morning"`** | **`[]`** | 08:00 — **not in any doc** |

The fifth is worth its own row: **"eight in the morning" is the engine's own
spoken idiom.** A caller who asks for a time using the exact words Susie just
said gets no candidate on the *request* path and falls through to the model.
The *accept* path is unaffected — `slot_accepted_by_caller`
([slot_followup.py:3301](../../app/tools/slot_followup.py:3301)) resolves against
the spoken labels, which is why N4's fix holds — so this only bites on
"have you got eight in the morning on Tuesday?", never on "eight in the morning
works".

None of the five is reproduced on a call.

### A8. DEC-2 — the surname gate measurement
`[book] SURNAME NOT READ BACK` was 34.6 % over 851 calls; the 7 Sep read-back
steer should have cut it; **never re-measured.** Below ~5 % the gate promotes
from warn to block. Needs the obs corpus (see §B).

### A9. DEC-3 — hold-speech pack awaiting a practitioner
`HOLD_SPEECH_REVIEW_PACK_2026-09-12.md` is rendered and published. The ask —
*would your front desk say this?* — is with the owner for Marcus / Jonathan.
Owner action, not engineering.

### A10. Stage C — the Render grep is still owed
`OVERNIGHT_RUNBOOK_2026-09-10.md` §6 item 2: grep all clinic logs for
`could not resolve spoken option(s)`. Not done. Note `STAGE_C_EVIDENCE` §1's
warning still applies — a clean grep is necessary and **not sufficient**,
because site B had no failure line. That half is now fixed (see §C1), so the
gate can finally mean what it says.

### A11. The ~900-line repair layer cannot be retired yet
`STAGE_C_EVIDENCE` §5: site A is only reachable when no deterministic offer was
built, and `slot_offers` only records turns where one **was** built — so the
population that reaches site A leaves no row. That measurement needs its own
instrumentation first. Unchanged.

### A12. `SLOT_FACT_GUARD=enforce` on the three clinic services
Demo has been on `enforce` since 11 Sep (confirmed again today:
`[slot_guard] mode=enforce`). The three clinic services sit at the `log`
default, each pending its own clean call.

### A13. DT-3 / DT-10/11 — the band producers are unproved on a call
Spec §9.1 marks them closed in code with *"Unproved on a call"*. Today's call
exercised the **time-request** path ("anything around midday"), not the **band**
path ("what about monday morning"). Still unproved.

### A14. The promotion itself
Seven commits, `dfaa0b02` → `4470555e`, verified fast-forward. D-s is live on
all three patient lines until it lands.

### A15. `docs/SMS_COST_GUARD_PROMPT.md` exists in no git branch
Of 43 untracked paths in the main worktree, 42 are already on `latency-eval`.
This one is on **no ref anywhere** — it is the brief that
`SMS_COST_GUARD_BLOCKED_2026-09-03.md` argues with. Commit it or discard it
deliberately; right now one `git clean` loses it.

### A16. Multi-tenancy — Phase 4 / 5 not started
Tenant identity is still per-SERVICE (`CLINIC_NAME`, the Acuity calendar IDs,
hardcoded clinic names in `fast_path.py`, `brain.py`, `utils.py`, `acuity.py`).
One service still serves one clinic. This is the cohort-scale blocker and it is
correctly parked behind the readiness gates.

---

## B. One blocker sits behind four of the above

**A read-only `OBS_DATABASE_URL`.** It gates:

* A8 — the surname rate;
* A6 — the unspoken-offer share;
* A1 — the arming anchor across the corpus rather than one call;
* the tool-vs-plain latency split (§C3 — the code is ready, only the data is missing);
* A3 — Gate 2's verification.

`OWNER_DECISIONS` DEC-1 already names this as *"the standing recommendation"*.
It is the highest-leverage non-engineering item on this list: one provisioning
step converts four measurements from blocked to answerable.

---

## C. Claimed open, actually DONE — correct these

Each was listed as outstanding in a doc dated 10–12 Sep and is fixed in the
tree. This is the failure mode `DEFECT_AUDIT_2026-09-07.md` §6 named, recurring.

### C1. `_record_stood_down_slots` "fails silently" — **fixed**
`SESSION_HANDOVER_2026-09-12.md` §3 and `STAGE_C_EVIDENCE` §1 ask for a failure
line at site B. S-6 built it:
[llm_stream.py:3990-4007](../../app/media_streams/llm_stream.py:3990) splits
*nothing parsed* (WARNING) from *already held* (INFO), which is exactly the
distinction the gate needed. **The handover's `llm_stream.py:3717` is also a
stale line number** — the function is at **:3955**.

### C2. `presented_days` "never recorded in single_day mode" — **fixed**
[llm_stream.py:7712-7718](../../app/media_streams/llm_stream.py:7712) falls back
to `[first_day]` when `presented_days` is absent and the offer mode is
`single_day`. The B-95 split is measurable on both modes now.

### C3. "Nothing per turn records whether a tool ran" — **fixed**
S-9 added `tool_calls` to `TurnTiming`
([latency_timing.py:263](../../app/media_streams/latency_timing.py:263)),
stamped at [llm_stream.py:6082](../../app/media_streams/llm_stream.py:6082),
persisted via `as_record()` (:383), and `scripts/latency_percentiles.py` already
prints a **"REAL SPLIT: the turn ran a tool (S-9)"** section alongside the old
proxy. The measurement is fully built; only a corpus with post-10-Sep rows is
missing (§B).

*Residual, genuinely open and trivial:* `tool_calls` is **not** in the printed
`[LAT]` line's format string (:309-317), so it is invisible in the Render log
and readable only from obs. One format-string edit if that is wanted.

### C4. `UNKNOWN_SLOW` "apologises on a turn that answered" — **fixed**
`_reason_answer` at
[hold_speech.py:741](../../app/hold_speech.py:741) exempts a body part named in
answer to the reason question. Confirmed behaviourally on today's call: the
ankle turn got the situational head *"Let's get you booked in —"*, not
*"Still with you —"*.

### C5. `SMS_COST_GUARD_BLOCKED_2026-09-03.md` — **the premise is gone**
The doc's status line reads *"not started. `app/notifications/sms_guard.py` does
not exist"*, with seven-branch search evidence. The guard **shipped the same
day**: `0a2c10b3` (3 Sep) + `25c18f44` (4 Sep). It is wired at the single send
funnel — [sms.py:226-232](../../app/notifications/sms.py:226) imports
`to_gsm7`, `check_budget`, `is_test_number`, `record_fake` — and there is a
local inbox route (`app/routes/dev_sms.py`).

This matters beyond tidiness: `is_test_number()` is the control that stops a
test texting a real handset, which has bitten twice. It exists and is live.

The copy sitting **untracked in the main worktree is an older draft** (196 lines,
no `check_budget`) — that one is safe to delete, as the handover says. The
tracked version is not.

### C6. D7 (demo-service Sheets) — should be closed WON'T FIX, not carried
`GOOGLE_SERVICE_ACCOUNT_JSON` is still malformed — reproduced today at 13:29:59,
`JSONDecodeError('Invalid \escape: line 5 column 46')`. It is carried as open on
three lists.

**Scope, verified:** that variable is read only by `app/tools/handoff.py:80` and
`app/integrations/sheets.py` — i.e. Sheets only. **Google Calendar is
unaffected**: `calendar_google.py` builds from OAuth `stored_tokens`
(`creds_from_stored` → `get_calendar_service`, :230), not from a service account.
Today's Vital Edge and demo availability lookups both succeeded on gcal.

Since Sheets is superseded by OBS by owner ruling, D7 has no consequence except
one WARNING per call. Close it as WON'T FIX and stop re-reading it.

### C7. `THEOREM_ACCEPTANCE_REGISTER.md` — ~12 stale "Status: open" rows
Twelve rows still read `**Status:** open`, all from August calls, none carrying
a re-check. Spot-checked the one marked **HIGH** — T-17, *"a dead guard injected
a synthetic turn on top of a live one"*: **fixed.** The v3 loop now resets
`_turn_speech_emitted` ([connection.py:13297](../../app/media_streams/connection.py:13297),
commented "B2 fix"), the non-FlowEngine path sets it (:13090), and :13904
carries an explicit `T-17 (2026-08-05)` fix comment.

Spec §10 retired all eight `OPEN_DEFECTS_*` registers and the call sheets but
**not** the two clinic acceptance registers — and `docs/plan/README.md` still
lists them under "Clinic work in flight". They read as authoritative and are
not. Either triage them row by row or retire them explicitly.
`VITALEDGE_ACCEPTANCE_SUITE.md` needs the same decision.

### C8. `docs/plan/README.md` is stale at the entry point
* ADR-002 is marked *"Inert until the Render services are repointed"* — they
  were repointed on 2 Sep; `production` has served three clinics for ten days.
* The "This week (Jules / Quentin away)" table is from 11–16 August. Links
  resolve, but the framing is a month old.
* The corrections log stops at 31 (11 Sep) — nothing for D-r, D-s, DEC-1/2/3,
  or the stale-item corrections in this document.

### C9. The scorer's 38 UNREACHABLE rows are not a backlog
Re-ran `scripts/score_slot_spec.py` at `4470555e`: **147 checks, 109 pass,
0 FAIL, 38 unreachable** — matching the handover exactly.

Grouped, **every one of the 38 is an inapplicable diary shape**, not a
dispatcher gap: "needs 2+ times", "needs 4+ times so a readout can drop one",
"the day holds one band only". **None says it needs `handle_transcript`.** The
dispatcher-driving problem that made DT-14/N6 unscorable was closed when the
row was rewired through `try_unspoken_followup_speech`. Worth stating plainly so
the number is not read as 38 unverified behaviours.

---

## D. Hygiene, with numbers

| item | state |
|---|---|
| **Registered worktrees** | **173.** CLAUDE.md says "~15". `git worktree prune --dry-run` finds **none** prunable — every directory still exists, so all 173 are live registrations. This is the measurement hazard CLAUDE.md warns about, an order of magnitude worse than documented. |
| Untracked files in main worktree | 43; **42 already on `latency-eval`** → safe to clean. The 43rd is A15. |
| Main worktree branch | `vitaledge-onboarding` — legacy, stopped 31 Aug. |
| Stale `sms_guard.py` draft | main worktree only, older than the tracked one — delete (§C5). |
| `vital_edge/clinic.json` | `_calendar_id_note` still reads *"TBC: confirm the exact calendar address/ID"* though VE calls resolve the diary correctly (12 Sep, 8 busy blocks named). Doc-only. |
| SMS safety invariants | **hold.** `_SMS_ENABLED_DEFAULT = "false"` (sms.py:36) and `APPOINTMENT_REMINDERS_ENABLED` defaults `"false"` (scheduler.py:68) on canonical, as CLAUDE.md requires. |

---

## E. What this changes about the ranked list

The handover's ranking stands with two amendments:

1. **Item 3 ("small and anchored") is four-fifths empty.** What remains of it is
   the multi-day lead-in (~1.8 s) and the `[LAT]` format string (§C3). Both
   genuinely small.
2. **Invariant 20 should move up.** It was written down as migration debt; D-r
   proved it ships caller-facing defects, and it is simultaneously the
   onboarding blocker. It is the one item on this list that is both a
   correctness risk today and a scale blocker in six weeks.

Everything else that looked open on the lists was either fixed, superseded, or
blocked on one provisioning step (§B).
