# Handover — 4 September 2026, evening

Written to carry a new session straight into the work. Read this top to bottom
before touching anything; it is the state of the world as of `9385a0d6`.

---

## 0. State in one screen

| | |
|---|---|
| Canonical branch | `latency-eval` @ **`9385a0d6`** — pushed, working tree clean |
| Production | `production` @ **`4eda31f3`** — **NOT yet promoted**, 36 commits behind |
| Suite | **97 failed / 8,261 passed / 22 skipped** — failing set byte-identical to this morning's baseline all day |
| Working tree | `C:/Users/quent/AppData/Local/Temp/claude/b127-latency-eval` |
| Demo line | **+447366263180** → clinic `northgate`, served by `latency-eval` |
| Live lines | Vital Edge, JV, Theorem — all three served by `production` |
| Revert target | `4eda31f3` — write it down before any push to production |

`git log origin/production ^origin/latency-eval` is **empty**. The promotion is
a genuine fast-forward, not a merge.

> The suite is **meant** to be red. 97 is the baseline. Never look for green —
> diff the failing SET, with digits preserved in the filter.

### The verification command, exactly

```bash
python -m pytest -q -p no:randomly --tb=no -rf > /tmp/X_full.txt 2>&1
grep -E "^FAILED |^ERROR " /tmp/X_full.txt | sed 's/ - .*//' | sort -u > /tmp/X_set.txt
diff /tmp/BASELINE_set.txt /tmp/X_set.txt
```

Check the captured count against the reported total. Earlier today a `tail -400`
silently truncated every capture and invalidated a whole morning of
"identical failing set" claims.

---

## 1. What shipped today (in commit order, newest last)

Five engine fixes plus supporting work. Every one is red-then-green proven by
**neutering the fix and watching the tests go red** — not by assuming.

| SHA | What a caller would notice |
|---|---|
| `d7097886` | Her answer to "my ankle hurts" is no longer cut off to ask when he'd come in |
| `bea61a7f` | The twenty-word rule reaches the turn that was breaking it |
| `591133d9` | Two of twelve times are no longer described as all of them |
| `57202217` | A comma no longer becomes a full stop mid-question |
| `12adacd7` | The re-ask no longer fires while the caller is drawing breath |
| `f23c5c35` | "Take your time" no longer closes a clinical screen's answer window |
| `70043f89` | The reason is no longer recorded as "i said no you may" |
| `efe39abe` | Surname read-back exposure is counted (warn-only, deliberately does not block) |
| `7eb61dd2` | "I can't wait a week" moves the offered days **closer** |
| `b24b1154` | **B-137** — a call opening with the complaint keeps it as the reason |
| `9e4dc3b3` | **B-138** — asking about another day is no longer read as accepting this one |
| `69d30b5d` | **B-139** — a pick on mid-conversation times lands; a refused time is never recorded as offered |
| `42a4bcb2` | **B-140** — no more sentence fragments after a hold phrase |

Plus the SMS cost guard (`0a2c10b3`, `1458882d`, `2ca17557`, `4d59614a`,
`12c5af8b`) and a new replay harness (`9385a0d6`).

### The three that matter most, in detail

**B-138 — `app/tools/slot_followup.py`, `_names_a_different_weekday`**

Live: `"do you have a 10 past 12 for wednesday for example"` → `caller ACCEPTED
2026-09-10T12:10` (a **Thursday**). Step 2 of `slot_accepted_by_caller` ends in
a last-resort branch that reads a one-date offer as "nothing left to be
ambiguous". Its own comment asserted the premise; the premise is false the
moment the caller names a day the offer does not hold. The guard declines only
when **no** weekday the caller named is this date's, so "saturday or thursday"
still resolves a Thursday offer. Day-of-month is deliberately **not** covered.

**B-139 — `offer_clauses` + `payload_slots_named_in`, same file**

