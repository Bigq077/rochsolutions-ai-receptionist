# tests/regression/test_phone_confirm_needs_the_phone_question.py
"""B-25 (2 Aug 2026) — the DTMF-active phone confirm had no question gate.

Observed on call CAce42c36b (21:57:53), sweep call 4
-----------------------------------------------------
    21:57:48  v3_phone_dtmf_active = True (name confirmed — phone collection phase)
    21:57:48  Susie: "Thanks Quentin — and your surname?"
    21:57:53  Caller: "just quentin's fine"
    21:57:53  [ms_conn v3] verbal phone confirm — stored calling number
                           07502211207 + phone_confirmed=True and exited DTMF

The caller was DECLINING to give a surname. The system read it as consent to
book on the caller ID and set ``phone_confirmed`` — the flag that satisfies
``book_appointment``'s A1 write gate — twenty-four seconds before the phone
question was asked at all.

Root cause
----------
Two sites perform the verbal phone confirm. Only one checked what question was
on the table:

  * booking flow (``elif _bk_caller_num and _bk_phone_step``) — guarded by a
    ``_PHONE_STEP_MARKERS`` test against the last assistant question;
  * DTMF-active, buffer-empty — guarded by ``_phone_confirm_is_yes`` alone.

``_phone_confirm_is_yes`` answers "did the caller say yes?". It has never
answered "yes to WHAT?". That is the question-gate's whole job, and the second
site never had one.

Why the window is wide enough to matter
---------------------------------------
``v3_phone_dtmf_active`` is set the moment a FIRST NAME is captured, so the
entire surname exchange sits inside the unguarded window. And the verdict says
"yes" to far more than a phone confirmation:

    _phone_confirm_verdict("just quentin's fine")            -> "yes"
    _phone_confirm_verdict("um yeah that'll be roch r-o-c-h") -> "yes"
    _phone_confirm_verdict("that's ok")                       -> "yes"

The second of those is the caller's actual SURNAME. On CAce42c36b it did not
fire only because the refusal one turn earlier had already switched DTMF off.

This is the same shape as B-17 (``booking_sms`` vs ``owner_alert``) and
DEFECT_REGISTER.md §A4 (one affirmative vocabulary in four places): a behaviour
implemented twice, where one copy is corrected and the other is forgotten. The
fix therefore puts the test in ONE place — ``_phone_question_on_the_table`` —
and calls it from both sites, so a future reword cannot fix one and miss the
other again.

Not in scope
------------
The verdict itself is unchanged. "just quentin's fine" is a perfectly reasonable
"yes" to a yes/no question; the defect was never that the verdict was wrong, it
was that nobody asked which question it was answering.
"""

import inspect

import pytest

from app.media_streams import connection as conn
from app.media_streams.llm_stream import _PHONE_STEP_MARKERS, _phone_confirm_verdict


# ---------------------------------------------------------------------------
# The defect, at the level that makes it dangerous. These pin WHY a question
# gate is required: the verdict cannot distinguish these from a real consent.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "utterance",
    [
        "just quentin's fine",              # the observed refusal, CAce42c36b
        "um yeah that'll be roch r-o-c-h",  # the surname ANSWER, same call
        "that's ok",
        "yeah",
    ],
)
def test_the_verdict_says_yes_to_things_that_are_not_phone_confirmations(utterance):
    """Retained deliberately as an assertion about the verdict, not a complaint
    about it. A yes/no judge answering a surname question will say yes; the
    guard has to come from the question, not from the answer."""
    assert _phone_confirm_verdict(utterance) == "yes", utterance


