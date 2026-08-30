"""The owner's record of a call must say what the caller HEARD.

`CA8e688605a9460906840840ed561246ac` (30 Aug 2026, demo line, build
`2d72f87a935c`). Two guards corrected ONE sentence on its way out:

    12:42:41.580  read-back time corrected: 'five past nine in the morning'
                  -> 'eight in the morning' for Tuesday 1st September
    12:42:41.588  CORRECTED a false 'the only time is' claim — the day holds
                  more times (B-100)

Both worked; the caller heard a true sentence. But `_append_history` stored the
model's RAW generation in `session["turns"]`, and `actionable_summary` builds
the owner-facing summary from it via `_format_turns`. So the owner's record of
the call named a time the caller was never offered, on a day where the guard
had already established what the right one was.

THE TIME IS THE MILD CASE. Gate 5f rewrites the spoken text when the model
claims a booking that never happened. The raw keeps the claim — so a "you're
booked in for Thursday" that Gate 5f stopped the caller ever hearing was still
reported to the owner as though it had been said. That is the same defect
`_append_history` was changed on 2026-08-02 to fix for `conversation_history`;
its docstring closed with "session["turns"] and the SMS path remain out of
scope". This is that revisit.

THE SMS PATH IS GENUINELY UNAFFECTED, checked rather than assumed. The patient
confirmation SMS (`app/sms_templates.build_sms`) reads `selected_slot` /
`selected_slot_speech` off the booking record and never touches the transcript,
and `smart_sms_router._recent_user_texts` reads only `turn["user"]` — the
CALLER side — so an assistant entry is invisible to it either way.

⚠️ ONE CONSUMER STILL NEEDS THE RAW, AND IT IS A P1 IF IT LOSES IT.
connection.py's Gate 5g name recovery re-runs the name parser against the raw
reply, because Gate 5g deletes the model's acknowledgement of the name the
caller just gave — the only place a first name is ever read from. It read
`turns[...]["text"]` back when that WAS the raw generation. Simply swapping the
key would have disarmed it silently: `_raw_reply` would equal `_last_bot`, the
`!= _last_bot` guard would decline, and the caller would be asked their name
until they hung up (CA041352eb, four times).

`test_name_survives_the_cta_holdback` does NOT catch that — it passes the raw
text straight to `_v3_try_persist_name` and never exercises the round trip
through `session["turns"]`. That seam is what the last section here pins.
"""
from __future__ import annotations

from app.media_streams.llm_stream import _append_history
from app.tools.actionable_summary import _format_turns


# The sentence from the call, before and after the two guards.
RAW = (
    "The earliest I have is Tuesday 1st September, and the available time is "
    "five past nine in the morning. Does that work?"
)
SPOKEN = (
    "The earliest I have is Tuesday 1st September, and I've got eight in the "
    "morning. Does that work?"
)
ASKED = "um what about um the soonest available slot you have"


def _one_turn(spoken: str = SPOKEN, raw: str | None = RAW) -> dict:
    session: dict = {}
    _append_history(session, ASKED, spoken, raw_text=raw)
    return session


# ── What the owner is told ──────────────────────────────────────────────────

def test_the_summary_is_given_the_spoken_time_not_the_generated_one():
    """The defect, exactly as it happened."""
    rendered = _format_turns(_one_turn()["turns"], max_turns=10)
    assert "eight in the morning" in rendered
    assert "five past nine" not in rendered, (
        "the owner's summary names a time the caller was never offered — the "
        "read-back guard had already established the right one"
    )


def test_a_false_completeness_claim_does_not_reach_the_owner_either():
    """The second guard on the same sentence. B-100 removed 'the available time
    is'; the owner must not be told the day held one time when it held eight."""
    rendered = _format_turns(_one_turn()["turns"], max_turns=10)
    assert "the available time is" not in rendered


def test_a_booking_claim_gate_5f_deleted_is_not_reported_as_spoken():
    """The case that matters more than the time.

    Gate 5f rewrites a booking claim the model had no right to make. If the raw
    reaches the summary, the owner is told the caller was given a confirmation
    that was, correctly, never said out loud.
    """
    session = _one_turn(
        spoken="Let me get that confirmed for you and I'll come back to you.",
        raw="All booked — you're in for Thursday at ten. See you then!",
    )
    rendered = _format_turns(session["turns"], max_turns=10)
    assert "All booked" not in rendered
    assert "Let me get that confirmed" in rendered


