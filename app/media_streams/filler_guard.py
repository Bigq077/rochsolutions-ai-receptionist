# app/media_streams/filler_guard.py
"""
FillerGuard — plays a short pre-synthesised µ-law clip on Acuity
availability turns only.

Lifecycle per turn
──────────────────
  1. arm(session)          — called immediately after STT delivers a
                             transcript and before the LLM task starts.
                             No-op unless session["booking_flow_active"] is True.
  2. cancel()              — called when the first audio chunk from the LLM
                             is about to reach the caller. Stops the timer if
                             it hasn't fired yet; if it already fired, does
                             nothing (interrupting short audio sounds worse).
  3. has_played            — True if the clip actually sent this turn. Caller
                             injects 100 ms of silence after the clip so the
                             LLM response doesn't start abruptly.

has_played and the internal task are reset at the top of every arm() call so
a fresh turn always starts clean.

Each clip is a POOL, not a file
───────────────────────────────
`clip_path` names the first member of a numbered set — `filler_checking.ulaw`,
`filler_checking_2.ulaw`, … — and one member is drawn per turn. A single file
meant every hold moment in every call was the same waveform: not merely the same
words but the same breath, the same stress, the same length, to the byte. Words
can repeat in a real conversation and pass unnoticed; an identical recording
cannot, and the second playing is what tells the caller they are talking to a
machine. Varying the wording alone would not fix it — two utterances of one
sentence by one person are never acoustically equal, so the rotation has to be
over recordings.

A pool of one is the old behaviour exactly, which is what a deploy that has not
yet regenerated its clips gets.
"""
from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path
from typing import Awaitable, Callable, List, Optional

logger = logging.getLogger(__name__)


def expect_slot_presentation(
    *,
    timing_preference_known: bool,
    slots_already_presented: bool,
    slot_map_active: bool,
    name_collection_pending: bool,
    phone_collection_active: bool,
    location_question_active: bool,
) -> bool:
    """
    True when this turn is the one moment the hold clip belongs to: the caller
    has given a day/time preference and Susie is about to fetch and read out the
    options.

    A pure predicate on purpose. The gate used to be an inline boolean at the
    arm() call site inside a 24k-line method, where the only way to check it was
    to place a live call — which is how it went four turns wrong before anyone
    noticed. Every argument is a plain bool so the caller owns reading the state
    and this owns the rule.

    ── Why this is now an ALLOW-list (CAd34a122247, 2026-08-08) ──────────────
    It used to be four exclusions and nothing else, which reads as "the moment
    is anything that is not one of these four stages". On a single-site clinic
    the location question auto-confirms at second one, so for most of a call
    NONE of the four were active and every turn qualified. The caller heard a
    hold phrase nine times in 123 seconds — on a 1.27s acknowledgement, on a
    price FAQ, on a phone confirmation, on "yeah 9 in the morning works".

    Timing every LLM round trip in that call shows why no exclusion list could
    have worked. Turn duration is bimodal with nothing in between: six turns
    took ONE model iteration (1.3-3.1s) and two took THREE (7.05s, 8.68s). One
    iteration costs ~2.3s all in. What separates the modes is whether a TOOL
    runs — a tool is two iterations minimum, so ~4.6s before the provider even
    answers. Stage is a proxy for that; a bare deny-list is a bad one.

    So the moment has to be asserted, not merely not-denied:

      timing_preference_known    a lookup needs something to look up. Before the
                                 caller has expressed any day/time there is
                                 nothing to fetch, so a hold phrase is covering
                                 a turn that was only ever going to be one
                                 model call.
      slots_already_presented    options are on the table; what follows is a
                                 readback, not a presentation. Distinct from
                                 slot_map_active, which is only True for the
                                 DTMF grid — on the call above slots were
                                 offered conversationally and the grid was never
                                 armed, so this was the state that mattered and
                                 nothing was reading it.

    Each exclusion still marks a stage where the moment has passed or not come:

      slot_map_active            the DTMF grid is up
      name_collection_pending    collecting the name (no tool at all)
      phone_collection_active    collecting the number (no tool at all)
      location_question_active   still choosing a clinic

    NOT a rule here, deliberately: "a tool will run". It cannot be known at
    350ms, and firing later does not help — tool detection lands 2.1-2.5s into
    the turn, by which point the caller has already had the silence the clip
    exists to prevent. Early on few turns beats late on many.
    """
    if not timing_preference_known:
        return False
    if slots_already_presented:
        return False
    return not (
        slot_map_active
        or name_collection_pending
        or phone_collection_active
        or location_question_active
    )


