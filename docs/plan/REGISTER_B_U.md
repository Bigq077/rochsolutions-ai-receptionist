# B/U register — sweep defects and unverified items

**Opened 2026-08-02. Statuses reconciled against the code 2026-08-03 at
`00ae6df`.** Branch `latency-eval`.

> **Reconciliation note, 3 Aug.** Before this pass, four defects fixed in code
> (`B-33`, `B-38`, `B-43`, `B-09`) still read **OPEN** here, and `B-45` did not
> exist on paper at all. Every section heading below now carries its commit SHA,
> and any row without one is genuinely open. **If this file and the code
> disagree, the code wins** — that is `CLAUDE.md` §7, and this file has now been
> on the wrong side of it once.
>
> **Coverage pass, same day — the more important half.** The reconciliation above
> made this file internally correct and left it **incomplete**, which is worse,
> because an internally consistent register invites you to trust it as the whole
> list. Six live defects were in no register at all and are now folded in as
> `B-46`–`B-51`. One of them, `B-46`, is a P1 affecting both live clinics.
> **This register is not the whole list until `ls docs/plan/` agrees it is** —
> several plan documents are untracked, so git history searches will not find
> them.

This register was carried in conversation only until now. Everything below was
re-checked against the code — originally at `e5a8ee9`, and re-anchored at
`13dd9f3` for `B-15`, `B-25` and `A3` — before being written down; where a row
rests on a claim I could not anchor to a file and line, it says so rather than
implying more confidence than exists.

> ⚠️ **ID collision — read this before citing any ID.**
> `DEFECT_REGISTER.md` uses **`B1` / `B2` / `B3`** (no hyphen) for the 25–29 Jul
> obs sweep: wrong screen for complaint, screen after confirmation, escalation on
> a greeting. Those are **unrelated** to `B-01`…`B-20` here. Always write the
> hyphen, and never merge the two tables.

**Two classes:**

- **`B-nn`** — defects found by reading code and logs. Each is a claim about
  behaviour.
- **`U-nn`** — *unverified* items: code that shipped with a regression test but
  has never been exercised on a live call. Not defects. They are debts that only
  dial time can pay, and they belong to the call suite, not to a code queue.

---

## `B-55` · Vital Edge is **instructed** to narrate a reschedule as confirmed — **FIXED 2026-08-04** (prompt half), gate half **left open deliberately**

**Not found by dialling. Found while running `VITALEDGE_PORT_PLAN.md` §7.1
against the rendered prompt.** No live occurrence yet — Vital Edge has no obs
corpus at all (see the §7.2 note below), so "no occurrence" here means
*unobserved*, not *does not happen*.

Vital Edge books **provisionally**: Jonathan confirms out of band, so no
appointment is ever confirmed on the call. `clinic_template_prompt.py`'s
`is_provisional` arm rewrites the **booking** success line and explicitly bans
`'all booked'`, `'confirmed'`, `'you're booked in'` and any claim that a text was
sent.

`is_provisional` has **four** sites in that file, and none of them touch
reschedule or cancel. So the shared closings render verbatim for Vital Edge:

```
RESCHEDULE CLOSING — say this EXACT line, word for word, changing ONLY the
day, date and time: 'That's you rescheduled — you're now in for Monday the
1st of June at three in the afternoon. We'll see you then — take care.'
```

**On a provisional clinic that sentence is false.** Moving a pending request
leaves it pending; the caller is told they are "now in for" a time Jonathan has
not agreed to. `cancel_appointment`'s *"That's all done — your appointment has
been cancelled"* is the milder case and may well be true — deleting a pending
request is a real deletion — so this entry is about **reschedule**.

**Worse than the booking case, in one specific way.** For booking the prompt
*bans* the false sentence. For reschedule the prompt **mandates** it, word for
word. There is no model judgement to rely on.

### Gate 5f does not cover it either

Same structure as the booking gap in `VITALEDGE_PORT_PLAN.md` §7.2, one step
further along. `_armed_write_families`
([turn_handler.py](../../app/media_streams/turn_handler.py)) arms the reschedule
family **only on a refusal**. A *successful* reschedule refuses nothing, so the
guard is not armed and the mandated line is never examined — by design, and
documented as such in the B-36 cause-2 comment ("a SUCCESSFUL reschedule refuses
nothing, so on that turn the guard is not armed").

That reasoning is correct for every confirmed-booking clinic. It does not hold
for a provisional one, where a successful write still does not make the
completion sentence true.

### Not yet a fix — the decision is the owner's

Two shapes, and they are not equivalent:

1. **Prompt** — give the `is_provisional` arm a reschedule closing, the way it
   already has a booking closing. Smallest diff, consistent with how the booking
   case is handled, and it is the only half that Vital Edge's own prompt can fix.
2. **Gate** — arm Gate 5f for provisional clinics regardless of the refusal
   signal. This is the same OR-not-replace shape as B-36 cause 2, and the same
   caution applies: `booking_write_confirmed` is load-bearing elsewhere
   ([slot_followup.py](../../app/tools/slot_followup.py)), so do not stop setting
   it.

**Do 1 before 2.** §7.2's standing instruction — *do not write code against the
provisional gap until it is observed* — was written about the booking sentence,
where the prompt already bans it and the question is whether the model obeys.
Here there is nothing to observe: the prompt tells Susie to say it.

### ✅ Fixed — the prompt half

`is_provisional` now selects the reschedule closing, exactly as it already
selected the booking closing. Vital Edge is told to say:

```
'That's the new time sent over to Jonathan — Monday the 1st of June at three
 in the afternoon. It's not confirmed until he comes back to you, same as
 before. Take care.'
```

…and is explicitly forbidden *"That's you rescheduled"*, *"you're now in for"*,
*"all set"* and *"confirmed"*. Both old phrases survive in the prompt **only**
inside that prohibition, which the tests check rather than assume.

**Scoped so nothing else moves.** Prompt SHA-256 captured for all five clinics
before and after: `demo`, `jv_v1`, `theorem`, `theorem_v3` **byte-identical**;
`vital_edge` the only change (74,834 → 74,900 chars). Those four hashes are
pinned in the test file, so a later edit that leaks out of the `is_provisional`
branch fails immediately.

Gate 5f now flags **one** claim-shaped line in the rendered VE prompt, down from
two, and the survivor is the cancel closing (see below).

`tests/regression/test_b55_provisional_reschedule_closing.py` — 9 tests.
Suite unchanged at the standing **95 failed** baseline; 0 new failures.

### 🔴 Still open — the gate half, on purpose

Gate 5f remains blind to provisional clinics. The prompt is again the only thing
standing between a Vital Edge caller and a false promise, on both booking and
reschedule. That is the same latent gap `VITALEDGE_PORT_PLAN.md` §7.2 documents,
and closing it is a change to every clinic's write path — it needs its own
measurement, not a same-day follow-on.

**What the fix does buy:** the prompt no longer *mandates* the false sentence, so
the question drops from "will Susie say the wrong thing when told to?" (always)
back to "will Susie volunteer it?" — which for the booking closing measured
**0/30** on 2026-08-04 (see §7.2).

### Cancel — assessed, not fixed

*"That's all done — your appointment has been cancelled"* still renders for
Vital Edge and still trips the detector. Left alone deliberately: deleting a
pending request **is** a real deletion, so unlike the reschedule case the
sentence is arguably true. Worth a decision, not a reflex fix.

### Scope

Vital Edge only, today. Any future clinic with
`operational.booking_system == "google_calendar_provisional"` inherits it. JV and
Theorem are confirmed-booking clinics and are unaffected.

Pinned by two files: `tests/regression/test_vital_edge_provisional_closing.py`
(the booking half — config chain and rendered closing) and
`tests/regression/test_b55_provisional_reschedule_closing.py` (the reschedule
half, plus the four unchanged-clinic hashes).

---

## `B-57` · **Theorem cannot cancel — its mandated CTA does not satisfy the cancel gate** — **FIXED 2026-08-05**

> ✅ **Both halves fixed.** `latency-eval` `7090e4c`; `theorem-onboarding` `d2a3338`
> (applied by symbol, not cherry-picked — the two branches have diverged too far
> in these files for a clean pick).
>
> **Arming half:** `_cancel_retention_asked` now returns True for *either*
> sanctioned wording — the retention question's `"altogether"` as before, or a
> direct cancel CTA via the new `_direct_cancel_cta`. The CTA arm requires BOTH
> an ask shape (`"shall i go ahead"`, `"would you like me to cancel"`, …) **and**
> a cancel verb, so a statement cannot arm the gate, and the booking and
> reschedule re-steers — which carry an ask shape but no cancel verb — still
> cannot. That leak is asserted shut in the tests.
>
> **Consent half — the B-44 loop below.** `_cancel_reply_consents` now takes the
> session and, *only* when the CTA named a single action, accepts a clear
> affirmative through `_book_verdict_deterministic` (which settles negation and
> correction before the yes, and returns `'unsure'` — which blocks — rather than
> guessing). Against the retention question an explicit `"cancel"` token is still
> required, because a bare "yes" answers an OR and identifies nothing. Every
> negation, reschedule word and "keep/leave it" still blocks first, ahead of all
> of this. **No classifier decides a deletion.**
>
> Read through `_cta_asked` rather than `last_bot_prompt` directly, so the
> uncapped `last_question` is consulted too: a cancel read-back naming service,
> practitioner and site runs past the 200-char cap (`B-38`), and the truncated
> form would have silently fallen back to demanding the token.
>
> Regression: `tests/regression/test_b57_theorem_cancel_gate.py`, per arm and per
> prompt — including the negation and re-steer cases, which are the ones that
> must never regress.

**P2. Found by sweep, 2026-08-04, not by a call.** The `B-36 R6` fix (`015eeb0`)
was a single-literal write gate meeting a prompt that mandates different wording.
That is a SHAPE, so all three write arms were swept against all three prompts.
**Six of seven pass. One does not.**

| arm | prompt | gate opens? |
|---|---|---|
| booking | theorem / template / template-provisional | ✅ ✅ ✅ (R6) |
| reschedule | theorem / template | ✅ ✅ (widened `CA23199d08`) |
| **cancel** | **theorem** | 🔴 **NO** |
| cancel | template | ✅ |

`_cancel_retention_asked` ([llm_stream.py](../../app/media_streams/llm_stream.py))
requires `"altogether"`. `clinic_template_prompt.py:2353` teaches *"…or cancel it
altogether?"* — passes. **Theorem mandates the other wording** at
[susie_system_prompt.py:2188](../../app/prompts/susie_system_prompt.py): *"The
CTA is always 'shall I go ahead and cancel that?'"* — no `"altogether"`, so
`cancel_appointment` is refused. The template even calls that phrasing
"redundant" at `:2387`, which is why only Theorem is exposed.

### Why this is P2 and not P1 — the outcome differs from R6

**Theorem's cancel closing IS visible to Gate 5f** (*"That's all done — your
appointment has been cancelled"* → `_false_write_claim` = True), so the phantom
is **stripped, not spoken**. No silent false confirmation. The re-steer then
contains `"altogether"` and re-opens the gate, so it self-heals in one turn.

### But it can loop, and that is B-44 recurring

Recovery needs an explicit cancel token — `_cancel_reply_consents` measured:

    "cancel it" / "yes cancel it"  -> True
    "yes" / "yes please" / "go ahead" -> False

The re-steer asks *"would you like to keep this appointment, or cancel it
altogether?"* — to which **"yes" is the natural answer**, and it blocks again.
`B-44` recorded exactly this: a caller stating an intention to cancel **four
times across 89 s**.

**Reaches Theorem only.** Not screening-scoped. `theorem-onboarding` is about to
take Mark's live traffic, so it lands with a cancel path a caller may not be able
to complete.

**Fix:** widen `_cancel_retention_asked` to accept `"cancel that"` — the same
repair as R6 and as `_move_confirmation_asked`. `_cancel_reply_consents` remains
the second arm, so the destructive write still needs explicit consent. Ships with
the R6 test file's structure: controls per arm, and end-to-end through
`sanitise_response` rather than the predicate alone.

---

## `B-54` · **a real calendar event was cancelled that the caller did not mean** — steering **FIXED `c273475`**, gate **FIXED `9c6fd53`**

**P1. Found live, 2026-08-03 22:46, `CA156fa25206ffa7b15cb3474b617c8672`, build
`68077af59dd3`.** The caller rang to cancel the appointment they had booked four
minutes earlier (15 Aug 11:45).

```
lookup_patient (gcal): match 1/15 name='Quentin Rock' AMBIGUOUS — name must be read back (B-42)
tool result: appointment_time "2026-08-05T20:30:00+01:00"
B-42: looked-up name 'Quentin Rock' was spoken to the caller — identity gate satisfied
cancel_appointment → success
cancelled_event: "Initial Assessment (Musculoskeletal) for Marcus — Quentin Rock"
was_at: 2026-08-05T20:30:00+01:00
```

**`B-42` answers "is this the right PERSON".** All 15 matches were the *same*
person, so saying the name settled nothing — the caller said *"yes it is"* to
their own name and match #1 was cancelled. There was no path for **"that's me,
but not that appointment"**, and nothing told the caller the other 14 existed.

> **This is NOT `B-42` recurring.** `B-42` is the shared-phone /
> different-person case (a couple, a parent, a carer) and its gate worked
> exactly as designed. `B-54` is the same-person / multiple-appointments case,
> which the gate does not model at all. A patient with an initial plus a
> follow-up — entirely routine — hits it.

### Fixed — the steering half (`c273475`)

Both copies of the instruction (`_LOOKUP_AMBIGUOUS_RULE` and the
`identity_confirmation_required` refusal in `llm_stream`) now:

1. **state the count** — *"say how many there are; a caller cannot ask for a
   different one if they do not know others exist"*;
2. require the **day and time** alongside the name;
3. extend the `next=true` escape from *"if they say it is not them"* to
   **"…OR that it is not the appointment they meant"**.

The match count is now on the session (`LOOKUP_MATCH_COUNT_KEY`) — the refusal
message lives in `llm_stream` and only has the session, which carried the
ambiguity *boolean* but not the *number*.

Every `B-42`/`B-44` literal survives; all **34** existing tests pass unchanged.
Weakening the shared-phone guarantee to make room would have been a worse defect
than the one being fixed.

### ✅ Fixed — the gate half (`9c6fd53`, 4 Aug) — and a correction to the row above

> **The cause this row recorded was wrong.** It said the gate *"never checks
> that the caller agreed"*. On `CA156fa25` **the caller did agree** — this same
> entry has them saying *"yes it is"* to their own name. A guard requiring
> agreement would have changed nothing, and building one would have closed the
> row while leaving the defect live.
>
> The agreement was about the **person**; the write was about an
> **appointment**. That is the axis that was unguarded.

`_note_lookup_slot_spoken` ([llm_stream.py](../../app/media_streams/llm_stream.py))
sets `_lookup_slot_spoken` only when the matched appointment's **date** reaches
TTS, and `_lookup_identity_unconfirmed` now requires **both** latches. Two axes,
neither subsuming the other: `B-42` is shared-phone / different-person, `B-54` is
same-person / multiple-appointments.

Matched on **weekday + day-of-month**, both required, word-bounded, digit or word
ordinal. The **time is deliberately not matched** — spoken time forms are a
false-negative factory and on this path a false negative loops a caller entitled
to cancel.

> ⚠️ **Residual, pinned by `test_same_day_duplicates_are_a_known_residual`:**
> two appointments on the **same date** are still not disambiguated. Rarer than
> the initial-plus-follow-up case this closes; needs the time-matching work.

An unparseable datetime **fails closed**, logged at warning; the turn continues
and degrades to taking a message.

`B-42` is not weakened — this only ADDS a condition, and `B-44`'s *"is that
you?"* identity framing is kept **verbatim**: `B-44` pins that literal precisely
because on `CAe74ceae7` the caller answered an appointment-framed question and
confirmed the wrong **person**. The read-back now asks **both** questions.

Suite 95 failed / 3596 passed, failing set diffed and identical. **Not verified
on a call** — B-54 stays dial-time debt until an ambiguous lookup is dialled and
the date is heard before the write.

**Reaches every clinic.** Not screening-scoped, not clinic-scoped.

> **Deployed to `latency-eval` and `theorem-onboarding` only.**
> `vitaledge-onboarding` and `jv-v1-onboarding` **cannot take this by
> cherry-pick** — both are ~300 commits behind and carry **zero** occurrences of
> `_note_lookup_name_spoken` / `_lookup_identity_unconfirmed` /
> `LOOKUP_AMBIGUOUS_KEY`. VE's `llm_stream.py` is 2,320 lines against
> `latency-eval`'s 4,082. Verified 4 Aug: cherry-picking `0dc510d` (B-42, the
> first dependency) conflicts immediately. **They inherit this through the
> re-cut** — `VITALEDGE_PORT_PLAN.md` Item 6 — not by porting the stack by hand.

---

## `B-53` · the L2 classifier's first call was the caller's — **FIXED `80d7234`**, **VERIFIED LIVE**

> **Production-verified 2026-08-03 22:39, `CA33ee0de5`, build `68077af59dd3`.**
> Same phrase, same first-call-after-deploy conditions, opposite outcome:
>
> ```
> FINAL: 'um go for it'
> [ms_llm] L2 classifier: 'um go for it' -> yes
> tool result: success=true, event_id=78cfm5a9e2mr72hr6k738m48fc
> ```
>
> **Asked once.** No `L2 classifier failed`, no re-steer, booking landed on the
> first answer.
>
> ⚠️ **Margin is thinner than it looks:** the warm round-trip measured **~0.92 s**
> against a **1.5 s** budget (`BOOK_CLASSIFIER_TIMEOUT_S`). ~60% utilisation. If
> `L2 classifier failed` is ever seen on a **warm** instance, raise the budget —
> do not blame the prewarm.


**Found on a live call, 2026-08-03 22:22, `CA3a6cfb84`, build `8e12aafe8b39`.**

The caller said *"uh go for it"*. L2 timed out, `_book_reply_verdict` failed
closed, `book_appointment` was blocked, and the caller had to answer *"i said go
for it"* before the booking went through — **an extra turn on the single most
important question in the call.**

**A known hazard, newly observed.** `_classifier_client()`'s own docstring
predicts it verbatim — *"a fail-closed timeout on the caller's first 'go for it'
and nothing in the transcript would explain it"* — and `cf3be18` (2 Aug) built
the client once to prevent it. But it is built **lazily, on first use**, so the
cost moved off calls 2…n and stayed on **call 1 after every deploy or cold
start**, where it is paid inside `BOOK_CLASSIFIER_TIMEOUT_S`. `grep -i timeout`
over this register returned **zero hits** before today: never seen live.

**The sharp edge, recorded because it is the whole reason the earlier fix
missed:** `app/main.py` step 1 *does* pre-warm an Anthropic client — but
`app.flows.conversation._get_client()` is a **different `AsyncAnthropic`
instance with its own httpx pool.** Warming one warms nothing for the other.
`test_prewarm_uses_the_same_client_the_classifier_uses` pins it.

Constructing the client is also not enough: the object is cheap, the expense is
the first request's DNS+TCP+TLS+auth. The prewarm therefore issues **one real
minimal request**, as the Acuity and ElevenLabs prewarms already do — and
deliberately **not** on the per-turn timeout, which is the very budget too tight
to absorb a cold connection.

Non-fatal in every failure mode (disabled, no key, API error, timeout,
un-constructable client). 9 regression tests, all 9 fail before. Suite 95/3515,
failing set identical.

> **Verification is free on the next deploy.** The boot log must carry
> `[ms_llm] L2 classifier TLS pool pre-warmed (Nms)`, and the first booking
> confirmation after a deploy must log `L2 classifier: … -> yes` rather than
> `L2 classifier failed`.

> **Note `"go for it"` reaching L2 at all is BY DESIGN, not a defect** — see the
> table at the `B-37` section. Adding it to the deterministic yes list makes
> *"don't go for it"* book. Do not "fix" it there.

---

## `B-17` · confirmed live, same call

`CA3a6cfb84` logged, two lines apart:

```
[sms] SMS_ENABLED is off — outbound SMS suppressed (not sent)
Booking confirmation SMS sent to ***1207
```

Reports success it never checked, exactly as the row says. Still deferred, but
it is no longer an inference.

---

## Status at a glance

| Track | Items | State |
|---|---|---|
| Closed | `U-06`, `B-18`, `B-13`, `B-14`, `B-15`, `B-25`, `B-31`, **`B-20`**, **`B-26`**, **`B-34`** | Shipped 2–3 Aug with tests. **`B-15` closed on a different defect than the one it was sold on** — see the honest verdict in its section. `B-20` closed by dial time, not by code — see the 3 Aug sweep |
| Parked | `B-01` – `B-04` | Two provisioning items + the name work — owner decision, not capacity. **`A3` now has a live instance, see the sweep section** |
| **Behaviour change pending deploy** | **`B-31`** | **CLOSED 3 Aug.** A 200-char cap had silently disabled the clinical screening layer. Fixed — and a call shaped like sweep call 2 now **escalates to NHS 111 and blocks the booking** where it previously booked. That makes the `B-20` authority decision urgent, not merely open |
| Track A — deterministic, no dial time | — | **EMPTY.** `B-26` closed 3 Aug, `B-09` closed 3 Aug (`00ae6df`) — it was our own off-by-one-week, not model arithmetic |
| ~~Track B — needs an owner decision~~ | `B-19` / `B-07` | **DECIDED AND SHIPPED `8b06879`, 3 Aug** — one re-arm at 5 s, then stop. **`B-30` is NOT closed by it** — different mechanism, see the row |
| Track C — prompt-side, needs dial time | `B-06` `B-08` `B-10` `B-11` `B-12` `B-16` | **`B-10` and `B-16` confirmed live 2 Aug**, see the sweep section |
| Track D — verification only | `U-02` – `U-05` | **Still entirely open.** Sweep call 2 exercised the keypad commit, C2 read-back and the overwrite guard — none of which are `U-nn` rows. `U-03` is the *reschedule* path (call 7) and was not touched |
| **CLOSED — decided, shipped, dialled** | `B-20` (Layer 2 over-screening) | **Option B, owner decision 3 Aug**, then **verified on six live calls the same night** — see the 3 Aug sweep. Screening authority bounded to a matching presentation; the grant is kept, not withdrawn, because 2 of 18 orphans were Layer 1 saves (`B-32`) |
| Withdrawn | `B-24` (claimed Layer 1 coverage gap) | My claim, wrong. Widening those triggers would manufacture `B-20` |
| **Deferred to last** | `B-17`, `B-22` (the SMS family) | Owner decision 2 Aug. **`B-17` is worse than recorded — it has a second consumer.** Revisit warranted |
| New from the 2 Aug sweep | `B-27` `B-28` `B-31` `B-30` | See the 2 Aug sweep section. `B-28` is root-caused by `B-31` |
| **New from the 3 Aug sweep** | `B-36` `B-33` `B-34` `B-35` | **`B-36` was the P1: a reschedule write was BLOCKED and Susie announced success anyway — both causes FIXED (`fe97b82`, `e387aac`, `d5b257c`) and the steering half now production-verified on `CA9cc1a23e`.** `B-34` closed same night. **`B-33` FIXED `c5210a2`** — and the row's inferred mechanism was wrong in every detail. **`B-35` is the only one still open, and it is a Render env var, not code** |
| **FIXED 3 Aug, all test-proven, NONE heard on a live call** | `B-41` `B-42` `B-44` `B-43` `B-38` `B-45` `B-33` `B-09` | `6901c27` · `0dc510d` · `6b34745` · `91524c4` (two rows) · `e108e44` · `c5210a2` · `00ae6df`. **`B-42` and `B-44` are the exceptions — both verified live** (`CAdbc84848`, `CA368775`). The other six are the standing dial-time debt. `B-09` is deliberately unverifiable by phone: it reproduces on **Sundays only** |
| New — blocks nothing, decides `B-20` | **`B-32`** (STT noise defeats Layer 1 triggers) | Two observed misses, both rescued by Layer 2. **Do not fix by adding keywords.** One safe 15-min config addition (`calves`) is separable |
| Withdrawn same night | `B-29` (claimed the DVT grader knew half its question) | My claim, wrong — written from a truncated keyword list quoted in `B-20` rather than from `clinic.json`. **That quotation is now corrected too** |
| **Unrecorded** | **`B-05`, `U-01`** | **See "Gaps" below** |
| **FOLDED IN 3 Aug — were in NO register** | **`B-46`** `B-47` `B-48` `B-49` `B-50` `B-51` | Six live defects that existed only in `THEOREM_PORT_PLAN.md`, `FIX_QUEUE_PRE_DEMO.md`, a 25 Jul sweep note and the clinical-campaign status. **`B-46` is an open P1 on both live clinics and `main` already fixed it.** See the section above "Gaps" |
| **FOUND BY DIALLING** | **`B-52`** | *"by the way"* booked as the surname **"Way"**. **Three weeks old, and eight test-only fixes shipped over it without finding it.** FIXED `dc974f6`. Read its closing note before shipping anything else untested |
| New — plan written | `B-23` (reason re-asked when already given) | `PLAN_REASON_CAPTURE.md`. **Fired live in the F4 shape.** Owner decision open on F5 |

