# `B-09` — full map before touching anything

**3 Aug 2026.** Requested: map everything, including every regression, before
implementing. Nothing has been changed yet — this document is the investigation.

---

## Headline: the register's diagnosis is wrong, and the fix is smaller than it says

`REGISTER_B_U.md` records `B-09` as *"model-side arithmetic with no deterministic
floor under it"*, prescribing *"add a resolver and wire it into the `date_hint`
path… half a day, not an hour."*

**There is a deterministic bug in our own code, and a correct resolver already
exists.** The +12 days is not the model failing at arithmetic — it is the model
counting correctly from an anchor we hand it that is **seven days late**.

That is the second time this row has been re-scoped. It should not be scheduled
off the register text again without re-reading this.

---

## Root cause

`_date_context` ([clinic_template_prompt.py:37](../../app/prompts/clinic_template_prompt.py)):

```python
days_until_sunday = (6 - weekday_num) % 7
this_sunday = now + _td(days=(days_until_sunday if days_until_sunday > 0 else 7))
next_monday = this_sunday + _td(days=1)
```

On a Sunday `days_until_sunday == 0`, so the `else 7` fires and "this Sunday"
becomes **next** Sunday. `next_monday` — which is literally tomorrow — is
reported as **eight days away**.

Reproduced across every weekday:

| Today | `next_monday` handed to the model | Truth | *"next Friday"* the model computes | Truth |
|---|---|---|---|---|
| Mon–Sat | correct | correct | correct | correct |
| **Sunday** | **+8 d** | +1 d | **+12 d** | +5 d |

The +12 in the register's title is this, exactly. **It reproduces only on
Sundays** — one day in seven, which is why it survived.

---

## Four implementations of the same calculation. Three are wrong.

| # | Where | Sunday | Timezone |
|---|---|---|---|
| 1 | `clinic_template_prompt.py:50` `_date_context` | ❌ +8 d | ✅ Europe/London |
| 2 | `config.py:933` | ❌ +8 d | ✅ Europe/London |
| 3 | `llm_stream.py:1290` | ❌ +8 d | ❌ **`date.today()`** — server local |
| 4 | `receptionist_tools._extract_week_range._next_monday()` | ✅ +1 d | ✅ call-time `today` |

Copy 4 is correct: `days_ahead = 7 - today.weekday()` gives +1 on a Sunday.

### So the two halves of the system disagree by exactly 7 days on Sundays

- If `date_hint="next week"` reaches the **tool** resolver → next Monday = **+1 d**, correct.
- If the model passes `after_date` computed from the **prompt** anchor → **+8 d**, wrong.

And the `check_availability` schema *explicitly instructs the model to do the
second thing*:

> `after_date` — *"Pass this when the caller cannot be seen before a certain date
> — e.g. for 'next week' or 'not this week', pass next Monday's date…"*

Observed live: `check_availability args={"after_date": "2026-08-10",
"day_window": 1, "date_hint": "any"}`. The model passes a literal date and
`date_hint` carries nothing the resolver can use. **The correct resolver is
bypassed on the path that matters.**

### Copy 3 has a second, independent defect

`llm_stream.py:1290` uses `date.today()` — **server local time, not
Europe/London**. Render containers default to UTC; during BST that is London−1h,
so between 23:00 and midnight London the injected date context is a **day
behind**. Combined with the Sunday bug, a 23:30 Sunday caller gets an anchor
computed as though it were Saturday.

**Not yet verified** whether `TZ` is set on the Render services — that is a
five-minute check and it changes whether copy 3's timezone bug is live or latent.

---

## Blast radius — what is actually wrong, and when

Everything counted from the anchor is 7 days late **on Sundays only**:

| Caller says | Result on a Sunday |
|---|---|
| *"next Friday"* | +12 d instead of +5 |
| *"next week"* | offered the week after next |
| *"not this week"* | same |
| *"next Tuesday"* etc. | +7 d late |
| *"this week"* | ✅ unaffected — `_week_of(today)`, no anchor |
| a literal date (*"the 14th"*) | ✅ unaffected |
| *"tomorrow"*, *"Monday"* | ✅ unaffected — no next-week anchor |

**Severity: books a wrong date, but audibly.** The slot list and every readback
name the date, so the caller can catch it — unlike `B-42` (wrong person, silent)
or `B-36` (phantom, silent). It is a correctness defect against bar 1, mitigated
by being spoken aloud.

