# Cohort 1 — Hands On Money webinar plan

**Written:** 2026-08-06. **Target webinar:** mid-September 2026 (recommend
Wed 16 September). **Owner:** Quentin.

This plan covers the commercial side of the HOM webinar and the engineering work
that gates it. It supersedes nothing — `PRODUCTION_READINESS_PLAN.md` still owns
the phased roadmap, `STATUS.md` owns the day-to-day. This is the layer above:
what we are selling, to how many, and what must be true first.

---

## 1. Situation as of 2026-08-06

| Client | State | Note |
|---|---|---|
| Theorem Health | **Live** since 2026-08-05 | First fully live clinic on its own number |
| Vital Edge | Live, on voicemail | Executive decision taken: move to own number, Theorem-style |
| Joint Venture | Slipped from Mon 2026-08-03 | Client-side (fiancé unwell). No hard deadline. |
| Hands On Money | Meeting done, went well | Live demo landed. Open invitation to webinar, mid-September. |

HOM is a financial-advice network of 230–250 physiotherapy clinics, of which
~100 are on the webinar list. **They are not taking a cut** (never raised across
the whole engagement — treat as settled unless they raise it).

---

## 2. Commercial plan

### 2.1 The numbers

Grounded estimates, not the optimistic case:

| Stage | Estimate | Basis |
|---|---|---|
| Live attendees | 20–30 | ~100-member list, network-promoted; 20–30% live attendance is normal |
| Attendee → discovery call | 30–35% | Top of the B2B range, justified by HOM's endorsement |
| Discovery call → closed | 40–50% | High-trust purchase: phone line + calendar access |
| **Attendee → paying** | **12–18%** | ≈ 4 clinics from 25 attendees |
| Replay/laggard → paying | 2–4% | Of the ~25–35 who actually open a replay: 1–2 more |

**Planning number: 4–7 clinics signed within 90 days.** Bull case 10, bear case 2.

> The original working estimate was 60–70% of attendees and 10% of the replay
> tail. That is 4–5× too high. Nothing in B2B converts at 60% off a webinar.
> Plan against 4–7 and treat 10 as upside.

### 2.2 Revenue

| Scenario | Clinics | Monthly | Annual |
|---|---|---|---|
| Bear | 2 | £398 | £4,776 |
| Realistic | 4–7 | £796–1,393 | £9,552–16,716 |
| Bull | 10 | £1,990 | £23,880 |

Existing three clinics at £109 add £327/mo.

**Read this correctly: the webinar is worth ~£10–17k ARR, and that is not the
point.** Its value is the reference base. Six visibly delighted clinics inside a
250-clinic network that talks constantly is what unlocks the other 240. Every
decision below optimises for references, not for cash in September.

### 2.3 Pricing

| | Price | Applies to |
|---|---|---|
| Founding rate | £109/mo | Theorem, Vital Edge, Joint Venture — held 12 months |
| Cohort 1 rate | **£199/mo** | All HOM clinics |
| Onboarding fee | **£300–500 one-off** | All new clinics from Cohort 1 |

**Why £199 and not more.** Jane's "I'd bite your hand off at that price" and
"anything under 200 is completely normal" is a clear mispricing signal — but
read the second half carefully: £199 sits at the *top of the normal band*, not
above it. £249 is testable and we will never have more pricing leverage than
immediately after a well-received live demo. We hold at £199 anyway, to keep the
option of raising later without renegotiating a reference customer mid-cohort.
**Take the references now, take the pricing power in year two.**

**Why the onboarding fee.** It recovers the real setup days, which £199/mo does
not for months. More importantly it filters: a clinic that won't pay £400 to get
set up won't do the work to make it succeed, and that is precisely the clinic
that becomes the bad reference. In a network this tight, a filter on cohort
quality is worth more than the fee.

**⚠️ Open blocker on pricing — see §4.1.** Flat £199 is not safe to sell until
per-call COGS is measured. A high-volume clinic may be loss-making at flat rate.
Do not commit to flat-rate pricing publicly before the cost rollup has run
against a month of real traffic.

### 2.4 Cohort cap

**Cap Cohort 1 at 6 clinics. Waitlist the rest for November.**

Two reasons, both real:

1. **Capacity.** One clinic currently = one branch = one Render service. At four
   clients this is painful; at ten, applying every fix ten times consumes the
   whole week and improvement stops entirely. See §3.
2. **Contagion.** In a 250-clinic network where everyone talks, one clinic with
   a missed booking is contagious in a way one delighted clinic is not. Getting
   15 would be *worse* than getting 5.

