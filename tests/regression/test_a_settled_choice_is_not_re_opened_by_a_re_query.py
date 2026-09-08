"""The caller picked, and Susie went back to the diary and read a new list.

`CA4215ab7f6c28a49f89a8f87ce781edd4`, theorem_v3, 2026-09-08 01:15, build
`08e99fab`. outcome=abandoned, quality_score=2, failure_tags
`["booking_error", "loop", "dead_end"]`.

    01:15:18  The earliest I have is Wednesday 9th September - Number 1, nine in
              the morning. Number 2, ten in the morning. Number 3, three in the
              afternoon. And I've a few others that day.
    01:15:32  caller: "yeah three works"
              caller ACCEPTED 2026-09-09T15:00:00+01:00        <- resolved
    01:15:39  model calls check_availability AGAIN
    01:15:39  "Sorry, still with you -"                        <- 3.5s hold head
    01:15:56  ...16.1 seconds of Friday 11th, Monday 14th, Tuesday 15th
    01:16:01  caller: "let me if the last one works for me"
    01:16:10  caller hung up.

-- WHY THIS IS THE LAYER, AND GATE 5 WAS NOT ---------------------------------
Gate 5 was fixed first, and the fix was real but nearly worthless on this call.
`calls.slot_offers` holds TWO entries with an IDENTICAL twenty-day payload --
seq 0 `single_day`, seq 1 `multi_day` -- and seq 1's deterministic chunks are
**91% token-identical** to the sentence the caller actually heard:

    model  "...Number 1, Friday 11th September ... Any of those suit you?"
    det    "...Number 1, Friday 11th September ... Any of those work?"

So suppressing the stand-down only changes which wording of the wrong list is
spoken. Every repair downstream of the tool result is cosmetic here, because by
the time any of it runs the model has already been handed twenty days.

-- THE CIRCULAR DEPENDENCY THAT LET IT HAPPEN --------------------------------
The engine resolved the pick exactly and told the model nothing. The model
learns a slot is settled only from `v3_confirmed_slot_phrase`, which is captured
out of its OWN name-request sentence -- so it is informed of the choice only
after it has already said so, and if it does anything else nothing corrects it.

That is the same shape as the `gate5g` name deadlock, where the first name is
only ever learned from Susie's own speech.

-- TWO PARTS, AND EACH IS TESTED SEPARATELY ----------------------------------
  * `chosen_slot_steer` - the cure. Tells the model, before it acts.
  * `_narrows_to_the_chosen_slot` - the backstop. A prompt line can be ignored;
    this cannot. It narrows rather than refuses, because the resolver is not a
    perfect reader of intent and two shapes leak through it (see below).
"""
from __future__ import annotations

import asyncio

import pytest

from app.tools.receptionist_tools import _narrows_to_the_chosen_slot
from app.tools.slot_followup import ACCEPTED_SLOT_KEY, chosen_slot_steer

CHOSEN = "2026-09-09T15:00:00+01:00"

#: The live payload, from `calls.slot_offers` seq 0. Twenty days; the first four
#: are what matters and the model read days 2-4 of them back to the caller.
_LIVE = [
    ("2026-09-09", "Wednesday 9th September",
     ["09:00", "10:00", "11:00", "14:00", "15:00"]),
    ("2026-09-11", "Friday 11th September",
     ["09:00", "10:00", "11:00", "13:00", "14:00"]),
    ("2026-09-14", "Monday 14th September",
     ["09:00", "15:00", "16:00", "17:00", "18:00"]),
    ("2026-09-15", "Tuesday 15th September",
     ["09:00", "11:00", "12:00", "15:00", "16:00"]),
]

_SPOKEN = {
    "09:00": "nine in the morning", "10:00": "ten in the morning",
    "11:00": "eleven in the morning", "12:00": "midday",
    "13:00": "one in the afternoon", "14:00": "two in the afternoon",
    "15:00": "three in the afternoon", "16:00": "four in the afternoon",
    "17:00": "five in the evening", "18:00": "six in the evening",
}


def _days():
    out = []
    for date, label, times in _LIVE:
        out.append({
            "date": date, "day_label": label,
            "slot_times": list(times),
            "slot_times_spoken": [_SPOKEN[t] for t in times],
            "times_not_shown": 0,
            "slots": [{"start": "%sT%s:00+01:00" % (date, t), "end": "",
                       "date": date, "day_label": label, "time": t,
                       "spoken": _SPOKEN[t]} for t in times],
        })
    return out


