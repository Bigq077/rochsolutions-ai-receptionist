# Session record — 2026-09-03 (overnight)

**Branch state at close:** `latency-eval` = `74ad7c73` · `production` = `e022ea8c` (unchanged)
**Promotion batch:** 5 commits, 5 app files. **NOT promoted — deliberately held.** See §3.
**Test baseline:** `98 failed / 7,822 passed` at `dc58d3b5` → `98 failed / 7,840 passed` at `74ad7c73`
(+18 = new tests). Failing sets diffed and identical, digits preserved.

---

## 1. Fixed this session

### 1.1 `dc58d3b5` — a first lookup could answer with a filler and no slots at all

**P6 was guarded on the wrong key.** `_flush_slot_buf`'s stand-down guard read
`session["last_offered_slots"]`, and its comment argued safety from *"on a FIRST
lookup `last_offered_slots` is empty"*. It never is —
`_exec_check_availability` writes it on its main path
(`receptionist_tools.py:6385`, before the only return on its direct body). So the
term was **always true** and P6 collapsed to "the model numbered no options".

Consequence, live on all three clinics: a first lookup whose buffered model
sentence named no numbered option **stood the deterministic offer down and spoke
that sentence instead** — caller hears a filler, gets no slot list, and no DTMF
map. Never observed live only because the model usually numbers its options.

The same false premise made `32cd187b`'s slot-LLM skip **unreachable** (a
deterministic offer existing *implies* the key is set, so the two conditions were
mutually exclusive). It shipped dead and was inert for a day.

Both now read **`v3_dtmf_slot_map`**, written by `_flush_slot_buf` *after* both
readers, so mid-turn it still describes the previous turn.
`connection.py:2690` already stated the rule: *"Guard on the MAP, never on
`v3_awaiting_slot_selection`"*. Every pop site audited — all are end-of-turn or
deliberate window closures.

Two lines of code. Verified live: `slot LLM call SKIPPED` now fires, offer intact.

### 1.2 `74ad7c73` — a caller who had just chosen a slot was apologised to

`WorkKind.UNKNOWN_SLOW` is the fallback for a turn whose work is **unknown**. On a
pick the work was known — `ACCEPTED_SLOT_KEY` pinned 3s earlier — and Susie still
said *"Sorry, still with you —"*.

Second wrong thing said on that turn shape. Before `_hs_picking` suppressed the
diary heads, the same pick produced *"Let me see what I've got in the afternoon —"*
(promising a lookup nobody was doing). Suppressing that left **nothing**, so the
apology took over.

Added `Intent.SLOT_PICKED` → **"Monday it is —"**. Confined to `app/hold_speech.py`
(a pure module); `llm_stream` already passed everything needed.

**Gated on the pick naming a DAY**, for two reasons that agree: `subject_for`
lower-cases bands (so `{subject} it is —` rendered *"afternoon it is —"*), and every
case pinned by `test_choosing_a_slot_still_gets_silence` (30 Aug) is band-only.
Those tests are a **decision**, not a broken fixture — left standing. See §2.4.

Verified live: `situational head (slot_picked): 'Monday it is —'`.

### 1.3 Also in the batch (pre-dating this session's work)

| commit | what |
|---|---|
| `32cd187b` | slot-LLM skip — was dead, now actually fires |
| `fff61547` | chunker: first chunk may break on a clause, not only a full stop |
| `9a2da474` | latency `-1` sentinel; 1,934 of 3,066 stored turns were clock readings |

---

## 2. Open defects found this session

Ranked. Nothing below is fixed.

### 2.1 P1 — an acceptance resolved to the wrong TIME

```
00:46:26,241  caller ACCEPTED 2026-09-07T08:00:00+01:00 ('uh yeah monday at 8 pm works')
```

Caller said **8 pm**; the resolver pinned **08:00 (8 am)** into `ACCEPTED_SLOT_KEY`.
It matched the digit and dropped the meridiem.

Saved only because the model happened to notice Monday has no evening 8 and
re-asked. Had it gone straight to a read-back, the pinned slot was 8 am — and
**P6b exists specifically to make a pinned acceptance survive into readouts**.
Same family as the 90-minute booking written to the diary as 60: a wrong value
that every verbal read-back sounds correct against, because the read-back is
generated from the same wrong pin.

Reproduction: `"monday at 8 pm"` against an offer containing `08:00`.
**This is the one to take first.**

### 2.2 P1 — "the slots I have that day are…" is a false completeness claim

