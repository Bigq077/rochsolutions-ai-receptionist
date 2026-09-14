# RochSolutions — Skills Build Plan

**Written:** 2026-09-14
**Companion to:** `OPERATING_SYSTEM_REVAMP.md` (what to build, in what order)
and `CLAUDE_CODE_OPERATING_MANUAL.md` (how to work day to day).

**This document:** the skills to build, what each is for, how each flows, and a
paste-ready build prompt for every one.

---

## 1. Why these skills exist

Each skill replaces a role you would otherwise hire. That is the test — if a
skill does not remove a job from your plate, it does not belong on this list.

| Skill | Replaces | Status |
|---|---|---|
| `susie-triage` | QA lead — "what is systematically wrong?" | ✅ **built** — reads suite results *and* live obs calls |
| `susie-debug` | engineer — "fix this one bug properly" | ✅ **built** — SKILL.md + 5 references |
| `verify-call` | QA tester — "prove it works" | ✅ **built** |
| `onboard-clinic` | implementation specialist | blocked on tenancy — after the webinar |
| `clinic-report` | account manager | next buildable; obs capture is on |
| `deliverable-pack` | marketing/content | independent — belongs in `roch-client-work` |
| *(existing)* `engineering:incident-response` | ops on-call | ❌ **not installed.** `docs/INCIDENT.md` written instead |

> **Status updated 2026-09-14.** Build-order items 1–5 are done. Corrections
> found while building are in §13 — several contradict this document, and the
> code won.



---

## 2. Design principles — apply to every skill here

1. **`SKILL.md` is the procedure, not the knowledge.** Keep it under ~150 lines.
   Deep detail goes in `references/*.md`, loaded only when the situation needs it.
   (Boris §112, progressive disclosure.)

2. **Judgement, not rules.** A rule that is right 90% of the time is wrong the
   other 10%. Say what good looks like and why; don't enumerate every case.
   (§110.)

3. **Never bulk-read this repo's markdown.** The plan docs have been wrong five
   times by their own admission, an untracked `CLAUDE.md` once contradicted the
   tracked one, and ~60 client deliverables sit in the root. A skill that
   ingests all of it absorbs the contradictions. Read *targeted* files, named
   in the skill.

4. **Git is the only source that cannot lie.** Docs describe intent; commits are
   what happened. Where a skill needs history, make it a step
   ("search `git log` for prior fixes to this module"), not a bulk pre-read.

5. **Every skill ends in proof.** State what was run and what it returned.
   "Looks right" is not an output.

6. **Every skill states its constraints.** Latency budget, clinic.json rule,
   canonical branch, smallest diff. An agent that doesn't know the constraints
   will violate them confidently.

7. **One skill, one job.** If it needs "and then", it's two skills.

---

## 3. `susie-triage` — the clustering skill

### Purpose
Turn a pile of failures into a short ranked list of root causes. This is a
different cognitive job from debugging: **clustering, not root-causing.**

### Why first
The 2026-04-07 suite run showed 34 failures. They collapse to roughly four
causes — `booking_confirmed` alone accounted for 20, and Phases 4/5/8 scoring
0/5, 0/3, 0/5 is one break in slot selection cascading downstream. **Four months
of manual test-calling never produced that summary.** One triage pass would have.

It is also the **least blocked** skill on this list: 2,874 result artefacts
already exist in `tests/auto/results/`. It can produce value today, before
tenancy, before obs, before anything.

### Fires when
- A suite run finishes with failures
- Weekly, over the last N days of obs records (once obs is on)
- "What's actually wrong with Susie right now?"

### Inputs
`tests/auto/results/*.json` and `report_*.txt`; or obs records once enabled.

### Flow
1. Collect every failure in the window.
2. For each, record: scenario, phase, **earliest** failing check, transcript excerpt.
3. Cluster by earliest failing check and by flow position — not by scenario name.
4. For each cluster, infer the candidate subsystem (use `references/symptom-map.md`).
5. Rank clusters by **patient impact**, not by count:
   a missed booking outranks a clumsy phrase, always.
