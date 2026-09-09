# The four-commit slot chain, 9 Sep 2026 — what happened and why

Written because the owner said, correctly, that this was going in circles.
Four commits in one afternoon, each fixing the previous one's fallout:

| commit | what it did | what it broke |
|---|---|---|
| `a08778f9` | D8 — pin the time the caller named back into the readout | nothing, but it was **inert** on northgate |
| `7f9c066e` | restore the weekday the model dropped from `day_window` | nothing |
| `60daf9fb` | nearest-time match, N-1 + N-2 | **"wednesday around 12" → Monday** |
| `69507b88` | tie declines; a bare weekday refuses another day | — |

## The pattern, stated once

**Enabling a dormant code path exposes every guard on it that was never
load-bearing.** The guards were not wrong; they were written while nothing could
reach them, and they say so:

* `day_named_by_caller` — *"A PARTIAL naming — 'that wednesday' — matches nothing
  … That is Tier 2, out of scope on purpose: it needs its own corpus."*

True and safe, until `resolve_requested_time` learned to read "around 12". Then a
caller naming Wednesday was offered Monday 14th, because the guard that should
have refused it had never had to.

Checked for siblings: the other two Tier-2 weekday deferrals in this file
(`_offered_day_by_weekday`, and the deny-by-default guard at ~line 673) already
handle a bare weekday in the safe direction. `_reject_if_caller_named_another_day`
was the only consumer that did not.

## Two supporting mistakes, both mine

**Validating the wrong axis.** D8 was replayed over 2,509 caller *utterances* and
**zero slot grids**. The parser was right; the matching was wrong. northgate's
diary steps in 50 minutes — 08:00, 08:50, 09:40, 10:30, 11:20, 12:10 — so only
two round hours exist in a whole day and an exact match could essentially never
fire. Measured across 383 real day-grids from `calls.slot_offers`:

    northgate    50min x2588, 35min x162
    theorem_v3   60min x300,  120min x54
    jv_v1        45min

**A safety property that lived in an idiom.** The old `len(time_hits) == 1` meant
two things: "found it" and, silently, "decline when the same time sits on several
days". `remaining` spans the whole sweep, so that second meaning was doing real
work. The nearest-match kept the meaning that was named and lost the one that was
implied.

## What is now true

* Tie ⇒ decline, written down rather than implied.
* A bare weekday refuses a hit on another weekday. **Refusing** on a weekday does
  not need the Tier-2 corpus that **selecting** on one would: a false positive
  declines and the caller is asked again; a false negative books them into a day
  they never said.
* `nearest_time_index` is the one owner of "which slot did the caller mean?",
  used by both the readout pin and the follow-up resolver — the exact-match bug
  existed in both, independently.
* Tolerance is ±20 min but only for `:00/:15/:30/:45`. A caller saying "twenty to
  ten" is quoting a slot back, and must not drift. That hazard was caught by the
  grid replay **before** shipping.

## Before enabling a path next time

1. `grep -n "Tier 2\|out of scope\|needs its own corpus\|nothing reads"` over the
   functions it will newly reach. Those are dormant guards about to go live.
2. When replacing a matcher, write down what its **return shape** guaranteed
   (`== 1`, `is None`), not only what it computed.
3. `python scripts/replay_slot_decisions.py --diff BASE.json CAND.json` — 1,845
   scored turns, and it reported **CHANGED: 0** for this chain. It does NOT cover
   the readout pin (obs stores speech, not payloads); that needs the grid replay
   against `calls.slot_offers`.
4. Never run the suite in a worktree you are still editing. Done twice today, and
   both times ~55 `inspect.getsource` tests failed and read as a real regression.

## Verified state

`latency-eval` = `69507b88`. `production` = `5f1003c9`, untouched — none of this
reached a patient line. Full suite 97 failed / 9659 passed, failing set
byte-identical to the baseline (md5 `6b563465`) at every step of the chain.
