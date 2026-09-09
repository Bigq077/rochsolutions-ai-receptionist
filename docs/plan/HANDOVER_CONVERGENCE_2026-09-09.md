# Susie — Demo/Theorem Convergence & Defect Closure

**Written** 2026-09-09 ~04:00 BST, at the end of a live debugging session.
**Audience** the next engineer (or Claude Code session) picking this up cold.
**Goal** close the open defects, and make the Theorem call path behave as closely as possible to the demo (Northgate) path.

> **Read §0 before running anything.** It contains the phone-testing safety rules. Getting those wrong deletes a real patient's appointment.

---

## 0. State, safety, and how to verify anything

### 0.1 Branches — both are the same commit right now

| Branch | SHA | Serves |
|---|---|---|
| `latency-eval` | `f9793204` | demo line only — **+447366263180** (`northgate`) |
| `production` | `f9793204` | live clinics — Theorem **+447380841468**, JV **+447367002651**, Vital Edge |

**Revert target if anything goes wrong: `ff0fa987`.**

```bash
git push --force-with-lease origin ff0fa987:production
```

`production` is a **fast-forward target, never a merge target**. Commit to `latency-eval`, verify, then:

```bash
git push origin origin/latency-eval:production
```

### 0.2 Which Render service is which

Confirmed from call logs this session — the stream URL in `[ms_router] incoming call` identifies the service:

| Service host | Clinic | Number | Branch |
|---|---|---|---|
| `low-latency-joint-venture.onrender.com` | `northgate` | +447366263180 | `latency-eval` |
| `rochsolutions-ai-receptionist.onrender.com` | `theorem_v3` | +447380841468 | `production` |

> ⚠️ Because both branches are currently the same commit, `[build_info] running build <sha>` **cannot** tell you which branch a service tracks. If you need to know, read the branch field in the Render dashboard — it is the only authority. `/health` returns a hardcoded `1.0.0` and is useless.

### 0.3 🔴 PHONE TESTING SAFETY — read in full

**The Theorem number +447380841468 is Mark's live patient line, and its bookings are real.** Theorem short-circuits to the **Acuity** executors, so a completed cancel or booking hits a real calendar.

1. **NEVER complete a cancel on the Theorem line.** Drive the flow as far as the appointment read-back ("I've got … is that the one?") and then **hang up**. The read-back is where all the interesting behaviour is; the confirmation is where the damage is.
2. **NEVER complete a booking on the Theorem line.** Not even one you intend to delete afterwards.
3. **Do all destructive-path testing on the demo line** (+447366263180, `northgate`). It has its own calendar; the entry the lookup finds there ("Quentin Roch, Wednesday 9 September 15:30") is a test fixture, safe to cancel and recreate.
4. **Prefer out-of-hours** for any Theorem call. A physio line at 03:00 has effectively no real callers; 10:00 does.
5. **Never run `tests/auto` or any suite that can reach Acuity without the opt-in guard.** A previous session booked **60 real appointments** this way. Check the opt-in before running anything outside `tests/regression`.
6. **SMS**: `SMS_ENABLED` is off on the demo service and on in the clinic services. Patching `sms.send_sms` does **not** stop outbound texts — `booking_sms`, `owner_alert` and `smart_sms_router` each bind their own copy. A previous test texted the owner six times.

### 0.4 Test suite — it is meant to be red

Do **not** look for green. Diff failing **sets**.

```bash
python -m pytest -q 2>&1 | grep "^FAILED" | sed 's/ - .*//' | sort > /tmp/after.txt
diff /tmp/before.txt /tmp/after.txt
```

Baseline on `f9793204`, 2026-09-09: **98 failed / 9298 passed**. (97 on 2026-09-08 — the 98th is a date rollover, see **D6**.)

Capture a `before.txt` on an unmodified tree **first**. Do not trust the count alone: the digit-safe filter matters, and a naive `[a-zA-Z_./-]` filter hides 56 defect-numbered test files.

### 0.5 Reading the obs corpus

