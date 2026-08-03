# Jules brief — 2026-08-03, ~00:30 start

**You:** Vital Edge onboarding. **Quentin:** Theorem Health.
**Engine branch:** `latency-eval`. **Your branch:** `vitaledge-onboarding`.

---

## 0. Read this first

Five engine commits landed on `latency-eval` tonight, **after** the last time you
pulled. Branch or rebase from `latency-eval` at `e3f3d2f` or later, or you will
be working against an engine that is missing a fix aimed directly at your clinic.

```bash
git fetch origin && git log --oneline latency-eval -6
```

---

## 1. The one with your name on it

**`e3f3d2f` — the provisional 90-minute fix is back on the canonical engine.**

`a1c2d70` ("let provisional callers book a 90-minute session") was committed to
**`vitaledge-onboarding` on 24 Jul and never came back to `latency-eval`**.
`git branch --contains` confirmed it lived on your branch only.

That means: **had you ported Vital Edge onto the canonical engine tonight, you
would have silently lost it** — a real caller already abandoned over this
(`CAe3aaab`, 24 Jul, asked for a 90-minute deep tissue, was told "that's only a
60-minute session").

It is now cherry-picked onto `latency-eval` and verified there — not assumed from
your branch. **Do not re-apply it.** If you hit a conflict on those hunks during
the port, the canonical version wins.

> **Check for siblings.** If `a1c2d70` stranded, others may have. Before you
> port, run `git cherry latency-eval vitaledge-onboarding` and look at anything
> marked `+` that touches `app/` rather than `app/clinics/vital_edge/`. Any
> engine fix there is a canonical-first violation and must come back to
> `latency-eval` first, not travel sideways.

---

## 2. The rule that produced that mess

**Canonical-first, no exceptions.** Engine fixes land on `latency-eval` and the
clinic branches inherit by cherry-pick. Never fix on `vitaledge-onboarding` and
port up — that is exactly how the 90-minute fix stranded for ten days.

If you find an engine bug tonight: fix it on `latency-eval`, with a regression
test, then cherry-pick down. If that is too slow in the moment, **write the row
down and hand it to Quentin** rather than patching your branch.

---

## 3. What else landed tonight, and whether it touches you

| Commit | What | Affects Vital Edge? |
|---|---|---|
| `a4c267d` | **Gate 5f stood down on any `?`** — the mandated cancel closing ends in a question, so the false-confirmation guard was disabled on the one wording the prompt requires. Not cancel-only: any completion claim followed by a question escaped, all three write families | **YES — this was live on your clinic until tonight** |
| `8b06879` | Filler was one-shot; a 14 s upstream stall became ~12 s of silence. Now re-arms once at 5 s | Yes |
| `2a146dd` | `lookup_recent_appointment` could overwrite a confirmed phone with the number on file, and the A3 gate would then "correct" the booking to the stale one. Hardening — reachability measured at 0 of 155 calls | No — Theorem-only path |
| `e3f3d2f` | The 90-minute backport above | **YES** |

> **`B-47` is closed** — the "phone number that isn't a phone number" row. Four
> mechanisms already covered it (A1 block, keypad format check, caller-ID-only
> verbal confirm, A3 reconcile). Don't re-investigate it; see the row in
> `REGISTER_B_U.md` for what a length check can and cannot catch.

---

## 4. Traps that cost real time tonight — do not re-pay them

1. **`flow.py` is not on the live path.** All four deployments run the free-form
   LLM loop (`connection.py:6714` — *"execution NEVER falls through to the
   FlowEngine code below"*). `vital_edge` is `template_v1`, so it qualifies. A
   grep that returns mostly `flow.py` hits has probably found dead code. This
   collapsed 13 candidate sites to 3 on one investigation tonight.
2. **`git stash` does not revert in this tree** (OneDrive locks). It saves and
   leaves the working copy dirty. Back changes out by hand and **verify with a
   reference count** before trusting a "before" measurement.
3. **The suite is meant to be red — 95.** Never look for green. Capture the
   failing set, diff it before and after. Tonight a change passed its own tests
   and both existing guard files, and the *set diff* caught that it had silently
   broken an unrelated gate 200 lines away. Counts alone would have missed it.
4. **obs transcripts are post-Gate-5 and exclude fillers.** A wrong sentence
   there may be the gate rewriting a correct generation, and ack-fillers never
   appear at all — so absence in a transcript is not absence on the call.
5. **A row without `file:line` is a lead, not a finding.** Five for five, every
   one-line defect row carried between sessions tonight turned out mis-scoped.

---

## 5. Still open on the engine — known, not forgotten

Do not treat these as new discoveries:

- **Track C** (`B-06` `B-08` `B-10` `B-11` `B-12` `B-39`) — prompt-side, each
  needs its own build and its own call to attribute. Will still be open.
- **Change A** — on an early-name call the surname is discovered by
  `book_appointment` refusing, *after* the caller has said "go ahead". Measured:
  6 of ~40 calls, reproduces on today's build. Two arms (early name; capture
  split). Not started.
- **`B-49`** — `vbi_neck` has never armed once across the corpus. **`jv_v1` only**
  — `vital_edge/clinic.json` has no `clinical_screening` block, so this cannot
  reach you.
- **`B-51`, `B-27`/`B-16`, `B-30`** — see the register.

Full detail: `docs/plan/REGISTER_B_U.md`.

---

## 6. If you dial

Reconcile every booking against the real calendar — not "did it sound right".
And note two things that are **not** verifiable by ear:

- The Gate 5f fix only shows up on a **refused** write. A clean-sounding call
  proves nothing about it.
- The filler re-arm only fires on an upstream stall you cannot summon. Its proof
  is `grep "second filler phrase" render.log` over real traffic, not a call.

**Deploy proof:** `/health` returns a hardcoded `1.0.0` and is useless. The only
proof of what is running is `[build_info] running build <sha>` in the Render log
at call cleanup.
