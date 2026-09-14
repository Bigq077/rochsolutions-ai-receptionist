"""CA2bfb791302 (14 Sep 2026, demo, build 803ca416) — a loop after the exit.

    Susie   "No problem — I'll pop what I've got on the booking, and I'll
             text you … Is 0750… the best number for you? If so, just say
             use this number."                                 <-- 5n-d exit
    Caller  "uh use this number"
    Susie   "Before I do that — is 0750… the best number for you? …"
    Caller  "uh use this number"
    Susie   "Before I do that — is 0750… the best number for you? …"
    Caller  "use this number"
    Susie   "Before I do that — …"                              (hung up)

    [ms_gate5] booking CTA held back — phone missing; asked for it instead  ×3

The verbal phone confirm lives in connection.py and needs a branch armed:
`v3_phone_dtmf_active` (set when connection.py persists the name itself)
or `booking_flow_active` (the booking-ack path, skipped when the opening
already carried the timing). A name persisted by `_gate5n_exit` -- every
turn_handler exit: 5n, 5n-c, 5n-d, 5n-f -- passed neither site, so "use
this number" fell to the model, whose booking was held for "phone
missing", and the same question was asked until the caller hung up.

The exit now arms the phase exactly as connection.py's persist site does.
Gate 5g's hold logs at ERROR and arms the branch itself if the caller has
just answered the phone question and the hold still fires.
"""
from app.media_streams.turn_handler import _gate5n_exit, sanitise_response


def _s():
    return {"clinic_id": "northgate", "collected": {"reason": "ankle"},
            "conversation_history": [], "selected_slot": "2026-09-14T14:40:00",
            "_turn_serial": 5, "_turn_user_text": "no i said god no",
            "slots_presented": True}


def test_the_exit_arms_the_phone_collection_phase():
    s = _s()
    out = _gate5n_exit(s, "Gardner")
    assert s["patient_name"] == "Gardner"
    assert s.get("v3_phone_dtmf_active") is True, (
        "connection.py's verbal phone confirm needs this to have a branch to land in"
    )
    assert "number" in out.lower()


def test_the_exit_does_not_rearm_once_the_phone_is_confirmed():
    s = _s()
    s["phone_confirmed"] = True
    _gate5n_exit(s, "Gardner")
    assert "v3_phone_dtmf_active" not in s


def test_gate_5g_names_the_loop_and_arms_the_branch():
    """The belt: name known, phone unconfirmed, caller just said 'use this
    number' to the phone question, and the CTA is still being held."""
    s = _s()
    s["patient_name"] = "Gardner"
    s["collected"]["name"] = "Gardner"
    s["_turn_user_text"] = "uh use this number"
    s["last_bot_prompt"] = ("Is 0 7 5 0 2 2 1 1 2 0 7 the best number for you? "
                            "If so, just say use this number.")
    s["conversation_history"] = [{"role": "assistant", "content": s["last_bot_prompt"]}]
    out = sanitise_response("Great — shall I go ahead and book that in?", s)
    assert "book that in" not in out.lower()
    assert s.get("_gate5g_phone_holds") == 1
    assert s.get("v3_phone_dtmf_active") is True