The highest-value tool in the repo for this work, and under-used. **939 calls across all four clinics.**

```python
import os
from dotenv import dotenv_values
os.environ.update({k: v for k, v in dotenv_values(".env").items() if v})
from sqlalchemy import create_engine, text
e = create_engine(os.environ["OBS_DATABASE_URL"], connect_args={"connect_timeout": 20})
with e.connect() as c:
    for r in c.execute(text("""
        select clinic_id, count(*) n, min(start_utc)::date, max(start_utc)::date
        from calls group by 1 order by 2 desc""")):
        print(r)
```

Gotchas, all hit this session:

* `load_dotenv()` raises under this Python — use `dotenv_values` as above.
* There is **one** table, `calls`. `obs_turns` exists but is empty.
* The timestamp column is `start_utc`, **not** `started_at`.
* `transcript` is `json` — you must cast: `transcript::text ilike '%…%'`.
* `failure_tags` is JSON; cast or parse before matching.

| clinic | calls | range |
|---|---|---|
| `jv_v1` | 614 | 2026-07-25 … 09-07 |
| `theorem_v3` | 134 | 2026-08-08 … 09-09 |
| `northgate` | 119 | 2026-08-28 … 09-09 |
| `vital_edge` | 72 | 2026-08-08 … 09-07 |

⚠️ Most of these are **our own test calls**, not real patients. Treat absolute quality scores as soft; trust relative patterns.

---

## 1. Context — what happened in this session

The owner made live cancel calls and reported what he heard. Four defects were found, fixed, verified on a real call, and shipped to `production`. The session then turned into a disagreement about root cause, which the obs corpus settled.

### 1.1 The sequence

**Started on Theorem.** A cancel call produced the clinic question three times and a duplicated opener. Root cause: three guards each kept a private copy of the "did we already ask which clinic?" keyword list, and the one that mattered was missing `"awlstuh or redditch"` — the phonetic spelling the model is actually taught to write. → `f89a4c7e`.

**Then the opener.** `join_after_head` had two dedupe paths, both keyed on a head *family* (apology, lookup opener). `INTENT_HEADS` has 21 families and 46 wordings. Measured: **30 of 46 echoed straight through**. Fixed on full word-equality → `66b8c209`. **The same defect then recurred on that very build**, because the chunker welded the echo onto the payload and equality cannot see a chunk that continues. Fixed again as a prefix strip, allowed only where the echo ends a clause → `ff0fa987`.

**Then the cancel became a booking.** With the openers fixed, a call reached turn 2 for the first time and Susie asked a cancelling caller *"Is there a particular day or time that works best for you?"*. `v3_caller_intent` had **eight readers, every one defaulting to `"booking"`**, and two writers that both miss the ordinary shape of a cancel. The correct intent was already computed 600ms into the call by `hold_speech.classify_intent` and thrown away. → `6f277d41`.

**Then it happened again through a different door.** Recording the intent was necessary and not sufficient: **five** sites decide what to ask once the clinic is known, and one — the deterministic verbal intercept — never read the intent. Which site a call reaches depends on **how well STT heard the clinic name**. → `f9793204`.

### 1.2 The disagreement, and how it resolved

Mid-session the owner proposed that **Theorem's different prompt** was behind most of the problems. The first analysis said it explained none of them, on the grounds that every open defect lived in engine code or affected Northgate too.

**That was too narrow, and the obs data corrected it.** See §3.2. The phonetic spelling in Theorem's prompt has measurably cost: one live defect, a corrupted quality signal across 14+ calls, and it duplicates a capability the TTS layer already has.

The owner also pushed back on a claim that the **location ladder** was the top problem. **He was right and the analysis was wrong** — that claim rested on three unrepresentative 4am calls. The corpus says the ladder resolves on the first answer **92%** of the time. See §3.3.

---

## 2. What was fixed and verified (do not redo)

