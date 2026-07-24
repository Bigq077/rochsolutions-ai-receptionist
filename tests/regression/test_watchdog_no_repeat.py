# tests/regression/test_watchdog_no_repeat.py
"""The no-input watchdog must not say the identical sentence to a caller twice.

Defect
------
The watchdog fires at most once per question generation
(`_no_input_reask_count` resets in `on_question_asked` and on every accepted
transcript), so it cannot repeat itself *within* a turn.  The repetition callers
actually hear arises ACROSS turns: the same question comes round again — because
their answer was not accepted — and the re-ask for it replays the same words.

On theorem_v3 this is the common path, because the FlowEngine state stays
"GREETING" for essentially the whole call (connection.py:3231), so most re-asks
land in the GREETING fallback branch that echoes `last_question` back.

Guard
-----
`SilenceHandler._spoken_reask_phrases` is a CALL-scoped set of every re-ask
already spoken.  When a replay branch would emit something already in it, the
phrase is swapped for a narrowing variant from `reask_variants`.

Scope is deliberately narrow and the guard is best-effort:
  * only replay branches are eligible — scripted branches (both location
    ladders, DTMF keypad prompts, name/phone/confirm scripts) always speak as
    written, because swapping a keypad instruction would strand the caller;
  * if no unused variant exists the original phrase is kept.  The guard may
    improve a re-ask; it must never block one.  Silence is worse than repetition.
"""

import asyncio
import time

import pytest

from app.media_streams.connection import _WATCHDOG_REASK_MARKER, SilenceHandler
from app.media_streams.reask_variants import normalize_phrase, variant_for

TIMING_Q = "Did you have a particular day or time in mind?"


def _handler(session, queue):
    return SilenceHandler(
        tts_text_queue=queue,
        trigger_transfer_fn=lambda *a, **k: None,
        get_session=lambda: session,
    )


async def _fire_once(handler, timeout=4.0):
    """Arm the watchdog with an already-elapsed deadline and return its phrase.

    Three things are needed to reach Phase 4 immediately instead of sitting out
    a real 6-10 s grace:

      * the deadline is `max(armed_at, last_engagement_at, _watchdog_grace_until)`
        (connection.py:2959), so ALL THREE must be backdated — a past `armed_at`
        alone is masked by a fresh `last_engagement_at` set in __init__;
      * `q_gen=0` — the stale-question guard at connection.py:2990 explicitly
        exempts 0, and any other value aborts because `_q_gen` starts at 0;
      * `_tts_playing` False, or Phase 3 loops waiting for Susie to stop talking.
    """
    _past = time.time() - 60.0
    handler.last_engagement_at = _past
    handler._watchdog_grace_until = 0.0
    handler._tts_playing = False
    task = asyncio.create_task(handler._no_input_watchdog(_past, 0))
    handler._no_input_watchdog_task = task
    try:
        _queued = await asyncio.wait_for(
            handler._tts_text_queue.get(), timeout=timeout
        )
        # Re-ask copy is queued behind a control-char marker
        # (_WATCHDOG_REASK_MARKER) that downstream stages strip; tests assert on
        # the spoken words, so remove it here.
        return _queued.replace(_WATCHDOG_REASK_MARKER, "")
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def _greeting_session(last_question=TIMING_Q):
    """v3-shaped session: state stays GREETING, question echoed from last_question."""
    return {
        "state": "GREETING",
        "last_question": last_question,
        "last_bot_prompt": last_question,
    }


# ── the guard fires ───────────────────────────────────────────────────────────

async def test_replay_already_spoken_is_not_repeated():
    """A replay phrase already said this call must not be said again.

    This is the defect: caller is mis-heard, the same question comes round, and
    Susie reads the identical sentence back a second time.
    """
    session = _greeting_session()
    handler = _handler(session, asyncio.Queue())

    first = await _fire_once(handler)
    assert TIMING_Q.lower() in first.lower(), (
        f"expected the first re-ask to replay the question, got {first!r}"
    )

    # Same question comes round again — a fresh q_gen, counter reset.
    handler._no_input_reask_count = 0
    second = await _fire_once(handler)

    assert normalize_phrase(second) != normalize_phrase(first), (
        f"watchdog repeated itself verbatim: {second!r}"
    )


