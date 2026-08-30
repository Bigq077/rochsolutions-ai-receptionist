"""In-process driver for the FREE-FORM turn loop — the path every live clinic runs.

WHY THIS EXISTS
---------------
Every live clinic is free-form: connection.py's _llm_loop sees
is_freeform_clinic() and never falls through to the FlowEngine below. The
engine's real turn function is LLMStream.run_turn.

Before this package, `run_turn` had NO behavioural test. A grep for it across
tests/ and scripts/ returned ten hits and not one of them CALLED it — they are
all inspect.getsource() string pins, which pass when the code is deleted and
rewritten elsewhere and fail on harmless refactors. Meanwhile nine test files
DO drive FlowEngine in-process, and that turn loop is dead on every live
clinic. The suite drove the dead path.

The only harness that exercised the live path was tests/auto, and it drives a
DEPLOYED server over a websocket: it needs Render, spends real LLM tokens and
— per call_runner's own docstring — books into that service's real Acuity
calendar in BOTH of its modes. So it is neither free nor safe, and it has been
gated shut since 2026-07-23.

That is the whole reason bug-finding here has meant picking up a phone.

WHAT THIS IS
------------
run_turn turned out to be almost pure already:

  * `websocket` is never dereferenced anywhere in llm_stream
  * `audio_out_queue` appears only in signatures
  * `_flush_slot_buf` runs INSIDE the stream, so a queue drained afterwards
    still sees the real spoken text and the real offer-record reconciliation

So one turn is (user_text, session) -> TTS queue + session mutations, given a
stubbed tool table and a stubbed save_session. No Twilio, no ngrok, no deployed
server, no STT, no TTS, no calendar.

SAFETY
------
conftest.py runs load_dotenv(override=True), so live ACUITY_* creds are in the
environment on every pytest run whether you want them or not. Absence of
credentials is therefore NOT a safety property here and must never be relied
on. `netfence` blocks outbound HTTP to everything except the Anthropic API, and
the driver installs it unconditionally.
"""