# ---------------------------------------------------------------------------
# The gate itself.
# ---------------------------------------------------------------------------
class TestPhoneQuestionOnTheTable:
    @pytest.mark.parametrize(
        "last_question",
        [
            "Thanks Quentin — and your surname?",
            "Could I take your first name and surname?",
            "What's your full name?",
            "Which one works — Thursday the 6th or Saturday the 8th?",
            "What's the appointment for?",
            "Do you have a preference for when you'd like to come in?",
            "",
        ],
    )
    def test_a_non_phone_question_closes_the_gate(self, last_question):
        assert conn._phone_question_on_the_table(
            {"last_question": last_question}
        ) is False, last_question

    @pytest.mark.parametrize(
        "last_question",
        [
            # Step 8 as actually spoken on every sweep call.
            "Thanks Quentin — I've got you on oh seven five oh two, two one one, "
            "two oh seven — is that the best number for the booking?",
            # The keypad invitation (call 2, 21:48:45).
            "No problem — go ahead and type the number on your keypad. "
            "You can press the star key to reset at any time.",
            # The dead-air re-ask (call 4, 21:58:34).
            "Sorry, I didn't catch that. Is that the best number for the booking?",
            # The wording B-15 made reachable but which has not yet fired live.
            "Sorry — is the number you're calling on the best one to reach you? "
            "Just say use this number.",
        ],
    )
    def test_a_real_phone_question_opens_the_gate(self, last_question):
        assert conn._phone_question_on_the_table(
            {"last_question": last_question}
        ) is True, last_question

    def test_it_falls_back_to_last_bot_prompt(self):
        """`last_question` is not always populated; the booking site has always
        read either. Preserved so the swap to the shared helper is behaviour-
        preserving at that site."""
        assert conn._phone_question_on_the_table(
            {"last_question": "", "last_bot_prompt": "is that the best number?"}
        ) is True

    def test_it_fails_closed_on_an_unknown_phrasing(self):
        """A phone question worded outside the marker set blocks the intercept
        rather than allowing it. The utterance then reaches the LLM, which asks
        again: a missed intercept costs a turn, a false one books a number the
        caller never confirmed."""
        assert conn._phone_question_on_the_table(
            {"last_question": "and how may we reach you?"}
        ) is False

    def test_it_reuses_the_shared_marker_set(self):
        """Not a private copy. There were already three copies of this list when
        B-25 was found; a fourth is how the next one happens."""
        src = inspect.getsource(conn._phone_question_on_the_table)
        assert "_PHONE_STEP_MARKERS" in src
        assert conn._phone_question_on_the_table(
            {"last_question": f"... {_PHONE_STEP_MARKERS[0]} ..."}
        ) is True


# ---------------------------------------------------------------------------
# The call sites. Both branches are inline in handle_transcript's loop and
# cannot be invoked in isolation, so the wiring is pinned against the source —
# matching test_reschedule_phone_confirm_verdict.py.
# ---------------------------------------------------------------------------
class TestBothSitesAreGated:
    START = '# ── Verbal "use this number" intercept'
    END = "exited DTMF; LLM will produce booking readback"

    @pytest.fixture
    def src(self):
        return inspect.getsource(conn.WebSocketCallHandler)

    @pytest.fixture
    def dtmf_block(self, src):
        """Exactly the DTMF-active, buffer-empty intercept — the B-25 site."""
        i = src.index(self.START)
        return src[i:src.index(self.END, i) + len(self.END)]

    def test_the_block_markers_are_unique(self, src):
        """A non-unique anchor silently tests the wrong branch. It did exactly
        that on the first draft of the sibling test file."""
        assert src.count(self.START) == 1
        assert src.count(self.END) == 1

    def test_the_dtmf_site_is_gated(self, dtmf_block):
        """THE regression. Fails on 1328f39, where this branch's only conditions
        were a caller number, the verdict, and the keypad guard."""
        assert "_phone_question_on_the_table(self.session)" in dtmf_block, (
            "the DTMF-active phone confirm can fire on an answer to a question "
            "that was not about the phone — B-25, call CAce42c36b"
        )

    def test_the_dtmf_site_still_requires_the_verdict(self, dtmf_block):
        """The gate is added to the existing conditions, not swapped for them."""
        assert "_phone_confirm_is_yes(utterance)" in dtmf_block

    def test_the_dtmf_site_still_refuses_to_overwrite_a_typed_number(self, dtmf_block):
        """Independently load-bearing and verified live on sweep call 2
        (21:49:03, 'verbal phone confirm SKIPPED — keypad number already on
        record'). Not part of B-25; asserted so this fix cannot drop it."""
        assert 'self.session.get("phone_entered_by_keypad")' in dtmf_block

    def test_the_booking_site_uses_the_same_gate(self, src):
        """It had its own inline copy before B-25. If it grows a second one,
        the two can drift — which is the defect this file exists for."""
        assert "_bk_phone_step = _phone_question_on_the_table(self.session)" in src

    def test_there_is_exactly_one_marker_test_in_the_module(self, src):
        """Both sites route through the helper, so the marker set is iterated in
        one place only. A third private `any(_mk in ... for _mk in
        _PHONE_STEP_MARKERS)` in this class is a regression of B-25's root
        cause even if both sites happen to be correct at the time."""
        assert "for _mk in _PHONE_STEP_MARKERS" not in src

    def test_both_confirm_sites_are_gated(self, src):
        """Two, and only two. A third unguarded site is the defect returning."""
        assert src.count("_phone_question_on_the_table(self.session)") == 2
