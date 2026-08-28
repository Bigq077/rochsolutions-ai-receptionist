"""Drive LLMStream.run_turn in-process: text in, spoken text out.

USAGE
-----
    diary = FakeDiary.weekly(start=NOW, days=14, times=["09:00", "14:00"])
    async with ConversationDriver(clinic_id="vital_edge", diary=diary) as call:
        turn = await call.say("Hi, I'd like to book a sports massage")
        assert "massage" in turn.spoken.lower()
        ...
        assert len(diary.bookings) == 1

WHAT IS REAL AND WHAT IS NOT
----------------------------
REAL: the system prompt, the model, the chunker, the fast path, the gates,
`_flush_slot_buf` and the whole offer-record reconciliation, every session
mutation, and the availability payload pipeline (see fake_clinic).

NOT REAL: STT, TTS audio, Twilio, Redis, and the calendar/SMS providers.

WHY save_session IS PATCHED RATHER THAN POINTED AT A FAKE REDIS
---------------------------------------------------------------
`save_session` is imported into llm_stream's own namespace at module import
(`from .session import save_session`), so patching `session.save_session` would
NOT be seen by run_turn. The patch has to be on the llm_stream attribute. This
is the same binding trap that made three separate SMS modules keep texting
during tests after `sms.send_sms` was patched.

THE TURN BOUNDARY
-----------------
In production the TTS loop drains `tts_text_queue` concurrently and can DROP
chunks (tts_inhibit, ack-filler cancel, pre-slot cancel, the dedup guard), and
`_record_spoken` runs before any of that - so the session's own record of what
was said is optimistic by design. This driver drains the queue AFTER run_turn
returns and reports every chunk, which models a call where nothing was
dropped. `Turn.spoken` is therefore what Susie TRIED to say. That is the right
default (barge-in is a separate concern with its own tests) but it must not be
read as proof the caller heard it.
"""
from __future__ import annotations

import asyncio
import dataclasses
import uuid
from contextlib import ExitStack
from datetime import datetime
from typing import Any, Dict, List, Optional
from unittest.mock import patch

from . import netfence
from .fake_clinic import FakeDiary, ToolCall


@dataclasses.dataclass
class Turn:
    """One caller utterance and everything the engine did with it."""
    said: str
    chunks: List[str]
    tools: List[ToolCall]
    suppressed: List[str] = dataclasses.field(default_factory=list)

    @property
    def spoken(self) -> str:
        """What the caller would have heard, after TTS-loop suppression."""
        return " ".join(c.strip() for c in self.chunks if c and c.strip())

    @property
    def generated(self) -> str:
        """Everything the model produced, including chunks the loop dropped.

        Use `spoken` for what the caller heard. Use this only when the question
        is what the MODEL did, e.g. proving a gate deleted a sentence rather
        than the model never writing it - a distinction that has cost four
        misaimed fixes here before.
        """
        parts = [c.strip() for c in (self.chunks + self.suppressed) if c and c.strip()]
        return " ".join(parts)

    def tool_names(self) -> List[str]:
        return [t.name for t in self.tools]

    def __repr__(self) -> str:  # keeps pytest -vv output readable
        return f"Turn(said={self.said!r}, spoken={self.spoken!r}, tools={self.tool_names()})"


class StubWebSocket:
    """`websocket` is never dereferenced in llm_stream - this exists so the
    parameter is not None if that ever changes, and so a stray call is loud."""

    def __init__(self) -> None:
        self.sent: List[Any] = []

    async def send_json(self, payload: Any) -> None:
        self.sent.append(payload)

    async def send_text(self, payload: Any) -> None:
        self.sent.append(payload)