| SHA | Defect | Verified |
|---|---|---|
| `f89a4c7e` | Clinic question asked twice — three guard copies, one missing the phonetic form | Theorem, live |
| `66b8c209` | Head echo when it arrives as its own chunk | Northgate, live |
| `ff0fa987` | Head echo welded to the payload (prefix strip, clause-boundary guarded) | Northgate 17:59, live |
| `6f277d41` | `v3_caller_intent` never recorded → cancel treated as booking | Theorem 03:12 + Northgate 03:35, live |
| `f9793204` | The one post-location site that never read the intent | Theorem 03:28, live |

All five are on `latency-eval` **and** `production`. Suite clean against baseline at each step.

Verified working end to end on the demo line at 03:35:

```
03:35:43.7  caller intent = cancel
03:35:44.5  head:  'No problem at all —'
03:35:46.6  model: "I've got you on oh seven five oh two … is that the number
                    the appointment was booked under?"   ← no echo
03:35:58.8  lookup_patient → 2 matches → name read back (B-42 guard working)
```

---

## 3. The core finding — the two lines are different systems

### 3.1 Same commit, very different call

Time from the caller saying "cancel" to being asked about their number:

| | Theorem 03:28 | Northgate 03:35 |
|---|---|---|
| elapsed | **21.1s** | **2.9s** |
| reached `lookup_patient`? | no, in 35s | yes, at 15s |

Three axes of divergence, **none of them the code**:

1. **One config field changes which code runs.** `northgate` has `locations = 1`; `theorem_v3` has `locations = 2`. Northgate logs `single-site 'didsbury' auto-confirmed at call start — two-clinic location gate suppressed on all paths` and skips the whole ladder.
2. **A different prompt writes different words.** `northgate` is `prompt_engine: "template_v1"` (data-driven from `clinic.json`). `theorem`'s `prompt_engine` is **unset** and falls through to `_build_theorem_v3` — **2,831 lines of hardcoded Python** (`app/prompts/susie_system_prompt.py:1759`).
3. **Per-service env differs.** Sheets works on Theorem, broken on demo; observability is on for both.

**Consequence: "byte-identical code" guarantees almost nothing here.** The demo line is not a weaker test of Theorem — it is a test of a different system. That is why all four defects were found by a human on a phone rather than by 9,298 tests.

Concrete example of the divergence mattering: the phone question differs in **kind**.

* Northgate (model-generated): *"I've got you on oh seven five oh two, two one one, two oh seven — is that the number the appointment was booked under?"*
* Theorem (engine constant `_V3_PHONE_CONFIRM_Q`): *"Is the number you're calling on the one associated with your booking? If so, just say 'use this number'."*

**Northgate reads the number back. Theorem does not.** The live clinic has the weaker version.

### 3.2 The phonetic spelling is a real, measured defect generator

`theorem_v3`'s prompt teaches the model to write `"Awlstuh"` (59 times) so the TTS pronounces Alcester correctly. It appears in **105 of 134** Theorem transcripts. Cost so far:

1. **A live defect** — the double-ask guard matched `"alcester"`, a spelling the model is instructed never to produce (`f89a4c7e`).
2. **A corrupted quality signal.** Of 45 low-scoring Theorem calls whose judge evidence mentions the clinic/location, only **7** involved a real ladder escalation. **14** are the judge reading the spelling and reporting a *"garbled clinic name"* when nothing went wrong. Verbatim:
   > *"Susie replied 'Right — Is this for our Awlstuh or Redditch clinic?' **(garbled clinic name)**"*
3. **It duplicates existing machinery.** `config/pronunciation_dict.json`, `_apply_tts_substitutions_elevenlabs` (`connection.py:180`) and `app/clinics/theorem/canonical.py:441` (`STT_PRONUNCIATION`) all already exist for exactly this.

Solving pronunciation in the prompt puts the hint into **every downstream consumer** — guards, transcripts, the judge, the owner's summaries.

### 3.3 The location ladder is NOT the problem

Measured over 134 Theorem calls, 104 of which reached the clinic question:

