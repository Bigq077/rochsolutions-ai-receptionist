# Call record — CA5685a2ab6b5017f0c7c1dd821768c4ce, 4 Sep 2026 09:53

theorem_v3, Redditch, 99s, 21 turns, judge score 2, `outcome=abandoned`.
Build `4eda31f3c8c9`. Caller: lower back pain with a postural shift to the
left, wanted Redditch, **as soon as possible**.

**He hung up seven seconds into a readout of appointments 13, 20 and 27 days
away, having just said he could not wait a week.**

> Distinct from `OPEN_DEFECTS_2026-09-04_MIDDAY.md` — that is northgate at
> 11:33. Two overlaps worth noting: §5 there (reason=None on a volunteered
> complaint) is §9 here, same root cause; and its "keypad never moved off the
> original three days" is the mirror image of §3 here, where the keypad moved
> and clobbered. Same seam, opposite direction.

---

## 1. 🔴 B-137 — "I can't wait a week" was answered with days further away

```
09:54:15  Offer 1 — "The earliest I have is Thursday 10th September"   (6 days out)
09:54:31  FINAL: "no i need it as soon as possible i can't wait a week"
09:54:31  day_preference captured: as soon as possible
09:54:35  [slot_followup] 1 of 4 days already offered -- leading with the 3
                          the caller has not heard
09:54:36  Offer 2 — "17th September... 24th September... 1st October"
09:54:42  hang-up
```

`choose_presented_days` leads with the days the caller has NOT heard. Right for
"what else have you got"; exactly inverted here, because the day he had heard
was the earliest — so the only day that could satisfy him was the only one
guaranteed to be dropped.

Reproduced against the pre-fix function with the call's real state:

```
PRE-FIX, caller asked for the soonest -> ['2026-09-17', '2026-09-24', '2026-10-01']
```

Byte-identical to what he heard.

**The system knew.** `day_preference` said "as soon as possible";
`check_availability` carried `date_hint="as soon as possible"`; the blocked
refetch's own payload said *"Read out ONLY the times in
first_day.slot_times_spoken"* and `first_day` was the 10th. The deterministic
multi-day builder overrode it. Nothing in the selection read any of it.

**Compounding: the rota was never mentioned and the other clinic was never
offered.** Redditch is a two-day-a-week site; Alcester runs five
(`clinic_config.py:305 location_working_hours`). All 30 Acuity lookups used
`calendarID=6579701`. The one code path that knows the other clinic exists —
`receptionist_tools.py:3135` — fires only on `no_availability`, i.e. zero slots
in 30 days. Redditch returned 20, so it stayed silent. The trigger is "did we
find anything?" when it needs to be "did we find anything IN TIME?"

**FIXED** on `fix/asap-sparse-rota`. Three parts, and the third is not
optional:

1. `caller_wants_soonest()` + a short-circuit in `choose_presented_days` — a
   soonest-request gets the EARLIEST days, not the unheard ones.
2. `acknowledge_sparse_rota()` — the sentence that makes the repeat honest and
   offers the busier clinic **as a question about its rota, never a claim about
   its slots**. Nothing has queried Alcester; promising it availability would
   hit the one caller guaranteed to be angry if it were wrong.
3. `_caller_requests_different_location()` — see §2.

---

## 2. 🔴 The offer in §1 was a dead end until this shipped

The `already_retrieved` guard (`llm_stream.py:5796`) has exactly ONE escape
hatch, `_caller_requests_different_day`. **A clinic is not a day.** Measured on
the pre-fix tree:

```
slot cache would clear? -> False    (DTMF map live => _should_clear_slot_cache stands down)
'yes please check alcester'   different-DAY request? -> False
'yeah try alcester'           different-DAY request? -> False
'yes check the other clinic'  different-DAY request? -> False
'yes please'                  different-DAY request? -> False
```

So every way of accepting the Alcester offer fell to `already_retrieved` —
*"present the existing slots"* — and Susie would have re-read the same
Thursdays she had just been told were too far out.

**Inviting a request the next guard refuses is worse than never offering.**
The offer and the predicate ship together or not at all.

---

## 3. 🔴 The keypad map was repointed mid-call — mis-book risk

```
09:54:15,861  slot map active — time_selection: {'1': 'nine in the morning',
                                '2': 'ten…', '3': 'one in the afternoon'}   <- all Thu 10 Sep
09:54:36,477  slot map active — day_selection:  {'1': 'Thursday 17th September',
                                '2': '…24th', '3': '…1st October'}
```

The second map REPLACED the first. A caller still holding "number 1" — 9am on
the 10th — would have been booked onto **17 September**. Nothing reconciles or
invalidates the superseded map, and the 10th became unreachable by any
keypress. **OPEN.** Highest mis-book risk on the call.

---

## 4. 🔴 A turn produced no speech, and Susie blamed the caller

```
09:53:39  caller: "how quick can i get in"
09:53:42  filler: "Right with you…"
09:53:43  [ms_gate5] no TTS emitted this turn (full_text=True, stop_reason='end_turn')
09:53:43  v3_location_q_active = True (clinic question detected in assistant reply)
09:53:44  "Sorry, I didn't quite catch that — could you say that again?"
```