Model-visible tool result held **twelve** Monday times
(`08:00, 08:50, 09:40, 10:30, 11:20, 12:10, 13:00, 13:50, 14:40, 15:30, 16:20, 17:10…`).
Susie said *"the slots I have that day are eight in the morning or ten past five."*

(The first half — *"doesn't have an 8 in the evening"* — is true.)

Structural, not a one-off. `more_times` (B-97) exists to say *"and I've a few others
that day"* but is **deliberately not spoken on multi_day** — `slot_offer.py:445`:

```python
# The tail is a claim about the clinic's diary, so it is made only where it
# has a referent — ONE day. "A few others that day" after a three-day
# readout names no day, which is the B-99 rule, here by construction.
if more and mode == "single_day":
```

So the caller hears *"Here's what we've got coming up"* over 2-of-12 per day with
no caveat, and the model later fills that silence with a false completeness claim.
Turn 4 made **no** fresh `check_availability` call — it answered from history.

**B-97 and B-99 are in direct conflict and multi_day currently resolves it by
saying nothing.** Needs a decision, not a patch. Note the register's warning: a
promise the retrieval path cannot keep ("a few others") once looped a caller into
hanging up (judge score 1). This is the `presented ≠ bookable` split, B-95.

### 2.3 P2 — no natural filler on the reason turn (`hold_speech.py:525`)

```python
(Intent.SYMPTOM, _rx(_HURT), _rx(_BODY), _rx(r"\?\s*$"))
#                 ↑ trigger   ↑ corroborator
```

The head that should fire is `Intent.SYMPTOM` → **"Sorry to hear that —"**
(`hold_speech.py:810`). It requires a **hurt word** to trigger.
*"um just my left ankle nothing serious"* has `_BODY` ("ankle") but nothing in
`_HURT`. No trigger → no head → `UNKNOWN_SLOW` at 3.5s → *"Still with you —"*.

The file warns about this exact shape four lines above the rule:

> Injury is often described with no word for pain at all — "done my ankle", "went
> over on it", "it gave way"… **adding more synonyms is the trap, the SHAPE of the
> matcher is the bug.**

Requiring `_HURT` as the trigger *is* that shape. A caller naming a body part in
answer to *"What's the appointment for?"* is describing a complaint whether or not
they use a pain word. Same root as the screening-trigger bigram defect.

### 2.4 P2 — band-only and positional picks still reach the apology

Deliberate scope limit of `74ad7c73`. *"ten in the morning"*, *"number two"*,
*"yeah, that one"* get silence → `UNKNOWN_SLOW` → *"Sorry, still with you —"* on a
slow turn.

Closing it means **reopening the 30 Aug decision**
(`test_choosing_a_slot_still_gets_silence`, `test_a_resolved_pick_silences_the_lookup_head`).
Their stated reason — *"a head in front of it would promise"* a lookup — does not
cover a head that promises nothing, so there is a principled case. But it is a
decision to take on purpose, and the words would need to work without a day
(*"That one works —"* is already in the pool as the documented fallback).

### 2.5 P2 — the surname is never confirmed before the write

The phone has a **code gate** — A1, `receptionist_tools.py:7565`,
`if session.get("phone_confirmed") is not True:` → `[book] BLOCKED`.
The surname has only a **prompt instruction** (Step 9a,
`clinic_template_prompt.py:2566`), which records that *"speech-to-text has written
the wrong surname to a real calendar twice"*.

`_unconfirmed_callback_number` (`:9283`) names the failure shape in one line:
**"The read-back was decorative."**

Scoped in full during this session. Gate goes beside A1 at `:7565` — it covers all
four executors, since Acuity/provisional dispatch at `:7616`/`:7624` happens after
it. `_reschedule_appointment_acuity:5054` is a legitimate exemption (name comes
from lookup, not fresh STT). Signal shape should copy
`accepted_slot_is_named_in` (`slot_followup.py:2171`) — compare a stored **value**
against spoken text, never a phrase matcher.

**BLOCKED ON A DECISION: warn-only or block?** Recommendation was warn-only first,
measure the false-negative rate, then promote.

### 2.6 P2 — STT name capture

*"Quentin Rock"* → *"quenching work"*, twice, costing three asks and ~14s.
`build_keyterms` is called once at connect (`stt_stream.py:776`) and baked into the
WebSocket URL — **call-scoped, cannot change per phase**. 100/100 terms used, all
clinical/clinic vocabulary, **zero name support**. During `capture_phase=name` the
decoder is boosted toward `back`, `crack`, `grip`, `won't`, `rolled`.

