# Open defects — 22 Aug 2026, 16:48

Handover state after a day of transfer/SMS/prompt work. Written to be read cold
by a new session.

**Branch heads at time of writing** (all four deployed, `autoDeploy` on):

| Branch | Head | Serves |
|---|---|---|
| `latency-eval` | `80b9e9cc` | test line **+447366263180** (loads `jv_v1` config) |
| `theorem-onboarding` | `68051280` | Mark — **+447380841468** (`theorem_v3`) |
| `vitaledge-onboarding` | `298fd13f` | Jonathan — **+447426779875** (`vital_edge`) |
| `jv_v2` | `d2abc28c` | Marcus — **+447367002651** (`jv_v1`) |

Deploy proof is `[build_info] running build <sha>` in the Render log. `/health`
returns a hardcoded `1.0.0` and will lie to you.

---

## P1 — caller-facing, reproduced

### B-90. Choosing a slot silently narrows every later search — ALL FOUR LINES

> **Susie told a caller a day had one slot when Acuity showed two.** Ground
> truth: the Acuity booking page for Wed 2 Sep shows **10:00 AM and 2:00 PM**
> both free. `CAa415c88d` (26 Aug, theorem_v3, build `f05c59f7`):
>
> ```
> 17:38:44  tool -> slot_times ["10:00","14:00"]          <-- BOTH present
> 17:38:45  Susie: "Number 1, ten in the morning. Number 2, two in the afternoon."
> 17:38:57  digit='1' -> injecting 'ten in the morning'
> 17:38:57  time_of_day_preference captured: mornings (from utterance 'ten in the morning')
> 17:39:13  tool: date_hint="morning Wednesday 2 September 2026"
> 17:39:13  result -> slot_times ["10:00"]                <-- 14:00 FILTERED OUT
> 17:39:21  Susie: "That's all we have on Wednesday the 2nd of September
>                   — just the ten in the morning."
> ```
>
> The caller had asked the most direct question available — *"or is that all you
> have that day"* — and was told yes while a 2pm sat free.
>
> **TWO bugs compounding, both pre-existing (NOT from the 26 Aug ports):**
>
> **A. A slot SELECTION is mined as a standing PREFERENCE.** Picking "ten in
> the morning" from a numbered list means "I'll take that one", not "I only do
> mornings". The DTMF path makes it certain — the injected label is *always* a
> time — but it fires on spoken selections too. The capture block scans "every
> accepted utterance" and does not exclude synthetic/injected transcripts.
>
> **B. It is never cleared.** The code says so: *"Once set this field is never
> cleared within a call."* Verified: **0 clear sites on all four branches.** One
> accidental capture poisons every subsequent availability call.
>
> **Blast radius: ALL FOUR LINES.** `connection.py` is shared; the DTMF
> injection is not clinic-gated (`theorem_v3` is only a log-string prefix).
>
> **No gate fix can touch this** — the payload itself is wrong, and Susie
> reports it faithfully. It is invisible in the transcript: her sentence is true
> about what she was handed.
>
> **Severity: P1 false refusal.** Loses bookings, and is the exact shape the
> `that_is_the_only` work has been chasing from the other end.
>
> **Fix direction (not yet written):** do not mine an injected slot-selection
> transcript for soft context, and give the preference a clearing rule when the
> caller asks a broadening question ("anything else", "what else have you got",
> "is that all"). Reproduce first — do NOT assume the DTMF path is the only door.



### B-89. Susie heard herself, and answered — FIXED 25 Aug

> `CAfcb3130c` (jv_v1). The slot was ALREADY AGREED — "yeah go on then that
> would work" — and the call ended abandoned at 168s with no booking.
>
> ```
> 21:18:45  Susie: "So that's Tuesday the 1st of September at five in the evening"
> 21:18:47  barge-in: partial="that's"        <- HER OWN WORD, off the line
> 21:18:48  barge-in #4 unqueued-final confirmed (1482ms) text='' ack='Yes, go on.'
> 21:18:52  caller: "i didn't say anything"
> 21:19:03  hang up.
> ```
>
> **`_barge_in_duration` is time in the barge-in STATE, not time the caller
> spoke.** 1482ms cleared the threshold so the B-67 unqueued-final resolver took
> its "confirmed" arm — but with `text=''` it was measuring nothing. The
> theorem_v3 echo suppressor could not help: gated on `v3_location_asked`, aimed
> at watchdog preservation, and keyed off `_tts_audio_done_at` — this echo
> arrived DURING playback.
>
> **"An empty final should never ack" is the WRONG FIX.** B-67's own call
> (`CAa0f76e2c`, VE) also ended in an empty final and there the ack is CORRECT —
> its partial was 'yeah yep', a real caller whose words STT lost. Blanket-
> refusing trades one silent call for another. Do not re-derive this.
>
> **The discriminator is the PARTIAL that started the barge-in.** "that's" is a
> contiguous fragment of the sentence in flight; "yeah yep" is not a fragment of
> "Right with you…". `_partial_is_own_speech` compares against what she was
> actually saying — data, not a phrase list. Contiguity is load-bearing: a
> caller saying "Tuesday evening" has both words in that readback but neither
> run.
>
> **Bounded** at `_MAX_ECHO_RESUMES` (2) per episode: `interrupted_tts_text` is
> snapshotted at barge-in start and NEVER cleared, so a resume re-speaks audio
> that can echo again. Past the cap it falls through to the ack, so the turn
> always has an exit. Budget resets on a real final.
>
> | Branch | Commit | Revert |
> |---|---|---|
> | `latency-eval` | `3c3fe45c` | `git revert 3c3fe45c` |
> | `jv_v2` | `f02cedae` | `git revert f02cedae` |
>
> Canonical 101 -> 101 (md5 `356af469`), 6428 -> 6438.
> `jv_v2` 103 -> 103 (md5 `40acb832`), 6239 -> 6249.
> Regression: `tests/regression/test_susie_barges_in_on_herself.py`, 10 tests.
> B-67's suite runs green alongside it.
>
> **Live check:** look for `barge-in #N was Susie's own audio (partial=... ) —
> resuming instead of acking`. It should appear on a call where you stay silent
> through a readback and NOT on one where you talk over her.
>
> **NOT ported to `vitaledge-onboarding` or `theorem-onboarding`.** Unaudited.



### B-88. A multi-day readout never updated the offer record — FIXED 25 Aug

