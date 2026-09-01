# Deployment Inventory

**Filled 2026-09-01, to the limit of what the repo can evidence.** Cells marked
**[owner]** are Render/Twilio dashboard facts that Claude Code cannot see — they
are not unknown because nobody looked, they are unknown because they are not
knowable from here. Everything else below is cited to a log line or a file.

---

## Services

Four Render services, one lineage (ADR-002). Region: Frankfurt for all — set in
`render.yaml`'s header comment, confirmed in the dashboard 2026-03-12.

| Render service | Branch | Clinic(s) | Twilio number(s) | Evidence |
|---|---|---|---|---|
| `srv-d9ac6bfaqgkc739dstsg` — the **demo** service, host `low-latency-joint-venture.onrender.com` | `latency-eval` | `northgate` | +447366263180 | stream URL + `[ms_router] to=` in call `CAc119b8838f556ac20f9552dee2e4021f`; service id confirmed 2026-07-27 |
| `srv-d8va6cbtqb8s73fbpvag` — `vitaledge` | `production` | `vital_edge` | +447426779875 | repointed to `latency-eval` 2026-08-31 01:57 UTC, to `production` 2026-09-01 |
| **[owner]** — the JV service | `production` | `jv_v1` | +447367002651 | `clinic_config.TWILIO_TO_CLINIC` |
| **[owner]** — the Theorem service | `production` | `theorem_v3`, and **[owner]** whether `theorem_v2` is the same service | +447380841468 (v3), +447366530580 (v2) | `clinic_config.TWILIO_TO_CLINIC` |

⚠️ `srv-d56h5bm…` appears in older notes and is **not** the demo service. Log
searches against it came up empty for a whole session in July. Do not reuse it.

**Calendars.** `northgate` books into Google Calendar
`abce6807cb23e39c85e993a08578f6834a05175804dce87815603c84feb694eb@group.calendar.google.com`
(event `vjqu5nlcsk5mp7iogh6fh21u14`, 2026-09-01). The rest are **[owner]** — and
note Theorem short-circuits to the **Acuity** executor, so its calendar is not a
Google one.

---

## Engine drift — CLOSED

**Zero.** This section existed to quantify FM-14 ("a fix verified on one clinic
may not exist on another"). The 2026-09-01 fold made all four services run one
lineage, and ADR-002 keeps `production` a strict ancestor of `latency-eval`, so
the two can differ only by commits that have not been promoted yet — never by
content. `git diff origin/production origin/latency-eval` is the whole answer,
and it is empty immediately after a promotion.

The retired per-clinic branches (`main`, `jv-v1-onboarding`,
`vitaledge-onboarding`, `theorem-onboarding`, `jv_v2`) still diverge wildly from
each other. That no longer matters for correctness — nothing deploys them — but
it is why they are kept: each is a working rollback target.

---

## Deploy safety

| Check | Status | Notes |
|---|---|---|
| Is `autoDeploy` on for every service? | **Yes**, deliberately | It was turned OFF on the three patient services as ADR-002's interim, then back ON once they tracked `production`. Auto-deploy is safe when the branch is gated; it was the *ungated* branch that was the problem. |
| Is the branch pinned per service in the dashboard? | **Yes** — and only there | `render.yaml` has no branch pin and declares one service. The repo cannot answer "what is live". |
| Does any service auto-deploy from a branch used for experiments? | **No, since 2026-09-01** | This was the urgent one. Until the repoint, all four tracked `latency-eval` and a push deployed every clinic — see ADR-002's opening. |
| Who can push to the deploy branches? | **[owner]** — repo collaborators | `production` carries a ruleset: no deletions, no force pushes, linear history required, owner on the bypass list. Nothing gates `latency-eval`, which is intended. |
| **Is there a staging service?** (line 53, asked since Phase 0) | **Sort of — and this is the honest answer.** | The demo service on `latency-eval` IS the staging gate now. But it is a *staging branch on a real service*, not a staging environment: it has its own live Twilio number, its own real calendar, and `SMS_ENABLED=off`. So it gates engine regressions and **cannot** gate anything behind an env flag, or anything tenant-specific to `theorem_v3` / `vital_edge`. A true staging service with mirror tenants is ADR-002 Option E, deferred and still wanted. |

---

## Secrets

37 env vars per `.env.example`. Per service, confirm presence — not values.

| Service | All 37 set? | Missing | Notes |
|---|---|---|---|

Note the tenant-identity vars — `CLINIC_NAME`, `CLINIC_ADDRESS`, `CLINIC_PHONE`,
`ACUITY_CALENDAR_ID_ALCESTER` / `_REDDITCH` / `_MARK` / `_LEANNE` — and the obs
vars `OBS_DATABASE_URL`, `OBS_ALERT_SMS_TO`, plus `SMS_ENABLED`. These are the
ones that will move into `clinic.json` under runtime tenancy (Phase 6).

---

## Worktree hygiene

`git worktree list` shows ~15 registered worktrees under
`C:/Users/quent/AppData/Local/Temp/claude/`, most marked *prunable*. Stale
worktrees are how a session ends up confidently measuring the wrong tree — which
has already happened once on this project.

```
git worktree prune
git worktree list      # confirm what remains is intentional
```

- [ ] Pruned
- [ ] Remaining worktrees are intentional and documented

---

## What could not be determined from the repo

List anything requiring dashboard access, and get it from Ismael.
