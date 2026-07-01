# Working on Susie — Test, Log & Fix Playbook

> For Jules. This is *how we make progress on this system without breaking it.*
> The single most important rule: **this codebase is powerful but highly interconnected
> (`flow.py` alone is ~25k lines). Small careless changes cause regressions.** We move
> slow and precise — one verified change at a time. "Fix everything fast" is how you go
> backwards.

---

## 1. The loop — every issue follows these 7 steps

**1. Reproduce.** Make the call from the call sheet and observe carefully. Note exactly:
which call/scenario, which step in the conversation, and **what Susie said vs. what she
should have said**. A vague symptom ("booking is weird") is useless; a precise one
("on call 3, after I gave my name she re-asked for it instead of confirming the phone
number") is fixable.

**2. Capture the Render logs.** While the issue is fresh, pull the server-side log for that
exact call (see §5). Every call has a **Call SID** — use it to grab just that call's lines.

**3. Hand it to Claude Code — and ask for a diagnosis, NOT a fix.** Paste three things:
(a) the call scenario, (b) expected vs. actual behaviour, (c) the Render log. Then ask Claude
to find the **root cause** and the **smallest change** — before it edits anything.

**4. Agree the change.** Claude should name the **exact file + function** and the minimal edit.
If it proposes a big refactor, renames things, or touches unrelated code — stop it. That's a
regression waiting to happen.

**5. Make one change.** Scoped to this single issue. Nothing else.

**6. Verify — twice.** Run the relevant test (`pytest` for units, or
`python tests/auto/run_tests.py` for call scenarios), **then re-make the call**. Confirm the
fix *and* that the rest of the suite still passes. A fix that breaks another flow is not a fix.

**7. Commit small.** One logical fix per commit, with a clear message. Small commits make
regressions easy to spot, bisect, and undo.

---

## 2. How to talk to Claude Code (the prompting method)

**Set the frame at the start of every session.** This is the standing instruction — paste it first:

> *"You are a professional software engineer working on a live, regression-sensitive
> codebase. Your goal is steady, verifiable progress — not speed. Make the smallest correct
> change, explain the root cause before editing, and never modify code I didn't ask you to."*

**The rules (these are the lessons, learned the hard way):**

- **Diagnose before fixing.** Always ask *"what's the root cause and the smallest change?"*
  before *"fix it."* Most bad edits come from fixing the symptom, not the cause.
- **One change at a time.** Never say "fix all of these." Change → test → confirm → next.
  Batching changes is the #1 source of regressions in this repo.
- **Be precise and rigid.** Give exact terminology, the exact flow step, expected vs. actual,
  and the log. Vague prompts produce vague, risky edits.
- **Demand blast-radius awareness.** `flow.py` and `connection.py` are giant and interlinked.
  Ask: *"what else could this change affect, and which tests cover it?"*
- **No silent rewrites.** Tell it explicitly: *don't refactor, rename, reformat, or "improve"
  anything outside the fix.*
- **Verify its claims.** Never trust "✅ fixed." Re-run the test and the call yourself. Claude
  can be confidently wrong — treat every claim as unverified until a test or a real call proves it.

---

## 3. Reusable prompt templates

**Diagnosis (use first, every time):**
> "Here is a call scenario, the expected vs. actual behaviour, and the Render log below.
> **Do NOT change any code yet.** Identify the root cause, the exact file and function
> responsible, and the smallest change that would fix it. Also list anything else that change
> could affect, and which tests cover it.
> \n[scenario] … [expected vs actual] … [paste Render log] …"

**Fix (only after you've agreed the diagnosis):**
> "Make ONLY that change. Do not touch unrelated code, do not refactor or rename anything.
> Show me the diff and explain in one line why it's the minimal fix."

**Verify (always):**
> "Which existing tests cover this area? Run them. Then list exactly what I should re-test by
> phone to confirm nothing regressed."

---

## 4. Golden rules — print these above the desk

1. **Reproduce** before you fix.
2. **Diagnose** before you edit.
3. **One change, one commit.**
4. **Always run the tests after** — and re-make the call.
5. **Assume every change can regress something** — ask what, and check it.
6. **Slow and precise beats fast and broad. Every single time.**

---

## 5. Getting the logs from Render

1. Log in to the **Render** dashboard → open the **`rochsolutions-ai-receptionist`** service.
2. Open the **Logs** tab.
3. Find the call by **time** (when you made it) or search its **Call SID** (it appears in the
   log lines for that call).
4. Select and **copy the full block of lines for that one call** — from the call starting
   through to it ending. That whole block is what you paste into Claude Code.
5. Errors and stack traces also surface here (and in Sentry if enabled) — include them.

> Tip: make one call, immediately grab its log, fix, verify, then move to the next call.
> Don't batch five calls and try to untangle the logs afterwards.



## 6. Where the call sheets & tests live

- **Call scripts:** `CALL_TEST_SCRIPT.md`, `JV_V1_TEST_CALL_SCRIPT.md`
- **Per-clinic regression suites:** `JV_V1_8CALL_TEST_SUITE.md`, `VITALEDGE_8CALL_TEST_SUITE.md`
- **Automated scenario runner:** `python tests/auto/run_tests.py` (supports `--phase N`)
- **Unit tests:** `pytest`
- **Current state & known issues:** `handoff.md`, `JV_V1_BUG_HANDOFF_2026-06-27.md`,
  `FREEZE_AND_BACKLOG_2026-06-19.md`

---

## 7. Track every call (so we have a record)

Keep a simple log as you go — it's how Quentin can see progress while away, and how we avoid
re-fixing the same thing:

| # | Call / scenario | Expected | Actual | Render log (Call SID / time) | Root cause | Fix commit | Re-test result |
|---|-----------------|----------|--------|------------------------------|------------|------------|----------------|
| 1 | | | | | | | |
| 2 | | | | | | | |

---

**The one sentence to remember:** *find the root cause, make the smallest change, prove it
didn't break anything — then move on.*