6. Detect cascades — flag when cluster B only ever occurs after cluster A.
7. Output the ranked list with, for each: likely cause, affected scenarios,
   one representative transcript, and the suggested next action.

### Output
`docs/triage/TRIAGE_<date>.md` — a ranked root-cause list. Feeds `susie-debug`.

### Done when
Run against the April results it produces **4–6 clusters, not 34 items**, and
names slot selection as the dominant cascade.

### Build prompt
```
Build a Claude Code skill at .claude/skills/susie-triage/SKILL.md.

Purpose: cluster many Susie call failures into a short ranked list of root
causes. This is clustering, not debugging — it decides WHAT to fix, and
susie-debug then fixes it.

Study first (read, don't assume):
  - tests/auto/evaluator.py — especially _CLAUDE_GATE_MAP and the rule-based
    checks. These 36 check names are the symptom vocabulary.
  - tests/auto/report.py — the report format it will parse.
  - tests/auto/results/report_20260407_110303.txt — a real run with 34 failures.
  - tests/auto/scenarios/all_scenarios.py — phase structure and flow order.

The skill must encode:
  - Cluster by EARLIEST failing check in flow order, never by the last one.
    booking_confirmed is terminal: it fails whenever anything upstream failed.
  - Rank by patient impact, not frequency. A missed booking outranks a clumsy
    phrase.
  - Detect cascades: flag when cluster B only ever appears alongside cluster A.
  - Distinguish stale-scenario failures from real regressions (see
    tests/auto/fixer.py — it already makes this call).
  - Output a ranked markdown report to docs/triage/.

Keep SKILL.md under 150 lines. Put the check→subsystem mapping in
references/symptom-map.md.

Verify it: run it against tests/auto/results/report_20260407_110303.txt. It
should produce 4-6 root causes, not 34 items, and identify slot selection as
the dominant cascade. If it lists more than ~8, the clustering is too weak.
```

---

## 4. `susie-debug` — the single-bug skill

### Purpose
Take one identified bug from report to proven fix.

### Status
A first version exists. It needs the `references/` split and the git-history
step. **Do not deepen `SKILL.md` — deepen the references.**

### Fires when
One specific call went wrong; a triage cluster is picked up for fixing.
**Not** when a clinic is down right now — that's incident response.

### Flow (already drafted)
Establish source → reproduce as a scenario → localise from the earliest failing
check → Susie bug or test bug → (telephony? route to the right Twilio skill) →
fix under constraints → prove with a regression test → kill the class.

### What to add
- **`references/architecture.md`** — call flow, which module owns what.
- **`references/symptom-map.md`** — the 36 checks → subsystems (shared with triage).
- **`references/known-bugs.md`** — recurring failures and what fixed them.
- **`references/gotchas.md`** — branch rules, latency budget, measurement traps.
- **A git-history step**: once localised to a module, search `git log` for prior
  fixes to it *before* writing new code. "This was fixed in April and regressed"
  is the single most valuable thing it can tell you — and with 33 commits that
  never got cherry-picked, regression-by-omission is a live failure mode here.

### Build prompt (for the references only)
```
The skill at .claude/skills/susie-debug/SKILL.md exists. Do not rewrite it.
Build its references/ directory so it carries deep system knowledge without
bloating the always-loaded procedure.

Create four files, each under 200 lines:

1. references/architecture.md — the call flow from Twilio PSTN through
   app/routes/twilio.py, app/media_streams/connection.py, stt_stream,
   utterance_router, the flow state machine, and app/booking. For each module:
   what it owns, and the symptoms that point at it. Derive this by READING THE
   CODE, not from CLAUDE.md.

2. references/symptom-map.md — every check name in tests/auto/evaluator.py
   mapped to the subsystem that produces it, plus flow order so the "earliest
   failing check" rule is mechanical.

3. references/known-bugs.md — mine `git log` for past fixes to the call flow.
   For each recurring class: symptom, root cause, the commit that fixed it,
   and whether it has regressed since. Prioritise anything fixed more than once.

4. references/gotchas.md — the traps. git fetch --all before measuring; compare
   origin/* never local; clinic behaviour belongs in clinic.json; p95 turn
   latency under 1.5s; no new dependencies (cold start); which branch is
   canonical.

Then add ONE step to SKILL.md, after localisation: search git log for prior
fixes to the implicated module before writing any new code.

Constraint: derive everything from code and git history. Do NOT bulk-read the
markdown in docs/ or the repo root — those documents contradict each other and
have been wrong repeatedly. Read a doc only to confirm something the code
already told you.
```

