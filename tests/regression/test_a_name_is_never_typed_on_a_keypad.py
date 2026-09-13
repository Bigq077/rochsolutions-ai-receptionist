"""Gate 5n — a name is never typed on a keypad, and never spelled.

CA9bd192c9 (northgate demo line, 13 Sep 2026, build dd3a9ff7). STT heard
"Elektra" as "a lecture" three times; on the third Susie said

    "I'm not quite catching that — could you try typing your surname on your
     keypad?"

and the caller hung up. A keypad carries three letters per key, so the request
cannot be answered. The prompt already forbids spelling and never mentions a
keypad for a name; the wording is bleed from the PHONE step's "type the number
on your keypad", the only recovery move the model has been taught.

Owner decision 13 Sep 2026: take the best effort, say the spelling will be
confirmed by text, move on. That exit already existed — the prompt's
placeholder rule, `needs_name_correction_sms`, the pending-name record and the
inbound-SMS Acuity update — and nothing enforced reaching it.
"""
from __future__ import annotations

import re

import pytest

from app.media_streams.turn_handler import (
    _NAME_KEYPAD_OR_SPELL_RE,
    _best_effort_name_from_history,
    sanitise_response,
)
from app.media_streams.llm_stream import _PHONE_STEP_MARKERS

KEYPAD_ASK = "I'm not quite catching that — could you try typing your surname on your keypad?"
CLI = "07502211207"


def _session(**over):
    s = {
        "clinic_id": "northgate",
        "twilio_from_local": CLI,
        "booking_flow_active": True,
        "slots_presented": True,
        "_turn_user_text": "a lecture",
        "conversation_history": [
            {"role": "user", "content": "um yeah i've got a bit of a knee thing is there parking"},
            {"role": "assistant", "content": "Sorry to hear that — yes, there's parking."},
            {"role": "user", "content": "um yes that'll be a lecture zani"},
            {"role": "assistant", "content": "did you say Lecture — is that right?"},
        ],
    }
    s.update(over)
    return s


# ── The ask never reaches the caller ───────────────────────────────────────

@pytest.mark.parametrize("line", [
    KEYPAD_ASK,
    "Could you type your surname on the keypad for me?",
    "Would you mind entering your first name on your keypad?",
    "Could you spell that for me?",
    "Could you spell your surname?",
    "Could you say it letter by letter?",
    "Can you give me your name letter-by-letter?",
])
def test_the_ask_is_never_spoken(line):
    out = sanitise_response(line, _session())
    assert "keypad" not in out.lower() or "number" in out.lower()
    assert "spell" not in out.lower() or "by text" in out.lower()
    assert "letter by letter" not in out.lower()
    assert "letter-by-letter" not in out.lower()


# ── The exit the prompt already specifies ──────────────────────────────────

def test_the_exit_takes_the_best_effort_and_promises_a_text():
    s = _session()
    out = sanitise_response(KEYPAD_ASK, s)
    assert "by text" in out.lower(), out
    assert s["patient_name"] == "Lecture"
    assert (s.get("collected") or {}).get("name") == "Lecture"
    assert s["needs_name_correction_sms"] is True
    assert s["_gate5n_exited"] is True


def test_the_exit_asks_the_step_genuinely_outstanding():
    out = sanitise_response(KEYPAD_ASK, _session())
    assert "?" in out, "a turn that asks nothing is dead air"
    assert CLI[0] in out and "best number" in out.lower(), out
    assert any(m in out.lower() for m in _PHONE_STEP_MARKERS), (
        "the substitute must register as the phone step, or the backstop misfires"
    )
    assert not out.startswith("Before I do that"), (
        "that framing belongs to a CTA substitution; nothing was about to be done"
    )


def test_a_single_token_name_still_chases_the_full_one():
    # book_appointment creates the pending-name record only when the stored
    # name is one token -- which is exactly what the exit stores.
    s = _session()
    sanitise_response(KEYPAD_ASK, s)
    assert len(s["patient_name"].split()) == 1


def test_the_exit_happens_once():
    s = _session()
    first = sanitise_response(KEYPAD_ASK, s)
    second = sanitise_response("Could you spell that for me?", s)
    assert "by text" in first.lower()
    assert "by text" not in second.lower(), "the promise is made once"
    assert "spell" not in second.lower()
    assert "?" in second


