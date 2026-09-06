# Handover — 6 September 2026: promotion, and Theorem's hold speech

Two tasks, both done. Continues `HANDOVER_2026-09-06_OVERNIGHT.md`.

---

## 0. State

| | |
|---|---|
| `latency-eval` | **`d0fbc9a3`** (plus this doc) |
| `production` | **`d0fbc9a3`** — Vital Edge, JV, Theorem |
| Suite | **97 failed / 8645 passed / 22 skipped** |
| Failing set | byte-identical to the `e2c3c2c5` baseline throughout |

**Revert targets, in order:**

| to undo | reset `production` to |
|---|---|
| the Theorem hold-speech port | `3933f45a` |
| everything from 5–6 Sep (B-145/145b/146/147/148) | `e2c3c2c5` |

`[build_info] running build <sha>` in the Render log is the only proof of what
is running. `/health` returns a hardcoded 1.0.0.

**This doc is on `latency-eval` only.** Promoting it would restart three live
clinic services for prose; it will ride along with the next engine promotion.

---

## 1. Promotion of the 5–6 Sep slot fixes

`e2c3c2c5 → 3933f45a`, fast-forward, no merge commit. Five behavioural fixes,
all verified on the demo line before the push:

| SHA | verified live |
|---|---|
| `4886f865` B-145 | `CAa90cac94` 10:46:33 — `'Thursday 10th September' answered from the payload … (B-145)`, head + 3 times + tail, keypad on TIMES, `3` selected the third TIME |
| `6ef2d3f5` B-145b | `CAb837c859` 10:48:06 — "morning works better for me" **kept** its `time_band` head; the backstop is not over-suppressing |
| `ca23c0a8` B-146 | `CAdf1e02ca` 10:14:08 — "can I have a sports massage please" → `book_new` at 721ms, and "how much is a sports massage" → `faq_price`, no booking head |
| `4ee93203` B-147 | `CAa90cac94` 10:46:12 — `a day was REFUSED … (B-147)`, no Monday readout, 133ms |
| `3933f45a` B-148 | `CAb837c859` 10:48:19 — Friday, never read out, answered from the payload in 129ms |

**Not called:** refusing a day *after* the offer has already narrowed. Unit
pinned — B-147's guard sits above the ladder B-148 widened — but no call.

---

## 2. Theorem hold speech — `d0fbc9a3`

The last of the four lines. northgate from the start, vital_edge 1 Sep, jv_v1
4 Sep, Theorem now.

### 🔴 The key is TOP-LEVEL for Theorem, and only for Theorem

This is the inverse of the other three and it is the way this port would most
likely have shipped as dead config.

| clinic | configured by | key goes |
|---|---|---|
| northgate, jv_v1, vital_edge | `app/clinics/<id>/clinic.json` | `operational.hold_speech` — a **top-level** key is overwritten with False |
| theorem, theorem_v2, theorem_v3 | hardcoded `CLINICS` in `clinic_config.py` | **top level** — `get_clinic` returns `dict(CLINICS[cid])` verbatim (~1651) and never calls `_map_json_to_clinic_contract`, so `operational` here is dead |

**Both failures are inaudible.** `hold_speech_enabled` fails to False, the
clinic keeps its pre-arbiter behaviour, and nothing logs.

Proven by neuter rather than by reading: writing the key the way the other
three clinics write it resolves to False on all three Theorem ids and fails
five tests.

**One key moves three ids.** `CLINICS["theorem_v2"] = deepcopy(CLINICS["theorem"])`
and `_v3` copies `_v2`. **+447380841468 → `theorem_v3` is Mark's live line.**

### The one way it could have lied

`booking_system` is `acuity` → `clinic_facts` reports `provisional=False` → the
write head is WRITE_BOOK, *"Right, booking you in —"*. True: Theorem writes a
real Acuity appointment. It can never draw PENDING_REQUEST, *"Sending that over
to {practitioner} —"*, which would be wrong twice — it claims a request Theorem
does not make, and Theorem carries no `practitioner` key to render.

### Measured on Theorem's own traffic

122 stored Theorem calls, 579 caller turns, replayed through the pure
classifier (`scripts/replay_hold_speech`, `scripts/replay_situational_heads`):

| | before | after |
|---|---|---|
| dead ends (a phrase with nothing behind it) | 15 (5.1%) | **4 (1.5%)** |
| worst single call | 17 | **8** |
| phrases claiming a lookup or a write | 197 (67.2%) | 172 (62.5%) |
| stacked behind another phrase | 18 | **0** (structural) |
| model's duplicate opener removed | — | 31 turns |
| turns left silent (unchanged) | — | 304 |

The 4 remaining dead ends are not claimed as zero.

### What did not move

Prompt hashes — hold speech is runtime, not prompt. The four golden-hash tests
and the Theorem-pinned cancel/screening files pass untouched.
`keep_pre_slot_speech` and `filler_clip` are unset for Theorem as they are for
northgate, and there is no Theorem-specific filler code path.

### Guard tests re-aimed, not deleted

`theorem` was the "a real clinic that has not opted in" example in two files.
`demo` takes that role — it answers no Twilio number and is `get_clinic`'s
fallback, so it is the last real config genuinely off. `PATIENT_LINES` is now
**derived from `TWILIO_TO_CLINIC`** rather than typed out, and a new
`test_every_live_line_is_accounted_for` catches a future number added and left
unrecorded — the old check fired only on a clinic that was ON and unlisted, and
with all four now on it had no negative case left.

---

## 3. Calls to make when you are back

Theorem's line, **+447380841468**. Check `[build_info] running build d0fbc9a3`.

1. **A booking.** Reach the write and listen for *"Right, booking you in —"*.
   She must **never** say *"Sending that over to …"* — that is Vital Edge's
   wording and would claim a request Theorem does not make.
2. **A lookup.** Ask for availability; the head should precede the times as one
   sentence, not sit in front of a pause.
3. **A cancel.** *"Taking care of that —"* / *"Right, sorting that —"*, and
   still no apology for a success.
4. **Anything slow.** At 3.5s you should hear *"Sorry, still with you —"* and
   never two phrases in a row — stacking is structurally impossible now, so if
   you hear two, something outside the arbiter is speaking.

If any of it is wrong, revert is one key: set
`CLINICS["theorem"]["hold_speech"]` to `False`, or reset `production` to
`3933f45a`.

---

## 4. Still open, unchanged by today

- The "30-minute session" heard as "5-minute" — keyterms carry no numerals,
  blocked on pulling a wav off Render.
- `UNKNOWN_SLOW` apologising on a slow turn that ANSWERED a question.
- Readouts at 17–19s, and the band-preference turn answering from the model
  with two options and no lookup (`presented ≠ bookable`).
- B-31's 200-char cap firing several times per call and recovering.