async def test_repeat_is_replaced_by_a_narrowing_variant():
    """The replacement narrows the question rather than restating it."""
    session = _greeting_session()
    handler = _handler(session, asyncio.Queue())
    handler._spoken_reask_phrases.add(
        normalize_phrase("Sorry, I didn't catch that. " + TIMING_Q)
    )

    phrase = await _fire_once(handler)
    assert normalize_phrase(phrase) == normalize_phrase(variant_for("timing", 2))


# ── the guard stays out of the way ────────────────────────────────────────────

async def test_first_replay_is_untouched():
    """A question asked once must still be replayed normally.

    The first re-ask replaying the question is correct — the caller may simply
    not have heard it.  If the guard fired here it would be a regression.
    """
    session = _greeting_session()
    handler = _handler(session, asyncio.Queue())

    phrase = await _fire_once(handler)
    assert TIMING_Q.lower() in phrase.lower()


async def test_scripted_branch_is_never_swapped():
    """ASK_LOCATION's approved copy must be spoken as written even if repeated.

    The location ladder and the DTMF prompts carry instructions the caller needs
    ("press 1 ... press 2").  Swapping one for a softer variant because it had
    been said before would strand the caller mid-escalation, so scripted
    branches are excluded from the guard entirely.
    """
    session = {"state": "ASK_LOCATION", "last_question": "Alcester or Redditch?"}
    handler = _handler(session, asyncio.Queue())

    first = await _fire_once(handler)
    # Pre-seed so the guard WOULD fire if this branch were eligible.
    handler._spoken_reask_phrases.add(normalize_phrase(first))
    handler._no_input_reask_count = 0
    session["location_retry_count"] = 0
    second = await _fire_once(handler)

    assert normalize_phrase(second) == normalize_phrase(first), (
        "ASK_LOCATION approved copy was swapped by the no-repeat guard — "
        f"{second!r}. Scripted branches must always speak as written."
    )


async def test_guard_never_blocks_a_reask():
    """With every variant exhausted, the watchdog still speaks.

    Best-effort contract: the guard may improve a phrase, never suppress one.
    A silent line is a worse outcome than a repeated sentence.
    """
    session = _greeting_session()
    handler = _handler(session, asyncio.Queue())
    # Poison the set with the replay AND every variant, leaving no escape.
    handler._spoken_reask_phrases.add(
        normalize_phrase("Sorry, I didn't catch that. " + TIMING_Q)
    )
    for arch in ("timing", "slot", "name", "phone", "confirm", "reason", "other"):
        handler._spoken_reask_phrases.add(normalize_phrase(variant_for(arch, 2)))

    phrase = await _fire_once(handler)
    assert phrase and phrase.strip(), "watchdog emitted nothing — guard blocked the re-ask"


# ── call-scoping is the point ─────────────────────────────────────────────────

async def test_set_is_call_scoped_not_per_question():
    """The history must survive a new question generation.

    A per-q_gen set would never hold more than one entry — the watchdog only
    fires once per q_gen — so it could never detect the cross-turn repeat this
    guard exists for. Pinning this stops a future 'reset it per question'
    cleanup from silently neutering the whole mechanism.
    """
    session = _greeting_session()
    handler = _handler(session, asyncio.Queue())

    await _fire_once(handler)
    assert handler._spoken_reask_phrases

    handler.on_question_asked(TIMING_Q)
    assert handler._spoken_reask_phrases, (
        "_spoken_reask_phrases was cleared on a new question — the guard can no "
        "longer detect the cross-turn repeat it exists to catch."
    )
