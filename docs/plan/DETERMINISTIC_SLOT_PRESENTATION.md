# Scope: build the slot sentence in Python, stop parsing it back

Written 31 Aug 2026, after `CA7e3ccfd4b4ee4cea0daf8d7b0099b47e` — a caller was
given four different answers to "what's available on Wednesday", three of them
opening "The available slots for Wednesday 9th September are", and abandoned.

**Not a proposal to redesign booking.** One seam moves: who writes the sentence.

---

## 1. The evidence this is a class, not a bug

`118 of the last 180 commits (66%) touch slot presentation` — 8 days.
B-78b, B-80, B-93, B-95, B-97, B-98, B-99, B-100, B-101, B-102, B-112, B-115,
B-116, B-125, B-126.

B-126 is the tell: a **guard corrupting correct model output**. That is what the
end of this road looks like, and it was found by the owner on a live line.

## 2. Root cause — three owners of one number, and a reverse-parse that fails

| owner | how many slots |
|---|---|
| `_MAX_PRESENTED_DAYS = 2` × `per_day = 1` | **2** |
| `MAX_SPOKEN_OPTIONS = 3` (enforced on assembled speech) | **3** |
| `SLOT_FORMATTER_SYSTEM_PROMPT` — "up to TWO times… per day", ≤3 days, "otherwise fall back to available_days" | **6** |

The model obeys the prompt, so `presented_days` is close to decorative. The
system then tries to learn what it said by parsing the sentence back into slots:

    resolve_spoken_options(["Monday 7th September — ten in the morning or five
                            in the evening", ...])  ->  []          # measured

It cannot parse `"day — time or time"`, the exact shape the prompt mandates.
Hence `slot buf: could not resolve spoken option(s)` on both Theorem calls on
31 Aug. Everything downstream — the guards, the "a few others that day" tail,
the DTMF map, B-98's band logic — then reads a record reconstructed from a
failed reverse-parse, or from `last_offered_slots`, which on multi_day is one
slot per day and positional by design.

## 3. The invariant that does not exist yet

> There is exactly ONE offer at any moment: a set of concrete slots, the exact
> sentence that named them, and the keypad map for it. Written once, by one
> function, at the moment the speech is produced. Everything else reads it.

Today "what was offered" is INFERRED at six sites. That is the whole defect
class.

## 4. What moves

`SLOT_FORMATTER_SYSTEM_PROMPT` is already a pure function — its own hard rules
say *"Output ONLY the spoken slot presentation. Never call any tool."* It takes
a payload and returns a sentence. **That is Python's job.**

New: `build_slot_offer(payload, session) -> SlotOffer` in `app/tools/slot_followup.py`

    SlotOffer:
        chunks:      list[str]   # TTS-ready, already split on Number boundaries
        slots:       list[dict]  # every slot NAMED, in spoken order — the record
        dtmf_map:    dict[str, str]
        more_times:  bool        # decided from the payload, never claimed

One function returns the speech AND the record, so they cannot disagree.

### Retired outright

`_flush_slot_buf` is 464 lines. Sections 3a, 3b, 3b-ii, 3c, 4, 5, 6 exist ONLY
to repair model-composed text:

| section | today | after |
|---|---|---|
| 3a cap + record what was read out | reverse-parse | returned by the formatter |
| 3b reconcile "a few others that day" | recompute vs payload | formatter emits it or doesn't |
| 3b-ii explain times outside the band | patch the sentence | formatter emits |
| 3c name further matching dates | patch the sentence | formatter emits |
| 4 slot-map extraction from the sentence | regex on speech | formatter returns the map |
| 5 re-split by "Number N" boundary | regex on speech | formatter emits chunks |
| 6 warn when chunks ≠ DTMF map | impossible to fix | impossible to occur |

Sections 1, 2, 7 (inhibit tracking) and 8 (send to TTS) survive.

In `slot_followup.py`, ~340 lines of reverse-parsing become dead:
`option_label_candidates` (45), `resolve_spoken_options` (36),
`resolve_all_spoken_times` (61), `cap_spoken_options` (36),
`extract_slot_options` (14), `reconcile_extra_slots_claim` (104),
`day_named_in_readout` (46).

`reconcile_readback_time` (158) STAYS — the read-back is still model-composed.
But it finally gets a true record to check against, which is what B-126 was.

**~900 lines retired. Net removal, not addition.**

## 5. Why this is also the latency fix

From the same call:

    turn_seq=12  path=slot_followup   content_ttfa_ms=144
    turn_seq=10  path=llm             content_ttfa_ms=5872  chunk_gate_ms=4025

**40x.** The buffer exists because the sentence must be complete before it can
be parsed. Nothing to parse, nothing to buffer. This is the single biggest item
against the 1.5s p95 bar in §6 of CLAUDE.md, and it is removal of work.

## 6. Precedent — the deterministic path is already live and already correct

`format_next_batch_speech` + `apply_next_batch_to_session` already do exactly
this for the FOLLOW-UP path: build the sentence and write the record in one
place. No B-number has ever been filed against that pairing. The work is to
extend it to the FIRST presentation, not to invent an approach.

## 7. Risk, and the honest limits

* **The sentence changes.** Deterministic prose is more repetitive than a
  model's. Mitigated by lifting the templates from
  `SLOT_FORMATTER_SYSTEM_PROMPT`'s own worked examples, which are what the
  model was copying anyway.
* **`tests/auto/scenarios/regressions/` pins presentation text** in ~30 files.
  These must be read, not bulk-edited — some pin a DEFECT's wording.
* **Corpus replay before any deploy.** Every stored availability payload gets
  run through `build_slot_offer` offline and the output diffed against what was
  actually spoken. That is a verification the current design cannot even
  express.
* **Not in scope:** the three-way cap disagreement is SETTLED by this change
  (one owner), but the chosen number is an owner decision — 2 days x 2 times is
  the proposal, matching what callers currently hear.

## 8. Order of work

1. `build_slot_offer` + unit tests, pure, no wiring.        <- START HERE
2. Corpus replay: every stored payload, diff vs spoken.
3. Wire single_day. Ship. Real call.
4. Wire multi_day. Ship. Real call.
5. Delete the formatter LLM call and the dead reverse-parsers.
6. Re-aim the pinned scenario tests.

Steps 3 and 4 ship separately and are independently revertible.
