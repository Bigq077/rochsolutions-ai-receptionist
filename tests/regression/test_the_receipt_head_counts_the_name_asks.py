"""N-7 — the receipt head stands down on a REPEAT name ask however it is worded.

CAe3023240 (northgate, 13 Sep 2026, build 0d5c8556), turn 8. The third name
ask on the call was Gate 5g's substitute — "Before I do that — could I take
your first name and surname?" — which is first-ask wording however many times
it has been put. N-1 reads re-ask wording ("again", "once more", "didn't
catch") off Susie's previous turn, so it saw a first ask, and "Thanks, got
that —" fired in front of a fourth read-back.

The count is the engine's to give: `name_already_asked(history)` says whether a
name question was put to this caller BEFORE the one on the table, and
llm_stream passes it as `classify_intent(name_reasked=)`.
"""
from __future__ import annotations

from app.hold_speech import Intent, classify_intent, name_already_asked

WHOLE_Q = "could I take your first name and surname?"
FIRST_Q = "Lovely — could I take your first name?"
SURNAME_Q = "Thanks Sarah — and your surname?"
GATE5G_Q = "Before I do that — could I take your first name and surname?"


def _h(*turns):
    out = []
    for role, text in turns:
        out.append({"role": role, "content": text})
    return out


# ── The call ────────────────────────────────────────────────────────────────

CALL = _h(
    ("user", "yeah go for it"),
    ("assistant", "That one works — " + WHOLE_Q),
    ("user", "a lecture"),
    ("assistant", "did you say Lecture — is that right?"),
    ("user", "no a lecture"),
    ("assistant", "I've got you on oh seven five oh two — is that the best number?"),
    ("user", "yeah that is"),
    ("assistant", "Thanks for that — " + GATE5G_Q),
)


def test_turn_8_gets_no_receipt_head():
    assert name_already_asked(CALL) is True
    assert classify_intent("yeah so that would be a lecture", GATE5G_Q,
                           name_reasked=True) == []


def test_turn_5_the_first_ask_keeps_its_head():
    first = CALL[:2]
    assert name_already_asked(first) is False
    assert classify_intent("a lecture", WHOLE_Q, name_reasked=False) == [Intent.NAME_GIVEN]


# ── The counter ─────────────────────────────────────────────────────────────

def test_the_question_on_the_table_is_not_counted():
    # One name question in history, and it is the one being answered now.
    assert name_already_asked(_h(("assistant", WHOLE_Q))) is False


def test_a_previous_name_question_is_counted_whatever_its_wording():
    assert name_already_asked(_h(
        ("assistant", FIRST_Q), ("user", "elektra"), ("assistant", GATE5G_Q),
    )) is True


def test_a_readback_is_not_a_name_question():
    # "did you say Lecture — is that right?" asks for a yes, not a name.
    assert name_already_asked(_h(
        ("assistant", "did you say Lecture — is that right?"),
        ("user", "no"),
        ("assistant", WHOLE_Q),
    )) is False


def test_a_statement_with_name_words_is_not_a_question():
    assert name_already_asked(_h(
        ("assistant", "I'll pop your name on the booking."),
        ("user", "ok"),
        ("assistant", WHOLE_Q),
    )) is False


def test_a_name_asked_long_ago_is_outside_the_window():
    old = _h(("assistant", "what name is the booking under?"))
    old += _h(("user", "x"), ("assistant", "Right.")) * 7
    old += _h(("assistant", WHOLE_Q))
    assert name_already_asked(old) is False


def test_empty_and_malformed_history_are_false():
    assert name_already_asked(None) is False
    assert name_already_asked([]) is False
    assert name_already_asked([{"role": "assistant"}, "junk", {"content": WHOLE_Q}]) is False


# ── The first-name / surname split still behaves ───────────────────────────

def test_the_surname_ask_is_the_second_name_question_and_was_already_silent():
    hist = _h(("assistant", FIRST_Q), ("user", "sarah"), ("assistant", SURNAME_Q))
    assert name_already_asked(hist) is True
    assert classify_intent("jones", SURNAME_Q, name_reasked=True) == []
    # ...and would have been silent without the count too (dd3a9ff7).
    assert classify_intent("jones", SURNAME_Q, name_reasked=False) == []


def test_the_flag_only_touches_name_answers():
    assert classify_intent("is there parking", "How can I help you today?",
                           name_reasked=True) == [Intent.FAQ_PARKING]
    assert classify_intent("yes", "Is that the best number for the booking?",
                           name_reasked=True) == [Intent.NUMBER_CONFIRMED]


def test_llm_stream_passes_the_count():
    import inspect

    from app.media_streams import llm_stream

    src = inspect.getsource(llm_stream)
    assert "name_reasked=_hs_name_reasked" in src
    assert "from app.hold_speech import name_already_asked as _hs_asked" in src
