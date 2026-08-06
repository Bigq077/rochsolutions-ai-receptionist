# Spec — per-call cost rollup

**Written:** 2026-08-06. **Blocks:** the flat-vs-tiered pricing decision in
`COHORT_1_PLAN.md` §2.3. **Branch:** built on `engine/converged`
(cut from `theorem-onboarding`).

> ## Status — code BUILT, rates UNFILLED
>
> Implemented 2026-08-06 and verified end-to-end on SQLite. 31 unit tests plus
> the existing capture suite pass (51 total).
>
> | File | State |
> |---|---|
> | `app/obs/cost.py` | Built. `RATES` are `None` placeholders. |
> | `app/obs/models.py` | `cost_pence` / `cost_breakdown` / `cost_version` added |
> | `app/obs/store.py` | `_cost_fields()` populates at capture, additive migration |
> | `app/obs/cost_report.py` | Built — per-clinic p50/p90/max, margin, break-even |
> | `scripts/backfill_call_costs.py` | Built — idempotent, `--dry-run`, `--force` |
> | `tests/obs/test_cost.py` | 31 tests |
>
> **The module deliberately refuses to produce a cost until `RATES` are filled.**
> `estimate_call_cost()` returns `total_pence: None` with
> `error: "rates_not_configured"`, and the backfill exits 2. A plausible-looking
> number derived from guessed rates is the specific failure this design prevents
> — see §5.
>
> **Next action is yours, not the code's:** pull one real month from the Twilio,
> AssemblyAI, ElevenLabs and LLM dashboards, fill `RATES`, set
> `RATE_TABLE_VERSION`, run the backfill, reconcile.
>
> ⚠️ The £0.25–0.50 per-call figure in §1 is **still an unvalidated guess.** A
> smoke run with illustrative rates produced ~10p for a 4-minute call, but on a
> short synthetic transcript with an estimated (not measured) LLM component. Do
> not quote either number until reconciliation is done.

---

## 1. Why

We are about to sell six clinics a flat £199/mo product whose cost of goods is
unmeasured. Four metered vendors bill per call: Twilio, AssemblyAI, ElevenLabs,
and the LLM.

Rough estimate for a 4-minute call: **£0.25–0.50**.

| Calls/month | Est. COGS | Margin on £199 |
|---|---|---|
| 100 | £25–50 | 75–87% |
| 300 | £75–150 | 25–62% |
| 500 | £125–250 | **−26% to 37%** |

The spread is wide *because these are guesses*. The busiest clinics are the most
likely to sign, the most visible in the HOM network, and the most likely to be
the reference customer — and they are exactly the ones a flat rate could make
loss-making. **Do not sell flat-rate pricing on an unmeasured cost base.**

---

## 2. What exists already

Grounded against `origin/theorem-onboarding` on 2026-08-06.

`app/obs/models.py` — `Call`, one row per completed call. Relevant existing
columns:

| Column | Type | Use here |
|---|---|---|
| `call_sid` | `String(64)` PK | Idempotent upsert key |
| `clinic_id` | `String(64)` indexed | Per-clinic rollup |
| `duration_s` | `Integer` | Twilio + AssemblyAI basis |
| `turn_count` | `Integer` | Sanity check on LLM calls |
| `transcript` | `JSON` — ordered `[{"role","text"}]` | ElevenLabs character count |
| `start_utc` | `DateTime(tz)` | Monthly bucketing |
| `raw` | `JSON` | Forward-compat catch-all |

`app/obs/store.py` — has an established **additive migration pattern**:
`_ADDED_COLUMNS: dict[str, str]` (name → DDL type) applied by
`_ensure_new_columns()` on `init_db()`, which skips columns that already exist
and logs failures without raising. New columns go through this. It runs on
SQLite and Postgres identically, which is why the model uses generic `JSON`.

`list_calls(since, until, clinic_id)` already gives the query surface the report
needs. No new query layer required.

`app/obs/rollup.py`, `reports.py`, `weekly.py` exist — the report belongs
alongside these, not as a standalone script.

---

## 3. Design

Three pieces. Keep them separate; only the first has any real logic.

### 3.1 `app/obs/cost.py` — new module, pure functions

No I/O, no DB, no network. Fully unit-testable.

```
RATE_TABLE_VERSION = "2026-08"

RATES = {
    "twilio_inbound_per_min":     ...,   # PSTN inbound
    "twilio_stream_per_min":      ...,   # Media Streams
    "assemblyai_per_hour":        ...,   # streaming ASR
    "elevenlabs_per_1k_chars":    ...,   # streaming TTS
    "llm_input_per_1m_tokens":    ...,
    "llm_output_per_1m_tokens":   ...,
    "usd_gbp":                    ...,   # FX, versioned with the table
}

def estimate_call_cost(
    duration_s: int | None,
    transcript: list[dict] | None,
    llm_usage: dict | None = None,
) -> dict:
    """Return {"total_pence": int, "breakdown": {...}, "version": RATE_TABLE_VERSION}"""
```