This is B-134 (`5924a96d`) restored after being reverted this morning
(`3b88d9d4`) for two separate reasons. Hole 1 was B-138's, fixed in the
resolver. Hole 2 was that `"Wednesday doesn't have ten past twelve"` recorded
12:10 as a slot the caller had been **offered** — so they could accept, in the
next breath, the thing they had just been refused. `offer_clauses` splits on
sentence ends and on the mid-sentence contrast (`but / however / although /
though / whereas`) and drops any clause carrying an availability negator. The
**day** is still matched against the whole sentence, because "Monday's fully
booked, but I have ten past twelve" names the day only in the rejected clause.

**B-140 — `app/media_streams/llm_stream.py`, `_ORPHAN_OBJECT` / `_SEPARATOR_LEAD`**

Live: the caller heard `"Sorry, still with you —"` then
`"wednesday's availability properly for you."` The model wrote "Let me check
Wednesday's availability properly for you"; `_INTERIM_DUPE_RE` removed the
opener and left that verb's **object** standing alone. `_ORPHAN_LEAD` is the
same defect caught one word earlier and it stopped one word short. The test is
on what was **consumed**, not on what is left: an opener ending in a bare
`check`/`see`/`look at` has had its object taken away. `_SEPARATOR_LEAD` is the
exception — `[,—-]?` does not reach past a space, so a dash arrives on the
remainder and closes the clause there.

---

## 2. The three verification calls, and what they proved

All three on build `9385a0d6`, demo line, 4 Sep afternoon.

### CAc6d7a34e275e — 15:27, "rolled my ankle" + wrong-day question

**Proved working:**
- `[first_turn] opening reason committed on the live path: 'rolled my ankle'` — **B-137**. The reason survived to the summary; this morning it was `None`.
- `[ms_gate5] B-134: the stood-down sentence named 1 payload slot(s) the record did not hold (['2026-09-07T12:10:00']) — recorded them` — **B-139 firing live**. Without it the caller's "uh yeah that works" had nothing to resolve against.
- The denial sentence recorded **nothing** — B-139's negation guard held on a live call.
- Screening armed and cleared; surname `Quentin Rook` captured correctly.

**Defect found:** she contradicted herself in eight seconds —
*"I haven't got ten past twelve on the list"* then *"actually, I do have ten
past twelve"*. Judge scored it **2**, tagged `hallucination` + `booking_error`.

