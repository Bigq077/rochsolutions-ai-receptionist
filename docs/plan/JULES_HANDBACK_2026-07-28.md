# Jules hand-back — 28 Jul 2026 (Tue night → Wed early)

Gate + clean runs on live `b405017`. No raw logs. For Quentin.

---

## 1 · Gate answer

**YES** — on live `b405017`, V3, V4 and V5 each offered two options and each booked.

## 2 · Gate calls (final build)

| SID | Case | Result | One sentence |
|---|---|---|---|
| `CAc6893381e4c0c4d223cc9b4842de58b1` | V3 | PASS | ASAP shape, two options, booked John Smith |
| `CA1119e592297a5ddedd4f17abbeaec6f5` | V4 | PASS | “anytime”, Number 1/2 only, booked Tom Green |
| `CA41ac1e38443a4d92e54b4ea154dcbd9c` | V5 | PASS | Thu 6 two spoken → later → booked unspoken 19:30 |

## 3 · Clean runs #2 / #3

| SID | Run | Booked | Stored name correct |
|---|---|---|---|
| `CA38281f8f4fa6c08d781f4dd7063ebb07` | clean #2 | y | y — `Tom Green` |
| `CAa0ddae86a1839bf757b523a12cddd159` | clean #3 | y | y — `Tom Green` (after “green”-only re-ask) |

## 4 · Mitigations

**All six held** on the clean runs:

- specific day → two options  
- offered time booked exactly  
- bare / recovered name matches  
- caller-ID accepted (no keypad)  
- `service == checked_service`  
- `collected.reason` populated  

## 5 · New findings (logged, not fixed)

| SID | Note |
|---|---|
| `CAb1aaa6cfbf81068caee8bc7a662a48cb` | ASAP V3 first dial offered Number 3 — redialled PASS |
| `CAdd3373ad0bc4404401b470c7c3dadb93` | Wide-open V4: one option first, then Number 1–3 after reject — redialled PASS |
| `CAa0ddae86a1839bf757b523a12cddd159` | Clean3 name stutter + spurious “check what's available” before confirm |

**2c fallback recording — DONE.**

| | |
|---|---|
| SID | `CAfd8014413cabf63c8e54ed09c634ca39` |
| Booked | y — Fri 7 Aug 18:00 · event `e5o2pe3t…` |
| Name | `Elliot Smith` |
| service = checked | y |
| Jules ear | penultimate “yes” felt ignored / she re-asked |
| Obs transcript | phone: “yes it is” → booking confirm: “yes please” → booked (no second ask visible) |

Likely either (a) the normal phone-yes → “shall I book that in?” two-step sounding like a re-ask, or (b) an STT/turn drop that never hit the transcript. **Usable as fallback audio if the recording itself sounds OK to Jules;** if the re-ask is audible and awkward, re-record one more clean pass.

## 6 · Git / deploy

- **Live:** `b405017`
- **Shipped (Quentin-authorised):**
  - `368b4e0` — unspoken follow-up (`available_days − last_offered`)
  - `b405017` — speech cap at two + `last_offered` synced to spoken subset
- **Do not ship** the speech cap without the follow-up — `076998b` alone failed V5 and was reverted
- **Rollback:** `2d553b6`

---

## Engineering note (for post-demo)

B1 (two-option readout) and V5 (book an unspoken time) are **separate layers**. Capping `available_days` in the tool return made the model treat the spoken list as the whole day. The fix that stuck: keep full availability in session/data; speak two; on “later” / a specific unspoken time, serve remaining **deterministically** from session — do not ask the model what exists.