| outcome | count | rate |
|---|---|---|
| resolved on the first answer | 93 | **92%** |
| needed rung 2 (biased confirm) | 8 | 8% |
| needed rung 3 (DTMF keypad) | 3 | 3% |

Do **not** rebuild the ladder. Wording and edges only. Two of the eight `dead_end` samples were the clinic question asked three times — that is `f89a4c7e`, already fixed.

### 3.4 What actually goes wrong on Theorem

`failure_tags` across all 134 calls:

```
booking_error        64
dead_end             42
loop                 34
caller_frustration   24
wrong_info           10
```

Judge evidence, spelling complaints filtered out:

* **Slots drip-fed two at a time** — *"Number 1, nine in the morning. Number 2, ten in the morning. And I've a few others that day"* — forcing the caller to keep asking. Most repeated complaint in the corpus.
* **Stalling mid-turn** — *"Just so we've got a reason on the booking."* then nothing, prompting *"hello?"*
* **A cancel looping after a successful reschedule** — new slot booked, original never cancelled.
* **Susie contradicting herself** — *"Right with you…"* then *"No — I'm Susie, Theorem Health's AI receptionist."*

---

## 4. Open defects, ranked

### D1 — Phonetic spelling leaks out of the TTS layer 🔴 highest leverage

The model writes `"Awlstuh"`; every downstream consumer sees it.

**Anchors**

* `app/prompts/susie_system_prompt.py:1759` — `_build_theorem_v3`, 2,831 lines, 59 occurrences of the phonetic form
* `app/clinics/theorem/canonical.py:441` — `STT_PRONUNCIATION`
* `config/pronunciation_dict.json`
* `app/media_streams/connection.py:180` — `_apply_tts_substitutions_elevenlabs`
* `app/media_streams/connection.py:1667` — `_CLINIC_Q_SIGNALS`, which currently has to carry **both** spellings because of this
* `app/clinics/theorem/clinic.json` — the location `address` strings also contain `"Awlstuh"` inline

**Fix shape** — the model writes **"Alcester"**; the TTS substitution layer converts to the phonetic form at synthesis time, and only there.

**Why it is first**: it un-corrupts the quality signal. Until it is done you cannot measure whether D2/D3/D4 improved anything, because ~19% of Theorem's low scores are an artefact.

### D2 — Slots are drip-fed two at a time 🔴 biggest failure cluster

**Anchors**

* `app/tools/slot_offer.py:50` — `MULTI_DAY_MAX_DAYS = 3`
* `app/tools/slot_offer.py:51` — `MULTI_DAY_TIMES_PER_DAY = 2`
* `app/tools/slot_offer.py:97` — `_pick_times_for_day`
* `app/tools/receptionist_tools.py:5492` — `_MAX_PRESENTED_DAYS = 3`

These are **module constants, identical for every clinic** — so this is not a Theorem/demo divergence, it affects all four lines. `_pick_times_for_day` already has good spreading logic for `limit >= 3`; the cap of 2 is what prevents it being used.

**Do not just raise the number.** Read the docstrings first — there is real reasoning about *"ten in the morning or eleven in the morning is not a choice a caller experiences as two options"*. The likely correct change is clinic-configurable, and larger when the caller has named a single day.

### D3 — `lookup_patient` runs as a reschedule on a cancel 🟠 small, understood

Observed on the **demo line**, 03:35:58: caller said cancel, engine recorded cancel, model called `lookup_patient(purpose="reschedule")`.

**Anchors**

* `app/tools/receptionist_tools.py:2062` — the enum is `["cancel", "reschedule", "history"]`, but the description says `"'cancel' or 'reschedule' — look up an upcoming appointment"`, i.e. it tells the model the two values are **interchangeable**
* `app/tools/receptionist_tools.py:10248` — `LOOKUP_PURPOSE_KEY`
* `app/tools/receptionist_tools.py:6063` — `_reschedule_busy_block`, gated on `_lookup_purpose == "reschedule"`
* `app/tools/receptionist_tools.py:6117` — service-sizing override, same gate

