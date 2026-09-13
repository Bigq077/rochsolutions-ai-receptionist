# Hold-head audit — 13 Sep 2026

Audit of 56 situational heads across the 12–13 Sep demo-line calls, after
`c117c0b4` (no generic hold phrase). Three findings; owner chose "A and A"
for the second and third.

| # | Finding | Fix | Commit |
|---|---|---|---|
| 1 | *"a knee thing … is there parking"* drew the parking head: `_BODY` + a complaint-noun ("thing / issue / problem / trouble / playing up") was not a SYMPTOM, so FAQ won. | Complaint-noun vocabulary added to the SYMPTOM rule; SYMPTOM already outranks FAQ. | `a6bb70b1` |
| 2 | **Thank-you chain.** Every booking: *"Thanks, got that —"* (first name) → *"Thank you —"* (surname) → *"Thanks for that —"* (number) → *"That's noted —"* (summary). Each right; the run is the tell. | **A** — the surname-only answer gets no head (`_SURNAME_ONLY_Q`, `_answer_moment` step 5). Content TTFA on those turns was 1.4–2.5 s and the 2.75 s receipt rung never fired on one; if the model stalls it still does. *"first name and surname"* keeps its head. | this commit |
| 3 | **Lookup head before the reason gate.** *"what's the soonest you've got"* → *"Let me find the soonest I've got —"* → *"what's the appointment for?"*. BOOKING STEPS 1b asks the reason before any diary read, so the lookup is a turn away. | **A** — new `reason_pending` flag on `classify_intent`; while it is set, EARLIEST / NAMED_DAY / NAMED_WEEK / TIME_BAND / TIME_AROUND / AVAIL_QUERY / SESSION_LENGTH yield to BOOK_NEW (*"Let's get you booked in —"*), which names no diary. A complaint in the same breath, or an answer to the reason question, releases it. `llm_stream` computes the flag from `_clinic_asks_its_own_reason_question` + the A2 reason slots, and not for cancel / reschedule. | this commit |

## Scope notes

* `reason_pending` is gated on `prompt_facts.reason_question`, the same key
  that gates the Gate 5b-r strip, the CALL STATE "already asked" latch and
  the opener extractor. Set for `northgate`, `jv_v1`, `vital_edge`; not
  `theorem`. **Theorem lead:** the prompt's rule 1b is unconditional, so the
  model may still ask the reason there and have it stripped and replaced by
  the next booking question — in which case the lookup head is early on
  Theorem too and this gate cannot see it. Not measured (call_logs is
  demo-line only). Needs a Theorem call transcript before it is a row.
* **Pre-existing, not introduced here:** a surname that is also intent
  vocabulary gets that head — *Price / Fee / Costa* → FAQ_PRICE, *Park* →
  FAQ_PARKING, *Monday* → NAMED_DAY. Verified identical on `a6bb70b1`. The
  intent loop runs before `_answer_moment`, so the name question never gets
  to claim the word. A row for the next audit, not this commit.

## Proof owed