def discover_clip_pool(first: Path) -> List[Path]:
    """
    Every variant of `first` that exists on disk, in order.

    `audio_clips/filler_checking.ulaw` yields that file plus
    `filler_checking_2.ulaw`, `_3`, … stopping at the first gap. Ascending and
    contiguous rather than a glob: a glob would silently absorb any stray
    `filler_checking_old.ulaw` someone leaves in the directory and play it to a
    patient, and would order the pool by whatever the filesystem returns.
    Stopping at the first gap also means a half-finished regeneration — the
    script died after variant 3 of 5 — yields a working pool of 3 rather than a
    pool with a hole in it.

    Returns [] if `first` itself is missing; the guard reads that as "no clip"
    and disables itself, which is the pre-existing behaviour.
    """
    if not first.exists():
        return []
    pool = [first]
    n = 2
    while True:
        nxt = first.with_name(f"{first.stem}_{n}{first.suffix}")
        if not nxt.exists():
            return pool
        pool.append(nxt)
        n += 1


def next_clip_index(pool_size: int, last_index: Optional[int]) -> int:
    """
    Index of the clip to play, given what played last time on this call.

    Random, minus the one just heard. Plain `random.choice` over the whole pool
    would let a five-clip pool say the same sentence on two consecutive lookups
    roughly one turn in five — and back-to-back is the only repeat a caller
    actually registers as a repeat, which makes it the one case worth spending
    a rule on.

    Pure so the rotation can be proven without synthesising audio or placing a
    call. `pool_size <= 1` returns 0: a single-clip pool must keep working, or
    a deploy that has not regenerated its clips loses its filler entirely and
    the dead air O-4 closed comes back.
    """
    if pool_size <= 1:
        return 0
    if last_index is None:
        return random.randrange(pool_size)
    choices = [i for i in range(pool_size) if i != last_index]
    return random.choice(choices)