The scarcity is genuine, not manufactured. Say it plainly on the webinar: "I'm
taking six clinics in this intake, the rest go on the list for November." A
waitlist converts better than an oversubscribed rollout.

### 2.5 Positioning

**Stop leading with the £30,000 receptionist comparison.** A 150× value gap does
not read as a bargain, it reads as suspicious — the instinctive response is
"what's wrong with it?"

Lead with a small, checkable claim instead: a physio initial assessment is
£45–60, a full course of treatment £250–400. **One recovered missed call a month
more than pays for it.** A clinic owner can verify that against their own diary
in thirty seconds. Keep £30k as the closer, not the opener.

### 2.6 The two-price problem

Existing three at £109, cohort at £199, inside a network of clinics advised on
money by a firm whose entire job is comparing costs. **This will surface.**

Get ahead of it: name the existing three "founding clinics" explicitly, hold
them at £109 for 12 months, and say so openly on the webinar. That is honest,
defensible, and makes £199 read as the real price rather than an opportunistic
markup. The failure mode is discovering the discrepancy live in Q&A.

### 2.7 Should the date move?

**No. Keep mid-September.** The meeting enthusiasm is fresh and HOM's attention
decays faster than the product improves. The cohort cap, not the calendar, is
the safety valve.

Move it only if the Gate at §5 fails — specifically, if convergence (§3) has not
landed. Delay for capacity, never for polish.

---

## 3. The binding constraint

**It is not lead volume. It is onboarding throughput and engine convergence.**

> **Corrected 2026-08-06** — an earlier draft of this section claimed "four
> different versions of the improvement engine" based on `app/obs/` file counts
> of 19/19/18/17/2. That count compared a stale *local* `latency-eval` against
> remote branches and was wrong. Full analysis:
> **`docs/plan/BRANCH_CONVERGENCE_ANALYSIS.md`**.

The real picture: `latency-eval`, `vitaledge-onboarding` and `theorem-onboarding`
carry an **identical 19-module `app/obs/`**. There is one observability
codebase, not four. `jv-v1-onboarding` is the sole outlier with 2 modules
(`__init__.py`, `alerts.py`) — it has operator failure-alerting but **no call
capture, no judge, no digest, no store.**

So the flywheel does spin — on three of four clinics. **JV is the exception, and
it is generating live calls that teach us nothing.**

The divergence that does bite is engine drift between the three live branches,
and it is measurable: the T-2 and T-3 fixes of 2026-08-05 were applied twice
under different SHAs with **byte-identical patch-ids** (`e6fed61`/`c585fff`,
`9f69b91`/`f35ba8a`). That is the fix-once-per-branch tax, already being paid,
already duplicating work. At ten clinics it consumes the week and improvement
stops permanently.

---

## 4. Engineering work to mid-September

Ranked. Each maps to the §6 definition of production-ready in `CLAUDE.md`.

### 4.1 Per-call cost rollup — **do first, it is small and it gates pricing**

No COGS tracking exists anywhere in `app/obs/`. `duration_s` is already captured
on the `Call` model; the rollup is additive. Full spec:
**`docs/plan/COST_ROLLUP_SPEC.md`**.

Rough estimate for a 4-minute call across Twilio + AssemblyAI + ElevenLabs +
LLM: **£0.25–0.50**. At 300 calls/month that is £75–150 against £199 revenue.
At 500 calls it is potentially loss-making. These are guesses — the spread is
wide *because* they are guesses, which is the whole argument for measuring.

Blocks: §2.3 flat-rate pricing decision.

### 4.2 Converge to one engine

Not full runtime multi-tenancy — that is a large lift against a 24,820-line
`flow.py` and there is not six weeks of appetite for it. The 80% win:

> A single `release/cohort-1` branch, with clinic identity supplied by env var
> per Render service. Still one service per clinic (services are cheap), but
> **one codebase** — so a fix ships once.

Full self-serve tenancy is a post-cohort project. Do not attempt it now.

### 4.3 The carry-forward trap — **verified CLOSED 2026-08-06**

`STATUS.md` warns that FM-01 (books on "no"), FM-25 (write filler on "no") and
FM-23 (ungated cancel/reschedule) live only on the clinic engine lineage, and
that converging without porting them would silently re-break live patient lines.

