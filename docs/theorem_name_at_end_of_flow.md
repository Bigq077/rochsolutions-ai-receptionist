# Theorem: move the name question from name-first to end-of-flow (JV/VE parity)

Owner-requested change. Goal: Theorem collects the caller's name at the **same
place JV / Vital Edge do — right after the slot is chosen, immediately before the
phone-number step** — instead of "name-first" (right after the greeting).

## The perfect system (owner's principle, verbatim intent)

1. Keep the **compound ask**: "could I take your first name and surname?"
2. Ask it **at the end of the booking flow**, right before the number question —
   NOT at the start.
3. Read back **only the first name** ("Thanks Quentin —").
4. The **surname is silent**: captured in the same breath, or as a later STT
   straggler, and **never re-asked** — even if it lands "the second time".
   (Backed by the surname-first STT recovery already shipped in `efeb5c4`.)
5. After the name, go to the phone step **once** ("use this number"). The name
   insertion must not create a second phone step or re-loop.

## Flow: before → after

```
BEFORE (name-first):
  greeting → NAME → [request] → location → timing → slot → PHONE → readback → book

AFTER (end-of-flow, JV/VE parity):
  greeting → [request/FAQ] → location → timing → slot → NAME → PHONE → readback → book
```

Only the NAME position moves. Location (Alcester/Redditch), timing, slot, phone,
readback, and the reschedule/cancel lookup flow are unchanged.

## Prompt changes — `app/prompts/susie_system_prompt.py` (`_build_theorem_v3`)

| # | Section (approx line) | Change |
|---|---|---|
| P1 | NAME FIRST fixed-response (~2498–2547) | **Remove name-first.** After the greeting, Susie answers the caller's request directly. No name gating of FAQs. Keep the emergency/999 and name-decline handling. |
| P2 | SLOT CONFIRMATION → PHONE (~3001–3042) | **Becomes SLOT CONFIRMATION → NAME.** On slot accept: confirm the slot AND ask the name in the same turn ("So that's [day date time] — could I take your first name and surname?"). Do **not** offer the phone here. |
| P3 | Time-selection shortcut (~3386–3395) | Time confirmed → **ask the name** (not phone), unless the name is already known (returning-patient lookup). |
| P4 | Step 7 NAME AT BOOKING (~3396–3410) | **Now the primary name collection**, mirroring template step 7: ask first name + surname, read back first name only, surname silent/never re-asked; then in the SAME turn acknowledge "Thanks [First] —" and run the phone step (offer "use this number"). Returning-patient with a known name → skip to phone. |
| P5 | "NAME ALREADY TAKEN / taken at the start" wording (~3004, 3035, 3393, 3577) | Reword to flow-neutral ("name has been taken — do not re-ask the surname"). The *mechanism* (don't re-ask a first-name-only lock) stays. |

Bundling rule (P2+P4): the phone offer moves from the slot-confirmation turn to
the name-acknowledgement turn, so there is exactly **one** "use this number".

## Code changes — `app/media_streams/connection.py`

| # | Location | Change | Why |
|---|---|---|---|
| C1 | FAQ clinic gate (~8664–8668) | Remove the `and bool(collected.name)` name-gate. | It suppressed the clinic-FAQ gate until a name was on file — fine under name-first, but under end-of-flow the name is absent for most of the call, which would break clinic-specific FAQ handling. JV/VE don't gate on name. |

Everything else is **position-agnostic** and needs no change:
- `v3_name_collection_active` / `post_slot_confirmation_pending` arm off the
  LLM emitting a name-request phrase — fires wherever the name is asked.
- Straggler keep-check: the slot-based arm (`v3_confirmed_slot_phrase and not
  phone_confirmed`) becomes the primary surname-straggler net — this is exactly
  the arm designed for after-slot name (JV/VE), so it now does the main work.
- Surname back-fill + the shipped surname-first recovery (`efeb5c4`) — unchanged.
- Phone-confirm ("use this number") handler — unchanged; fires after the name.
- Spec Q phone-DTMF: still armed by keypad-mention triggers; no change needed.
- Reschedule/cancel lookup flow (separate) — untouched.

Stale comments referencing "name-first" (334, 1201, 5623, 9070, 9150) are left
as-is except where they change behavior; only C1 is behavioral.

## Risks / validation

- The flow itself is **LLM-driven**; unit tests can confirm the assembled prompt
  is self-consistent (no name-first text, name-ask present at slot confirmation)
  but **cannot** validate live turn-taking. One live test call is the final gate.
- Test call script: book → pick clinic → pick a time → **expect the name ask
  here** → give "Quentin … Roch" → expect "Thanks Quentin —" then the phone
  ("use this number") → confirm → readback with "Quentin". Surname must never be
  re-asked; there must be exactly one phone step.
- Rollback: `git revert <sha>` restores name-first cleanly (prompt + C1 only).