**Fill `RATES` from actual invoices, not published list prices.** List prices
ignore committed-use discounts, minimums, and rounding, and will be wrong in
both directions. Pull one real month from each of the four vendor dashboards.

Per-component rules:

- **Twilio** — bills per *started* minute. Use `ceil(duration_s / 60)`, not the
  raw seconds. Both inbound PSTN and Media Streams legs.
- **AssemblyAI** — priced on audio duration. `duration_s / 3600`. Note this is
  the *call* duration, which is longer than speech duration; that is correct,
  the stream is open the whole time.
- **ElevenLabs** — priced per character of synthesised text. Sum
  `len(t["text"])` over transcript turns where `role` is the assistant. Verify
  the exact role literal against `call_logger` before relying on it — do not
  assume `"assistant"`.
- **LLM** — **prefer real token counts.** There is already cost-adjacent code in
  `app/media_streams/llm_stream.py`; check whether it surfaces usage. If it
  does, thread it into the captured record and use it. Only fall back to a
  chars÷4 token estimate if it does not, and flag estimated rows so they can be
  excluded from the margin analysis.

### 3.2 Storage — three new columns on `calls`

Add via the existing `_ADDED_COLUMNS` mechanism in `store.py`:

| Column | DDL | Meaning |
|---|---|---|
| `cost_pence` | `INTEGER` | Total, GBP pence. Integer — no float money. |
| `cost_breakdown` | `JSON` | Per-vendor pence + whether LLM was estimated |
| `cost_version` | `VARCHAR(16)` | `RATE_TABLE_VERSION` used |

Populate in `_row_from_record()` at capture time. It is deterministic from data
already in hand, so there is no reason to defer it to the worker.

`cost_version` is what makes the numbers survive a price change: when rates
move, bump the version and recompute rather than silently mixing rate bases in
one average.

**Constraint: this must not be able to break capture.** Wrap the cost call so
any exception logs and leaves the three columns NULL. A pricing bug must never
cost us a call record. Follow the defensive style already used in
`_ensure_new_columns`.

### 3.3 `app/obs/cost_report.py` — the answer

Reads via `list_calls()`. Emits, per clinic per month:

- Call count, total COGS, **mean and p50/p90/max cost per call**
- Margin at £199 and at £109
- **Break-even call volume** — the number that decides flat vs tiered
- Count of rows with estimated (not measured) LLM cost

The distribution matters more than the mean. A clinic whose p90 call costs 3×
its median is a different pricing problem from one with a tight spread.

---

## 4. Backfill

`scripts/backfill_call_costs.py` — recompute over stored rows.

- Idempotent; re-runnable.
- `--since` / `--until` / `--clinic-id` to match `list_calls`.
- `--dry-run` prints the aggregate without writing.
- Recomputes rows whose `cost_version` differs from the current table.

Theorem and Vital Edge have real traffic to backfill against. That is the whole
point of the exercise — a month of real distribution beats any estimate.

---

## 5. Reconciliation — do not skip this

Computed totals will not match the invoices. Rounding, minimums, and free tiers
all bite.

After the first backfill, compare computed monthly total against the four actual
vendor invoices for the same month. If a component is off by more than ~10%,
fix the rate or the unit basis before trusting anything downstream. Record the
reconciliation in this file as a dated table.

An unreconciled cost model is worse than no cost model, because it will be
believed.

---

## 6. Tests

`tests/obs/test_cost.py` — pure, no DB:

- Known duration + transcript → known pence, hand-checked
- Twilio minute rounding: 61s bills 2 minutes, not 1.02
- `duration_s=None`, `transcript=None`, empty transcript → no crash, NULL cost
- Estimated vs measured LLM path both produce a valid breakdown
- Version stamped on every result

One store-level test that capture still succeeds when `estimate_call_cost`
raises. That is the regression that matters — everything else is arithmetic.

---

## 7. Definition of done

- [ ] `RATES` filled from real invoices, not list prices
- [ ] Costs captured on new calls, three columns populated
- [ ] Theorem + Vital Edge backfilled over ≥1 month
- [ ] Reconciled against all four invoices, within 10% per component
- [ ] Report shows p50/p90/max per call and break-even volume per clinic
- [ ] **Pricing decision taken and written into `COHORT_1_PLAN.md` §2.3**

The last line is the actual deliverable. The code is only how we get there.
