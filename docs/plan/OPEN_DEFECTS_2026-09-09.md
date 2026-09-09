# Open defects after the 2026-09-09 convergence session

**Written** 2026-09-09, overnight, working `docs/plan/HANDOVER_CONVERGENCE_2026-09-09.md`.
**Branch** `latency-eval` (the demo line only — `production` untouched).
**Baseline** `f9793204` → 98 failed / 9303 passed. **Now** 97 / 9349+.

> The one entry that left the failing set is D6's own fix. Nothing else moved,
> at any step, and each step was measured against the same `before.txt`.

---

## 1. What closed

| ID | Defect | Commit | How it was verified |
|---|---|---|---|
| **D3** | `lookup_patient` ran as a reschedule on a cancel | `ba37ea29` | regression test + full suite |
| **D4** | The watchdog armed small talk over an outstanding question | `f0d30d86` | regression test + full suite |
| **D5** | The head rotation handed back an opener the model had just used | `9cff40aa` | regression test + full suite |
| **D6** | A regression case pinned to the real clock | `e780d2ff` | the case now passes on any date |
| **D1** | The phonetic spelling of Alcester left the TTS layer | `70860840` | prompt render-diff + 1,527-line corpus replay |
| **D2** | Slot-presentation caps were engine constants | `58d5706c` | regression test + full suite |
| **N2** | `can't` was boosted over `cancel`, so a cancel was misheard | `dfd8f907` | **verified on a live call** |
| **N4** | Two wordings of one wait apology, back to back | `e2e1d859` | regression test + full suite |

### D4's unresolved question, answered

The handover said: *"Something clears or diverges between 03:28:24.46 and
03:28:30.41 and I do not know what."*

Nothing cleared. `on_question_asked` never stored the question in the first
place. `_is_question_worth_storing` ends with `t.endswith("?")`, and
`_V3_PHONE_CONFIRM_Q` ends on the instruction that qualifies its question —
`"...just say 'use this number'."` — so the predicate answered "no question
here" about a question, `_sh_w.last_question` stayed empty, `_outstanding_q_w`
was `""`, and the T-3 branch was reached by construction rather than by
judgement.

Three predicates in `connection.py` had the same shape and all three now read
the whole turn's speech. `_LOC_RUNG2_CONFIRM` is the second instance of the
same string shape and had already been worked around at the arming site with a
`v3_awaiting_use_this_clinic` special case — that special case is deliberately
left in place.

---

## 2. Two corrections to the handover, both measured

### 2.1 D2 is not "slots drip-fed two at a time" any more

The handover ranked D2 as the biggest failure cluster on `booking_error 64` and
the judge's drip-feed complaints. Both halves of that had already been fixed on
this branch before the handover was written:

* `7624b1a7` (1 Sep 10:44) — three days at two times, and single-day at three;
* `fee6e67a` (2 Sep 12:07) — a continuation says *"I also have — Number 1, …"*.

Every judge complaint about drip-feeding predates both. Since the second landed
there is **exactly one** readout complaint in the entire corpus, and it is not
about batch size. Measured 2026-09-09:

| period | readout complaints |
|---|---|
| August | 11 |
| 1–2 Sep (builds at or before `fee6e67a`) | 3 |
| after `fee6e67a` | **1** |

And more is not better: one of the 2 Sep failures is a **nine-slot** readout the
caller hung up on.

**So the caps were made clinic-configurable with the current values as
defaults, and no number was raised.** That is step 2 of the handover's Phase 3,
and steps 3–4 are deliberately not done — the evidence for them is gone.

### 2.2 The obs corpus reproduces in full

Phase 0's gate is closed. 939 calls, the four clinic counts and their date
ranges, 105/134 Theorem transcripts carrying the phonetic form, and the
failure-tag table (`booking_error 64, dead_end 42, loop 34,
caller_frustration 24, wrong_info 10`) all reproduced exactly.