> ### VERIFIED LIVE 25 Aug 23:38 — `CA5d91113f`, build `07629bd8`
>
> ```
> 23:38:09  slot buf: spoken options span 2 days — recorded as heard
> 23:38:25  slot buf: spoken options span 2 days — recorded as heard
> ```
>
> **Same payload that failed 16 minutes earlier.** On `f02cedae` at 23:22:43 the
> identical `available_days` — `{"date": "2026-08-31", "day_label": "Monday 31st
> August", "slot_times": ["20:15"]}` + Tuesday 1 Sept — logged
> `could not resolve`. A same-data before/after, not an inference.
>
> `recorded as heard` proves BOTH gates: the `elif _r:` arm only runs when
> resolution succeeded (gate 1), and the wording only exists because the
> cumulative record is now written (gate 2).
>
> NOT exercised: the caller switched weeks rather than asking "what else have
> you got?", so the re-offer symptom itself is still unobserved-fixed. The
> mechanism that prevents it is confirmed active.
>
> ### UPDATE 26 Aug — the first fix was NOT enough; TWO more gates
>
> `6861fd8b` recovered the TIME from the label. `CA9bd4ecf0` (build
> `f02cedae`) then showed both remaining gates in one call.
>
> **Gate 1 — the time was looked up across EVERY day at once.** The log carried
> the candidates with correct times in them, so a zero-hit was impossible; the
> only route to `None` was MORE THAN ONE hit — the deny-on-ambiguity rule.
> `flatten_bookable_slots` keys all days into one list by spoken time, and this
> clinic runs the same rota two evenings running. The rule was refusing data it
> could have told apart, because **the option names its own day** and that was
> being discarded.
>
> `prefer_day` never could have covered this: it is ONE day and a multi-day
> readout presents several. `_norm_day` drops "the"/"of" because the payload
> says "Monday 31st August" and the model speaks "Monday the 31st of August".
>
> **Gate 2 — `spoken options span 2 days` WAS `6861fd8b` working.** That line
> only appears because the options resolved; the one-day guard then discarded
> the result. A multi-day readout is a real offer, so the CUMULATIVE spoken
> record now learns from it — checked, not assumed: it is a flat set of ISO
> starts and `unspoken_remain_on_day` filters by day itself.
>
> **STILL NOT WIDENED — deliberately.** `last_offered_slots` and `slot_labels`
> are not written for a multi-day offer. `_resolve_slot_iso` indexes that record
> BY POSITION for an ordinal choice, and `slot_labels` is times-only, ambiguous
> across two days. ~20 consumers to audit; a test pins the asymmetry.
>
> | Branch | Commit | Revert |
> |---|---|---|
> | `latency-eval` | `f4a09b83` | `git revert f4a09b83` |
> | `jv_v2` | `07629bd8` | `git revert 07629bd8` |
>
> Canonical 101 -> 101 (md5 `356af469`), 6438 -> 6442.
> `jv_v2` 103 -> 103 (md5 `40acb832`), 6249 -> 6253.
>
> **A test written for the first fix asserted the OVER-BROAD behaviour** —
> `test_an_ambiguous_time_is_still_refused` demanded None for an option that
> names Thursday. Re-aimed into a pair: resolved when the day is named, refused
> when it is not.
>
> **Live check:** the discriminating line is
> `spoken options span 2 days — recorded as heard` (working) vs
> `could not resolve` (gate 1 open) vs
> `span 2 days — not recorded` (gate 2 open).


> **NOT the keypad.** The log line reads like it is:
>
> ```
> slot buf: could not resolve spoken option(s) ['Thursday 27th August',
> 'Saturday 29th August'] against available_days — offer record left unchanged
> ...
> slot map extracted on complete response (2 option(s)) — DTMF standby: {...}
> ```
>
> A keypress injects the mapped label as a **synthetic transcript** and the LLM
> handles it as speech. It never resolves against the offer record, and B-80's
> `v3_slot_map_superseded` guard already covers the stale-map case. There is no
> wrong-booking path via the keypad. That misreading cost a mis-scoped P1 —
> do not re-derive it.
>
> **The real cause is one regex.** `_OPTION_LABEL_STOP_RE` splits on an em dash
> and keeps the FIRST segment, so `"Number 1, Thursday 27th August — half past
> seven in the evening"` becomes `"Thursday 27th August"`. `_resolve_within`
> matches against a slot's `spoken` field by normalised EQUALITY and that field
> holds a TIME, so a day label can never match. All-or-nothing means nothing is
> recorded.
>
> **Why it hid:** the single-day form puts the day in the PREAMBLE, leaving each
> option a bare time. Only the MULTI-DAY form puts a day inside the option, and
> that form comes from the widened `requested_day_empty` path — hence every
> widened turn and nowhere else. Seen on three consecutive live calls.
>
> **What depended on it:** `record_spoken_slots` (the cumulative record of what
> the caller has heard — so "what else have you got?" re-offers times heard 40
> seconds earlier), and `session["last_offered_slots"]`, which
> `_resolve_slot_iso` indexes BY POSITION for an ordinal choice with no
> verification against what was spoken.
>
> **Fix:** `option_label_candidates` — per option, the whole text plus each dash
> segment, tried in order — used ONLY by the resolver. `extract_slot_options` is
> untouched so the DTMF map and its injected transcript do not move
> (`test_the_dtmf_map_is_unchanged` pins this). Ambiguity is still refused
> outright, never retried with another candidate.
>
> | Branch | Commit | Revert |
> |---|---|---|
> | `latency-eval` | `6861fd8b` | `git revert 6861fd8b` |
> | `jv_v2` | `d09c688a` | `git revert d09c688a` |
>
> Canonical 100 -> 100 (md5 `8da5c878`), 6417 -> 6429.
> `jv_v2` 102 -> 102 against its own baseline (md5 `6170f424`), 6228 -> 6240.
> Regression: `tests/regression/test_multi_day_option_labels_keep_the_time.py`,
> 12 tests.
>
> **Editing the fixtures:** a plain ASCII hyphen is NOT in
> `_OPTION_LABEL_STOP_RE` — only em dash, en dash and period. Fixtures written
> with `-` do not reproduce this and pass on the parent.
>
> **NOT ported to `vitaledge-onboarding` or `theorem-onboarding`.** Unaudited.



### B-87. Susie named a day that does not exist — FIXED 25 Aug

> ```
> caller: "um do you have any availability tomorrow tuesday"
> Susie:  "Tuesday 26th August is fully booked, I'm afraid - ..."
> ```
>
> **26 August 2026 is a WEDNESDAY.** Live on Marcus's line (+447367002651)
> twice in two days — `CAfcb3130c` (abandoned) and `CAdf057714` (booked). To a
> caller who has just named Tuesday this is wrong twice over.
>
> **The payload was right, and proves the model wrong**: the same result labels
> the 27th "Thursday 27th August" and the 28th "Friday 28th August".
> `requested_day_label` is `_spoken_day_label("2026-08-26")` = "Wednesday 26th
> August"; `SLOT_FORMATTER_SYSTEM_PROMPT` says use it verbatim and ships a
> worked example of the template. The model filled the template in and
> substituted a weekday lifted from the caller's garbled "tomorrow tuesday".
>
> **Not fixable by prompting** — it already had the string, the instruction and
> the example. `_correct_weekday_against_known_dates` does not depend on the
> model.
>
> **The weekday is corrected to the DATE, never the reverse.** The date is what
> gets booked (`_resolve_slot_iso` matches `available_days` on the date; a
> caller pressing "number 2" speaks no date at all). Rewriting the date to
> match a hallucinated weekday would move a real appointment.
>
> Deny by default: only a day+month the session already knows, from the tool's
> own `date` fields. No year inferred. Unknown or ambiguous left as spoken.
> Three wiring sites — the gate chain before stripping, the assembled slot text
> (`sanitise_response` runs per STREAMED chunk there, so a split date matches
> nothing), and `session["requested_day_iso"]` (the requested day is EMPTY in
> that branch by definition, so it is absent from `available_days`).
>
> | Branch | Commit | Revert |
> |---|---|---|
> | `latency-eval` | `6213f19a` | `git revert 6213f19a` |
> | `jv_v2` | `3d064611` | `git revert 3d064611` |
>
> Canonical 100 -> 100 (md5 `8da5c878`), passing 6390 -> 6417.
> `jv_v2` 102 -> 102 against its own baseline (md5 `6170f424`), 6201 -> 6228.
> Regression: `tests/regression/test_spoken_weekday_matches_the_date.py`, 26
> tests, all unrunnable on the parent.
>
> **Bundled with a log-only fix** (`d4430ecd` / `9e265ef8`): the
> `kept scarcity sentence` line sat BEFORE `pattern.sub`, so it fired on every
> turn the session state merely permitted a keep — nine times in `CAdf057714`,
> including on "All booked — you're in for Friday the 28th". Behaviour was
> always right; the line could not verify it, which is its only job.
>
> **NOT ported to `vitaledge-onboarding` or `theorem-onboarding`.** Both render
> dates and both could show this. Unaudited.



### B-86. Susie refused an available day — a bare weekday does not filter the sweep

