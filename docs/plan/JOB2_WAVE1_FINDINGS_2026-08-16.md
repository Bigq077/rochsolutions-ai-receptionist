# Job 2 Wave 1 — findings

**Clinic / dial:** `jv_v2` · `+447367002651`  
**Build:** `cf0b516` throughout  
**Calls:** A1–A10 (+ A9a retry) · sheet [`ADVERSARIAL_SESSION_2026-08-15.md`](ADVERSARIAL_SESSION_2026-08-15.md)  
**Written:** 2026-08-16  
**Also this window:** Job 1 Emma Clifton (theorem) — see §Job 1 below

---

## Wave 1 scorecard

| # | Result | Notes |
|---|---|---|
| A1 interrupter | **PASS** | |
| A2 mind-changer | **PASS** | |
| A3 rambler | **PASS-ish** | Re-asked name once; recovered. Diary STT `Danny Wellan` |
| A4 non-answerer | **PASS** | Reason asked once; FAQ digressions never re-asked reason |
| A5 mumbler | **PASS** | Short answers → CTA; no chase |
| A6 correcter | **PASS** | Diary `Stephen Marsh` (not Steve) |
| A7 time-abuser | **PASS** | “30th” = date; landed 19 Sep; duration 40 min |
| A8 impossible asker | **PASS** | Sunday closed; 8pm in-window (last=20:30); half-nine refused |
| A9a withheld book | **PASS** (retry) | First FAIL `no_audio_close`; path works with keypad |
| A9b withheld reschedule | **PASS** | Move landed; post-move UX broken (batch) |
| A10a quit @ slots | **PASS** | `outcome=abandoned` correct (no book) |
| A10b quit @ name | **PASS** | Name collected; no book; abandoned OK |
| A10c quit @ CTA | **PASS** | No book; abandoned OK |

**Headline:** the adversarial set did **not** find a diary-corrupting defect on JV. Edges are UX / barge-in / silence-handler wrong-question — real, but not “wrong booking written.”

---

## Batch 1 — fix queue (priority order)

Ship canonical-first on `latency-eval`, then port `jv_v2` (+ theorem where shared).

### B1.1 Withheld keypad has no “why” — HIGH UX · **porting → call-proof**

| | |
|---|---|
| **SID** | `CA86dfad89d61fa92c9696a5b7ecf81914` |
| **Symptom** | Jumps to “type the number on your keypad” with no explanation. Caller: “sorry what are you asking.” |
| **Fix** | Withheld line is now: *“I can't see a phone number on this call — could you type the number on your keypad? …”* (prompt CALL STATE + reschedule branch (b) + Gate 5g `_phone_question_for`). Decline/wrong-number still uses “No problem — go ahead…”. |
| **Status** | On `latency-eval`; cherry-pick to `jv_v2`; call-proof with `#31#` (hear the “can't see” beat before keypad). |

### B1.2 Slot flag survives write CTA → wrong silence re-ask — HIGH

| | |
|---|---|
| **SID** | `CAba5b162932ff73cc9c5b847f8e86aeeb` |
| **Symptom** | After move CTA + “yep”, move likely succeeded; confirm TTS barge-cut; silence then said *“Still with you — which of those days suits you?”* |
| **Cause** | `v3_awaiting_slot_selection` still true. Silence path prefers that flag over write-CTA context. Speaking the CTA does **not** clear the flag (reschedule never asks name → Spec J never closes the window). Related to B-37 (bypass only). |
| **Wanted** | (a) Clear / ignore flag when write CTA outstanding or just spoken. (b) If confirm TTS barge-cut after successful write, re-speak outcome once. (c) Post-write close: *“That’s done — anything else today? If not, take care.”* |

### B1.3 Greeting stolen by early STT — MEDIUM (recurring)

| | |
|---|---|
| **SID** | `CA731df4d682dedfdaf3611ecba3d618c7` (seen often) |
| **Symptom** | Noise / “hi” during or right after greeting → she re-asks “how can I help?” instead of finishing / progressing. |
| **Wanted** | Finish greeting (or ignore pure greetings/noise) before treating as a real turn. |

### B1.4 Phone readback supersonic — MEDIUM (recurring)

| | |
|---|---|
| **SID** | `CA8aaf2eaf3b9cd117159252d25c3fad88` |
| **Symptom** | Digit-spaced readback still rattled at full TTS speed (“Eminem mode”). |
| **Wanted** | Real pacing — short pauses or spoken groups — not another prompt-only “digit by digit” line. |

### Logged, watch / lower priority

| SID | Note |
|---|---|
| `CA6556d596…` | A3 re-asked name once — fix if it repeats |
| `CA79cfa9d6…` | A9a first attempt `no_audio_close` after booking offer — intermittent listen fault |

---

## Job 1 (same window) — Emma Clifton / theorem

| | |
|---|---|
| **Trigger** | Caller SMS callback · score 2/5 · `+447792904435` |
| **Call** | `CA3b303f92132395d10f0454ff2d37d0af` |
| **What happened** | Reschedule collapsed into `book_appointment` (duplicate risk) + cancel loop (same-id lookup reset spoken latches). |
| **Diary** | Human check: 1 Sep free, 8 Sep taken → **no patient callback needed** |
| **Fix** | `latency-eval` `ffceb94` · `theorem-onboarding` `02fd991` · call-proven on latency-eval staging (`CA8a90c9…` move PASS) |

---

## Explicitly not in Batch 1

- Live SMS flip (`SMS_ENABLED`)
- Quentin env: sheets / digest recipient (Job 3 leftovers)
- `flow.py` refactors
- Mutation Wave 2 of A1–A10 (optional later)