def _session(chosen=CHOSEN):
    s = {"available_days": _days(), "clinic_id": "theorem_v3"}
    if chosen:
        s[ACCEPTED_SLOT_KEY] = chosen
    return s


def _fake_executor(calls):
    """Stands in for the real check_availability: four provider paths, one
    contract. Records the args it was given so a test can assert the wrapper
    did not tamper with the call itself."""
    async def _exec(args, session):
        calls.append(dict(args or {}))
        return {"available_days": _days(), "total_days": len(_days()),
                "slots": ["untouched"]}
    return _exec


def _run(args, session, executor=None):
    calls = []
    wrapped = _narrows_to_the_chosen_slot(executor or _fake_executor(calls))
    return asyncio.run(wrapped(args, session)), calls


# ---------------------------------------------------------------------------
# The backstop
# ---------------------------------------------------------------------------

def test_the_re_query_no_longer_returns_a_fresh_set_of_days():
    """THE defect. Twenty days went back to the model; three of them reached
    the caller as 16.1 seconds of list."""
    session = _session()
    result, _ = _run({"service": "physiotherapy assessment"}, session)

    assert [d["date"] for d in result["available_days"]] == ["2026-09-09"]
    assert result["total_days"] == 1


def test_the_model_is_told_what_to_do_with_it():
    """A narrowed payload on its own would invite "that's all I have" -- which
    is false. The result has to say why it is short."""
    result, _ = _run({"service": "x"}, _session())
    msg = result.get("message") or ""
    assert CHOSEN[:19] in msg
    assert "already chosen" in msg.lower()
    # ...and in words. The model composes SPEECH from this, and translating a
    # bare timestamp into a weekday is exactly where a wrong day gets spoken.
    assert "Wednesday 9th September" in msg
    assert "three in the afternoon" in msg


def test_the_session_copy_is_narrowed_too():
    """`session['available_days']` is what the slot layer resolves picks
    against. Leaving it wide would let the next turn resolve a day the caller
    was never read."""
    session = _session()
    _run({"service": "x"}, session)
    assert [d["date"] for d in session["available_days"]] == ["2026-09-09"]


def test_the_times_on_the_chosen_day_all_survive():
    """Narrowed to the DAY, not to the slot. "Is that all you have that day?"
    still has a true answer."""
    result, _ = _run({"service": "x"}, _session())
    assert result["available_days"][0]["slot_times"] == [
        "09:00", "10:00", "11:00", "14:00", "15:00"]


# ---------------------------------------------------------------------------
# The guards. Each is a way to turn this into a worse defect than it fixes.
# ---------------------------------------------------------------------------

def test_no_pick_means_no_narrowing():
    """The ordinary first lookup. Every day must survive, or the caller is
    offered one day for the rest of the call."""
    result, _ = _run({"service": "x"}, _session(chosen=None))
    assert len(result["available_days"]) == 4
    assert "message" not in result


@pytest.mark.parametrize("hint", [
    "friday", "next week", "evenings", "Thursday afternoon", "the 15th",
    "something earlier", "mornings only",
])
def test_a_real_request_stands_this_down(hint):
    """The caller who picks and then changes their mind. The model passes what
    they asked for, and a hint the wrapper does not recognise must WIDEN, not
    narrow -- the failure direction that costs a caller their choice is the one
    that hides days, so an unknown hint is always treated as a real request."""
    result, _ = _run({"service": "x", "date_hint": hint}, _session())
    assert len(result["available_days"]) == 4


@pytest.mark.parametrize("hint", ["", "  ", "any", "Any Time", "asap",
                                  "as soon as possible", "no preference",
                                  "whenever", "earliest."])
def test_a_vacuous_hint_is_not_a_request(hint):
    """The model fills this field whether or not the caller asked for anything,
    so an empty-ish hint has to read as "no new request" -- otherwise the
    backstop never fires and this whole file tests nothing."""
    result, _ = _run({"service": "x", "date_hint": hint}, _session())
    assert [d["date"] for d in result["available_days"]] == ["2026-09-09"]


