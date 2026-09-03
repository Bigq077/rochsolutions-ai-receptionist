# Overnight handover — 3 Sep 2026, 02:20 to 03:40

**Read this first, then decide whether to promote.**

`production` = `eb6c8e4e` (pushed 02:20, verified by a live call).
`latency-eval` = **five commits ahead**, all built while you slept, none heard
on a call. **Nothing has been promoted since you went to bed.**

**Revert target for the LAST promotion: `e022ea8c`.**
If you promote this batch, the revert target becomes **`eb6c8e4e`** — write it
down before pushing.

---

## 1. What is waiting on `latency-eval`

| commit | what | risk |
|---|---|---|
| `e01cbdbf` | transfer no longer falls back to a private mobile | **behaviour change on every clinic** |
| `63a3f464` | a body part answering "what's it for?" gets a head | caller-audible |
| `bece1bc4` | the apology is not said twice | caller-audible |
| *(test)* | re-aims the northgate transfer-phone decision | none |
| `+1` | service no longer calls itself "Theorem Health" | cosmetic |

**Suite: 98 failed / 7,910 passed. Failing set byte-identical** to a same-day
baseline at `eb6c8e4e` taken in a separate worktree with `.env` copied in.
Every fix is red-then-green proven by neutering it, not assumed.

---

## 2. The one that needs your judgement before it ships

### `e01cbdbf` — transfer fallback

`TRANSFER_FALLBACK_NUMBER` defaulted to **`+447502211207`**, your own handset,
directly beneath a comment saying the env var existed "to avoid hardcoding a
real UK number in source code". That default answered for:

* any Render service missing the env var;
* any clinic whose config forgot `transfer_phone`;
* any Twilio number missing from `TWILIO_TO_CLINIC` — an unmapped number falls
  through to the `demo` clinic, which has no `transfer_phone` at all.

A patient asking for a human got your phone. `resolve_transfer_target` logged it
as a warning nobody read.

**The default is now empty.** That is a controlled outcome, not a broken one:
`_handle_transfer` logs `transfer ABORTED` and declines to redirect, so the
caller keeps talking to Susie. `<Dial></Dial>` would drop them mid-call.

**What you should check:** all four live clinics resolve their own target
through `get_clinic`, so nothing should change for them —

```
northgate  +447502211207   (added to its clinic.json by this commit)
jv_v1      +447478558845
vital_edge +447545862307
theorem    +447870166861
```

⚠️ **If any Render service sets `TRANSFER_FALLBACK_NUMBER` and relies on it for
a clinic not in that list, that clinic's transfers now abort.** I cannot see
Render env vars from the repo. This is the one thing worth eyeballing in the
dashboard before you promote.

### The test this broke, and why it is re-aimed rather than deleted

`test_northgate_carries_no_transfer_phone` asserted the opposite. It is a
**decision** from 2026-08-29, not a stale fixture — and its own failure message
set the terms for reversing it:

> "northgate has a transfer_phone again — that is fine now, but check the
> ForwardedFrom guard is still in place before shipping it"

Checked, both directions, and now asserted in the test rather than trusted:

```
direct dial (ForwardedFrom empty) -> caller ID KEPT      (was the 29 Aug defect)
genuine diversion                 -> still suppressed
```

The 29 Aug defect was that the demo line's transfer target *is* the handset that
rings it, so every test call had its caller ID blanked — "I can't see a phone
number on this call", then fifteen seconds of DTMF. That cannot recur while
`_suppress_forwarded_caller_id` requires `ForwardedFrom` as positive evidence.

---

## 3. The two caller-audible ones

### `63a3f464` — "just my left ankle" got an apology instead of sympathy

Live 01:28:16: `'um just my left ankle nothing serious'` → `ttfa_ms=3642
content_ttfa_ms=3642` (equal, so nothing spoke) → `UNKNOWN_SLOW`'s "Still with
you —", which apologises for a wait instead of acknowledging what the caller
just said about their body.

`Intent.SYMPTOM` triggered on `_HURT` and corroborated with `_BODY`, and that
utterance has "ankle" and no word for pain. **The comment four lines above
`_HURT` already diagnosed it** — *"adding more synonyms is the trap, the SHAPE
of the matcher is the bug"* — so this adds no synonyms. A body part named **in
answer to the reason question** is a complaint, pain word or not.