The module contradicts itself here (`stt_stream.py:183`): *"Ordinary English is
never mis-heard, so every common word in this set is a wasted slot"* — yet the live
100 include `car`, `back`, `crack`, `snap`, `grip`, `balance`, `journey`,
`temperature`, `dizzy`.

A mid-session `UpdateConfiguration` transport exists (`stt_stream.py:705`) but
sends only silence thresholds, and is WS-C machinery (default OFF).

**BLOCKED:** needs a wav pulled off Render to A/B the keyterm list.
`logs/audio/` is empty locally and every replay harness works on transcripts, not
audio.

### 2.7 P3 — B-31 200-char cap fires on nearly every call

`last_bot_prompt truncated at 200 chars and lost its '?'` — observed **twice per
call** on both calls tonight. Recovers via the `last_question` fallback each time,
so it is not currently harmful, but it is live constantly and it disables clinical
screening orphan matching whenever the fallback also misses.

### 2.8 P3 — verbosity (the largest caller-experience item)

Measured on `CAdcd52a8a` from synthesis/terminal-chunk timestamps:

| turn | spoken |
|---|---|
| slot readout | **17.9s** |
| ankle empathy + preference question | **13.9s** |
| phone read-back | 8.4s |
| final read-back | 6.7s |
| everything else | ~23s |

**≈70.3s of a 117.6s call — Susie talks 60% of the time.** The caller barged in on
**8 of 9 turns**. That call reached `outcome=reached_confirmation` and booked
nothing.

For scale: the whole L1–L5 latency plan recovers ~14s across that call, at high
risk. Trimming the empathy turn and the readout recovers ~20s, at low risk, in
code we control.

### 2.9 Known-accepted / pre-existing

- `GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON` → Sheets append skipped.
  Known-accepted on the demo line **only**.
- ElevenLabs `401` on `/v1/models` — benign, self-documented in the log line.
- 4 pre-existing `tests/test_filler_guard.py` failures (`AttributeError` at
  `filler_guard.py:383`), present at `32cd187b`, unrelated to this session.

---

## 3. Still needs verifying before promotion

`production` is **5 commits behind**. Held deliberately overnight 2026-09-03.

| commit | live-call exposure | risk |
|---|---|---|
| `74ad7c73` acceptance head | **1 call** | cadence change on EVERY booking call |
| `dc58d3b5` P6 fix | **1 call** | live speech path |
| `32cd187b` slot skip | 2 calls | live speech path |
| `fff61547` chunker clause break | **none this session** | caller-audible, unverified |
| `9a2da474` latency sentinel | n/a | measurement only |

**Before promoting:**

1. Three demo-line calls on `74ad7c73` — a **day pick** ("Monday works"), a
   **band-only pick** ("ten in the morning"), and one asking for a **time that
   does not exist**.
2. Decide whether `74ad7c73`'s cadence is right: a situational head fires at
   `HOLD_HEAD_DELAY_MS` (600ms), not the `UNKNOWN_SLOW` timeout (3500ms, ~8% of
   turns). So it now speaks on **most** day-picks. One line to revert.
3. Verify `fff61547` on a call — it changes where the first spoken chunk breaks
   and has no call behind it.
4. Decide whether §2.1 (the 8 pm defect) ships in the same promotion.
5. **Record `e022ea8c` as the revert target before pushing.** The Render log line
   `[build_info] running build <sha>` is the only proof of what is running.
6. Make a real call **after** the production push, per CLAUDE.md.

---

## 4. Method notes worth keeping

**A test fixture must be a state the engine can reach.** Three defects here were
pinned by tests that passed, because their hand-built sessions omitted a key the
runtime always writes. `test_a_first_lookup_still_speaks_the_payload` existed
*specifically* to assert the property P6 was breaking, and passed throughout.
Repaired, it fails on `dc58d3b5`.

**But distinguish a broken fixture from a decision.** Repairing
`_first_lookup()`/`_session_mid_offer()` was right — they described impossible
states. Rewriting `test_choosing_a_slot_still_gets_silence` would have been
wrong — that is a dated decision. Narrowing the change to day-picks left it
standing and broke nothing.

**`\b` in awk is backspace, not a word boundary.** `/^[[:space:]]+return\b/`
silently matches nothing. It produced a confident "there are no early returns"
that was wrong by 13. The AST test caught it.

**`git checkout -- tests/` does not remove untracked files.** A new test file left
in a "pristine" baseline worktree aborted collection for the whole suite. Use
`git clean -fd` as well, and check `git status --porcelain` is genuinely empty.

**Diff failing SETS, never counts, and keep digits in the filter** —
`[a-zA-Z_./-]` hides defect-numbered files (`test_b84_*` here).