def test_a_chosen_day_missing_from_the_payload_is_left_alone():
    """The diary moved under the caller -- their slot is gone. Narrowing to a
    day that is not there would return NOTHING and strand them."""
    session = _session(chosen="2026-10-31T15:00:00+01:00")
    result, _ = _run({"service": "x"}, session)
    assert len(result["available_days"]) == 4


def test_the_args_reach_the_executor_untouched():
    """It narrows the RESULT. A wrapper that edited the request would be
    changing what was asked of the provider, which is not its business."""
    calls = []
    args = {"service": "physiotherapy assessment", "location": "alcester"}
    _run(args, _session(), executor=_fake_executor(calls))
    assert calls == [args]


def test_a_single_day_result_is_not_touched():
    """Nothing to narrow, and rewriting `total_days` or attaching a message
    where there is no list to suppress is noise the model has to read."""
    async def _one_day(a, s):
        return {"available_days": _days()[:1], "total_days": 1}
    result, _ = _run({"service": "x"}, _session(), executor=_one_day)
    assert "message" not in result


@pytest.mark.parametrize("bad", [
    None, "a string", 0, [], {"error": "no_availability", "slots": []},
    {"available_days": None}, {"available_days": "nonsense"},
    {"available_days": [{"no_date": 1}, {"no_date": 2}]},
])
def test_it_never_raises_and_never_empties_a_result(bad):
    """An error result from the provider must pass straight through: the
    executor's own contract is `{"error": ..., "message": ...}` with no
    available_days, and the model needs that message intact."""
    async def _bad(a, s):
        return bad
    result, _ = _run({"service": "x"}, _session(), executor=_bad)
    assert result == bad


def test_a_raising_executor_still_raises():
    """It must not swallow a provider failure into a silent empty list --
    that is the failure mode this codebase names as its worst."""
    async def _boom(a, s):
        raise RuntimeError("acuity down")
    with pytest.raises(RuntimeError):
        _run({"service": "x"}, _session(), executor=_boom)


# ---------------------------------------------------------------------------
# The cure
# ---------------------------------------------------------------------------

def test_the_model_is_told_the_caller_has_chosen():
    steer = chosen_slot_steer(_session())
    assert "Wednesday 9th September" in steer
    assert "three in the afternoon" in steer
    assert "check_availability" in steer


def test_the_steer_is_silent_when_nothing_was_chosen():
    """It renders on the pick turn only. The pin is popped and re-resolved at
    the top of every caller turn, so this cannot go stale mid-call the way
    `v3_confirmed_slot_phrase` did for three callers who changed day."""
    assert chosen_slot_steer(_session(chosen=None)) == ""


def test_the_steer_does_not_tell_it_to_book():
    """The caller has chosen a TIME. The name, the phone and the confirmation
    all still have to happen; a steer that skipped them would trade this defect
    for a worse one."""
    steer = chosen_slot_steer(_session()).lower()
    assert "book_appointment" not in steer
    assert "book it" not in steer


def test_the_steer_falls_back_to_the_iso_rather_than_going_silent():
    """A pin with no matching slot in the payload still has to reach the model
    -- silence there is the defect this fixes."""
    session = {ACCEPTED_SLOT_KEY: CHOSEN, "available_days": []}
    assert CHOSEN[:19] in chosen_slot_steer(session)


@pytest.mark.parametrize("junk", [None, "", 0, [], {}, {"available_days": None}])
def test_the_steer_never_raises(junk):
    assert isinstance(chosen_slot_steer(junk), str)


# ---------------------------------------------------------------------------
# Both prompt builders, because there are two
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("module,func", [
    ("app.prompts.clinic_template_prompt", "_b7_call_state"),
    ("app.prompts.susie_system_prompt", "_build_theorem_v3"),
])
def test_every_prompt_builder_carries_the_steer(module, func):
    """theorem_v3 renders from its own builder, so a rule written in the
    template is absent from the clinic this defect actually happened on. The
    read-back name steer had to be done exactly this way for the same reason."""
    import importlib
    import inspect
    src = inspect.getsource(getattr(importlib.import_module(module), func))
    assert "chosen_slot_steer(session)" in src, (
        "%s does not carry the steer - the clinic it renders for keeps the "
        "defect" % func)