**That warning is now stale. The gates are already everywhere.** All three
regression tests are byte-identical (md5) across `jv-v1-onboarding`,
`latency-eval`, `theorem-onboarding` and `vitaledge-onboarding`, and the guard
code is present in `flow.py` on all four.

| Gate test | jv-v1 | latency-eval | theorem | vitaledge |
|---|---|---|---|---|
| `test_book_affirmative_gate.py` | `0dc9ff54` | `0dc9ff54` | `0dc9ff54` | ✓ |
| `test_cancel_reschedule_gate.py` | `af338084` | `af338084` | `af338084` | ✓ |
| `test_write_ack_filler_gate.py` | `caf787d3` | `caf787d3` | `caf787d3` | ✓ |

Remaining obligation is only to **assert** this, not to port it: run the three
tests on `release/cohort-1` as a convergence gate so a regression can't slip in
unnoticed. Downgraded from "highest danger on the board" to a checklist item.

⚠️ `main` is the exception — it has none of the FM commits and its tip is
2026-07-24. See `BRANCH_CONVERGENCE_ANALYSIS.md` §5: **`main` is abandoned and
must not be a convergence base.**

### 4.4 OBS on every live clinic, same version

Bring `jv-v1-onboarding` up from 2 files to parity, and reconcile the 17/18/19
drift across the others. This is what makes the flywheel real.

### 4.5 Write and time the onboarding runbook

**Vital Edge moving off voicemail onto its own number is the first live test of
it.** Time it end to end.

That number sets the cohort cap more than anything else in this document. If
onboarding takes three days per clinic, six clinics is the whole of September.

### 4.6 Use JV as the pilot

JV has slipped with no hard deadline. That is a gift: a real clinic with low
time pressure. Migrate JV to the converged engine first.

---

## 5. Milestones

Gates are hard. Do not start a phase before its gate passes.

| Window | Work | Gate |
|---|---|---|
| **Wk 1** — Aug 6–14 | Cost rollup (§4.1) built + run against a month of Theorem/Vital Edge traffic. Pricing decision taken. | Real COGS distribution exists. Flat-vs-tiered decided. |
| **Wk 2–3** — Aug 17–28 | Carry-forward port (§4.3) → `release/cohort-1` convergence (§4.2). OBS parity (§4.4). | FM-01/25/23 verified present on converged engine. One codebase serving all clinics. |
| **Wk 4** — Aug 31–Sep 4 | JV migrated to converged engine (§4.6). Onboarding runbook written + timed. | JV live and clean on `release/cohort-1`. Per-clinic onboarding time is a known number. |
| **Wk 5** — Sep 7–11 | Vital Edge off voicemail onto own number, using the runbook. Webinar rehearsal. | Runbook survives contact with a second clinic. 3 consecutive clean rehearsal calls. |
| **Wk 6** — Sep 14–16 | **Freeze.** No changes after the last clean run. Webinar Wed 16th. | Recorded fallback demo in pocket. |

**Go/no-go on the webinar date: end of Week 3.** If convergence has not landed,
move the webinar to early October. Delay for capacity, never for polish.

---

## 6. The daily practice-call regime

10–15 calls/day is the right volume. Freeform calls decay fast — after ~2 weeks
of the same scenarios they teach nothing. Three rules:

1. **Fixed scenario bank with rotation.** Extend `JV_V1_8CALL_TEST_SUITE.md`.
   Rotate one adversarial variant in daily: interruptions, accents, background
   noise, mid-sentence hangups, callers who change their mind, callers who give
   a body part instead of a name.
2. **One defect log, not four.** Every defect logged once, fixed once on the
   converged engine, shipped with a regression test in `tests/regression/`.
   Convergence (§4.2) is what makes this possible at all.
3. **Review 100% of live calls, daily, while volume is low.** One real Theorem
   call is worth ten practice calls. This habit is cheap now and impossible
   later — build it while there are four clients.

**Track weekly: defects found per 10 calls.** When that flattens for two
consecutive weeks, the practice-call regime has stopped paying; shift the time
to onboarding prep.

---

## 7. Open items

- [ ] Confirm HOM genuinely takes no cut — get it in writing before the webinar.
- [ ] Set the exact webinar date with HOM (recommend Wed 16 September).
- [ ] Decide onboarding fee: £300 or £500.
- [ ] Decide flat vs tiered pricing — **blocked on §4.1**.
- [ ] `CLAUDE.md` is stale: describes the HOM meeting as upcoming end of July,
      treats `latency-eval` as a contested baseline, does not mention Theorem
      being live. It orients every future session — refresh it.
