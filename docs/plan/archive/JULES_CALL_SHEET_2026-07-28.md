# Jules — 7 calls, then we freeze

**Build: `b405017` (live, frozen). Rollback: `2d553b6`. Number: `+447366263180`.**
**No code. No pushes. If something breaks, log it and keep dialling.**

UK mobile. Natural delivery — pause, hesitate. One case per call.
Check obs after **every** call, not memory. A call that *sounded* perfect and
wrote `collected.name = None` is a **FAIL**.

---

# BLOCK A · Name capture — 3 calls · do these first

**Name capture is the most broken part of this system.** Not an opinion — the
evidence:

- **36 of the 96 baseline test failures** live in `name_collector`. Its intended
  behaviour is no longer pinned by tests at all.
- Bare names went **11/11**. Names with a lead-in went **1/3**.
- On the live build, *"tom green"* was heard as **"home green"** on two separate
  calls, and on one of them the caller's correction was accepted out loud and
  still stored `collected.name = None`.
- That same call is the one where she asked *"shall I book that in?"* twice and
  **never booked**. The name failure is not cosmetic — it is the mechanism by
  which a caller confirms twice and gets nothing.

Every call below books normally. **The name is the only variable.**

### A1 · The lead-in

> Complaint, pick a slot, then when asked for your name:
> *"yeah, that'd be Tom Green"*

| **PASS** | `collected.name == "Tom Green"` **and** the call books |
| **FAIL** | name wrong or `None`, **or** more than one "shall I book that in?" |

### A2 · The correction — the most important name call

Give your name, and **when she reads it back wrong, correct her**:
*"no — Tom Green"*

If she happens to get it right first time, **redial and say it faster**. We need
the mis-hear to happen. It has happened twice unprompted on this build.

| **PASS** | `collected.name` is the **corrected** name, and the call books |
| **FAIL** | `None`, the original mis-hear, or she loops back to the confirmation |

*This is the exact shape that produced the double-confirm dead end.*

### A3 · A name the STT has to work for

> Bare, no lead-in: *"Sarah-Jane Okonkwo"*

| **PASS** | stored intact, captured in **one pass**, no spelling loop, books |
| **FAIL** | surname dropped, first name only, or she asks a third time |

*Real cohort patients will not all be called Tom Green.*

---

# BLOCK B · The other three diagnostics

These deliberately break the demo script. A failure here does **not** stop the
demo — it tells us how hard to lean on the script rule that avoids it.

### B1 · Two services in one request

> *"Hi — I've got knee pain, and I'd also like a sports massage."*

Then book whichever she offers.

| **PASS** | `service` **==** `checked_service` |
| **FAIL** | they differ — F-021 is live, and the one-service rule becomes mandatory |

*The only `BLOCKER`-rated defect whose status is genuinely unknown. Nobody has
ever attempted this shape on this build.*

### B2 · Reject the first offer

> Ask for a specific day, then when she offers times: *"none of those"*

| **PASS** | the next offer is **two** options |
| **FAIL** | three or more |

*Known open. Sizing it, not fixing it.*

### B3 · Neck complaint, straight to booking

> *"I've had some neck pain recently"* — then book normally.

| **PASS** | any screening question comes **before** "shall I book that in?" |
| **FAIL** | she screens **after** you have already confirmed |

---

# CALL 7 · THE GATE — the demo script, exactly

| Step | Say |
|---|---|
| open | *"Hi — can I book an appointment please?"* |
| reason | one plain complaint, **one service only** |
| timing | **a specific day** — never "as soon as possible", never "anytime" |
| slot | a time **she actually offered**, in her words |
| name | **bare** — *"Tom Green"*, no lead-in |
| number | *"Yes"* — accept the caller-ID, **never touch the keypad** |
| confirm | *"Yes please"* |

> ### Ready for demo if, and only if:
> - `booking_confirmed` **and** `calendar_event_id` both set
> - `collected.name` is exactly what you said
> - `service` == `checked_service`
> - `collected.reason` populated
> - **one** "shall I book that in?", not two
> - no dead air over 3 s

**If call 7 fails, redial once. If it fails twice — stop and message Quentin.**

---

## Hand-back — 5 lines, no logs

1. **Block A, per call:** SID · what you said · what `collected.name` stored ·
   booked y/n. This block matters most — be exact about the wording you used.
2. Block B: SID · PASS/FAIL · one sentence each.
3. Call 7: PASS/FAIL, and the six checks above.
4. Anything new: SID + one line. **Not fixed.**
5. Confirm: **nothing committed, nothing pushed.** And — does the fallback
   recording still sound usable to your ear?

**Do not paste raw logs into chat.**