So did §3.3: **104** calls reached the clinic question, and the ladder resolves
on the first answer **91%** of the time (6% rung 2, 3% rung 3) against the
handover's 92/8/3. One warning for whoever re-measures it — a rung-3 matcher
loose enough to catch `press 1` anywhere also catches the GREETING
(*"to speak to Mark directly press 1"*), which moved the split to 84/6/11 on the
first attempt. Anchor it on `press 1 for <town>`.

The conclusion is unchanged: **do not rebuild the ladder.**

---

## 3. Still open

### D8 — a caller who names an exact time is read the day's default times 🔴 NEW, and the only live readout defect left

`theorem_v3`, **7 Sep 2026 21:33**, `CA7d48a879ed6cb0554a3738dee8941380`,
judge score 3, tag `loop`. Verbatim:

```
caller : wednesday the 9th of september at 12 pm
Susie  : Wednesday 9th September — Number 1, ten in the morning.
         Number 2, eleven in the morning.
         Number 3, three in the afternoon. And I've a few others that day.
caller : no what else have you got that day
Susie  : On Wednesday 9th September I also have — Number 1, midday. …
caller : number 1 midday
```

Midday was bookable the whole time. The caller named it, was read three other
times, had to ask again, and the judge tagged the call `loop`.

**Mechanism.** `_filter_same_day_slots` → the band filter already handles this
correctly: `_has_explicit_clock` skips the coarse morning/afternoon band
precisely so a named time is never filtered out. But surviving the filter is not
the same as being *spoken*. `_cap_presented_slots` then hands the day to
`choose_presented_indices`, whose rule (B-116) is "unheard times first,
chronologically" — which has no notion of a time the caller asked for, so it
picked 10:00, 11:00, 15:00 and dropped the 12:00.

**Anchors**

* `app/tools/receptionist_tools.py:176` — `_EXPLICIT_CLOCK_RE`, already
  detects a named clock time
* `app/tools/receptionist_tools.py:190` — `_has_explicit_clock`
* `app/tools/slot_followup.py` — `_pin_accepted_index`, **the template**: a
  wrapper that forces one specific ISO back into a readout B-116 dropped,
  displacing the last chosen rather than adding to them
* `app/tools/slot_followup.py` — `choose_presented_indices`, the one owner of
  "how many, and which", four readers, **not to be widened in place**

**Fix shape.** A sibling of `_pin_accepted_index` — `_pin_requested_time_index`
— pinning the slot that matches a clock time the caller named, on the same
displace-not-add contract. The detector exists; what does not exist is a
resolver from `"wednesday the 9th of september at 12 pm"` to `12:00`, and the
requested time is not currently carried to the readout layer. Carry it on the
payload the way `band_label` and `sparse_rota_note` already are — *"hand the
decision up rather than letting the readout re-derive it"*.

**Why it is not done here.** It needs a new clock-time parser on the live
booking readout for all four clinics, and this repo's date parsing has already
produced `"September 19th" → 19 August`. Writing one overnight with no call to
verify it is the wrong trade. It is small, well-anchored and evidence-backed —
it should be the next thing done, with a phone call behind it.

### N5 — the stall itself, and the two-rung filler ladder 🟠 needs an owner decision

Five samples now, across both lines and both builds. `llm_ttft` on a turn that
performs a WRITE spans the tool round-trip, so those turns are structurally
slower — last turn of a call that cancelled: n=7, p50 2.2s, **3 over 10s**;
booking completions p50 5.3s; every other turn p50 1.6s, 1% over 10s.

The recovery, not the latency, is the fixable part. The ladder has two rungs and
stops: on the demo line at 09:29 the caller heard nothing for 14s, and at 10:02
nothing for 13s after the second phrase. N4 closes the case where the second
rung says the same thing twice, but it does not add a third rung, and
`UNKNOWN_SLOW` has no third wording to give it. **That is caller-facing copy —
an owner decision.** "Bear with me" is not available: Gate 5 strips it as a
banned phrase.