**Reachability:** clinics are shut on Sundays but the receptionist answers 24/7,
and "I'll ring at the weekend to book for next week" is an entirely ordinary
caller behaviour. This is not a corner case, it is a seventh of all traffic.

---

## Every regression I can identify in fixing it

### R1 — the `else 7` may have been deliberate
Fixing it means that on a Sunday the prompt says *"This week ends on Sunday
9 August"* when today **is** Sunday 9 August. Someone may have written `else 7`
to avoid that phrasing. It is correct and unambiguous, but it **changes the text
the model sees on Sundays** and could shift behaviour beyond dates.
*Mitigation: change the value, and separately check the sentence still reads
sensibly on a Sunday.*

### R2 — the three copies must move together
Copies 1–3 feed different prompts. Fixing one leaves the halves disagreeing in a
**new** way, which is worse than a consistent error because it becomes
intermittent by code path. *Mitigation: single shared helper, all four sites
reading it, with a test asserting they agree on all seven weekdays.*

### R3 — fixing the anchor does not remove the model's arithmetic
Even with a correct anchor, `after_date` is still computed by the model and
nothing validates it. The anchor bug is the **dominant** cause, not the only one.
*Mitigation: add the `next <weekday>` branch to `_extract_week_range` so
`date_hint` has a deterministic path — but this is now a second, smaller job, not
the headline.*

### R4 — slot sets change on Sundays
Correcting the anchor shifts `after_date` a week earlier for Sunday callers. That
**is** the fix, but it means different slots are offered, and any cached
`v3_last_presented_date_hint` / slot-map state from earlier in the call may not
match. *Mitigation: verify the cache-invalidation path
(`_date_hints_differ_materially`, `llm_stream.py:152`) treats the corrected hint
as materially different.*

### R5 — `A2` interaction, and it cuts the wrong way
`v3_confirmed_slot_phrase` is scraped from the model's spoken text
([connection.py:10122](../../app/media_streams/connection.py)) and Gate 5 then
forces every later readback to **agree** with it. A wrong date is therefore made
*consistent*, not corrected — the caller hears the same wrong date every time,
which makes it **less** likely they catch it. Fixing the anchor removes the
source; `A2` remains a separate hazard and should not be closed by this work.

### R6 — timezone change is its own risk
Making copy 3 use Europe/London corrects it, but any test or behaviour that
implicitly assumed server-local dates will shift. *Mitigation: check `TZ` on the
Render services first; if `TZ=Europe/London` is already set, copy 3's timezone
bug is latent and can be fixed for correctness without behavioural change.*

### R7 — the dead code is a trap for the next person
`_DOW_RE` and `_DOW_INDEX` ([receptionist_tools.py:323-331](../../app/tools/receptionist_tools.py))
are defined and **referenced nowhere**. They look exactly like the machinery a
`next <weekday>` resolver would use, so the next person to read this row will
assume the resolver exists and is broken. *Mitigation: either wire them into R3's
resolver or delete them — do not leave them.*

### R8 — 1-in-7 reproducibility defeats casual verification
A dial-time check on a Monday proves nothing. *Mitigation: the fix must be
verified by unit tests over all seven weekdays; a live call can only confirm, and
only if placed on a Sunday.*

### R9 — the tests will not catch a regression here today
No existing test exercises the Sunday branch (the bug survived two months). A fix
without a seven-weekday test would be unverifiable by the diff method we rely on
for the port.

---

## Recommended sequencing

1. **One shared date-context helper**, correct on all seven weekdays and
   explicitly Europe/London, used by all three prompt-side copies and asserted
   equal to the tool-side resolver. Test across all seven weekdays and both BST
   and GMT.
2. **Check `TZ` on the Render services** before deciding how much of R6 is live.
3. **Then** the `next <weekday>` branch in `_extract_week_range` (R3), wiring or
   deleting the dead `_DOW_*` (R7).
4. **Leave `A2` open** and cross-reference it — this work removes a source, not
   the "consistent, not correct" hazard.

**Revised estimate: item 1 is ~1 hour and closes the observed symptom.** The
register's "half a day" belongs to items 3 and 4, which are now optional
hardening rather than the fix.
