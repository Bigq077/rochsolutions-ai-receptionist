"""
P5: "Theorem — Susie answered her own question." She did not.

OPEN_DEFECTS_HOLD_AND_SLOTS_2026-09-01, from CA17e0639e237340:

    assi  Let's get that moved for you. Was your original appointment at our
          Awlstuh or Redditch clinic?
    assi  Alcester.

Read as the assistant answering itself. It is not. `connection.py` resolves the
location INLINE — `_ack = f"{_loc_label}."` — and speaks that ack to confirm the
clinic the caller just named. What is missing from the transcript is the
CALLER's "Alcester", because that branch never reaches `run_turn`, and until
this change `llm_stream._append_history` was the only caller of `record_user`.

Measured over the 798-call corpus: of 105 location questions, 68 are followed by
Susie's own bare "Alcester."/"Redditch." with no caller turn between, and 28 by
an actual caller turn. A defect that reproduces on 65% of a deterministic path
is a recording bug, not a model one.

WHY IT IS WORTH FIXING RATHER THAN JUST EXPLAINING
--------------------------------------------------
The judge's free-text verdict on this transcript IS the operator CALL BACK SMS
(app/obs/judge.py). A caller who appears silent gets described to the operator
as one — the same shape as CAe2120b, where the judge reported a caller as having
hung up when they had not.

THE OTHER P5 CLAIMS, FOR THE RECORD
-----------------------------------
* "a phonetic spelling reached TTS" — NOT a defect. `app/clinics/theorem/
  canonical.py` documents "Awlstuh" as a TTS-only pronunciation hint for
  Alcester ("AWL-stuh"), and P2 established that obs stores the
  pre-substitution form. Pinned below so it is not "fixed" by someone reading
  the transcript.
* the doubled reschedule question — REAL, and still open. 3 occurrences in 806
  calls once the designed no-input re-asks are excluded; root cause needs the
  Render log, not the corpus.
* stacked fillers — REAL, 89 in the corpus, and NOT Theorem-specific. Its own
  job.
"""

import inspect

from app.media_streams import connection as c
from app.obs import turns


ALCESTER_Q = "Was your original appointment at our Awlstuh or Redditch clinic?"


# -- the recording hole ----------------------------------------------------

def test_the_inline_location_branch_records_the_caller():
    src = inspect.getsource(c.WebSocketCallHandler)
    ack_at = src.index('_ack = f"{_loc_label}."')
    window = src[ack_at:ack_at + 1400]
    assert "record_user" in window, (
        "the inline location branch no longer records the caller's answer, so "
        "the transcript is back to showing Susie asking the clinic question "
        "and then answering it herself"
    )


def test_the_caller_is_recorded_before_the_ack_is_queued():
    """Order matters: the ack is recorded by the TTS loop when it dequeues, so
    recording the caller after the put would invert the exchange."""
    src = inspect.getsource(c.WebSocketCallHandler)
    ack_at = src.index('_ack = f"{_loc_label}."')
    window = src[ack_at:ack_at + 1400]
    assert window.index("record_user") < window.index("tts_text_queue.put(_ack)")


# -- record_user is now safe to call from more than one place --------------

def test_an_inline_record_then_a_run_turn_record_does_not_double():
    """The location branch re-queues the utterance to run_turn on its FAQ
    sub-path, so the same words can arrive at record_user twice."""
    s = {}
    turns.record_user(s, "alcester")           # inline site
    turns.mark_turn_start(s)
    turns.record_user(s, "alcester")           # run_turn, same utterance
    assert [t["text"] for t in s["obs_turns"]] == ["alcester"]


def test_the_guard_checks_the_entry_at_the_insert_point_too():
    """run_turn INSERTS at its mark while an inline site APPENDS, so a second
    copy can land on either side of the insert point — not only behind it."""
    s = {
        "obs_turns": [{"role": "user", "text": "alcester"}],
        "_obs_turn_start": 0,                  # mark points AT the duplicate
    }
    turns.record_user(s, "alcester")
    assert [t["text"] for t in s["obs_turns"]] == ["alcester"]


def test_a_genuine_repeat_later_in_the_call_is_still_recorded():
    """Adjacent-only. A caller who says the same word again after Susie has
    replied is saying it again, and the transcript must show both."""
    s = {}
    turns.record_user(s, "alcester")
    turns.record_assistant(s, "Alcester.")
    turns.record_assistant(s, "Is the number you're calling on the right one?")
    turns.record_user(s, "alcester")
    assert [t["role"] for t in s["obs_turns"]] == [
        "user", "assistant", "assistant", "user",
    ]


def test_the_exchange_now_reads_as_a_question_and_an_answer():
    """The whole point: CA17e0639e's three lines, as they will now be stored.

    No `mark_turn_start` — that is the inline path, which never enters
    run_turn. record_user therefore appends, which is what puts the caller
    between Susie's question and Susie's acknowledgement of the answer."""
    s = {}
    turns.record_assistant(s, "Let's get that moved for you. " + ALCESTER_Q)
    turns.record_user(s, "alcester please")
    turns.record_assistant(s, "Alcester.")
    assert [(t["role"], t["text"]) for t in s["obs_turns"]] == [
        ("assistant", "Let's get that moved for you. " + ALCESTER_Q),
        ("user", "alcester please"),
        ("assistant", "Alcester."),
    ]


# -- the claim that was NOT a defect --------------------------------------

def test_the_phonetic_spelling_is_a_documented_pronunciation_hint():
    """"Awlstuh" is how Alcester is said. It is a TTS hint, not a leak, and
    obs stores the pre-substitution form (P2). Someone reading the transcript
    and "fixing" the spelling would break the pronunciation on every Theorem
    call."""
    from app.clinics.theorem import canonical

    assert canonical.STT_PRONUNCIATION["tts_say"]["Alcester"] == "Awlstuh"
    assert "pronunciation hint" in (canonical.__doc__ or "")
