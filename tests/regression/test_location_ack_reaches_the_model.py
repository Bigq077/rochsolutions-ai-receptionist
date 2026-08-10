# tests/regression/test_location_ack_reaches_the_model.py
"""
CAc8f74ddf (Theorem, 2026-08-10, build 3081b4e) — Susie asked which clinic
FOUR times and the caller hung up on a booking he never got.

    19:23:59  caller: "yeah hi there um do you have anything on a wednesday
                       afternoon"
    19:24:01  Susie:  "Which clinic were you thinking of — Awlstuh or Redditch?"
    19:24:08  caller: "uh you're always the clinic"      <- STT ate "Alcester"
    19:24:09  location unresolved -> defaulting to primary site 'alcester'
    19:24:09  Susie:  "Awlstuh."
    19:24:12  Susie:  "Just to check — which clinic were you thinking of...?"
    19:24:19  caller: "we're always the clinic"
    19:24:21  Susie:  "I want to make sure I get you to the right place — did
                       you mean our Awlstuh clinic, or Redditch?"
    19:24:30  caller: "i said the osteo clinic"
    19:24:33  Susie:  "...we have two clinics... Which were you thinking of?"
    19:24:43  hangs up. 12 turns, outcome 'abandoned', no booking, no SMS.

The resolver was RIGHT. It read a mangled answer, defaulted to alcester — the
site he wanted — and said so. Then the conversation layer threw the result away.

── The mechanism ───────────────────────────────────────────────────────────
The intercept spoke the ack straight onto the TTS queue and never wrote it to
`conversation_history`. It also *consumed* the caller's answer, re-queueing the
stored time preference in its place. So neither half of the exchange existed in
the model's context. What the model saw was its own unanswered "Which clinic
were you thinking of?" followed by a user turn reading "afternoons" — so it
asked again, and the same thing happened to that answer too.

── Why it looked intermittent ──────────────────────────────────────────────
Only the `_h_tp` branch recorded nothing. When the caller has NOT already given
a time, `_h_next_q` is asked and that question was appended, so history advanced
and the loop did not form. `_h_tp` is set by a time in the opening sentence —
"do you have anything on a wednesday afternoon" — which is one of the most
ordinary ways to open a call to a clinic.

── Not caused by the 10 Aug deploy ─────────────────────────────────────────
Checked before fixing: the only connection.py change in 117c56a..3081b4e is
inside `_v3_try_persist_name`, which handles caller NAMES and cannot run before
one exists. The intercept was last touched 2026-08-07 (a233a5c2).

── Both roles, deliberately ────────────────────────────────────────────────
conversation_history is deep-copied into the Messages API request very nearly
verbatim. Appending only the assistant ack would put two assistant turns
side by side; recording the caller's answer as well keeps the roles alternating
AND is the truthful transcript, since that utterance was otherwise lost.

Sibling of test_location_defaults_to_primary_site (the resolver) — this file
covers what happens to its answer afterwards.
"""
from __future__ import annotations

import inspect
import re

import pytest

from app.media_streams import connection as conn_mod


def _loop_src() -> str:
    return inspect.getsource(conn_mod.WebSocketCallHandler._llm_loop)


def _ack_block() -> str:
    """The HAIKU-resolve branch — the one the live call went through.

    Anchored on `_spoken`, not on `put(_ack)`. There are FOUR location-ack
    sites in this method and `put(_ack)` matches the first one, which is the
    DTMF ladder — an early draft of this file asserted against that block and
    failed for reasons that had nothing to do with the fix.

    Bounded by markers rather than a character count: the code is indented ~40
    columns and carries long comments, so a fixed window silently truncates.
    """
    src = _loop_src()
    i = src.index("_spoken = [_ack]")
    j = src.index('"location_acked_this_turn"', i)
    return src[i:j]


# ── 1. The defect ───────────────────────────────────────────────────────────

def test_the_spoken_ack_is_written_to_history():
    """
    Without this the model never learns the clinic question was answered, and
    re-asks it on the next turn — four times on the live call.
    """
    block = _ack_block()
    assert "conversation_history" in block, (
        "the location ack is spoken but never recorded — the model will re-ask"
    )
    assert '"content": " ".join(_spoken)' in block, (
        "history must carry what was actually SAID, ack included"
    )


def test_the_callers_answer_is_written_to_history():
    """
    It is consumed here and replaced by the re-queued time preference, so if it
    is not recorded it is gone — and the model sees its question unanswered.
    """
    block = _ack_block()
    assert '"role": "user"' in block and '"content": utterance' in block, (
        "the caller's clinic answer is swallowed by the intercept unrecorded"
    )


def test_the_ack_is_recorded_on_the_requeue_branch_too():
    """
    The heart of it. `_h_tp` — caller already named a time — is the branch that
    recorded nothing, and it is the branch a normal opening sentence takes.
    The append must sit AFTER the if/elif so both paths reach it, not inside
    either one.
    """
    block = _ack_block()
    i_hist = block.index("conversation_history")
    i_elif = block.index("elif _h_next_q:")
    assert i_hist > i_elif, (
        "the history append is inside a branch — the _h_tp path will skip it, "
        "which is exactly the live bug"
    )


