# Jules hand-back — 29 Jul 2026 evening / 30 Jul morning

Against brief `JULES_BRIEF_2026-07-29.md`. Live build: Gate 5 class (`801152a`) + spoken obs (`4cb7273`), live since ~21:10 BST 29 Jul. Rollback if needed: Render `dep-d9k618bl550s73aortug` (`02b63a9`).

---

## 1 · Block 1 — Gate verdict

**PASS (Gate 5g).** Both V1 and V2 booked; both confirmation questions were complete (not truncated / emptied). No abandoned booking. **Do not roll back for Gate 5g.**

| SID | Case | Start (UK) | Outcome | Verbatim confirm (obs) |
|---|---|---|---|---|
| `CA3264ed4bbce3bd62693b3a3a241673e4` | V1 clean | 07:01 | **booked** Tue 4 Aug 17:00 · event `cspacq9n…` · Tom Green | “shall I go ahead and book that in?” — complete question |
| `CA5c4fb14fb555756f3f64952ad945788d` | V2 change-mind | 07:06 | **booked** Wed 5 Aug 19:00 · event `r51k9sb9…` · John Smith | First: “…Tuesday the 4th… quarter to six… shall I go ahead…?” · After change-mind: “…Tuesday the 4th… seven… shall I go ahead…?” — both complete |

`false_confirm_fired` = 0 on both.

### Obs vs heard

| SID | Result |
|---|---|
| V1 | Jules heard ≈ “Should I go ahead and book that in?” · obs “shall I go ahead and book that in?” — **match**, not truncated. Supports `4cb7273` on this path. |
| V2 | **Not separately written up by Jules ear vs obs in this session.** Treat as untested for the spoken-store check unless Jules confirms. |

### Cancel test bookings

Brief requires cancel before finish. **Jules has no Acuity access** this session — Demo bookings land on **Susie Demo Google Calendar** (same place John Smith was verified). Cancel there if edit access; else flag for Quentin.

| Event | Calendar truth |
|---|---|
| John Smith `r51k9sb9…` | Was Wed 5 Aug 19:00 — **Jules canceled all Wednesday Demo appts** this session. |
| Tom Green V1 `cspacq9n…` | Expected Tue 4 Aug ~17:00 — cancel status unknown (not Wednesday). |
| Tom Green A2 `92v6vul…` | **Confirmed Sat 1 Aug 18:00–18:40** — still on calendar unless Jules cancels separately. |

---

## 2 · `detect_defects.py --since 2026-07-29T21:10`

2 calls (06:01Z–06:06Z). Exit 0.

| id | n | note |
|---|---|---|
| A1 | 0 | |
| A2 | **0** | **Detector miss** — see §5. Manual A2 on V2. |
| A3 | 0 | |
| A4 | 1 | V2 — 2× “shall I book” (change-mind → **LEGITIMATE** by design) |
| B1 | 1 | V2 — cauda screen on reason=`minor knee pain` |
| B2 | 0 | |

Script still buckets newest build as `b405017` — cutoffs do not include Gate 5 deploys; **ignore that label** for these SIDs.

---

## 3 · New finding — A2 on V2 (manual; detector silent)

| | |
|---|---|
| SID | `CA5c4fb14fb555756f3f64952ad945788d` |
| Spoken (confirm + post-book) | “Tuesday the 4th of August at seven in the evening” |
| `collected.selected_slot` | `2026-08-05T19:00:00` |
| Calendar (Demo) | **Wednesday, 5 August · 7:00–7:40pm** · John Smith — matches slot ISO |
| Caller intent after change-mind | Asked for a **Wednesday** slot; she re-offered **Tuesday** times verbally, then booked Wed ISO |

**Class:** wrong day-name spoken for a **correct** calendar date (classic A2 / Block 4 shape). Not a Gate 5g fail. Not a silent wrong-day booking.

Also process note (not Gate fail): after “actually… Wednesday”, re-offer stayed on Tuesday in speech.

---

## 4 · Block 2 — A4 classification (`--since 2026-07-25`)

**n=20** (not 19 — includes post-brief V2). Detector: >1 bot turn matching `shall i book|book that in|get that booked`.

