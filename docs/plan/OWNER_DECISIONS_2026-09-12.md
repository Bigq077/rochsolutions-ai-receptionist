# Owner decisions — 12 September 2026

Three decisions that had been parked across `HANDBACK_2026-09-10.md` §2.3,
`DEFECT_AUDIT_2026-09-07.md` §1.2 and §5. Taken by the owner on 12 Sep 2026
on the engineer's recommendation. **None touches slot presentation** — the
owner's explicit constraint for the day; `SLOT_PRESENTATION_SPEC.md` is
unchanged.

Same convention as the spec's §3: a decision is an input, dated, with the
reasoning that carried it, so a later session does not re-derive it.

| # | decision | what changes |
|---|---|---|
| **DEC-1** | **N5 — no third stall rung.** `feat/n5-third-rung` (`e49733ef`, 1 commit) is closed unmerged. | Branch deleted, SHA recorded here. The lead it pointed at moves to `LATENCY_DISTRIBUTION_2026-09-10.md` §8 item 1. |
| **DEC-2** | **Surname write gate stays WARN-ONLY.** The count `[book] SURNAME NOT READ BACK` (`efe39abe`, 4 Sep) is the instrument; promotion to a block needs the measured rate under ~5 % of bookings with a two-part name. | Nothing. The criterion was already in the helper's docstring; this makes it the owner's rather than the author's. |
| **DEC-3** | **Hold speech goes to a practitioner for review** — every non-answer phrase Susie says, rendered from the live code, not from the 29 Aug reading copy. | `HOLD_SPEECH_REVIEW_PACK_2026-09-12.md`, for the owner to put in front of Marcus / Jonathan with one recording. |

---

## DEC-1 — why no third rung

The corpus argued against it twice (`LATENCY_DISTRIBUTION_2026-09-10.md` §6):

1. On `CA5e14516b` the first token arrived at 9.5 s, inside the 10 s deadline,
   so the ladder correctly stood down and a third rung would not have fired.
2. Of the ten worst turns, two (`CA8522b3e23fc6` seq 5–6, `CA7d6aa7714225`
   seq 5) had `ttfa` ≈ `content_ttfa` with a **fast** first token — 14–21 s of
   silence while the model answered promptly. The ladder never started. A
   third phrase helps only when the ladder is already running and runs out of
   things to say.

A third rung is also caller-facing copy, which is why the branch was parked
in the first place. The engineering fact underneath is that `UNKNOWN_SLOW`
holds two one-sentence apologies and the family rule refuses a repeat; the
honest outcome of that is one apology and quiet, which the author's own note
called "the better fault".

**What replaces it:** the arming lead. Three calls, no `file:line` yet, the
arming path unread. It needs the obs corpus (per-turn `latency` JSON) to
anchor, which is why the standing recommendation is a read-only
`OBS_DATABASE_URL`. Until anchored it is a lead, not a row
([[anchor-defect-rows-before-scheduling]]).

## DEC-2 — why warn-only

`_surname_was_spoken_back` (`receptionist_tools.py:2535`) measured, over 851
calls, that the surname was never spoken back on **34.6 %** of calls that
actually booked with a two-part name. A block at that rate refuses one
booking in three. The 7 Sep read-back steer now speaks the stored surname
back, so the rate should have fallen — but it has not been re-measured. The
decision is therefore the same shape as the slot guard's: **log until the
number says enforce.**

To re-measure: count `[book] SURNAME NOT READ BACK` against bookings in the
obs corpus since `efe39abe` + the steer (7 Sep). Below ~5 %, promote; the
gate would mirror A1 (`phone_confirmed is not True` → `[book] BLOCKED`) at
the same site, above the backend branch, so all four executors are covered.

## DEC-3 — the review

`DEFECT_AUDIT_2026-09-07.md` §5: *"Hold speech is live on all four lines and
no practitioner has heard it. Longest-standing un-reviewed audible change in
the product."* The 29 Aug reading copy (`HOLD_HEAD_PHRASES.md`) is accurate
for the intent heads but does not list the tool-work heads, the stall
ladder, the recorded clip, the fillers or the barge-in acknowledgements —
i.e. it is not "everything a caller hears that is not an answer". The pack
is that, rendered from `hold_speech.py`, `llm_stream.py`, `connection.py`
and `audio_clips/CLIPS.json` at `dfaa0b02`.

The ask to the practitioner is one question per phrase: *would your front
desk say this?* Rewordings come back through the owner; the import-time
checks in `hold_speech.py` (ends in a dash, no lookup verb in a topic head,
family uniqueness) still apply to any replacement.

---

## Correction, same day — the invented-symptoms item was not open

The 12 Sep audit listed `OPEN_DEFECT_INVENTED_SYMPTOMS_2026-09-09.md` as
"not started". It was fixed 21 minutes after that document was written
(`8e838f0f`), with a regression test and re-pinned hashes, promoted that
night, and call-verified at 23:08 (`CA5e14516b`). The document carried no
status line, so a reader who did not grep the tree for the rule inherited
"not fixed here" as current. This is `DEFECT_AUDIT_2026-09-07.md` §6's
mechanism exactly — a diagnosis doc outliving its fix — and the reason that
audit replaced the lists. The doc now carries a status header; nothing was
changed in the prompt.