class ConversationDriver:
    """One in-process call against the free-form turn loop."""

    def __init__(
        self,
        clinic_id: str,
        diary: Optional[FakeDiary] = None,
        twilio_from: str = "+447700900123",   # Ofcom reserved FICTITIOUS range
        twilio_to: Optional[str] = None,
        now: Optional[datetime] = None,
        initial: Optional[Dict[str, Any]] = None,
        allow_hosts: tuple = (),
    ) -> None:
        self.clinic_id = clinic_id
        self.diary = diary if diary is not None else FakeDiary()
        self.twilio_from = twilio_from
        self.twilio_to = twilio_to
        self.now = now
        self.initial = dict(initial or {})
        self.allow_hosts = allow_hosts

        self.call_sid = f"CA{uuid.uuid4().hex[:30]}"
        self.stream_sid = f"MZ{uuid.uuid4().hex[:30]}"
        self.session: Dict[str, Any] = {}
        self.turns: List[Turn] = []
        self.tool_calls: List[ToolCall] = []

        self._stack: Optional[ExitStack] = None
        self._llm = None
        self._ws = StubWebSocket()

    # -- lifecycle -----------------------------------------------------------

    async def __aenter__(self) -> "ConversationDriver":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    async def start(self) -> None:
        from app.media_streams import llm_stream as ls
        from app.media_streams.session import _fresh_session
        from app.tools import receptionist_tools as rt
        from .fake_clinic import build_tool_executors

        stack = ExitStack()
        self._stack = stack

        # Fence the network BEFORE anything else can dial out.
        netfence.install(stack, self.allow_hosts)

        fake_table = build_tool_executors(self.diary, self.tool_calls, now=self.now)

        # Fail loudly rather than silently letting a real executor run: an
        # unstubbed name would fall through to a provider.
        missing = set(getattr(rt, "TOOL_EXECUTORS", {}) or {}) - set(fake_table)
        if missing:
            stack.close()
            raise AssertionError(
                "ConversationDriver refuses to run: these real tools have no "
                f"stub and could reach a provider: {sorted(missing)}. Add them "
                "to fake_clinic.build_tool_executors."
            )

        stack.enter_context(patch.object(rt, "TOOL_EXECUTORS", fake_table))

        # Must patch the name llm_stream BOUND at import, not session's.
        async def _no_save(call_sid, session):
            return None

        stack.enter_context(patch.object(ls, "save_session", _no_save))

        # Build the session exactly as connection.py does for an inbound call.
        session = _fresh_session()
        session["call_sid"] = self.call_sid
        session["stream_sid"] = self.stream_sid
        session["ws_connected"] = True
        session["clinic_id"] = self.clinic_id
        if self.twilio_from:
            session["twilio_from"] = self.twilio_from
            if self.twilio_from.startswith("+44"):
                session["twilio_from_local"] = "0" + self.twilio_from[3:]
        if self.twilio_to:
            session["twilio_to"] = self.twilio_to
        session.update(self.initial)
        self.session = session

        self._llm = ls.LLMStream()

    async def stop(self) -> None:
        if self._stack is not None:
            self._stack.close()
            self._stack = None

    # -- the one method that matters ----------------------------------------

    async def say(self, text: str, timeout: float = 90.0) -> Turn:
        """Feed one caller utterance through the real turn loop."""
        if self._llm is None:
            raise RuntimeError("ConversationDriver.start() has not run")

        tts_q: asyncio.Queue = asyncio.Queue()
        audio_q: asyncio.Queue = asyncio.Queue()
        before = len(self.tool_calls)

        await asyncio.wait_for(
            self._llm.run_turn(
                text,
                self.session,
                self.call_sid,
                self.stream_sid,
                tts_q,
                audio_q,
                self._ws,
            ),
            timeout=timeout,
        )

        chunks, suppressed = self._drain(tts_q)
        turn = Turn(said=text, chunks=chunks, suppressed=suppressed,
                    tools=list(self.tool_calls[before:]))
        self.turns.append(turn)
        return turn

    def _drain(self, tts_q: asyncio.Queue):
        """Apply the TTS loop's own suppression rules to the queued chunks.

        Mirrors connection.py's _tts_loop for the PRE_SLOT marker: chunks are
        emitted speculatively while the model streams, and DROPPED wholesale
        once check_availability is detected mid-stream, so the caller never
        hears partial text ahead of the real slot data.

        Reporting those as spoken would make every availability turn claim
        speech the caller never got - the same optimistic-record error that
        `_record_spoken` has, and the reason `last_bot_prompt` once named a
        sentence nobody heard.

        NOT modelled here (they need live TTS-loop state, and none of them fire
        on the injected-text path): the confirmed-barge-in inhibit,
        _ack_filler_cancelled, and the consecutive-identical dedup guard.
        """
        from app.media_streams.llm_stream import PRE_SLOT_MARKER

        cancelled = bool(self.session.get("_pre_slot_cancelled"))
        chunks: List[str] = []
        suppressed: List[str] = []

        while not tts_q.empty():
            item = tts_q.get_nowait()
            if isinstance(item, dict):
                item = item.get("text")
            if not isinstance(item, str):
                continue

            if item.startswith(PRE_SLOT_MARKER):
                body = item[len(PRE_SLOT_MARKER):]
                if not body.strip():
                    continue
                (suppressed if cancelled else chunks).append(body)
                continue
            chunks.append(item)

        return chunks, suppressed

    # -- convenience ---------------------------------------------------------

    @property
    def transcript(self) -> str:
        lines = []
        for t in self.turns:
            lines.append(f"CALLER: {t.said}")
            if t.tool_names():
                lines.append(f"   [tools: {', '.join(t.tool_names())}]")
            lines.append(f"SUSIE:  {t.spoken}")
        return "\n".join(lines)
