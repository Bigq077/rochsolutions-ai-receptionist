"""
The FAQ re-queue must not inject a turn on top of one that is still speaking (T-17).

Observed on the first reschedule call ever attempted, 2026-08-05 00:08:43.

    00:08:43.638  [ms_gate5] turn complete
    00:08:43.639  synthesise_chunk: 'I can see an appointment on Wednesday the 12th…'
    00:08:43.676  FAQ loc Q answered, no TTS emitted this turn — queuing synthetic
                  'The Alcester clinic please'

TTS had been emitted 37 ms earlier. The synthetic transcript started a second
LLM turn on top of the one still speaking: lookup_patient ran twice, the
appointment was read out twice, "Let me pull that up for you now" and "Bear
with me just a moment…" piled in behind it, five `stale tts_finished ignored`
lines followed, and the caller hung up.

Root cause: the silence test read `_turn_speech_emitted`, which is maintained
ONLY by `_TrackedQueue` in flow.py. theorem_v3 returns before the FlowEngine
path — connection.py, "CRITICAL: do not fall through to FlowEngine path" — so
on this clinic the flag is reset to False every turn and never set back. The
clause was dead and the branch fired unconditionally.

The codebase already knew: a comment near the deferred-gate5 block says "the v3
tts_text_queue is a plain asyncio.Queue (not _TrackedQueue), so
_turn_speech_emitted is NOT used here". This site had simply never been told.

`_turn_real_tts` is the flag llm_stream maintains on this path — reset at the
top of run_turn, set the moment a chunk reaches the TTS queue.

Reachability note: theorem_v3 is the ONLY clinic with two sites. No other
deployment asks "which clinic?", so no other deployment can enter this branch.
Nothing to port from latency-eval — its reschedule works because it never runs
this code, not because it runs a better version.
"""

import inspect
import re

import pytest

from app.media_streams import connection as conn
from app.media_streams import llm_stream


@pytest.fixture(scope="module")
def requeue_block():
    """The FAQ follow-up re-queue, isolated."""
    src = inspect.getsource(conn)
    start = src.index("# ── FAQ follow-up re-queue")
    end = src.index("queuing synthetic", start)
    return src[start:end + 200]


# ── the regression ──────────────────────────────────────────────────────────

def test_guard_uses_the_flag_this_path_maintains(requeue_block):
    """THE regression."""
    assert '"_turn_real_tts"' in requeue_block, (
        "the re-queue silence test is not reading _turn_real_tts"
    )


def _code_only(block: str) -> str:
    """Strip comment lines.

    The fix is DOCUMENTED in a comment that names the old flag, so a naive
    substring check trips on the explanation rather than on the code.
    """
    lines = [
        ln for ln in block.splitlines() if not ln.lstrip().startswith("#")
    ]
    return "\n".join(lines)


def test_guard_no_longer_uses_the_flowengine_flag(requeue_block):
    """_turn_speech_emitted is permanently False on theorem_v3, so reading it
    here makes the guard a no-op and the branch unconditional."""
    code = _code_only(requeue_block)
    assert "_turn_speech_emitted" not in code, (
        "the re-queue is reading _turn_speech_emitted again — that flag is "
        "only maintained by _TrackedQueue on the FlowEngine path, which "
        "theorem_v3 never reaches, so the guard is dead"
    )
    assert '"_turn_real_tts"' in code, (
        "the live guard is not reading _turn_real_tts"
    )


# ── the flag it now depends on must keep its contract ───────────────────────

def test_turn_real_tts_is_reset_each_turn():
    """A latch that never resets would make the guard permanently True instead
    of permanently False — the same bug pointing the other way."""
    src = inspect.getsource(llm_stream)
    assert 'session["_turn_real_tts"]    = False' in src or \
           'session["_turn_real_tts"] = False' in src, (
        "_turn_real_tts is no longer reset at the start of a turn"
    )


def test_turn_real_tts_is_set_when_tts_is_emitted():
    """If nothing sets it True any more, the guard reverts to always-passing."""
    src = inspect.getsource(llm_stream)
    setters = len(re.findall(r'session\["_turn_real_tts"\]\s*=\s*True', src))
    assert setters >= 2, (
        f"only {setters} site(s) set _turn_real_tts True — the emit paths have "
        "changed and the silence guard may no longer see real speech"
    )


# ── the structural fact that makes this clinic-specific ─────────────────────

def test_theorem_v3_never_reaches_the_flowengine_path():
    """The reason the old flag was dead. If theorem_v3 ever starts using
    FlowEngine, revisit this whole area rather than assuming it still holds."""
    src = inspect.getsource(conn)
    assert "do not fall through to FlowEngine path" in src


def test_only_theorem_has_two_sites():
    """Why no other deployment can hit this, and why there was nothing to port
    from latency-eval. If a second multi-site clinic appears, every
    location-gate finding (T-16, T-17, the keypad ladder) applies to it too and
    none of it has been exercised anywhere else."""
    from app.clinic_config import CLINICS

    multi = []
    for cid, cfg in CLINICS.items():
        locs = (cfg or {}).get("locations") or []
        if hasattr(locs, "__len__") and len(locs) > 1:
            multi.append(cid)

    assert "theorem_v3" in multi, "theorem_v3 no longer has multiple locations"
    unexpected = {c for c in multi if not c.startswith("theorem")}
    assert not unexpected, (
        f"a new multi-site clinic exists: {sorted(unexpected)}. The whole "
        "location-gate surface — clinic question, keypad ladder, alias "
        "detector, this re-queue — has only ever run for Theorem."
    )
