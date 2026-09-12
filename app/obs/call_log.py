"""
app/obs/call_log.py
-------------------
The call carries its own log.

Every record logged while a call is open is buffered under that call and
written -- redacted -- to the `call_logs` table on a timer and at teardown.
After this, the Render log is not the only place the decision lines exist:
`slot_guard REPLACED …`, `caller ACCEPTED …`, `WATCHDOG_FIRE`, the hold
heads, the `[LAT]` rows -- all of it is one `SELECT` away, per call, and
`LIKE` across the corpus.

Owner decision, 12 Sep 2026. Three demo calls that night carried five
distinct defects, and every one of them was diagnosable only from a pasted
Render log slice: obs held the OUTCOME (transcript, offers, pins) and the
log held the DECISION. This closes that gap without touching the ~2,000
log sites, by attribution rather than by edits.

How attribution works -- and why it is an object, not a string
----------------------------------------------------------------
The call's SID is not known when the pipeline's tasks are created: the
WebSocket handler spawns seven tasks and THEN Twilio's `start` event names
the call. A `ContextVar[str]` set at that point would be seen by the receive
loop alone, because every other task copied its context at creation.

So the ContextVar holds a mutable `CallLogContext`, created in `handle()`
BEFORE the tasks exist. All seven tasks -- and every task or thread they
spawn (`asyncio.create_task`, `asyncio.to_thread` both copy the context) --
hold a reference to the same object. When the SID arrives it is written into
that object, and the lines buffered before it was known are attributed with
the rest. Threads that were NOT started from inside the call (executors
created at import time) carry no context and their lines are not captured;
that is a known and bounded gap, not a leak between calls.

Never on the hot path
---------------------
`emit` appends a formatted string to a list under a lock and returns. The
DB write happens in a worker thread (`asyncio.to_thread`), on a timer
(OBS_LOG_FLUSH_SEC) and once at the end. A DB failure is logged and
swallowed; the buffer is kept for the next attempt. Nothing here can end a
call or delay a turn.

Redaction
---------
`app.obs.redact.redact_text` runs over every line at flush: phone numbers,
e-mails, and -- at the final flush, when it is known -- the caller's
collected name. Call and stream SIDs (`CA…`, `MZ…`) are protected first,
because the phone regex treats a 6-digit run inside a hex SID as a number and
would make the column unsearchable by the one key that matters.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

_log = logging.getLogger(__name__)

#: The Render log's own format, so a stored line and a pasted line are the
#: same bytes. Read from main.py's basicConfig rather than duplicated.
_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

#: Twilio identifiers. Protected from the phone regex, restored after.
#: ...and any hex word with at least one letter in it (a build SHA such as
#: b4a4d3395752, a tool-use id, a UUID segment): the phone regex only wants
#: digits, but it will happily take the digit run INSIDE a hex word --
#: "b4a4d3395752" came back as "b4a4d[PHONE]" on the first live row.
_SID_RE = re.compile(
    r"\b(?:CA|MZ|AC|SM|PN)[0-9a-f]{32}\b"
    r"|\b(?=[0-9a-f]*[a-f])[0-9a-f]{7,}\b"
)
_SID_TOKEN = "\x00SID{}\x00"
_SID_TOKEN_RE = re.compile(r"\x00SID(\d+)\x00")


@dataclass
class CallLogContext:
    """One open call's line buffer. Shared by reference across its tasks."""

    call_sid: Optional[str] = None
    clinic_id: Optional[str] = None
    build_sha: Optional[str] = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    max_bytes: int = 2 * 1024 * 1024
    lines: List[str] = field(default_factory=list)
    byte_count: int = 0
    truncated: bool = False
    dropped: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)
    #: Set by the final flush; a later emit (a straggling task) is dropped.
    closed: bool = False

    def append(self, line: str) -> None:
        with self.lock:
            if self.closed:
                return
            size = len(line) + 1
            if self.byte_count + size > self.max_bytes:
                if not self.truncated:
                    self.truncated = True
                    self.lines.append(
                        f"[call_log] TRUNCATED at {self.byte_count} bytes -- "
                        f"OBS_LOG_MAX_BYTES cap; later lines are in the Render log only"
                    )
                self.dropped += 1
                return
            self.lines.append(line)
            self.byte_count += size

    def snapshot(self) -> "tuple[list[str], int, bool]":
        with self.lock:
            return list(self.lines), self.byte_count, self.truncated


