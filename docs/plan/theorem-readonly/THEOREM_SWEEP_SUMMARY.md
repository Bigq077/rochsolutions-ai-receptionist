# Theorem acceptance sweep — summary

**Run:** 2026-08-04, 21:04–21:57 · **Branch:** `theorem-onboarding` · **Clinic:** `theorem_v3`
**Calls made:** 7 of a planned 20 · **Stopped:** deliberately, at diminishing returns on discovery
**Detail:** `THEOREM_ACCEPTANCE_REGISTER.md` (per-call evidence, exact wording, log excerpts)

---

## Read this first

**The sweep found plenty. It certified nothing.**

Every call aborted at *"shall I go ahead and book that in?"*, exactly as the run
sheet requires for calls 1–17. So `book_appointment` **has never fired**, and no
appointment has ever been written to Mark's calendar by this branch. Cancel and
reschedule were never exercised at all.

Fifteen findings, four closed. But the three things the system exists to do —
book, cancel, reschedule — remain unproven end to end.

---

## The three clusters

The individual rows matter less than these. Each collapses to one fix.

### Cluster A — extractors read question turns as answers

| | Caller said | Susie stored |
|---|---|---|
| T-7 | "a shockwave on its **own**" | first name = `Own` |
| T-11 | "what if I rearrange **the morning** of" | timing preference = mornings |
| T-14 | "**yeah** but I want to know how many sessions" | booking assent |

Three deterministic extractors, all scraping a turn the caller framed as a
**question**, all writing into state the booking flow later trusts. T-7 can put
a junk first name on a real Acuity appointment; STT has already written a wrong
surname to Mark's calendar twice, and this needs no mishearing at all.

**One fix:** gate answer-extraction on `_transcript_is_question`
(`connection.py`, corrected in `6e6d7aa`). Do not run answer-extractors on a
turn that parses as a question.

**Highest priority of the three clusters** — it is the only one that writes bad
data, and calls 18–20 write for real.

### Cluster B — hand-maintained vocabularies dropping caller speech

| | Caller said | What happened |
|---|---|---|
| T-15 | "more so afternoons" | discarded as a meaningless fragment ✅ FIXED `ec150b7` |
| T-13 | "should I take ibuprofen, ice or heat" | classified a non-question, answered with "which clinic?" ✅ FIXED `6e6d7aa` |
| T-1 | "I don't know if you're open on Saturdays" | answer generated, then suppressed by the slot gate — **OPEN** |

Two were word-list gaps; both lists were missing the most likely thing a caller
would say. The comment on `_PURE_FILLER_TOKENS` in `connection.py` names this
failure mode and cites four prior instances (B-25, the step-8 reword, the timing
singles, B-36 cause 1). T-13 and T-15 are the fifth and sixth.

T-1 is different and still open: `connection.py:12166` drops **all** pre-slot
text once `check_availability` fires, so a direct factual answer is lost
whenever the caller asks a question in the same breath as a timing preference —
ordinary caller behaviour.

**Structural note:** widening a list is the safe direction (more reaches the
LLM; dropping is the destructive act) but it does not fix the shape. Each new
list will go stale the same way. Post-handover, these guards should key off
intent, not enumerated vocabulary.

### Cluster C — answer length

**T-5, and it is the single highest-impact finding of the sweep.**

Worst turns: **20.2 s**, **20.1 s**, 19.1 s, 15.1 s, 15.0 s, 14.8 s, 14.5 s,
14.2 s, 14.0 s, 13.6 s, 13.1 s, 13.0 s, 12.8 s, 12.6 s, 12.4 s, 12.1 s, 12.0 s…