> ### UPDATE 25 Aug — the fix was inert in production, and there are TWO doors
>
> **Door 1 — the gate. CLOSED.** `ecd7d60d` on `latency-eval`. Revert:
> `git revert ecd7d60d`. **Not yet ported to `jv_v2`** (Marcus, the line this
> came in on).
>
> `81da8da4`'s widen was gated on `not args.get("day_window")`, so it never ran
> whenever the model supplied a window — which is every availability call on the
> abandoned 25 Aug call. Its justification named the `requested_day_empty` path;
> that path lives inside `if not free_slots:`, which ends in an unconditional
> `return` and cannot reach the widen. The gate deferred to something that was
> never there. One deleted line. Suite 100 -> 100, failing sets byte-identical
> (md5 `8da5c878`), passing 6381 -> 6385. Regression file 8 -> 12 tests.
>
> **Door 2 — one slot suppresses the widen. CLOSED.**
> `_weekday_found` is `any(slot is on the named weekday)`, so a single slot on
> the pinned day is enough to skip the widen entirely. On the abandoned call the
> caller said "I can only do Tuesdays"; 1 September was pinned, had exactly one
> slot (17:00), and Tuesday 8 September was never examined. They asked for
> alternatives four times.
>
> **Fix:** `28245401` on `latency-eval`. Revert: `git revert 28245401`.
> Payload-only, no extra Google round trip: `day_requested_occurrences_examined`
> plus a second `guidance` branch for the found-but-one-occurrence case. The
> count is taken off the WINDOW, not off the slots — it states what was looked
> at, which is the only thing the payload can honestly assert.
>
> The date/weekday split is load-bearing in BOTH directions and is pinned by
> `test_the_narrow_guidance_separates_the_date_from_the_weekday`: "that's the
> only slot on the 1st" is TRUE and must stay sayable (it is what `e9de5eef`
> restored), while "that's all we have on Tuesdays" is about dates nobody read.
>
> Suite 100 -> 100 on canonical, failing sets byte-identical (md5 `8da5c878`),
> passing 6385 -> 6390. Regression file 12 -> 17 tests, 3 red on the parent.
>
> **DEPLOYED TO `jv_v2` 25 Aug** — Marcus, +447367002651 — as one bundle with
> door 1 and the stranded `e9de5eef`:
>
> | On `jv_v2` | From canonical | Revert |
> |---|---|---|
> | `59ab0ef8` | `e9de5eef` (that_is_the_only) | `git revert 59ab0ef8` |
> | `0cc52eb1` | `ecd7d60d` (door 1) | `git revert 0cc52eb1` |
> | `6de3cbc7` | `28245401` (door 2) | `git revert 6de3cbc7` |
>
> Roll the whole deploy back with `git revert --no-commit 6de3cbc7 0cc52eb1 59ab0ef8`.
> `jv_v2` suite 102 -> 102 against ITS OWN baseline at `7a77de9d`, failing sets
> byte-identical (md5 `6170f424`), passing 6175 -> 6201 (+26 = the ported tests).
> Port verified by CONTENT, not `git log`: the 140-line widen+payload block is
> byte-identical to canonical.
>
> `e9de5eef` had been stranded on canonical — `jv_v2` had only the raw
> `that_is_the_only` pattern at `turn_handler.py:407` and none of the guard. The
> defect that burned the most of the 25 Aug call was still live on the line it
> happened on.
>
> **Still divergent:** `receptionist_tools.py` differs from canonical by ~530
> lines beyond these fixes. Not audited. Do not assume other engine fixes have
> landed here.
>
> Door 1 does not touch this — it closes the case where the pinned date and the
> named weekday DISAGREE, where the caller's day is never searched at all.
>
> Cheapest next step is payload-only and costs no Google round trip: when
> `day_requested_found` is True but `window_examined_days` is small, the payload
> currently sends **no `guidance` at all** (guidance is set only on a miss).
> Saying "only N day(s) were examined" is what lets Susie offer "the 1st only has
> five o'clock, but I've more on the 8th".


> ## FIXED 25 Aug 2026 — and the mechanism below is the WRONG EXECUTOR
>
> **Fix:** `81da8da4` on `latency-eval`. Revert: `git revert 81da8da4`.
> **Deployed to `jv_v2` 25 Aug as `85de889f`** (revert: `git revert 85de889f`),
> pushed together with `1f6cdf59`, the SIM/digest config change.
> Regression: `tests/regression/test_named_weekday_beyond_the_search_window.py`
> — 8 tests, 5 red on the parent. Suite 100 → 100 on canonical and 102 → 102 on
> `jv_v2`, failing sets byte-identical on both.
>
> **Everything under "Mechanism" below describes a code path JV cannot reach.**
> `_WEEK_ANCHORS` and `_has_week_anchor` live inside `_check_availability_acuity`,
> and the dispatcher routes there only for `theorem`/`theorem_v2`/`theorem_v3`.
> `jv_v1` is `booking_system: "google_calendar"` on every branch including the
> repro build `4484df31`, so it takes the Google-Calendar reader instead. The
> quoted `[ms_tools] week filter bypassed` line has exactly one emit site, inside
> the Acuity reader — a jv_v1 call cannot produce it. That evidence block came
> from a Theorem-config call and was spliced in.
>
> **The real JV door**, two lines, both in the Google-Calendar path:
>
> 1. `day_window_days = int(args.get("day_window") or 7)` — the default sweep is
>    **7 days**, which holds exactly ONE occurrence of each weekday.
> 2. `_filter_tuples_by_preference` applies the weekday filter *only if it leaves
>    at least one slot*, and silently discards it otherwise.
>
> So: the one Tuesday in the window is full → the filter matches nothing → it is
> dropped → Monday and Thursday are presented → the model reads the absence of
> Tuesday as clinic state. Tuesday 1 September was **8 days out**, one day past
> the horizon. Reproduced offline against the real functions:
>
> ```
> 7-day window,  hint='Tuesday' -> ['Thursday 27th August', 'Monday 31st August']
> 14-day window, hint='Tuesday' -> ['Tuesday 8th September']
> ```
>
> **Frequency was understated.** This is not "the model picked bad arguments" —
> it is a structural 7-day horizon, so any weekday whose next free occurrence is
> 8+ days out is refused. On a busy week that is most of them.
>
> **The fix does both halves**, and they are not the same half: widen once to
> `_WIDEN_WINDOW_DAYS` before refusing, AND carry `day_requested` /
> `day_requested_found` / `window_examined_days` plus explicit `guidance`
> forbidding "unavailable" and "fully booked" when the widened search also finds
> nothing. Option (b) below was right that the honest framing is the durable
> half; option (a) was wrong that a weekday filter is behaviour-changing on every
> call — the filter already exists, it just gets thrown away.
>
> **Theorem's door is still open** and is genuinely the `_WEEK_ANCHORS` one: a
> bare weekday there still bypasses the week filter across a 30-day sweep. Lower
> severity (30 days contains four occurrences, so the filter rarely comes up
> empty) but not closed. **VE is unaffected** — it uses `_check_availability_diary`
> / `_check_availability_published`, neither of which is either door.

**P1. Not caused by the B-79/B-80/B-81/B-83 work — verified.**
**Where:** ~~`app/tools/receptionist_tools.py:2827` `_WEEK_ANCHORS`~~ — see the
correction above; that is the Theorem executor. The JV site is the
Google-Calendar path in `_exec_check_availability`.
**Superseded text follows.**
**Where:** `app/tools/receptionist_tools.py:2827` `_WEEK_ANCHORS` / the
`_has_week_anchor` bypass.
**Evidence:** `CAfcb3130c`, 24 Aug 14:39, jv_v1, build `4484df3102a6`.

