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
