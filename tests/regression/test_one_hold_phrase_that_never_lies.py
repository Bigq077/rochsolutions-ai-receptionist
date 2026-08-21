"""The hold phrase is spoken once per turn, and only about work that is real.

Both properties are structural, not tuned. The 323 stored calls (25 Jul - 21 Aug
2026) show what the previous arrangement produced, and each defect below maps to
a test here:

  * 175 of 322 hold phrases were followed by a QUESTION rather than data. The
    phrase promised a lookup that never happened, because the ack filler fired at
    1800ms — before the tool call exists in the LLM stream — and guessed.
  * 32 dead-ends where the phrase was the last thing said.
  * Runs of two and three with no caller turn between.
  * 3 Vital Edge calls said "Just locking that in now..." and then "sent to
    Jonathan... subject to his confirmation". Nothing was locked in.

decide_hold is pure, so all of this is provable without a socket or a phone.
"""
import re

import pytest

from app.hold_speech import (
    EM_DASH,
    HEADS,
    WorkKind,
    decide_hold,
    render_head,
    work_for_tool,
)


class TestOneHeadPerTurn:
    def test_a_second_head_in_the_same_turn_is_refused(self):
        """The corpus stacked three: "Right with you... / Of course - just
        pulling your appointment up... / That's absolutely fine - sorting that
        for you now..." and only then the answer."""
        first = decide_hold(kind=WorkKind.DIARY_READ, head_already_spoken=False)
        second = decide_hold(kind=WorkKind.DIARY_READ, head_already_spoken=True)
        assert first.speak
        assert not second.speak
        assert second.reason == "one head per turn"

    @pytest.mark.parametrize("kind", list(WorkKind))
    def test_the_latch_beats_every_kind(self, kind):
        # Checked FIRST, before the work is even consulted: every stacked run in
        # the corpus was a second producer deciding it had something to add.
        assert not decide_hold(kind=kind, head_already_spoken=True).speak


class TestItNeverPromisesWorkThatIsNotHappening:
    def test_no_work_means_silence(self):
        """A turn that answers immediately needs no hold phrase.

        This is the 175. "Right with you..." then "Thanks Quentin - I've got you
        on oh seven five oh two... is that the best number?" — a caller-ID
        readback with no tool behind it at all.
        """
        d = decide_hold(kind=WorkKind.NONE, head_already_spoken=False)
        assert not d.speak
        assert d.reason == "no work in flight"

    def test_an_unknown_stall_names_no_work(self):
        """The "are you a robot?" case.

        The corpus has "Just getting that for you..." answered by "No - I'm
        Susie, Theorem Health's AI receptionist." UNKNOWN_SLOW is the only kind
        that can be wrong about the work, so it must not describe any.
        """
        names_work = re.compile(
            r"\b(check|look|find|pull|diary|schedule|availab|book|cancel|move|"
            r"shift|sort|lock|get)", re.IGNORECASE,
        )
        for head in HEADS[WorkKind.UNKNOWN_SLOW]:
            assert not names_work.search(head), head

    @pytest.mark.parametrize("tool,expected", [
        ("check_availability",     WorkKind.DIARY_READ),
        ("lookup_patient",         WorkKind.PATIENT_LOOKUP),
        ("reschedule_appointment", WorkKind.WRITE_MOVE),
        ("cancel_appointment",     WorkKind.WRITE_CANCEL),
    ])
    def test_the_wording_follows_the_tool(self, tool, expected):
        assert work_for_tool(tool) is expected

    def test_an_unrecognised_tool_stays_silent_rather_than_guessing(self):
        # NONE -> silence. Guessing is what produced the 175.
        assert work_for_tool("escalate_to_claude") is WorkKind.NONE
        assert not decide_hold(
            kind=work_for_tool("escalate_to_claude"), head_already_spoken=False,
        ).speak


