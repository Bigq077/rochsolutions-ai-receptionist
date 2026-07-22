# Skill Playbook

Which installed engineering skill to invoke at each point, and — equally
important — which ones not to bother with. Skills are leverage, not ceremony;
invoking one that does not fit costs context and produces plausible-looking
output that nobody needs.

---

## Directly useful

### `engineering:testing-strategy` — **highest value in this window**
**Use in Phase 0 and Phase 3.**
The adversarial call suite is the single highest-leverage artefact you can build
in ten days. It is also the thing that makes it safe to touch `flow.py` later.
Use it to design the 20-scenario suite, and again to design the characterization
test approach for the post-meeting `handle_transcript` decomposition.

*Prompt shape:* "Design a test strategy for a voice AI receptionist where the
unit of failure is a phone call, not a function. Cover barge-in, STT errors,
provider failure, and clinical red flags. Distinguish what can be unit-tested
from what requires a live call."

---

### `engineering:debug` — **use constantly in Phase 3**
Every defect found by the adversarial suite. Its value here is enforcing
*reproduce → isolate → diagnose → fix* rather than the pattern this codebase
appears to have accumulated: wrap in a try/except and move on. Given a 15.7k-line
`handle_transcript`, disciplined isolation is not optional.

---

### `engineering:code-review` — **use on every Phase 1 and Phase 2 diff**
Phase 1 changes error handling on the booking path. That is exactly the class of
change where a plausible-looking diff introduces a new silent failure. Review
every one before merge, with explicit attention to: does any path still confirm
without a booking ID, and does any new exception handler swallow.

---

### `engineering:deploy-checklist` — **Phase 5, and before the demo deploy**
You have four Render services with `autoDeploy: true` and no branch pinning in
`render.yaml`. That is a configuration where a push to the wrong branch changes
what answers a real clinic's phone. Use this to build a written pre-deploy
checklist and rollback trigger list. Run it before the demo deploy.

---

### `engineering:incident-response` — **Phase 5, to write the runbook**
Use it to structure the runbook *before* an incident, and to define severity
levels. "Bookings failing silently" and "the assistant said something odd" are
not the same severity and should not get the same response.

---

### `engineering:documentation` — **Phase 5**
Runbook, rollback procedure, onboarding guide. The onboarding guide matters more
than it looks: at cohort scale, the guide *is* the product's operational surface.
Writing it now also surfaces exactly how much of onboarding is currently
"an engineer does a thing" — which is your scale bottleneck made visible.

---

### `engineering:architecture` — **Phase 2 and Phase 6**
Two ADRs worth writing:
1. *Which observability flags to enable for cohort one and which to leave off* —
   particularly the judge (per-call LLM cost) and transcript retention (GDPR).
   A written decision beats memory in three weeks.
2. *Runtime tenancy design* — this is the significant architectural decision of
   the next quarter. Inbound-number → tenant resolution, config precedence,
   secret management per clinic, and tenant isolation guarantees. Write it as an
   ADR before writing code.

---

### `engineering:tech-debt` — **Phase 6 only. Not before.**
Run it *after* the meeting, scoped to `flow.py` and `connection.py`. Running it
now would produce a large, correct, demoralising list of things you must not do
this fortnight. Debt inventory is a planning tool, not a pre-demo activity.

---

### `engineering:system-design` — **Phase 6**
For the runtime-tenancy and self-serve-onboarding design work. Not needed for
anything inside the 10-day window.

---

## Not useful here

- **`engineering:standup`** — solo work against a fixed deadline. No audience.
- **All `searchfit-seo:*`** — wrong domain entirely. (Relevant to the client
  deliverables cluttering the repo root, not to this system.)
- **All `21st:*`** — UI generation. No user-facing UI in scope. Would become
  relevant if the observability dashboard or an onboarding wizard is built
  post-meeting.
- **`skill-creator`** — possible post-meeting: a "clinic onboarding" skill that
  turns a discovery call into a validated `clinic.json` would be genuinely
  valuable at cohort scale. Not now.
- **`docx`/`pptx`/`xlsx`/`pdf`** — for meeting collateral, not engineering.

---

## Subagent usage

The repo is 86k lines with two files over 10k. Context management matters.

- **`Explore`** for "where is X handled" questions across `flow.py` and
  `connection.py`. Do not read those files whole — it will consume the context
  window and leave nothing for the work.
- **`Plan`** before Phase 2's cherry-pick sequencing and Phase 6's tenancy design.
- **`general-purpose`** for the Phase 0 deleted-test triage — a self-contained,
  mechanical, high-volume classification job that is perfect to delegate.
- **Verification subagent** on the Phase 1 diff. The booking path is where a
  second independent read genuinely pays for itself.

---

## Connectors currently unavailable

The `engineering` plugin bundles GitHub, Linear, Slack, Datadog, PagerDuty,
Asana, Atlassian and Notion MCP servers. **All require OAuth authorisation and
none are currently connected.** Authorise them via `/mcp` in an interactive
Claude Code session if you want them.

Worth connecting before the window:

- **GitHub** — PR review workflow on the Phase 1 and 2 diffs.
- **Datadog or an alternative** — Phase 2 alerting has to land somewhere a human
  actually looks. Email via the existing SMTP config is an acceptable first
  cohort answer and needs no new integration.

The rest are project-management overhead you do not need for ten days of solo
focused work.