**Fix shape** — two parts: (a) make the schema description distinguish the two; (b) validate the model's `purpose` against `session["v3_caller_intent"]`, which is now reliably recorded (`6f277d41`). The engine's recorded intent should be the authority.

### D4 — Watchdog arms the wrong recovery after a two-sentence question 🟠 has an unknown

Observed on Theorem 03:28:30 (did not fire — caller hung up 3s before).

```
question-less turn reached the arming family —
  last_sent="If so, just say 'use this number'."
T-3 nudge armed — armed "Anything else you'd like to know?"
```

`_V3_PHONE_CONFIRM_Q` is two sentences; the chunker splits on sentence boundaries; `last_sent` is therefore the trailing non-question. The nudge then does `_sh_w.last_question = _nudge_w`, **overwriting the outstanding phone question**.

**Anchors**

* `app/media_streams/connection.py:1411` — `_V3_PHONE_CONFIRM_Q`
* `app/media_streams/connection.py:~16450` — `_outstanding_q_w` derivation
* `app/media_streams/connection.py:~16510` — the T-3 nudge arming

**⚠️ UNRESOLVED**: the site that ran **does** call `on_question_asked(_next_q)` with the full two-sentence string, so `_sh_w.last_question` should have been populated and the BACKSTOP branch should have fired instead of T-3. **Something clears or diverges between 03:28:24.46 and 03:28:30.41 and I do not know what.** Find that before fixing; do not paper over it by special-casing the string.

### D5 — The same opener twice in one call 🟡 polish

Demo line 03:35: head `'No problem at all —'` at 03:35:44, then the model opened turn 2 with `'No problem at all.'` at 03:35:58. **Not** the echo defect — separate turns, so the dedupe correctly declined. It is a head-rotation question: `render_intent_head` rotates by `len(session["used_fillers"])`, but the model's own openers are not part of that rotation.

### D6 — A date-dependent regression test 🟡 hygiene

`tests/regression/test_month_first_date_is_honoured.py:213`, case `[8th or 9th-start2-end2]`. Passed on 2026-09-08, fails on 2026-09-09, on an **unmodified tree**. It pins a literal against the real clock. Freeze the clock or express the expectation relative to an injected date. **Do not delete the case** — the behaviour it pins is real.

### D7 — Demo service Sheets is broken ⚪ env var, not code

```
GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON: JSONDecodeError('Invalid \escape: line 5 column 46')
```

Works on Theorem, fails on the demo service — a malformed env var on `low-latency-joint-venture`, not a code fault. Fix by re-pasting the service-account JSON in Render. No code change.

### Non-defects — do not "fix" these

* **ElevenLabs 401 on `/v1/models`** — a prewarm probe with a key lacking `models_read`. Synthesis uses a different endpoint and is unaffected. The log line says so itself.
* **`cache_read=0` on turn 1** — every call's first turn is a cold cache by definition. Turn 2 reads correctly (`cache_read=28469` observed). The cache works; an earlier read of this as "the cache is broken" was a sampling error.
* **Duplicate `/ms/incoming` + Twilio 400 `21220`** — Twilio retrying a webhook after the call ended. Noise.

---

## 5. Plan of action

Work the phases in order. Each ends with a suite clean against baseline and a commit on `latency-eval`. Promote to `production` only where stated.

### Phase 0 — Establish the baseline (30 min, no changes)

