# The booking readback does not read from the record — measured, and scoped

7 September 2026. Prompted by `CA8d5b2e3e`, where the caller heard
"So that's Quentin **Roch**" and the record was written `Quentin **R-O-C-H**`.

That parse defect is fixed separately (`1a8d19c6`). This document is about the
thing it exposed: **the sentence the caller confirms is not built from the thing
that gets written.**

---

## 1. Where the two sources are

There IS a mechanism that injects the stored name into the readback —
`_rb_name = collected["full_name"] or collected["name"]`,
`app/media_streams/llm_stream.py:5876`. It is gated by
`_post_collect_readback_due`, whose first line is

```python
if tool_name != "check_availability":
    return False          # llm_stream.py:1302
```

So it fires only when the model attempts *another* `check_availability` after
the details are settled and that call is **blocked** in favour of the readback.

On the ordinary path the caller confirms their number, the model goes straight
to the readback, no tool call is attempted, and the sentence is composed **from
conversation history**. `CA8d5b2e3e` has no `check_availability BLOCKED —
forcing booking readback` line, which is why it said "Roch" while the store held
"R-O-C-H".

---

## 2. How often the two disagree

Over the whole obs corpus: **319 calls with a stored name, 273 of them
containing a real booking readback** (matched on the exact shape — a
capitalised name, a comma, a weekday; "That's reassuring" is not a readback and
an earlier looser matcher counted nine of those).

| | count | |
|---|---|---|
| spoken == stored | 199 | |
| model said the **first name only** | 67 | **25%** — the surname is absent from a quarter of readbacks |
| **disagree** | **6** | **2.2%** |

The six, dated:

| date | clinic | stored | spoken | booked |
|---|---|---|---|---|
| 27 Jul | jv_v1 | `Tom Green` | `Come` | **yes** |
| 27 Jul | jv_v1 | `Quinton Rock` | `Quentin` | **yes** |
| 3 Aug | jv_v1 | `Quentin Roch` | `Quentin Rook` | **yes** |
| 23 Aug | jv_v1 | `Actually` | `Jane Smith` | no |
| 23 Aug | jv_v1 | `Been` | `John Smith` | no |
| 7 Sep | northgate | `Quentin R-O-C-H` | `Quentin Roch` | no |

**Three of the six reached a real calendar.** Five of six are `jv_v1` — a live
patient line — though that is also where most of the corpus is.

### The direction is the scoping fact

It is not one defect. It runs both ways:

* **store wrong, speech right** — `Actually`, `Been`, `R-O-C-H` (3)
* **store right, speech wrong** — `Come`, `Rook` (2)
* **both differ** — `Quinton Rock` spoken as `Quentin` (1)

Reading the readback from the store would fix the second class outright, and
would surface the first class **audibly** — Susie saying "So that's Actually,
Monday the 24th…" is absurd, and a caller corrects an absurd sentence. Today
they confirm a plausible one and the absurd version goes to the diary silently.

---

## 3. What it would cost

Stored names that fail the project's own name stoplists
(`NAME_FALSE_POSITIVES`, `SURNAME_STOPWORDS`): **3 of 319 — 0.9%**, all
`'Would'`, all on one day. So roughly one call in a hundred would have a silly
name spoken aloud unless the change guards for it, which it should: fall back to
today's behaviour when the stored name fails the stoplists, rather than
broadcasting a parse failure.

The bigger behavioural change is the 25%. Sixty-seven readbacks currently name
only the first name; under this change they all gain a surname. That is the
point — but it lengthens the most sensitive sentence in the call and makes any
wrong surname audible, so it is the owner's call, not a silent fix.

---

## 4. Scope

**Recommended: extend the existing injection, do not build a second one.**

1. Extract the `_rb_name` / `_rb_loc` / `_rb_slot` injection block
   (`llm_stream.py:5866-5905`) into a helper with one owner. It is currently
   inline inside the blocked-tool branch, which is why it has only ever been
   reachable from there.
2. Call it on the phone-confirm transition as well —
   `connection.py:9234` and its sibling at `:8957`, both of which already log
   *"LLM will produce booking readback"*, so the moment is already identified.
3. Guard on plausibility: if the stored name fails the stoplists, keep today's
   behaviour. One call in a hundred, and speaking `Would` aloud is worse than
   the status quo for that call.
4. Tests: a regression file, plus a replay of all 273 stored readbacks to
   confirm the 199 agreements are untouched.

**Effort:** roughly half a day, plus one live call. It changes what Susie says
at the confirmation turn, so it does not ride along with anything else.

**Out of scope, and worth saying plainly:** this does not close the family. It
reconciles the model's memory with the record. If STT mishears and the model and
the store agree on the *same* wrong name, the readback is consistent, the caller
hears their own name wrong and confirms it, and everything downstream matches.
The only defence against that is asking the caller to confirm a spelling, which
is a product decision about call length rather than an engineering one.
