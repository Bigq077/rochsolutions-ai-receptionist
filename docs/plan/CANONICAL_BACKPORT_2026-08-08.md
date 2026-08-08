# Canonical back-port — Theorem's engine fixes onto `latency-eval`, 2026-08-08

**Pass 2 of the Theorem → Vital Edge port.** Pass 1 fixed the starved clinic;
this repairs the canonical-first violation behind it, so `jv-v1-onboarding` and
every future clinic inherit these fixes instead of rediscovering them.

**Base:** `82ac6e3`. **34 commits.** Zero regressions.

| | failures | passing |
|---|---|---|
| Baseline `82ac6e3` | 96 | 3946 |
| After the back-port | **96** | **4433** |

Failing set **byte-identical** to baseline, verified with `comm`. 487 more tests
passing. All modules import clean; `app/clinics/` and `susie_system_prompt.py`
untouched.

---

## 1. Why these commits were stranded

Thirty-one engine fixes were committed straight onto `theorem-onboarding`
between 4 and 8 August, during live-call debugging. That breaks the
canonical-first rule in CLAUDE.md §2: engine fixes land on `latency-eval` first
and clinics inherit by cherry-pick. The cost of the violation is exactly what
Pass 1 had to pay — Vital Edge was three days behind on fixes that had nothing
to do with Theorem, and finding that out required diffing 90 commits by hand.

## 2. Deliberately NOT ported

### `6f664a4` — SMS_ENABLED default flip. **Must never land here.**

It flips `SMS_ENABLED`'s in-code default to ON. That is right for a live clinic
branch and actively wrong for this one: `latency-eval` is an isolated
timing-eval service that must never text a real caller, and the comment at
`app/notifications/sms.py:159` says so. Verified after the port — the default is
still `false`, and `APPOINTMENT_REMINDERS_ENABLED` is still `false`. **A future
port that "completes the set" by bringing this across will make the eval branch
text real patients.**

### The location family — `6901ffb`, `a1cbb8c`, `a233a5c`

`6901ffb` conflicts, dragging `docs/plan/THEOREM_CALL_SUITE_V2.md` and two
theorem-specific test files. Resolving it would push Alcester/Redditch
behaviour into the shared engine — the thing CLAUDE.md §5 explicitly forbids
("if you find yourself writing `if clinic == "..."` in `app/`, stop"). These
stay on `theorem-onboarding`, where they are already deployed and verified. Any
clinic that later needs multi-site disambiguation should take the *behaviour*
through `clinic.json`, not these commits.

## 3. `9ca1ce2` needed a gate — and the suite could not see why

Theorem's reason-question suppression (Gate 5b-r) arrived **unconditional**.
Ungated on canonical it is a silent booking-failure landmine for Vital Edge.

The trap is that **it tests green**. Applied ungated,
`test_reason_question_once` passes all 26 assertions, because Vital Edge's
*mandated* wording —

> "Is there a particular area or reason for the massage — like back tension,
> general stress, or something else?"

— does not match `_REASON_QUESTION_RE`. But the model composes each turn freely.
On `CA86c320ef` it improvised *"Right — What's the appointment for?"*, which the
regex **does** strip. From there:

```
stripped -> never spoken -> note_reason_question_asked never latches
         -> no reason collected -> book_appointment REFUSES
```

A live clinic silently unable to book, caused by a fix for a different clinic,
invisible to every existing test. `902411a` flagged this direction when it built
the latch; this is the same hazard arriving from the other side.

**Fix (`d7da581`):** gate on `prompt_facts.reason_question` via `get_clinic`,
mirroring `bec1b5e` rather than inventing a convention, failing **closed** so an
unknown clinic or any error keeps today's suppression. It keys off config, not a
clinic name, and a test asserts the function contains no hardcoded clinic id.

Tests pin the **improvised** phrasing, not the mandated sentence — the mandated
sentence passes with the gate deleted and would prove nothing. A precondition
test fails if that fixture ever stops matching the regex, so they cannot go
vacuously green.

## 4. The filler reconcile, again

Same conflict as Pass 1, same resolution, carried across as `3ce2a29`.
`latency-eval` owns the cooldown clock (`265d95e`) and now takes Theorem's
`skip_primary` (`8ce4b74`). Both are kept; `skip_primary` runs first and **must**
call `note_filler_played` before speaking its secondary, or that audio is
invisible to the other two producers — the `265d95e` defect reintroduced through
the one path that bypasses its check.

## 5. State after both passes

| Branch | Has the 31 engine fixes | Notes |
|---|---|---|
| `theorem-onboarding` | yes (origin) | plus the location family, plus ungated Gate 5b-r |
| `vitaledge-onboarding` | yes (Pass 1, `211e236`) | no Gate 5b-r — VE asks the reason on purpose |
| `latency-eval` | yes (this) | Gate 5b-r present and clinic-scoped |
| `jv-v1-onboarding` | **no** | inherits from here whenever it is next re-cut |

## 6. Open

- **`jv-v1-onboarding` has none of this yet.** It is a live clinic branch and is
  now the most behind. That is the next port.
- **Theorem still carries Gate 5b-r ungated.** Harmless there (no
  `reason_question` in its config, so the gate would be a no-op), but carrying
  `d7da581` across would let Theorem re-inherit from canonical without a
  divergence.
- **FillerGuard still does not feed the cooldown clock.** Its clip suppresses
  `with_filler`'s phrase but not `connection.py`'s or `llm_stream.py`'s. Real
  gap, new behaviour, not folded into a port. Belongs here, with its own test.