_CTX: contextvars.ContextVar[Optional[CallLogContext]] = contextvars.ContextVar(
    "obs_call_log_ctx", default=None
)


def current() -> Optional[CallLogContext]:
    """The open call's context in this task, or None."""
    return _CTX.get()


def open_context(*, max_bytes: Optional[int] = None) -> Optional[CallLogContext]:
    """Create and bind a context for the call about to run. Call BEFORE
    spawning the pipeline tasks. Returns None (and binds nothing) when line
    capture is off, so the handler stays a no-op on every emit."""
    try:
        from app.obs.store import log_lines_enabled

        if not log_lines_enabled():
            return None
        from app import config

        ctx = CallLogContext(
            max_bytes=int(max_bytes or getattr(config, "OBS_LOG_MAX_BYTES", 2 * 1024 * 1024))
        )
        _CTX.set(ctx)
        return ctx
    except Exception:  # pragma: no cover - the log layer never breaks a call
        _log.warning("[call_log] open_context failed", exc_info=True)
        return None


def bind_call(ctx: Optional[CallLogContext], call_sid: str, *,
              clinic_id: Optional[str] = None, build_sha: Optional[str] = None) -> None:
    """Name the call once Twilio's `start` event has arrived."""
    if ctx is None:
        return
    with ctx.lock:
        ctx.call_sid = call_sid or ctx.call_sid
        if clinic_id:
            ctx.clinic_id = clinic_id
        if build_sha:
            ctx.build_sha = build_sha


# ── The handler ──────────────────────────────────────────────────────────────

