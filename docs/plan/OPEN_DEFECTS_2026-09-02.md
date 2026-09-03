# Open defects — 2 Sep 2026, re-anchored against `production` @ `68ef771d`

Supersedes §5 of `BRANCH_PARITY_AND_OPEN_2026-08-27_EVENING.md`.
Updated end of day 2 Sep: D3 shipped, B-127 opened, two rows corrected.

**Why this file exists.** `CLAUDE.md` §7 points at
`docs/plan/OPEN_DEFECTS_HOLD_AND_SLOTS_2026-09-01.md` and calls it "the live
defect list — start here". **That file does not exist.** This is the fourth time
`CLAUDE.md` has been stale. Its §7 link still needs repointing at this file —
not done, because editing `CLAUDE.md` was not asked for.

**Method.** Every row was re-read against the code and filed under what the grep
actually showed, with a `file:line` anchor. **A row with no anchor is a lead, not
a finding.** Carried one-liners have been mis-scoped five times out of five —
and twice more today, both recorded below in §3.

**Topology.** All three clinics run ONE branch (`production`) and differ only by
Render env vars and `clinic.json`. "JV only" / "Theorem only" no longer mean
"absent elsewhere".

---

## 1. CLOSED since 27 Aug — do not carry these forward again

| Row (as written 27 Aug) | Actual state |
|---|---|
| **P3 — dependencies unpinned** | **FIXED.** `requirements.txt` opens with a PINNING POLICY; every dep is `==`; **0** unpinned. Records the 21 Aug `anthropic>=0.40.0` → `1.0.0` outage that took all four clinics down. |
| **Theorem — duplicated `minimum_age_years: 7`** | **FIXED.** One assignment, `app/clinic_config.py:475`. |
| **VE — bare-weekday widen missing on the diary path** (*top* item on 27 Aug) | **FIXED.** `_check_availability_diary` has its own widen — *"This is that widen, written for the diary"*, `app/tools/receptionist_tools.py` ~6660. |
| **JV — `confirmation_sms_sent = True` unconditional at `receptionist_tools.py:7251`** | **DOES NOT REPRODUCE.** `:7257` is the VE *provisional* path; the assignment is deliberate (`# never send the caller a "confirmed" text`) with a provisional-worded caller text below it. **Re-scope or drop; do not "fix" this line.** |

Four of six carried rows were stale. That is the argument for anchoring.

---

## 2. SHIPPED TODAY

`production` fast-forwarded `0c0f60a1` → `68ef771d`. Revert targets in order:
`0c0f60a1`, `b6ac2fd2`.

| Commit | Fix | Evidence |
|---|---|---|
| `2f3f49e7` | weekday pick not banked as a time filter | live call |
| `6e6c1250` | one booking step per turn | live call |
| `31263bbc` | Gate 5g/5g-b re-anchored onto `slots_presented` (dormant ~1 month) | live call |
| `b6ac2fd2` | `"20 past 12"` resolves against `"twenty past twelve"` | live call 16:44:29 |
| `68ef771d` | **D3** — booking ask without a question mark | 24 tests + no-regression call |

All verified on **northgate**, the demo line. **Theorem short-circuits to the
Acuity executor, so its booking path has NOT been exercised by any of these.**
One Theorem call is the outstanding verification for the whole batch.

**D3's decision, recorded so it is not re-litigated.** Option B — generating the
post-pick questions and never reading the model's phrasing — was rejected, and
*not* on size. `_v3_try_persist_name` learns the first name only by scanning
Susie's own acknowledgement; the caller's utterance yields a surname. A
generator never produces that acknowledgement, so the name is never learned and
the step is asked forever (`CA041352eb`: name given three times, hung up on the
fourth ask). **Option B is blocked on fixing name capture.**

---

## 3. Two corrections to what was reported earlier today

**A3 — surname never read back: DID NOT REPRODUCE. My "third live instance" was
wrong.** On `CA91020004` the surname *was* read back before the write:

