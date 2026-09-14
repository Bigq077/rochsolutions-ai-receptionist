# Known bugs — what has broken before, and what fixed it

Mined from `git log` on 2026-09-14 (1,889 commits). Ordered by **how many times
the same class was fixed** — a class fixed repeatedly is a class that will come
back, and "this was fixed in April and regressed" is the most valuable thing
this file can tell you.

**Search git before writing code.** For any module you have localised to:

```bash
git log --oneline -20 -- <path>
git log -i --grep="<symptom keyword>" --pretty='%ad %h %s' --date=short
git log -S"<the symbol you are about to change>" --oneline
```

Regression status below was checked against **`origin/latency-eval`** — the branch engine work lands on — on 2026-09-14.
**Re-check it** — that is one `grep`, and this file ages.

---

## 1. Slot selection: the caller's choice is not matched, and the state has no exit

**Fixed at least 6 times.** The highest-value class in this file.

- **Symptom:** Susie presents times, the caller answers, and she re-asks the same
  question forever. `selected_slot` stays `None`; `booking_confirmed` never sets.
  In `turn_traces`: `PRESENT_TIMES -> PRESENT_TIMES`, `handled_by=slot_ordinal_selection`,
  with `user_text_raw` **populated** — heard, not understood.
- **Root causes seen:** an `UnboundLocalError` in the ordinal path; "please"
  treated as a standalone YES so a day-change request confirmed a slot; day-escape
  intent ("any other dates") falling through to `extract:any` and auto-binding;
  a time-density gate eating the slot list.
- **Fixes:**

  | Date | Commit | What it did |
  |---|---|---|
  | 2026-04-23 | `e291bd38` | `PRESENT_TIMES`: UnboundLocalError + conservative ordinal pending-confirm |
  | 2026-04-24 | `e3a80c6f` | `SC_DAY_ESCAPE` veto before yes_patterns; removed "please" from yes_patterns; `_PT_STEPBACK`, `_PD_EXPLORATORY` |
  | 2026-06-17 | `9d944eb2` | accept part-of-day/clock words as selection candidates |
  | 2026-06-22 | `57c56645` | multi-date hint ("8th or 9th") wrongly refused |
  | 2026-07-07 | `d7456d72` | `high_time_density` gate no longer eats the slot list |
  | 2026-07-07 | `c200e7ae` | re-fetch availability after a modality switch (DEFECT-1) |

- **Regression status:** `SC_DAY_ESCAPE`, `_PT_STEPBACK`, `_PD_EXPLORATORY` all
  present in `flow.py` on `origin/latency-eval`. **Not regressed.**
- **Worth knowing:** `docs/triage/TRIAGE_2026-04-07.md` triages a suite run from
  **2026-04-07** whose dominant cluster (23 of 34 failures) is exactly this bug —
  fixed 17 days later by `e3a80c6f`. If you are handed an old results file,
  check the fix date before debugging anything.

## 2. The surname never reaches the calendar

**Fixed at least 10 times in 25 days, including one revert.**

- **Symptom:** booking lands in Acuity with a first name only, or a title, body
  part, slot word or filler captured as the name. Sometimes the surname is
  spoken back correctly and still dropped on write.
- **Root causes seen:** surname arriving as a separate short "straggler" turn and
  being discarded; a mid-name re-search wiping the capture; the booking readback
  not being treated as authoritative; lead-ins ("would be", "that's") not stripped.
- **Fixes (selected):**

  | Date | Commit | What it did |
  |---|---|---|
  | 2026-06-18 | `2730faaf` | **Revert** of the first full-name attempt |
  | 2026-06-22 | `154f52d3` | deterministically capture surname so full name reaches Acuity |
  | 2026-06-27 | `94d6e129` | keep short trailing surname (straggler guard) |
  | 2026-06-28 | `159ac4f7` | recognise surname collection as the name phase |
  | 2026-07-07 | `c4f822a0` | back-fill a late surname instead of dropping it |
  | 2026-07-07 | `3f28b98d` | capture full name from the booking readback (authoritative) |
  | 2026-07-08 | `be912f13` | block slot words as names; surname past leading filler |
  | 2026-07-10 | `ea37fbbb` | **one canonical `name_collector.py`, byte-identical per clinic** |
  | 2026-07-17 | `f959212d` | block body parts / practitioner / filler words in the fallback |

- **Regression status:** `app/media_streams/name_collector.py` is **byte-identical
  across `origin/latency-eval`, `jv-v1-onboarding`, `vitaledge-onboarding` and
  `theorem-onboarding`** (blob `c5c74efa`). The canonicalisation held.
- **Rule that came out of it:** fix name capture in `name_collector.py` only.
  The reason this class was fixed ten times is that it was being fixed per clinic.

## 3. ASK_LOCATION thrash — fixed and reverted four times in eight days

- **Symptom:** two-clinic callers loop on "which clinic", get bound to the wrong
  site silently, or get a re-ask where a silent bind was expected.
- **History:** `61476d14` (04-16 restore) → `c54639d8` (04-19 revert) →
  `b8d936e3` (04-20 revert) → `87122fa5`, `c27d8dac`, `5350394d`, `007b06ca`,
  `0d5bd9d7` (all 04-24) → `a86453f3` (04-24 revert of the thresholds).
- **What to learn:** this is what tuning a threshold without a reproduction looks
  like. Five fixes and three reverts in nine days, converging on
  *asymmetric thresholds + a pending-guess confirmation band*.
- **Before touching `location_resolver.py`:** write the failing scenario first.
  This module has already consumed a week of thrash.