| Bucket | n | Verdict for the register |
|---|---|---|
| **REAL** | **11** | Real defect class — mostly **yes → phone re-ask → shall-I-book again** |
| **LEGITIMATE** | **4** | Re-confirm after correction / incomplete details — withdraw from defect count |
| **AMBIGUOUS** | **5** | 3× detector false-positive phrasing; 2× unclear / stacked chaos |

**Bottom line:** the headline “20% A4” over-counts. After triage, **REAL ≈ 11/20**. Still the largest open behavioural class — driven by the phone-confirm / booking-confirm ping-pong, not by change-of-mind. Do **not** treat V2-style re-asks as the same bug.

### Full table

| SID | Bucket | One-line reason |
|---|---|---|
| `CA7efc2c8c37f4cb8efb3a38d7b246f9bc` | LEGITIMATE | Between asks caller said “number please” (wanted phone readback), not a booking yes |
| `CAfcec5a89b6a11fcec6071fc12f5907b7` | AMBIGUOUS | Detector FP — first hit is “Before I go ahead and book that in — 30 or 60?”; only one real confirm |
| `CAbb354b162d695f191e21e52fd481488d` | LEGITIMATE | Affirmed then surname still missing; re-confirm after “Tom Green” |
| `CA81de6e308ce6222eccf46d47388d7512` | AMBIGUOUS | Detector FP — first hit is name-collect “…to get that booked in?”; day change then one real confirm |
| `CA4f929d5e42908d481c5ac0aa9ead9141` | REAL | “please” after shall-I-book → phone re-ask → shall-I-book again |
| `CA44b1b076f739fb547f3fd887df8a4c85` | REAL | “yes please” → phone re-ask → shall-I-book again |
| `CAc907f7060ad11b29fb3a314786a773ad` | REAL | “yes please” → phone re-ask → identical shall-I-book again |
| `CAe2af4ac70d6a5657796d088d0c1328a3` | REAL | “please” → phone → shall-I again → surname chase → third shall-I |
| `CAb4ff64d9bfce5aa3269b72dcade4ddca` | AMBIGUOUS | Detector FP — first hit is cauda “Before we go ahead and book that in…”; one real confirm |
| `CAdb298edc6b6d702388b51960c37a90bd` | REAL | Four shall-I-books; garbled user turns (“open”, “hi hello”) but no new booking info |
| `CA76bc921fe665dbf01a75317913c87e01` | REAL | “yes please book me in” then later shall-I again after phone/A1 digression (no slot change) |
| `CAfe6a41626d0b69eb27f7869e0152c8ff` | LEGITIMATE | Name correction (“Come”→Tom) then surname — re-confirms warranted |
| `CAbad8422e3c5ece30b96e54225065341f` | AMBIGUOUS | Premature confirm before slot + surname + phone correction stack — not a clean yes→reask |
| `CA1235c2344654c95808a52b82ea20c2b3` | REAL | Keypad done → “yes please” → phone readback → shall-I-book again |
| `CA847e8406a2b2e8e620351ef452ed9c5f` | REAL | Soft yes → phone → shall-I again; third after surname chase |
| `CAa4942bcea465e89b9b45d9a3b9d9a03b` | REAL | Pathological 7× asks — includes re-ask after clear go-ahead and after “All booked” (early phone change alone would be LEGITIMATE) |
| `CA86e9a3f7192f3034bb5f8f55fc79353f` | AMBIGUOUS | Caller said “both” (unclear); re-ask looks like clarification, not ignoring a yes |
| `CA2f0b070761e2d98a0418c8f43035f3da` | REAL | “please” → phone re-ask → shall-I again (A1 leak on second confirm turn) |
| `CAc64a05f1075555a564fd2ac8d5ab2684` | REAL | “book me” → phone re-ask → later shall-I again (also wrong-slot smoke01 — separate from A4) |
| `CA5c4fb14fb555756f3f64952ad945788d` | LEGITIMATE | V2 — caller asked for a different/Wednesday slot; re-confirm after new time |

---

## 5 · Block 4 — A2 diagnostic (no fix)