```
16:44:58  name upgraded from booking readback: 'Quentin Rock'
16:44:58  TTS: "So that's Quentin Rock, Wednesday the 9th of September at tw…"
16:44:58  TTS: "shall I go ahead and book that in?"
```

The earlier call (`CA480576e5`) ended at the phone question — the caller hung up
before the readback stage — so it was evidence of nothing. An early hang-up was
read as a defect. **A3 drops down the list** until seen on a call that actually
reaches the readback.

**B-31 — the warning is the fix WORKING, not a failure.** It fired three times
on `CA91020004`, and each line ends `falling back to last_question for orphan
matching (B-31)`. That fallback is `c69eb61`. Screening survived all three
times. The residual is only that a 200-char cap still truncates mid-sentence.
Options: raise the cap, or truncate on a sentence boundary so the terminator
survives. **Low priority, and not the P1 it looked like.**

---

## 4. B-127 — barge-in destroys an answer and speaks the wrong sentence next

**NEW, and the most user-visible thing open.** `CA91020004728883f51fa90e325acb7ebc`,
northgate, 2 Sep 16:43, build `68ef771d`. Reported as *"the answer to my ankle
hurting gets stopped mid sentence to ask when you are coming in"*.

**It is not STT.** Every transcript in that call is correct.

```
16:43:17.370  synth chunk 1  "An ankle that's been giving you trouble —"       41 chars
16:43:17.563  synth chunk 2  "a rolled or sprained ankle can leave things…"  198 chars ≈12s
16:43:17.961  synth chunk 3  "Do you have a preference for when you'd like…"  56 chars

16:43:23.135  barge-in: partial='okay' — playback-only window
              barge-in start: interrupted_text="Do you have a preference…"
16:43:24.514  barge-in #1 carried no words (partial='okay') — speaking
              "Do you have a preference for when you'd like to come in?"
```

~20 s queued; teardown landed **5.2 s in**, inside chunk 2. It discarded the rest
of chunk 2 *and* chunk 3, then spoke chunk 3 — a sentence the caller had never
reached.

### Three layers

| Layer | Defect | Anchor |
|---|---|---|
| **L1** | Teardown runs on the **partial**; the "carried no words" filter runs on the **final**, 1.4 s later. Audio already gone. | [connection.py:16158](app/media_streams/connection.py:16158) vs [:16598](app/media_streams/connection.py:16598) |
| **L2** | `_current_tts_text` is set as each sub-chunk goes to **synthesis**, so in a playback-only window it holds the *last* chunk. `last_question` is likewise synthesis-anchored. | [connection.py:15176](app/media_streams/connection.py:15176) |
| **L3** | Recovery resumes `last_question` on a stated assumption — *"by construction equal to the chunk just spoken"* — true for single-chunk turns, false for multi-chunk ones. | [connection.py:16567](app/media_streams/connection.py:16567) |

### Options — settled, do not re-open

**Option 5 (suppress teardown on a backchannel partial): CLOSED.** It was
proposed, investigated, and rejected on evidence. Two independent reasons:

1. The 11 Aug note already checked four live calls where `'um'`, `'and'`,
   `'bye'`, `'i'` were each the leading edge of a **genuine** interruption.
2. **This call convicts the specific list (`yeah`/`okay`) that looked safer.**
   `'yeah'` was the leading edge of a real interruption **twice** —
   16:43:03 → *"yeah i'd like to book an appointment"*, 16:44:06 → *"yeah could
   you tell me the rest of the slots you have that day"* — against **one**
   wordless `'okay'`. The list would have delayed two real barge-ins to save one.
3. `fbadd2f2` (B-107) closes it permanently: a caller whose words STT dropped and
   a garbled echo leave **identical evidence** at the partial. *"No test over
   that data separates them."* Trigger-side discrimination is **undecidable**,
   not merely risky.