1. `git fetch`, confirm both branches at `f9793204`, spin your own worktree. (~15 registered worktrees exist under `AppData/Local/Temp/claude/`; another session's `reset --hard` can destroy uncommitted work in a shared tree. Run `git worktree prune`, then `git rev-parse --abbrev-ref HEAD`.)
2. Copy **both** `.env` files into the worktree — a scratch worktree without them runs a *different set of tests* (~96 without vs ~104 with).
3. Capture `/tmp/before.txt` (expect **98** on 2026-09-09).
4. Run the obs recipe in §0.5 and reproduce the numbers in §3.2–3.4. If they do not reproduce, **stop** — something has changed and this document's evidence is stale.

**Gate**: baseline captured, obs numbers reproduced.

### Phase 1 — Close the cancel path (D3, D4)

The two defects in the flow the owner is actively testing.

1. **D3** — schema description + validate `purpose` against `v3_caller_intent`. Regression test: a cancel-intent session must not produce `purpose="reschedule"`; a genuine reschedule must still work; the validation must never raise.
2. **D4** — **first find out why `_sh_w.last_question` was empty.** Add temporary instrumentation if needed and reason it from the code path; the answer is between `on_question_asked` and the tts_finished handler. Only then fix. The fix should make the "did this turn ask a question?" predicate read the **whole turn's speech**, not the last chunk — that is the general form and it protects every multi-sentence question, not just this one.
3. Suite, diff failing set, commit each separately.

**Gate**: both fixed, suite clean, **one demo-line cancel call** (safe — §0.3) confirming `purpose="cancel"` in the tool log and no T-3 nudge.

### Phase 2 — Move pronunciation out of the prompt (D1)

The convergence work's first real step, independently valuable. **Its own commit, never bundled.**

1. Confirm the TTS substitution layer can do the job: `_apply_tts_substitutions_elevenlabs`, `config/pronunciation_dict.json`. Add `Alcester → Awlstuh` there if not present.
2. Replace the phonetic form with `"Alcester"` in `_build_theorem_v3` and in `app/clinics/theorem/clinic.json`'s location strings.
3. Simplify `_CLINIC_Q_SIGNALS` (`connection.py:1667`) — once the model writes the real spelling the phonetic entries become dead weight. **Keep them anyway** for one release: old sessions and the corpus still contain them, and removing a guard entry is how `f89a4c7e` happened.
4. **Verify by replay, not by phone.** Render both prompts and diff them. Every difference must be either the pronunciation change or nothing. A third category means something else moved.
5. Re-run the §3.2 obs analysis on **new** calls to confirm the judge stops reporting "garbled".

**Gate**: prompts diff to exactly the intended change; suite clean; **one Theorem call, abandoned at the read-back** (§0.3 rule 1) confirming Alcester is still pronounced correctly.

### Phase 3 — Slot presentation (D2)

Biggest failure cluster (`booking_error` 64). Affects **all four clinics**.

1. Read the docstrings in `slot_offer.py` before changing a number. The reasoning about adjacent times is sound.
2. Make the caps clinic-configurable with the current values as defaults, so nothing changes until a clinic opts in.
3. Widen for the case the corpus complains about: caller names **one day** → offer more than two times on it, using the existing `limit >= 3` spreading.
4. Regression tests against the stored payloads that produced the complaints.

**Gate**: suite clean; replay the offending calls from the corpus and confirm the new presentation would have avoided the caller's repeat question.

### Phase 4 — Converge Theorem onto `template_v1`

**The strategic goal, and the answer to "make Theorem like the demo path".** Only start once Phases 0–3 are shipped and stable.

The property that makes this safe: **the final switch is one config value.** Everything before it is inert.

1. **Extract, don't rewrite.** Move Theorem's facts out of `_build_theorem_v3` and `canonical.py` into `clinic.json`, with the hardcoded builder still live and still serving. Nothing changes for callers.
   * Known trap: Theorem's **hours** have four sources with only one reaching the model; its **age policy** had six. Assert the *claim*, not the phrasing.
2. **Diff, don't eyeball.** Render `template_v1` for Theorem and the current builder; compare. Every difference is either a fact that did not migrate or a behaviour you are deliberately changing. There is no third category.
3. **Replay before you call.** 134 stored Theorem calls. A prompt change is exactly what that corpus is for — behavioural diffs on real transcripts are the only test that means anything here, because **the 9,298 tests pin engine logic and almost none pin what the model says**.
4. **Flip `prompt_engine` last** — one line, one commit, instantly revertible.

**Known deliberate divergences to preserve** (do not "converge" these away):

* Theorem never asks the reason for the visit. Theorem-only and correct; asking on VE/JV is correct there.
* Theorem's press-1 transfer to Mark.
* The two-clinic location ladder — 92% first-time, leave it.

**Gate**: rendered prompts differ only as intended; replay shows no behavioural regression; one Theorem call abandoned at read-back.

### Phase 5 — Close the coverage gap

The reason four defects reached a live number tonight.

1. Enumerate which engine paths each live line can actually reach. Northgate (`locations = 1`) structurally cannot enter the location ladder, so ~6 decision sites have **no live coverage at all**.
2. Either give Theorem a safe two-site test tenant on `latency-eval`, or add a `locations = 2` fixture clinic to the demo service. **This is the single highest-value structural change in this document** — it converts "the owner finds it on a call" into "a test finds it".
3. Add replay-based regression over the obs corpus for the paths tests cannot reach.

---

## 6. Testing protocol

Apply **all** of these, in this order, for every change.

1. **Regression test first.** Every behavioural fix ships with a test in `tests/regression/` that **fails before and passes after**. Prove the "fails before" by reverting the source file, not by assuming — a collection error is not proof of a behavioural failure.
2. **Full suite, diff the failing set** (§0.4). Never the count alone.
3. **Count literals, not references.** When collapsing duplicated strings use an AST walk over `ast.Constant`, not grep. This session: grep found 2 copies of a sentence, an AST walk found 4 — the wording was split across line breaks differently at each site.
4. **Replay against the obs corpus** for anything touching model behaviour.
5. **Phone call last**, under §0.3 rules. One call, one hypothesis, and read the log rather than trusting your ear.

### Verifying from a Render log

* `[build_info] running build <sha>` at call cleanup — the **only** deploy proof.
* `[ms_conn v3] caller intent = …` — the fix from `6f277d41`.
* `[ms_llm] situational head (…)` — which intent the hold-speech classifier saw.
* `[LAT] turn_seq=N … cache_read=… llm_ttft_ms=…` — per-turn latency. `llm_ttft_ms=-1` means **no LLM call** that turn (deterministic path).
* `[ms_tts] synthesise_chunk … text=…` — what was actually spoken, chunk by chunk.

---

## 7. Things that will waste your time if you don't know them

* **`flow.py` is frozen.** 24,820 lines; `handle_transcript()` is a single 15,734-line method. Change it only for a specific reproduced defect, with a regression test, smallest possible diff. No refactoring.
* **`FlowEngine` is bypassed on every live clinic** — all four deployments are free-form (the `[ms_conn v3]` path). A grep returning mostly `flow.py` hits has probably found dead code.
* **`connection.py` and `llm_stream.py` are CRLF.** String edits need CRLF-aware matching; a whole-file rewrite lands as a 16k-line diff. Commit **before** any before/after `git checkout` comparison.
* **`git stash` does not reliably revert here** (OneDrive locks). Back out by hand, or your baseline is a lie.
* **Never `cd X; cmd`** — use `git -C`. A failed `cd` has clobbered the primary tree before.
* **Repeated lesson, six instances and counting**: *code must never match one literal of model speech*. Before adding any phrase match, check it against `_BANNED_SENTENCE_RE` and ask how many other copies of the idea exist. Both `f89a4c7e` and `f9793204` were this defect.
* **Config keys that never reach the model** — three instances on record. A fact in `clinic.json` with no renderer branch reads as a model failure. Render via `build_system_prompt_parts` and grep before blaming the LLM.

---

## 8. Definition of done

1. D1–D6 closed, each with a regression test that fails before and passes after.
2. Failing set identical to the `f9793204` baseline, minus D6's fix.
3. Theorem reaches `lookup_patient` on a cancel in comparable time to the demo line (currently 21.1s vs 2.9s).
4. Theorem and Northgate ask the phone-confirm question in the same **kind** — with the number read back.
5. A fresh obs pull shows the judge no longer reporting "garbled clinic name".
6. Every live path has at least one automated test that can reach it, or a documented reason why not.