class CallLogHandler(logging.Handler):
    """Root-logger handler: routes each record to the open call's buffer."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.setFormatter(logging.Formatter(_FORMAT))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            ctx = _CTX.get()
            if ctx is None:
                return
            # The redactor runs at flush, not here: emit must stay O(1).
            ctx.append(self.format(record))
        except Exception:  # pragma: no cover - never raise from a log handler
            pass


_installed: Optional[CallLogHandler] = None


def install() -> CallLogHandler:
    """Attach the handler to the root logger once. Idempotent."""
    global _installed
    if _installed is None:
        _installed = CallLogHandler()
        logging.getLogger().addHandler(_installed)
    return _installed


def uninstall() -> None:
    """Tests only."""
    global _installed
    if _installed is not None:
        logging.getLogger().removeHandler(_installed)
        _installed = None


# ── Redaction ────────────────────────────────────────────────────────────────

#: The line's own timestamp: "2026-09-12 18:41:29,193". The phone regex reads
#: "2026-09-12 18" as a grouped digit run, and because its separator class
#: includes whitespace it can also run ACROSS a newline into the next line's
#: timestamp -- which is how the first live row (CAc0678171, 12 Sep 2026) came
#: back with "[PHONE]:41:29" prefixes and 76 lines merged into 60. So: redact
#: one line at a time, never the joined text, and keep the timestamp out of
#: the redactor's sight entirely.
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d{3})?")


def redact_line(line: str, names: Iterable[str] = ()) -> str:
    """One line: timestamp and SIDs survive; phones, e-mails, names do not."""
    from app.obs.redact import redact_text

    ts = _TS_RE.match(line)
    head, rest = (line[: ts.end()], line[ts.end():]) if ts else ("", line)
    sids: List[str] = []

    def _protect(m: re.Match) -> str:
        sids.append(m.group(0))
        return _SID_TOKEN.format(len(sids) - 1)

    protected = _SID_RE.sub(_protect, rest)
    redacted = redact_text(protected, names)
    return head + _SID_TOKEN_RE.sub(lambda m: sids[int(m.group(1))], redacted)


def redact_lines(lines: Iterable[str], names: Iterable[str] = ()) -> str:
    """Redact each line on its own and join. Line count is preserved."""
    return "\n".join(redact_line(l, names) for l in lines)


# ── Flushing ─────────────────────────────────────────────────────────────────

_purged_this_process = False


def _flush_sync(ctx: CallLogContext, *, complete: bool, names: Iterable[str]) -> bool:
    """Worker-thread body: snapshot, redact, upsert. Returns True on write."""
    global _purged_this_process
    from app import config
    from app.obs.store import purge_call_logs, upsert_call_log

    lines, byte_count, truncated = ctx.snapshot()
    if not ctx.call_sid or not lines:
        return False
    text = redact_lines(lines, names)
    ok = upsert_call_log(
        ctx.call_sid,
        lines=text,
        line_count=len(lines),
        byte_count=byte_count,
        truncated=truncated,
        complete=complete,
        clinic_id=ctx.clinic_id,
        build_sha=ctx.build_sha,
        started_at=ctx.started_at,
    )
    if ok and not _purged_this_process:
        _purged_this_process = True
        try:
            n = purge_call_logs(int(getattr(config, "OBS_LOG_RETENTION_DAYS", 90)))
            if n:
                _log.info("[call_log] purged %d call_logs rows past retention", n)
        except Exception:
            _log.warning("[call_log] retention purge failed", exc_info=True)
    return ok


async def flush(ctx: Optional[CallLogContext], *, complete: bool = False,
                names: Iterable[str] = ()) -> bool:
    """Write the buffer so far. Never raises. `complete=True` is the final
    flush: it marks the row and closes the buffer to stragglers."""
    if ctx is None:
        return False
    try:
        ok = await asyncio.to_thread(_flush_sync, ctx, complete=complete, names=names)
        if complete:
            with ctx.lock:
                ctx.closed = True
            if ok:
                _log.info(
                    "[call_log] stored call_sid=%s lines=%d bytes=%d truncated=%s",
                    ctx.call_sid, len(ctx.lines), ctx.byte_count, ctx.truncated,
                )
        return ok
    except Exception as exc:
        # Keep the buffer; the next flush retries. Said once per failure.
        _log.warning("[call_log] flush failed call_sid=%s: %r", ctx.call_sid, exc)
        return False


async def periodic_flush(ctx: Optional[CallLogContext], interval_s: Optional[float] = None) -> None:
    """Background task: flush every `interval_s` until cancelled."""
    if ctx is None:
        return
    from app import config

    every = float(interval_s or getattr(config, "OBS_LOG_FLUSH_SEC", 20.0))
    if every <= 0:
        return
    try:
        while True:
            await asyncio.sleep(every)
            if ctx.closed:
                return
            if ctx.call_sid:
                await flush(ctx, complete=False)
    except asyncio.CancelledError:
        return


# ── CLI: python -m app.obs.call_log <call_sid> ───────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: python -m app.obs.call_log <call_sid> [grep-substring]", file=sys.stderr)
        return 2
    # Run from a worktree with the slotspec `.env` copied in; `app.config`
    # does not load it, so without this the CLI reports "no call_logs row"
    # for a row that exists (12 Sep, first use).
    try:
        from dotenv import load_dotenv
        load_dotenv(".env")
    except Exception:
        pass
    from app.obs.store import get_call_log

    row = get_call_log(args[0])
    if row is None:
        print(f"no call_logs row for {args[0]}", file=sys.stderr)
        return 1
    needle = args[1].lower() if len(args) > 1 else None
    header = (
        f"# {row['call_sid']} clinic={row['clinic_id']} build={row['build_sha']} "
        f"lines={row['line_count']} bytes={row['byte_count']} "
        f"truncated={row['truncated']} complete={row['complete']}"
    )
    print(header)
    for line in (row.get("lines") or "").splitlines():
        if needle is None or needle in line.lower():
            print(line)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