Present in **all seven calls**. On call 5 the caller barged in on *every single*
long answer (barge-ins #1–#4 all confirmed). On call 6 they had to say *"say
that again you got cut off"*, and the replay cost another 13.1 s. Call 3 ended
`abandoned` two seconds after a ~20 s monologue finished.

This is not pipeline latency — the LLM answers in 1–3 s. It is answer length:
two short questions drawing five facts plus two competing offers.
`docs/plan` caps dead air at 3 s; **nothing caps how long Susie holds the
floor**, and it shows.

Why it ranks first for the demo: it is the only finding that affects every call,
and it is what a clinic owner notices in the first thirty seconds.

---

## Status of all 15

### Closed and verified live
| | | Commit |
|---|---|---|
| **T-4** | Caller-ID number confirmed without ever being spoken | `76cef3d` — heard on calls 6 and 7 |
| **T-10** | SMS was off entirely — branch inherited latency-eval's default | `6f664a4` — Twilio 201 on calls 4, 6, 7 |

### Closed, not yet verified live
| | | Commit |
|---|---|---|
| **T-13** | Medication question answered with "which clinic?" | `6e6d7aa` — deployed after call 6, never exercised |
| **T-15** | "more so afternoons" discarded | `ec150b7` — not yet deployed |

### Open, ranked
| | Severity | Note |
|---|---|---|
| **T-5** | high | Answer length. Every call. See Cluster C. |
| **T-7** | high | `Own` stored as a first name. Cluster A. |
| **T-0** | high | *"Are you a real person?"* → **"Yes,** I'm an AI receptionist." Cause not yet located — the turn is LLM-generated; find the rendered instruction before writing a fix. |
| **T-1** | high | Slot gate eats a caller's factual question. `connection.py:12166`. |
| **T-9** | medium | No Acuity calendar ID for `location='mark'` or `'leanne'` on any theorem variant. **Blocks named-practitioner booking — check before calls 18–20.** |
| **T-11** | medium | "the morning of" → timing preference. Cluster A. |
| **T-14** | medium | "yeah but" → booking assent. Cluster A. |
| **T-2** | medium | Two summary rows per call; on calls 4 and 6 they disagreed on outcome and on whether a phone number existed. Bites when `SHEETS_ENABLED` goes on at handover. |
| **T-3** | medium | No watchdog armed after a bare FAQ answer. Narrower than first written — the backstop exists and fires when a question is outstanding. |
| **T-8** | medium | TTS chunk split "wellbeing" → caller hears *"well. **Being** by working with…"* |
| **T-6** | low | `staff notify SMS sent` logged unconditionally, even when suppressed. Misleads the moment SMS is on. |
| **T-12** | decision | Every abandoned call now texts the caller — live since T-10. A member of the public who asks a price and hangs up gets an unsolicited text. **Owner decision, not a bug.** |

---

## Two operational items for handover

1. **`OBS_ALERT_SMS_TO` — find out whose number this is.** Every call in the
   sweep scored 3 or 4 from the obs judge, and every one fired an immediate
   operator SMS via `review_alert()` (`app/obs/alerts.py:220`). If that is
   Mark's number, he is texted after essentially every call from day one — and
   alerts that always fire get ignored, which is how a real one gets missed.

2. **`ASSEMBLYAI_USE_U35` is ON.** The recommendation before the run was to
   leave it off so a failure could be attributed to the engine rather than the
   acoustic model or the changed endpointing (`min_turn_silence=600`,
   `max_turn_silence=1280`). Every transcription-shaped finding here carries
   that caveat.

---

## What the sweep never touched

- **A real Acuity write.** `book_appointment` has not fired once.
- **Cancel.** Not one call.
- **Reschedule.** Not one call.
- **The location DTMF ladder in production.** Verified by 12 tests (`e0ca288`),
  never seen live — no call reached rung 3.
- **Named-practitioner booking.** See T-9.

**Minimum before handover: three more calls** — one booking through to the
Acuity write, one cancel, one reschedule. That is what turns "we found a lot"
into "we know it works." Ten more FAQ-shaped calls would mostly re-confirm T-5.

---

## Two things this branch taught us about itself

Worth carrying forward, because both cost real time tonight:

1. **`theorem-onboarding` descends from `latency-eval`, not from `main`.** It
   therefore carries eval-branch defaults and lacks main's live-branch fixes.
   Two instances found: `TRANSFER_DISABLED` missing (`4dcad7d`) and
   `SMS_ENABLED` defaulting off (`6f664a4`) — the latter past a comment saying
   in as many words not to port that default to live branches. **Assume a
   third exists and sweep for it.** 128 main commits have no equivalent here.

2. **Large parts of `susie_system_prompt.py` are dead text for this clinic.**
   `theorem_v3` has no `prompt_engine` key, so the `CALLER ID FIRST`, `Step 4b`
   and cancel-flow blocks never render. Three of five sites edited for T-4 were
   dead. **Always assert against the rendered prompt** — `_build_theorem_v3()` —
   never the source file, or a test passes green while the live model sees
   nothing.