class FillerGuard:
    """
    Filler guard gated on booking_flow_active.

    Plays a primary clip after `delay_ms` if the LLM hasn't responded.
    If the LLM still hasn't responded `second_delay_ms` after the primary
    clip finishes, plays a second clip (falls back to the primary clip if
    no dedicated second clip is configured).

    arm()      — start (or restart) the delay timer.
    cancel()   — stop the timer; no-op if clip already fired.
    has_played — True if the primary clip was sent this turn.
    """

    def __init__(
        self,
        clip_path: Path,
        send_audio: Callable[[bytes], Awaitable[None]],
        clip_path_2: "Path | None" = None,
        second_delay_ms: int = 2500,
    ) -> None:
        # Each path names the FIRST member of a numbered pool — see
        # discover_clip_pool. A pool of one behaves exactly as the single clip
        # did before, so nothing here depends on the variants having been
        # generated yet.
        self._pool: List[bytes] = [p.read_bytes() for p in discover_clip_pool(clip_path)]
        if self._pool:
            logger.info(
                "[filler_guard] loaded %d clip(s) from %s (%d bytes total)",
                len(self._pool), clip_path.parent, sum(len(c) for c in self._pool),
            )
            if len(self._pool) == 1:
                logger.info(
                    "[filler_guard] only one primary clip — every hold moment in "
                    "every call will be the identical recording. Run "
                    "scripts/synthesise_filler.py to cut the variants."
                )
        else:
            logger.warning(
                "[filler_guard] clip not found: %s — filler disabled until clip is generated",
                clip_path,
            )

        # Second pool — falls back to the primary pool if not provided or missing
        self._pool_2: List[bytes] = (
            [p.read_bytes() for p in discover_clip_pool(clip_path_2)] if clip_path_2 else []
        )
        if not self._pool_2:
            # Reuse the primary pool; a repeated hold phrase beats silence
            self._pool_2 = self._pool
            if clip_path_2:
                logger.info(
                    "[filler_guard] second clip not found (%s) — will reuse primary",
                    clip_path_2,
                )

        self._second_delay_s = second_delay_ms / 1000.0
        self._send_audio = send_audio
        self._task: "asyncio.Task | None" = None
        self._played: bool = False
        # Which variant spoke last, per pool, for THIS call — the guard is
        # constructed per WebSocketCallHandler, so the no-repeat rule is scoped
        # to one caller's ear and does not need resetting between calls.
        self._last_idx: Optional[int] = None
        self._last_idx_2: Optional[int] = None

    async def arm(
        self,
        session: dict,
        delay_ms: int = 350,
        expect_lookup: bool = True,
    ) -> None:
        """
        Start the filler timer.

        Guards:
          - session["booking_flow_active"] must be True; otherwise no-op.
          - expect_lookup must be True; otherwise no-op.
          - clip must be loaded; otherwise no-op.

        `expect_lookup` is the caller's answer to "is this the turn where slots
        are about to be READ OUT?" — the caller has given a day/time preference
        and Susie is going to fetch the options. Owner decision 2026-08-08: that
        is the only moment in the whole call where this clip should fire.

        booking_flow_active alone is far too coarse: it stays True for the WHOLE
        booking, so the clip also fired on the name turn and the phone turn,
        which call no tool at all. On CA1e755281 (2026-08-07 23:43:21 and
        23:43:35) those turns answered in 2.5s and 1.9s — the clip is not
        covering dead air there, it is padding an already-fast turn, and the
        caller heard a hold phrase four times in a 90-second call.

        Note that "a tool will run" is NOT the rule. At 23:43:02 the caller
        narrowed to a specific time; that turn did call check_availability, but
        it ended in a readback and a name request, not a slot presentation, so
        the clip does not belong there either. Suppressing it is not silence:
        `_filler_clip_spoke_this_turn` stays False, so with_filler speaks its
        own TTS phrase at tool invocation.

        The decision is the CALLER's because the state it needs is not all in
        `session`: `post_slot_confirmation_pending` is an attribute on the
        connection, and `session["post_slot_confirmation_pending"]` is never
        assigned anywhere (the read at connection.py:10181 is always None).
        Deciding it here would silently read a key that does not exist.

        Cancels any in-flight timer from a previous arm() call and resets
        has_played so every turn starts with a clean slate.
        """
        # Always cancel any previous timer and reset state.
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None
        self._played = False

        # Every turn starts with the clip unspoken. `with_filler` reads this to
        # decide whether its own opening phrase would just be a second way of
        # saying what the caller already heard — see
        # `filler_phrases.with_filler(skip_primary=...)`. Reset here rather than
        # in _fire() so a turn where the clip never fires clears the previous
        # turn's flag.
        session["_filler_clip_spoke_this_turn"] = False

        # Gate 1: only fire on booking_flow_active turns.
        if not session.get("booking_flow_active"):
            return

        # Gate 2: only fire on turns that plausibly hit Acuity. See the
        # docstring — this is what stops the clip on name/phone collection.
        if not expect_lookup:
            logger.info(
                "[ms_filler] not armed — turn is not an availability lookup"
            )
            return

        # Gate 3: at most once per call. Owner rule — the recorded filler
        # belongs to the one moment before options are read out, and a normal
        # booking has one such moment.
        #
        # This is the gate that does not depend on getting the state read right.
        # On CAd34a122247 every check_availability was BLOCKED and retried, so
        # it returned no slots, so `slots_already_presented` never became True
        # and the stage exclusions had nothing to bite on — Susie read the
        # options out of her own text instead of a slot buffer. A predicate
        # built only from booking state inherits every bug in that state. A
        # latch does not: whatever else is wrong, the caller hears the clip
        # once.
        if session.get("_filler_clip_spoke_this_call"):
            logger.info(
                "[ms_filler] not armed — clip already spoke once this call"
            )
            return

        # Gate 4: clip must be present.
        if not self._pool:
            return

        # Draw this turn's variants at arm() time, not inside _fire(): a turn
        # whose LLM answers inside the delay is cancelled before it speaks, and
        # advancing the rotation for a clip nobody heard would let the next turn
        # play the one the caller last actually heard.
        _idx = next_clip_index(len(self._pool), self._last_idx)
        if self._pool_2 is self._pool:
            # No dedicated second pool, so both clips come out of one list. Avoid
            # the index just chosen rather than the one played on a previous
            # turn: these two play 2.5s apart in the SAME turn, and the identical
            # recording twice inside three seconds is the worst version of the
            # sameness this pool exists to break up.
            _idx_2 = next_clip_index(len(self._pool_2), _idx)
        else:
            _idx_2 = next_clip_index(len(self._pool_2), self._last_idx_2)

        _delay_s        = delay_ms / 1000.0
        _second_delay_s = self._second_delay_s
        _clip           = self._pool[_idx]
        _clip_2         = self._pool_2[_idx_2]
        _send           = self._send_audio
        _session        = session

        async def _fire() -> None:
            await asyncio.sleep(_delay_s)
            self._played = True
            self._last_idx = _idx
            # Set at the moment audio actually goes out, not at arm() time: a
            # turn whose LLM answers inside 350ms cancels this task before the
            # sleep returns, and nothing was spoken, so with_filler must still
            # be allowed its own phrase. The once-per-call latch is set here for
            # the same reason — a turn that never spoke must not spend the call's
            # one clip.
            _session["_filler_clip_spoke_this_turn"] = True
            _session["_filler_clip_spoke_this_call"] = True

            # Register in the SHARED cooldown clock. Without this the clip is
            # invisible to every other filler producer: on CAd34a122247 at
            # 08:37:06.969 the clip said "Let me just check that for you…" and
            # 1.46s later llm_stream's ack filler said "Right with you…" on top
            # of it. `should_play_filler` existed precisely to stop that and had
            # never been told the clip speaks. Imported here rather than at
            # module scope — app.filler_phrases is a sibling of the media_streams
            # package and a top-level import would make this module's import
            # order depend on it.
            try:
                from app.filler_phrases import note_filler_played

                # The wording matters as well as the fact: the clip SAYS "let
                # me just check that for you", so the model must not open its
                # reply with the same phrase 1-2s later. join_after_head reads
                # this to strip the duplicate. The text ends closed, like the
                # clip's own falling contour, so the reply is not decapitalised
                # to continue a sentence the audio already finished.
                note_filler_played(
                    _session,
                    is_write=False,
                    text="Let me just check that for you…",
                )
            except Exception:  # pragma: no cover - never break the clip on this
                logger.warning("[ms_filler] could not register in filler cooldown")
            logger.info(
                "[ms_filler] clip firing (delay=%dms, variant %d/%d)",
                delay_ms, _idx + 1, len(self._pool),
            )
            await _send(_clip)
            # Wait to see if the LLM responds; if not, play second clip.
            # cancel() will interrupt this sleep when the first TTS chunk arrives.
            await asyncio.sleep(_second_delay_s)
            self._last_idx_2 = _idx_2
            logger.info(
                "[ms_filler] second clip firing (variant %d/%d, still no LLM "
                "response after %.1fs)",
                _idx_2 + 1, len(self._pool_2), _second_delay_s,
            )
            await _send(_clip_2)

        self._task = asyncio.create_task(_fire(), name="ms_filler_guard")

    def cancel(self) -> None:
        """
        Cancel the pending timer.

        If the clip has already started playing, do nothing — interrupting
        a short clip sounds worse than letting it finish naturally.
        """
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    @property
    def has_played(self) -> bool:
        """True if the clip was actually sent this turn."""
        return self._played