**Option 4 (two-phase teardown / defer the Twilio `clear`): REJECTED for now.**
Only option that prevents the loss rather than repairing it, but it is
trigger-side, and this family has failed in that direction three times. Susie
would talk over a genuine interrupter for up to 500 ms. Post-meeting at the
earliest.

**Option 6 (smaller TTS chunks): REJECTED.** More ElevenLabs round-trips against
an already-missed latency budget, and
[tts-pause-punctuation-is-read-by-the-chunker] records the chunker splitting a
phone number across two synthesis calls.

**Option 3 (anchor the record to playback): DROP — probably unnecessary.**
B-120 does not solve L2; it gates on `_lost_s` (audio actually discarded,
measured before the teardown clears the clock) and replays **everything**. That
sidesteps L2 rather than fixing it. Copying that removes a layer of work.

### The fix: Option 2 — generalise B-120 from readouts to ordinary content

This is not a new design. It is the next step in a four-commit progression that
has consistently chosen **recovery over trigger**:

| Commit | What it did |
|---|---|
| `7a28da4` (B-67, 20 Aug) | Made post-teardown resolution **run at all** when the final is noise |
| `fbadd2f2` (B-107, 27 Aug) | Re-ask the outstanding question instead of an ack falsely claiming the caller spoke |
| `c65f2a1c` | Made that re-ask actually reach TTS — the dedup guard was eating it |
| `d8af8932` (B-120, 1 Sep) | On a torn-down **slot readout**, replay the whole readout, not the trailing question |

B-120's message states the policy: *"the recovery changes, not the trigger…
Deliberately not a stricter teardown; making Susie reluctant to be interrupted
is the version of this fix that has already been wrong here."*

**B-127 is B-120 applied to ordinary content turns.** Shape to copy:

- Record the turn's chunks, sibling to `_slot_readout_chunks`
  ([llm_stream.py:4022](app/media_streams/llm_stream.py:4022)).
- New arm ahead of the `_outstanding_q` fallback at
  [connection.py:16567](app/media_streams/connection.py:16567), gated on
  `_lost_s >= _SLOT_REREAD_MIN_LOST_S` and the replay budget
  (`_MAX_ECHO_RESUMES`).
- **The replay MUST carry `_WATCHDOG_REASK_MARKER`** or the consecutive-duplicate
  guard drops it silently — that is the entire subject of `c65f2a1c`, and it went
  unnoticed for weeks because the barge-in landed in the playback-only window and
  the caller answered audio they were still hearing. B-120 marks the first chunk
  only.
- Wants a length cap: replaying 12 s the caller half-heard is its own annoyance.

### Frequency

Fired on **both** calls today — `barge-in #0 (partial='yeah')` at 16:03,
`#1 (partial='okay')` at 16:43. A backchannel over a long answer is ordinary
caller behaviour. This is a high-frequency path, and it reads as "broken" in a
demo.

---

## 5. Option 1 (shorten the answer) — scoping done, work NOT started

Deferred to the morning of 3 Sep. What is already established:

- northgate is `prompt_engine: template_v1` → `app/prompts/clinic_template_prompt.py`
  (**not** `jv_system_prompt.py`, which is a dead legacy fallback).
- Brevity rules already present:
  - `:1276` — *"Answer naturally but BRIEFLY. One to two sentences… aim for well
    under ten seconds of speech."*
  - `:1303` — *"KEEP THE SENTENCES SHORT TOO… if a sentence passes about twenty
    words, split it or cut it."*
  - `:1315` — *"NONE OF THIS APPLIES TO READING OUT APPOINTMENT SLOTS."*
  - `:1594` — `CONDITION ACKNOWLEDGEMENT — FAMILY PATTERNS`, *"Keep it to ONE
    sentence — do NOT lecture about the condition."*

