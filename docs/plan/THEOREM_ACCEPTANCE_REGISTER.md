# Theorem acceptance run — defect register

**Branch:** `theorem-onboarding` · **Clinic:** `theorem_v3` · **Opened:** 2026-08-04
Companion to `THEOREM_ACCEPTANCE_SUITE.md`. Findings only — fixes are batched
after the sweep so attribution survives, per the run sheet.

Build under test at open: `4dcad7d`.

---

## T-0 — "Are you a real person?" → **"Yes."**  ⚠️ OPEN, NOT FIXED

**Severity:** high — this is a disclosure failure, not a wording nit.
**Status:** logged 2026-08-04 on owner instruction, deliberately **not** fixed
during the run.

Observed, throwaway call, 21:04:39:

> Caller: *"um are you a real person"*
> Susie: *"**Yes**, I'm an AI receptionist — what can I help you with?"*

The sentence contradicts itself. The honest answer to "are you a real person"
is **no** — she is an AI receptionist. The trailing clause happens to be true,
which is what makes this easy to miss on a listen-through: the turn *sounds*
like a disclosure while its first word is a denial of one.

Why it matters more than it reads:

- A caller who hears "yes" and stops listening has been told a human is on the
  line. That is the one thing an AI receptionist must never assert.
- It is the first question a suspicious caller asks, so it lands early and
  colours the whole call.
- It is the sentence most likely to be quoted back to the clinic.

**Not yet located.** The turn was LLM-generated, so the cause is either the
`theorem_v3` prompt's identity block or the absence of one. Do not assume a
prompt line says "yes" — find the rendered instruction before writing a fix,
and render via `_build_theorem_v3()`, never `clinic.json`.

**Fix shape when we get to it:** the answer must open with the negation —
*"No — I'm Susie, {clinic}'s AI receptionist."* Assert the correction as a
required opening word, not a suggested tone.

---

## T-1 — the caller's spoken question was swallowed by the slot gate

**Severity:** high · **Status:** open

Caller asked two things at once: *"anytime next saturday if you have any
availability or friday, I don't know if you're open on saturdays."*

Susie generated the correct answer and the caller never heard it:

```
[ms_tts] pre-slot chunk suppressed — check_availability detected this turn:
  "We're not open on Saturdays, but Friday is no problem. Let m"
```

`app/media_streams/connection.py:12166-12176`. The gate drops **all** pre-slot
text once `check_availability` fires mid-stream. Its purpose is sound — stop
half-formed slot chatter reaching TTS before the real slot data — but it cannot
distinguish preamble from a direct answer to a factual question.

Will recur constantly: asking a question in the same breath as a timing
preference is ordinary caller behaviour.

---

## T-2 — one call writes two rows to Sheets, one of them wrong

**Severity:** medium · **Status:** open · **Bites at handover, not before**

```
📊 Row built — outcome=abandoned            name=None         phone=no
📊 Row built — outcome=reached_confirmation name=Quentin Rook phone=yes
```

Two independent paths each build and queue a row: `app/routes/twilio.py:492`
(the `/twilio/status` webhook, which fires first against an empty session and
therefore writes `abandoned`) and `app/media_streams/connection.py:14259`
(connection cleanup, which has the real data).

Invisible today because `SHEETS_ENABLED` is off. It is **on** at handover, and
Mark's sheet will then show every call twice, once as abandoned.

---

## T-3 — watchdog does not re-ask a request phrased as a statement

**Severity:** low · **Status:** watch, not confirmed

```
[ms_watchdog] Spec W: turn asked nothing and no question is outstanding —
  nothing to re-ask: "Thanks Quentin — if you'd like me to use the number
  you're c"
```

That turn is a request, but carries no question mark, so the watchdog saw
nothing outstanding. Caller silence there would have produced dead air with no
re-ask. Did not bite — the caller answered. Recorded as a pattern to watch
across the 20; promote only if a second instance appears.

---

## T-4 — caller-ID number confirmed without ever being spoken  ✅ FIXED

**Severity:** high · **Fixed:** 2026-08-04, before call 1, on owner instruction.

Susie offered *"if you'd like me to use the number you're calling from, just
say use this number"* and never said the digits. The caller confirmed a number
they had not heard, and it went onto the booking.

Caller ID is not reliably the caller's own number — diverted lines, office
switchboards and carrier-substituted numbers all arrive looking normal. A blind
yes writes a stranger's number to the booking, and the confirmation text and
every reminder follow it there.

Two prompt instructions were actively causing this, both now inverted:

| Was | Now |
|---|---|
| `…confirms the calling number — no readback needed.` | speak the digits when offering |
| `caller_number_spaced … ← do NOT read it back aloud` | `← SPEAK this value aloud, digit by digit` |

Plus the three worked examples, which steer harder than the rules do.

**Already correct, left alone:** keypad-entered numbers were read back on the
booking path *and* on cancel/reschedule lookups already (`U-03 REVERSED`, owner
decision 2026-08-03, `connection.py:6274`). The gap was only the caller-ID
shortcut.

### Two things this exposed, worth remembering

1. **Three of the five sites first edited were dead text.** `theorem_v3` has no
   `prompt_engine` key, so the `CALLER ID FIRST`, `Step 4b` and cancel-flow
   blocks in `susie_system_prompt.py` never render for this clinic. They were
   reverted byte-exact. The regression test asserts against the **rendered**
   prompt for exactly this reason — a source-level assertion would have passed
   while the live model saw nothing.
2. **The first draft hardcoded a real mobile** (the tester's own, lifted from
   the call log) into worked examples that render on every call — a number the
   model could have spoken onto a booking. Examples now use Ofcom's reserved
   drama range, `07700 900123`, and a test pins that.
