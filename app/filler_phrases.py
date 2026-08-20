# app/filler_phrases.py
"""
Filler phrases played concurrently during API latency (availability check,
booking write) so dead air doesn't sound like a crash.

Two lists:
  - THINKING_FILLERS_PRIMARY   — played when check_availability is called
  - THINKING_FILLERS_SECONDARY — played if the API takes > 4 seconds
  - BOOKING_WRITE_FILLERS      — played when book_appointment is called
  - LOOKUP_FILLERS             — played when lookup_patient is called
  - CALLBACK_FILLERS           — played when request_callback is called
  - WAITLIST_FILLERS           — played when add_to_waitlist is called

Usage:
    from app.filler_phrases import with_filler, THINKING_FILLERS_PRIMARY, BOOKING_WRITE_FILLERS
    result = await with_filler(executor(args, session), THINKING_FILLERS_PRIMARY, session, tts_fn)
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Callable, Coroutine, List, Optional

logger = logging.getLogger(__name__)

_MAX_FILLER_CHARS = 400


# ── Filler cooldown (CA8cf0aaea, 2026-08-05) ─────────────────────────────────
# Three independent producers queue hold phrases and none of them knew about
# the others, so on a reschedule the caller heard, in 3.4 seconds:
#
#   15:13:33.278  "Let me just check that…"        connection.py, phone confirm
#   15:13:35.094  "One moment…"                    llm_stream.py, background ack
#   15:13:36.727  "Let me bring that up for you…"  filler_phrases.py, tool call
#
# and at the write, 1.1 s apart, two phrases saying the same thing:
#
#   15:14:42.691  "Just moving that for you now…"
#   15:14:43.810  "No problem at all — I'm moving that for you now…"
#
# A cancellation path already exists (`_ack_filler_cancelled`) but it only
# helps when the ack filler has not yet reached ElevenLabs. By the time a tool
# call is detected it usually has, so the cancel is a no-op and both play.
#
# This is the shared clock those producers were missing. Deliberately a
# COOLDOWN and not a per-turn budget: silence is the worse failure, and a turn
# that genuinely stalls must still be able to speak again.
FILLER_COOLDOWN_S = 3.0


def should_play_filler(session: dict, *, is_write: bool = False) -> bool:
    """False when another filler played too recently to add a second one.

    A write filler is exempt from the FIRST suppression — the calendar
    round-trip after a caller says "yes, go ahead" is the most anxious silence
    in the call and must always be covered (B-40, an 11.1 s measured stall).
    It is not exempt from a second write filler, which is the duplicate pair
    above.
    """
    last_ts = session.get("_last_filler_ts")
    if last_ts is None:
        return True
    if (time.monotonic() - last_ts) >= FILLER_COOLDOWN_S:
        return True
    if is_write and not session.get("_last_filler_was_write"):
        return True
    return False


def note_filler_played(session: dict, *, is_write: bool = False) -> None:
    """Record that a filler reached the TTS queue. Call at EVERY producer."""
    session["_last_filler_ts"] = time.monotonic()
    session["_last_filler_was_write"] = bool(is_write)


_WRITE_FILLER_MARKERS = (
    "locking that in",
    "moving that for you",
    "booked in for you",
    "popping that in the diary",
)


def is_write_filler(text: str) -> bool:
    """Matches the same phrases connection.py arms its barge-in guard on."""
    low = (text or "").lower()
    return any(m in low for m in _WRITE_FILLER_MARKERS)

THINKING_FILLERS_PRIMARY: List[str] = [
    "Let me have a look at what we've got…",
    "One moment while I check that for you…",
    "Just checking the diary now…",
    "I'll take a look at the schedule for you…",
    "Let me pull that up now…",
    "Checking what's free for you…",
    "Let me see what we have available…",
]

THINKING_FILLERS_SECONDARY: List[str] = [
    "Nearly there…",
    # Was "Just a moment longer…" — `config.SILENCE_RULE` bans "just a moment"
    # and `turn_handler` strips it from model speech, so this list was quietly
    # exempt from a rule the rest of the engine enforces.
    "Won't be long now…",
    "Almost got it…",
]

# lookup_patient is used both to FIND an appointment and while acting on one
# (cancel / reschedule confirmation). These must read sensibly in BOTH cases,
# so they avoid "checking the diary / what's free" phrasing (which is wrong once
# the appointment is already found and we're cancelling it) — P17.
#
# 2026-08-05, owner instruction: this is the wait a caller hears when they have
# rung to CANCEL or MOVE something, which is the most anxious moment of any call
# on this system — they are worried about a fee, or about being told no. The
# wording should sound understanding rather than like a hold message.
#
# "Bear with me just a moment…" is gone, and it should never have been here:
# `config.SILENCE_RULE` bans that exact phrase and `turn_handler` strips it out
# of model speech, so a deterministic filler was the one path by which the
# caller could still hear the phrase the engine forbids everywhere else.
LOOKUP_FILLERS: List[str] = [
    "No problem at all — let me find that for you…",
    "Of course — just pulling your appointment up…",
    "Let me bring that up for you…",
    "One moment while I find that for you…",
    "Let me take a look for you…",
]

# request_callback and add_to_waitlist used to draw from LOOKUP_FILLERS, which
# was never a decision — 34becd6 added the tool and copied lookup_patient's
# mapping. Every phrase in that list is about an appointment the caller already
# has, and neither of these tools is.
#
# Live on Vital Edge, CAa0f76e2c2851f9eb3f28eddc38b75e3b (2026-08-20 15:32:09):
# Ray rang to ask for Jonathan, had never booked anything, and heard "Of course
# — just pulling your appointment up…" while the owner was texted.
#
# Two rules these have to keep, both learned by the sibling lists above:
#
#   * Describe the action, never the outcome. `with_filler` queues the primary
#     phrase BEFORE awaiting the executor, so a phrase claiming the clinic has
#     been told is spoken even when the executor refuses — which the phone
#     gate in `_exec_request_callback` now does. "I'll let them know" would be
#     BOOKING_WRITE_FILLERS' "Getting that all booked in for you…" again.
#   * Say nothing the caller has to already have. A callback and a waitlist
#     both start from nothing on file, so no phrase may reference a booking,
#     an appointment, or the diary.
#
# Checked against `turn_handler._BANNED_SENTENCE_RE` before landing: a filler is
# queued straight to TTS and never passes through `sanitise_response`, so that
# list does not protect these — it has to be done by hand, here.
CALLBACK_FILLERS: List[str] = [
    "Taking those details down for you…",
    "Let me get that message over for you…",
    "Getting those details across now…",
]

WAITLIST_FILLERS: List[str] = [
    "Taking those details down for you…",
    "Let me get you onto that list…",
    "Adding those details now…",
]

BOOKING_WRITE_FILLERS: List[str] = [
    # Was "Getting that all booked in for you…", which Gate 5f's real claim
    # detector reads as a COMPLETED booking — while its two siblings do not. A
    # filler is queued straight to TTS and never passes through
    # `sanitise_response`, so the detector never saw it; the caller heard it
    # before the write had returned, and heard it again if the write then failed.
    "Just getting that into the diary for you…",
    "Just locking that in now…",
    "Popping that in the diary…",
]

# The write turns for the two flows a caller frets about. Before this they had
# no filler at all — `_FILLER_TOOLS` covered availability, booking and lookup
# only — so the caller who had just agreed to a cancellation heard nothing while
# the calendar call ran. `B-40` measured 11.1 s of that on a live cancel.
#
# Reassuring, and TRUE even if the provider call then fails: they describe the
# action being taken, not an outcome. They cannot be spoken over a REFUSED
# write, because every write gate returns before the executor — and therefore
# before the filler — is ever reached.
CANCEL_WRITE_FILLERS: List[str] = [
    "No problem at all — I'm taking care of that for you now…",
    "That's absolutely fine — sorting that for you now…",
    "Not to worry — doing that for you now…",
]

RESCHEDULE_WRITE_FILLERS: List[str] = [
    "No problem at all — I'm moving that for you now…",
    "Of course — getting that changed for you now…",
    "That's fine — shifting that across for you now…",
]


def confirm_write_filler(session: dict, caller_confirmed: bool) -> Optional[str]:
    """Return an action-acknowledging filler for the turn RIGHT AFTER the caller
    says "yes" to a booking or reschedule readback — or None.

    The generic ack fillers ("Give me a moment…", "Right with you…") are
    confusing at this exact moment: the caller has just confirmed, so a
    "please wait" phrase makes them think they weren't heard, and they speak
    again — which can re-open the confirmation (the Marcus spiral). Playing a
    write-acknowledging line instead leaves nothing to respond to.

    Detection is deliberately narrow: it keys off the previous assistant turn
    being the LOCKED confirm CTA ("book that in for you" / "move it for you").
    CANCEL is intentionally excluded — its go-ahead is the ambiguous
    reschedule-or-cancel retention question, and the cancel branch is designed
    to run with no readback/filler (a cancel readback loops; see prompt).

    FM-25 (2026-07-22 JV live call): the confirm CTA being the prior turn is
    necessary but NOT sufficient — ``caller_confirmed`` must be True (the caller
    actually said a clear yes). Otherwise a "no"/ambiguous reply hears "Just
    locking that in now…" and believes they were booked against their wishes.
    Mirrors the FM-01 book-gate: verify consent, not just that the CTA was asked.
    """
    if not caller_confirmed:
        return None
    last = ""
    for _m in reversed(session.get("conversation_history") or []):
        if _m.get("role") == "assistant":
            last = (_m.get("content") or "").lower()
            break
    if not last:
        return None
    if "book that in for you" in last or "book that in" in last:
        return "Just locking that in now…"
    if "move it for you" in last or "move that" in last:
        return "Just moving that for you now…"
    return None


def pick_filler(filler_list: List[str], used: list) -> str:
    """
    Pick an unused filler phrase from filler_list.

    If all phrases have been used, resets the used pool and picks again
    so fillers never run dry.

    Args:
        filler_list: Pool of candidate phrases.
        used:        List of already-used phrases this call (mutable — modified
                     in place).  Stored as a JSON-serialisable list in session.

    Returns:
        A phrase no longer than _MAX_FILLER_CHARS characters.
    """
    available = [f for f in filler_list if f not in used]
    if not available:
        used.clear()
        available = list(filler_list)
    choice = random.choice(available)
    used.append(choice)
    # ElevenLabs 400-char safety gate
    if len(choice) > _MAX_FILLER_CHARS:
        choice = choice[:_MAX_FILLER_CHARS]
    return choice


async def with_filler(
    api_coro: Coroutine,
    filler_list: List[str],
    session: dict,
    tts_fn: Callable[[str], Coroutine],
    skip_primary: bool = False,
) -> Any:
    """
    Run api_coro concurrently with a filler phrase on the TTS queue.

    - Picks a primary filler and puts it on the TTS queue immediately.
    - Awaits the API coroutine with a 4-second timeout.
    - If the API hasn't finished in 4s, picks a secondary filler and queues it.
    - Waits for the API to complete, then returns its result.
    - If the API raises an exception, re-raises it after the filler completes.

    Args:
        api_coro:    The slow API coroutine (e.g. check_availability(args, session)).
        filler_list: Primary filler list to draw from.
        session:     Call session dict — must contain "used_fillers" (list).
        tts_fn:      Async callable (text: str) -> coroutine — queues TTS text.
        skip_primary: Suppress the opening phrase because the caller has ALREADY
                     heard one — FillerGuard's pre-recorded clip. The 4-second
                     secondary is deliberately kept: the clips cover roughly the
                     first five seconds, and suppressing both would reopen the
                     dead air on a slow Acuity round-trip that O-4 closed.

    Returns:
        Whatever api_coro returns.
    """
    used = session.setdefault("used_fillers", [])
    # ── Reconcile, 2026-08-08 (Theorem -> Vital Edge port) ──────────────────
    # Two independent fixes for the same defect meet here: this branch
    # (Theorem 8ce4b74, the recorded clip already spoke) and the cooldown
    # clock below (latency-eval 265d95e, another producer spoke recently).
    # Both are kept. This one runs FIRST because FillerGuard does not call
    # note_filler_played -- so if the cooldown ran first and the clip were
    # ever wired into the clock, this branch's 4-second secondary would be
    # skipped entirely and the dead air O-4 closed would reopen.
    # FillerGuard has already spoken this turn. Its clip and this list say the
    # same thing in different words — "Let me have a look at what we've got…"
    # was verbatim THINKING_FILLERS_PRIMARY[0] — and because pick_filler()
    # chooses at random it could even say the identical sentence twice. On
    # CAa11b26a1 (2026-08-07 22:23:15–20) the caller heard four ways of saying
    # it inside 4.6s: clip, second clip, this phrase, then the slot preamble.
    #
    # Before O-4 landed, FillerGuard held b"" and this was the only voice in the
    # gap, which is why the redundancy was invisible until the clips shipped.
    if skip_primary:
        logger.info(
            "[filler] primary suppressed — FillerGuard clip already spoke this turn"
        )
        api_task = asyncio.create_task(api_coro)
        done, _pending = await asyncio.wait([api_task], timeout=4.0)
        if not done:
            secondary = pick_filler(THINKING_FILLERS_SECONDARY, used)
            logger.info("[filler] API slow (>4s) — secondary filler: %r", secondary)
            note_filler_played(session, is_write=is_write_filler(secondary))
            await tts_fn(secondary)
        return await api_task

    # Peek before picking: pick_filler mutates `used` in place, so choosing a
    # phrase we are about to suppress would burn it out of the rotation and
    # push the pool towards an early reset for nothing.
    _peek = pick_filler(filler_list, list(used))
    if not should_play_filler(session, is_write=is_write_filler(_peek)):
        logger.info(
            "[filler] suppressed by cooldown (%.1fs) — another filler is still "
            "in the caller's ear: %r", FILLER_COOLDOWN_S, _peek[:60],
        )
        return await api_coro

    filler_text = pick_filler(filler_list, used)

    # If an ack filler (background FILLER_PHRASE) was already queued for this
    # turn, cancel it in favour of this tool-call filler.  The tool filler
    # always wins: set _ack_filler_cancelled so _tts_loop silently drops the
    # marked ack-filler chunk before it reaches ElevenLabs.
    if session.get("_ack_filler_active"):
        session["_ack_filler_cancelled"] = True
        session["_ack_filler_active"]    = False
        logger.info(
            "[ms_tts] ack filler cancelled — tool call filler taking over: %r",
            filler_text[:60],
        )

    note_filler_played(session, is_write=is_write_filler(filler_text))
    # Queue primary filler immediately (non-blocking — returns once enqueued)
    filler_task = asyncio.create_task(tts_fn(filler_text))

    # Run the API coroutine with a 4-second "slow API" watch
    api_task = asyncio.create_task(api_coro)
    done, pending = await asyncio.wait([api_task], timeout=4.0)

    if not done:
        # API still running after 4s — play a secondary filler. Not gated on
        # the cooldown: four seconds of nothing is the failure this exists to
        # prevent, and 4s > FILLER_COOLDOWN_S anyway. It still records itself,
        # so whatever speaks next sees it.
        secondary = pick_filler(THINKING_FILLERS_SECONDARY, used)
        logger.info("[filler] API slow (>4s) — secondary filler: %r", secondary)
        note_filler_played(session, is_write=is_write_filler(secondary))
        await tts_fn(secondary)

    # Ensure primary filler task is done before we return.
    # filler_task is None when the cooldown suppressed it — there is nothing to
    # wait for, and shield(None) would raise.
    if filler_task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(filler_task), timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    # Collect API result — re-raise any exception
    if done:
        result = done.pop()
        if result.exception():
            raise result.exception()
        return result.result()
    else:
        # API was still running — await pending task now
        return await list(pending)[0]
