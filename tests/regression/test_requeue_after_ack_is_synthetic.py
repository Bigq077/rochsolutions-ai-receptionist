# tests/regression/test_requeue_after_ack_is_synthetic.py
"""Re-queues after an ack-only turn must carry synthetic=True (10 Aug 2026).

Incident
--------
Call 4, opening turn. The caller named a clinic and a time in one breath::

    19:56:54.689  time_pref already known ('August 19th at 3 pm')
                  — timing Q skipped, re-queued pref
    19:56:54.689  location answer intercepted — ack-only, no run_turn: alcester
    19:56:54.691  same-breath straggler dropped — 'August 19th at 3 pm' (1ms early)
    19:56:55.508  BACKSTOP armed — turn asked nothing ('Awlstuh.')
    <10 seconds of dead air>
    19:57:05.520  WATCHDOG_FIRE "Sorry, I didn't catch that..."

Mechanism
---------
The location intercept is ack-only *by design* — `no run_turn`. The re-queued
time preference is therefore not an extra; it **is** the rest of the turn. It
was dropped 2ms after being queued, so the turn was one word long ("Awlstuh.")
and nothing followed it.

Why it was dropped: that site pushed a 2-tuple ``(ts, text)``. Every sibling
re-queue pushes ``(ts, text, True)`` — and ``True`` is the synthetic flag that
bypasses the STT-phantom guards (``_synthetic = False`` is the default when the
drain loop unpacks a 2-tuple). Without it the re-injection races the ack turn's
completion and the same-breath guard eats it, exactly as designed: it was
enqueued 1ms before ``_last_turn_done_at``.

The reasoning was already written down sixty lines below in the same site, on
the FAQ branch::

    synthetic=True: bypass STT-phantom guards — this re-injection races the
    ack turn's completion and would else be dropped as a same-breath straggler.

Fix
---
Push the flag at all three 2-tuple re-queue sites: the booking-branch time
preference (the incident), the DTMF ladder's time preference, and the lookup
keypad read-back's digits. The latter two are the same shape and the same
latent dead air; the keypad one additionally ends its turn with ``continue``,
so the digits are likewise the whole rest of the turn.

Deliberately NOT changed: the raw DTMF keypress injections ('alcester',
'redditch', a slot label) and the DTMF phone commit's ``complete``. Those are
driven by a keypad event rather than queued in the tail of an ack turn, so they
do not race a turn completion — and marking them synthetic would widen the
phantom-guard bypass to ordinary keypad input, which is a different decision.

Why this test is source-shaped
------------------------------
Every one of these sites is inline inside ``handle_transcript`` — a 15k-line
async method that cannot be entered without a live WebSocket, STT, TTS and LLM.
The invariant is a property of the call sites, so it is asserted on the call
sites, by AST rather than by regex: a tuple's arity is a parse-tree fact and
this file's whole point is that one site silently had the wrong arity.

See also test_same_breath_window.py, which owns the guard these sites bypass —
including ``test_synthetic_utterances_are_never_dropped``, the other half of
this contract.
"""

import ast
import inspect

import pytest

from app.media_streams import connection as conn


# ---------------------------------------------------------------------------
# Every `self.transcript_queue.put((...))` in the module, by the name of the
# thing being queued.
# ---------------------------------------------------------------------------
def _queue_puts():
    """-> list of (lineno, tuple_arity, queued_name).

    `queued_name` is the identifier of the payload element, or None when the
    payload is a literal (the raw DTMF injections).
    """
    src = inspect.getsource(conn)
    out = []
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "put"):
            continue
        recv = node.func.value
        if not (isinstance(recv, ast.Attribute)
                and recv.attr == "transcript_queue"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Tuple):
            continue
        payload = node.args[0].elts[1]
        name = payload.id if isinstance(payload, ast.Name) else None
        out.append((node.lineno, len(node.args[0].elts), name))
    return out