### D7 — demo-service Sheets is broken ⚪ env var, not code

Unchanged from the handover. `GOOGLE_SERVICE_ACCOUNT_JSON` is malformed on
`low-latency-joint-venture`. Re-paste it in Render; no code change, and nothing
here can do it.

### The engine's location constants are still written phonetically

Deliberate, and now harmless to the record: `_LOC_RUNG1_OPEN`,
`_LOC_RUNG2_CONFIRM` and `_LOC_RUNG3_DTMF` keep `"Awlstuh"` because the DTMF
handler and the clinic binding key off that wording, and D1b canonicalises the
obs transcript instead. Rewriting the constants would mean changing strings
several guards match on, for no gain the inverse does not already give.

`_CLINIC_Q_SIGNALS` keeps **both** spellings for one release, per the
handover's own instruction.

---

## 3b. D1's Theorem gate — CLOSED by observation, 9 Sep

The handover asked for a Theorem call confirming Alcester is still pronounced
correctly after the prompt stopped teaching the phonetic spelling. Two owner
calls to `+447380841468` on 9 Sep supplied it without needing a new one, because
**they ran the OLD build** (`f97932045fe7`) and still demonstrate the mechanism
end to end:

    obs transcript (pre-substitution) : [assistant] Alcester.
    synthesise_chunk (post-)          : text='Awlstuh.'

`connection.py` records `_obs_chunk_text` BEFORE `_apply_tts_subs`, so the model
wrote the canonical spelling and the caller heard the phonetic one. Across the
corpus that is **90 of 136** Theorem calls, on every build back to August.

So D1 does not introduce the substitution path — it makes it the ONLY path
instead of one of two. The remaining risk is the 43 prompt lines themselves,
and those were render-diffed line by line with the other four clinics
byte-identical.

## 4. Gates this session could not close

All three need a phone, and one needs the Render dashboard:

1. ~~**Phase 1** — one demo-line cancel call.~~ **CLOSED 9 Sep 09:28 and
   10:01.** `purpose="cancel"` on every lookup, no T-3 nudge over an
   outstanding question, and N2 verified: "cancel it altogether" transcribed
   correctly with no `BLOCKED` line, judge 4 against 3.
2. ~~**Phase 2** — one Theorem call.~~ **CLOSED by observation** — see §3b.
3. **D7** — re-paste the service-account JSON in Render. Still open; the
   demo-service log showed it again on both 9 Sep calls.

**Nothing here has been promoted to `production`.** Every commit is on
`latency-eval`, which serves the demo line only. Both gates are now closed, so
promotion is a decision rather than a blocker:

    git push origin origin/latency-eval:production      # revert target f9793204

Note what the three live clinics are running while it waits: `f9793204`, which
contains D1, D3, D4, D5 and N2 — including the misheard-cancel defect that cost
a live caller a `loop` this morning.

---

## 5. Phase 4 and Phase 5 — not started, and why

Phase 4 (converge Theorem onto `template_v1`) is explicitly gated in the
handover on "Phases 0–3 shipped **and stable**". Stable means live calls, which
have not happened. It is also the largest change in the document and its own
first step — extracting Theorem's facts into `clinic.json` — runs straight into
four hazards already on record (hours have four sources, the age policy had six,
`theorem_v3` does not even read `clinic.json`, and the Acuity short-circuit
means a config change may never run). Starting it on top of six unverified
commits would make a rollback ambiguous.

Phase 5 (close the coverage gap) is the handover's own "single highest-value
structural change", and it needs a decision this session cannot make: either a
safe two-site test tenant on `latency-eval` or a `locations = 2` fixture clinic
on the demo service. Both are provisioning, not code.

One thing D2's work makes cheaper: `operational.slot_presentation` is the first
piece of "how this clinic speaks" to move out of engine code and into
`clinic.json`, which is the shape Phase 4 needs for everything else.