---

## Sweep — 3 Aug 2026, 00:13–00:21, six calls — **`B-20` verification**

Build **`ab39553809fc`**, read from the log itself (`[build_info] running build
ab39553809fc` at call cleanup) rather than from `/health`, which returns a
hardcoded `"version": "1.0.0"` and cannot report a commit. **That line is the
only place the running SHA appears — use it.** Service
`low-latency-joint-venture`, number `+447366263180`, clinic `jv_v1`.

Suite: `CALL_SUITE_B20_VERIFY_2026-08-03.md`. Dialled in the order written, with
the two safety controls first, so that a failure would stop the run before
spending calls on the discriminating tests.

| # | Presentation | Purpose | Result |
|---|---|---|---|
| 1 | calf, denies the red flags | screen must still fire | ✅ `dvt ARMED` → asked → graded `clear` → continued to slots |
| 2 | calf, admits swelling + warmth | escalation must block the booking | ✅ `dvt POSITIVE (block=True)`, push to "book Thursday anyway" refused, no `check_availability` |
| 3 | *"i'd like to book for shoulder pain"* | must not screen | ✅ zero orphans |
| 4 | *"book an appointment for my knee"* … *"just been aching, couple weeks"* | must not screen | ✅ zero orphans |
| 5 | ankle, no journey context | — | ✅ no screen (script not followed; see below) |
| 5b | ankle **+ long journey** + denial | `B-31` must not false-escalate | ✅ booked through, no escalation |

**One `ORPHAN`-family line across six calls, and it was the detector's fault,
not the model's** — see `B-34`. Against the 18-in-133 baseline that is the
result `B-20` was shipped for.

> **What this does and does not prove.** Six calls is not 133, and `B-20` Option
> B was only ever scoped to remove the eight *band-one* orphans. Calls 3 and 4
> are the band-one shapes and both came back clean, which is the claim. Call 5
> going clean on a bare ankle is a band-**two** shape and is *one* observation —
> encouraging, not a measurement. Do not upgrade it into one.

### `B-20` — dial-time debt **PAID**

The row's standing caveat was *"a prompt change ⇒ the test pins wording, not
behaviour"*. Behaviour is now observed on the live build, on both the shapes the
change targets and both safety invariants it must not break. Moving from
*"DECIDED and shipped — needs dial time"* to **closed**.

### `B-31` — confirmed working on a live call, not just in a test

Call 3 produced, verbatim:

```
WARNING last_bot_prompt truncated at 200 chars and lost its '?' — falling back
to last_question for orphan matching (B-31).
  bot='essment so Marcus can take a proper look'
  question='Would you like to book an assessment so Marcus can take a proper look?'
```

That is `c69eb61` catching precisely the failure it was written for and
recovering. Pre-fix that turn reads as question-less and switches the layer off.

### `B-33` · a name was invented from Susie's own utterance — **FIXED `c5210a2`**

Call 5, `CAc3c4e6619660fa69416e8545c9d5674a`, 00:20:05.660. The caller had said
exactly one thing — *"i've hurt my ankle"* — and had given no name:

```
[ms_conn v3] name persisted (normal path): 'Rehab'
v3_phone_dtmf_active = True (name confirmed — phone collection phase)
...
📊 Row built — outcome=abandoned name=Rehab phone=yes dur=33s
```

`_v3_try_persist_name` ([connection.py:10041](../../app/media_streams/connection.py))
extracts the name from the **bot's** last utterance, on the pattern *"Thanks
Sarah — if you'd like to use the number…"*. Susie's ankle reply was a long
clinical explanation and a capitalised word inside it was read as a
confirmation. **The capture is certain; the "capitalised word" mechanism is
inferred and not yet anchored** — read the extractor before scheduling a fix,
per the standing "anchor before scheduling" rule.

Why it outranks its family (`A3` is a *mangled* surname): this is a name with no
caller origin at all, and it armed DTMF phone collection behind it. Type a
number at that moment and the appointment is written under "Rehab". It
self-cleared here only because the next utterance was conversational.