```
14:39:28  day_preference captured: tuesday
14:39:31  tool: check_availability {date_hint: "Tuesday"}      ← no after_date, no day_window
          [ms_tools] week filter bypassed — no week anchor in date_hint: 'Tuesday'
14:39:33  "Tuesday isn't available at the moment, I'm afraid — but here's what
           we've got coming up — Number 1, Monday 24th August…  Number 2,
           Thursday 27th August…"
14:39:56  tool: check_availability {after_date: "2026-09-01", day_window: 1}
          → 2026-09-01: ["17:45", "18:30", "19:15", "20:00"]   ← FOUR free slots
```

**Mechanism.** `_has_week_anchor` requires "next week" / "week of" / an ISO date
/ an ordinal date / "today|tomorrow". **A bare weekday name matches none of
them**, so the week filter is bypassed and the full 30-day sweep returns
unfiltered by weekday. `_cap_presented_slots` then takes the first
`_MAX_PRESENTED_DAYS = 2` days — Monday and Thursday — and Tuesday 1 September,
though present in `available_days`, never reaches the model's speech. The model
read that absence as clinic state.

**This is the exact failure the code's own comment records**, reached through a
different door: the month-first-ordinal fix in that same block was written
because *"the model read that absence as clinic state and said 'Wednesday the
19th of August is fully booked, I'm afraid'. It was not."* Named dates were
fixed; **bare weekdays were not**, and a weekday is how most callers ask.

**`day_preference` is captured and then thrown away.** `connection.py:11487`
stores it; its ONLY consumer (`connection.py:11864`) uses it to decide whether
to arm a filler clip. It never reaches the availability tool.

**Why it is intermittent.** The model sometimes also passes `after_date` +
`day_window=1`, which *does* scope the sweep — that is why `CAb6bd961f` (13:58,
the C-1/C-2 pass) correctly reached Tuesday 1 September and this call did not.
The caller gets a correct answer or a false refusal depending on which arguments
the model happens to choose.

**Blast radius.** `_cap_presented_slots` and the week-anchor bypass are generic,
so this is not JV-specific. Per
[[ve-jv-have-no-date-filter]] VE and JV have no named-date filter at all, so the
weekday door is the *only* door there.

**Not fixed.** Two candidate fixes and they are not equivalent: (a) treat a bare
weekday as a filter — narrow and behaviour-changing on every call; (b) stop the
model asserting unavailability from the PRESENTED subset — the honest framing,
since `available_days` had the answer. Decide before coding; see
[[availability-payload-total-days-is-not-days-found]].

### B-80. The keypad still pointed at the offer before last — **FIXED on `latency-eval` only**
**Where:** `app/tools/slot_followup.py` (`_supersede_slot_map`, called from both
`apply_*_to_session`), consumed in `app/media_streams/connection.py` slot-DTMF
handler.
**Fix:** `4484df31` (`latency-eval`). Revert: `git revert 4484df31`.
**Evidence:** `CA6b90c3a2`, 24 Aug 12:24:39 — `slot map active — time_selection`
still listed all five times while the deterministic follow-up had just offered
20:00 alone. A keypress would have resolved to a time the caller heard EARLIER
and which is no longer the offer: a **silent wrong-slot booking**, strictly
worse than the keypress doing nothing.

**Mechanism.** `v3_dtmf_slot_map` is built in `_flush_slot_buf` from a NUMBERED
readout, and `_derive_slot_window` is the only thing that takes it away. The
follow-up paths in `slot_followup` speak their times directly and **unnumbered**
— they never reach `_flush_slot_buf` — so the map survives while the offer moves
on underneath it.

**MARK, do not clear — this is why the obvious fix is wrong.** The map *owns*
the slot window: `_derive_slot_window` re-derives `v3_awaiting_slot_selection`
from it every turn, and `_should_clear_slot_cache` reads its presence to decide
whether the next turn may wipe `last_offered_slots`. Popping the map would hand
the next turn permission to wipe the very input the follow-up paths open with
(`if not offered: return None`) — **re-breaking B-78**. The window stays open for
VOICE; only digit-to-label resolution is invalidated. Both facts are asserted in
`tests/regression/test_b80_stale_keypad_map_after_followup.py` (7 tests, 2 red on
the parent) because a future reader will otherwise try the clear.

One writer, two clearers (fresh map armed; window closed), one reader.
Deliberately **not** added to `_DTMF_EXPECTED_FLAGS` — that list means "the flow
is expecting keypad input", and a staleness marker there would stand the silence
watchdog down after every follow-up. A superseded press gets its own `[ms_lost]`
reason code so it stays countable apart from an out-of-range digit.

Suite 98 → 98, same set, +7 passing. **PORTED to all three live branches 24 Aug** — `vitaledge-onboarding` `dc4a7bcf`, `theorem-onboarding` `4b9e4439`, `jv_v2` `d720872d`. Failing sets byte-identical on all three.

### B-81. A caller named Lucy was recorded as "Good" and then deafened — **FIXED on `latency-eval` only**
**Where:** `app/media_streams/connection.py:2592` `_V3_NAME_CONFIRM_PATTERNS_ANCHORED` pattern 1b.
**Fix:** `be423adb` (`latency-eval`). Revert: `git revert be423adb`.
**Evidence:** `CA03ea1ce6`, 24 Aug 13:17, jv_v1, build `58319e89bc65`. Susie
said *"...so it's good you're getting it looked at."* Pattern 1b captured
`good`; `name persisted (normal path): 'Good'`. Persisting a name arms
`v3_phone_dtmf_active` unconditionally, so the caller's slot keypress
(`DTMF raw digit='2' v3_phone_dtmf_active=True`) went into the phone buffer
instead of the slot handler, and every utterance after it was binned:
`hi i'm lucy` / `right okay` / `hello` / `hello you still there` /
`hello hello hello`. `outcome=abandoned`, `dur=93s`, drop-off ping to the
owner reading `lead='Good'`.

**The fix:** require the captured word to be capitalised (`[A-Z]`, was
`[A-Za-z]`). The stopword lists already blocked the function words; what got
through were **adjectives** — good, worth, best, fine, important — which are
unbounded, so this is fixed at the matcher, not with another wordlist entry.
Same discriminator BARE pattern 2 already uses. Sentence-boundary anchoring
(the B-33 fix for 1c) was rejected: *"Right, so that's Sarah"* is a natural
readback and anchoring loses it.

**Second occurrence of B-33's shape** (`'Rehab'`, 3 Aug) through a third
pattern. Suite 107 → 98, delta is exactly the 9 new tests
(`tests/regression/test_b81_name_invented_from_so_its.py`, 15 tests).

**PORTED 24 Aug** to `vitaledge-onboarding` `dc4a7bcf`, `theorem-onboarding` `4b9e4439`, `jv_v2` `d720872d` — all now read `[A-Z]`, verified from each remote ref. `main` is untouched.

### B-82. The escape hatch from a deafened call covers ONE of the ten arm sites
**Where:** `_stray_dtmf_buffer_yields_to_speech`,
`app/media_streams/connection.py:1757` (conditions at 1804–1812). Written for
JV `CA29d50a41`, 18 Aug, whose docstring describes this failure almost verbatim.
**Evidence:** on `CA03ea1ce6` (B-81 above) the guard existed and did not fire.
**Two independent reasons, both verified by running the real functions against
the five real utterances:**