# The re-injection sites: something the engine decided to replay into its own
# transcript queue, in the tail of a turn that has just finished (or is about
# to). Named individually rather than matched by pattern — a new site should
# fail this list and be classified deliberately, not inherit a default.
_REINJECTIONS = {
    "_existing_tp":       "booking-branch time pref after clinic ack (THE INCIDENT)",
    "_dtmf_tp":           "DTMF ladder time pref after clinic ack",
    "_rb_phone":          "lookup keypad read-back digits after 'yes'",
    "_faq_pending":       "FAQ utterance after clinic ack",
    "_ae_tp":             "treatment-bypass time pref after ack",
    "_dtmf_faq_pending":  "FAQ utterance after DTMF clinic ack",
    "_utc_faq_pending":   "FAQ utterance after use-this-clinic ack",
    "_utc_tp":            "use-this-clinic time pref after ack",
    "_h_tp":              "Haiku-path time pref after ack",
    "_disp":              "DTMF slot-label dispatch",
    "_buf":               "flushed partial buffer",
}

# Keypad-event injections and the DTMF phone commit. Not re-injections: no turn
# is completing underneath them, so the phantom guards are correct to apply.
_NOT_REINJECTIONS = {"complete", "_label", "_queued_utt", None}


@pytest.mark.parametrize(
    "name,what",
    sorted(_REINJECTIONS.items()),
    ids=sorted(_REINJECTIONS),
)
def test_every_reinjection_carries_the_synthetic_flag(name, what):
    sites = [(ln, arity) for ln, arity, nm in _queue_puts() if nm == name]
    assert sites, (
        f"no transcript_queue.put of {name!r} found ({what}) — the site moved "
        f"or was renamed; re-point this test rather than deleting the row"
    )
    bad = [ln for ln, arity in sites if arity != 3]
    assert not bad, (
        f"{name} ({what}) is queued as a 2-tuple at line(s) {bad}. A 2-tuple "
        f"unpacks with _synthetic=False, so the same-breath guard drops it "
        f"~1ms after it is queued and the caller hears dead air until the "
        f"watchdog fires ~10s later."
    )


def test_the_incident_site_is_the_booking_branch_one():
    """Guards the parametrised row above against being satisfied by a rename.

    `_existing_tp` must still be the value re-queued when the caller's time
    preference is already known and the timing question is skipped — that log
    line and that put are the two halves of the incident.
    """
    src = inspect.getsource(conn.WebSocketCallHandler)
    i = src.index("timing Q skipped")
    before = src[max(0, i - 900):i]
    assert "_existing_tp" in before, (
        "the 'timing Q skipped, re-queued pref' log no longer follows a "
        "_existing_tp re-queue"
    )
    assert "True," in before or "_existing_tp,\n" in before


def test_no_unclassified_queue_put_sites():
    """A new put must be classified as a re-injection or not, on purpose.

    This is the row that makes the list above load-bearing: without it, adding
    a fourth 2-tuple re-queue would pass every other test in this file.
    """
    known = set(_REINJECTIONS) | _NOT_REINJECTIONS
    unknown = sorted(
        {nm for _, _, nm in _queue_puts() if nm not in known},
        key=str,
    )
    assert not unknown, (
        f"unclassified transcript_queue.put payload(s): {unknown}. If the site "
        f"replays an utterance in the tail of a finishing turn it belongs in "
        f"_REINJECTIONS and needs synthetic=True; if it is driven by a keypad "
        f"or media event it belongs in _NOT_REINJECTIONS."
    )


def test_the_drain_loop_still_reads_a_third_element():
    """The flag only does anything because the unpack looks for it. If the
    3-tuple form is ever dropped from the drain loop, every site above becomes
    decoration and the incident returns silently."""
    src = inspect.getsource(conn.WebSocketCallHandler)
    assert "_enqueue_ts, utterance, _synthetic = _raw_item" in src, (
        "the transcript drain loop no longer unpacks the synthetic flag"
    )
    assert "_synthetic = False" in src, (
        "the 2-tuple default is gone — legacy puts would now raise instead of "
        "being treated as STT-origin"
    )


def test_the_guards_the_flag_bypasses_all_honour_it():
    """Three guards read `_synthetic`: the barge-in flush, the C8-2 location-ack
    drop, and the same-breath straggler. A flag honoured by only two of them
    still loses the utterance."""
    src = inspect.getsource(conn.WebSocketCallHandler)
    assert src.count("not _synthetic") >= 4, (
        "fewer STT-phantom guards check _synthetic than when this was written "
        "— a re-injection can now be eaten by whichever one stopped checking"
    )
    assert (
        'not _synthetic and self.session.get("location_acked_this_turn")'
        in src
    ), (
        "the location-ack drop no longer exempts synthetic re-injections — "
        "which is precisely the guard an after-ack re-queue must survive"
    )