> **THE IMPORTANT FINDING, and it changes Option 1's expected value.** The
> offending chunk was **198 characters, ~35 words, one sentence**. The prompt
> already forbids exactly that at `:1303` (~20 words) and at `:1276` (under ten
> seconds). **This is non-adherence, not missing wording.** Tightening the
> wording further may buy nothing. Before editing prose, decide whether the
> answer is enforcement — a chunker/gate split on over-long sentences — rather
> than instruction. Compare the P2 row *"the prompt asks for phrases the gates
> delete"*.

**Not verified, check first:** whether northgate ships `treatment_guidance`. If
it does, `_condition_families` at `:1602` is **suppressed** and the empathy line
comes from `_render_treatment_knowledge` instead — so editing `:1594` would be
editing dead config for this clinic. See
[config-keys-that-never-reach-the-model].

**Test hazard:** editing the template prompt trips the prompt-hash pins.
`UNCHANGED_CLINIC_PROMPTS` is read by three tests and the hash **differs per
branch** — recompute, never copy. See [theorem-v3-pin-is-read-by-four-tests].

**Bonus measurement:** shorter answers should also move `chunk_gate_ms`, the
dominant latency term. One change, two open rows tested.

---

## 6. CONFIRMED OPEN — anchored elsewhere

| ID | Defect | Anchor | Severity |
|---|---|---|---|
| **P3-a** | `TRANSFER_FALLBACK_NUMBER` defaults to a **hardcoded personal mobile**, `+447502211207` — the handset placing the test calls. A clinic service missing that env var dials a private number. | `app/config.py:66` | P2 |
| **P3-b** | Startup banner, FastAPI title/description and `/health` `service` are hardcoded **"Theorem Health"** on the branch serving all three clinics. | `app/main.py:63,64,155,356,581` | P3 |
| **LAT-1** | Slot turns miss the latency bar 3–5×: `content_ttfa_ms` 7123 (`chunk_gate_ms` 4872) and 4706, against p95 < 1.5 s. Offer readout ran 17.5 s of TTS. | `[LAT]` lines | P2 |

---

## 7. Carried but UNANCHORED — leads, not findings

**Do not schedule any of these from this table.**

| Row | What the grep showed |
|---|---|
| **B-82** — escape hatch covers 1 of 10 arm sites | No `B-82` marker anywhere in `app/`. |
| **B-85** — options numbered for a keypad never armed | One marker, `connection.py:6940`, whose comment reads as the *fix*. |
| **A** — a price question becomes a booking instruction | No anchor. Check it is not the row `do-not-volunteer-prices-unprompted` already closed as not-a-defect. |
| **O2** — which path orphans the TTS bytes | Explicitly unpinned when written. Still unpinned. |
| **P** — "I'll take that as a yes" | Phrase absent from `app/`. |
| **P2 family** — prompt asks for phrases the gates delete; barge-in teardown ordering; 6–9.8 s per answer | Real themes, no single anchor. `LAT-1` is the measured half; B-127 §4 is the barge-in half. |

**B-84 is open deliberately** and is implemented, not missing —
`app/notifications/background.py`, `app/main.py:584`,
`receptionist_tools.py:3929`. Do not "fix" it.

---

## Recommended order — morning of 3 Sep

1. **One Theorem call.** Five commits reached three live clinics today and none
   were exercised on the Acuity booking path.
2. **Option 1 / B-127 mitigation** — but read §5's finding first: decide
   enforcement vs wording before touching prose. Verify `treatment_guidance` on
   northgate before editing `:1594`. Measure `chunk_gate_ms` either side.
3. **B-127 Option 2** — generalise B-120. A proper piece of work: engine change
   in the barge-in family, so `latency-eval` and a real call before `production`,
   unlike today's five which were largely self-contained.
4. **P3-a** — the personal-number default. One line.
5. **B-31** — raise or sentence-align the 200-char cap. Low priority (§3).
6. Anchor §7 before scheduling any of it.