# ── 2. Roles must alternate ─────────────────────────────────────────────────

def test_user_is_appended_before_assistant():
    """
    History goes to the Messages API very nearly verbatim. Assistant-then-user
    would put this ack adjacent to the previous turn's assistant message.
    """
    block = _ack_block()
    i_hist = block.index("conversation_history")
    tail = block[i_hist:]
    assert tail.index('"role": "user"') < tail.index('"role": "assistant"')


def test_the_next_question_is_not_appended_twice():
    """
    `_h_next_q` used to be appended inside its own branch. Now it joins
    `_spoken` instead — if the old append came back, that branch would write two
    assistant entries for one turn and double the question in context.
    """
    block = _ack_block()
    assert block.count("conversation_history") == 1, (
        "more than one history append in this branch — roles will desynchronise"
    )
    assert "_spoken.append(_h_next_q)" in block


# ── 3. The other copies of this intercept ───────────────────────────────────
# The clinic answer resolves at FOUR sites in this method, and every one has
# the same shape: an "ask the day/time question" branch that records, and a
# "time preference already known, re-queue it" branch that asked nothing. The
# second is the looping one, at every site. Fixing only the reproduced copy
# would leave the identical bug live on the paths a slightly different caller
# takes — which is how a one-word answer once had to pass three separate
# deny-by-default filters before anyone noticed the other two.


# Each verbal re-queue branch, keyed by a fragment of its own log line. Named
# explicitly rather than found by regex: these strings are split across source
# lines at different points, so a pattern that matches two of them silently
# misses the third — which is how the first draft of this test "passed" the
# site it was written for and skipped the one that actually broke.
_REQUEUE_SITES = {
    "use-this-clinic": '" time preference known (%r)"',
    "alias-match": '" re-queued pref",',
    "haiku-resolve": '" time preference already"',
}


@pytest.mark.parametrize("site", sorted(_REQUEUE_SITES))
def test_every_requeue_branch_records_the_exchange(site):
    """
    Three verbal sites: use-this-clinic, alias-match and Haiku-resolve. A caller
    whose answer is understood must not get a different outcome from one whose
    answer STT mangled — all three re-queue, so all three must record.
    """
    src = _loop_src()
    anchor = _REQUEUE_SITES[site]
    assert anchor in src, (
        f"the {site} re-queue branch has moved — this file needs re-reading"
    )
    i = src.index(anchor)
    # Measured 2026-08-10: the appends sit at +1003, +823 and +4033 from their
    # anchors, and the nearest OTHER site is +16182 away. 5000 clears the
    # largest (the Haiku site carries this fix's comment block) while staying
    # far short of the gap, so a window can never pass on a neighbour's append.
    #
    # Looks for the CALLER turn specifically, not merely "conversation_history".
    # Verified against the unfixed tree: these branches sit near other appends
    # (`"content": _next_q` and friends), and the looser check passed on two of
    # the three sites before the fix existed. `"content": utterance` is the one
    # thing only this fix writes.
    assert '"content": utterance' in src[i: i + 5000], (
        f"the {site} re-queue branch does not record the caller's answer — a "
        "caller taking this path hears the clinic question repeat until they "
        "hang up"
    )


def test_the_dtmf_ladder_is_the_known_exclusion():
    """
    The fourth site lives in `_handle_dtmf`, not `_llm_loop` — which is why the
    scan above cannot see it and why it is called out here by name.

    It shares the defect, but the caller's "answer" there is a keypress rather
    than speech, so the user turn this fix appends would have to be synthesised
    rather than quoted. That is a different decision, and there is no reproduced
    call behind it yet.

    Pinned so the gap is a recorded choice and not something that was missed.
    When the DTMF path gets its own fix, this test should fail and be deleted.
    """
    src = inspect.getsource(conn_mod.WebSocketCallHandler._handle_dtmf)
    i = src.index("DTMF: time preference known")
    assert "conversation_history" not in src[i: i + 800], (
        "the DTMF re-queue branch now records — delete this test and fold the "
        "site into test_every_requeue_branch_records_the_exchange"
    )


# ── 4. Unrelated behaviour, pinned ──────────────────────────────────────────

def test_the_ack_is_still_spoken():
    """The audible default is the safety property of the 2026-08-06 decision.
    Recording it must not have replaced saying it."""
    assert "await self.tts_text_queue.put(_ack)" in _loop_src()


def test_the_time_preference_is_still_requeued():
    """The whole point of the _h_tp branch: go straight to availability rather
    than asking a day/time the caller already gave."""
    block = _ack_block()
    assert "self.transcript_queue.put(" in block
    assert "_h_tp" in block


def test_the_session_still_records_the_resolved_location():
    """selected_location is read by the booking readback and the summary."""
    block = _loop_src()
    assert '"selected_location"' in block
