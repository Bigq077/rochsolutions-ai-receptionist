"""The booking ack must survive a speculatively-set booking_flow_active.

CA2087528410, 2026-08-24 00:03:57 — seven seconds of dead air.

    00:03:38  caller: "how much is like a general massage or appointment cost"
              -> a PRICE question, but it contains "appointment", so the
                 treatment-mention path matched _transcript_has_booking_intent
                 and set booking_flow_active = True.
    00:03:57  caller: "that's all good can i book an appointment then mate"
              -> the REAL booking request. The sentinel arm of _is_booking_ack
                 was gated on `not self.booking_flow_active`, which the price
                 question had already falsified, so the ack could not fire and
                 no next question was queued. The prompt's bare "Right —" stub
                 was left standing alone until the caller said "hello".

`booking_flow_active` has TWO writers and they mean different things: one is the
genuine ack, the other is speculative. The gate wants "has the ack already run?"
and was reading "has anyone shown booking interest?". The fix gives the genuine
event its own marker, `v3_booking_ack_fired`, and gates on that.

These assertions are STRUCTURAL. The gate lives inside _llm_loop, a method of
several thousand lines whose execution needs STT, LLM, TTS and Redis; a harness
to reach it would be a larger and riskier artefact than the fix. So this file
guards the invariant at the source level instead — it fails if the gate is
reverted to the conflated flag, if the marker stops being set, or if a third
writer of booking_flow_active appears and invalidates the premise.
"""
import re
from pathlib import Path

SRC = (Path(__file__).resolve().parents[2]
       / "app" / "media_streams" / "connection.py").read_text(encoding="utf-8")


def _sentinel_arm() -> str:
    """The normal-sentinel arm of the _is_booking_ack condition."""
    start = SRC.index("Normal sentinel arm")
    end = SRC.index("CTA-affirm arm", start)
    return SRC[start:end]


def test_sentinel_arm_gates_on_the_ack_marker():
    assert "v3_booking_ack_fired" in _sentinel_arm(), (
        "the sentinel arm must gate on v3_booking_ack_fired — the flag that "
        "means 'the ack already ran' — not on booking_flow_active"
    )


def test_sentinel_arm_does_not_gate_on_the_conflated_flag():
    arm = _sentinel_arm()
    # `not self.booking_flow_active` as a GATE is the regression. The words may
    # appear in prose, so look for the code form only.
    assert not re.search(r"^\s*not self\.booking_flow_active\s*$", arm, re.M), (
        "REGRESSION: the sentinel arm is gated on booking_flow_active again. "
        "That flag is set speculatively by the treatment-mention path, so a "
        "price question mentioning 'appointment' permanently shuts this arm "
        "and the caller's real booking request produces a bare 'Right —' "
        "followed by dead air (CA2087528410)."
    )


def test_the_genuine_ack_sets_the_marker():
    # Anchor on the STATEMENT, not the prose: line ~11509 contains
    # "(if _is_booking_ack:)" inside a comment and would win a plain .index().
    m = re.search(r"^\s*if _is_booking_ack:\s*$", SRC, re.M)
    assert m, "could not locate the _is_booking_ack branch"
    block = SRC[m.start():m.start() + 1200]
    assert 'self.session["v3_booking_ack_fired"] = True' in block, (
        "the ack path must record that it ran, or the gate has nothing "
        "honest to read"
    )


def test_the_speculative_setter_does_not_set_the_marker():
    """The treatment-mention path may set booking_flow_active — never the marker."""
    i = SRC.index("treatment mention + booking")
    block = SRC[max(0, i - 1500):i + 400]
    assert "v3_booking_ack_fired" not in block, (
        "the speculative setter must NOT set v3_booking_ack_fired — that would "
        "restore exactly the conflation this fix removes"
    )


def test_booking_flow_active_writers_are_accounted_for():
    """Premise guard: a NEW writer needs this analysis redone.

    Was 2 (speculative treatment-mention, genuine ack). B-87 added a THIRD and
    the analysis was redone rather than the count simply bumped:

      * it fires only when a clinical screen has just CLEANLY cleared and the
        caller had ALREADY asked to book in the utterance that armed the screen
        — never while a screen is pending, never on a red flag, which is the
        same rule the speculative writer's own clinical guard states;
      * it does NOT set `v3_booking_ack_fired`, so the sentinel arm this file
        exists to protect is untouched and a genuine ack can still fire later;
      * the three gates that read `not self.booking_flow_active`
        (treatment-mention, ask-which-clinic, CTA counting) are all downstream
        of the screening block's `continue`, so none of them is skipped on the
        turn it is set; on later turns the flag would have been True anyway
        once the caller answered the offer that B-87 removes.

    If you are adding a FOURTH, do the same work: say which of the three
    meanings your write has, and why the sentinel arm still holds.
    """
    writers = re.findall(r"^\s*self\.booking_flow_active = True\s*$", SRC, re.M)
    assert len(writers) == 3, (
        f"expected 3 writers of booking_flow_active (speculative "
        f"treatment-mention, genuine ack, B-87 post-screen continuation), "
        f"found {len(writers)}. A new writer may reintroduce the conflation — "
        f"re-check the sentinel arm's gate and this docstring."
    )


def test_the_b87_writer_does_not_claim_the_ack_ran():
    """B-87 sets booking_flow_active but must NOT set v3_booking_ack_fired.

    The ack path did not run — the caller never answered a booking offer,
    because B-87 exists to stop that offer being made. Claiming otherwise
    would shut the sentinel arm for a real ack later in the call.
    """
    # There is exactly ONE place that may claim the ack ran: the genuine ack
    # path. Counting the writer is robust where slicing the source is not —
    # a third writer of booking_flow_active must not have brought a second
    # writer of the ack marker with it.
    ack_writers = re.findall(
        r"^\s*self\.session\[\"v3_booking_ack_fired\"\] = True\s*$", SRC, re.M
    )
    assert len(ack_writers) == 1, (
        f"v3_booking_ack_fired must have exactly ONE writer (the genuine ack); "
        f"found {len(ack_writers)}. A path that sets it without the caller "
        f"having answered a booking offer shuts the sentinel arm for a real "
        f"ack later in the call."
    )