> **FIXED `c5210a2`. The inferred mechanism above was wrong in its specifics,
> and the capitalisation is ours.** Reproducing it end to end found **three**
> faults, not the "capitalised word inside a long clinical explanation" this row
> guessed at. This is the "anchor before scheduling" rule paying for itself
> again — the row was right that something invented a name, and wrong about
> every detail of how.
>
> 1. **Gate 5 manufactures the shape.** It strips a banned opener (*"Of course —
>    "*) and then **re-capitalises the next word** (`turn_handler`, "Fix A"),
>    turning an ordinary mid-sentence noun into a sentence-initial title-case
>    word — exactly what the readback patterns hunt for. Pinned by its own test,
>    because the name layer must stay safe against **our own pipeline** rather
>    than a hypothetical model quirk.
> 2. **Pattern 1d sat in the `ANCHORED` list and did not belong there.** That
>    list's stated criterion is that an acknowledgement verb or readback opener
>    *precedes* the captured word, and `ANCHORED` bypasses the phase gate on
>    every turn. 1d has no leading lexeme at all — only a trailing hint — so
>    *"Massage — if you'd prefer something gentler…"* was read as a name. Moved
>    to `BARE`, where the gate applies.
> 3. **Pattern 1c matched `"right"` as an ordinary mid-sentence adjective** and
>    captured whatever followed: *"doesn't feel right Marcus can take a look"* →
>    `'Marcus'`; *"that's right Bolton is our only site"* → `'Bolton'`. None are
>    in any false-positive list, so **a practitioner's name or the clinic's town
>    could become the patient's name.** Anchored to a sentence boundary.
>
> The fault that closes the observed call is the **phase gate**: a reply that
> *asks* for the name cannot also read one back, because the caller has not
> answered yet. `BARE` now requires `post_slot_pending` alone. The
> same-response ask-and-acknowledge case the gate was widened for is untouched —
> *"Thanks Sarah — and your surname?"* is `ANCHORED`, and `ANCHORED` still runs
> every turn; `CA8f9c5578`'s ungated readback is pinned to prove it.
>
> `test_pattern_split_preserves_the_full_ordered_list` failed on the `BARE` count
> and **was right to** — it demands that any addition be a deliberate
> classification, not an accident. Answered rather than weakened: the count moves
> to 2 with the reason recorded beside it, and assertions on *which* patterns are
> in the list so it cannot be bumped to go green.
>
> 17 new tests. Suite verified by diffing failing node IDs — identical.
> **Not yet heard on a live call.**

### `B-34` · the orphan detector scored a booking CTA as clinical evidence — NEW, **CLOSED same night**

Two sightings, calls 3 and 5. jv_v1's canned `booking_offer` — *"…so Marcus can
take a proper look?"* — collided with `trauma_fracture`'s *"That sounds like a
proper knock"* on the single word `proper`, scoring 1 of the 2 evidence words
needed. `trauma_fracture` carries only four evidence words, so one further
generic collision logs a **false `ORPHAN`** — in the metric `B-20` is scored
against. Fixed by stopwording `proper`; test
`tests/regression/test_orphan_stopwords_reject_booking_cta.py` pins the CTA at
**zero** evidence and pins every screen's own question still matching itself.

### `B-35` · the call-summary sink is unconfigured on this service — NEW

Every call this suite:

```
📊 Row built — outcome=abandoned …
[ms_conn] call-summary row queued to Sheets
WARNING Sheets not configured — GOOGLE_SERVICE_ACCOUNT_JSON present, GOOGLE_SHEETS_ID MISSING
WARNING Sheets append SKIPPED (no client) tab='CallSummaries' rows=1
```

Rows are built, queued, dropped. Bar 4 of the production-ready definition
("every call produces a record; failures alert an operator") is not met on
`low-latency-joint-venture`. Nothing is lost outright — obs and
`logs/calls_YYYY-MM-DD.jsonl` both captured — but the operator-facing sink is
dead and has been failing at WARNING. **Provisioning, not code:** set
`GOOGLE_SHEETS_ID` on the Render service.

### `B-26` — five more sightings, **CLOSED**

Fired after all six calls, unchanged, while synthesis returned 200 throughout.
Demoted to WARNING and reworded to state only what is known; test
`tests/regression/test_prewarm_401_does_not_claim_tts_failure.py` pins the level
and pins both false claims ("credits exhausted", "will fall back to OpenAI") out
of the message.

> **The fix broke `B-13`'s own regression test, and that was the correct
> outcome.** `test_prewarm_status_is_not_ready.py::test_a_401_is_logged_at_error`
> asserted the ERROR level directly, on the premise *"a dead credential … the
> one fault that silences the assistant"*. That premise is true of a 401 from
> `synthesise_chunk` and false of a 401 from `GET /v1/models`. The test has been
> **inverted, not deleted** — it now requires WARNING-or-above, keeping the half
> that was always right (a 401 must not be silent, must not read as success) and
> dropping the half the live evidence refutes. Renamed accordingly, with the
> reasoning in its docstring so the next reader does not "restore" it.
>
> Worth noting as a pattern, alongside the `B-15`/`B-17` scope lesson: **a
> regression test can pin a wrong belief just as durably as it pins a fix.**
> This one would have blocked `B-26` silently if the suite had not been run.

### Call 7 — the reschedule, 00:34–00:36, `CA3f1d124905854919a9b0f3cc554ff80f`

Passed the criteria it reached: acknowledge-and-stop on turn 1, `U-06` consent
**judged** (`L2 classifier: 'uh go go for it' -> yes`), the write succeeded
(`rescheduled_to: Saturday 08 August at 12:30`), and the closing did **not**
promise a text — `ad938cf` holds.

**`U-03` was not exercised.** The caller said *"use this number"*, so the keypad
never opened — and `U-03` is the one row this call was dialled to settle. Moot
now; see below.

#### `U-03` REVERSED — owner decision, 3 Aug

The lookup path now reads a keypad-typed number back exactly as booking does.
The old scoping said a lookup number is a **search key**, not a contact field,
so a wrong digit costs a failed search rather than an unreachable booking.
Overruled because **the caller cannot tell those two apart**: a mistyped key
surfaces as *"I can't find your appointment"*, which sounds like the clinic
losing their booking, and the only party who can catch a digit Twilio mangled
never hears the number.

Implemented as a **second flag**, not a widened one.
`phone_entered_by_keypad` still means "the booking commit accepted this", and
`_commit_dtmf_phone_for_booking` still returns early for cancel/reschedule
before setting `phone_confirmed` — a search key must never satisfy
`book_appointment`'s A1 gate. The new
`phone_entered_by_keypad_for_lookup` buys the read-back without the commit.
Everything else is shared on purpose: wording, `_spell_phone`, the one-shot
pending flag, `_is_phone_readback_rejection`, `_reject_keypad_number`'s
teardown and mandated re-arm line, and the three-rung ladder counter.

**The crumb that would have broken it silently:** on the lookup path the digits
*are* the transcript that drives `lookup_patient`, and the read-back consumes
that turn. On confirmation the digits are now queued, so the model sees exactly
what it saw before, one turn later. Without that the read-back sounds perfect
and the lookup never runs.

#### The half of that decision I missed first time — corrected 00:55

The owner's request was *"the same behaviour as the booking flow **so it reads
the number out loud**"*, **and** the keypad case. The commit above built the
keypad case only. `CAa6910560040bc5b8befab6d1920af7ce` (00:51) hung up on the
first phone turn, which never reaches a keypad, so none of that work was even
touched — and the turn it hung up on was still:

> *"Was your original appointment booked under the number you're calling from?
> If so, just say 'use this number.'"*

That sentence was **mandated by the prompt**
([clinic_template_prompt.py:2187](../../app/prompts/clinic_template_prompt.py))
while step 8 of the *same file* says, in capitals, *"NEVER ask the caller to say
a set phrase, never say 'just say use this number'"*. **One prompt, two halves,
opposite instructions about the same field — the `B-20` shape again.** Now
reworded to step 8's form: digits in three groups, plain yes/no.

**Two of the phrasings the owner named did not work, and were checked rather
than assumed:**

| Utterance | Before | Why it missed |
|---|---|---|
| `"go for it"` | ❌ | three words, none a bare affirmative in `_PHONE_CONFIRM_AFFIRMATIVES` |
| `"that's the number"` | ❌ | the signal list holds `"that number"` and `"this number"`; neither is a substring of `"that's the number"` |

Both added. The `<=3`-word cap and the negative-intent guard are untouched, so
`"yes but call me on my work phone"` still does **not** confirm — a long yes that
redirects is a yes to a different question, and a wrong number is an unreachable
patient.

> This is a **prompt** change, so the same caveat as `B-20` applies: the test
> pins wording, not behaviour. It needs a dial.

> **Known coverage hole, stated not implied.** That queueing branch lives inline
> in `handle_transcript` and is not reachable without standing up a whole
> connection, so `test_lookup_keypad_number_is_read_back.py` (29 cases) does
> **not** assert it. Delete the branch and all 29 still pass while the lookup
> breaks. `CALL_SUITE_2026-08-02.md` Call 7 turn 3 has been rewritten to make
> that the explicit fail condition. **This needs a dial.**

### Verification calls — 3 Aug 09:37 and 09:57, builds `e61c8f805d6e` / `9d9efddb9a22`

| Call | Purpose | Result |
|---|---|---|
| **1** — reschedule that SUCCEEDS | `B-36` **over-fire** check | ✅ **PASS, twice** (`CA8d90deb2` on `e61c8f8`, `CA80f2d410` on `9d9efdd`). Move happened, *"That's you rescheduled…"* spoken **unaltered**, **no `[ms_gate5f]` line on either call**. This is the direction that abandoned a completed booking on 2026-06-12; it is clean |
| **4** — `B-37`, "go for it" | slot-guard bypass | ✅ **PASS.** `[ms_conn] write CTA outstanding — bypassing slot guard for 'uh go for it'` at 09:59:12.040, `iteration=1` 5 ms later, then `L2 classifier: 'uh go for it' -> yes`. The L1-unsure → L2 path, exactly as predicted |
| **2** — reschedule that stays REFUSED | phantom + the **R5 leak** | ✅ **PASS** (`CA9cc1a23e`, 10:03, `9d9efdd`). See below — the richest of the four |
| **3** — cancel | destructive-write path | ⚠️ **PASS on outcome, two NEW defects found** (`CA66d6f1b4`, 10:09). See `B-39` / `B-40` |

#### Call 2, turn by turn — `CA9cc1a23e431b4eb54acfd29d86479315`

```
10:03:21.893  tts   'Shall I go ahead and move it for you?'
10:03:32.467  FINAL 'um i guess so maybe'
10:03:32.471  [ms_conn] write CTA outstanding — bypassing slot guard      <- B-37
10:03:35.215  [ms_gate5g] removed self-narration: "That's a soft
              affirmative to a reschedule readback — treating it as yes."
10:03:37.443  L2 classifier: 'um i guess so maybe' -> no
10:03:37.444  reschedule_appointment BLOCKED — no clear caller yes
10:03:37.444  guard ARMED for the reschedule family this turn             <- B-36 2a
10:03:38.626  tts   'Shall I go ahead and move it for you?'               <- re-asked
--- next turn ---
10:03:48.676  [ms_conn] write CTA outstanding — bypassing slot guard
10:03:52.644  L1 verdict: 'um yeah go for it' -> yes
10:03:53.462  result={"success": true, "rescheduled_to": "Monday 10 August at 19:30"}
10:03:55.418  tts   "That's you rescheduled — you're now in for Monday..."  <- UNALTERED
```

**Five things verified in production by this one call:**

1. **The refusal marker arms** (cause 2a). First production firing of `d5b257c`.
2. **2d works, and worked *first*.** The model was told the move had not
   happened and **did not claim it had** — it re-asked. Gate 5f never needed to
   fire. This is the "also acceptable" pass the call sheet anticipated: the
   steering layer is the first line, the guard is the backstop.
3. **No R5 leak.** The re-ask was the **move** CTA. No booking CTA reached
   `last_bot_prompt`, so the caller's next *"yes"* could not have booked a new
   appointment. (Indirect evidence — Gate 5f's own re-steer was not exercised.)
4. **The marker is genuinely turn-scoped** (R2). It was ARMED on the blocked
   turn and the *very next turn* spoke a legitimate `"That's you rescheduled"`
   **unaltered**. That is the exact over-fire that abandoned a completed booking
   on 2026-06-12, reproduced as a scenario and **not** triggered.
5. **`B-37` on both reply shapes** — an ambiguous reply and an affirmative,
   neither dropped.

**Also notable:** the model narrated *"That's a soft affirmative … treating it as
yes"* and called the tool. **The model and the gate disagreed, and the gate won.**
Without FM-23 this call would have rescheduled on *"um i guess so maybe"*.

> ### ✅ SUPERSEDED 2026-08-03 23:22 — **Gate 5f fired in production, and it worked**
>
> `CA3a6cfb842684c894b743733c9914ab10`, build `8e12aafe8b39`:
>
> ```
> L2 classifier failed (TimeoutError()) — failing closed to a re-ask
> book_appointment BLOCKED — no clear caller yes (last_user_text='uh go for it')
> book_appointment did not succeed (status='affirmation_required')
>     — false-confirmation guard ARMED for the booking family this turn
> [ms_gate5f] false booking confirmation with no successful write (armed=booking)
>     — re-steering: "All booked — you're in for Saturday the 15th of August
>        at eleven in the morning."
> ```
>
> The write was blocked, the model narrated **"All booked"** anyway, and the
> guard caught it and re-steered. **A phantom booking, prevented on a live
> call.** The guard half is now production-verified, not test-only.
>
> **And it did not over-fire.** Later in the same call, after the write actually
> succeeded, *"All booked — you're in for Saturday the 15th at eleven…"* was
> spoken normally — no `gate5f` line, no re-steer. That was the regression check
> for `a4c267d`, and it passed.
>
> ⚠️ **`a4c267d` was NOT what caught this.** The claim carried no `?`, so the
> pre-`a4c267d` code would have caught it too. Do not credit the sentence-level
> fix with this firing — its own case (a claim beside a mandated question) has
> still never been seen live.
>
> The paragraph below is retained for the reasoning it records.
>
> **What was still NOT verified at the time of writing: Gate 5f itself has never fired in production.**
> Its re-steer text, its family attribution and the R5 isolation remain
> **test-only** evidence. That is not a gap that can be closed by dialling
> harder — reaching it requires the model to claim success *despite* being told
> not to, which is exactly the non-determinism `CALL 5` vs `CALL 12` recorded in
> the first place. Gate 5f is a **backstop**, and a backstop that never fires in
> three calls is the expected result, not a failure. Record it as: steering half
> **production-verified**, guard half **test-verified only**.

#### Call 3 — cancel, `CA66d6f1b4571b3209546c126dede8d6c9`

```
10:10:02.629  tts   'Would you like to reschedule this appointment, or cancel it altogether?'
10:10:08.507  FINAL 'uh yes'                        <- bare yes, per the script
10:10:10.334  tts   'Just to check — would you like to reschedule it to a new tim…'
10:10:17.508  FINAL 'uh cancel'
10:10:19.366  tts   'Got it — and just before I do, would you like to reschedule …
                     or are you happy to cancel it altogether?'
10:10:29.491  FINAL "i said i'd like to cancel it"  <- audible frustration
10:10:30.641  HTTP 200 (LLM stream opens)
              ... 9.9 SECONDS OF SILENCE, no filler ...
10:10:40.550  tool  cancel_appointment
10:10:41.849  result {"success": true, "cancelled_event": "… Quentin Rock"}
10:10:44.223  tts   "That's all done — your appointment has been cancelled."
```

**What passed:**

- **A bare "yes" did not cancel.** Correct outcome — but via the *model*
  re-asking, not the gate. `cancel_appointment` was never called, so there is no
  `BLOCKED` line. **The cancel gate's blocking arm remains unexercised in
  production**, the same status as Gate 5f.
- **No over-fire on the legitimate cancellation.** *"your appointment has been
  cancelled"* matches the new `_FALSE_CANCEL_CLAIM_RE` exactly, and Gate 5f did
  **not** touch it — because the write succeeded, so the cancel family was never
  armed. That is cause 2c dissolving, verified for the cancel family on a
  destructive write.

### `B-41` · Susie said *"Their choice is to cancel."* out loud — **FIXED** `6901c27`

`CA12db707b1b887d38b7408aa36fc990d6`, 10:16:19. Third-person internal reasoning
reached TTS and the caller heard it:

```
10:16:19.358  [ms_gate5] removed banned phrase (lookup_reasoning_leak)
10:16:19.360  [ms_tts] synthesise_chunk: text='Their choice is to cancel.'
10:16:19.984  [ms_gate5] turn complete: 0 chunk(s) dropped as reasoning
```

**Reproduced offline, deterministically** — this needs no further dial time:

```python
th.sanitise_response("The caller wants to cancel.", s)   -> ''        # stripped
th.sanitise_response("Their choice is to cancel.", s)    -> unchanged # SPOKEN
th.sanitise_response("Their preference is to reschedule.", s) -> unchanged
th.sanitise_response("That is what they want.", s)       -> unchanged
```

**Cause.** `lookup_reasoning_leak`
([turn_handler.py:101](../../app/media_streams/turn_handler.py)) is a
**sentence-level** strip (Gate 5b): it removes only the sentence carrying
*"look up the patient/details"*. The model generated **two** reasoning sentences;
the sibling had none of that vocabulary and survived. Gate 5a's whole-chunk
reasoning drop did not fire either — `_get_reasoning_drop_reason` returns `""`
for it, and the log confirms `0 chunk(s) dropped as reasoning`.

**The gap is grammatical, and that is what makes it fixable.** The detectors
catch first person and *"the caller"*; they do not catch **third-person
possessive** narration about the caller — *"Their choice…"*, *"Their
preference…"*. Susie addresses the caller as *you*, so a sentence whose subject
is *their/they* referring to the caller is internal by construction, exactly the
argument the existing `lookup_reasoning_leak` comment already makes for *"the
patient"*.

Not a safety defect — the cancellation was correct and correctly confirmed. It is
**demo-audible**, and it is the kind of thing a clinic owner on a webinar
remembers.

**FIXED `6901c27`.** A decision-noun arm added to **Gate 5g**, not to the flat
`_BANNED_SENTENCE_RE` list — 5g is structural, additionally requiring *no
second-person reference* and *no question mark*, and those two guards are what
make a broader pattern safe to add. Both are exercised: *"We'll confirm their
preference with you"* survives on the second-person guard, *"Is their preference
the afternoon?"* on the question guard.

**Deliberately not a bare `they|their` arm, with a test asserting it stays that
way.** The clinic is also *"they"* — *"They close at six."* and *"They're fully
booked that day."* carry no second person and no question mark, so a bare arm
would delete them from real caller audio. That direction is the Gate 5c failure
of 2026-06-12 that abandoned a completed booking. **Under-firing is the correct
bias here:** a missed leak is embarrassing, a deleted sentence is a broken call.
*"That is what they want."* is knowingly uncovered and pinned as a deliberate
trade rather than left silent.

Test `tests/regression/test_b41_third_person_caller_narration.py`, 31 cases,
including the verbatim two-sentence chunk from the call and eight legitimate
third-party sentences that must survive. Suite verified by diffing failing node
IDs: identical, no test moved. **Not yet heard on a live call** — the fix is
proven offline against the exact recorded text, so dial time is confirmation, not
evidence.

### `B-42` · **a cancellation was confirmed against the wrong person's appointment** — **FIXED `0dc510d`, VERIFIED LIVE**

`CAe74ceae7002d6cff1ba8a324f04cf134`, 3 Aug 10:39, build `aa964ff8d173`. Caller
was `+447502211207`. The appointment cancelled belonged to **Sarah Jenkins**.

```
10:39:01.704  lookup_patient (gcal): match 1/13 name='Sarah Jenkins'
10:39:04.040  tts 'I can see an appointment on Wednesday the 5th of August at q…'
10:39:04.251  tts 'is that the right one?'          <- NO NAME SPOKEN
10:39:12.265  FINAL 'yes'
10:39:26.247  cancel_appointment args={"patient_name": "Sarah Jenkins", …}
10:39:26.890  {"success": true, "cancelled_event": "… for Marcus — Sarah Jenkins"}
```

> **Scope this honestly.** On *this* call it is test-data contamination — the
> test calendar holds 13 future appointments under one phone number, booked
> under assorted names across the morning's calls (`Quentin Rock`,
> `Quentin Rook`, `Quinton Rock`, `Sarah Jenkins`). **No real patient was
> harmed.** But the *mechanism* is not test-specific and it is the worst class
> of failure this system has.

**Mechanism**, [`_lookup_patient_gcal`, receptionist_tools.py:6397](../../app/tools/receptionist_tools.py):

```python
matches = [ev for ev in events
           if (name_lower and name_lower in name(ev).lower())
           or (pk and pk == _phone_key(_gcal_event_phone(ev)))]
matches.sort(key=lambda e: e["start"]["dateTime"])
return _emit(matches[0], 0, len(matches))     # <- earliest match, unconditionally
```

Two failures compound:

1. **Phone-only match, first-of-N, silently.** The lookup takes the earliest
   upcoming appointment on that number with no disambiguation.
2. **The readback omits the patient name.** Susie said the day and the time and
   asked *"is that the right one?"*. The caller confirmed a **date**, never a
   **person**.

**Why this is a production risk, not a test artefact:** a shared phone number is
completely ordinary in physiotherapy — couples, a parent booking for a child, a
carer. Caller rings from the family mobile, the earliest appointment on that
number is their partner's, Susie reads a plausible day and time, the caller says
yes, and **the partner's appointment is cancelled** with no one aware.

**The tool already returns everything needed to prevent this.** `_emit` sends
`match_count` and `has_more`, and the lookup supports `next=true` to step through
matches. The model received `match_count: 13, has_more: true` and neither said
the name nor offered the next match.

**Recommended fix — a gate, not a prompt line.** A wrong cancellation is
destructive and the caller has no way to know it happened. Block
`cancel_appointment` / `reschedule_appointment` when the active lookup returned
`has_more: true` **unless** the patient name has been spoken to the caller and
confirmed. Same shape as the surname and phone backstops already guarding
`book_appointment`. Prompt wording alone is what B-36 cause 1 proved insufficient.

#### `B-42` — verified end to end on `CAdbc84848`, 10:55, build `0dc510d196f1`

Every link in the chain fired, in order:

```
10:54:45.279  lookup_patient (gcal): match 1/12 name='Quentin Rock'
              AMBIGUOUS — name must be read back (B-42)
10:54:46.977  tts 'I can see an appointment on Wednesday the 5th…'   <- STILL no name
10:55:04.275  FINAL "i'd like to cancel it"
10:55:08.041  cancel_appointment BLOCKED — ambiguous lookup, name not
              read back (B-42): name='Quentin Rock' matches>1          <- GATE
10:55:08.041  false-confirmation guard ARMED for the cancel family     <- B-36 composes
10:55:09.433  tts "I've got that appointment under the name Quentin Rock — is t…"
10:55:09.463  B-42: looked-up name 'Quentin Rock' was spoken to the
              caller — identity gate satisfied                         <- RELEASED
10:55:34.070  {"success": true, … "Quentin Rock"}                      <- right person
```

The model **complied with the refusal message** and read the name back in its own
words. The gate then released deterministically off what was spoken, and the
correct person's appointment was cancelled.

**Side effect: the `patient_name: "Unknown"` lead is resolved.** The blocked call
carried `"Unknown"`; the retry after the identity readback carried
`"Quentin Rock"`. Forcing the name into the conversation puts it into the write
args as well. Lead 1 can be struck.

### `B-44` · the identity check is in the right place for safety and the wrong place for the conversation — **FIXED `6b34745`, VERIFIED LIVE**

Same call. It took **89 seconds and seven turns**, and the caller stated an
intention to cancel **four separate times**:

```
10:54:25  "i'd like to cancel my appointment please"
10:55:04  "i'd like to cancel it"
10:55:16  "uh yes"                       (to "is that you?")
10:55:26  "oh i'd like to cancel it altogether"
```

The `B-42` gate sits at the **write**, which is correct as a safety net — it is
the last point before something irreversible. But the natural place to say the
name is the **readback**, twenty seconds earlier at 10:54:46, where Susie already
recites the day and time. Because it is not said there, the sequence becomes:
retention question → answer → identity block → name readback → "yes" → consent
block → **retention question asked all over again** → answer again → write.

The consent block at 10:55:18 is *correct in isolation*: `"uh yes"` answered
*"is that you?"*, not *"do you want to cancel?"*. The fault is the ordering, not
either gate.

**Fixed by steering, not by a prompt edit.** An ambiguous lookup result now
carries a `caller_message_rule` telling the model to name the patient in the
read-back and settle identity *before* asking anything else. Delivered on the
tool result for the same reason B-36's Layer 2 was — it arrives at the moment of
use, cannot drift out of step with the gate, and reaches every clinic without
touching a 24k-line prompt or any `clinic.json`. **The gate is unchanged.**

#### Verified end to end on `CA368775868983933e143bcfc1d8eb3899`, 11:09, build `6b3474555005`

```
11:09:11.104  lookup_patient (gcal): match 1/11 name='Quentin Rook'
              AMBIGUOUS — name must be read back (B-42)
11:09:13.575  tts "I've got an appointment for Quentin Rook on Wednesday the 5t…"
11:09:13.579  B-42: looked-up name 'Quentin Rook' was spoken to the caller —
              identity gate satisfied
11:09:25.929  tts 'Would you like to reschedule this appointment, or cancel it…'
11:09:35.335  cancel_appointment  patient_name="Quentin Rook"
11:09:35.910  {"success": true, … "Quentin Rook"}
```

**There is no `cancel_appointment BLOCKED` line anywhere on this call.** That is
the designed outcome, and it is the same division of labour that closed B-36
cause 2 on `CA9cc1a23e`: the steering layer resolved the turn and the guard never
had to fire. The guard remains, verified, for when it does not.

| | `CAdbc84848` (B-42 only) | `CA368775` (B-42 + B-44) |
|---|---|---|
| Call duration | 86.7 s | **60.3 s** |
| LLM turns | 6 | **4** |
| Retention question asked | twice | **once** |
| Caller stated "cancel" | 4 times | **2** |
| Identity gate fired | yes | **no** |
| `patient_name` on the write | `"Unknown"` then correct | **correct first time** |

**26 seconds and two turns removed**, on the destructive path, with the safety
property unchanged.

> **`B-39` was NOT observed on this call** — the retention question was asked
> once and *"I'd like to cancel it altogether"* was accepted first time. Do not
> read that as closed. Of the three earlier sightings, only `CAdbc84848`'s was
> caused by the identity block interrupting between question and answer; the
> `CA66d6f1b4` and `CAe74ceae7` sightings pre-date `B-42` entirely and had some
> other cause. So `B-44` has removed **one of `B-39`'s triggers**, not `B-39`.
> One clean call is one observation.

### Latency on `CAdbc84848` — **three turns over 4.8 s to first token**

```
turn 1  llm_ttft_ms=5808  content_ttfa_ms=7022
turn 4  llm_ttft_ms=4876  content_ttfa_ms=5276
turn 6  llm_ttft_ms=8525  content_ttfa_ms=9044   <- ~6 s of dead air after the filler
```

**Not `B-40`.** `chunk_gate_ms` was 1032 / 280 / 402 — the gate was not holding
anything. This is time-to-first-token from the model, and it is upstream of
everything in this repo.

Earlier calls the same morning ran 1.1–1.8 s on the same path, so it is variance
rather than a step change, but turn 6 breaks two production-ready bars on its own.
**Measurement before action:** the plausible in-repo contributor is context
growth — each blocked write appends a long `tool_result`, and this call had three
— but 8.5 s is far more than that should cost. Do not tune anything until
`llm_ttft_ms` has been read across a dozen calls; the honest reading today is
"observed, unexplained, not reproduced on demand".

### `B-45` · the degraded LLM path could write to the calendar with **no gates at all** — **FIXED `e108e44`**

Promoted from `B-36`'s residual 2, which recorded it as "wants its own row". It
does, and it is worse than the residual made it sound.

`_gpt_fallback` runs when Claude is overloaded and the retries are spent. It
calls `TOOL_EXECUTORS` **directly** and never reaches `_execute_tools`, so
**none** of the write gates applied there:

- not FM-01, not the surname or phone backstops;
- not FM-23's move and cancel consent;
- not `B-42`'s identity check — **a cancellation on that path could not tell
  whose appointment it was destroying**;
- not `_note_write_result`, so Gate 5f never armed and a phantom could not be
  caught either.

All three write tools were advertised to that model. **The path activates under
load, which is when a busy clinic can least afford it.**

**Deliberately NOT replicating the gate chain.** Extracting a 350-line `elif`
chain out of the most dangerous file in the repo, for a degraded path, is exactly
the refactor `CLAUDE.md` §4 tells us not to attempt right now. And the correct
degraded behaviour is already written down in §6 bar 3: when the LLM is down,
produce a controlled outcome — take a message, promise a callback, transfer —
never a hallucinated confirmation. **A missed booking is recoverable by a
callback; a wrong cancellation is not.**

Three layers, and the redundancy is the point:

1. the write tools are **withheld from the fallback schema**, derived from
   `_WRITE_TOOL_FAMILIES` rather than a second hand-kept list;
2. the dispatch **refuses them anyway**, ordered before `TOOL_EXECUTORS`, so the
   guarantee does not rest on a tool list a later edit could widen;
3. the refusal routes through `_note_write_result`, which **arms Gate 5f** and
   attaches the do-not-claim rule — and this path *does* sanitise its reply
   through Gate 5, so a narrated booking is still caught. Verified end to end: a
   phantom *"your appointment has been cancelled"* after a degraded refusal is
   re-steered.

Plus the steering half in `_GPT_CONSTRAINT_PREFIX`, so the caller does not have
to hit the wall for the model to discover it cannot book.

> **One near-miss caught in review and pinned by its own test.** The refusal log
> line was written as `self.call_sid`; `LLMStream` has no such attribute. It
> would have raised inside the `try`, been swallowed by the broad
> `except Exception`, and **silently downgraded a clean refusal into a generic
> error** — losing the steering message *and* the Gate 5f arming, at precisely
> the moment both matter. That is the broad-exception hazard `CLAUDE.md` §4
> warns about, biting inside a safety fix.

18 new tests. Suite verified by diffing failing node IDs — identical.
**Not yet exercised on a live call, and it is the hardest of today's fixes to
dial** — reaching it requires Claude to be overloaded with its retries spent.

---

### `B-43` · another first-person reasoning leak — *"I need to action the cancellation now."* — **FIXED `91524c4`**

Same call, 10:39:23.738, spoken aloud. Gate 5g's `_SELF_NARRATION_RE` carries
`I need to book (?:this|it) in now` — a **booking-specific literal** — and nothing
for cancel or reschedule. Reproduced offline:

```python
th.sanitise_response("I need to book this in now.", s)            -> ''
th.sanitise_response("I need to action the cancellation now.", s) -> unchanged
th.sanitise_response("I need to process the cancellation now.", s)-> unchanged
```

**`B-41` therefore did not close the family, only one arm of it.** And the shape
of the gap is exactly `B-36` cause 2 again: a guard scoped to *booking* while the
same failure exists verbatim on the cancel and reschedule paths. Generalise the
verb rather than adding two more literals.

> **FIXED `91524c4`. The family was nine phrasings wide and one was caught.**
> Gate 5g carried a single arm — `I need to book (this|it) in now` — so nine
> plausible phrasings of the same internal sentence went to TTS. Generalised the
> verb rather than adding two more literals, which would have been the same bug
> waiting for a third path.
>
> **Safe to widen because Gate 5g is structural**, not a flat phrase list: a
> sentence counts only if it *also* carries no second person and is not a
> question. Those two guards, not the verb list, are what spare the sentences a
> caller may legitimately hear — *"I need to book **you** in now"* and *"I need
> to book this in now — shall I go ahead?"* both survive, with a test asserting
> the flip. Same argument that made `B-41`'s widening safe.

### `B-39` · the retention question is asked three times — **FIXED 2026-08-05**

> ✅ `latency-eval` `3d5d0b8`; `theorem-onboarding` `d2a3338`. Prompt-layer, in
> both prompt engines — `clinic_template_prompt.py` (jv_v1, vital_edge) and
> `susie_system_prompt.py` (theorem_v3).
>
> **The cause was one clause.** Both prompts said the retention question was
> *"REQUIRED on the cancel path **EVERY TIME**"*. That is true of the **call** and
> false of the **turn**, and "every time" is precisely the reading that produces a
> loop — which is what the `CAe74ceae7` transcript above shows, the question and
> the action in the same breath. Replaced with a **count**: *ASK IT ONCE PER
> CALL*, followed by an explicit list of the answer shapes that discharge it
> ("cancel", "cancel it altogether", "yes cancel it", or a plain affirmative) and
> three named prohibitions — do not re-ask because the answer was short, do not
> re-ask to be sure, never say it in the same turn as actioning the cancellation.
> Each of the three sightings above maps to one of those three.
>
> **Also scoped to the cancel path, which is Quentin's bonus item** (2026-08-05):
> the reschedule branch now carries an explicit prohibition — never ask a caller
> who is *moving* an appointment whether they would rather cancel it, because they
> are trying to keep it and the question invites them to lose it. The question
> remains on the cancel path, where it is a deliberate retention step, and on a
> genuinely ambiguous opening.
>
> ⚠️ **The `"altogether"` wording is deliberately still present in the cancel
> turn.** Until `B-57` it was also load-bearing — the gate armed on that literal
> and nothing else. `B-57` removed that coupling, but the phrase stays because
> retention is wanted; the two fixes shipped together so neither is standing on
> the other.
>
> Regression: `tests/regression/test_b39_retention_question_scope.py`, per prompt
> engine, asserting the reschedule prohibition, the one-ask bound, the same-turn
> case, and that the `"every time"` clause is gone.

The caller said *"I'd like to cancel my appointment"*, then **"yes"**, then
**"cancel"**, then **"I said I'd like to cancel it"** — and was asked to
reconsider **three separate times** across 27 seconds. The third ask
(*"Got it — and just before I do, would you like to reschedule…"*) comes
**after** the caller has already said the word "cancel" plainly.

Not a gate problem: no `cancel_appointment` call was attempted on those turns, so
nothing blocked them. The model chose to re-ask. **Prompt-layer, in the template
cancel flow.**

> **NARROWED by the second cancel** (`CA12db707b`, 10:15), where the caller
> answered the retention question with *"uh I'd like to cancel it"* and it fired
> **first time — one ask, no loop.** So this is not a general retention loop.
>
> Re-reading `CA66d6f1b4` with that in hand, ask #2 was **legitimate**: the
> caller had said *"uh yes"* to an **or**-question, which is genuinely ambiguous,
> and Susie disambiguated. The defect is only **ask #3** — after the caller had
> answered that clarification with the bare token *"uh cancel"*.
>
> So the real shape is: **a bare `"cancel"` token does not satisfy the model,
> while `"I'd like to cancel it"` does.** Much narrower than first written, and
> it means the trigger is a short answer to the clarify, not the retention step
> itself. Anchor that before touching the prompt.
>
> **That narrowing was WRONG — withdrawn 10:39.** On `CAe74ceae7` the caller
> answered the retention question with *"I'd like to cancel it altogether"* —
> the **canonical phrase, verbatim from the question itself** — and Susie
> **re-asked the whole retention question anyway**, in the same turn as
> actioning the cancellation:
>
> ```
> 10:39:21.165  FINAL 'i'd like to cancel it altogether'
> 10:39:23.542  tts   'Would you like to reschedule this appointment, or cancel it altogether?'
> 10:39:23.738  tts   'I need to action the cancellation now. Let me do that for yo…'
> 10:39:26.247  tool  cancel_appointment
> ```
>
> The caller hears the question, then immediately hears it being done. So it is
> **not** about short tokens: the model re-emits the retention question even on
> a full, canonical, unambiguous answer. Three sightings now, on three different
> answer shapes. **Two for two on my own narrowings being too clever** — see
> [[anchor-defect-rows-before-scheduling]].

### `B-40` · 9.9 s of dead air on the cancel turn, no filler — **MITIGATED 2026-08-05**

> ✅ `latency-eval` `4eb1e0c`; `theorem-onboarding` `d2a3338`. **Read this as
> mitigated, not closed** — the chunk gate holding output for ~10 s is the
> underlying cause and it is untouched. What changed is that the silence is no
> longer silent.
>
> `_FILLER_TOOLS` mapped `check_availability`, `book_appointment` and
> `lookup_patient` — and **not** `cancel_appointment` or
> `reschedule_appointment`. So the two turns where a caller is most anxious, and
> which both make a calendar round-trip after the caller's go-ahead, were the two
> with no filler pool at all. Both are now mapped, to new
> `CANCEL_WRITE_FILLERS` / `RESCHEDULE_WRITE_FILLERS`.
>
> **Safe against a blocked write:** a gate refusal returns before the filler
> branch, so neither pool can be spoken over a `*_confirmation_required` result —
> i.e. a filler can never imply an action the gate just refused.
>
> **This did not fix the arming path**, which is the other half of the sighting:
> the filler armed on earlier turns only because a tool was detected early, and
> here the tool call arrived at the end of generation. A pool cannot play if the
> path never arms. What the mapping guarantees is that when it *does* arm on a
> cancel or reschedule, the caller hears something appropriate rather than a
> booking phrase or nothing.
>
> Bundled with it, per the owner's 2026-08-05 instruction: **"bear with me" and
> "just a second" are gone** from every pool that can play on these turns
> (`LOOKUP_FILLERS`, `THINKING_FILLERS_SECONDARY`,
> `config.FILLER_PHRASES`), replaced with warmer wording. Two of the
> replacements were caught by the new test for making a **false completion
> claim** — *"Getting that all booked in for you…"* trips
> `_false_write_claim` — which is exactly the `B-36` shape appearing in filler
> text, and worth knowing is possible.
>
> Regression: `tests/regression/test_stall_phrases_on_cancel_and_reschedule.py` —
> asserts the mapping, the banned phrases' absence, and that no filler reads as a
> completed write.

```
susie.latency  turn_seq=20 ttfa_ms=11155 content_ttfa_ms=11155
               llm_ttft_ms=1151 chunk_gate_ms=9907
```

The caller stopped speaking at 10:10:29.491. Susie next made a sound at
10:10:40.550 — **11.1 s later** — and `chunk_gate_ms=9907` says ~10 s of that was
the chunk gate holding output, not the model thinking (`llm_ttft_ms=1151`).

**No filler played.** Earlier turns on this same call fired one (*"Give me a
moment…"*) because a tool was detected early; here the tool call arrived at the
very end of the generation, so the filler path never armed.

This breaks **two** of the five production-ready bars in `CLAUDE.md` §6 at once:
p95 turn latency under 1.5 s, and no dead air over 3 s without a filler or
acknowledgement. On a demo call an 11-second silence reads as a dropped line.

**DID NOT REPRODUCE** on the second cancel (`CA12db707b`, 10:15). Four turns,
`chunk_gate_ms` = 1134 / — / 360 / 872, against 9907. Max `ttfa_ms` 2333.

That call also carried **no caller correction** — no `ep_cutoff … reason=correction`
— so the correction-path hypothesis is **not refuted, and not confirmed**: the one
call that spiked had a correction, the one that did not, did not. Still n=1 for the
spike.

**Do not fix on this evidence.** Next step is targeted, not more random cancels:
dial a call that deliberately contains a mid-turn correction ("actually, no —
cancel it") and watch `chunk_gate_ms` on the turn *after* it. If it spikes, the
cause is the correction path and it is narrow. If it does not, this was a
one-off and the row should be downgraded to a watch item rather than carried as
a P1.

---

### `B-38` · the write CTA can be truncated out of `last_bot_prompt` — **FIXED `91524c4`**

Found by reading the passing call, not from a failure. All three write gates and
`B-37`'s new `_write_cta_outstanding` read **`last_bot_prompt`, which is capped
at 200 characters** ([llm_stream.py:1434](../../app/media_streams/llm_stream.py)).
On `CA80f2d410` the confirmation turn was two chunks — the readback (`len=110`)
and the CTA (`len=37`) — about **148 chars, leaving ~52 of headroom**.

A longer readback spends that headroom: a practitioner name, a location clause,
a wordier date. When it does, *"Shall I go ahead and move it for you?"* falls off
the end of `last_bot_prompt` and **two separate protections fail at once**:

- `_move_confirmation_asked` → `False` → the write is **BLOCKED** — `B-36`
  cause 1 again, arriving by truncation instead of by rewording;
- `_write_cta_outstanding` → `False` → the caller's *"go ahead"* is **dropped**
  by the slot guard — `B-37` again.

The caller then hears a re-steer and loops. `B-31` (`c69eb61`) already hit this
exact cap and fixed it *for the clinical layer* by falling back to
`last_question` — **which is stored uncapped**
([llm_stream.py:1441](../../app/media_streams/llm_stream.py)) and holds precisely
the CTA sentence. The write gates never got that fallback.

**Fix is small and well-anchored:** have the three CTA predicates read
`last_bot_prompt` OR `last_question`. ~~**Anchored, not yet reproduced**~~

> **FIXED `91524c4` — and it is no longer a lead. REPRODUCED offline on ordinary
> wording.** Name the service, the practitioner and the site and the read-back
> runs **251 chars on a reschedule and 207 on a cancel**, against the 200-char
> cap. Seven characters over on the *cancel* path. The calls already dialled ran
> 148, so the headroom is tens of characters, not hundreds.
>
> When it fires, **three things break together**: the write is BLOCKED (`B-36`
> cause 1, arriving by truncation instead of by rewording), the caller's *"go
> ahead"* is DROPPED by the slot guard (`B-37` by another route), and Gate 5f
> arms. **One truncation re-opens two defects that were fixed and verified the
> same day.**
>
> All four consumers now read through `_cta_asked` — the three gates plus
> `B-37`'s `_write_cta_outstanding` — with a test asserting **no gate reads the
> capped prompt directly any more**. A fallback applied to three and forgotten on
> the fourth is the failure mode; that is the `B-31` shape, which fixed this same
> cap for the clinical layer and left the write gates behind.
>
> **Each source is judged whole and deliberately NOT concatenated.** A prompt
> ending *"I'll book that"* beside a question *"in June?"* joins into the booking
> CTA `"book that in"` and would open the gate on a sentence nobody said. Pinned
> both ways: that the join produces the false match, and that judging
> independently does not.
>
> **Staleness was the one way this fallback could turn unsafe. It cannot:**
> `F_LAST_QUESTION` is assigned unconditionally every turn, so a turn that asks
> nothing sets it to `""` rather than leaving an older CTA standing. Pinned from
> both the source and the behaviour side.
>
> 50 new tests across `B-38` and `B-43`. Suite verified by diffing failing node
> IDs — identical. **Not yet verified on a live call.**

### Leads from the same two calls — **anchored, NOT findings**

1. ~~**Write tools are called with `patient_name: "Unknown"`**~~ — **RESOLVED
   10:55 by `B-42`'s gate**, see above: forcing the name to be spoken also puts
   it into the write args. Retained below for the mechanism only.
   Historically: it appeared only **Narrowed 10:16:** on `CA12db707b` `cancel_appointment` carried
   `patient_name: "Quinton Rock"` correctly, and the difference is *when the
   lookup ran*: on that call `lookup_patient` fired in the **same turn**, one
   iteration earlier. On the calls that sent `"Unknown"`, the lookup had happened
   on an **earlier turn**. So the name is not being carried across turns into the
   write args. The write succeeds either way (it matches on phone); what is
   unknown is whether the name is used downstream (SMS, owner alert, calendar
   title). Read that before scheduling.
2. **The slot map is never cleared after selection.** `slot map active —
   time_selection` is still logged after the write succeeds, and `'perfect'` was
   dropped as a slot fragment at 09:59:27 after the call had effectively ended.
   Harmless here; it is the root cause `B-37` works *around* rather than removes.
3. ~~**Service-type drift on reschedule.**~~ **WITHDRAWN 10:03.** `CA9cc1a23e`
   called `check_availability` with `service: msk_initial_assessment`, correctly
   matching the existing appointment type. The earlier mismatch was the model
   choosing differently on one call, not a systematic defect. Non-determinism
   worth watching, not a row.
4. **The service restarted mid-call** at 09:58:15 (the deploy), surviving on
   Anthropic retries. The shutdown banner reads *"Theorem Health AI
   Receptionist"* on the JV service — cosmetic clinic-identity leak, consistent
   with the hardcoded clinic names noted in `CLAUDE.md`.

---

### `B-37` · "uh go ahead" was dropped before the LLM ever saw it — NEW, **FIXED** `38dd929`

Found on the `B-36` verification call itself. `CA8d90deb26327b97d8b6f396e55b63272`,
3 Aug 09:38:53, build `e61c8f805d6e`. Susie asked *"Shall I go ahead and move it
for you?"*; the caller said **"uh go ahead"**:

```
09:38:43  tts: 'Shall I go ahead and move it for you?'
09:38:53  FINAL → queue: 'uh go ahead'
09:38:53  [ms_conn] slot fragment ignored — re-arming: 'uh go ahead'
09:39:03  WATCHDOG_FIRE prompt='Still with you — which of those would you like?'
09:39:11  FINAL → queue: 'um i said go ahead and move it for me'   <- caller repeats
```

Three words, none in `_COMMUNICATIVE_WORDS`, so `_is_short_meaningless_fragment`
([connection.py:414](../../app/media_streams/connection.py)) discarded it inside
the Spec H slot guard. It never reached the LLM — no `iteration=1`, no tool call.
The watchdog then re-asked the **wrong question** (a SLOT re-ask, when the
outstanding question was the move CTA). ~18 s, and the caller had to repeat
themselves. It did complete on the retry, so it reads as clunky rather than
broken — which is why it had not been caught.

**Why booking never hit it.** The Spec J bypass one branch up is armed by
`_NAME_REQUEST_PHRASES`, and booking asks for a name after slot selection. A
reschedule already knows the patient from `lookup_patient`, never asks for a
name, so `post_slot_confirmation_pending` was never set and the slot map stayed
live straight through the move CTA (`slot map active — time_selection` is still
being logged at 09:39:17).

> **It is NOT the affirmation verdict, and the obvious fix is forbidden.** The
> first diagnosis — widen the accepted affirmatives — was wrong twice. The write
> gates use `_book_reply_verdict`, not `_book_reply_is_affirmative`; L1 already
> settles *"go ahead"* as yes and returns `'unsure'` for *"go for it"*, handing it
> to the L2 classifier built for that exact phrase (`_book_verdict_deterministic`
> names it, and the booking it lost on `CA7e389a47`). And adding *"go for it"* to
> `fast_path._YES_PATTERNS` would make *"don't go for it"* **book** —
> `test_the_shared_yes_patterns_were_not_edited` forbids it by name. Nothing in
> the verdict layer needed changing. **Anchor before scheduling: the symptom
> pointed at the confirmation logic and the cause was two layers upstream.**

**Fix:** a fourth arm on the slot guard — when a write confirmation is
outstanding, read via the gates' own predicates so it cannot drift from what the
gate accepts, anything that is not pure disfluency reaches the LLM. Ordered
before both the fragment drop and Spec AJ's clarify, both pinned. A routing
decision, not a safety one: dropping is the dangerous act, and passing to the LLM
is safe because the write gate still adjudicates.

Test `tests/regression/test_b37_write_cta_survives_slot_guard.py`, 45 cases.
**Not yet verified on a live call.**

---

### `B-36` · a phantom reschedule — the write was BLOCKED and Susie said it happened

**NEW, P1.** `CA23199d08907234dddb7d2167fb23753c`, 3 Aug 01:04, build
`c462f1e21c98`. The worst failure mode this system has, live:

```
01:04:33  tool: reschedule_appointment … new_slot_iso 2026-08-06T18:45
01:04:33  WARNING reschedule_appointment BLOCKED — no clear caller yes after
          the move confirmation (last_user_text='uh yeah go for it')
01:04:33  result: {"status": "reschedule_confirmation_required", …}
01:04:37  "That's you rescheduled — you're now in for Thursday the 6th…"
```

`outcome='abandoned'`. Nothing moved. The caller was told it had.

**Two independent causes. Both fixed 3 Aug — `fe97b82` (cause 1), `e387aac` +
`d5b257c` (cause 2). Neither is verified on a live call yet.**

#### Cause 1 — the CTA test was one literal — **FIXED**

The gate read `"move it for you" in last_bot_prompt AND await
_book_reply_verdict(...)`. The model asked *"Shall I go ahead and **move your
appointment to Thursday the 6th of August at quarter to seven in the
evening**?"* — the confirmation question, in other words, because the caller had
just said *"I think you got cut off"* so it re-asked with full detail.

The substring missed and `and` **short-circuited, so the caller's affirmative was
never evaluated at all** — no `L2 classifier` line in the log, unlike the 00:35
call where the canned phrase happened to be used verbatim. The old warning then
blamed the caller's reply, which actively misdirected the investigation; it now
names which arm failed.

The booking gate two branches up accepts `"shall i go ahead" OR "book that in"`
and has never had this problem. `_move_confirmation_asked` brings reschedule into
line: the canned CTA, or an ask-shape **and** a move verb. **It does not weaken
the gate** — it makes it reachable; `_book_reply_verdict` still runs
independently, and a booking CTA cannot satisfy it (ask shape, no move verb).
Test `test_move_confirmation_cta_survives_rewording.py`, 17 cases, including the
verbatim line and the read-back statement that must NOT count as asking.

#### Cause 2 — mapped 3 Aug, **FIXED 3 Aug** — `e387aac` (2d) + `d5b257c` (2a/2b/2c/2e)

> **Shipped as mapped, in the order the map demanded.** The map below is left
> intact because it is the reasoning, not the changelog. What changed:
>
> - **2d — `e387aac`, steering only.** `_note_book_write_result` →
>   `_note_write_result`, keyed on a write-tool map, with a per-family
>   `caller_message_rule`. Fires on the already-failed path, so it carries no
>   over-fire risk — which is why it was separable and went first.
> - **2a/2b/2c/2e — `d5b257c`, the guard.** Arm is now `refused this turn` **OR**
>   the original `booking_flow_active AND NOT booking_write_confirmed`.
>
> **`OR`, not replace — the map's one substantive correction.** Arming *only* on
> refusals would have dropped the case the guard already caught: a pure
> hallucination, where the model claims a booking having called no tool at all.
> That is arguably the commoner LLM failure, and replacing the arm would have
> been a silent downgrade of something that works. It does not drag 2c back in:
> the old arm is `booking_flow_active`-gated and a reschedule never sets it.
>
> **The fault the map did not contain, and it was the dangerous one.** The
> guard's re-steer becomes `last_bot_prompt`, and `last_bot_prompt` is what every
> write gate reads to decide whether its confirmation question was asked. The
> booking re-steer contains **both** booking-gate literals. One shared re-steer
> string fired on a reschedule phantom would leave a booking CTA on record, and
> the caller's next *"yes"* would satisfy the **booking** gate — turning a phantom
> reschedule into a **real booking of a new appointment**, which is worse than the
> bug being fixed. Now one re-steer per family, each arming its own gate and no
> other, pinned against the gates' real predicates in
> `tests/regression/test_b36_gate5f_write_families.py`.
>
> **Also corrected from the map:** the marker cannot be detected by
> `success: False`. All five gate refusals return `{"status": "..._required"}`
> with **no `success` key at all**, so the test is `not (success is True)` and it
> fails closed. And it is scoped by tool *name*, not result shape — the lookup
> family reports `found`, and *"I can't find your appointment"* must not arm
> anything.
>
> **Measurement.** 16 reschedule + 12 cancel phantoms caught; 25 legitimate lines
> clean across all three families, including the FAQ trap *"we've moved to a new
> building"* — which is why the pattern requires an object after the verb, never a
> bare `moved to`. The 18/27 booking measurement is unchanged and re-run. 140 new
> tests plus the 47 existing. Suite verified by **diffing failing node IDs**
> against a detached worktree at `8b7b10d`: 93 before, 93 after, sets identical.
>
> **Two residuals, both recorded rather than fixed:**
>
> 1. **The guard is turn-level, not utterance-level.** The marker is set when the
>    tool result returns, so a claim streamed in the *same* assistant message as
>    the `tool_use` block is spoken before the refusal is known and escapes.
>    `CA23199d089` spoke on a later iteration, which is the shape in scope. Noted
>    in the gate itself.
> 2. ~~**The GPT fallback path has no write gates at all**~~ — **promoted to
>    `B-45` and FIXED `e108e44`.** [llm_stream.py
>    `_gpt_tool_loop`](../../app/media_streams/llm_stream.py) called executors
>    directly and never reached `_note_write_result`, so none of FM-01, FM-23,
>    2d or Gate 5f's refusal arm applied there. **Note it is not dead code** — it
>    is the fallback when Claude fails, which is why it mattered. See the `B-45`
>    section.
>
> **Still unverified on a live call — this is the outstanding debt.** Everything
> above is proven in tests only. The verification is three dialled calls on the
> deployed build, read from `[build_info] running build <sha>` in the Render log
> (`/health` reports a hardcoded `1.0.0` and cannot confirm a deploy):
>
> 1. **Reschedule, refused then recovered** — ask to move an appointment, answer
>    the move CTA with something the gate accepts. Expect the move to succeed and
>    the confirmation to be spoken **unaltered**. This is the 2c over-fire check
>    and it matters more than the phantom test: it is the direction that
>    previously abandoned a completed booking.
> 2. **Reschedule, refused and left refused** — answer the move CTA ambiguously.
>    Expect `[ms_gate5f] false reschedule confirmation` in the log if the model
>    claims success, the caller to hear *"…would you like me to move it for
>    you?"*, and **no booking** to appear in Acuity.
> 3. **Cancel** — same shape as 2 against the retention question.

##### The map, as written 3 Aug — the reasoning behind the fix

Traced end to end rather than patched. The order matters: **2a alone makes 2b
irrelevant**, so anyone who "fixes" the vocabulary first will ship a change that
does nothing and believe it worked.

| | Fault | Where | Consequence alone |
|---|---|---|---|
| **2a** | **Gate 5f is never armed on a reschedule.** It requires `session["booking_flow_active"]`, which has exactly two assignment sites — a treatment-mention + booking-intent path and the booking-ack/CTA path. **Neither fires on a reschedule**, and no reschedule log in this sweep contains the line | [connection.py:9792](../../app/media_streams/connection.py), [:10874](../../app/media_streams/connection.py); read at [turn_handler.py:927](../../app/media_streams/turn_handler.py) | The guard does not run at all. **Fixing 2b without this is a no-op** |
| **2b** | The claim pattern is booking-only — `booked`, `confirmed`, `got you in for`. *"That's you **rescheduled** — you're now in for Thursday"* matches nothing | `_FALSE_CONFIRM_CLAIM_RE`, [turn_handler.py:527](../../app/media_streams/turn_handler.py) | Even when armed, the phantom passes |
| **2c** | **The success signal does not exist for reschedule.** `_note_book_write_result` returns early unless `tool_name == "book_appointment"`, so `booking_write_confirmed` is never set by a *successful* reschedule either | [llm_stream.py:559](../../app/media_streams/llm_stream.py) | **The trap.** Fix 2a+2b and leave this, and the guard is permanently armed on reschedules and strips **real** confirmations — precisely the Gate 5c over-fire that abandoned a completed booking on 2026-06-12 |
| **2d** | The model is given no prohibition. Layer 2 of the same helper attaches `caller_message_rule` — *"The booking was NOT made. Do not tell the caller they are booked…"* — and is scoped to `book_appointment` too. The reschedule block message says *"ask X and wait"* and never says *"do not claim it happened"* | [llm_stream.py:565](../../app/media_streams/llm_stream.py) | The model had an instruction to ask a question and no rule against announcing success |
| **2e** | **`cancel_appointment` has all four of the above**, and it is *destructive* | `cancellation_confirmation_required`, [llm_stream.py:2983](../../app/media_streams/llm_stream.py) | A refused cancellation can be narrated as done |

> **This was a documented decision, not an oversight.** `_note_book_write_result`
> says so in its own docstring: *"Reschedule is intentionally out of scope: its
> confirmation is a different phrase family ('moved'), and Gate 5f targets
> booking phantoms."* The reasoning was coherent when written. `CA23199d089`
> falsifies it — record it as a decision overturned by evidence, not as someone
> having been careless.

##### The fix that follows from the map: **arm on the refusal, not on the flow**

Every one of 2a–2e comes from the guard being scoped to *booking* — the flow
flag, the vocabulary, the success signal, the steering rule, the tool name. The
scoping is the bug, and widening each of the five in turn is five chances to get
the over-fire wrong.

Instead: have the tool-gate branches set a **`write_refused_this_turn`** marker
when they return any `*_required` status or a `success: False`, and have Gate 5f
arm on **that** instead of `booking_flow_active`.

- **2a** dissolves — arming no longer depends on a flow flag that reschedule
  never sets.
- **2c dissolves, and this is the point.** The guard is off on every turn where
  nothing was refused, so a successful reschedule's *"That's you rescheduled"* is
  never even examined. The over-fire hazard that makes the vocabulary approach
  dangerous simply does not arise, and `booking_write_confirmed` stops being
  load-bearing.
- **2d** becomes one shared rule attached at the same site.
- **2e** is covered for free, along with any future gated write.
- **2b** still needs the claim pattern to know "rescheduled"/"moved"/"cancelled",
  but now it is only asked to judge a turn where a write is *known* to have been
  refused — a far weaker requirement than judging every turn of a booking call.

**Not done the night this was written, on purpose.** It touches the one guard
with a recorded history of stripping a real confirmation, and the correct version
is a restructure, not a regex. Owner decision, with a clear head. — *Decision
taken and shipped the following day; see the block at the top of this section for
what actually landed and where the map turned out to be incomplete.*

#### Cause 2 — the original note, superseded by the map above

Gate 5f ([turn_handler.py:513](../../app/media_streams/turn_handler.py)) exists
for exactly this — *"Call 5: book_appointment was REJECTED yet the model
narrated 'All booked'"*. Its claim pattern only knows **booking** vocabulary
(`booked`, `confirmed`, `got you in for`). The model said *"That's you
**rescheduled** — you're now in for…"*. No match. The guard was armed and silent.

**A block the model can talk over is not a block.** Left open on purpose rather
than fixed at 01:30 by widening a regex: a reschedule never sets
`booking_write_confirmed`, so the guard is permanently armed on that path, and
Gate 5c has already abandoned a completed booking once by over-firing. The right
fix is almost certainly to gate on **the last tool result carrying a
`*_required` status** rather than on vocabulary at all — which also covers
`cancel_appointment` and any future gated write, and is a bigger change than it
looks. Owner decision pending.

### Two observations recorded without a row

- **The clinical barge-in guard armed on a plain empathy line.** `"sorry to
  hear"` is in `_CLINICAL_EMPATHY_PHRASES`
  ([connection.py:11956](../../app/media_streams/connection.py)), so a 10.3s
  empathy-plus-CTA turn on call 3 became un-interruptible. There is direct
  precedent for narrowing that list — `"sorry about that"` was removed from it
  for locking a caller out of correcting a mis-captured name. Not opened as a
  defect: no caller was actually harmed on these calls and the list is curated
  deliberately.
- **The DVT screen question takes 10.5 s to speak.** On calls 1 and 2 the caller
  began answering ~3 s before it finished and got three
  `barge-in suppressed — clinical response completing` lines. Working as
  designed, and the answer was still captured intact. Shorten the question
  rather than weaken the guard.

---

## Sweep — 2 Aug 2026, 21:33–21:59, three calls

Build `dc31c6c`/`1328f39`, service `low-latency-joint-venture`, number
`+447366263180`, clinic `jv_v1` (`prompt_engine=template_v1`).

Calls placed: **1** (colloquial booker, two day-changes), **2** (caller-ID
refusal), **4** (dead air at the phone step). Call 3 was dropped — call 2 had
already exercised the keypad path end to end. Call 7 not yet placed.

All three booked correctly: number digit-for-digit, slot as spoken, day name
matching the date on every calendar entry. **Surname is the exception — see `A3`
below.**

### The three calls are in obs — re-anchor, do not re-argue

Every transcript quoted in this section is re-fetchable. Do not carry these
quotes forward in prose when you can pull them:

| Call | SID | `python scripts/show_call.py` | Booked |
|---|---|---|---|
| 1 — colloquial booker, two day-changes | `CAa8862c123a675855d2757c34b3a863a5` | `CAa8862c12` | Sat 8 Aug 11:00, event `75rjjof4r3tf…` |
| 2 — caller-ID refusal, the DVT screen | `CA2ada6263fbd072aa1872854f0daa4286` | `CA2ada6263` | Thu 6 Aug 16:30, event `ndgtm3oo0f8o…` |
| 4 — surname refusal, `B-25` | `CAce42c36b7d76889991800a3497a86594` | `CAce42c36b` | Sat 8 Aug 11:45, event `onmme2flm1mj…` |

All three: `build_sha=1328f3926695`, `clinic_id=jv_v1`, `booking_confirmed=True`,
`final_state=GREETING`, caller `+447502211207`.

> **What obs cannot settle.** It stores what was *said*, not what the engine
> *did* — there is no tool-call trace. So `B-28` (no `clinical_screening` line on
> call 2) and the `B-13`/`B-26` prewarm 401s are **Render-log claims** and are
> not re-checkable from obs. And per the standing caveat, stored assistant turns
> are **post-Gate-5**, so a wrong sentence here may be the gate rewriting a
> correct generation. Neither caveat touches the caller turns, which is where
> every finding below actually rests.

### `A3` · confirmed live — a mangled surname reached a real calendar event

`A3` in `DEFECT_REGISTER.md` (surname written with no read-back) had been argued
from four manglings across older calls. Call 1 is a clean instance, and calls 1
and 4 together isolate the mechanism:

| Call | Caller said | Stored as | Read back? |
|---|---|---|---|
| 1 | *"uh rook"* | `Quentin Rook` | **No** — final confirm was *"So that's **Quentin**, Saturday the 8th…"*, first name only |
| 4 | *"um yeah that'll be roch r-o-c-h"* | `Quentin Roch` ✓ | Yes, incidentally — *"So that's **Quentin Roch**, Saturday the 8th…"* |

The true surname is **Roch**. Call 1 put **Rook** on Google Calendar event
`75rjjof4r3tfoldhu936cqei90` and the caller was never given a chance to hear it.
Call 4 got it right **only because the caller spelled it unprompted.**

**Read the read-back column carefully — call 1 is the compliant one.** The prompt
forbids a surname read-back at *both* sites:

- Step 7 ([clinic_template_prompt.py:2057](../../app/prompts/clinic_template_prompt.py)):
  *"never read back, spell, or confirm the surname … the surname is registered
  silently … accept ANY distinct word the caller gives as the surname and move
  on."*
- Step 9, WARM READBACK ([:2095](../../app/prompts/clinic_template_prompt.py)):
  *"State caller **first name**, day, date, and time"*, example *"So that's
  James, …"*.

So call 1 followed the prompt exactly and wrote `Rook` to a real calendar.
Call 4's *"So that's Quentin Roch"* was the model **violating step 9** — and that
stray violation was the only moment in three calls at which a caller could have
caught a mangled surname. The design is doing what it says; what it says is the
defect.

Two details worth keeping:

1. The prompt's own example of the STT split is *"Quentin **Rock**"*
   ([:2065](../../app/prompts/clinic_template_prompt.py)). The author already
   knew this exact surname mangles, and the instruction written in response was
   to accept the mangled word **silently**.
2. Call 4's surname loop (*"I do need a surname to complete the booking"*, twice)
   is a **separate** instruction from the silent-acceptance rule and the two now
   pull against each other: the caller was pressed hard for a surname that,
   once given, is never checked.

This is the first end-to-end demonstration in one sitting: wrong input → no
verification → real record. It does not change `A3`'s scope. The parked
`B-01`–`B-04` note stands — closing this is a **design reversal**, not an
addition, and the reversal must name which of the two rules above it is undoing.

#### Reversed 3 Aug — the read-back now carries the surname

**Third instance, and the one that forced it.** `CA451f165085a33431137630a188ed871a`
(build `8ac5ecf069e5`, 18:22–18:24) wrote `Quentin Rook` to Google Calendar event
`94q9h39eo4n9qdm2o0eer81890` — the *same* mangling as call 1's
`75rjjof4r3tfoldhu936cqei90`, eight hours after `B-52` fixed a different cause of
the same outcome. Three wrong-surname calendar events now, from **three
different causes**: the parser (`Way`), and STT twice (`Rook`, `Rook`). That is
the argument the row was waiting for — the causes are unbounded, so no
per-cause guard closes this. Only the read-back does.

The proof that the surname was never spoken is in the chunk lengths, not the
transcript: the turn-8 read-back TTS chunk is `len=83`, **byte-identical to the
turn-6 chunk generated before any surname existed**, while `book_appointment`
carried `patient_name="Quentin Rook"`.

**The "both sites" count above was wrong — it was four mentions, three of which
reach a booking.** Corrected inventory, and what was done to each:

| Site | Was | Now |
|---|---|---|
| `NAME CONFIRMATION RULES` | *"NEVER plausibility-checked, confirmed, read back, or spelled"* | *"read back"* removed; **plausibility + spelling prohibitions kept verbatim** |
| Step 7 | *"never read back, spell, or confirm the surname"* | narrowed to *"at this step"*; points forward to Step 9 |
| Step 9 | *"State caller **first name**"*, example *"So that's James, …"* | full name; example *"So that's James Whitfield, …"* |
| Step 9a | — | new: says the surname exactly once, bans a standalone *"is that right?"* and any spelling request, and names the correction path |
| Waitlist/callback | *"read back the FIRST name only"* | **untouched** — different write family (§`B-36`), and it has no booking summary to carry a surname |

Fixed in `clinic_template_prompt.py` only, with
`tests/regression/test_a3_surname_is_read_back.py` (14 assertions; **8 fail on
the pre-fix prompt**, 6 are invariants that must hold in both states).

**Which rule is being reversed, named as the row demanded:** *"the surname is
never read back"* — and **only** that one. *"Never plausibility-checked"* and
*"never spelled"* survive intact, because they are what hold off the call-4
surname loop this row's own point 2 warns about. Over-rejecting a surname costs
more than mis-hearing one: it loops the caller (`B-15`, twice on a live call).

Three things this does **not** do, all deliberate:

1. **It does not change the collection order.** The surname is still discovered
   by `book_appointment` returning `surname_required` *after* the caller has
   said "go ahead" and heard *"Just locking that in now…"*. That is change **A**,
   it is an engine change, and it carries a real regression — moving the surname
   ahead of the phone step moves that turn **out from behind** the BUG-14 guard
   at [llm_stream.py:2889](../../app/media_streams/llm_stream.py), which is the
   only thing that stopped the model re-running `check_availability` on
   `CA451f16` at 18:24:09. Split deliberately so a bad call names its own half.
2. **The correction may not reach the stored name.** `_awaiting` at
   [connection.py:1946](../../app/media_streams/connection.py) is true only when
   Susie's **spoken** last turn contains `surname` / `last name` / `family name`
   / `full name`. The read-back contains none of them, so at that turn a **bare**
   corrected surname does not backfill — only an explicit cue (*"my surname is
   Roch"*) does. The caller can now hear the error; whether the correction lands
   is untested and is Call 3 of `CALL_SUITE_A3_VERIFY_2026-08-03.md`.
3. **It does not reach `theorem` or `demo`.** Neither carries a `prompt_engine`
   key, so both are served by the legacy `susie_system_prompt.py`, which has its
   own read-backs at `:709`, `:958`, `:1094` using an ambiguous `[Name]`. The
   Theorem port must carry this by hand — a cherry-pick will not.

#### VERIFIED LIVE — `CA74b20e5dff8d3b4734e5a0be016b537f`, build `914cda38cf9f`

Call 1 of the suite, first dial, and the arming condition held: STT mangled the
surname again, so the fix was tested against a real mis-hear rather than a clean
one.

```
FINAL → queue: "i'm here to see quentin rook"          <- STT mangled it
read-back:     "So that's Quentin Rook, Monday the 10th…"
caller:        "it's not quentin rook it's quentin roch r-o-c-h"
read-back:     "Thanks for that — so that's Quentin Roch, Monday the 10th…"
book_appointment  patient_name: "Quentin Roch"  ->  am79qq8nl9v6rmbc10nnrdlq4s
```

The whole chain broke for the first time: mangled → **spoken** → caught by the
caller → corrected → correct name on the calendar. On `CA451f16` the identical
mangle reached the calendar unheard.

Behaviour matched Step 9a exactly — correction taken silently, whole summary
re-stated once, **no spelling loop and no standalone "is that right?"**. That is
the B-15 direction the prohibition existed to protect, and it held.

Two things this call did **not** show, stated so they are not assumed:

- **The early-name path was never exercised.** No name was given at turn 1, so
  the flow ran the canonical Step 7 order (slot → name → phone → read-back) with
  **zero blocked tool calls** — no `surname_required`, no *"Just locking that in
  now…"* before a question. Worst turn 3909 ms against 7585 ms on `CA451f16`.
  Change **A** remains untested and unfixed.
- **The correction never reached session state** — see `A3b` below.

---

### `A3b` · the corrected name reached the calendar and nothing else

Same call. The row disagreed with the calendar:

```
book_appointment  patient_name: "Quentin Roch"   -> calendar  ✓
📊 Row built —    name=Quentin Rook                           ✗
```

**Mechanism.** The name is kept in two places and the consumers disagree about
which to read. [actionable_summary.py:229](../../app/tools/actionable_summary.py)
checks `session["patient_name"]` **first**, falling back to `collected["name"]`.
The booking executors updated only `collected["name"]`. And the read-back
upgrade at [connection.py:10376](../../app/media_streams/connection.py) had
already latched `session["patient_name"] = "Quentin Rook"`:

```python
" " in _rb_full and (not _cur_name
                     or (" " not in _cur_name        # <- stored name must be FIRST-NAME-ONLY
                         and _rb_full.lower().startswith(_cur_name.lower())))
```

A one-way ratchet: once a two-token name is stored, no later read-back can
change it. The first surname spoken wins permanently. **That is deliberate** —
*"only ever EXTEND the existing first name"* — and it is what stops a model
paraphrase overwriting a surname captured from the caller's own clean
transcript. **Left alone.** Relaxing it is a different change (Option A) with the
risk running the wrong way, and `test_the_ratchet_is_deliberately_left_alone`
pins it so a future relaxation forces a re-review of this fix.

**Fixed** by aligning session with the name that actually reached the calendar,
downstream of every write gate: `_sync_booked_patient_name()` writes both
records together.

**It was four executors, not one.** Scoping this to `_exec_book_appointment`
would have fixed the Google Calendar clinic and left it live for the two the
port plans are about:

| Executor | Path |
|---|---|
| `_exec_book_appointment` (calendar branch) | JV / Google Calendar |
| `_exec_book_appointment` (manual-followup branch) | no event exists — the row IS the record |
| `_book_appointment_acuity` | Acuity clinics |
| `_book_appointment_provisional` | Vital Edge — practitioner has not accepted yet, so the row is the ONLY record of who asked |

**One earlier claim of mine was wrong and is withdrawn:** I said the confirmation
SMS would also carry the mangled name. It would not —
[sms_templates.py:107](../../app/sms_templates.py) reads `collected["name"]`,
which the executors *did* update. The SMS was already correct; only the summary
row (and Sheets, once `GOOGLE_SHEETS_ID` is set) carried the wrong name.

Test `tests/regression/test_a3b_booked_name_reaches_every_record.py` — 14
assertions, **7 fail pre-fix** across all three executors in both directions;
the helper tests skip rather than error pre-fix, by lazy `getattr`, so nothing
fails for the wrong reason. Suite A/B: 95 failed / 3454 passed before AND after,
failing sets diffed and **identical**.

Not separately dial-verifiable — it is invisible to the caller. It shows up on
the next real booking's summary row.

### Verified working on a live call for the first time

| What | Evidence |
|---|---|
| Date guard (`7b698f6` + `100b561`) | Call 1 survived **three** day changes. `v3_confirmed_slot_phrase refreshed 'Thursday…' -> 'Saturday the 8th…' (caller moved to the day now on offer)`. Zero `NOT corrected` lines |
| `8d152f0` verdict confirm | Call 2: *"no, it's actually 07798…"* did **not** store the caller ID |
| Keypad overwrite guard | Call 2, twice: `verbal phone confirm SKIPPED — keypad number already on record; refusing to overwrite it with caller ID` |
| `capture_phase` (`779ceda`) | `phone` on the phone turns, `name` on the name turns, `conversation` after |
| `B-13` prewarm branch (`a5e8415`) | Fired at ERROR on all three calls — see `B-26` for what it revealed |

> **The suite's Call 1 turn 9 and Call 2 turn 1 criteria are both wrong.**
> They expect an `L1 verdict: … -> yes|no` line at the booking phone-confirm.
> That call site logs `booking verbal phone confirm — stored calling number …`
> instead, and on the **no** path logs nothing at all. The pass criterion for a
> refusal is an **absence**: no store line, and the caller ID not on the
> booking. Fix the suite before dialling call 2 again or a blocker and a pass
> score identically.

---

## `B-25` · the phone confirm fired on a question about names

**CLOSED — `13dd9f3`, 2 Aug.** Found on sweep call 4, `CAce42c36b`.

```
21:57:48  v3_phone_dtmf_active = True (name confirmed — phone collection phase)
21:57:48  Susie: "Thanks Quentin — and your surname?"
21:57:53  Caller: "just quentin's fine"
21:57:53  [ms_conn v3] verbal phone confirm — stored calling number 07502211207
                       + phone_confirmed=True and exited DTMF
```

The caller was **refusing to give a surname.** It was read as consent to book on
the caller ID and set `phone_confirmed` — the flag satisfying
`book_appointment`'s A1 write gate — **24 seconds before the phone question was
asked.**

**Root cause: two sites, one gate.** Both perform the verbal phone confirm; only
the booking-flow site (`connection.py:6902` **at the parent `1328f39`**) tested
`_PHONE_STEP_MARKERS` against the live question. The DTMF-active site
(`:7094` at the parent) is a copy that never got one. `_phone_confirm_is_yes`
answers *"did the caller say yes?"* and has never answered *"yes to what?"* —
that is the gate's entire job.

**The window is not narrow.** `v3_phone_dtmf_active` goes True the moment a
**first name** is captured, so the whole surname exchange sits inside it.
Reproduced before writing the fix:

```
_phone_confirm_verdict("just quentin's fine")             -> "yes"
_phone_confirm_verdict("um yeah that'll be roch r-o-c-h") -> "yes"   ← the surname ANSWER
_phone_confirm_verdict("that's ok")                       -> "yes"
```

The surname answer trips it too; it only missed on that call because the refusal
one turn earlier had already switched DTMF off.

**Fix:** the marker test moved into `_phone_question_on_the_table` and **both**
sites call it. One implementation deliberately — this defect *is* a copy that
drifted, and a third private copy is how it recurs (§A4). Fails closed: an
unknown phrasing blocks the intercept and the LLM re-asks. No carve-out for a
bare *"use this number"* — every prompt that invites it already contains a
marker, so a caller cannot be coached to say it without the question carrying
one.

At `13dd9f3` the anchors are: the helper
[connection.py:1317](../../app/media_streams/connection.py), the booking-flow
caller `:6975`, the DTMF-active caller `:7161`. `_phone_confirm_is_yes` is
`:1288` and is unchanged.

Test: `tests/regression/test_phone_confirm_needs_the_phone_question.py`, 25
cases. `test_the_dtmf_site_is_gated` is the load-bearing fails-before. The two
invariant tests **pass** on the parent, which is what a preserved invariant
should do. Suite 95/2850, failing set byte-identical to baseline.

> **Two self-inflicted lessons worth keeping.** The first draft broke
> `test_phone_confirm_verdict.py` by landing a clause between the verdict and
> the keypad guard, which it pins by adjacency — the clause was moved rather
> than that test loosened. The second draft broke two more tests because the
> **comment** spelled `_phone_confirm_is_yes(utterance)` literally, and the
> sibling tests count call sites by substring over module source, comments
> included. Neither would have surfaced without diffing failing sets against
> baseline; looking for green would have shown 96 failures and explained
> nothing.

---

## `B-26` · the prewarm probes an endpoint the call path never uses — NEW

**Track A, ~15 min. Opened by `B-13`'s own fix.**

`prewarm()` fires `GET /v1/models`
([tts_stream.py:323](../../app/media_streams/tts_stream.py)) and now logs, on a
401:

> *"ElevenLabs rejected the API key (401) … credits exhausted or key invalid.
> The first call will fall back to OpenAI TTS mid-sentence."*

It fired after **all three** sweep calls. **The claim is false.** Synthesis uses
`POST /v1/text-to-speech/{voice}/stream` and returned **200 roughly thirty-five
times across the same three calls**, including twelve seconds after a 401.
Exhausted credits would fail the stream endpoint too. This reads as an API key
scoped for TTS but not `models_read`.

So `B-13` traded a silent lie for a loud one: an ERROR on every call, naming a
consequence that does not occur. Fix is to probe something the synthesis path
actually needs, or to demote a `/v1/models` 401 to a warning that says what it
means.

> Recorded honestly: I called this demo-blocking on first read and was wrong.
> The evidence to catch it — thirty-five 200s in the same log — was present in
> call 1 and I did not weigh it until call 2 repeated the pattern.

---

## `B-27` · the DIFFERENT-DAY steer fires on a slot *selection* — NEW

Two sightings, calls 2 and 4.

- Call 2: *"thursday the 6th at half past 4 you said the first slot you offered"*
  → `DIFFERENT DAY REQUESTED steer applied` → a **redundant**
  `check_availability` for the same day, identical result, ~4 s of the caller's
  time.
- Call 4: *"um yeah this saturday at quarter to 12 in the morning works"* → same.

Both are the caller **choosing** from what was just offered. Same shape as
`B-16`: a selection misread as a navigational request. Cheap, cosmetic, no
correctness exposure — but it makes the steer's log line untrustworthy as
evidence that a day change happened.

**It is countable without Render logs.** obs exports the counter in `guards`, so
the false-positive rate is measurable across the whole corpus:

| Call | `different_day_steer_fired` | Genuine day changes | False |
|---|---|---|---|
| 1 `CAa8862c12` | 2 | 2 — the caller really did ask for Friday, then Saturday | 0 |
| 2 `CA2ada6263` | 1 | 0 | **1** |
| 4 `CAce42c36b` | 1 | 0 | **1** |

Call 1 is the control and it matters: the steer is **not** simply always-on. It
fires correctly on a navigational request and incorrectly on a selection, which
is what makes it a discrimination defect rather than noise. Two of four firings
across three calls were false — a rate worth confirming against the stored corpus
before scheduling, since that is a cheap query and this row is otherwise cosmetic
enough to deprioritise on impact alone.

---

## `B-28` · the orphan detector cannot see the screens it exists to count — NEW

Sweep call 2 asked a full DVT screen (verbatim in the `B-20` section below).
**There is not one `clinical_screening` line anywhere in that call** — no
`trigger`, no `orphan`, no `red_flag`.

Every orphan in the register's count of ten was caught at
[clinical_screening.py:975](../../app/media_streams/clinical_screening.py). This
one was not. **The ten is a floor of unknown tightness**, and any `B-20`
frequency estimate built from obs understates by an unknown amount. This
compounds the existing evidence caveat (only 2 of 13 trigger-armed calls have
their arming utterance stored) — that one limits *arming* statistics, this one
limits *orphan* statistics, which is the half `B-20` rests on.

**Root-caused and fixed the same night — it is `B-31`.** The floor caveat above
stands and is now measured: replayed over 992 stored bot turns, the cap was
eating **11 orphan detections**, against 26 the matcher caught. The count of ten
was missing roughly as many again.

---

## `B-29` · ~~the DVT grader knows only half of its own question~~ — **WITHDRAWN 2 Aug, same night it was opened**

I claimed the grader's red-flag vocabulary was `swollen / warm / hot / red` — the
first half of its own question — so *"long journey"* would go unscored. **That is
false.** `clinic.json`'s `dvt.red_flag_answer_keywords` is:

```
swollen, warm, hot, red, surgery, operation, flight, long drive,
long journey, bed rest, immobile, can't walk on it
```

Both halves are covered, including the exact phrase the caller used. Replayed
against the stored turn:

```
classify_screen_answer(<call 2 caller turn>, dvt) -> "red_flag"
_red_flag_hits(...)                               -> 1
```

I wrote the row from the four keywords quoted in `B-20`'s amplifier paragraph and
never opened the config. **That paragraph is wrong too and is corrected below.**

The one true thing in the original row — *whatever asks the question must grade
all of it* — is already satisfied here. Do not schedule this.

---

## `B-30` · two write fillers play back to back — NEW

Call 4, 21:58:54–55: *"Just locking that in now…"* then *"Getting that all
booked in for you…"*, one second apart. The `TTS dedup: skipping duplicate
chunk` guard that caught this on call 1 keys on identical text and these differ.
Cosmetic. Belongs with `B-07`/`B-19` as the same filler-cadence decision.

---

## `B-31` · a 200-character cap silently switched off the clinical safety net

**CLOSED — 3 Aug.** Root cause of `B-28`. Deterministic, proven offline, no dial
time needed.

`last_bot_prompt` is capped at 200 characters
([llm_stream.py:1281](../../app/media_streams/llm_stream.py)):

```python
session[F_LAST_BOT_PROMPT] = _apply_tts_subs(_display_reply)[:200]
```

`match_asked_screen` — the orphan detector — reads that field and early-returns
`None` if it contains no `"?"`
([clinical_screening.py:424](../../app/media_streams/clinical_screening.py)),
on the reasoning that *"every configured screen_question ends in '?'"*. True of
the config. **Not true of a 200-character prefix of one.**

Sweep call 2's screening question, measured against the exact stored text:

```
len(question)                     = 205        ← five characters over
question[:200] ends with '?'      = False
match_asked_screen(FULL)          = 'dvt'
match_asked_screen(question[:200]) = None      ← and returns SILENTLY
```

The silent return is why `B-28` saw no log line of any kind. A detector that
found nothing logs nothing, so a suppressed safety check and a call with no
red flags are **indistinguishable in the logs**.

### What the deterministic layer would have done

Replayed offline through the real functions against the real stored turns:

```
match_asked_screen           -> 'dvt'
update_screening_state       -> action='escalate', block=True
                                "…need checking urgently to rule out a clot…
                                 please contact NHS 111 now…"
screen_arm_paths             -> {'dvt': 'orphan'}
screen_red_flag              -> 'dvt'
```

So on that call the deterministic layer had computed an NHS 111 escalation and a
booking block, and a five-character overrun meant it was never consulted. Susie
said *"That's reassuring"* and booked the appointment instead.

> **Two defects cancelled each other out, and neither is safe.** `B-20`'s
> recorded amplifier risk — *"an incidental affirmative would produce a
> deterministic NHS 111 escalation for a complaint that never warranted one… no
> call has hit this, so it is live risk, not live damage"* — **call 2 hit it.**
> It was the first call to satisfy the condition. The only reason it did not
> produce a false escalation is that `B-31` suppressed the layer entirely. The
> outcome we observed (booked, with a false reassurance) is the *product* of two
> failures, and fixing either one alone changes what the next such call does.
> **Fix `B-31` before `B-20` and the next ankle caller gets sent to NHS 111.**
> They must be scheduled as one piece of work.

### Blast radius — and why it is exactly the orphan path

Every configured `screen_question` is comfortably under the cap, so **Layer 1's
own questions never truncate**:

| Screen | `len(screen_question)` | Survives the cap |
|---|---|---|
| `cauda_equina` | 160 | ✅ |
| `dvt` | 185 | ✅ |
| `serious_spinal` | 165 | ✅ |
| `trauma_fracture` | 173 | ✅ |
| `vbi_neck` | 182 | ✅ |
| `inflammatory` | 150 | ✅ |

Only a **model paraphrase** can overrun — and a paraphrase is by definition the
Layer 2 orphan case. The arithmetic on call 2, exactly:

```
config  dvt.screen_question                                       185
  swap "Before we go further,"  →  "Before we look at getting you booked in,"   +19
  swap "the area"               →  "the ankle"                                  +1
spoken                                                            205   → cap 200
```

Nineteen characters of conversational throat-clearing is the entire margin.
**The bug fires precisely and only on the path `B-20` is about**, and it fires on
a paraphrase no reviewer would look at twice.

`last_bot_prompt` has ~20 other readers in `connection.py` (CTA-affirm, keypad
prompts, the surname window). The truncation is pre-existing and known — the
comment at [connection.py:9836](../../app/media_streams/connection.py) already
records it hiding a booking CTA past char 200 — so this is the **second** defect
from the same cap. Scope any fix against those readers before widening it.

### Fix as shipped

Options 1 and 2 of the three considered. **Option 3 — raising the cap — was
deliberately not taken**: ~20 other readers in `connection.py` were written
against 200 and one (CTA-affirm) has already been tuned around it.

1. When `last_bot_prompt` carries no `"?"` **and is at or over the cap**, fall
   back to `last_question`, which holds the extracted question sentence and is
   **not** truncated. The text was in the session all along — the pre-existing
   fallback just required `last_bot_prompt` to be *empty* rather than *unusable*.
2. Two log lines, because the defect's whole cost was silence: a WARNING when the
   fallback rescues a truncated question, and an INFO **NEAR MISS** when a screen
   matches ≥1 but fewer than `_ORPHAN_MIN_EVIDENCE` evidence words.

**The length gate is the load-bearing half.** `last_question` is only rewritten by
`llm_stream`; `connection.py`'s ~20 short deterministic writers (fillers, keypad
prompts) overwrite `last_bot_prompt` and leave `last_question` **stale**. An
unconditional fallback would arm DVT off a filler and escalate a caller who was
never asked anything. Every such writer is far below 200 chars, so the gate
excludes them by construction.
`test_the_fallback_does_not_reach_a_stale_last_question` is that case and passes
on the parent — do not relax it.

> **The sibling function was never exposed, and that is the tell.**
> `_question_was_asked` ([clinical_screening.py:289](../../app/media_streams/clinical_screening.py))
> reads `last_bot_prompt + " " + last_question` — **both**, concatenated — so
> truncation could never blind it. `match_asked_screen` used `or`: either/or.
> The orphan detector is a later copy of the same idea that diverged on one
> operator. §A4 again, and the same shape as `B-25`. It was **not** fixed by
> simply copying the concatenation: that would make the stale-`last_question`
> path live on every turn, which the gate exists to prevent.

### Measured against the stored corpus before shipping

Replayed old vs new `match_asked_screen` over **992 assistant turns across 133
calls** in obs:

| | Count | Share of bot turns |
|---|---|---|
| Turns hitting the new fallback branch | 116 | 11.7% |
| **Turns whose outcome actually changes** | **11** | **1.11%** |
| — of those, `None` → a screen | 11 | all of them |
| — of those, a screen → something else | **0** | — |
| NEAR MISS lines emitted | 39 | 3.9% |

Every one of the eleven is a genuine screening question the model asked and the
cap was eating — `dvt` ×6, `trauma_fracture` ×3, `cauda_equina` ×2, on calls
`CA2ada6263`, `CA3264ed4b`, `CAfd801441`, `CA782fff7c`, `CAdd3373ad`,
`CA325372e5`. **Not one spurious match.** So the fix recovers real orphans rather
than manufacturing them, and `B-28`'s "the ten is a floor" is now quantified: the
detector was missing roughly as many orphans as it caught.

The 3.9% near-miss rate is about one line every couple of calls — visible without
being noise. `test_an_ordinary_booking_turn_logs_nothing` pins the quiet case.

### What this changes on a live call — read before deploying

**A call shaped like sweep call 2 now escalates to NHS 111 and blocks the
booking.** That is a real behaviour change on the demo build and it is the
opposite of what happened on 2 Aug.

It is still the right way to fail, and the reasoning should not be relitigated
later:

- The escalation already fires today for any orphan whose question happens to be
  **under** 200 characters. The cap was not a safety decision, it was a lottery
  on sentence length. This makes behaviour length-independent, not more
  aggressive in principle.
- Of the two failure directions, an over-cautious *"please get that checked"* is
  survivable; *"that's reassuring"* to someone who just confirmed a risk factor
  is not.

**But the friction is real and it is commercial**, three days before the demo: a
caller who wanted an ankle appointment is told to ring NHS 111 and is not booked.
That cost is `B-20`'s, not this fix's — the question should never have been asked
— which is exactly why the two are scheduled together and why the authority
decision is now urgent rather than merely open.

### Verification

| Check | Result |
|---|---|
| Parent baseline (`1fe8f7f`, clean worktree) | 93 failed / 2848 passed |
| With the fix, **same worktree** | 93 failed / 2865 passed (2848 + exactly the 17 new tests) |
| Failing-set diff | **Byte-identical.** Zero regressions, zero accidental fixes |
| Main tree, corroboration | 95 failed / 2867 passed — failure count unchanged from the recorded 95, and 2850 + exactly 17 |
| Fails-before | 6 of 17 fail on the parent |
| Load-bearing failure reasons | `assert None == 'dvt'` and `assert 'none' == 'escalate'` — the defect itself, not an import error |
| Invariants | 11 of 17 **pass** on the parent, including the stale-`last_question` safety case |

Test: `tests/regression/test_orphan_survives_the_prompt_cap.py`, 17 cases.

> **The 93 is not the 95 you may be expecting.** The main tree fails 95; a clean
> worktree of the same commit fails 93 and skips 8 rather than 4. The difference
> is untracked files the worktree does not have (`.env` and friends), not code.
> Both numbers are stable; what matters is that baseline and post-fix were run in
> the **same** tree. A cross-tree comparison here would have invented two
> regressions that do not exist.
>
> Also worth keeping: the first baseline of the night was captured while an edit
> landed mid-run, and the second was read from a **tail-truncated** background
> log that yielded 14 of 95 `FAILED` lines. Both were discarded. Capture failing
> sets to a file you redirect yourself, and never diff across trees.

## `B-32` · STT noise defeats Layer 1's exact-phrase triggers — NEW

**Opened 3 Aug. A finding, not a lead — two observed instances with call SIDs.
It is NOT a vocabulary gap and must not be fixed by adding keywords.**

Both words were already in `clinic.json`. The transcriber destroyed them:

| Call | Caller said (as transcribed) | Layer 1 | Config has | The model |
|---|---|---|---|---|
| `CAcaae3aa7` (25 Jul) | *"hi my **call's** been very sore lately"* | `None` | `calf` | asked `dvt`, caller disclosed **recent surgery**, escalated to NHS 111 — **correctly** |
| `CA3264ed4b` (30 Jul) | *"for some **back pin**"* | `None` | `back pain` | said *"back pain can be really debilitating"*, asked `cauda_equina` — **correctly** |

Verified against the matcher directly:

```
match_screen_trigger("hi my calf has been very sore lately") -> dvt
match_screen_trigger("hi my call's been very sore lately")   -> None
match_screen_trigger("for some back pain")                   -> cauda_equina
match_screen_trigger("for some back pin")                    -> None
```

One letter in each case. `_norm` deletes apostrophes, so `"call's"` becomes
`calls`, and `_kw_in`'s inflection whitelist (`s|es|ed|ing|ness`) does not bridge
`calf`→`calls` or `pain`→`pin`. Both behaved exactly as designed.

**These are the only two Layer 1 misses in the corpus** — 2 of 18 orphan calls,
the other 16 being genuine `B-20` over-reach. Both were caught by Layer 2, which
is why they are the load-bearing evidence in the `B-20` A/B/C decision and why
that recommendation moved from C back to B.

### Why "just add the words" is wrong

The obvious config fix is a trap and the numbers say so. `call` appears in **2 of
999** caller turns; the other is *"hi can you call me back later"* — no complaint,
no pain word. Adding `call` or `calls` to `dvt.trigger_keywords` would arm a DVT
screen on a caller asking to be rung back, i.e. **manufacture a `B-20` orphan of
exactly the kind we are trying to remove.** `pin` as a back trigger is worse.

More fundamentally: STT errors cannot be enumerated. Three confirmed manglings of
clinical vocabulary in this corpus alone — `calf`→`call's`, `back pain`→`back
pin`, and `ankle's`→`angles` (`CAc0a67a9d`, *"my left angles in a lot of pain"*).
There is no finite keyword list that closes this.

> The `angles` one is worth noting precisely because it changed nothing: plain
> ankle pain is not a trigger for any screen, so Layer 1 was silent for the right
> reason and the mangling was irrelevant. Only manglings that land on a word
> carrying clinical signal cost anything — which is why the count here is two,
> not three.

### What is actually available

1. **Nothing — accept Layer 2 as the cover.** This is what happened on both calls
   and it worked. It is also `B-20` option B's implicit position, and the honest
   reason B beats C.
2. **Phonetic or edit-distance matching on trigger keywords only.** Would catch
   both. Engine work in `_kw_in`'s neighbourhood, with a real false-positive
   budget to establish first — `_kw_in`'s docstring records that loosening
   matching is precisely how `red`/`tired`, `numb`/`number`, `hot`/`photo` and
   `fell`/`fellow` produced false escalations. **Not a pre-demo change.**
3. **One safe config addition, unrelated to STT**, found while scoping this and
   worth doing on its own merit: `calf` does not match `calves` (irregular
   plural — `_kw_in`'s docstring says explicitly these "belong in the clinic
   config as their own keywords"), and `calves` is **absent** from
   `dvt.trigger_keywords`. It occurs **0 times** in 999 caller turns, so it
   carries no collision risk. Small, safe, and it does not touch this row's root
   cause — a real caller saying *"my calves have been sore"* arms nothing today.

**Do not schedule 2 before the demo.** Option 1 is the status quo and is adequate
*provided `B-20` resolves to B or A rather than C* — which is the dependency
worth writing down. Option 3 is fifteen minutes and independent of all of it.

> **Scoping honesty.** I first tried to size this with seventeen phrasings I
> invented and judged "should arm", of which ten missed. That instrument was
> wrong and the number is discarded: I am not the clinician who decides what
> warrants a screen, and constructing a coverage gap is exactly the mistake that
> made `B-24` wrong. Everything above is observed caller speech with a call SID.
> Anything that needs a clinical judgement on what *ought* to trigger belongs to
> Marcus, not to this register.

---

## Closed

### `U-06` · reschedule lookup matched phrases instead of judging consent
**CLOSED — `48d9e57`, 2 Aug.**
The last live caller of a predicate empirically proven to accept *"don't use that
one"* as a confirmation. Now routed through `_phone_confirm_verdict`.
Test: `tests/regression/test_reschedule_phone_confirm_verdict.py` (167 lines).

### `B-18` · the same-breath guard discarded the caller's reply during any slow turn
**CLOSED — `ab60809`, 2 Aug.**

Root cause, and the reason this was not a duplicate of `B-19`: the guard dropped
any utterance enqueued before `_last_turn_done_at`, which is set in the `finally`
of the LLM turn — i.e. when *generation completes*, not when audio ends. On a turn
with `llm_ttft_ms=13868`, every second of the caller's experience of that silence
was a second in which their speech was classified as a same-breath straggler and
thrown away. **The slower the system got, the more reliably it discarded the
caller's reaction to the slowness.** `B-19` is the upstream spike and is not ours;
`B-18` was ours, and it converted a spike into a lost utterance and a dead call.

Fix: an age bound. `_SAME_BREATH_WINDOW_S = 2.0` at
[connection.py:1403](../../app/media_streams/connection.py) — a breath is short,
whatever `_last_turn_done_at` says. The overlapping-turn protection at the Spec N
guard is unchanged.
Test: `tests/regression/test_same_breath_window.py` (250 lines).

*Follow-on, not yet done:* the four stacked `_in_name_collection` exemption arms
exist to patch the same over-reach and should now be reducible. Do this only with
the tests above green — it is cleanup, not a fix.

---

## Parked — `B-01` – `B-04`

Two provisioning items plus the name work. Parked by owner decision on 2 Aug, not
for want of capacity. **The name work overlaps `A3` in `DEFECT_REGISTER.md`**
(surname written to a clinical record with no read-back, no confirmation, no audit
trail — four manglings of one caller's name across four calls, three of them
booked to real Acuity events). Whoever unparks `B-01`–`B-04` should read `A3`
first: today the prompt *forbids* a surname read-back
([clinic_template_prompt.py:2057](../../app/prompts/clinic_template_prompt.py)),
so this is a design reversal, not an addition.

---

## Track A — deterministic, testable without a phone

### P3 hygiene batch — `B-13` `B-14` `B-15` (`B-17` deferred, see below)

All of them make logs or config lie. They are cheap, and they are the instruments
every other item on this register will be read through — which is why they come
before the behavioural work rather than after it.

#### `B-13` · a 401 from ElevenLabs is logged as "ready"
**CLOSED — `a5e8415`, 2 Aug.** The prewarm interpolated `resp.status_code` into
its log line but never branched on it, so an auth failure read as
`prewarm: connection ready in 214ms (status=401)` at INFO. The socket really is
warm on a 401 — that is why it looked like success — but the credential is dead,
and the prewarm fires at webhook time, the earliest anything can know.
`synthesise_chunk` already calls the same status an error and switches the
process to the OpenAI fallback; it just does not run until a caller is on the line.

Now: 401 → error naming the cause; other 4xx/5xx → warning naming the status;
2xx → today's INFO line verbatim. That last branch is half the fix — a prewarm
that warns on every healthy start drowns the line it exists to surface.
Unchanged and asserted: the return value (latency accounting, not health) and
`_ELEVENLABS_EXHAUSTED`, which stays unarmed here.
Test: `tests/regression/test_prewarm_status_is_not_ready.py`, 12 cases;
6 verified failing on the parent commit.

> **Still open, deliberately:** should the prewarm arm `_ELEVENLABS_EXHAUSTED`?
> Today a startup 401 still costs the first caller a failed synth before the flag
> flips on the synth path. Arming it at startup moves the fallback decision onto a
> probe — defensible, but a behaviour change needing its own evidence. Not yet
> given an ID; give it one if it is scheduled.

#### `B-14` · the pronunciation dictionary is inactive and the config is the wrong shape
**CLOSED — turned off, 2 Aug.** Owner decision: off.

`config/pronunciation_dict.json` contained `{"Alcester": "Awlstuh"}` — a
word→alias map — while the loader wanted a `{pronunciation_dictionary_id,
version_id}` locator pair. The lookup failed on every startup, `_PRON_DICT_LOCATOR`
stayed `None`, and the request body was never touched. **The dictionary had never
been active.**

Removed rather than repaired: the one word it demonstrably mattered for is
already covered locally and deterministically by `_TTS_SUBSTITUTIONS_ELEVENLABS`,
and enabling it would have put pronunciation in two places free to disagree — the
§A4 pattern. It had never executed, so removal cannot change a call; *enabling*
would have been the risky move three days before a demo.
Test: `tests/regression/test_pronunciation_has_one_owner.py`, 6 cases — five of
them pinning the local Alcester rule, since that is now the only line of defence.
`scripts/setup_pronunciation_dictionary.py` is kept; re-enabling is documented in
the tombstone comment at the removal site.

#### `B-21` · "Redditch" has no pronunciation rule — NEW, opened by the B-14 removal
**Lead, not a finding.** `scripts/setup_pronunciation_dictionary.py` states that
`Redditch` synthesises with a doubled-d artefact and the dictionary carried an
alias for it. `_TTS_SUBSTITUTIONS_ELEVENLABS` covers **only** `Alcester`, so with
the dictionary gone nothing covers Redditch.

Deliberately not fixed alongside `B-14`: adding a substitution changes spoken
output on the strength of a claim in a script docstring of unknown age, and
Redditch may not be live vocabulary on this branch at all (several
`fix/*redditch*` branches redirect it to Alcester). **Confirm by ear before
changing anything** — one call where Susie says the word settles it.

#### `B-15` · `capture_phase` mislabelled
**CLOSED — `779ceda`, 2 Aug.** Fixed as planned below, plus one adjacent defect
found while writing the diff: the phone branch had only hard flags where the name
branch had a prompt fallback, and `v3_awaiting_phone_confirm` is set in exactly
one place (connection.py:5335, the reschedule/cancel DTMF path). On an ordinary
booking the phone step therefore **never** resolved to `"phone"` — so the phone
re-ask wording at connection.py:13599 was unreachable on the booking path. Both
had to move together: reordering alone would have demoted the phone step from
`"name"` to `"conversation"`, trading a wrong label for a worse one.
Test: `tests/regression/test_capture_phase_follows_the_question.py`, 20 cases;
11 verified failing on the parent commit, and the 9 that pass there are exactly
the invariants the fix preserves.

**Behaviour change to watch on the next sweep:** dead air at the phone step now
answers *"is the number you're calling on the best one to reach you? Just say use
this number"* instead of asking for the name again. That branch is pre-existing
and was written for this case; this is the first build on which it can fire.

> **Honest verdict on the severity I claimed — the headline scenario may not be
> constructible.** Recorded 2 Aug after sweep call 4.
>
> I sold `B-15` on consumer 1: a caller who goes quiet at the **booking-confirm**
> step gets asked for their name again. That needs `v3_awaiting_surname` stuck
> `True` at a later step, which needs the call to *reach* a later step with no
> surname captured. Both clear sites require a surname to have been found —
> [connection.py:1869](../../app/media_streams/connection.py) (backfill) and
> `:1924` — so the stickiness is real. But **the call does not get past the name
> step without one.** Sweep call 4, verbatim:
>
> ```
> [user     ] just quentin's fine
> [assistant] I do need a surname to complete the booking — could you give me one?
> [user     ] i'd rather not just quentin
> [assistant] I completely understand, but I do need a surname to complete the
>             booking — it's just so we can get you properly registered.
> ```
>
> Two refusals, two hard re-asks, and the caller gave one. That matches step 7's
> *"A surname is REQUIRED before booking"*
> ([clinic_template_prompt.py:2062](../../app/prompts/clinic_template_prompt.py)).
> It is **prompt** adherence, not a deterministic gate, so it is not a proof —
> but I have no live instance of consumer 1, and the mechanism that would produce
> one is actively fought by the prompt. Treat the caller-audible claim as
> **unproven**, not as demonstrated.
>
> **The fix stands regardless, on the adjacent defect rather than on this one.**
> The phone branch never resolving to `"phone"` on an ordinary booking — so the
> phone re-ask wording at connection.py:13599 was unreachable — is live,
> caller-facing, and has nothing to do with the sticky flag. That is what `779ceda`
> is worth. Consumers 2 and 3 remain correctness fixes behind default-off flags.
>
> Filed here rather than quietly dropped because this is the fifth time a one-line
> defect description has turned out to be mis-scoped once anchored, and the
> pattern is worth more than the row.

Original analysis follows.

**Root cause: a sticky flag outranked the live question.**

Not one of the three call sites — the resolver itself.
[`capture_phase()`](../../app/media_streams/latency_timing.py) (latency_timing.py:110-190)
tests in this order: phone flags → `v3_awaiting_surname` → prompt keywords. The
middle test is the defect, because that flag is never cleared by anything outside
name capture.

`v3_awaiting_surname` has **exactly three assignment sites**, all inside
`_v3_try_capture_name` in connection.py — `:1869` and `:1924` set it `False`,
`:1930` sets it `True`. Both `False` sites require a surname to have actually
been found. The code says so itself at connection.py:1833: *"v3_awaiting_surname
is sticky: nothing clears it when the conversation moves on."*

> Line numbers in this subsection were re-anchored 2 Aug at `13dd9f3`; the
> original four (`:1826` `:1881` `:1887` `:1790`) were stale by ~43 lines and
> pointed into the middle of the same function. The claims were unchanged.

So a caller who gives a first name only leaves the flag `True` **for the rest of
the call**, and `capture_phase()` answers `"name"` on every subsequent turn —
the slot choice, the phone step, the booking confirmation, the closing. The live
question is ignored in favour of a stale flag.

Three consumers, three very different costs:

| # | Consumer | Cost | Live today? |
|---|---|---|---|
| 1 | Dead-air re-ask, [connection.py:13588](../../app/media_streams/connection.py) (`_cap_phase` resolved at `:13564`) | **Caller-audible.** `_cap_phase == "name"` selects *"Sorry — could I take your first name and surname again?"* So a caller who goes quiet at the **booking-confirm** step is asked for their name again. **Unproven live — see the honest verdict above** | **YES** — the site comments *"pure lookup, independent of LATENCY_TIMING"* |
| 2 | `[LAT]` / `[LAT-EP]` lines (latency_timing.py:259 and `:302`; `_ep_prev_phase` at connection.py:6895 carries it into `emit_cutoff`) | Every turn after the stick is bucketed `name`. Phone-capture turns recorded as name turns; any per-phase latency or cutoff analysis is wrong for the whole tail of the call | No — `LATENCY_TIMING` defaults `false` |
| 3 | `_ws_c_apply_endpoint_profile`, [connection.py:12986](../../app/media_streams/connection.py) | Early-returns when the phase is unchanged. Stuck at `name`, the **phone** profile is never pushed and the **conversation** profile is never restored — flatly contradicting its own docstring, *"Leaving capture restores the conversation profile"* | No — `WS_C_SEMANTIC_ENDPOINT` defaults `false` |

> **Consumer 1 probably explains some of `B-08`** ("asks for information already
> given"). Investigate `B-08` with this in mind before treating it as a prompt
> problem — a dead-air re-ask that asks for the name again is exactly that
> symptom, and it is deterministic, not model behaviour.

**Fix plan** (~30 min + test):

1. Reorder `capture_phase()` so the **live question** is judged before the sticky
   flag: prompt keywords first, `v3_awaiting_surname` only as a tiebreaker when
   the prompt says nothing either way. Phone flags stay first — they are set and
   cleared tightly.
2. **Do NOT clear `v3_awaiting_surname` instead.** Its stickiness is load-bearing:
   branch 3 of `_v3_backfill_surname` depends on it to accept a bare straggler
   word as the surname. Clearing it early re-breaks surname capture — the exact
   defect `96417a9` and `aa0b3bd` were fixed to close.
3. Test: parametrised sessions asserting a stuck `v3_awaiting_surname` no longer
   turns a phone-step or booking-confirm turn into `"name"`, plus the
   false-negative half — a genuine name turn must still resolve to `"name"` when
   the prompt is the name question.
4. `capture_phase()` is a pure function with three consumers and no other
   callers, so blast radius is exactly the table above.

**Verify before writing the diff:** that reordering cannot lose a real name turn
whose `last_bot_prompt` has already moved on. That is the case the sticky flag
was presumably added for, and it is the one thing the fix could regress.

#### `B-17` · booking SMS reports success it never checked
**ANCHORED 2 Aug — and larger than "a no-op log".**

[`booking_sms.py`](../../app/notifications/booking_sms.py) calls `send_sms` at
**eleven** sites across nine functions — lines 103, 174, 183, 191, 244, 290, 338,
369, 395, 427, 455. **Not one captures the return value.** Every path then logs a
success line and `return True`.

`send_sms` returns `None` in three distinct cases: the kill switch is off
([sms.py:76](../../app/notifications/sms.py)), the number fails E.164 validation,
or Twilio raises. All three are reported as sent.

The correct pattern already exists in this codebase, in the neighbouring module —
[owner_alert.py:120-128](../../app/notifications/owner_alert.py) captures the sid,
logs `"send returned no SID — not sent"`, and returns `False`. `booking_sms` is
the copy that drifted.

**Severity is branch-dependent, and that is the point:**

- **On `latency-eval`:** log-only. `SMS_ENABLED` defaults `false`, so the line is
  the known "SMS is lying on every call" noted in `CALL_SUITE_2026-08-02.md` §0.1.
  The return is consumed by nothing on the call path — only a debug route
  ([twilio.py:1312](../../app/routes/twilio.py)).
- **On `jv-v1-onboarding` and `vitaledge-onboarding`, where `SMS_ENABLED`
  defaults `true`:** a genuinely failed booking confirmation — bad number, Twilio
  outage — is logged as sent and returns `True`. A patient silently gets no
  confirmation and nothing anywhere says so. That is **FM-15 territory**, not P3.

**Fix plan** (~45 min + test):

1. Capture the sid at all eleven sites; mirror `owner_alert` exactly rather than
   inventing a second shape.
2. A function with multiple sends (`send_24hr_reminder`, sites 174/183/191) must
   decide what a partial success returns. Suggest: the primary send governs the
   return, extras are logged individually — but **make it explicit**, because
   today it is an accident.
3. Test: `send_sms` patched to return `None`, asserting every public function
   returns `False` and logs no success line; and the mirror case with a sid.
4. **Canonical-first applies and matters here.** Fix lands on `latency-eval`,
   then cherry-picks to both onboarding branches — which is where it actually
   pays, since this branch cannot send an SMS at all.

---

## Deferred — the SMS family, `B-17` and `B-22`

**Owner decision, 2 Aug: all SMS work goes to the end of the queue.**

The reasoning is sound and worth writing down so it is not relitigated. SMS
cannot fire on this branch at all (`SMS_ENABLED` defaults `false` and must stay
that way here), so nothing in this family is provable by any call we can place
before the demo. Every hour spent on it is an hour of unverifiable work while
`B-09` silently books wrong days and the by-ear backlog grows.

**What deferring costs, stated plainly** so the decision can be revisited with
open eyes: on the two live clinic branches, a booking SMS that genuinely fails
stays invisible for as long as this sits in the queue. That is a real patient
getting no confirmation with nothing in the record to say so. It is the right
trade against a demo three days out, and it stops being the right trade the
moment the demo is behind us.

> **One leg of that argument has since gone, 3 Aug.** The "unverifiable SMS work
> versus `B-09` silently booking wrong days" comparison no longer holds — `B-09`
> is closed (`00ae6df`) and Track A is empty. The deferral may still be right,
> but it now rests only on the unverifiability point, not on a more urgent
> defect competing for the same hours. Worth re-putting to the owner rather than
> letting it stand by inertia — especially as `B-17`'s failure mode (a booking
> SMS that fails silently on a live clinic branch) is a bar-1 correctness
> problem, and `B-45` has just demonstrated that "inert on this branch" is
> exactly how a gap survives review.

**Do them as one batch when they come up.** `B-17` and `B-22` are the same
subsystem, they want the same test scaffolding, and they cherry-pick to the same
two branches in the same operation.

### `B-17` · booking SMS reports success it never checked
Full analysis above. Anchored, plan written, ~45 min. **Deferred.**

### `B-22` · Susie promises a text before anyone knows it was sent — NEW
**Lead, opened 2 Aug while scoping `B-17`. Deferred with it.**

The closing line is built from the `SMS_ENABLED` **env var at prompt-construction
time**, not from the send's outcome —
[clinic_template_prompt.py:1710](../../app/prompts/clinic_template_prompt.py):
`_text_promise = " I've just sent you a confirmation text." if _sms_on else ""`
(and `:2133` for the reschedule closing).

So on a live branch with the flag on, Susie tells the caller the text is sent
before the send has been attempted, let alone confirmed. **`B-17` does not fix
this** — it makes the failure visible in logs; this one is what the caller hears.
Of the two, this is the one with a caller-facing consequence, and it needs a
different shape of fix: defer the closing, or condition it on the write result.

Harmless on `latency-eval` (flag off ⇒ the promise is never spoken). Matters only
where SMS is live, which is the same place `B-17` matters — hence one batch.

---

### `B-09` · "next Friday" resolves +12 days — **FIXED `00ae6df`**

> **RE-SCOPED A SECOND TIME, 3 Aug, and the verdict below is wrong.** Mapped in
> `bff8b9c`, fixed in `00ae6df`. It is **not** model-side arithmetic: it is our
> own off-by-one-week, in three duplicated copies.
>
> ```python
> days_until_sunday = (6 - weekday) % 7      # == 0 on a Sunday
> this_sunday = now + (days_until_sunday if days_until_sunday > 0 else 7)
> ```
>
> On a Sunday the `else 7` fires, so "this Sunday" becomes *next* Sunday and
> `next_monday` — literally tomorrow — is handed to the model as **eight** days
> away. A model counting Friday from that anchor lands on **+12**, which is the
> number this row is filed under. **The model's arithmetic was never at fault;
> the anchor was seven days late.** Wrong on Sunday only, which is how it
> survived two months.
>
> A fourth implementation, `_extract_week_range._next_monday` in
> `receptionist_tools`, was **correct all along** (`7 - weekday`) — so on Sundays
> the two halves of the system disagreed by exactly seven days, and the
> `check_availability` schema instructs the model to use the wrong one. Observed
> live: `after_date` arrives as a literal while `date_hint` carries `"any"`, so
> the correct resolver is bypassed on the path that matters.
>
> Now **one** implementation, `app/date_context.py`, dependency-free for the same
> reason `app/name_capture.py` is. Each call site keeps its own formatting, so no
> model-visible text changes except the corrected dates. Second defect fixed in
> the same line: `_build_date_prefix` used a bare `date.today()`, server-local
> rather than Europe/London — a day behind between 23:00 and midnight on a UTC
> container under BST. The zone is now explicit.
>
> 58 tests, and **they are the verification rather than a call** — this
> reproduces on Sundays only, so a dial-time check on any other weekday proves
> nothing. Seven weekdays swept in both BST and GMT, the old arithmetic pinned as
> still producing 8 and 12 so it cannot be quietly reverted, and source
> assertions that no call site has grown its own `% 7` or `else 7` again.
>
> **Scope held, and the residual is stated rather than swept:** the tool-side
> resolver *also* uses server-local time. Its weekday arithmetic is correct so it
> is not part of this defect, but under BST near midnight it would resolve
> "tomorrow" to today. Recorded here, deliberately not widened into.
>
> `_DOW_RE` / `_DOW_INDEX` remain defined and referenced nowhere. Wire them in or
> delete them — they look exactly like the machinery a weekday resolver would
> use, so the next reader will assume it exists and is broken.

**Superseded — the 2 Aug re-scope, kept for the reasoning it records.**

It was queued as "pure date arithmetic, ~1 h, fully testable." That assumes there
is arithmetic to fix. There is not:

- `_DOW_RE` and `_DOW_INDEX`
  ([receptionist_tools.py:323-331](../../app/tools/receptionist_tools.py)) are
  **defined and never referenced anywhere in the codebase.** Dead code, both.
- The week-filter resolver handles `"next week"`, `"this week"`, `"week of …"`,
  `"from <date>"` and two-date ranges. It has **no `next <weekday>` branch at
  all.**
- The only thing steering the phrase is prompt text:
  [clinic_template_prompt.py:2292](../../app/prompts/clinic_template_prompt.py)
  hands the model next Monday's literal date as the anchor for *"not this week"*.
  A model counting Friday from that anchor on a Sunday call lands **exactly +12
  days** — the observed symptom.

So `B-09` is **model-side arithmetic with no deterministic floor under it.** The
fix is to add a resolver and wire it into the `date_hint` path, then decide
whether it overrides the model or merely bounds it. **Half a day, not an hour**,
and it should follow the hygiene batch rather than precede it.

> **Build it once.** `A2` in `DEFECT_REGISTER.md` is the same shape from the other
> end: the spoken day-name is never checked against the actual date.
> `v3_confirmed_slot_phrase` is scraped from the model's spoken text
> ([connection.py:10122](../../app/media_streams/connection.py)) and Gate 5 then
> forces every later readback to agree with it — so a wrong weekday is made
> *consistent*, not correct. One weekday↔date resolver should serve `B-09` and
> `A2` both. Building it twice is how we end up with two that disagree, which is
> the standing failure pattern of this codebase (see `DEFECT_REGISTER.md` §A4:
> one affirmative vocabulary maintained in four places).

---

## Track B — ~~blocked on an owner decision~~ — **DECIDED AND SHIPPED `8b06879`, 3 Aug**

### ⚠️ CORRECTION — the Gate 5f `?` fix (`a4c267d`) was justified on an example that does not occur

**Recorded 2026-08-04 against `CA156fa25`.** The commit and the `B-47` write-up
claimed the mandated cancel closing disabled Gate 5f, because
`_false_write_claim` stood down on any `?` and the prompts require *"Is there
anything else I can help with?"* after the cancellation claim.

**In the live pipeline that sentence never reaches Gate 5f.** Gate 5b strips it
first — `is_there_anything_else` is a banned-phrase pattern, and the call log
shows it plainly:

```
[ms_gate5] removed banned phrase (is_there_anything_else)
synthesise_chunk: "That's all done — your appointment has been cancelled."
```

**Why the analysis was wrong:** the reachability was tested by feeding raw
chunker output straight into `_false_write_claim`, instead of through
`sanitise_response`, where Gate 5b runs first. Wrong harness, so a stripped
sentence was treated as if it survived.

**What stands:** the fix itself is sound and measured — 224 guard tests, no
over-fire across three live calls. It still closes the class for any
claim-beside-a-question shape the banned-phrase list does **not** cover. Its
real-world reachability is simply much lower than claimed, and the specific
cancel-closing example was already handled upstream.

**Generalises:** when measuring whether a gate sees some text, run the text
through the whole pipeline, not the one predicate under test. See
[[obs-transcripts-are-raw-not-spoken]] for the same error in the other
direction.

---

### `B-19` / `B-07` · the filler is one-shot, so an upstream spike becomes bare silence — **FIXED `8b06879`**

> **CLOSED 2026-08-03.** Owner decision: **one re-arm at 5 s, then stop.** A
> continuing "still with you" cadence was offered and rejected — three or four
> phrases on one slow turn sounds anxious rather than attentive.
>
> Shipped as `LLM_FILLER_SECOND_DELAY_MS = 5000` plus a second sleep inside
> `_delayed_filler` ([llm_stream.py](../../app/media_streams/llm_stream.py)).
> Decision logic extracted to `_second_filler_text` so it is testable without a
> connection — the `_post_collect_readback_due` pattern from `B-46`.
> 10 regression tests; full suite 95/3468 → 95/3478, **failing sets diffed
> identical**.
>
> **The trap, recorded because the obvious fix was wrong.** The natural
> suppression check is `_ack_filler_cancelled`. It cannot be used: `_tts_loop`
> **consumes** it — the "ack filler suppressed" branch in `connection.py` resets
> it to `False` after dropping one chunk — so by the time the re-arm wakes it
> reads `False` whether or not a tool filler won, and the second phrase would
> have played **on top of** the tool filler. That is the `B-30` shape. The
> durable signal is `_ack_filler_active`, cleared by
> `filler_phrases.with_filler` and consumed by nothing.
> `test_suppression_does_not_read_the_consumed_cancelled_flag` pins it.
>
> **Not the same mechanism as `FillerGuard`**, which already plays a second clip
> at 2500 ms — that is the Acuity-availability path, gated on
> `booking_flow_active`. Anyone re-reading this row should not "fix" that one.
>
> **`B-30` is NOT closed by this.** Its sighting was an ack filler and a tool
> filler one second apart — a pre-existing race in the marker handoff, untouched
> here. Do not mark it closed on the strength of this commit.
>
> **Live verification is a log grep, not a call.** The re-arm only speaks when
> the LLM produces no token for 1.8 s and still nothing 5 s later — an upstream
> stall, which cannot be summoned by dialling (see `B-40`, where two identical
> cancel calls produced 9.9 s and 1.1 s). Proof is
> `grep "second filler phrase" render.log` over real traffic. Zero hits means the
> stall did not occur, **not** that the fix is broken. A clean-sounding test call
> earns "no regression observed" and nothing stronger.
>
> Original text follows.

`B-19` is the upstream LLM latency spike — not ours to fix. What makes it a dead
call rather than a slow one is that the filler fires **once**. A 14 s spike
therefore produces ~14 s of silence. Fix is to re-arm the filler on a timer while
a turn is genuinely in flight.

This subsumes most of `B-07` (filler inconsistency), because the inconsistency is
partly the one-shot flag being consumed by an earlier turn.

**Decision needed from the owner — how chatty:** one filler then a second at ~5 s,
or a continuing "still with you" cadence? Everything else about the fix is
determined. **Do not pick this default silently** — it is the most audible change
on the register and it is a judgement about the clinic's voice, not about code.

---

## Track C — caller-visible, prompt-side, no deterministic test

Needs dial time *between* changes; prompt edits cannot be regression-tested the
way Track A can, and two prompt changes shipped together cannot be attributed.

| ID | Defect |
|---|---|
| `B-06` | 11.8 s openers |
| `B-08` | Asks for information the caller has already given |
| `B-10` | CTA false positive |
| `B-11` | Turn-end fires on a statement |
| `B-12` | Wrong dead-air wording |
| `B-16` | `time_of_day_preference` inferred from a slot pick |

---

## Track D — verification only, needs a phone

Not defects. Code that shipped with a regression test and has never met a real
caller. These belong in the call suite.

| ID | What needs proving |
|---|---|
| `U-02` | C5 rung-3 termination — the ladder cannot loop forever |
| ~~`U-03`~~ | ~~C6 lookup-key scope — no read-back where the number is a search key~~ — **REVERSED 3 Aug by owner decision.** The lookup path now reads the number back exactly as booking does. See the 3 Aug sweep |
| `U-04` | Rung-2 verbal fallback |
| `U-05` | The `dc5c89d` bound — two unsettled answers go to the keypad |

> ~~`CALL_SUITE_2026-08-02.md` names build `7610f9a` and is eight commits
> stale~~ — **resolved by `1328f39`**, which rewrote the suite against `dc31c6c`.
> It is now two commits stale (`dc31c6c` → `1328f39` → `13dd9f3`) — but **one of
> those two is `B-25`, which changes the phone-confirm path the suite's Call 1
> turn 9 and Call 2 turn 1 criteria are aimed at.** So the suite is not merely
> behind; it is behind on the one behaviour it tests most directly. Re-read those
> two criteria against `13dd9f3` before dialling, and deploy `13dd9f3` — the
> sweep above ran on `1328f39`, i.e. **before** the `B-25` fix existed. Confirm
> the Render service/branch pairing while you are there, which per `README.md`
> correction 15 is not knowable from this repo.
>
> **Two suite criteria are known wrong and were not fixed by that rewrite** —
> Call 1 turn 9 and Call 2 turn 1, both expecting an `L1 verdict:` line the
> booking phone-confirm site does not emit. See the boxed note in the sweep
> section. Fix those before Track D is dialled or a blocker scores as a pass.

---

## Unclassified — needs a read before it can be scheduled

### `B-20` · `clinical_screening` ORPHAN — **investigated 2 Aug, re-scoped**

**It is ten, not two, and it is not a Layer 1 gap.**

`arm_paths` across 104 stored calls carrying screening state:

| Screen | trigger | orphan | other |
|---|---|---|---|
| `cauda_equina` | **11** | 3 | — |
| `dvt` | 2 | 2 | 3 arming_utterance, 1 model_asked |
| `inflammatory` | **0** | 2 | 1 model_asked |
| `vbi_neck` | **0** | 3 | — |

Ten orphans across four screens, 27 Jul → 2 Aug, **two on today's builds**
(`d0a0d8a`, `dc5c89d`).

**Layer 1 was right every time.** Replaying every orphan call's caller turns
through `match_screen_trigger` arms nothing — correctly, because the callers'
presentations did not warrant those screens:

| Screen the model asked | What the caller actually said |
|---|---|
| `cauda_equina` ×2 | *"i'd like to book for shoulder pain"* |
| `vbi_neck` ×3 | *"i'd like to book an appointment please"* |
| `dvt` ×2 | *"i'd like to book an appointment please"* / *"okay"* |
| `inflammatory` ×2 | *"book an appointment for my knee"* |

`cauda_equina` armed correctly 11 times on real back presentations, so Layer 1
demonstrably runs and works. **The defect is Layer 2 over-screening:** the model
asks red-flag questions unprompted, on presentations that don't warrant them or
on no presentation at all (6 of 10 had no complaint stated).

Root cause is prompt-side. `clinic.json`'s `how_to_use` is correct — it ties
screening to *"CALL STATE flags the active screen as SCREEN REQUIRED"* — but
[clinic_template_prompt.py:379](../../app/prompts/clinic_template_prompt.py)
renders the header as **"PROACTIVE RED-FLAG CHECKS (run BEFORE booking)"**, lists
all six questions beneath it, and closes with *"never skip it to reach a booking
faster."* The model self-serves from the catalogue and, when nothing fits, asks
the nearest one anyway. There is no knee screen; an aching knee warrants none.

**Amplifier:** [clinical_screening.py:975](../../app/media_streams/clinical_screening.py)
does not merely log the orphan — it arms the screen and **grades the answer**. A
shoulder caller's reply is then scored against DVT's twelve red-flag words —
`swollen, warm, hot, red, surgery, operation, flight, long drive, long journey,
bed rest, immobile, can't walk on it`. Negation scoping (24 Jul) covers *"no,
it's not swollen"*, but an incidental affirmative would produce a deterministic
NHS 111 escalation for a complaint that never warranted one.

> **Corrected 2 Aug, twice over.** This paragraph originally listed only the
> first four keywords, and I built `B-29` on that truncated list before opening
> the config — read the real list above, not a quotation of it. And the closing
> claim, *"no call has hit this — `red_flag=None` on all ten — so it is live
> risk, not live damage"*, **was true of the ten archived orphans and is no
> longer true.** Sweep call 2 hit it. It produced no escalation only because
> `B-31` had already switched the layer off. See `B-31`.

**Why the row said "Layer 1 gap":** the taxonomy at
[clinical_screening.py:51](../../app/media_streams/clinical_screening.py) defines
`orphan` as *"the model asked it; Layer 1 never armed. Layer 1 gap."* It imagined
two causes. The third — Layer 2 asking something it never should have — has an
identical signature and is not in the enum. **Correcting that comment is part of
the fix**, or this mis-scopes again.

#### First live sighting — sweep call 2, `CA2ada6263`, 2 Aug 21:47

Until tonight `B-20` rested entirely on obs archaeology. Here it is happening,
verbatim, four turns into a call:

```
[user     ] um yeah i'd like to book an appointment please
[assistant] Right — what's the appointment for?
[user     ] uh my left ankle just got a small bit on it i just want to get assessed
[assistant] Before we look at getting you booked in, can I quickly check — is the
            ankle swollen, warm or red compared with the other side, and have you
            had any recent surgery, illness, or a long journey sitting still?
[user     ] um i mean i had a long journey sitting still but i don't think that's
            why it's a problem i'm kind of confused just just want to get assessed
            mate i don't know why you're asking me this question
[assistant] That's reassuring — thanks for bearing with me on that, it's just a
            quick safety check we run. Do you have a preference for when you'd
            like to come in?
```

Three separate things go wrong in six lines, and they should not be conflated:

1. **The model authored a DVT screen unprompted.** No `clinical_screening` line
   exists anywhere in this call (`B-28`) — Layer 1 did not arm it. Same signature
   as the ten archived orphans, now with the arming turn present.
2. **The caller objected out loud.** *"i'm kind of confused… i don't know why
   you're asking me this question."* The cost of over-screening had been argued
   as theoretical caller friction. It is not theoretical; a caller said so, mid
   call, unprompted.
3. **The answer was a positive DVT risk factor and was called reassuring.**
   *"i had a long journey sitting still"* is textbook immobility exposure — and
   it is **in the screen's red-flag vocabulary verbatim**. Susie replied
   *"That's reassuring."* Nothing contradicted her because the deterministic
   layer never ran on that turn: `B-31`, a 200-character cap that truncated the
   question's `"?"` away and made the orphan detector return `None` silently.

> **"That's reassuring" is not the model's phrasing — it is scripted.** Found
> 3 Aug in `clinic.json`'s `clinical_screening.how_to_use`, verbatim:
>
> > *"If the answer is clearly no / none of those, reassure briefly (**'that's
> > reassuring'**) and carry on to booking as normal."*
>
> It appears again, correctly used, on `CA3264ed4b`. So the model did not
> improvise a clinical judgement and then dress it in warm language. It was
> handed the question, the verdict rule, and the reassurance wording, and its
> only error was classifying *"i had a long journey sitting still, **but i don't
> think that's why it's a problem**"* as a clearly-no — latching onto the
> caller's own hedge.
>
> This matters for the fix: the config grants the model **grading authority**,
> not just asking authority, and does it in one sentence that reads as a tone
> instruction. Whichever of A/B/C is chosen, that sentence needs the deterministic
> grader named as the authority for the *verdict*, with the scripted line as
> wording only.

**Point 3 is the serious one and it is a new class of finding.** The register has
until now treated over-screening as *asking a question that was not warranted* —
a friction and false-escalation risk. This is the inverse failure: having asked,
the system **affirmatively reassured a caller about a risk factor they had just
confirmed.** No deterministic layer participated at any point — not in asking,
not in grading, not in the reply.

> To be exact about the clinical claim, because it is easy to overstate: a long
> journey plus an ankle injury is **not** a DVT diagnosis, and nothing here says
> this caller was at risk. The defect is that the system asked a screening
> question, received an affirmative, and told the caller it was reassuring —
> without anything, anywhere, evaluating the answer. That is unsafe **as a
> mechanism**, regardless of this caller's actual risk.

> **Do not read point 3 as "the model overrode the safety layer".** Replayed
> offline, the layer would have escalated this caller to NHS 111 and blocked the
> booking — arguably itself wrong for an ankle sprain, and exactly the false
> escalation `B-20`'s amplifier paragraph predicted. Call 2 is a **compound**
> failure: `B-20` produced an unwarranted question, `B-31` suppressed the
> unwarranted escalation that would have followed, and the residue was a false
> reassurance. Fixing one without the other changes the failure, not the risk.

#### DECIDED — **B**, by the owner, 3 Aug. Shipped.

The bound is in. Two lines changed, one engine and one config, because they were
the two that contradicted each other:

| Where | Was | Now |
|---|---|---|
| [clinic_template_prompt.py:385](../../app/prompts/clinic_template_prompt.py) | `PROACTIVE RED-FLAG CHECKS (run BEFORE booking)` | `CONDITIONAL RED-FLAG CHECKS (only when the caller's complaint matches a row below)` |
| [:413](../../app/prompts/clinic_template_prompt.py) | *"match the caller's presentation to a row, then ask that screen's question"* | *"CONDITIONAL, not a checklist … ask ONLY when the complaint the caller has actually described IS the presentation named on that row. If what they have described matches no row, ask NOTHING from this list … Never reach for the nearest row … If they have not described a complaint yet, ask what the appointment is for; that is not a screen."* |
| `jv_v1/clinic.json` `how_to_use` | *"SAFETY SCREENING runs BEFORE booking."* … *"never skip it to reach a booking faster"* | *"runs BEFORE booking WHEN IT APPLIES … Most callers match no screen at all"* … *"never skip a screen that DOES apply"* |

**The safety half was deliberately preserved, not weakened.** A screen that *does*
apply still must not be skipped to book faster; a positive answer still blocks
booking; the emergency path is untouched. Those are pinned by tests that pass on
the parent.

**Engine text stays clinic-agnostic.** The tempting version of this bound spells
out *"a knee is not the inflammatory row"* — true for `jv_v1`, wrong for a clinic
that has a knee screen. Which complaint maps to which screen is config; each row
already renders its own *"when the caller describes …"* line, which is what the
new rule points at. `test_engine_text_names_no_body_part` renders a
placeholder-only clinic and asserts no anatomy survives.

**What was NOT changed, and why.** The grading authority — `how_to_use` and the
per-screen `IF clearly NO → reassure briefly ('that's reassuring')` at
[:434](../../app/prompts/clinic_template_prompt.py) — is left alone. `B-31`
already closed that harm: a deterministic escalation is spoken and the turn
`continue`s at [connection.py:7380](../../app/media_streams/connection.py), so
**the LLM never runs** and cannot contradict the grader. The model only reassures
on a turn the grader scored clean, which is correct. Changing it would have been
a prompt edit with no defect behind it.

> **Residual, noted not fixed:** `_resolve_screen_answer` can return
> `action='none'` on an *unclear* answer while leaving `pending_screen` set. The
> model then speaks and may reassure on an answer that resolved nothing. Booking
> stays blocked and the SCREEN REQUIRED steer re-drives, so the outcome is safe.
> No observed instance; not scheduled.

##### This is a prompt change — the test pins wording, not behaviour

`test_screening_is_conditional_not_a_checklist.py`, 14 cases; 8 verified failing
on the parent `8c6b30a`, and the 6 that pass there are exactly the invariants.
But **a passing suite does not show that the model stopped over-screening.**
Track C rules apply: this needs dial time. The measurement already exists — the
`ORPHAN` warning fires whenever the model screens unprompted, and `B-31` added
the `NEAR MISS` line — so the next sweep can count orphans directly and compare
against the 18/133 baseline below.

**Verification:** baseline `8c6b30a` 95 failed / 2867 passed → 95 failed / 2881
passed (2867 + exactly the 14 new). Failing set byte-identical, zero regressions,
zero accidental fixes.

**Success criterion for the next sweep:** the 8 "matched no screen" orphans
should go to zero. The 8 "same region" ones are expected to remain — B does not
address them, and call 2 is one of them.

---

#### The decision, as it was argued

**Recommendation was B.**

Recorded plainly because it has moved twice in a day and the reasoning matters
more than the answer: **B on 2 Aug (morning) → C on 2 Aug (after call 2) → back
to B on 3 Aug**, after classifying all eighteen orphan calls instead of arguing
from call 2 alone. C was a conclusion drawn from one call.

##### The evidence the options are scored against

Every jv_v1 call in obs replayed turn-by-turn through the real engine, post-`B-31`.
Eighteen calls where the model screened and Layer 1 did not. Classified by what
the caller had actually said **before** the screen was asked:

| | Calls | What happened |
|---|---|---|
| **The complaint matched no screen** | **8** | knee→`cauda_equina` ×2, shoulder→`cauda_equina` ×2, shoulder→`vbi_neck`, knee→`inflammatory` ×2, knee→`trauma_fracture`. Wrong body part or wrong pattern entirely |
| **Same region, no red-flag signal** | **8** | ankle→`dvt` ×4 (incl. call 2), shin and ankle→`trauma_fracture`, neck→`vbi_neck` ×2. The screen is *arguable* from the body part; nothing the caller said indicated it |
| **Genuine Layer 1 miss, rescued by Layer 2** | **2** | `CAcaae3aa7` and `CA3264ed4b` — see `B-32` |

So **16 of 18 are over-reach and 2 are saves.** The saves are the entire
argument, and until this morning I had them as a hypothetical.

##### The scoring

| | Option | Effect on the 18, measured |
|---|---|---|
| A | Prompt-side tightening — keep the catalogue, add stronger "only when CALL STATE says SCREEN REQUIRED" language | **Unquantifiable, and aimed at the wrong line.** `how_to_use` already says exactly this; [:384](../../app/prompts/clinic_template_prompt.py) then tells the model to "match the caller's presentation to a row, then ask". Adding words to the losing side of a contradiction |
| **B** | **The model may screen on a real presentation, never when the complaint matches no screen** | **Removes the 8 that matched no screen. Keeps both saves.** Residual is the 8 same-region asks — the least harmful kind, and the kind a physio would not blink at |
| C | **Layer 1 arms, or nothing is asked.** Remove the catalogue from the prompt | Removes all 16 over-reaches — **and both saves.** `CAcaae3aa7` gets booked for physiotherapy and massaged with a possible clot |

**Be honest about what B does not do:** it would **not** have stopped call 2. An
ankle is a defensible DVT context, so call 2 sits in the second band. B removes
half the over-screening, not all of it. That is the trade, stated plainly rather
than sold.

##### Why C is now the wrong answer

C rested on `B-24`'s withdrawal — Layer 1's triggers were checked against the
corpus and were right every time, so there appeared to be no coverage gap for C
to expose. **That finding stands and is not what `B-32` contradicts.** `B-32` is
a different failure: both saves are calls where the trigger word *was* in the
config and the transcriber destroyed it (`"calf"` → `"call's"`, `"back pain"` →
`"back pin"`). Not a vocabulary gap — transcription noise defeating exact-phrase
matching.

That distinction is the whole decision. A vocabulary gap can be closed in
`clinic.json` and then C becomes safe. **STT noise cannot be enumerated**, so
under C there is nothing left covering it. Layer 2 is not redundant
belt-and-braces here; on 2 of 18 orphan calls it was the only layer working.

##### What B actually requires, and what it does not

- **It is a prompt change, not engine work.** The line to change is
  [:384](../../app/prompts/clinic_template_prompt.py) — "match the caller's
  presentation to a row" is the grant of authority. Bounding it ("only where the
  caller's stated complaint is the presentation named in that row") is the fix.
- **Do not widen Layer 1's triggers to compensate.** `B-24` is explicit that
  widening manufactures `B-20`, and the two neck→`vbi_neck` calls in the second
  band are exactly what the compound `trigger_all_groups` was designed to
  suppress.
- **`B-32` is separate and does not block this.** See its row for why it should
  not be "fixed" by adding keywords.

**`B-31` was required under all three options and is done** (`c69eb61`). Under A
or B the orphan detector stays load-bearing and must not fail silently; under C
the orphan path *should* become unreachable, which is not a reason to leave a
silent failure in the component that would tell you it hadn't.

> **Deploy order.** `c69eb61` makes a call-2-shaped call escalate to NHS 111 and
> block the booking. Under B that call still screens (band two), so `c69eb61`
> alone shifts the failure from a false reassurance to a false escalation without
> removing the cause. **Land the B prompt change and `c69eb61` together**, then
> dial call 2's script again before anything else.

~~`B-29` is required either way.~~ Withdrawn — the grader already covers its
whole question.

### `B-24` · ~~Layer 1 coverage gap~~ — **WITHDRAWN 2 Aug, the claim was mine and it was wrong**

I asserted that `vbi_neck` and `inflammatory` having **zero** trigger arms in 104
calls was "the real Layer 1 gap". Checked against the corpus, it is correct
behaviour and there is nothing to fix:

| Vocabulary | Occurrences in 967 caller turns |
|---|---|
| VBI group 2 — dizzy / light-headed / blackout / double vision / unsteady / wobbly | **0** |
| Inflammatory — morning stiffness / stiff for an hour / both hands / several joints | **0** |
| VBI group 1 — neck / whiplash | 2 (both plain neck pain, no neuro signal) |

`vbi_neck` uses `trigger_all_groups` — neck **AND** a dizziness signal — precisely
so *"a plain neck-pain caller is not over-screened"*, which the code says in as
many words. No caller ever supplied the second group, so the compound was never
satisfiable and zero arms is the config working as designed.

The six turns containing *"stiff"* are **answers to Susie's screening question**
(*"stiffness"*, *"i think stiffness i guess yeah"*), not presentations.

> **Widening either trigger would manufacture `B-20`.** Over-screening is the
> defect we already have; loosening the triggers is how you get more of it. Do
> not "fix" this row.

**Consequence for `B-23`:** the F6 sequencing argument — *wire the extractor only
after Layer 1 coverage is fixed* — loses its basis. There is no coverage work.
See `PLAN_REASON_CAPTURE.md` §7.

> **`B-32` does not reopen this row — read both before citing either.** This
> withdrawal says Layer 1's *vocabulary* is right: the words callers used were
> matched, and the words that were absent were absent from the corpus too.
> `B-32` says something different — that on two calls the right word **was** in
> the config and the transcriber mangled it. Vocabulary coverage and transcription
> robustness are separate properties and only the first was ever tested here.
> The instruction above is unchanged: **do not widen these triggers.** `B-32`'s
> one config suggestion (`calves`, an irregular plural of a keyword already
> present) is an addition of an existing concept's inflection, not a widening of
> what the screen is for.

### Evidence caveat — stored transcripts are incomplete for screening analysis
Of the 13 calls that armed a screen via `trigger`, only **2** contain the arming
utterance in the stored transcript. In several the first stored caller turn is
already an answer to the screening question (*"no i don't"*, *"no none of those"*,
*"no not at all"*), i.e. the opening turns are missing.

Any future "replay the corpus" analysis of screening has this blind spot. It does
not affect the `B-20` finding — that rests on the orphan calls, whose openings
**are** present — but it does mean arming-rate statistics from obs are a floor,
not a measurement.

---

## Folded in 3 Aug — six live defects that were in no register

**These were not new findings.** All six were already written down — in
`THEOREM_PORT_PLAN.md`, `FIX_QUEUE_PRE_DEMO.md`, a 25 Jul sweep note and the
clinical-campaign status — and **none of them was in this file or in
`DEFECT_REGISTER.md`.** They surfaced only because the question *"what is open
that is not SMS or Sheets?"* was asked directly and every plan document was read
end to end.

> **The lesson is about coverage, not accuracy.** The 3 Aug reconciliation pass
> made this register internally correct — every heading carries a SHA — while
> leaving it **incomplete**, which is the more dangerous of the two failures: an
> internally consistent register invites you to trust it as the whole list.
> `B-46` is an open P1 affecting both live clinics and it existed as one line in
> an **untracked** plan file. Per `CLAUDE.md`, git history searches do not find
> those. **Before treating this register as exhaustive, `ls docs/plan/` and read
> what is there.**

### `B-46` · the booking readback fires before any slot is offered — **FIXED `80b545b`**

Was Item 0 of `THEOREM_PORT_PLAN.md`. **The highest-severity item in this
section, and `main` already fixed it.**

The post-collect guard on `check_availability`
([llm_stream.py:2722](../../app/media_streams/llm_stream.py)) reads:

```python
_col = session.get("collected") or {}
if (
    tool_name == "check_availability"
    and _col.get("phone")                                   # <- always truthy
    and (_col.get("name") or _col.get("full_name"))
    and not _caller_requests_new_day_or_time(messages or [])
):
```

**`collected["phone"]` is pre-loaded from the Twilio caller-ID at connect** —
[connection.py:6456](../../app/media_streams/connection.py), whose own comment
says *"Populate collected.phone from Twilio caller-ID so Susie never asks for
it."* It is set unconditionally on every inbound call that carries a number, so
that arm is **true from turn one** and the guard collapses to *"a name has been
collected"*.

Under name-first the first name is stored at turn 1, so the guard fires **before
any slot has been offered**, blocks `check_availability`, and forces a booking
readback for a slot that does not exist — skipping the surname and
phone-confirmation steps entirely.

`main` fixed it by gating on `session["phone_confirmed"]` instead
(`main:1749-1755` comment, `main:1786-1793` gate). That flag is set **only** where
the caller actively confirms a number — the keypad commit
([connection.py:6194](../../app/media_streams/connection.py)), the booking verbal
confirm (`:7215`), and the DTMF-active verbal confirm (`:7389`) — and
`book_appointment`'s A1 gate already requires it, so a booking cannot complete
without it. **The guard's original purpose is therefore fully preserved**: it
still blocks a re-check once details are settled, because "settled" is exactly
when `phone_confirmed` becomes true.

> **Two things must survive the port and they are `latency-eval`-only.** The
> `_caller_requests_new_day_or_time` escape (added 2026-07-30 after a caller
> asked for Wednesday seven times, was re-read Tuesday, and hung up unbooked) and
> the BUG-14 name/location injection. `main` has neither. Swap the *condition*,
> not the block.

#### Fixed `80b545b` — and the plan's line numbers were stale, as usual

`llm_stream.py:2604-2612` in the port plan pointed at the Gate 5 fallback, not at
this guard. Found by symbol instead. **Treat every `llm_stream.py:NNNN` in the
plan documents as approximate** — the same rot already measured at +36 to +88 in
`connection.py`.

**Extracted to `_post_collect_readback_due` rather than edited in place.** The
condition sat inline in `_execute_tools`, reachable only by standing up a whole
connection — and a guard that cannot be reached from a test is one whose tests
pass when it is deleted. That is the coverage hole already recorded against the
lookup keypad branch, and the reason `B-38` extracted `_cta_asked`. The call site
is source-pinned to route through the predicate, and the predicate is
source-pinned against reading `collected["phone"]` again, because the whole
defect was one dictionary key.

> **Fails-before was verified by running the OLD condition against the same
> session shapes, not by checking out the parent.** The predicate does not exist
> on the parent, so the tests would have errored on import — which proves the
> import works, not that the defect was real. All four pre-confirmation shapes
> return `True` on the old condition and `False` on the new; the invariant
> (confirmed phone + name) returns `True` on both.

36 tests, `tests/regression/test_b46_readback_waits_for_confirmed_phone.py`.
Full suite 95 failed / 3411 passed against a baseline of 95 / 3375 — 3375 plus
exactly the 36 new — failing node IDs diffed, identical.

**Not yet verified on a live call**, and this one *is* dialable: place a booking
call, give a first name, and confirm slots are offered before any read-back.
It joins the seven other fixes carrying the same debt.

### `B-47` · a phone number that isn't a phone number — **CLOSED 3 Aug, anchored against the code**

> **Closed. Not by one guard — by four, and the owner was right that it had
> already been fixed.** The row's "anchor before scheduling" instruction was
> followed; this is what it found.
>
> **All three live clinics run free-form** (`jv_v1` and `vital_edge` are
> `prompt_engine: template_v1`, `theorem_v3` by literal id), so **every
> `flow.py` phone site is dead code on the live path.** That collapses 13
> `phone_confirmed` sites to three.
>
> `book_appointment` **hard-blocks** unless `phone_confirmed is True` — the A1
> gate at [receptionist_tools.py:4771](../../app/tools/receptionist_tools.py).
> The only three sites that set it:
>
> | Site | Number's origin | Validation |
> |---|---|---|
> | `connection.py:6194` | keypad | `_is_valid_uk_mobile` at `:6185` |
> | `connection.py:7215` | **caller ID** on a verbal yes | machine-captured |
> | `connection.py:7389` | **caller ID** on a verbal yes | machine-captured |
>
> Then the A3 gate (`:4803`, `_reconcile_booking_phone`) overwrites any
> model-authored `phone` argument disagreeing with the confirmed number.
>
> **There is no live path by which a spoken, STT-transcribed number reaches a
> booking.** The "verbal confirm" sites confirm the *caller ID*, not a spoken
> number. A booked number is either typed (format-validated) or the caller's own
> line. All four mechanisms postdate the 25 Jul sightings.
>
> **Corpus corroboration.** Instance 1 is in obs: `CAcd8b36e198aa` (25 Jul)
> stored `7009001230` — and stored it **raw**, with no leading zero, so it never
> passed through `_normalise_keypad_number` at all. `CA3590527bc7c4` (1 Aug)
> stored `07987124700`, the fabricated number named in
> `_reconcile_booking_phone`'s own docstring. Both sit **before** their fixes and
> nothing of either shape appears after.
>
> ⚠️ **What a length check can NOT catch, recorded so nobody re-derives it.**
> `_is_valid_uk_mobile` is `^07\d{9}$`, and `_normalise_keypad_number` pads a
> 10-digit buffer starting `7`. So the mangled `7009001230` normalises to
> `07009001230` — eleven digits, valid by the regex, **and a different person's
> number.** The padding rule that repairs a genuine dropped zero also launders a
> shifted transcription into something well-formed. That class is only catchable
> by a read-back, never by a format check. The helper's own docstring already
> refuses to pad a 10-digit buffer starting `0` for this reason; the `7` case has
> the same hazard and is kept because a dropped leading zero is the commoner
> input. **Do not "tighten" this into a length check and call the class closed.**
>
> **Two things the anchor turned up — both new, neither part of `B-47`:**
>
> **(a) `_fast_path_phone_confirmed` is write-only.**
> [fast_path.py:311](../../app/fast_path.py) sets it when the caller accepts the
> caller-ID number; it is declared in `config.py`, `session.py` and
> `redis_store.py` and **read by nothing**. So that confirmation does not set
> `phone_confirmed`, A1 blocks the booking, and Susie asks for the number again.
> Friction, not a wrong number — and a plausible contributor to `B-08` ("asks for
> information the caller has already given"). **Mechanism anchored; reachability
> of that branch NOT established.** Lead, not a finding.
>
> **(b) A lookup could hand the A3 gate the wrong number — HARDENED `2a146dd`.**
> `_exec_lookup_recent_appointment` wrote the provider's number over
> `collected["phone"]` unconditionally — the very field the A3 gate uses as its
> reference. A caller who confirmed a new number and then hit the lookup would
> have had their own number read as the mismatch and the booking "corrected" to
> the stale one.
>
> **Reachability measured, not assumed: 0 of 155 obs calls.** Ten stored a
> caller-supplied number, four ran the returning-patient path, **none did both**.
> The conversational order is naturally safe (identity before number), so
> reaching it needs a same-call reschedule after a keypad entry. Fixed anyway
> because it is one branch and a test; recorded as **hardening, not a reproduced
> defect**.
>
> **Theorem-only by construction** — the function returns early for any other
> clinic (`:6148`), so it cannot touch `jv_v1` or `vital_edge`, and it matters
> for **the clinic being onboarded**. Six regression tests; the three asserting
> the guard fail before it, the three pinning existing behaviour pass before, so
> nothing fails for the wrong reason. Full suite 95/3484, failing set diffed
> identical.
>
> Original text follows.

Was `A6` in `FIX_QUEUE_PRE_DEMO.md`, opened 25 Jul, never carried forward.
**Three observed instances, two of them booked:**

| Observed | Result |
|---|---|
| `"07700 900123"` | stored as `7009001230` |
| `01392255` — eight digits | **booked, no readback** (F-024) |
| a complete 11-digit DTMF entry | **discarded, and a different number booked** (F-020) |

This is `CLAUDE.md` §6.1's worst class: the call sounds perfect, the booking
exists, and the patient is uncontactable. Low-probability on a rehearsed demo
(the caller accepts the caller-ID number); near-certain across a 230-clinic
cohort.

**Partly overtaken by later work and that must be checked before scheduling.**
`_is_valid_uk_mobile` and `_normalise_keypad_number` now guard the keypad commit
([connection.py:6185](../../app/media_streams/connection.py)), which may already
close the F-020 shape. The **verbal** path and the tool boundary are the
unverified halves. Anchor before scheduling.

### `B-48` · a full urgent-care escalation, retracted in the same breath

Call `…03ddb215` (C3b, 25 Jul sweep). Susie delivered the complete urgent-care
escalation and then **reversed herself mid-turn**:

> *"Wait — I can see from our conversation that you've already confirmed the calf
> isn't swollen or warm, so that's reassuring"*

…then pivoted to a booking offer. **Telling a caller to go to A&E and retracting
it in the same breath is worse than never escalating**, and worse than the
"cleared correctly" grade the sweep gave the neighbouring calls.

Never opened as a row because the sweep write-up's headline finding was a
*different* call (C3c) scored as a safety FAIL — and that score was wrong, the
transcript shows a correct DVT escalation. **The real defect was in the call
nobody re-read.**

Not yet anchored to code. The mechanism is unknown; it may be the same
self-narration family as `B-41`/`B-43`, or a genuine state read mid-turn. **A
lead with a call SID, not a finding** — pull `…03ddb215` from obs and read the
turn before scheduling.

### `B-49` · `vbi_neck` can never arm — structural

From the clinical campaign (F-017, cause 2). `vbi_neck` uses `trigger_all_groups`
— neck **AND** a dizziness signal — and the matcher requires both groups in
**one utterance**. In real calls the neck complaint and the dizziness arrive in
**different turns**, so the compound is never satisfied and the screen has
**zero trigger arms across the whole corpus**.

> **This does not contradict `B-24`'s withdrawal — read both.** `B-24` says the
> vocabulary is right and that zero arms is correct *because no caller ever
> supplied the second group*. `B-49` says that even a caller who supplies both
> would not arm it if they say them a turn apart. Vocabulary and accumulation are
> separate properties and only the first was tested.
>
> **And the standing instruction still holds: do not widen the triggers.**
> Fixing this means accumulating groups across turns within a screening window,
> not loosening what matches. Widening is how `B-20` is manufactured.

### `B-50` · the wrong service is booked — the semantic variant — **MEASURED 3 Aug, DOWNGRADED to a watch item**

> **The measurement this row asked for has been taken, and it does not
> reproduce.** Across **all 155 obs calls**, `collected.checked_service` took
> exactly three values — `msk_initial_assessment` (67), `sports_massage` (2),
> `msk_treatment_session` (1). **All three resolve in `clinic.json`. Zero
> informal strings** — no "msk treatment", no "neuro", no "massage".
>
> **The trap that was checked before believing it:** `collected["service"]` is
> written from the raw arg
> ([receptionist_tools.py:4525](../../app/tools/receptionist_tools.py), `:4996`,
> `:5124`), so an unresolvable string *would* surface — but the reconcile at
> `:4889` rewrites book-service to `_checked_service` first, so absence at the
> **book** end proves nothing. `checked_service` is the clean measurement, and
> it is clean.
>
> **Do not build the bounded fuzzy resolver.** There is no measured failure to
> size it against, and this row's own instruction was "do not build a
> booking-path fix blind."
>
> **Cannot reach Theorem regardless** — `theorem`/`theorem_v2`/`theorem_v3` hit a
> hard whitelist at
> [receptionist_tools.py:3877-3880](../../app/tools/receptionist_tools.py)
> (`_raw_service not in _VALID_SERVICES` → reject) before any of this.
>
> **What is NOT settled:** the four clinical-campaign calls (4/7/11/14) that
> produced F-021 are not necessarily in this store. Absence here is absence in
> the obs corpus, not proof the shape never existed. Re-open on a fresh sighting,
> with the `service` argument attached.

Was F-021. Four of four in the clinical campaign. The `book`→`check` bind at
`_exec_book_appointment` already forces them to agree, so **every remaining
instance is the model picking the wrong service at `check_availability` and the
guard faithfully booking it.** There is no disagreement for a tool-boundary guard
to catch.

Splits into two sub-cases, neither fixed by binding:

1. the model passes an **informal** service string (*"msk treatment"*, *"neuro"*,
   *"massage"*) that `_find_service_def` cannot resolve — needs a bounded,
   measured fuzzy resolver at the tool boundary;
2. the model picks the wrong service **consistently** across check and book —
   needs caller-intent capture.

**Do not build a booking-path fix blind.** Get the actual `service` arguments
passed on calls 4/7/11/14 first.

### `B-51` · `cauda_equina` falsely arms on "behind my back"

Was F-029, P2. A shoulder complaint phrased *"behind my back"* arms the cauda
screen via the `my back` keyword. Precision problem — the **opposite** direction
to `B-49` and to F-032's lay-phrasing fix, which deliberately widened this
screen's vocabulary.

The engine has **no negative-keyword support**, so this cannot be fixed in
`clinic.json` today. Needs its own commit and its own measurement, and it pulls
against F-032 — widening cauda's vocabulary is what made this reachable.

### `B-52` · *"by the way"* was written to the calendar as the surname **"Way"** — **FIXED `dc974f6`**

**Found by the first call placed after eight test-only fixes**, `CAb215dec5`,
3 Aug, build `9032ee804f19`:

```
16:58:00  FINAL 'um my name is quentin by the way'
16:58:02  [ms_conn v3] name persisted (normal path): 'Quentin Way'
16:58:02  v3_phone_dtmf_active = True (name confirmed — phone collection phase)
16:59:00  book_appointment patient_name: "Quentin Way"   -> ut7p0a17j71f…
```

**One fault, three symptoms — and the two downstream ones are the whole reason
the call felt muddled:**

| Symptom | Cause |
|---|---|
| The caller was **never asked their family name** | `Quentin Way` is a complete first+surname, so the surname step had nothing left to ask for |
| The **phone was asked at a strange point** — before the reason was finished, before any slot | `name confirmed` armed the phone phase at turn 2, one log line later |
| A real calendar event under a name never given | the write carried `patient_name: "Quentin Way"` |

> **NOT a regression, and this was checked rather than assumed.** Reproduced
> identically at `a1ef3dc` (12 Jul), `7dfc0c2` (18 Jul), `aa0b3bd` (31 Jul — the
> last change to that file), `1fe8f7f` (2 Aug) and **`3af4bd8` (3 Aug 12:10, the
> last commit verified on a live call)**. Three weeks old. Nobody had said *"by
> the way"* to Susie before — **the call sheet's own fallback line supplied it.**

#### Two independent faults — fixing either alone leaves a live hole

1. **The preposition class was half-present.** `SURNAME_STOPWORDS` held `on`,
   `with`, `for`, `from`, `to`, `of`, `as` — but not `by`, `at`, `in`. The same
   accident the file already records for *"one present / six absent"*. This is
   what produced **`'By'`** on the long-tail branch.
2. **`_walk_particles_back` dropped leading tokens without checking them.**
   Dropping is legitimate only for a **middle name** (`["james","rock"]` →
   `"rock"`). Given `["by","the","way"]` it dropped `"by the"` and kept `"way"`
   — and **`way` passes `ok()` on its own merits**: not a stopword, not a
   contraction, not a false positive. **No word list reaches it.** The signal is
   the company it keeps — `the` sits between the first name and the candidate,
   and a real surname group never contains an interior stopword.

**Deliberately not fixed by adding `way` to a list.** That is the §A4 pattern —
an open-ended set of English words patched one live call at a time, already done
four times for the phone affirmatives.

**Wider than the call showed.** Measured against the pre-fix module:

| Utterance | Before | After |
|---|---|---|
| *"um my name is quentin by the way"* | `'Way'` | `''` |
| *"…by the way i've hurt my knee"* | `'By'` | `''` |
| *"my name is quentin at the moment"* | `'Moment'` | `''` |
| *"i'm sarah in a rush"* | `'In'` | `''` |

**Not one control moved:** `Roch`, `De Silva`, `Van Der Berg`, `Bin Ahmed`,
`O'Brien`, `Smith-Jones`, the dropped middle name, and trailing
*"please"* / *"thanks"* are all unchanged. **Over-rejecting is the failure
direction that matters here** — it sends the caller back round the surname loop,
which `B-15` recorded being asked twice on a live call. Eleven controls pin it.

29 tests. Suite 95 failed / 3440 passed against 95 / 3411 — failing node IDs
diffed, identical; the 36 `NameCollector` reds did not move.

> **What this row is really evidence for.** Eight fixes shipped on 3 Aug with a
> green diff and no dial. The first call found a three-week-old defect that
> broke the flow end to end, and none of the eight was implicated. **A diffed
> failing set proves you broke nothing. It does not prove the call works.**
> `B-52` is the cost of that distinction, and the reason the remaining fixes are
> now blocked on dial time rather than on more code.

**`A3` is the fix that generalises.** A surname read-back would have caught
`Way` on this call, and catches the next mangling too — the register already has
four on one caller's name. It remains an owner decision because the prompt
forbids the read-back at two sites.

---

## Gaps in this register

Recorded honestly rather than filled in:

- **`B-05` has no entry.** It was not carried in the 2 Aug update and I do not
  know what it was. Either it exists and is lost, or the numbering skipped. Do
  not reuse the ID until that is settled.
- **`U-01` has no entry.** Same. The `U` series as carried runs `U-02`–`U-06`.
- ~~`B-15` and `B-17` have no file:line anchor~~ — **both anchored 2 Aug**, and
  both turned out to be different from their one-line descriptions. `B-15` is not
  a mislabel at any of the three call sites but a resolver that lets a sticky flag
  outrank the live question, with one caller-audible consequence. `B-17` is not a
  no-op log but eleven unchecked return values that, on the two live clinic
  branches, report a failed booking SMS as sent.
  **Worth noting as a pattern:** a one-line defect description carried across
  sessions had, in both cases, the wrong scope. Anchor before scheduling.

- **Line-number rot is now measured, not suspected.** Re-anchoring the `B-15`
  section at `13dd9f3` found **eleven** stale references. The drift is **not a
  constant** — it ranges +36 to +88 (`:1826`→`:1869` is +43, `:13500`→`:13588`
  is +88, `latency_timing.py:74`→`:110` is +36) — so you cannot correct these by
  applying an offset; each must be re-grepped by symbol. `connection.py` grows
  under every fix, including my own `13dd9f3`, which moved the `:7094` site cited
  in `B-25`'s own root-cause paragraph. Treat any `connection.py:NNNN` written
  before 2 Aug as approximate.
- **Three ways of getting a screening claim wrong, all mine, all in 24 hours.**
  `B-24`: asserted a coverage gap from *absence of arms*, without checking whether
  the vocabulary ever appeared — wrong, withdrawn. `B-29`: asserted a grader gap
  from a keyword list *quoted in this file* rather than from `clinic.json` —
  wrong, withdrawn. `B-32` (scoping): tried to size a trigger gap with seventeen
  phrasings **I invented and judged should arm** — discarded before it reached a
  row. The pattern is one thing: **screening claims must come from observed
  caller speech with a call SID, and any judgement about what *ought* to trigger
  is clinical, not engineering.** `B-32` as written contains only the two cases
  that actually happened.
- **`B-29` is the register quoting itself.** I opened it, and withdrew it four
  hours later, on the strength of a four-keyword list that appeared inside
  `B-20`'s prose. The real list in `clinic.json` has twelve entries and contains
  the exact phrase I said was missing. Nothing in this file is a source; it is
  all commentary on sources. **Read the config, the code, or the transcript —
  never a row in here — before opening a row.** This is a different failure from
  the anchoring one above: those rows were unanchored, this one was anchored to
  the wrong artefact.
- **The night's most serious finding came from a replay, not from a call.**
  `B-31` was invisible in the logs by construction, invisible in obs (no
  tool-call trace), and invisible in the transcript. It surfaced only by running
  `match_asked_screen` and `update_screening_state` offline against the stored
  turns. **Replay the pure predicates against obs before concluding a
  deterministic layer "did nothing"** — "no log line" and "did not run" and "ran
  and found nothing" are three different things, and the logs distinguish none
  of them.
- **One residual left deliberately in code, not fixed here.** The `capture_phase`
  docstring at
  [latency_timing.py:125](../../app/media_streams/latency_timing.py) cites
  `connection.py:1790` for the sticky-flag comment; the real line is `1833`. It
  is comment-only and this pass was docs-only by design — fold it into the next
  commit that touches that file rather than spending a commit on it.

If you find the source these came from, fold it in here and delete this section.

---

## Rules for this file

1. **The code wins.** If this file and the tree disagree, fix the file and note
   it. These plan documents have been wrong fifteen times; the count is in
   `README.md`.
2. **A row without an anchor is a lead, not a finding.** Say which it is.
3. **Closing a row requires a commit SHA and a test path**, both named in the row.
4. **`U-nn` rows close on a call, not on a test.** A green test is what put them
   in the `U` series in the first place.
