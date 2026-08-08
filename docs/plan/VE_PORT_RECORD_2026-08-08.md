# Vital Edge port record — Theorem engine fixes, 2026-08-08

**What happened:** 45 commits landed on `vitaledge-onboarding`, bringing Jonathan's
clinic up to the engine Theorem had been debugged onto over 4–8 August.

**Base:** `58ec8fe` (`origin/vitaledge-onboarding`, 5 Aug) — *not* the local
branch, which was one commit behind at the time.

**Rollback:** `git push --force origin 58ec8fe:vitaledge-onboarding`.
Render autodeploys, so that one command is the full revert.

---

## 1. What was ported

| | count | |
|---|---|---|
| latency-eval engine delta | 12 | canonical-first — VE was behind |
| Theorem engine fixes | 32 | generic, clinic-neutral |
| Port reconcile test | 1 | new, written for this port |
| **Total** | **45** | |

90 commits separated the branches. 45 did not port, and almost all of them
dropped out without a judgement call, because the two clinics render different
prompts: Theorem is `theorem_v3` in `susie_system_prompt.py`, Vital Edge is
`template_v1` in `clinic_template_prompt.py`. Anything touching only the
Theorem prompt — the Leanne rota, joint injections, the Redditch redirect, AI
disclosure, FAQ length — is invisible to Vital Edge by construction.

**`app/clinics/` was not touched at all.** No Theorem clinic data reached this
branch. Verified by diff, not by inspection.

## 2. What was deliberately excluded

| Commit | Why |
|---|---|
| `9ca1ce2` reason-question suppression | Owner decision. Theorem never asks what brings the caller in; **Vital Edge deliberately does**, with its own wording in `clinic.json`, asked exactly once (`902411a`). The Theorem fix is not clinic-gated and would have silenced it. |
| `a233a5c`, `a1cbb8c`, `6901ffb` location family | **Vital Edge is single-site** (Kingston). Theorem is two-site. Dead code here — and dropping `a233a5c` is what turned the port's only structural conflict into a clean apply. |
| `82ac6e3` reminder kill-switch | Owner decision 2026-08-08: **reminders stay ON for Vital Edge.** That commit adds `APPOINTMENT_REMINDERS_ENABLED` defaulting OFF, so porting it would have switched them off unless a Render variable were set. Nothing else in the port touches `scheduler.py`, so excluding it is dependency-free. |
| ~25 Theorem prompt commits | `susie_system_prompt.py` only — cannot reach `template_v1`. |
| 16 Theorem acceptance docs | Mark's call registers. |

## 3. The one conflict, and the defect the merge would have introduced

`8ce4b74` conflicted in `app/filler_phrases.py`. Two branches had independently
fixed filler duplication:

- **latency-eval `265d95e`** — a cooldown clock. Three producers queued hold
  phrases with no shared state; each now records itself via
  `note_filler_played()` and checks `should_play_filler()`.
- **Theorem `8ce4b74`** — `skip_primary`, suppressing the TTS phrase when
  FillerGuard's recorded clip already spoke, while keeping the 4-second
  secondary.

They are complementary, so both were kept. **But a naive merge opens a hole
neither original had.** Theorem's branch had no clock to register with, so its
`skip_primary` path speaks a secondary filler without calling
`note_filler_played` — real audio the other two producers cannot see, which is
the `265d95e` defect reintroduced through the one path that bypasses its check.
The registration was added; see `3ce2a29`.

Ordering is deliberate: `skip_primary` is evaluated **before** the cooldown,
because `FillerGuard` never calls `note_filler_played`. If the clip is ever
wired into the clock, a cooldown-first ordering would return with no secondary
at all and reopen the dead air O-4 closed. Both halves are pinned by tests
proven against a reverted source.

## 4. Test evidence

Full suite, same worktree, `.env` present (a worktree without it runs a
different set of tests and the diff is meaningless).

| Stage | failures | passing |
|---|---|---|
| Baseline `58ec8fe` | 102 | 3782 |
| After Pass 1A | 96 | 3931 |
| After Pass 1B | 96 | 4402 |

**Regressions: zero.** The failing set after the port is a strict subset of the
baseline's — verified by `comm`, not by comparing counts.

Six tests were **fixed**: four `test_b55` prompt-hash pins plus the
`test_reason_question_once` and `test_under_age_booking_gate` byte-identical
checks, all repaired by `2c24daa` (a scope pin that expired at midnight).

Vital Edge behaviour verified green after the port: reason question, under-18
gate, deposit policy, home visits, 60/90-minute duration capture, per-clinic STT
keyterms, provisional booking, 90-minute bookability — 313 assertions.

**Four pre-existing reds** in `tests/test_filler_guard.py`
(`AttributeError: '_second_delay_s'`) are inherited, not caused here — verified
identical on `origin/theorem-onboarding`.

## 5. Open — needs the owner, not the port

**F-1. Vital Edge has no `owner_alerts` config.** `get_clinic('vital_edge')`
returns `owner_alerts = {}`, so `manual_followup` is off and **a failed booking
escalates to nobody.** This is production-ready criterion 1, and it was already
true before this port — but the port sharpens it, because `6f664a4` flips
`SMS_ENABLED`'s default ON, so Susie now texts patients confidently while still
paging no operator when a write fails. Theorem hit exactly this and logged it as
T-10. Deliberately not fixed here: enabling owner alerts is new outbound SMS to
a real person and needs an explicit destination decision.

The dropped-caller path is fine — `1223cbd` resolves the practitioner from
`clinic.transfer_phone`, and `get_clinic('vital_edge')` supplies
`+447545862307` from `clinic_config.py`.

**F-2. `SMS_ENABLED` on Render.** `6f664a4` flips the in-code default to ON.
If VE's service already sets it true this is a no-op; if not, Vital Edge starts
texting patients on this deploy. Worth confirming in the dashboard.

**F-3. `ELEVENLABS_VOICE_ID` must be `6fZce9LFNG3iEITDfqZZ`.** The hold clips
were cut with that voice; the code default is `kBag1HOZlaVBH7ICPE8x` and there
is no per-clinic override. Owner states the keys are shared across all three
clinics. If it does not match, the hold phrase is a different person cutting
into Susie's turn.

**F-4. FillerGuard does not feed the cooldown clock.** Its clip suppresses
`with_filler`'s phrase but not `connection.py`'s or `llm_stream.py`'s. A real
gap, deliberately left alone: it is new behaviour, not a port, and belongs on
`latency-eval` with its own test.

## 6. Next

Pass 2 — back-port these 32 engine commits to `latency-eval` so
`jv-v1-onboarding` inherits them, per the canonical-first rule. `latency-eval`
is not a live line; push freely. F-4 lands there too.