1. **Condition 3 rejects categorically.** It requires
   `session["v3_phone_dtmf_armed_speculatively"]`. That key has exactly **one
   writer**, at `connection.py:6830`, alongside the `_phone_outstanding` arm.
   `v3_phone_dtmf_active` is set `True` at **ten** sites; the name-persist arm
   that fired here (`connection.py:12096`, *"name confirmed — phone collection
   phase"*) does not set it. So the guard is unreachable from nine of the ten
   ways the call can be deafened — **nothing the caller said could have
   rescued it.**
2. **Condition 2 rejects too**, so it fails twice over.
   `_is_conversational_during_dtmf` needs **>4 words**; all five utterances
   return `False`, `"hello you still there"` being four — one short of its own
   threshold. A caller who has been deafened says *short* sentences; the
   threshold selects for the speaker who is being heard.

**Not fixed. Deliberately not tested from a unit test** — the arming lives
inside `handle_transcript`, and the only way to pin it from a unit test is to
regex this module for the branch text, which is how
`test_spec_i_keeps_cache_while_awaiting_slot_selection` passed all the way
through the B-78 bug it claimed to cover. The `_stray_...` docstring itself
warns that sibling tests locate the site with `src.index()` over the module.

**B-81 removes one trigger, not the coupling.** Any other route to a spurious
name — or to any of the other nine arm sites — still deafens the caller for
the rest of the call. Reaching a fix means either widening the flag to record
*why* DTMF was armed at all ten sites, or moving the guard off that flag.

### B-83. The record path was capitalised on 22 Aug; the live path was not — **FIXED on `latency-eval` only**
**Where:** `app/media_streams/connection.py` patterns 1e (`Of course X`) and 1f
(`Just to confirm … X`), both ANCHORED so both bypass the phase gate.
**Fix:** `1db23a26` (`latency-eval`). Revert: `git revert 1db23a26`.
**Evidence:** found while fixing B-81, by comparing the **two copies** of this
matcher in the repo:

| Copy | Char class | State |
|---|---|---|
| `app/tools/actionable_summary.py:268-269` — the call RECORD | `[A-Z]` | fixed 22 Aug, `e59f86b` |
| `app/media_streams/connection.py` — the LIVE path | `[A-Za-z]` | was not fixed |

The 22 Aug sweep enumerated six false names and capitalised both of its arms.
It never crossed to `connection.py`, so the path that merely **mislabels a
record** was hardened while the path that **arms phone DTMF** — and can
therefore deafen a live call, which is exactly what B-81 did — kept the defect.

Reproduced directly against the shipped matcher:
`"Of course darling, one moment."` → `'Darling'`;
`"Just to confirm, that's booked for Tuesday."` → `'Booked'`. **`Booked` is one
of the six the 22 Aug sweep already found.** 1f's phrasing is the real Susie
confirm shape (the `_stray_dtmf…` docstring quotes *"Just to confirm — I'm
moving your appointment to Monday the 31st"*); 1e's is plausible but unobserved.

**Two ways a capital fix could have been wrong here, both checked:**
`_INTERIM_DUPE_RE` strips none of these openers, so nothing re-capitalises the
captured word; and `join_after_head` lowers only the **first** word of a
payload, which in every ANCHORED pattern is the lead-in, never the captured
word. (This also retro-validates B-81.)

Suite 98 → 98, same set, +18 passing
(`tests/regression/test_b83_name_invented_from_of_course_and_confirm.py`).
**PORTED to all three live branches 24 Aug**, same as B-81 — `vitaledge-onboarding` `dc4a7bcf`, `theorem-onboarding` `4b9e4439`, `jv_v2` `d720872d`.

### B-84. Patterns 1a and 1c are still uncapitalised — open, deliberately
**Where:** `app/media_streams/connection.py:2564` (`Thanks X`) and `:2602`
(`Right X`).
**Evidence:** a 27-word probe through the shipped matcher survives **13 words on
each**: darling, sweetheart, good, fine, booked, sorted, done, confirmed,
absolutely, certainly, not, first, next.
**Not fixed, and that is a judgement call, not an oversight.** No realistic
Susie utterance reproduces either: 1c is sentence-anchored by B-33, so it needs
a sentence to *begin* "Right <lowercase>"; 1a's known false positives
("Thanks for calling", "Thanks very much", "Thanks ever so much") are all
stoplisted and verified still blocked. Capitalising both is a two-character
change with the same safety argument as B-83 — **the reason to hold is CLAUDE.md's
"smallest possible diff", not a belief that they are safe.** Raise if a live
call ever produces a name from either.

### B-85. The options are numbered "for keypad selection" — and the keypad is never armed
**Where:** `app/media_streams/connection.py:6700-6709` — the ONLY writer that
sets `v3_slot_dtmf_active = True`.
**Evidence:** `CAd075ea9673`, 24 Aug 13:58, jv_v1, build `1db23a26bb94` (C-3).
Susie read three numbered options. The caller pressed `1` and it was thrown
away:

```
13:58:39  slot map extracted on complete response (3 option(s)) — DTMF standby:
          {'1': 'five in the evening', '2': 'quarter to six…', '3': 'half past six…'}
13:58:56  [ms_conn] DTMF raw digit='1' v3_phone_dtmf_active=False
13:58:56  WARNING [ms_lost] reason=dtmf_digit_discarded text='1' call_total=1
13:59:11  transcript: "yeah i'll take the first slot you offered"   ← what actually booked it
```

**Mechanism.** Slot DTMF arms on a four-way conjunction; three held (map
present, stage `TIME_SELECTION`, not already armed) and the fourth did not:

```python
and "keypad" in self.session.get("last_bot_prompt", "").lower()
```

It is **fallback-only by construction** — it arms only after Susie has said the
word "keypad", which she does only when she could not understand a *spoken*
slot choice. On a first readout she never says it, so the press is discarded,
falls past the phone-accumulation gate, and is counted as lost.

**Why that is a contradiction and not just a limitation.** The live slot prompt
(`susie_system_prompt.SLOT_FORMATTER_SYSTEM_PROMPT`, selected on iteration 2 —
`switched to HAIKU + focused slot prompt`) instructs the numbering **twice**,
each time giving the same reason:

> *Keep the "Number 1, … Number 2, …" wording EXACTLY as written — it is parsed
> for keypad selection*

…and it never tells Susie to say "press 1" or mention the keypad at all. So the
system numbers the options *because* they are for keypad selection, extracts the
map, logs "**DTMF standby**" — and then does not arm. Note also that JV's main
rendered prompt says the opposite (`"no numbered list"`, 107,991 chars, contains
no "Number 1"); the numbering comes solely from the focused slot prompt.

**Blocker cleared.** This was gated behind **B-80** (stale map after a
deterministic follow-up), which is now fixed on `latency-eval` in `4484df31`: a
superseded map no longer resolves a digit. Arming at presentation time is
therefore no longer the "press books the wrong time" hazard it was — **on
`latency-eval`**. It still is on the four live branches, which have neither fix.

**Remaining design question before arming**, and it is not a small one: the
follow-up speaks its times **unnumbered**, so after a follow-up there is nothing
for a digit to refer to at all. Arming at presentation time gives the caller a
keypad for the FIRST readout and then silently takes it away one turn later.
Either the follow-up batch must be numbered too (caller-facing speech change,
and it must clear `_BANNED_SENTENCE_RE` first), or the arm must be scoped to the
turn that numbered the options. Decide that before writing code.

**Severity:** P2, not P1. The digit is counted (`lost_total=1`) and the 18 Aug
fix restores the speech watchdog on the discard path, so the caller gets a
re-ask rather than dead air, and voice selection still works — this caller
recovered by speaking. It is a dead affordance, not a broken call.

### A. A price question becomes a booking instruction
**Where:** STT / keyterms. `app/media_streams/stt_stream.py:335` `build_keyterms()`
**Evidence:** `CA8ebb258b` (22 Aug, JV config). Caller asked how much an
appointment costs. Transcript delivered `"um i'll book you an appointment"`;
partials show `"um i've got just an appointment"`. Susie set
`booking_flow_active = True` and asked `"What's the appointment for?"`. The
caller had to correct — `"no i asked how much is an appointment"` — and the
endpointer logged `ep_cutoff reason=correction`. Two turns burned.

**Contributing factor (hypothesis, not proven):** the keyterms list is **100
terms, capped at `_KEYTERMS_MAX`**, and priority 1 is clinical-screening
vocabulary. There is **no pricing or booking vocabulary in it at all** — no
"how much", "cost", "price", "appointment". Whether keyterms would have rescued
this specific garble is untested.

**Careful:** B-66 (`keyterms boost cancer not cancel`) is the precedent for
boosting a term family — and the precedent for it going wrong. Adding terms
blindly pushes clinical vocabulary off the end of a capped list.

### O. 19 seconds of unguarded dead air — **FIXED (bound corrected, re-landed)**
| Branch | Commit | Revert |
|---|---|---|
| `latency-eval` | `1969d979` | `80b9e9cc` |
| `theorem-onboarding` | `48bacb3d` | `68051280` |
| `vitaledge-onboarding` | `f5ce71ed` | `298fd13f` |
| `jv_v2` | `2ac322d9` | `d2abc28c` |

**All four live lines carry the corrected bound.** `_clamp_play_secs` is
byte-identical across all four (verified by hashing the function body, not by
`git cherry` — see the port-audit note in O2). The only inter-branch difference
anywhere near it is `latency-eval`'s `latency_timing` import, which is
branch-only by design.

**Where:** `app/media_streams/connection.py` — `_clamp_play_secs()` (module
level) called from `_send_loop`'s sentinel branch.
**Evidence:** `CA268397d43e00dd2ceaa3e2817334e7dd` (22 Aug 15:48, Theorem,
build `c28669a2aa9e`).

Turn 1's reply logged `[ms_silence] tts_finished in 26.7s`. Chunk 3 therefore
never fired a terminal `tts_finished`, **no `WATCHDOG_START` was armed after
turn 1 at all**, and the call sat silent 15:48:49 → 15:49:07 (**19 s**), rescued
only because the caller spoke unprompted. A real caller hangs up. Same shape as
`CA1747c2d9` on 6 Aug (29.1 s for a 5 s phrase).

**Confirmed mechanism:** the preceding filler's playout-end was 15:48:48.37 and
the sentinel was dequeued at 15:48:49.04, so `playout_start` was `now` — the
26.7 s was **`_tts_bytes_sent` alone**, ~190 kB charged to a chunk that should
carry ~24 kB. The clamp now reports the excess as ~41,599 orphaned bytes, which
is the figure that pins O2.

#### The first bound was inert — two unit errors, both off a log line

`80b9e9cc` bounded at **6 c/s + 5 s against a 49-character chunk**. Both halves
of that were read off `[ms_silence] tts_finished in 26.7s: "No — I'm Susie…"`,
and the log line **truncates text at 60 characters**. The real chunk is **175
characters** (three sub-chunks of 49/61/63): one `_TTS_DONE_SENTINEL` is placed
per `chunk_text`, after *every* sub-chunk of it has been synthesised, so the
text behind a sentinel is the WHOLE reply. The old ceiling was therefore 34.2 s,
the 26.7 s failure sailed under it, and **the fix was inert against the exact
call it was written for while its test went on passing.**

Corrected in `1969d979`: **10 c/s + 4 s**. Every healthy chunk measured across
the Theorem calls of 22 Aug clusters at 18.6-21.0 c/s against 6.55 for the
corrupt one, so 10.0 sits ~1.9x under the slowest real chunk and ~1.5x above the
failure. The live case clamps 26.7 s to **21.5 s**; healthy chunks are untouched.

The bound is taken on the text **as it will be spoken**, via the same two calls
`tts_stream` makes - substitutions first (a phone number expands from 11 written
characters to 47 spoken, and bounding on the written form would false-clamp the
one turn where the caller is checking eleven digits), then the `speed` scaling
(so a 0.8 readback or a call tuned to the 0.7 floor cannot silently tighten it).
On any fault both fall back **the widening way**.

**`play_secs` is per-chunk; the log line is not.** The counter is zeroed at each
sentinel, so the clamped quantity is that chunk's own audio. `sched_delay` -
what `[ms_silence] tts_finished in Ns` actually prints - is *cumulative*,
anchored to `_tts_playout_end_mono`. On a multi-chunk turn the two diverge
sharply: on `CAb1b894204b5bc2698e14de755f99d96f` the slot chunks logged
8.3 / 12.8 / 18.1 s while their real `play_secs` were 8.3 / 4.7 / 5.7 s
(13.4 / 17.1 / 18.2 c/s, all healthy). **Reading those log figures as
`play_secs` would manufacture a phantom false-clamp.** The 26.7 s case is only a
faithful `play_secs` because `playout_start` was `now` - established from the
timestamps above, not assumed.

**Verified:** 32 regression tests in
`tests/regression/test_o_impossible_play_duration.py`, built on the real
175-character chunk and on every chunk those calls actually spoke, asserting a
2x margin rather than mere inertness, and reading the shipped u-law clips so a
longer filler fails here rather than on a call. **Mutation-checked:** reverting
the constants to 6.0/5.0 fails 3 tests including `test_the_live_call_is_clamped`
- the old test could not do this, which is why the defect survived it. Full
suite **101 -> 101, failing set diffed and identical**.

**PORTED TO ALL FOUR, 22 Aug ~21:45.** Three commits per branch: recovery-path
tests, the absolute cap, and the cap resize after the verification call.

| Branch | Tip | Revert to |
|---|---|---|
| `latency-eval` | `e30d092c` | `1969d979` |
| `theorem-onboarding` | `e8089332` | `48bacb3d` |
| `vitaledge-onboarding` | `a20b9045` | `f5ce71ed` |
| `jv_v2` | `31c68c90` | `2ac322d9` |

Verified by grepping the added code and hashing the function — **not** `git
cherry`, which cannot audit port status here. All four show identical constants
(10.0 / 4.0 / 34.0), an identical `_clamp_play_secs` body hash `d9d1892f`, and
the same git blob for all three test files. Full suite per branch, failing
**sets** diffed: Theorem 105 -> 105, VE 101 -> 101, `jv_v2` 101 -> 101, all
identical.

**Verification call, 22 Aug 20:21, demo line `+447366263180`, build
`4e40dd821027`:** no `IMPOSSIBLE play duration`, no `WATCHDOG_FIRE`, every chunk
17.9-24.1 c/s. It proved non-regression only — the slot list returned two
options so the longest chunk was 97 chars, far below the ~300 where the cap
binds. **The cap is still unexercised in production.**

That call also forced a correction. The greeting ran at **17.9 c/s**, slower
than the 18.6 the cap was sized from, which moved the false-clamp threshold to
455 chars — only 1.3x the corpus max, and the corpus understates the risk
because the replay covers only `ResponseChunker` output while the ~20 direct
`tts_text_queue.put(phrase)` sites bypass it and were never measured. The cap
moved 28.0 -> 34.0, putting the threshold at 562/571/576 chars (speed
1.0/0.8/0.7). **Size a cap by the length at which it starts lying, not by the
corpus max** — that was the error.

Updating the rate broke `_MIN_SPEECH_CHARS_PER_SEC <= SLOWEST / 1.8` by
0.06 c/s. That was a proxy; the property it stood for still held, because the
headroom dominates the rate difference at every length. It is now replaced by
the invariant itself — no real chunk at any length or speed may exceed its own
bound — which also catches a cap set too LOW, as the proxy never could.

**Recovery path now covered (`59f427cb`, latency-eval, tests only).** Until
22 Aug the clamp's *arithmetic* was tested and its *consequence* was not: on
every call observed, `IMPOSSIBLE play duration` has never fired, so
clamp -> early `_delayed_tts_finished` -> watchdog armed was an unexercised path
guarding the worst failure mode in the system. `tests/regression/
test_o_clamp_recovery_path.py` (10 tests) drives the **real** `SilenceHandler`
and pins it: the live chunk clamps 26.7 s -> 21.5 s and
`_no_input_watchdog_task` goes None -> live. Mutation-verified (A clamp
reverted: 2 fail; B backstop not spawned: 1 fail; E `_restart_timer` no-op:
1 fail). Mutations C/D — killing `on_tts_finished`, killing the spec-W direct
arm — do **not** fail, because arming is layered and fails OPEN across spec-W,
the BACKSTOP arm and T-3; that is a property of the engine, not a hole.

**Two residuals recorded by those tests rather than fixed:**
1. `_ooo_force_fire` waits on `_tts_playout_end_mono + 3 s` — *the same clock
   the corruption inflated* — so the backstop inherits the inflation instead of
   correcting it.
2. ~~The bound is proportional to `len(text)` with **no absolute cap**.~~
   **ADDRESSED `4e40dd82` (latency-eval only, not yet ported).**
   `_MAX_CHUNK_PLAY_SECS = 28.0`, scaled by `1/speed`, applied as `min()` with
   the proportional bound. Sized from the corpus, not from taste: all 2858
   assistant turns replayed through the real `ResponseChunker` give 3088 chunks
   (median 97, p95 186, p99 252, p99.9 314, **max 351**); the worst real chunk
   takes 21.5 s at speed 1.0, so 28.0 clears it ~1.3x. Scaling is load-bearing —
   a flat 28 s collapses to ~1.07x at the 0.8 phone speed and would start
   cutting real speech. Caps 351 chars from 39.1 s -> 28 s and a 1277-char
   turn-as-chunk from 131.7 s -> 28 s; **ordinary chunks (<~240 chars) are
   untouched** and the live 175-char chunk still clamps to 21.5 s.
   Mutation-verified F/G/H = 3/1/5 failures.

Net: the clamp converts *stranded forever* into *recovers slowly*. ~21.5 s
before the watchdog's own window still fails the 3 s dead-air bar in CLAUDE.md
section 6. **The clamp is a bound on a wrong number; O2 is the actual fix.**

**Live-inert confirmed:** `CAb1b894204b5bc2698e14de755f99d96f` (22 Aug 18:40,
Theorem, build `48bacb3d6ac8`) ran two filler clips *and* the three-chunk slot
presentation - the worst case for the +4 s headroom - and logged **no
`IMPOSSIBLE play duration`**. The tightened bound does not fire on real audio.

**Known cosmetic defect:** the code comments say "173 characters" in two places;
the real figure is **175** (`len(LIVE_CHUNK)`). Not worth a dedicated push to
three gated clinic branches - fold it into the next engine port.

**The fix bounds the damage; it does not identify the leaking path.** The
early-return reproduction fired exactly as theorised and produced **no** byte
inflation, so that path is not sufficient to cause it. **Still open - see O2.**



### O2. Which path orphans the TTS bytes — UNPINNED
`_tts_bytes_sent` reached ~190 kB against an expected ~24 kB on `CA268397d4` and
neither known escape route (`_send_ulaw` injections, teardown drains) accounts
for it. O's clamp makes this visible rather than silent: grep the clinic logs
for `IMPOSSIBLE play duration` and the orphaned-byte figure localises it.
**Do not close O2 by reasoning about the code alone** — two sessions have now
mis-derived the interleaving of this loop from timestamps.

### P. "I'll take that as a yes" — accepted an offer, then did nothing
**Evidence:** same call, turn 2.

Susie's turn 1 ended `"…and I can put you through to Mark if you'd rather speak
to him"` — an implicit yes/no offer. The caller's next utterance came back from
STT garbled as `"hello your buggering arse ain't it"`. Susie replied:

> `"Ha — I'll take that as a yes! How can I help you today?"`

Two readings, both bad:
1. The "yes" attaches to the standing transfer offer — she **verbally accepted a
   transfer and did not transfer**. Same family as B-58 / B-62: narrating an
   action that never happened.
2. It attaches to nothing — she manufactured a cheerful affirmative out of an
   uninterpretable transcript rather than asking the caller to repeat.

Which one it is depends on what the model had in context; **unproven, and it
needs the obs turn record before anyone writes a fix.** Note the transcript was
*profane nonsense* and still did not trip any low-confidence path — related to
P1-A (a garbled transcript taken as an instruction).


---

## P2 — misses the production-ready bar

### B. Latency — `chunk_gate_ms` is now the dominant term
**Confirmed on Theorem, `CA268397d4`:** `content_ttfa_ms` 3230 / 2227 / 2662,
`chunk_gate_ms` 850 / 372 / **959**. Every turn over the 1.5 s bar; the first
turn worst, as on JV.
**Bar:** p95 caller-perceived turn latency under 1.5 s.
**Measured, 22 Aug:** `content_ttfa_ms` 1877–4042. `chunk_gate_ms` observed at
84 / 193 / 237 / 388 / 676 / 833 / 1130 / 1344 / 1387. At the top end **more
than half the delay is our own gate**, not the model. `llm_ttft_ms` runs
1134–3039. First turn of a call is consistently the worst (~3.1–3.3 s).

### B2. First-turn latency is a cold PROMPT CACHE, not a cold TCP pool
**Where:** `app/media_streams/llm_stream.py:3845` — the static system block's
`cache_control: {"type": "ephemeral"}`, which is Anthropic's **5-minute** TTL.
**Measured**, first LLM turn vs the mean of later turns on the same call:

| Call | turn 1 `llm_ttft_ms` | mean, later turns | penalty |
|---|---|---|---|
| `CAd075ea9673` 13:57 | 2333 | ~1631 | **+702 ms** |
| `CAfcb3130c` 14:38 | 2744 | ~1753 | **+991 ms** |

The code's own comment states the mechanism: *"Anthropic caches the static
prefix for 5 min — only turn 1 pays full input cost for the ~19K-token static
block."* JV's rendered prompt is **107,991 chars (~27K tokens)**.

**Why it is every call, not just the first after a deploy.** Clinic traffic is
sparse. Any call arriving more than five minutes after the previous one starts
on a cold cache, so in practice **almost every caller pays it**, on the very
first thing they say. Both calls above were an hour apart and both paid it.

**This is NOT the httpx-pool theory** parked earlier in the day. That theory is
still true and still small: each `AsyncAnthropic` owns its own pool, and
`app/main.py:279` warms `app.flows.conversation._get_client()` — which (a) only
CONSTRUCTS the client, issuing no request, and the codebase's own
`prewarm_classifier` docstring says *"constructing the client is NOT enough"*,
and (b) sits on the FlowEngine path, which is bypassed on every live clinic
([[flowengine-is-bypassed-on-every-live-clinic]]). So the main conversational
client at `llm_stream.py:2324` is warmed by nothing. Worth fixing, but it buys
hundreds of milliseconds, not the second.

**Two levers, neither applied:**
1. **Extended cache TTL** — Anthropic supports a 1-hour cache TTL. Nothing in
   the repo passes `ttl`; the default 5 min is being taken implicitly. Cache
   WRITES cost more at 1h, so this trades a per-call write premium against the
   miss.
2. **A cache heartbeat** — a minimal request carrying the same cached prefix
   every ~4 minutes refreshes the window at cache-READ price. Precedent exists:
   `prewarm_classifier` and the ElevenLabs prewarm both issue one real request
   at boot for exactly this reason. This one has to repeat, and it must use the
   SAME client and the SAME prefix, per that docstring.

**Instrumented, 24 Aug — `858ee459` on `latency-eval` only.** `[LAT]` now
carries `cache_read`, `cache_write` and `in_tok`, captured off `message_start`,
first-write-wins so they describe the same API call `llm_ttft_ms` measures.
`-1` means not observed; a real `0` (cold cache) survives as `0`.

**Read it off one call before choosing a lever.** On turn 1 expect either
`cache_read=0 cache_write=~27000` (cold — the hypothesis holds, and `cache_write`
prices what a 1h TTL would cost) or `cache_read=~27000` (warm — the hypothesis is
wrong and the remaining second is elsewhere). Later turns should read warm on
either reading.

### C. Verbosity — 6–9.8 s of speech per answer
Location answer measured at **9.1 s** and **6.3 s**; pricing **6.0 s**;
greeting **5.2–6.6 s**. Several turns then arm the T-3 nudge
(`turn answered but asked nothing`), adding another. No defect fires — this is
a design call about how long a caller should sit through an answer.

### D. The prompt asks for phrases the gates delete
**Where:** `app/prompts/susie_system_prompt.py` — **5 occurrences in the
RENDERED `theorem_v3` prompt**, 8 in the file. Present **identically on all four
branches** (not a port gap — I checked).
`"Bear with me a moment."` is listed under SHORT ACKNOWLEDGEMENTS as an
encouraged warm expression, while `_BANNED_SENTENCE_RE`
(`app/media_streams/turn_handler.py:115`, key `bear_with_me`) **strips the whole
containing sentence**. Observed firing: `[ms_gate5] removed banned phrase
(bear_with_me)` in the JV logs.
The same rendered prompt *also* lists "bear with me" under an explicitly-banned
list — so it contradicts itself as well as the gate. Wasted tokens, and a
deleted sentence is a beat of dead air.

### E. Press-1 buried in the greeting — **Theorem only**
**Third observed instance, `CA268397d4`:** greeting scheduled at **6.3 s**, and
the caller barged in over it at 2.2 s with "um" — before the press-1 offer had
even been spoken. Greeting runs **6.3–6.6 s** and the DTMF offer sits mid-sentence
(`"…to speak to Mark directly press 1, otherwise how can I help…"`). Two
observed callers ignored it and asked verbally instead. Not testable on the
test line: `jv_v1` has `call_overflow.enabled = False` and no press-1 in its
greeting. Arguably a design decision, not a bug.

### F. Barge-in teardown ordering
Teardown happens on the PARTIAL; `_BARGE_NOISE` and the TTS-resume are on the
FINAL. Partially mitigated in practice — `[ms_stt] garbage transcript: 'um'` →
`watchdog preserved` did fire correctly on 22 Aug — but a bare `"um"` still
produces `barge-in` + `WATCHDOG_CANCEL` before the noise filter runs. Known
defect with prior analysis; harmless in every 22 Aug call because TTS had
already finished.

---

## P3 — config / operational hazards

### G. Visibility on the three clinic services is UNVERIFIED
**Partially closed for Theorem, 22 Aug:** `CA268397d4` logged
`Sheets append ok tab='CallSummaries' rows=1` **and**
`[obs.store] captured … turns=8` / `judged … score=2`. **Sheets and obs are both
live on the Theorem service.** VE and `jv_v2` still unverified.
`SHEETS_ENABLED` (`app/tools/handoff.py:48`) and `OBS_CAPTURE_ENABLED`
(`app/config.py:85`) **both default to `false`**. Obs is known working on VE and
Theorem; Sheets is not verified anywhere. **Production-ready criterion 4 —
"every call produces a record; failures alert an operator the same day" — is
unproven on the clinic lines.**

### H. `GOOGLE_SERVICE_ACCOUNT_JSON` is malformed on the latency-eval service
`JSONDecodeError('Invalid \\escape: line 5 column 46 (char 182)')` — an escaped
newline in the private key. Sheets is dead on that service. Known-accepted on
the demo line; **status on the three clinic services unknown**.

### I. `TRANSFER_FALLBACK_NUMBER` is a hardcoded personal number
`app/config.py:38` — `os.getenv("TRANSFER_FALLBACK_NUMBER", "+447502211207")`.
Not in `render.yaml`, not in `.env.example`, not in `.env`, so that literal is
live on all four services. Any clinic without `transfer_phone`, and **any Twilio
number missing from `TWILIO_TO_CLINIC`** (unmapped numbers fall back to the
`demo` clinic, which has none), sends a patient there.
Mitigated 22 Aug (`2f71eef`): `resolve_transfer_target` now logs
`transfer target is the FALLBACK, not the clinic's own number`. Still routes.

### J. `EVAL_STAFF_SMS_TO` warns but cannot refuse
`app/notifications/sms.py:82`. Set on the latency-eval service — correctly, its
line loads Marcus's real config. **Confirmed unset on all three clinic services
(owner, 22 Aug).** If it is ever set on one, every owner alert, waitlist ping
and missed-transfer notice silently goes elsewhere and the clinic gets nothing.

### K. Dependencies are unpinned
`anthropic>=` once took **all four clinics down**. Every deploy is a fresh
resolution, tests cannot catch it, and a revert does not fix it.

### L. Startup banner is hardcoded "Theorem Health"
`👋 Theorem Health AI Receptionist shutting down…` logs on the JV service too.
Cosmetic, but misleading at 2 a.m.

---

## Needs a look — not yet classified

### M. `clinical_screening` orphan NEAR MISS grades Susie's own sentence
Observed repeatedly, e.g.
`orphan NEAR MISS — trauma_fracture matched 1 of the 2 evidence words needed;
NOT armed, nothing graded this turn: 'I can put you through to the clinic team
right now — shall I do that?'`
Orphan detection reads the bot's last prompt by design, so this may be correct
behaviour producing noise. **Unverified either way.** Worth ten minutes.

---

## Verification owed

*(none outstanding — item N closed, see below)*

---

## Closed today — do not re-raise

| | Fix | Verified |
|---|---|---|
| Missed-transfer net dead (no action URL) | `ccd765f` + `400a494` | **live**, `dial_status=no-answer` → clinic SMS → voicemail |
| Transfer-miss callback 403'd (host mismatch) | `8c99bcb` | **live**, `200 OK` |
| Caller texted a callback promise mid-transfer | `787e52c` | deployed |
| VE diary reader offered slots too short (B-77) | VE `4ee4f82` + refactor `a16688d` | 45-case reproduction pinned |
| Call record took its name from Susie's adverbs (`name=Now`) | `e59f86b` | deployed |
| Delivery log named a number the text never reached | `2f71eef` | **live**, log now names the real destination |
| "Reception" request answered by refusing / without disclosing | `a292c3f` (template) + `c28669a2` (Theorem) | **ALL FOUR verified live.** Theorem closed on `CA268397d4`, 22 Aug 15:49, build `c28669a2aa9e` — see N-closed |

---

## Standing traps for whoever picks this up

- **Render `_build_theorem_v3` / `build_system_prompt_parts` before asserting any
  Theorem prompt fact.** `susie_system_prompt.py` contains a large markdown block
  with an `## 9b. AI disclosure` section that is **dead** — not in the rendered
  output.
- **Prompts diverge by BRANCH, not just by clinic.** `theorem-onboarding`'s
  `theorem_v3` carries the AI-disclosure rule (112.8k chars);
  `latency-eval`'s does not (87.4k). Do not "port up" the Theorem fix.
- **Prompt-hash pins must be recomputed per branch, never copied.** The same
  clinic hashes differently on each. Three tests share
  `UNCHANGED_CLINIC_PROMPTS` from
  `tests/regression/test_b55_provisional_reschedule_closing.py`.
- **Baseline is RED and that is expected.** Diff the failing *set*, never the
  count: `latency-eval` / VE / `jv_v2` = 101, `theorem-onboarding` = 105.
- **A transfer test on the latency-eval line dials Marcus's real mobile.**
  `EVAL_STAFF_SMS_TO` protects SMS only — the dial is covered solely by
  `TRANSFER_DISABLED`.
- **`SMS_ENABLED` defaults `"false"` on `latency-eval` and `"true"` on the three
  clinic branches.** Never port that line in either direction.