---

## 5. `verify-call` — the gate

### Purpose
One command that answers "does Susie still work?" Every agent runs it before
claiming done.

### Blocked on
The suite running green-ish against the canonical branch (Revamp Stage 2b).

### Fires when
Any behavioural change is complete; before any deploy; on demand.

### Flow
Confirm branch → pick scope (single scenario / phase / full 118) → run in
direct-WS mode → report pass rate against `MIN_PASS_RATE = 0.97` → name every
failure with its earliest failing check → state clearly whether this passes.

### Output
A pass/fail verdict with the number. Never a narrative.

### Done when
An agent that has made a change can run one skill and produce a number you trust
without you phoning anyone.

---

## 6. `onboard-clinic` — the constraint

### Purpose
Take a new clinic from signed to live in under a day, without touching engine code.

### Why it matters most
Your own CLAUDE.md: *"the constraint is onboarding throughput, not demand."*
This is the skill that makes 15 clinics arithmetic rather than a wince.

### Blocked on
Env-var tenancy (Revamp Stage 2a). Until one service serves many clinics by
config, this skill would just automate the branch-per-clinic pattern that
already broke at four.

### Flow
Gather clinic facts → write `clinic.json` → write `knowledge.md` (FAQ, services,
prices, hours, location) → provision the Twilio number
(`twilio-developer-kit:twilio-numbers-senders`) → wire the booking calendar →
run the clinic-specific scenario subset → go-live checklist → first-week watch.

### Done when
A dry run onboards a fictional clinic end-to-end in under a day of your time.
**That dry run is the real webinar go/no-go** — not the date.

---

## 7. `clinic-report` — the account manager

### Purpose
A weekly per-clinic report the clinic actually wants to read: calls handled,
bookings made, escalations, anything that needs their attention.

### Blocked on
obs capture enabled (Revamp Stage 4a). `scripts/weekly_report.py` already exists
— this skill wraps and extends it rather than replacing it.

### Runs
On a Routine, weekly, per clinic. Not in a terminal.

---

## 8. `deliverable-pack` — the other business

### Purpose
The ~60 client documents are six repeating shapes: SEO audit, strategy plan,
campaign/content plan, meeting kit, proposal, deck.

### Independent
No dependency on any engine work. Build it in the `roch-client-work` repo, in
Lane 4, whenever the engine lanes are blocked.

### Note
`searchfit-seo:*` and `marketing:*` are already installed and underused. This
skill should orchestrate them plus `pptx`/`docx`, not reimplement them.

---

## 9. Wire in, don't build: `engineering:incident-response`

**You have live patients and no incident process.** This is the highest-risk gap
on the list and it needs no building — the skill is installed.