Two defects. The model produced a complete response and none of it reached TTS.
And `connection.py:14028` arms the location question by pattern-matching the
model's TEXT, then the very next block discovers nothing was spoken — so the
state recorded "I asked which clinic" for a question that was never audible.
The watchdog then logged `loc_active=True` against the prompt *"Sorry, I didn't
quite catch that"*.

**OPEN.** The arming block needs the same `_v3_post_turn_speech` guard the
fallback block below it already uses. **No state may be armed off a reply the
caller did not hear.**

---

## 5. 🟠 Two of five slots withheld, and the two mornings are 60 minutes apart

Acuity returned `["09:00","10:00","11:00","12:00","13:00"]`. She offered
**09:00, 10:00, 13:00**. 11:00 and 12:00 exist, are bookable, never mentioned —
and the gap makes them sound taken.

Traced through `_spread()` (`slot_followup.py:1874`) and it reproduces exactly:
`first=09:00`; one non-morning part gives `13:00`; `chosen=[0,4]` is one short
of `limit=3`; the top-up computes `need=1 -> step=0.0 -> rest[0]` = **10:00**.

The top-up's own comment claims it is *"still better than filling from the
front"*. With `need==1` it fills from the front, literally — producing the
fifty-minutes-apart pair `_spread` was written on 1 Sept to eliminate.

**OPEN.** Fix: when `need==1`, take the midpoint of `rest`, not `rest[0]`.

---

## 6. 🟠 "How quick can I get in" — asked twice, never answered

09:53:39 -> "Sorry, I didn't catch that". 09:53:50 -> *"Which clinic were you
thinking of?"*. The location intercept fires on `Haiku unknown + question
detected` and swallows the question instead of answering it first. A one-clause
lead-in would have set the expectation that later collapsed the call. **OPEN.**

---

## 7. 🟠 TTS ran 11 seconds after teardown

```
09:54:42,098  stop event
09:54:42,121  cleanup … REMOVE … remaining=0
09:54:44,721  tts_finished: non-terminal chunk 6 (expected 8)
09:54:44,724  [ms_conn] mirrored to call: prefix …
09:54:53,452  tts_finished: terminal chunk 8 — silence timer starting   <- dead call
```

Playback bookkeeping, session mirroring and a **silence timer** all ran against
a connection removed 11s earlier. **OPEN.** Leaked task.

---

## 8. Latency — nothing under 3s against a 1.5s bar

| turn | ttfa | content_ttfa | llm_ttft | chunk_gate |
|---|---|---|---|---|
| 1 | 4313 | 4313 | 2517 | 1615 |
| 3 | 3618 | 3618 | 3256 | 241 |
| 6 | 3147 | **5587** | 3194 | 1383 |
| 7 | 4889 | 4889 | 2078 | 1260 |

Also on the critical path: a live `GET gov.uk/bank-holidays.json` inside the
availability lookup (09:54:15,829), no timeout visible.

---

## 9. The abandoned lead reached the clinic with nothing in it

```
Row built — outcome=abandoned name=None phone=yes dur=99s source=llm
[call_summary] pre-summary reason: collected=None session=None -> None   (x3)
```

He volunteered his complaint in his FIRST sentence. `commit_opening_reason` is
gated behind `_clinic_asks_its_own_reason_question`, and Theorem never asks
(`llm_stream.py:3150`) — correct for ASKING, wrong for a reason the caller
VOLUNTEERED. Mark gets "someone rang and left".

**Same root cause as MIDDAY §5, which is item 4 on that list.** Fix once.

---

## 10. Checked and NOT defects — do not "fix" these

* **No clinical screening armed.** Deliberate and correctly configured: Theorem
  has no `enabled` and no `screens` (`clinic_config.py:735`); Mark declined
  triage. Emergency keyword intercept is armed; nothing on this call matched.
* **"Awlstuh"** is the intentional phonetic respelling of Alcester for TTS.
* **B-31** fired (`last_bot_prompt truncated at 200 chars and lost its '?'`)
  and the `last_question` fallback caught it. Latent, not live.
* **The duplicate `check_availability`** was correctly blocked and cached.
* **"Redditch is Thursday-only"** — config says Mon + Thu, but all four Mondays
  in the window returned zero from Acuity. The engine must claim only what the
  diary supports: *"the only days I've GOT are Thursdays"*, never *"we're only
  open Thursdays"*. `acknowledge_sparse_rota` is worded on this rule.

---

## 11. Ordered, for whoever picks this up

1. **§3 keypad clobber** — mis-book risk, and it shares the readout path with
   the B-137 work already done.
2. **§4 state armed off unheard speech** — a caller was told they were unclear
   when they were not.
3. **§5 `_spread` top-up** — one line, and it hands back two real slots.
4. **§9 opening reason** — shared with MIDDAY §5, fix once for both.
5. **§7 post-teardown TTS**, **§6 unanswered question**, **§8 latency**.