**Unexplained:** no `caller ACCEPTED` line, and `collected.selected_slot` /
`chosen_day` are both `null` — the system never pinned which slot was taken,
even though Susie said it aloud and reached the booking CTA. Verified offline:
given the record B-139 wrote, `slot_accepted_by_caller(session, "uh yeah that
works")` **does** return `2026-09-07T12:10:00+01:00`. So the record was right
and the pin did not happen. Candidates: `connection.py:9878` (the "non-slot
utterance during slot selection" branch, which says it falls through) vs
`connection.py:12168` (where the resolver actually runs). **Needs a focused
read — do not guess.**

### CA20a6394253eb — 15:30, "what about Wednesday specifically"

Owner's own words: *"should have said the second wednesday sentence straight
away without me having to say no as it seemed like there was only two slots."*

At 15:31:40 she answered **without calling `check_availability` at all** — no
tool call on that turn. She answered from the three-day offer, which carries
two times per day, so the caller heard two of Wednesday's **eleven**. Only
after "none of those" did it run a real lookup and produce the correct three
plus *"And I've a few others that day if none of those suit."*

**Proved working:** `caller ACCEPTED 2026-09-09T15:30:00+01:00 ('yeah half past
3 works')` — the pick pinned cleanly.

### CAe5c2f6e00d58 — 15:36, the ASAP path

**Proved working:** `day_preference captured: as soon as possible`, and on both
lookups `[slot_followup] caller asked for the soonest — leading with the 3
earliest of 6 days, not the unheard ones`. Call 4's objective met.

**Defect found — see §3.1.** Two bad sentences, both from B-125.

---

## 3. Open defects, ranked. Each anchored to file:line.

> A row without file:line is a lead, not a finding. Five for five, every
> one-line defect row carried between sessions has been mis-scoped.

### 3.1 — B-125's earliest-claim strip leaves a fragment · **P1, do this first**

`app/media_streams/turn_handler.py:1905` (`_EARLIEST_CLAIM_RE`),
`:1933` (`_EARLIEST_CLAIM_POST_RE`), `:1962` (`_strip_earliest_claim`).

Live, CAe5c2f6e00d58 at 15:37:34, the caller heard:

> *"…the very tomorrow, Saturday the 5th."*

The model wrote *"the very **earliest I have is** tomorrow, Saturday the 5th"*.
The pattern's optional prefix is `(?:[Tt]he\s+)?` and **"very" sits between the
article and the match**, so the regex matched at `earliest` and removed
`earliest I have is `, stranding "the very" in front of the date.
`very\s+first` is in the value alternation; `very earliest` is not.

Same guard, eight seconds earlier at 15:37:26, deleted a **true** claim:

```
'Saturday the 5th is tomorrow — that's the soonest we have. Would that…'
→ 'Saturday the 5th is tomorrow. Would that work, or would you like me to look a bit further ahead?'
```

Saturday 5th at 09:00 **was** the soonest. The guard judges a **day-level**
claim with **time-level** evidence: it read "the soonest" against the presented
09:50 while 09:00 sat bookable on the same day.

Both bad moments of that call trace to this one guard — the caller said "not
soon enough" three times, and the sentence that would have ended it ("today is
fully booked, the earliest I have is tomorrow") is the sentence B-125 kept
destroying. She then offered to *look further ahead* to someone asking for an
appointment now.

**Two changes, and the second is the more valuable:**
1. Absorb the intensifier in **both** patterns — `(?:very|absolute|absolutely)\s+` after the optional article — so the strip takes the whole phrase.
2. Apply the **B-140 rule**: if a strip leaves a dangling determiner or intensifier at the seam (`the`, `a`, `an`, `very`, `my`, `our`), the removal was mid-phrase — drop the sentence rather than speak the fragment. This is the part that catches the next intensifier nobody thinks of.

This is the third instance this week of *a strip whose remainder is not a
sentence* (B-140, `_ORPHAN_LEAD`'s original six in August, this).

### 3.2 — A multi-day offer presents 2 of ~12 and says nothing is hidden · **P1**

Measured from the stored `slot_offers` for both afternoon calls:

| Day | presented | bookable | `times_not_shown` |
|---|---|---|---|
| Monday 7th | 2 | 12 | **0** |
| Tuesday 8th | 2 | 11 | **0** |
| Wednesday 9th | 2 | 11 | **0** |
| Thursday 10th | 2 | 14 | **0** |

This one fact produced both the 15:29 contradiction and the 15:31 partial day.

**Do not "fix" this by recomputing `times_not_shown` at the trim** —
`receptionist_tools.py:5508-5531` is where the trim happens, but the field is
computed correctly for the **payload** at `receptionist_tools.py:463`
(`times_found_on_day − len(day_slots)`), and `session["available_days"]` holds
the **full** payload, not the trimmed one. Verified: `flatten_bookable_slots`
finds Monday's 12:10.

So `times_not_shown` is answering *"did a time-of-day band filter this day?"* —
not *"how much of this day did the caller actually hear?"*. Two readers gate
caller-facing claims on it and both ask it the wrong question:

- `app/tools/slot_followup.py:183` `_days_showing_a_filtered_view` — a trimmed day is a filtered view and is not in the set, so a missing slot is taken as evidence about the diary. **That is the 15:29 contradiction.**
- `app/tools/slot_followup.py:3830` — "positive proof of completeness" returns `True` for a day where 2 of 12 were spoken. **That is the 15:31 partial day.**

The honest measure is **spoken versus bookable**: `spoken_starts_for_offer(session)`
already holds what the caller heard. This is the "presented vs bookable" split
already recorded from B-95.

Related but distinct: at 15:31:40 the model answered "what about Wednesday" from
the projection **without calling `check_availability`**. A caller narrowing to
one named day should force a single-day lookup; the mechanism exists and fired
one turn later (`check_availability cache INVALIDATED — date_hint changed from
'next week' to 'Wednesday'`).

### 3.3 — The accepted slot is not always pinned · **P2, measurement first**

See CAc6d7a34e275e above. `collected.selected_slot` was `null` on a call that
reached the booking CTA with the slot spoken aloud. Anchors:
`connection.py:9878` and `connection.py:12168`. Read the branch order before
writing anything.

### 3.4 — Keypad clobber during a readout · **P2, deliberately held**

Shares a code path with the ASAP fix (`7eb61dd2`) that landed today. Two
changes to one readout path in a day, with a single round of calls between
them, is how a regression ships. Safe to do now that the ASAP path has been
exercised on a live call.

### 3.5 — `last_bot_prompt` truncated at 200 chars, loses its `?` · **P3**

Fired on every call today (`clinical_screening` B-31 warning). It falls back to
`last_question` correctly, so it is noise rather than damage — but it is firing
constantly and deserves a look.

### 3.6 — STT hears "10 past 12" as "a temperature" · **measured, deliberately NOT touched**

`temperature` is a boosted keyterm because it is a red-flag answer word for
northgate. **Zero occurrences across 4,795 stored caller turns.** No evidence of
frequency, and the change would weaken a safety screen's vocabulary to fix
something seen once. It needs the stored wav
(`logs/audio/<call_sid>.wav`) and a reproduction, not a guess.

### 3.7 — Known-accepted noise, do not chase

- `GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON` → Sheets is accepted-broken on the demo line.
- `ElevenLabs returned 401 on /v1/models` on prewarm → benign; the log line says so itself. Synthesis uses a different endpoint.
- `SMS_ENABLED is off — outbound SMS suppressed` → correct on the demo service.
- Latency: content TTFA ran 2.3–5.7s and readouts 16–19s. Above the 1.5s p95 bar, unchanged today, and the situational heads cover the opening (TTFA 730–815ms).

### 3.8 — Already fixed, do not fix twice

The duplicated apology (`"Sorry to hear that —"` then `"I'm sorry to hear
that —"`) is **already handled** by `_APOLOGY_HEAD_RE` in `llm_stream.py`,
including the `"Oh, sorry…"` variant. Three corpus hits exist and all predate
the fix. Measuring saved a duplicate change; do the same before writing.

---

## 4. Verification tooling — what exists and what it can actually see

### The gates (required before any slot-layer change ships)

```bash
python scripts/replay_day_picks.py      # 1a — 835 calls, day-naming turns
python scripts/sweep_slot_offer.py      # 1b — 528 generated diaries, invariants
```

**Gate 1a is blind to resolver changes.** It classifies with its own scorer and
never calls `slot_accepted_by_caller` — its 835-call output was byte-identical
with B-138 on and off, on the same day that defect booked a live caller onto
the wrong day. Do not read a clean 1a as a resolver being safe.

### The new differential (`9385a0d6`)

```bash
python scripts/replay_slot_resolutions.py
```

Calls the real resolver, on a session built by the real
`apply_offer_to_session`, from the real payload the engine stored in the
`slot_offers` column. **Two passes**, and the second is the one that matters:
pass A replays the offer as read out (always multi-day, so the one-date branch
is never reached); pass B reaches the narrowed state the way the engine does,
through `payload_slots_named_in`. 61 narrowed states, 496 resolutions.

Proven to detect both of today's fixes:

- **B-138 neutered** → exactly two lines move, both the live defect. `'…10 past 12 for wednesday…'` resolves to `2026-09-10T12:10`, a Thursday.
- **B-139's clause split neutered** → eight lines move, all one shape. `'um yeah the 10 past 12 time works'` resolves to `2026-09-09T12:10`, a time she had just refused.

Nothing else moves in either case. Run it on two checkouts and diff.

### The other replays — all byte-identical to `production` today

```bash
python scripts/replay_slot_readouts.py
python scripts/replay_situational_heads.py
python scripts/replay_hold_speech.py
python scripts/replay_screening.py
```

Note these read **post-Gate-5** stored text, so they cannot see a change to a
stripper like B-140 or B-125 either way.

### The obs corpus

```python
import os; from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import create_engine, text as _sql
e = create_engine(os.environ["OBS_DATABASE_URL"])
# NOTE: the column is start_utc, NOT started_at.
# Useful columns: transcript, slot_offers, quality_score, failure_tags,
#                 evidence, collected, build_sha, latency
```

`slot_offers` is **forward-only** and recent — 7 calls carry one, 12 offers
total. It holds `payload` (all bookable), `presented` (what was read out) and
`offer` (chunks + dtmf_map). It is the only way to replay a real offer.

---

## 5. Discipline — the rules that were earned the hard way today

1. **Neuter every fix and watch the test go red.** B-134 shipped 16 tests that stayed green when the fix was disabled — it could have shipped dead. B-135's first version would have shipped a clinical regression, caught only by checking every caller of the shared function before running the suite.
2. **Prove the harness, not just the fix.** A differential that cannot see the change it was built for is a green light that proves nothing. Neuter the fix and confirm the harness moves.
3. **Measure before writing.** Zero corpus occurrences killed the keyterm change; 4-of-821 killed a duplicate apology fix that was already done.
4. **A comment that asserts a premise is where the next bug lives.** B-138, B-139, B-140, B-125 and B-134 are all *a guard whose safety rests on a premise false in one reachable case*. When you write a comment saying "this is safe because X", go and check X.
5. **CRLF files** — `receptionist_tools.py`, `slot_followup.py`, `connection.py`, `llm_stream.py`. Scripted edits must preserve line endings and assert every anchor matched **exactly once**.
6. **Never run `tests/auto`** — it has booked 60 real Acuity appointments via plain pytest.
7. **Never `git clean -fd` in the primary worktree** — the plan docs are untracked there.
8. **`SMS_TEST_NUMBERS` must never be set on a live service.**
9. **Don't let a doc quote a SHA it invalidates by existing.** The afternoon's call sheet went stale twice in fifteen minutes before being rewritten to derive them.

---

## 6. Promotion to production

**Not yet done.** Decide deliberately.

The case for pushing now: three calls confirmed the fixes they tested, the
failing set has not moved, and every open defect in §3 is **already live on
production** — none was introduced today. Promoting fixes five real defects and
makes none worse.

The case for waiting: B-125 (§3.1) spoke broken English to a caller on the demo
line this afternoon. It is a small, well-understood fix in a family already
solved once today. One more call re-tests it **and** the ASAP path together.

**Recommendation: fix B-125 first, make one call, then promote.** It costs one
call rather than two, and B-125 is the most demo-visible defect on the list.

```bash
# Record the revert target somewhere off this machine FIRST
#   production before promotion = 4eda31f3

git fetch origin
git log --oneline origin/latency-eval ^origin/production   # read it, recognise every line
git push origin origin/latency-eval:production
```

`autoDeploy` is on — this reaches Vital Edge, JV and Theorem within minutes.
Then:

1. Watch each service's log for `[build_info] running build <sha>` and match it against `git rev-parse --short origin/latency-eval`. That log line is the **only** proof of what is running; `/health` returns a hardcoded 1.0.0.
2. **Make one real call to one live clinic line** and take it to a booking. An engine change is not verified until a live line has answered.
3. Rollback: `git push --force-with-lease origin 4eda31f3:production`

**Not in this promotion:** `SMS_ENABLED` and `APPOINTMENT_REMINDERS_ENABLED` are
per-service env vars, untouched, code defaults off. Nothing from `jv_v2`,
`vitaledge-onboarding` or `theorem-onboarding` — those are legacy and were
superseded, not merged.

---

## 7. Suggested order for the next session

1. **B-125** (§3.1) — highest value, well understood, same family as a fix already verified today. Both changes: absorb the intensifier, and add the dangling-determiner rule.
2. **One verification call** — re-tests B-125 and the ASAP path together. Say *"I need an appointment as soon as possible"*, then *"that's not soon enough"* twice. Listen for a whole sentence naming the earliest, and for her **not** offering to look further ahead.
3. **Promote to production**, then a live-clinic call.
4. **§3.2, the presented-vs-bookable split** — the biggest remaining correctness item, and the one behind two of today's three calls. Measure with `slot_offers` before writing.
5. §3.3 (pin), §3.4 (keypad clobber), §3.5 (B-31 truncation).

Companion documents: `docs/plan/CALL_SHEET_AND_PROMOTION_2026-09-04_PM.md` for
the call scripts and the promotion procedure in full.