What it needs is a one-page `docs/INCIDENT.md`: who to call, how to reach each
clinic, how to roll back a Render service, what "severity 1" means here (a
booking the caller believes exists but doesn't), and the holding message a
clinic gets while you diagnose.

**A live clinic broken at 9am is not a debugging session.** Restore first,
communicate second, diagnose third. Do this before the cohort arrives.

---

## 10. Build order

```
NOW (nothing blocks these)
  1. susie-triage           ← runs against April results today
  2. susie-debug references ← reads code + git history
  3. docs/INCIDENT.md       ← one page, highest risk reduction

AFTER canonical branch settled (Revamp Stage 1)
  4. Update the branch rule in susie-debug/references/gotchas.md

AFTER suite runs (Stage 2b)
  5. verify-call

AFTER tenancy (Stage 2a)
  6. onboard-clinic

AFTER obs on (Stage 4a)
  7. clinic-report

ANY TIME, separate repo
  8. deliverable-pack
```

Use `anthropic-skills:skill-creator` to build each one — it also supports evals,
so a skill's triggering accuracy can be measured rather than guessed.

---

## 11. How to know a skill is good

- It fires when it should and stays quiet when it shouldn't (test the description).
- `SKILL.md` is under ~150 lines; depth lives in `references/`.
- It states its constraints, so an agent cannot violate them unknowingly.
- It ends in evidence, not assertion.
- Running it twice on the same input gives the same answer.
- **Someone other than you could follow it.** That is the whole point — these
  skills are the employees you are choosing not to hire.

---

## 12. Open questions

These change the plan and are still unanswered:

1. **Is obs capture actually enabled yet?** If not, every skill here starts from
   "Quentin remembers a call" — the exact bottleneck the plan exists to remove.
   `clinic-report` and half of `susie-triage` stay blocked until it's on.
2. **What does a normal working day look like** — do you start by checking
   something, or does work arrive when a clinic emails? This decides whether the
   weekly triage is a Routine or a habit.
3. **Has the canonical branch been settled?** `susie-debug` currently says
   "land it on the canonical branch." If ADR-002 is still open, that instruction
   has no referent and the skill will be as lost as a new hire would be.

---

## 13. Corrections found while building (2026-09-14)

Recorded here because several contradict the plan above. Where they disagree,
these were measured.

**1. `susie-debug/SKILL.md` did not exist.** §4 says a first version exists and
must not be rewritten. Nothing by that name was on disk; it was built from the
flow drafted in §4.

**2. `engineering:incident-response` and `twilio-developer-kit` are not
installed.** §9 calls incident-response "the highest-risk gap… it needs no
building — the skill is installed." `docs/INCIDENT.md` was written from scratch.

**3. `app/obs/` already contained half of what this plan proposes**, and §2's own
"orchestrate, don't reimplement" principle was not applied to it. `weekly.py` is
the Monday ritual; `to_scenario.py` mines a PII-free regression scenario from a
real call; `judge.py` already scores and tags. `susie-triage` now wraps
`weekly.py` rather than duplicating it, and `susie-debug` mines from real calls.

**4. `booking_confirmed` does not mean a booking exists.** It is set in `flow.py`
where the confirmation *sentence* is composed, not where the calendar write
succeeds. The transcript then reads as a perfect call, so the judge scores it
5/5 and `tests/auto/evaluator.py` — which calls that field "the authoritative
source" — passes it. Detected retrospectively by `susie-triage --obs`
(`PHANTOM_BOOKING`) and live by the new `booking_not_written` alert.

**5. Clinics differ in whether a booking id exists at all.** Theorem is Acuity,
Vital Edge is Google Calendar, Joint Venture and Northgate are Carepatron portal
handoffs that store **no id**. Booking integrity is therefore unverifiable from
the call record for two of the four clinics. That gap is open.

**6. The offline regression gate is worth nothing as a regression signal.**
`app/obs/regress.py` imports no `app` code and reads only each scenario's frozen
`transcript`, so no code change can alter its result — it is a lint over a
recorded corpus. Compounding it, all 60 mined scenarios carry the identical
placeholder `expected: {'no_technical_error': True}` while being tagged
`booking_error` ×47, `loop` ×47, `dead_end` ×45. "All 60 pass" means only that
Susie never said "technical issue" in 60 stored transcripts.

**7. The `verify-call` gate in §5 is achievable now.** §10 blocks it on "the
suite running green", but the credentials, the runner and a direct-WS mode that
costs nothing are all present. It was built.

## 14. Open work, in priority order

1. **Run `susie-triage --obs` against the live store.** Needs `OBS_DATABASE_URL`.
   Nothing has ever looked for `PHANTOM_BOOKING`; any hit is a patient to phone.
2. **Fill the nine `FILL:` blanks in `docs/INCIDENT.md`** — on-call number,
   Render service names and branches, clinic contact names.
3. **Sharpen the 60 mined `expected` blocks**, and decide whether the offline
   gate should drive `responses` through the flow instead of asserting frozen
   text. Today it cannot catch a regression.
4. **Close the Carepatron booking-integrity gap** (correction 5).
5. `clinic-report`, then `onboard-clinic` after the webinar.