One demo call at the promoted build: *"a knee thing … is there parking"*
(#1), the name turns (#2), an ASAP opener with no complaint (#3).
`production` stays at `c7ff4204` until then.

## Proof call 1 — CA9bd192c92caff6b7b941378752921cff (build dd3a9ff7, 16:01 UTC)

* **#1 PROVED** — "a bit of a knee thing is there parking" → *Sorry to hear that —*.
* **#2 NOT EXERCISED** — Susie asked "first name and surname" in one question
  (whole-name shape keeps its head by design). Needs a call where the first
  name arrives alone so the surname is asked on its own.
* **#3 NOT EXERCISED** — reason was banked from the opener, BOOK_NEW correct either way.

STT heard "Elektra" as "a lecture" three times. Name turns:

| turn | caller | head | content |
|---|---|---|---|
| 5 | "a lecture zani" | Thanks, got that — | did you say Lecture — is that right? |
| 6 | "no just a lecture and yeah" | **Not to worry —** | I want to make sure I get that right — could you say your first name… |
| 7 | "a lecture" | **Thanks, got that —** | **I'm not quite catching that — could you try typing your surname on your keypad?** |

### New rows

* **N-1 Head contradicts content on a name re-ask** (turn 7). NAME_GIVEN fires
  off the caller's words; the model then says it did not get it. After one
  re-ask the receipt head should stand down. `name_reask_count >= 1` ⇒ no head.
* **N-2 REFUSAL head over a name correction** (turn 6). "no, just …" after a
  `_NAME_CONFIRM_Q` is a correction, not a refusal; "Not to worry —" is
  consolation for the wrong thing. Prev-turn name-confirm + "no" ⇒ no head.
* **N-3 OWNER (13 Sep): keypad on the name turn is wrong behaviour.** Impossible
  on a phone (3 letters per key). The prompt already bans spelling AND never
  mentions keypad for names (clinic_template_prompt.py:1987-1990); the wording
  bled from the phone-number rule at :2986. No gate strips it. Fix: add the
  keypad-on-name shape to the "type your number" strip family; on the second
  miss the ENGINE sets `needs_name_correction_sms` so the prompt's placeholder
  exit ("we'll confirm by text") actually fires. Voice-UX rule: one clean
  re-ask, then best-effort + move on + SMS confirm; never spell, never keypad,
  never read back a mangled name.
* **N-4 Hardcoded clinic in the name-correction SMS** — flow.py:19776 says
  "Susie from Theorem Health" on every clinic. Engine text must come from
  clinic.json.
* **N-5 `name_collector.py` (deterministic, HAS a spelling ladder) did not run
  on this call** — the LLM path owns names on the demo line and the two
  specs disagree (spell vs never spell). Decide which is live; retire the other.
* **L-1 Latency outliers** — turn 2 content 16.1 s (LLM TTFT 13.2 s), turn 1
  5.9 s. Model-side; the 7 s rung fired. One-off or not — check before the demo.

## Proof call 2 — CAe30232407e8e65fbd7687b5c644b12ff (build 0d5c8556, 16:45 UTC)

* **#1 PROVED** again. **N-2 PROVED** — turn 6 "no a lecture" over the read-back: no head.
* **N-1 NOT EXERCISED** — the third ask was Gate 5g's first-ask wording (see N-7).
* **Gate 5n NOT EXERCISED** — the model took the silent-accept path, not the keypad.

| turn | Susie asked | caller | head | content |
|---|---|---|---|---|
| 6 | did you say Lecture? | "no a lecture" | *(none)* | I've got you on 0750… best number? |
| 7 | best number? | "yeah that is" | Thanks for that — | **Before I do that — could I take your first name and surname?** |
| 8 | (3rd ask) | "yeah so that would be a lecture" | Thanks, got that — | did you say Lecture? |

* **N-6 (shipped `38b6bb6c`)** — Gate 5g asked for a name it recovered 100 ms
  later from the raw reply it had deleted. Now reads the name (ANCHORED
  patterns) out of spoken-so-far + this chunk BEFORE judging the CTA. O-18
  stays as layer two for BARE-form acks.
* **N-7 (open)** — N-1 reads re-ask WORDING; Gate 5g's substitute is first-ask
  wording even on the third ask. `llm_stream` should count prior name asks and
  pass `name_reasked=True`.
* **L-1 (pattern, 2 data points)** — availability turn 16.1 s → 9.0 s (LLM TTFT
  13.2 / 8.3 s), 7 s rung fired both times. Own investigation.

Shipped since dd3a9ff7: `808a60fb` Gate 5n, `0d5c8556` N-1/N-2, `38b6bb6c` N-6.

## Proof call 3 — CA8b038f9a08c83c0a7519a9ac7e0c842e (build 6c63c98f, 17:29 UTC)

* **#1 ✅ · N-2 ✅ · N-1 ✅ (first time) · #2 surname head-less ✅ (first time).**
  Zero heads across the four name turns; the model re-asked in its own words.
* L-1 did not reproduce (availability 2.0 s). Three points: 16.1 / 9.0 / ~4 s.
* Turn 5 "yeah uh lecturer" → no receipt head: pre-existing bare-yes rule
  (≤4 words opening with a yes-word), identical on a6bb70b1. Not a row.

**PROMOTED 17:34 UTC:** `production` = `latency-eval` = `6c63c98f` (11 commits).
Revert target `c7ff4204`. Owed: build-SHA on the three clinic services + one
patient-line call.

## Proof call 4 — CAb5a26a1090136f952707c7d0cf8e9b6d (build 6c63c98f, 17:38 UTC)

* **#3 ✅ (first time)** — "what's the soonest you've got" → *Let's get you
  booked in —* → reason question.
* **Gate 5n FIRED (first time).** Caller never heard keypad/spell; exit + phone
  question spoken. Then the exit went wrong twice:
  * **N-8** — "still wrong it's X Y" → best effort **'Still'**. Prefix strip
    read the first word; a correction carries the name after a cue.
  * **N-9** — `book_appointment` BLOCKED `surname_required` (llm_stream surname
    backstop: one-token name + surname never asked on its own) right after
    "we'll double-check the spelling by text" → fifth name ask → "gping" →
    **booked "Still Gping"**, GCal event `6m60fe5ta…`, Mon 14 Sep 08:50
    Didsbury — **delete from the demo calendar.**
* Turn 18 "got it — X Y": model accepted the name in BARE form; engine did not
  persist (no phase signal). Lead, not a row — a BARE pattern is what produced
  'Rehab' and 'Good'.
* N-6 / N-7 not exercised (name known before the CTA).

Fix (this commit): the reader takes the text after the last correction cue
(`it's` / `i said` / `name is` / `that's` / `called`) before the prefix strip;
the surname backstop yields to `_gate5n_exited`.
