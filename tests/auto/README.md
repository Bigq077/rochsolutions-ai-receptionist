# Susie Automated Test Suite

Fully automated, no-human-required testing for the Susie AI receptionist voice pipeline.

Each test makes a real outbound call to Susie's demo number, plays pre-scripted patient
responses via ElevenLabs TTS, captures everything Susie says, and evaluates the
conversation with Claude — producing a pass/fail report.

---

## How It Works

```
run_tests.py
    │
    ├─ CallRunner          Makes Twilio outbound call
    │       │              Starts FastAPI webhook server
    │       │              Exposes webhook via ngrok
    │       │              Plays ElevenLabs TTS at each turn
    │       │              Captures Susie's speech (Twilio STT)
    │       └─ result dict
    │
    ├─ Transcript          Builds chronological readable transcript
    │
    ├─ Evaluator           Sends transcript + criteria to Claude
    │                      Returns pass/fail + reasons
    │
    ├─ Recorder            Saves result JSON, transcript, call recording
    │
    └─ Report              Builds pass/fail report across all scenarios
```

---

## Setup

### 1. Install dependencies

```bash
pip install twilio anthropic pyngrok fastapi uvicorn httpx elevenlabs
```

### 2. Set environment variables

```bash
# Twilio — make outbound calls
export TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
export TWILIO_AUTH_TOKEN=your_auth_token
export TWILIO_TEST_NUMBER=+441234567890   # Your Twilio number (must be UK for Susie)

# Anthropic — evaluates call transcripts with Claude
export ANTHROPIC_API_KEY=sk-ant-...

# ElevenLabs — generates realistic patient speech
export ELEVENLABS_API_KEY=your_elevenlabs_key
export ELEVENLABS_PATIENT_VOICE_ID=21m00Tcm4TlvDq8ikWAM   # optional, has default
```

### 3. ngrok auth (first time only)

```bash
ngrok authtoken YOUR_NGROK_TOKEN
```

---

## Running Tests

### Run all scenarios

```bash
python tests/auto/run_tests.py
```

### Run a specific phase

```bash
python tests/auto/run_tests.py --phase 2
python tests/auto/run_tests.py --phase 1 2   # multiple phases
```

### Run a specific scenario

```bash
python tests/auto/run_tests.py --scenario 2.1
python tests/auto/run_tests.py --scenario 2.1 5.2   # multiple scenarios
```

### Dry run — see what would be called without making calls

```bash
python tests/auto/run_tests.py --dry-run
```

### Skip Claude evaluation (faster, no pass/fail verdict)

```bash
python tests/auto/run_tests.py --no-eval
```

### Run with concurrency (2 calls at once)

```bash
python tests/auto/run_tests.py --concurrency 2
```

---

## Test Scenarios

| ID  | Name | Phase |
|-----|------|-------|
| 1.1 | Greeting Recognition | Phase 1: Basic Connection |
| 1.2 | Call Routing Confirmation | Phase 1: Basic Connection |
| 2.1 | Standard New Patient Booking | Phase 2: New Patient Booking |
| 2.2 | New Patient — Specific Doctor Request | Phase 2: New Patient Booking |
| 2.3 | New Patient — Urgent Appointment | Phase 2: New Patient Booking |
| 2.4 | New Patient — Asks About Services First | Phase 2: New Patient Booking |
| 3.1 | Existing Patient — Reschedule | Phase 3: Existing Patient Management |
| 3.2 | Existing Patient — Cancellation | Phase 3: Existing Patient Management |
| 3.3 | Existing Patient — Check Appointment Time | Phase 3: Existing Patient Management |
| 4.1 | Clinic Hours Inquiry | Phase 4: Information Requests |
| 4.2 | Services Available Inquiry | Phase 4: Information Requests |
| 4.3 | Insurance and Payment Inquiry | Phase 4: Information Requests |
| 4.4 | Prescription Refill Request | Phase 4: Information Requests |
| 5.1 | Confused Caller — Wrong Number | Phase 5: Edge Cases |
| 5.2 | Emergency Routing | Phase 5: Edge Cases |
| 5.3 | Caller With No Specific Need | Phase 5: Edge Cases |
| 5.4 | Caller With Hearing Difficulty | Phase 5: Edge Cases |
| 5.5 | Call That Ends Abruptly | Phase 5: Edge Cases |
| 5.6 | Caller Asks If Susie Is Human | Phase 5: Edge Cases |
| 6.1 | Booking Two Appointments | Phase 6: Multi-Turn Complexity |
| 6.2 | Complaint Then Booking | Phase 6: Multi-Turn Complexity |

---

## Results

All results are saved to `tests/auto/results/`:

```
results/
  result_2_1_20240317_143022.json      # raw call result + evaluation
  transcript_2_1_20240317_143022.txt   # human-readable transcript
  recording_2_1_20240317_143022.mp3    # Twilio call recording
  report_20240317_143100.txt           # full pass/fail report
  combined_20240317_143100.json        # all results in one file (for CI)
```

### Interpreting the report

```
============================================================
SUSIE AUTOMATED TEST REPORT
Generated: 2024-03-17T14:31:00
============================================================
TOTAL: 21 | PASS: 20 | FAIL: 1 | RATE: 95.2%
STATUS: ❌ NOT READY          ← needs 97% to be clinic ready

RESULTS BY PHASE:
----------------------------------------
Phase 2: New Patient Booking: 3/4
  ✅ Standard New Patient Booking
  ✅ New Patient — Specific Doctor Request
  ✅ New Patient — Urgent Appointment
  ❌ New Patient — Asks About Services First
     REASON: Susie did not answer the mental health services question
     DETAIL: ...

FAILURES REQUIRING FIXES:
----------------------------------------
• New Patient — Asks About Services First: Did not answer mental health services question
```

---

## Pass Threshold

**97%** — configured in `config.py` as `MIN_PASS_RATE`.

`run_tests.py` exits with code `1` if the pass rate is below this threshold,
making it suitable for use in CI/CD pipelines.

---

## Adding Scenarios

Edit `tests/auto/scenarios/all_scenarios.py`. Each scenario is a dict:

```python
{
    "id": "7.1",                          # unique, e.g. "phase.number"
    "name": "My New Scenario",
    "phase": "Phase 7: My New Phase",
    "description": "What this tests",
    "responses": [
        "First thing patient says after greeting",
        "Second thing patient says",
        "Third thing patient says",
    ],
    "pass_criteria": [
        "Susie does X",
        "Susie does not do Y",
        "Z is confirmed before call ends",
    ],
}
```

Then add it to `ALL_SCENARIOS` at the bottom of the file.

---

## Architecture Notes

- **Webhook server**: FastAPI + uvicorn, started in-process on a random port
- **Public URL**: ngrok tunnel (requires ngrok auth token)
- **TTS**: ElevenLabs `eleven_flash_v2_5` model — low latency
- **STT**: Twilio's enhanced speech recognition (en-GB)
- **Evaluator**: `claude-sonnet-4-6` with structured JSON output
- **Concurrency**: controlled by `--concurrency` (default 1 — sequential)
- **Audio files**: saved to `results/` and served from the webhook server