def test_history_and_the_owner_record_now_agree():
    """Two records of one call that disagree is the whole bug. They are allowed
    to differ in SHAPE, never in what Susie said."""
    session = _one_turn()
    assert session["conversation_history"][-1]["content"] == session["turns"][-1]["text"]


def test_the_caller_side_is_untouched():
    """The user utterance goes to conversation_history and is not this fix's
    business."""
    assert _one_turn()["conversation_history"][-2]["content"] == ASKED


def test_the_owner_transcript_carries_no_caller_lines_on_this_path():
    """NOT a consequence of this change — pinned because it surprised the author
    and will surprise the next reader.

    `_append_history` appends only the ASSISTANT entry to session["turns"]; the
    caller utterance goes to conversation_history alone. `_format_turns` labels
    a turn "Patient" from `turn["user"]`, which only theorem/flow.py writes. So
    on the media-streams path the "conversation" context handed to the summary
    LLM is one-sided: every Susie line, no caller lines.

    That is a separate visibility gap from the one this file fixes, and fixing
    it here would change the owner-facing summary shape for live clinics as a
    side effect. Recorded, not fixed.
    """
    rendered = _format_turns(_one_turn()["turns"], max_turns=10)
    assert "Susie:" in rendered
    assert "Patient:" not in rendered
    assert ASKED not in rendered


# ── The deterministic paths ─────────────────────────────────────────────────

def test_a_turn_with_no_raw_text_is_unchanged():
    """`_append_history` has two other callers — the fast path and the follow-up
    speech — which pass no raw_text because what they queue IS what they speak.
    They must not grow a `raw` key or change shape."""
    session = _one_turn(spoken="We're open till six on Tuesdays.", raw=None)
    entry = session["turns"][-1]
    assert entry == {"role": "assistant", "text": "We're open till six on Tuesdays."}
    assert "raw" not in entry


def test_raw_is_only_stored_when_it_actually_differs():
    session = _one_turn(spoken=SPOKEN, raw=SPOKEN)
    assert "raw" not in session["turns"][-1]


def test_a_turn_that_spoke_nothing_renders_no_susie_line():
    """Every chunk dropped before TTS. The caller heard nothing, so the owner's
    transcript must not claim a sentence — but the generation is still kept for
    diagnosis. `_format_turns` skips empty text, so this needs no special case.
    """
    session = _one_turn(spoken="", raw="Some sentence Gate 5a dropped entirely.")
    assert _format_turns(session["turns"], max_turns=10).count("Susie:") == 0
    assert session["turns"][-1]["raw"] == "Some sentence Gate 5a dropped entirely."


# ── The consumer that still needs the raw — the P1 guard ────────────────────

def test_the_raw_generation_is_still_recoverable_from_turns():
    """Gate 5g's name recovery reads this. Losing it costs the caller their name
    four times over and then the call."""
    session = _one_turn(
        spoken="Lovely — could I take your first name and surname?",
        raw="Thanks Quentin — could I take your first name and surname?",
    )
    entry = session["turns"][-1]
    assert entry["raw"] == "Thanks Quentin — could I take your first name and surname?"
    assert "Quentin" not in entry["text"], (
        "the premise is gone — Gate 5g is no longer deleting the acknowledgement "
        "and this file's last two tests are testing nothing"
    )


def test_the_gate_5g_recovery_reads_the_raw_key_and_not_the_spoken_text():
    """The seam, end to end, in the shape connection.py reads it.

    `test_name_survives_the_cta_holdback` passes the raw text directly to
    `_v3_try_persist_name` and so never exercises this round trip. It is the
    round trip that broke when `text` stopped being the raw generation, and it
    broke SILENTLY — the recovery simply found nothing new and declined.
    """
    from app.media_streams.connection import _v3_try_persist_name

    spoken = "Lovely — could I take your first name and surname?"
    raw = "Thanks Quentin — could I take your first name and surname?"
    session = _one_turn(spoken=spoken, raw=raw)

    # Exactly the lookup connection.py performs.
    recovered = ""
    for t in reversed(session["turns"]):
        if t.get("role") == "assistant":
            recovered = t.get("raw") or t.get("text", "") or ""
            break

    assert recovered == raw
    assert recovered != spoken, (
        "the recovery would find the spoken text, the `!= _last_bot` guard "
        "would decline, and the name would never be persisted"
    )

    # ...and the parser still gets the name out of it.
    fresh: dict = {"booking_flow_active": True}
    assert _v3_try_persist_name(
        fresh, recovered,
        post_slot_pending=True,
        caller_utterance="um yeah that would be quentin rook",
    ) is True
    assert fresh["patient_name"].split()[0] == "Quentin"
