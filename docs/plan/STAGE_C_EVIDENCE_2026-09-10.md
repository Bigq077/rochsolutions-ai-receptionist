# Stage C evidence — 2026-09-10 (T3)

> 🟢 **BOTH CODE ASKS IN THIS DOCUMENT WERE BUILT. Updated 12 Sep 2026.**
>
> * §1 / §5.2 — *"site B needs a failure line of its own"*: **done.** S-6 splits
>   *nothing parsed* (WARNING) from *already held* (INFO) at
>   [llm_stream.py:3990-4007](../../app/media_streams/llm_stream.py:3990). The
>   gate below can now mean what it says. **Note the line numbers in §1 and §2
>   have drifted**: `_record_stood_down_slots` is at **:3955**, not `:3717`.
> * §4 — *"a one-argument fix at the single-day call site"*: **done.**
>   [llm_stream.py:7712-7718](../../app/media_streams/llm_stream.py:7712) falls
>   back to `[first_day]`, so the B-95 presented-vs-bookable split is measurable
>   on single-day readouts too.
>
> **Still open from this document, and unchanged:**
> * §5.1 — the Render log grep for `could not resolve spoken option(s)` across
>   the clinic services. Never run.
> * §5's closing note — the population reaching **site A** still leaves no row,
>   so the ~900-line repair layer **cannot be retired** on a clean grep.
>
> A 12 Sep handover copied §1 and §4 forward as open work without re-grepping;
> see `DOC_AUDIT_2026-09-12_EVENING.md` §C1–C2 and README correction 33.

**Gate:** no `could not resolve spoken option(s)` on any clinic.
**Status (as written 10 Sep): NOT MET, and the gate as written cannot be met by
looking at that line alone.** The reasoning is below; the Render log check is a
morning job.

**Scope note.** This establishes which paths can still reach the reverse-parse,
from the code and from the corpus. It changes no engine code. The ~900-line
repair layer and its fifteen B-numbers are **not** touched — that is a separate
change with its own call gate.

---

## 1. The finding, first

There are **two** reverse-parse sites in `_flush_slot_buf`, not one, and only
one of them can produce the log line the gate is written against.

| | site | resolver | on failure |
|---|---|---|---|
| **A** | `llm_stream.py:4021` (section 3a) | `resolve_spoken_options` | logs `could not resolve spoken option(s)` |
| **B** | `llm_stream.py:3717` `_record_stood_down_slots` | `payload_slots_named_in` | **logs nothing** |

Site B returns silently when it resolves nothing: `_fresh` is empty and the
function returns, and "the sentence named no payload slots" is indistinguishable
in the log from "the sentence named slots I could not parse". Its whole body is
inside a `try/except Exception` whose handler logs only on a raise.

**So a green Render grep is necessary and not sufficient.** Site B can fail on
every call of the day and the gate still reads clean. If Stage C is to mean what
it says, site B needs a failure line of its own — one line, but on a live path,
so it has a call gate and is not a tonight job.

---

## 2. Which paths reach site A

`_flush_slot_buf` (`llm_stream.py:3567`) has four exits, in order. The first
three all `return` before section 3a:

1. **P6b stand-down** (`:3772`) — the model names the slot the caller just
   accepted. Calls `_record_stood_down_slots` (**site B**), speaks the model,
   returns.
2. **P6 stand-down** (`:3788`) — the model numbered no options and an offer is
   already on the table. Calls `_record_stood_down_slots` (**site B**), speaks
   the model, returns.
3. **Deterministic offer in force** (`:3846`) — `apply_offer_to_session` writes
   the record from the payload. **No reverse-parse at all.** This is the path
   Stage C exists to make universal.
4. **Everything else** — falls through to section 3a and **site A**.

So site A is reached on exactly one condition: **no deterministic offer was
built for this turn, and the model composed the readout itself.** Every other
path either records deterministically or goes through site B.

That is a narrower answer than "which paths can still reach the reverse-parse"
usually gets, and it is the useful one: **the way to close site A is not to
harden the parser, it is to build a deterministic offer on the turns that
currently have none.**

---

## 3. What the corpus says

120 calls carry `slot_offers`; between them **77 deterministic offers** were
built.

| | count |
|---|---:|
| offers **built** | 77 |
| of those, actually **spoken** | 67 |
| **built but never said** (P6/P6b stood them down) | **10 (13 %)** |
| numbered (`Number 1…`) | 76 of 77 |
| `mode = multi_day` | 53 |
| `mode = single_day` | 24 |

Per clinic: northgate 52 spoken / 9 not; theorem_v3 14 / 1; jv_v1 1 / 0;
vital_edge 0 recorded offers at all in this window.

**Two things follow.**

**(a) 13 % of built offers are discarded and replaced by model speech.** Each of
those ten went through site B — the reverse-parse with no failure signal. This
is the population Stage C's gate is blindest to, and it is not small.

**(b) `record_offer` fires where the offer is BUILT, not where it is spoken**
(`llm_stream.py:7353`, above both stand-down branches). Anything reading
`calls.slot_offers` as "what the caller heard" is wrong 13 % of the time. This
was a live defect in tonight's own replay harness and is fixed in `e6d986bf`;
any future harness over this column needs the same transcript cross-check.

---

## 4. A second gap, found on the way

`record_offer` takes `presented_days` and stores it, because — in its own
docstring — "the gap between them IS the `presented != bookable` split that
B-95 is about, and a harness that only saw one could not measure it."

Measured: **populated on all 53 `multi_day` offers, empty on all 24
`single_day` offers.** The single-day producer never passes it.

So the B-95 split is measurable on multi-day readouts and **structurally
invisible on single-day ones** — which is the mode a caller reaches by naming a
day, and therefore the mode the T1 exhibit was in. This is a one-argument fix at
the single-day call site, but it is on a live path, so it is written down here
rather than made tonight.

Related: [[availability-payload-total-days-is-not-days-found]] — the same
presented-vs-found split, in a different field, making a guard inert.

---

## 5. What the morning needs to do

1. **The Render log grep**, across all four clinics, for
   `could not resolve spoken option(s)`. That is the gate as written, and it
   still needs doing — but read §1 before scoring it, because a clean grep does
   not clear site B.
2. **Decide whether Stage C's gate is amended** to require a failure line at
   site B too. Recommended: yes. A gate that cannot observe half of what it
   covers is not a gate.

Do **not** delete the repair layer on the strength of a clean grep. Site A is
only reachable when no deterministic offer was built, and this corpus cannot
show how often that happens — `slot_offers` only records the turns where one
**was** built, so the population that reaches site A leaves no row. That
measurement needs its own instrumentation before the layer can be retired.