def test_a_second_ask_keeps_whatever_else_the_turn_asked():
    s = _session()
    sanitise_response(KEYPAD_ASK, s)
    out = sanitise_response(
        "Could you spell that for me? Is that the best number for you?", s
    )
    assert "spell" not in out.lower()
    assert "best number" in out.lower()


def test_with_no_name_attempt_yet_it_is_one_plain_reask():
    s = _session(_turn_user_text="", conversation_history=[])
    out = sanitise_response("Could you spell your surname for me?", s)
    assert "spell" not in out.lower()
    assert "?" in out
    assert s.get("patient_name") is None, "nothing heard, nothing stored"
    assert s.get("needs_name_correction_sms") is not True


# ── What must NOT change ───────────────────────────────────────────────────

def test_the_phone_keypad_line_survives():
    for line in (
        "I can't see a phone number on this call — could you type the number on "
        "your keypad? You can press the star key to reset at any time.",
        "No problem — go ahead and type the number on your keypad.",
        "Could you type your number on your keypad?",
    ):
        assert sanitise_response(line, _session()) == line, line
        assert not _NAME_KEYPAD_OR_SPELL_RE.search(line), line


def test_the_location_keypad_rung_survives():
    line = ("No problem at all — on your keypad, just press 1 for Awlstuh, "
            "or 2 for Redditch.")
    assert sanitise_response(line, _session()) == line


def test_an_ordinary_name_question_is_untouched():
    # "Lovely —" is deliberately absent: Gate 5b's banned-opener strip removes
    # it, and that is not this gate's business.
    for line in ("Thanks Elektra — and your surname?",
                 "Could I take your first name and surname?",
                 "did you say Lecture — is that right?"):
        assert sanitise_response(line, _session()) == line, line


def test_spell_is_left_alone_once_a_name_is_on_record():
    s = _session(patient_name="Sarah Jones")
    line = "How do you spell the road name?"
    assert sanitise_response(line, s) == line


# ── The best-effort reader ─────────────────────────────────────────────────

@pytest.mark.parametrize("said,expected", [
    ("um yes that'll be a lecture zani", "Lecture"),
    ("it's Kowalczyk", "Kowalczyk"),
    ("my surname is O'Brien", "O'brien"),
    ("Roch", "Roch"),
    ("no", ""),
    ("", ""),
    ("i've got a bit of a problem with my left ankle and it's been going on a while now", ""),
    ("is that the right number?", ""),
])
def test_the_best_effort_reader(said, expected):
    assert _best_effort_name_from_history({"_turn_user_text": said}) == expected


def test_the_reader_prefers_this_turn_over_history():
    s = {
        "_turn_user_text": "a lecture",
        "conversation_history": [{"role": "user", "content": "Kowalczyk"}],
    }
    assert _best_effort_name_from_history(s) == "Lecture"


def test_the_reader_falls_back_to_history():
    s = {
        "_turn_user_text": "",
        "conversation_history": [
            {"role": "user", "content": "Kowalczyk"},
            {"role": "assistant", "content": "did you say Kowalczyk?"},
        ],
    }
    assert _best_effort_name_from_history(s) == "Kowalczyk"


# ── The SMS names the caller's OWN clinic ──────────────────────────────────

def test_the_name_correction_sms_is_not_hardcoded_to_theorem():
    import inspect

    from app.media_streams import flow

    src = inspect.getsource(flow)
    assert '"Hi, this is Susie from Theorem Health. "' not in src, (
        "the name-correction SMS told every clinic's caller they were Theorem"
    )
    assert 'f"Hi, this is Susie from {_ncorr_cname}. "' in src
    assert '_ncorr_clinic.get("sms_name")' in src


@pytest.mark.parametrize("clinic_id,expected", [
    ("northgate", "Northgate Physio"),
    ("jv_v1", "Joint Venture Physiotherapy"),
    ("vital_edge", "Vital Edge Therapy"),
    ("theorem", "Theorem Health and Wellness"),
])
def test_every_clinic_resolves_its_own_sms_name(clinic_id, expected):
    from app.clinic_config import get_clinic

    c = get_clinic(clinic_id) or {}
    assert (c.get("sms_name") or c.get("display_name")) == expected


def test_llm_stream_stashes_the_turn_utterance():
    import inspect

    from app.media_streams import llm_stream

    src = inspect.getsource(llm_stream)
    assert 'session["_turn_user_text"] = _last_user_text(messages or [])' in src, (
        "Gate 5n reads the caller's attempt from here -- conversation_history "
        "is not appended until after the turn"
    )