### (1) Who generates the day-name?

**Model free-text — then we capture and re-inject the wrong string.**

| Layer | What happens | Where |
|---|---|---|
| Slot *offers* | We build `day_label` from the real datetime (`strftime("%A")` + ordinal). Format like `Thursday 26th March`. | `app/tools/receptionist_tools.py` ~247–248 |
| Name-request / confirm speech | Model writes “`<weekday> the <ordinal> of <month>`” under prompt ABSOLUTE DATE FORMAT. **Not** derived from `selected_slot` ISO. | Prompt + LLM output |
| Capture | We slice the model’s own name-request readback into `v3_confirmed_slot_phrase`. | `app/media_streams/connection.py` **9233–9278** |
| Later confirms | Backstop tells the model to say that phrase **verbatim**. | `app/media_streams/llm_stream.py` **1774–1790** |
| Gate “date enforcement” | Once `phone_confirmed`, replaces spoken dates with the date **inside** `v3_confirmed_slot_phrase` — so a wrong weekday in the phrase is **copied forward**, not corrected from ISO. | `app/media_streams/turn_handler.py` **696–722** (matches brief) |

**Fix class (for Quentin):** gate/enforcement — build the spoken phrase from `selected_slot` ISO (or replace weekday from date), do not trust model weekday. Not a `strftime` bug in `day_label`.

### (2) Calendar on the date or the day-name?

| SID | Spoke | `selected_slot` (obs) | Booked in obs | Calendar truth |
|---|---|---|---|---|
| `CAcd8b36e198aa6ddb8befb1be3a2175c0` | “Wednesday the 30th of July” @ 17:30 | `2026-07-30T17:30` = **Thursday** | `booking_confirmed=False`, **no** `calendar_event_id` (said “All booked” anyway) | **Unknown from obs** — Jules: search Demo calendar for Tom Green ~30 Jul 17:30 if still present |
| `CAfe6a41626d0b69eb27f7869e0152c8ff` | “Friday the 1st of August” @ 18:00 | `2026-08-01T18:00` = **Saturday** | Yes · event `92v6vulnq5rvis6a7j6eigknpo` | **Confirmed by Jules:** Demo calendar **Sat 1 Aug 18:00–18:40** Tom Green · Bolton · +33617769867 — **on the date**, wrong day-name spoken (same class as V2). |
| `CAaf76d3b0983e516d95d4a7c7d624f064` | “Saturday the 9th of August” (offer to check) | none | No | Exploratory speech only — 9 Aug 2026 is **Sunday**; never reached post-book confirm |
| `CA5c4fb14fb555756f3f64952ad945788d` (V2) | “Tuesday the 4th… seven” | `2026-08-05T19:00` = **Wednesday** | Yes · `r51k9sb9…` | **Confirmed:** Demo calendar **Wed 5 Aug 19:00** — **on the date**, wrong day-name spoken |

**Answer to brief Q2:** when a real event exists and was checked (**V2** and **`CAfe6a41`**), the booking is on the **ISO date**; the caller heard the wrong **weekday**.

---

## 6 · Blocks not finished

| Block | Status |
|---|---|
| **2 — A4 classify** | **Done** |
| **3 — A1 residual / `_SELF_NARRATION_RE` / blast audit** | **Not started.** No commit. |
| **4 — A2 diagnostic** | **Done.** (1) model free-text + phrase copy. (2) V2 + `CAfe6a41` both **on the date**. `CAcd8b36` no event in obs. |

---

## 7 · Git / deploy

- **No further deploys** this session (brief rule; Quentin written OK required).
- **No A2/A4 code fixes** this session (brief: evidence only).
- Live commits of interest remain `801152a` (Gate 5g / A1 class) and `4cb7273` (spoken obs).

---

## 8 · Session close

- **Block 3 (A1 residual) not started** — deferred; no commit/deploy this session (brief: Quentin written OK required).
- Handback is evidence-complete for Quentin: Gate 5g PASS · A4 triage · A2 cause + calendar-on-date.
- Optional cleanup: cancel Sat 1 Aug Tom Green `92v6vul…` and any remaining Tue V1 Tom Green if still present.