Read from Susie's own previous turn, never inferred from the answer, so it
cannot fire on a body part mentioned anywhere else in the call.

Also gave the reason-question matcher **one owner**:
`llm_stream._note_reason_question_asked` carried the pattern list inline and now
calls `hold_speech.question_asks_the_reason`. Two copies would be two answers,
and that family has already been wrong twice for listing the literals seen so
far (B-36; CAea8abdb on 2 Sep). A test pins that it was not copied back.

**Listen for:** "Sorry to hear that —" when you answer "what's it for?" with a
body part and no pain word.

### `bece1bc4` — the apology said twice

Two of three symptom calls last night:

```
01:56:05  head:  'Sorry to hear that —'
01:56:08  model: "I'm sorry to hear that — shoulder pain that's really…"
```

`join_after_head` has stripped duplicate openers since the 95 stored duplicates
it was built from, but every pattern in `_INTERIM_DUPE_RE` is a *lookup* phrase.
The symptom head is a different family.

**Conditional on the head, and that is the whole safety of it.** Stripping an
apology wherever one appeared would delete the *first* apology on a turn whose
head was a lookup phrase. That case has its own test and is the one to keep if
these ever have to be relaxed.

Not every "sorry" is sympathy either: "Sorry about that, could you say that
again?" apologises for **our** failure and is preserved. Hence two branches —
"sorry TO HEAR" may end on any punctuation; a bare "sorry" only counts on a dash
or ellipsis, because a comma there introduces a request.

---

## 4. Findings recorded, NOT fixed

### B-31 is mis-diagnosed, and I did not touch it

**There is no code that truncates `last_bot_prompt`.** `_LAST_BOT_PROMPT_CAP =
200` exists only as a reader-side assumption in `clinical_screening.py`. What
actually happens is that `last_bot_prompt` holds one **chunk** and the question
is in a different chunk — exactly what last night's logs show:

```
bot='to nine in the morning, or ten past five'   question='Any of those work?'
```

The fallback behaviour is correct; the explanation is wrong. Fifth instance of
[[guard-comments-assert-false-premises]]. **I left it alone deliberately** — it
is a P3 that recovers correctly every time, it sits on the clinical screening
path, and a behaviour change there is not something to make unattended.

### A second private-number default

`app/flows/triage_legacy.py:3826` defaults `THEOREM_NOTIFICATION_SMS` to Mark's
own staff number, on the legacy `/twilio` path. Same class, lower severity — it
texts that clinic's own staff rather than sending a patient to a stranger — and
emptying it could silently stop those alerts if the env var is unset in Render.
The new grep test exempts it explicitly with that reasoning. The other two
readers of that var already default to nothing, which is the shape it should
end up with.

### Still open from last night

* **A pure DAY-pick produces no pick signal.** "yeah monday works" still gets
  silence. Item 3b in `SLOT_PRESENTATION_CONVERGENCE.md`; the trap is that
  "what about monday" names one offered day too and is a *request*.
* **The false completeness claim** — *"the slots I have that day are…"* naming
  2 of Monday's 12 times. Third live instance, 02:07:40.
* **Slot readouts still run 17s.**
* **Theorem's Acuity booking path has still been exercised by nothing.**

---

## 5. Suggested order when you wake

1. **Check `TRANSFER_FALLBACK_NUMBER` in the Render dashboard** (§2). It is the
   only thing here I could not verify from the repo.
2. **One demo call**, `+44 7366 263180`. Say *"I'd like to book an appointment"*,
   wait to be asked what it is for, then answer **"just my left ankle"** — no
   pain word. You should hear *"Sorry to hear that —"* and **not** hear the
   apology twice.
3. If that is clean, promote: `eb6c8e4e` → head of `latency-eval`, recording
   `eb6c8e4e` as the revert target, then one Vital Edge call after.
4. Then item 3b, which is the highest-value thing still open.

**If you would rather not ship §2 today**, `63a3f464` and `bece1bc4` are
independent of it — cherry-pick those two and leave the transfer change on
`latency-eval` until you have checked Render.