class TestAProvisionalClinicNeverClaimsAWrite:
    def test_a_provisional_booking_becomes_a_request(self):
        """Vital Edge, three stored calls.

        "Just locking that in now..." then "I've noted your preferred time and
        sent it to Jonathan. Your appointment is subject to his confirmation."
        """
        assert work_for_tool("book_appointment", provisional=True) is (
            WorkKind.PENDING_REQUEST
        )
        assert work_for_tool("book_appointment", provisional=False) is (
            WorkKind.WRITE_BOOK
        )

    def test_no_pending_head_sounds_like_a_completed_write(self):
        from app.filler_phrases import is_write_filler
        for head in HEADS[WorkKind.PENDING_REQUEST]:
            rendered = head.replace("{practitioner}", "Jonathan")
            assert not is_write_filler(rendered), rendered
            assert "locking" not in rendered.lower()

    def test_the_practitioner_is_named_from_config_not_hardcoded(self):
        assert "Jonathan" in render_head(
            WorkKind.PENDING_REQUEST, practitioner="Jonathan",
        )
        assert "Mark" in render_head(
            WorkKind.PENDING_REQUEST, practitioner="Mark",
        )

    def test_a_clinic_with_no_named_practitioner_still_reads(self):
        # Never "Sending that over to  -".
        head = render_head(WorkKind.PENDING_REQUEST, practitioner="")
        assert "{practitioner}" not in head
        assert "  " not in head
        assert head.rstrip().endswith(EM_DASH)


class TestEveryHeadIsAnUnfinishedClause:
    """The reply has to be able to complete the sentence.

    A head ending in the ellipsis renders as a falling contour plus a trailing
    pause, and that contour is what makes a filler sound canned. These are also
    enforced at import time in hold_speech._self_check, so a bad head is a
    startup failure; asserted again here so the reason is written down where a
    future edit will read it.
    """

    @pytest.mark.parametrize("kind", list(HEADS))
    def test_the_clause_is_left_open(self, kind):
        for head in HEADS[kind]:
            assert head.rstrip()[-1:] in (EM_DASH, ",", "-"), head

    @pytest.mark.parametrize("kind", list(HEADS))
    def test_no_ellipsis_and_no_full_stop(self, kind):
        for head in HEADS[kind]:
            assert "…" not in head, head
            assert not head.rstrip().endswith("."), head

    @pytest.mark.parametrize("kind", list(HEADS))
    def test_no_head_is_deleted_by_gate_5b(self, kind):
        """Hold phrases bypass sanitise_response.

        That asymmetry is how "just a moment" stayed reachable for months after
        it was banned from model speech: the ban applied to the model and not to
        us. A phrase the engine would delete from the model is one the engine
        should not be saying either.
        """
        from app.media_streams.turn_handler import _BANNED_SENTENCE_RE
        for head in HEADS[kind]:
            rendered = head.replace("{practitioner}", "Jonathan")
            for name, rx in _BANNED_SENTENCE_RE:
                assert not rx.search(rendered), f"{head!r} killed by {name}"


class TestTheHeadJoinsOntoTheReply:
    def test_a_head_and_a_reply_read_as_one_sentence(self):
        from app.media_streams.llm_stream import join_after_head
        head = render_head(WorkKind.DIARY_READ)
        joined = head + " " + join_after_head(
            "The earliest I have is Monday the 10th.", head,
        )
        assert joined == "Let me see — the earliest I have is Monday the 10th."

    def test_a_day_name_keeps_its_capital_across_the_join(self):
        from app.media_streams.llm_stream import join_after_head
        head = render_head(WorkKind.DIARY_READ)
        joined = head + " " + join_after_head(
            "Friday the fourteenth at ten is free.", head,
        )
        assert joined == "Let me see — Friday the fourteenth at ten is free."


class TestRotation:
    def test_consecutive_heads_of_one_kind_differ(self):
        # A caller who hits two diary reads in one call must not hear the same
        # waveform twice; the clip pool being size 1 is the audio-side version
        # of the same complaint.
        seen = [render_head(WorkKind.DIARY_READ, index=i) for i in range(3)]
        assert len(set(seen)) == 3

    def test_rotation_wraps_without_error(self):
        assert render_head(WorkKind.DIARY_READ, index=99)
