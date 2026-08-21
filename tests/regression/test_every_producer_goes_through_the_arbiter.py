"""Every hold-phrase producer asks the arbiter, and the latch is call-wide.

The arbiter can only guarantee "one head per turn" if every producer actually
consults it. Five did not, each with its own phrase list and its own idea of when
to speak, and the audible result on CA8cf0aaea was three hold phrases in 3.4
seconds: connection.py's phone-confirm fired immediately, llm_stream's ack filler
1.8s later, and the tool filler 1.6s after that.

These are structural tests over the source. That is deliberate: the producers
live inside a 15k-line async method that cannot be driven from a unit test, so
the property is pinned where it can be read — a new producer that picks its own
phrase will fail here rather than on a call.
"""
from pathlib import Path

import pytest

from app.filler_phrases import note_filler_played
from app.hold_speech import WorkKind, decide_hold, work_for_tool

import app.media_streams.connection as connection
import app.media_streams.filler_guard as filler_guard
import app.media_streams.llm_stream as llm_stream


def _src(mod) -> str:
    return Path(mod.__file__).read_text(encoding="utf-8")


class TestNoProducerPicksItsOwnPhrase:
    def test_nothing_chooses_at_random_from_the_legacy_pool(self):
        """`random.choice(FILLER_PHRASES)` was how a producer guessed.

        It is a guess by construction: the pool is generic, so whatever it
        returns describes work nobody has established is happening.
        """
        for mod in (connection, llm_stream):
            src = _src(mod)
            assert "choice(FILLER_PHRASES)" not in src, (
                f"{mod.__name__} still picks a hold phrase at random"
            )

    @pytest.mark.parametrize("mod", [connection, llm_stream])
    def test_each_producer_module_consults_the_arbiter(self, mod):
        assert "decide_hold" in _src(mod), (
            f"{mod.__name__} speaks while the caller waits without asking "
            f"hold_speech"
        )

    def test_the_clip_registers_itself_and_its_wording(self):
        """FillerGuard's clip bypasses TTS entirely, so it is invisible to the
        obs transcript and was once invisible to the cooldown as well. It must
        at least register, or a phrase stacks on top of recorded audio."""
        src = _src(filler_guard)
        assert "note_filler_played" in src
        assert "text=" in src, "the clip must record what it SAYS, not just that it spoke"


class TestTheLatchIsResetAtTheCallerTurnBoundary:
    def test_the_reset_is_not_inside_run_turn(self):
        """Ordering bug this guards against.

        The phone-confirm producer speaks BEFORE run_turn is reached. While the
        reset lived inside run_turn it cleared that producer's latch, and the ack
        filler spoke on top of it — the stack returns. The reset belongs at the
        dispatch boundary, where a caller turn actually begins.
        """
        src = _src(llm_stream)
        assert '"_hold_head_spoken"] = False' not in src, (
            "resetting the latch inside run_turn is too late — see the "
            "phone-confirm producer, which speaks before run_turn"
        )

    def test_the_reset_sits_with_the_new_turn_stamp(self):
        src = _src(connection)
        i_reset = src.find('self.session["_hold_head_spoken"] = False')
        i_turn = src.find("self._turn_timing = _lat_new_turn(")
        assert i_reset != -1, "the latch is never reset — hold phrases would stop"
        assert i_turn != -1
        # Reset immediately precedes the new-turn stamp: same boundary, and no
        # producer can run between them.
        assert 0 < (i_turn - i_reset) < 700, (
            "the latch reset has drifted away from the caller-turn boundary"
        )


class TestTheLatchActuallyLatches:
    def test_any_producer_speaking_blocks_the_next(self):
        session = {}
        first = decide_hold(
            kind=WorkKind.PATIENT_LOOKUP,
            head_already_spoken=bool(session.get("_hold_head_spoken")),
        )
        assert first.speak

        # Whatever spoke, it registers through this one function.
        note_filler_played(session, text=first.head)

        second = decide_hold(
            kind=WorkKind.DIARY_READ,
            head_already_spoken=bool(session.get("_hold_head_spoken")),
        )
        assert not second.speak
        assert second.reason == "one head per turn"

    def test_the_wording_is_recorded_for_the_join(self):
        session = {}
        note_filler_played(session, text="Let me see —")
        assert session["_hold_head_text"] == "Let me see —"
        assert session["_hold_head_spoken"] is True

    def test_a_producer_that_passes_no_wording_still_latches(self):
        # The latch must not depend on a producer remembering to pass text.
        session = {}
        note_filler_played(session)
        assert session["_hold_head_spoken"] is True


class TestTheProvisionalLieIsUnreachableOnTheToolPath:
    def test_vital_edge_book_never_claims_a_write(self):
        """Three stored VE calls said "Just locking that in now…" and then
        "sent it to Jonathan… subject to his confirmation"."""
        from app.filler_phrases import is_write_filler
        from app.hold_speech import render_head

        kind = work_for_tool("book_appointment", provisional=True)
        assert kind is WorkKind.PENDING_REQUEST
        head = render_head(kind, practitioner="Jonathan")
        assert not is_write_filler(head)
        assert "locking" not in head.lower()
        assert "Jonathan" in head

    def test_a_confirmed_clinic_still_says_it_is_booking(self):
        # The provisional wording must not leak the other way: a real write
        # understated as a request would understate a booking that did happen.
        kind = work_for_tool("book_appointment", provisional=False)
        assert kind is WorkKind.WRITE_BOOK
