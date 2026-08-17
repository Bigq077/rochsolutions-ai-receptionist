# Adversarial calls — Job 2 · session 2026-08-15

**Target:** ~10–15 today toward Quentin’s ~50  
**Clinic / dial:** `jv_v2` · **`+447367002651`**  
**Build at start:** confirm `[build_info] running build` → expect **`cf0b516`** (or newer)  
**Rules:** exact words; *(interrupt)* = talk over her; same script twice is useful; **check the diary, not the read-back**; delete every test booking; record `call_sid` + build sha.

**SMS:** leave `SMS_ENABLED` off.  
**Stop word:** say when you’re out of energy — we pause mid-wave, no pressure to finish A1–A10 tonight.

---

## Wave 1 — scripted set (A1 → A10)

| # | Persona | `call_sid` | Build | Result | Notes / diary |
|---|---|---|---|---|---|
| 1 | **A1** interrupter | `CAb0ff51e0124d977b649dce17f1c4252d` | `cf0b516` | **PASS** | |
| 2 | **A2** mind-changer | `CA2beb349ba3a4dc448ec079d5398e6bee` | `cf0b516` | **PASS** | |
| 3 | **A3** rambler | `CA6556d596d8c2c88db47027b4d7d96bd7` | `cf0b516` | **PASS-ish** | Slots/time perfect. Re-asked name; “i just told you that” → recovered. Diary `Danny Wellan` (STT). |
| 4 | **A4** non-answerer | `CA23d3de6d936fa52da330c8036d619d99` | `cf0b516` | **PASS** | Asked reason once (“what's the appointment for?”). Price / parking / Marcus → clean FAQ answers, **never re-asked reason**. Call ended before turn 5 (“me neck”); no booking. Main probe passed. |
| 5 | **A5** mumbler | `CAb7d9a8e26b45e9577d8d0572610a3d93` | `cf0b516` | **PASS** | Reached CTA with short answers (`back`, `go on then`, `no`, slot pick, name, phone yes). No repeats / chasing. Hung up at “shall I book” — no diary write. |
| 6 | **A6** correcter | `CA751fb451a511ae071cd2ee2465337146` | `cf0b516` | **PASS** | Diary `Stephen Marsh`, booked Sat 22 Aug 09:30 (`2nnqd3t…`). Name correction + phone change clean. **Delete test booking.** |
| 7 | **A7** time-abuser | `CA3c5b401045dac21ec0d3c7e492bde499` | `cf0b516` | **PASS** | “30th” treated as **date** (30 Aug); landed on **19 Sep** (not Aug). 17:15 correctly unavailable; duration asked → 40 min. STT heard name as “7 March” → blocked (nice). No booking written. |
| 8 | **A8** impossible asker | `CA6e8704ca05a2286d73beefbe196100ff` | `cf0b516` | **PASS** | Sunday closed honest; 8pm offered in-window (JV last = 20:30); half-nine refused — “latest is half past eight”. No silent substitute. |
| 9a | **A9** withheld book (`#31#`) | `CA86dfad89d61fa92c9696a5b7ecf81914` | `cf0b516` | **PASS** | Withhold OK (`caller=None`); keypad path armed; name John Smith. **UX defect:** jumped straight to “type the number on your keypad” with no “I can’t see a caller ID” — caller confused. **Fix after sweep.** |
| 9b | **A9** withheld reschedule (`#31#`) | `CAba5b162932ff73cc9c5b847f8e86aeeb` | `cf0b516` | **PASS** | Move landed. **UX/engine:** STT noise barged mid move-confirm; caller didn’t hear confirmation; silence then fired `Still with you — which of those days suits you?` while `v3_awaiting_slot_selection` still set (wrong re-ask after CTA/write). Batch after Wave 1. |
| 10a | **A10** quit at slots | `CA3b2e508e0b05965c1b1c37e798173496` | `cf0b516` | **PASS** | Hung up mid Number 1/2 (Sat 22). `outcome=abandoned` — correct (no booking). `phone` collected early; name/slot null. |
| 10b | **A10** quit after name | `CA731df4d682dedfdaf3611ecba3d618c7` | `cf0b516` | **PASS** | Name `John Smith` collected; hung up on phone confirm. No booking. `outcome=abandoned` correct. Opening: STT heard hi/hello/hi → re-asked “how can I help” ×3 before booking intent. |
| 10c | **A10** quit at CTA | `CA8aaf2eaf3b9cd117159252d25c3fad88` | `cf0b516` | **PASS** | Hung up on “shall I go ahead and book…”. No booking written. Obs `outcome=abandoned` (OK — nothing booked). User also saw `reached_confirmation`. **Phone readback zooms** (“0 0 3 3…” rattled) — batch. |

## Defects found this session

| call_sid | Symptom | Branch | Status |
|---|---|---|---|
| `CA6556d596d8c2c88db47027b4d7d96bd7` | A3: re-asked name after it was in the opening ramble; recovered after “i just told you that” | `jv_v2` | logged — fix if it repeats |
| `CA79cfa9d622616e365e39a01113c32814` | A9a withheld: “can't hear you” / `no_audio_close` after booking offer | `jv_v2` | logged (retry PASS) |
| `CA86dfad89d61fa92c9696a5b7ecf81914` | A9a withheld: keypad ask with **no reason** (no “I can’t see your number”) — abrupt, caller confused | `jv_v2` | **batch fix after Wave 1** |
| `CAba5b162932ff73cc9c5b847f8e86aeeb` | A9b: after move CTA + “yep”, silence re-ask used **slot** phrase (`Still with you — which of those days…`) because `v3_awaiting_slot_selection` still true after CTA; confirm TTS barge-cuttable; no post-move closing | `jv_v2` | **batch after Wave 1** — clear flag when write CTA spoken; harden/re-speak confirm; post-write closing |
| `CA731df4d682dedfdaf3611ecba3d618c7` | Opening: STT hi/hello during/after greeting → “how can I help” loop before intent (recurring) | `jv_v2` | **batch after Wave 1** — ignore early barge / finish greeting before treating as a turn |
| `CA8aaf2eaf3b9cd117159252d25c3fad88` | Phone readback **supersonic** (digit string with spaces still raced by TTS) | `jv_v2` | **batch after Wave 1** — slow digit pacing (pauses / spoken groups), not Eminem mode |

## Cleanup

Delete every test booking before you stop. Log Acuity/gcal IDs here if useful.

---

## Next script card

**Wave 1 complete (A1–A10).**  

Post-sweep **Batch 1** — see [`JOB2_WAVE1_FINDINGS_2026-08-16.md`](JOB2_WAVE1_FINDINGS_2026-08-16.md) · Quentin note [`JOB2_WAVE1_SYNTHESIS_2026-08-16.md`](JOB2_WAVE1_SYNTHESIS_2026-08-16.md).

1. ~~Withheld keypad: say why~~ — **`jv_v2` `8add945`** — call-proof `#31#` (expect “can't see a phone number…”)  
2. Clear `v3_awaiting_slot_selection` when write CTA spoken; harden/re-speak confirm; post-write closing  
3. Greeting: don’t let early STT barge steal the opening  
4. Phone readback: slower digit pacing
