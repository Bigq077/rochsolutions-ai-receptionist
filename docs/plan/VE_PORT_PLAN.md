# Vital Edge Port Plan — bringing Jonathan's clinic up to the Theorem engine

**Status:** planned and dry-run proven, not executed
**Written:** 2026-08-08
**Target branch:** `vitaledge-onboarding` (live — Jonathan's clinic)
**Source branches:** `origin/theorem-onboarding` @ `6a7e403`, `latency-eval` @ `82ac6e3`
**Owner:** Quentin

---

## 0. The thirty-second version

Theorem took three days of live-call debugging (4–8 Aug) that Vital Edge never
received. **90 commits** separate them. Of those, **44 port**, and the dry run
proved **43 apply clean** with **one hand-resolved conflict**.

The separation is much cleaner than the commit count suggests, because the two
clinics render different prompts: Theorem is `theorem_v3` in
`susie_system_prompt.py`, Vital Edge is `template_v1` in
`clinic_template_prompt.py`. **Every commit touching only the Theorem prompt
drops out automatically** — the Leanne rota, joint injections, the Redditch
redirect, AI disclosure, FAQ length. Those are ~25 of the 90.

**The one thing that can go silently wrong:** the port flips `SMS_ENABLED`'s
default to ON (`6f664a4`). Vital Edge has **no `owner_alerts` config at all**,
so booking failures still escalate to nobody — the port makes Susie *text
patients* without making her *page an operator*. See §6.

---

## 1. Ground truth — verified 2026-08-08

| Branch | Tip | Note |
|---|---|---|
| Vital Edge (**deployed**) | `origin/vitaledge-onboarding` @ `58ec8fe` | 5 Aug |
| Vital Edge (local) | `vitaledge-onboarding` @ `04c9963` | ⚠️ **one commit behind origin** |
| Theorem (deployed) | `origin/theorem-onboarding` @ `6a7e403` | 8 Aug, = `fix/phone-step-ordering` |
| latency-eval | `82ac6e3` | 7 Aug |

> ⚠️ **Trap, already stepped in once.** The local `vitaledge-onboarding` is
> behind `origin` by `58ec8fe` (the T-3 FAQ-watchdog port). Basing the port on
> the local branch silently drops it. **Base on `origin/vitaledge-onboarding`.**

Divergence: merge base is `4f4803e` (4 Aug). 90 commits on Theorem not on VE;
27 on latency-eval not on VE, of which 6 are already equivalent on VE by
cherry-pick.

**Vital Edge is single-site** (Kingston, 1 location). Theorem is two-site
(Alcester / Redditch). This is why the entire location-disambiguation family is
inert for VE — see D-2.

---

## 2. What ports, and what does not

| Bucket | Count | Disposition |
|---|---|---|
| latency-eval engine delta | 12 | **PORT** — clean linear inherit |
| Theorem engine fixes, generic | 32 | **PORT** — 31 clean, 1 reconcile |
| Theorem prompt / clinic config | ~25 | **EXCLUDE** — `susie_system_prompt.py`, `theorem/clinic.json` |
| Theorem acceptance docs | 16 | **EXCLUDE** — Mark's call registers |
| Merge commits | 3 | **EXCLUDE** — replaced by taking latency-eval directly |
| Already equivalent on VE | 8 | **SKIP** — `git cherry` confirms |

Six Theorem fixes (T-2, T-3, both obs fixes, the callback-SMS one, the inbound
SMS relay) already exist on latency-eval as twins. **VE takes those from
latency-eval, not from Theorem** — canonical-first, and the latency-eval
versions are the reviewed ones.

---

## 3. The proven sequence — 44 commits, in this order

Dry run: worktree off `origin/vitaledge-onboarding`, branch `ve-port-dryrun`.
Result recorded below is measured, not predicted.

### Pass 1A — latency-eval delta (12, all clean)

```
2c24daa  test(b55): a scope pin that expires at midnight is not a scope pin
7090e4c  fix(b57): Theorem could not cancel — the gate could not hear its own prompt
3d5d0b8  fix(b39): the retention question belongs to the cancel path, asked once
4eb1e0c  fix(fillers): the waits on cancel and reschedule sounded like a hold
c69ec2c  feat(eval): staff SMS redirect so eval runs cannot text the practitioner
e6fed61  fix(T-2): an operator was paged about a successful booking
57d5b67  fix(booking): a caller who led with a condition was never offered a booking
265d95e  fix(fillers): three hold phrases in 3.4 seconds, from three producers
cef1856  feat(sms): a patient's text reached one person, and sometimes nobody
63ba798  fix(obs): a call Susie ended was reported to the operator as a hangup
1345520  fix(obs): the judge never saw the end of the call, so it invented one
486401c  fix(obs): the CALL BACK SMS reported a paraphrase, not the call
```

`63ba798` brings `app/obs/turns.py`, which **does not exist on VE at all**.

### Pass 1B — Theorem engine fixes (32; 31 clean, `8ce4b74` reconciled)

```
4dcad7d  fix(sweep): a 20-call acceptance run would have rung Mark's real mobile
6f664a4  fix(sms): a live clinic line inherited an eval branch's silence   ← see §6
6e6d7aa  fix(clinic-q): a caller asked about medication and was asked which clinic
ec150b7  fix(slots): "afternoons" was not a word Susie knew how to hear
01d0070  fix(name): a pricing question produced a first name called "Own"
1a6981a  fix(staff-notify): the missed-patient ping logged "sent" when it wasn't
d9df18a  fix(reschedule): a dead guard injected a turn on top of one still speaking
b4174bf  fix(reschedule): the appointment decides the clinic, not a session default
cc385a8  fix(transfer): a caller was told they were being put through to nobody
a330eb7  fix(slots): a caller who picked a slot was dropped for saying "Three."
d063680  fix(caller-id): a withheld caller's number was "anonymous", we believed it
9427f8f  fix(transcripts): a caller answered a slot list four times, heard none
cffdac1  fix(dtmf): pressing 1 to pick the first slot transferred the caller
acbe0c6  fix(phone gate): the clinic keypad question counted as asking for a number
420a809  fix(noise): "aye" is a yes, and it was being deleted as mouth-noise
6efcb80  fix(location): Susie asked which clinic, then stopped listening
e6c49f6  fix(dead air): barge-in charged interrupted audio to the next utterance
72cc6ce  fix(slots): the caller said "three", we heard "free", said nothing back
691c7fa  fix(slots): reaching the model with "free" is not being understood
80244ef  fix(booking): he agreed to the booking 52 seconds before it happened
2fe7ee4  fix(gate5g): the phone gate was eating reschedule/cancel confirmations
da3bf51  fix(keypad): he typed eleven digits into a closed keypad
da144db  fix(gate5g): the name was asked last, after the number had been typed
6cdda07  fix(sms): the caller who got furthest was the one we never texted
5f81d83  fix(filler): the hold clips were looked for in whatever directory
1223cbd  fix(dropoff): only one clinic's dropped callers ever reached a human
ee6050b  fix(transfer): she promised to put him through, then said nothing
72158d1  feat(filler): the hold clips exist
8ce4b74  fix(filler): four ways of "let me look" in 4.6 seconds   ← RECONCILE, D-3
cf0f35d  fix(filler): cut the hold clips in the voice callers actually hear
03929f2  fix(filler): the hold clip fired four times in a ninety-second call
6a7e403  fix(name): he said his name three times and was asked a fourth
```

`8ce4b74` and `cf0f35d` **must ship together.** `8ce4b74`'s own commit message
carries a deploy warning — it suppresses the correct-voice TTS phrase, so
shipping it without `cf0f35d`'s re-cut clips makes a voice mismatch *more*
audible, not less. They are adjacent in this list for that reason.

---

## 4. The two conflicts

### C-1 `a233a5c` — dropped, not resolved

`fix(location): an unresolvable clinic answer defaults to the primary site`.
Conflicts in `connection.py` and `test_non_bookable_clinic_redirects_not_asks.py`
because it builds on `a1cbb8c` (the Redditch guard), which is Theorem-only.

**Vital Edge has one location.** The whole family — `a233a5c`, `a1cbb8c`,
`6901ffb` — is dead code for a single-site clinic. Dropping it costs nothing.
With it dropped, the preceding 40 commits apply **clean**.

### C-2 `8ce4b74` — hand-resolved, and the ordering is load-bearing

The two branches independently fixed filler duplication:

- **latency-eval `265d95e`** — a shared cooldown clock (`should_play_filler` /
  `note_filler_played`, `FILLER_COOLDOWN_S = 3.0`) called at every producer.
- **Theorem `8ce4b74`** — a `skip_primary` flag suppressing the TTS phrase when
  FillerGuard's recorded clip already spoke.

They are **complementary, not competing** — a cross-producer clock and a
clip-vs-TTS suppressor. Both are keepers. They conflict only because both want
the same insertion point in `with_filler`.

**Resolution: `skip_primary` first, cooldown second.** Verified: `FillerGuard`
sets `_filler_clip_spoke_this_turn` but **never calls `note_filler_played`**, so
the recorded clip is invisible to the cooldown clock. If that is ever wired up
(see D-4) and the cooldown ran first, `with_filler` would return with no
secondary at all — reopening the dead air on a slow Acuity round-trip that O-4
closed. Ordering it this way is safe under both present and future behaviour.

Proven: `tests/regression/test_filler_is_not_said_twice.py`,
`test_filler_only_on_lookup_turns.py`, `test_filler_clips_are_on_disk.py` all
green on the resolved tree.

---

## 5. Decisions taken

| # | Decision | Rationale |
|---|---|---|
| **D-1** | **`9ca1ce2` (reason-question suppression) EXCLUDED** | Owner decision, 8 Aug. Theorem never asks the reason; VE deliberately does, with its own wording in `clinic.json`, asked exactly once (`902411a`). The Theorem fix is **not clinic-gated** and would silence VE's question. VE keeps asking. |
| **D-2** | **Location family DROPPED** (`a233a5c`, `a1cbb8c`, `6901ffb`) | VE is single-site. Inert code, and the source of the only structural conflict. |
| **D-3** | **Filler: full port, clips included** | Owner confirmed the ElevenLabs keys are shared across all three clinics. Take the end state through `03929f2` — the work went through five iterations and only the last is correct. |
| **D-4** | **Wiring FillerGuard into the cooldown clock: NOT in this port** | It is a real gap — the clip does not suppress `connection.py`'s or `llm_stream.py`'s producers, only `with_filler`'s. But it is a new behaviour, not a port, and belongs on `latency-eval` with its own test. Filed, not folded in. |
| **D-5** | **VE first, latency-eval second** | Owner decision. VE is the starved branch. Pass 2 back-ports the same 32 engine commits to `latency-eval` so `jv-v1` inherits them. |
| **D-6** | **`82ac6e3` (24hr/2hr reminder kill-switch) — DECISION NEEDED** | It is in the latency-eval delta but is a *behaviour* choice, not a fix. Does Jonathan want appointment reminders off? Currently unanswered. |

---

## 6. Findings the port surfaces but does not fix

### F-1 — Vital Edge has no `owner_alerts` config. HIGH.

`vital_edge/clinic.json` has **no** `owner_alerts`, `digest`, or `staff_notify`
keys. A booking that fails escalates to nobody. This is production-ready
criterion 1 and it is currently unmet on a live clinic.

It becomes sharper *because* of this port: `6f664a4` flips `SMS_ENABLED`'s
default to ON, so after the port Susie texts **patients** confidently while
still paging **no operator** when a write fails. Theorem hit exactly this and
logged it as T-10.

### F-2 — `SMS_ENABLED` default flip changes live behaviour. Verify before deploy.

`origin/vitaledge-onboarding` still carries latency-eval's `SMS_ENABLED=false`
default, comment and all (`app/notifications/sms.py:75`), including the line
"DO NOT port this default flip to main/theorem/jv live branches" — which was
ported anyway, to VE as well as Theorem.

**If VE's Render service already sets `SMS_ENABLED=true`, this is a no-op.
If it does not, Vital Edge starts texting patients the moment this deploys.**
Check the Render dashboard before, not after.

### F-3 — `ELEVENLABS_VOICE_ID` must be `6fZce9LFNG3iEITDfqZZ` on VE's service.

The clips were cut with that voice; the code default is
`kBag1HOZlaVBH7ICPE8x` (`app/media_streams/config.py:58`). There is **no
per-clinic voice override** — it is one global env var. Owner states the keys
are shared, so this should hold, but `audio_clips/VOICE_ID.txt` says it plainly:
if it does not match, "the hold phrase is a different person cutting into
Susie's turn."

### F-4 — 4 pre-existing reds in `tests/test_filler_guard.py`.

`AttributeError: 'FillerGuard' object has no attribute '_second_delay_s'`.
**Verified identical on `origin/theorem-onboarding`** — inherited debt, not a
port artefact. Do not let them mask a real regression in the baseline diff.

---

## 7. Execution

Gates are hard. Do not start a step before its gate passes.

**Step 0 — baseline.** Fresh worktree off `origin/vitaledge-onboarding`. Copy
`.env` in first (a scratch worktree without it runs a *different* set of tests —
~96 failures without, ~104 with). Record the full failing set to a file.
*Gate: a recorded failing set exists.*

**Step 1 — Pass 1A.** Cherry-pick the 12 latency-eval commits with `-x`.
*Gate: 12/12 clean; suite failing set diffed against Step 0 and explained.*

**Step 2 — Pass 1B.** Cherry-pick the 32 Theorem commits with `-x`, resolving
`8ce4b74` per §4 C-2.
*Gate: failing set diffed against Step 1. Any new failure is triaged before
proceeding — no "probably fine".*

**Step 3 — VE-specific regression check.** VE's own behaviours must be
untouched: the reason question (`test_reason_question_once.py`), the under-18
gate, the deposit policy, home visits, 60-vs-90-minute duration capture, STT
keyterm boosting.
*Gate: all green. If the reason question broke, D-1 was violated.*

**Step 4 — config verification.** F-2 and F-3 against the Render dashboard.
*Gate: both answered in writing before any push.*

**Step 5 — deploy.** `vitaledge-onboarding` is a **gated deployment branch**
serving a live clinic. Out-of-hours, revert commit written in advance,
Jonathan told it is happening. Confirm the build actually shipped: `/health`
returns a hardcoded `1.0.0` and proves nothing — the only deploy proof is
`[build_info] running build <sha>` in the Render log at call cleanup.

**Step 6 — live verification.** One booking call end to end. The failure mode
this system has is *the call sounds perfect and the booking silently never
happened*, so the check is the Acuity calendar, not the call.

**Step 7 — Pass 2.** Back-port the 32 engine commits to `latency-eval` so
`jv-v1-onboarding` inherits them. `latency-eval` is not a live line — push
freely. This is where D-4 lands too.

---

## 8. Dry-run evidence

Reproduced 2026-08-08 in a throwaway worktree, branch `ve-port-dryrun`:

- Pass 1A: **12/12 clean.**
- Pass 1B with `a233a5c` dropped: **40/40 clean** through `72158d1`.
- `8ce4b74`: one conflict, `app/filler_phrases.py`, resolved as §4 C-2.
- `cf0f35d`, `03929f2`, `6a7e403`: **clean.**
- Total applied: **44 commits.** Audio clips transferred intact as binaries
  (`filler_checking.ulaw` 11146 B, `filler_moment.ulaw` 8916 B).
- Filler regression tests green; the 4 `test_filler_guard.py` reds match
  Theorem exactly.

The full suite has **not** yet been run against the ported tree — that is
Step 0–2's gate, and it is the remaining unknown.