- **Also:** single-site clinics must never reach it — `d1168184` (06-26) suppresses
  the gate on **all** paths. Check that guard before adding another.

## 4. Dead air and stranded TTS

- **Symptom:** silence over 3 s with no filler; Susie never resumes after a
  barge-in; the watchdog re-asks before she has finished speaking.
- **Root causes seen:** `_tts_playing` left stranded so the silence nets
  deadlocked; out-of-order TTS chunks leaving a sequence gap; the watchdog timing
  measured from the wrong instant.
- **Fixes:** `006a367e` (06-17) → `553a68a6` (**reverted next day**) → `5ea03f1f`
  (06-18, recover at real audio-end rather than a blind 30 s) → `beb6e998`
  (06-22, seq gap) → `6036833a` (06-12, cumulative playout scheduling) →
  `fd7df888` (06-19, strand when clinic resolved mid-booking).
- **What to learn:** the first barge-in fix was reverted within 24 hours. Timing
  changes here are the easiest place in the codebase to trade one silence bug for
  another. Measure both directions before and after.

## 5. Spurious "didn't quite catch that"

- **Symptom:** Susie re-asks a question the caller answered clearly.
- **Root causes seen:** the gate-5 filter over-dropping (`b11f6778`, 06-18); a
  slot-stage guard eating genuine FAQs and driving a reprompt loop to abandonment
  (`8cb00e0b`, 06-18); clipped/filler finals in choice states not being tolerated
  (`a75e7adf`, 04-23); the first-turn greeting re-ask firing at ~10 s instead of
  4.5 s (`44645911`, 07-14).
- **Trap:** a *single* "didn't quite catch" in a test transcript is normal — the
  harness's injected audio often misses the first endpoint. A **repeating** re-ask
  on the same question is the real signal. Do not chase the single one.

## 6. Wrong service or modality booked

- **Symptom:** the appointment exists but is the wrong thing — in-clinic when the
  caller asked for a home visit, or a service that was never offered.
- **Fixes:** `83acbf6e` (07-01, BUG-5: book the modality actually chosen),
  `bd684608` (07-07, default in-clinic, remote strictly opt-in), `faa72b89`
  (07-08, reconcile service to the availability that was checked), `c200e7ae`
  (07-07, re-fetch availability after a modality switch).
- **Severity:** this is a severity-1 class — the caller believes a booking exists
  and it is not the one they agreed to.

## 7. Booking readback drops the slot, name or date

- **Symptom:** the final confirmation names the wrong date, or omits the surname
  or location that was already captured.
- **Fixes:** `b65f388f` (DEFECT-3, persist confirmed slot into readback),
  `14f2e25d` (enforce confirmed date in the spoken readback), `52695499`
  (BUG-14, forced readback injects known name + location), `a3b9b31f`
  (lock readback wording, fix the post-yes filler spiral).
- **Note:** `3f28b98d` makes the **readback authoritative** for the name. If you
  change readback text, you are changing what gets written to Acuity.

## 8. NameError / UnboundLocalError shipped to production

**Five occurrences, two labelled "ship-blocker".**

| Date | Commit | Where |
|---|---|---|
| 2026-04-23 | `e291bd38` | `flow.py` PRESENT_TIMES ordinal path |
| 2026-06-08 | `191cec38` | `llm_stream.py` `_static_prompt`/`_dynamic_prompt` |
| 2026-06-26 | `448e6585` | `receptionist_tools.py` `booked_label` — **booking failed** |
| 2026-07-07 | `2c10a88f` | `llm_stream.py` slot-locked availability guard (ship-blocker) |
| 2026-07-11 | `8a91cd43` | `connection.py` `_V3_SLOT_LEAD_WORDS` deleted, then restored |

- **What to learn:** every one is a name that existed, was removed or never
  defined on one path, and was not caught because the path is behind a condition
  no test reaches. In files this size, deleting a symbol is not a local change.
  After removing or renaming anything in `flow.py` or `connection.py`:

  ```bash
  grep -rn "<symbol>" app/ | grep -v __pycache__
  python -c "import app.media_streams.flow, app.media_streams.connection"
  ```

- `1fd7b20e` (07-14) is the same shape in a different disguise: literal braces in
  an f-string in `get_system_prompt`.

## 9. Spoken reasoning and internal labels leaking to the caller

- **Symptom:** the caller hears Susie's internal deliberation, a CALL STATE
  label, or a duplicated FAQ call-to-action.
- **Fixes:** `9e4ec420` (GF6 reasoning leak), `61a2fdb2` (strip internal CALL
  STATE labels), `44c3f70f` (reasoning-leak hardening), `25b5321f` (reschedule:
  look up once, no spoken reasoning), `b8ebad5f` (deterministic strip of repeated
  FAQ booking CTAs), `1615c55c` (align v3 leak sources to canonical ack).
- **Where:** the deterministic strips live in the flow/prompt boundary, not in
  the prompt alone. `78183338` reverted the prompt-only attempt and kept the
  deterministic strips — prompt instructions did not hold; code did.

---

## Meta-lesson from the history

Three patterns repeat across all nine classes:

1. **Fixed per clinic, so fixed many times.** The cure was a canonical shared
   module (`name_collector.py`). Apply the same instinct: if your fix would have
   to be repeated on another branch, it is in the wrong place.
2. **Tuned without a reproduction, so reverted.** ASK_LOCATION and the barge-in
   fixes. Write the failing scenario first — it is also what tells you the fix
   worked.
3. **Prompt-only fixes did not hold; deterministic code did.** `78183338` is the
   clearest instance. If the defect must never happen, do not ask the model.
