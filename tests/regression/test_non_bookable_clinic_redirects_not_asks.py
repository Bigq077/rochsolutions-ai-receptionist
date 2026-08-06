"""
Choosing a clinic Susie can't book must redirect, not open a booking.

THEOREM_LOCATIONS['redditch']['bookable'] = False (Mark, 2026-07-08) is the
single toggle for the Redditch redirect. Two things honour it already:

  * the prompt block in susie_system_prompt.py, which even anticipates this
    case — "including when they choose Redditch at the clinic question or
    press 2";
  * llm_stream._location_not_bookable(), which refuses check_availability and
    book_appointment so Acuity is never reached.

Neither covers the deterministic location path. When the caller answers the
clinic question, connection.py resolves the location ITSELF — by alias, by
Haiku, or by keypad — acks it and asks the day/time question, logging
"location answer intercepted — ack-only, no run_turn". The prompt never gets a
turn in which to redirect, and the tool guard never fires because no tool is
attempted. The prompt's instruction is advice to a model nobody consulted.

CAf779a697, 2026-08-06:

    17:14:03  'gedditch' → Haiku resolved location: redditch
    17:14:04  "Redditch."  +  "Is there a particular day or time…?"
    17:14:12  caller: "tuesdays and fridays"
    17:14:15  a 10.7-second correction monologue
    17:14:28  caller hangs up, 1.7 seconds after it ends

Three paths can reach a chosen clinic and all three are guarded here. The
redirect sentence is copied verbatim from the prompt block so the code and the
prompt say the same words.
"""

import inspect

import pytest

from app.media_streams import connection as c
from app.media_streams.connection import (
    _NOT_BOOKABLE_REDIRECT,
    _location_is_bookable,
)


# ── the flag, and the decision to fail open ────────────────────────────────

def test_redditch_is_not_bookable():
    assert not _location_is_bookable("theorem_v3", "redditch")


def test_alcester_is_bookable():
    assert _location_is_bookable("theorem_v3", "alcester")


def test_case_is_not_a_way_round_it():
    for spelling in ("Redditch", "REDDITCH", "  redditch "):
        assert not _location_is_bookable("theorem_v3", spelling)


@pytest.mark.parametrize("clinic,location", [
    ("theorem_v3", ""),          # nothing resolved yet
    ("theorem_v3", "nowhere"),   # unknown location
    ("jv_v1", "redditch"),       # another clinic entirely
    ("", "redditch"),            # clinic not resolved
])
def test_it_fails_open(clinic, location):
    """
    Wrongly refusing to book is worse than the redirect not firing. Anything
    unknown must stay bookable.
    """
    assert _location_is_bookable(clinic, location)


def test_one_source_of_truth_with_the_tool_guard():
    """
    Both this and llm_stream._location_not_bookable must read the same flag,
    so flipping it back to True restores Redditch everywhere at once — which
    is what the comment on the flag promises.
    """
    from app.media_streams.llm_stream import _location_not_bookable

    ours = inspect.getsource(_location_is_bookable)
    theirs = inspect.getsource(_location_not_bookable)
    for src in (ours, theirs):
        assert "THEOREM_LOCATIONS" in src
        assert '"bookable"' in src or "'bookable'" in src


# ── all three resolution paths redirect ────────────────────────────────────

def _guard_call_sites(src: str) -> list:
    """
    Every place the handler consults the bookable flag, excluding the helper's
    own definition. Anchored on the call rather than on log wording: the log
    strings wrap across source lines and any test that spells them out breaks
    on reformatting rather than on behaviour.
    """
    # Only calls made by the HANDLER are guards. Module-level helpers use the
    # same flag as a FILTER — _primary_location() picks the first bookable site
    # and _other_bookable_locations() lists the rest — and a filter has no
    # caller in front of it to redirect. Scoping to the handler's own source
    # keeps that distinction structural rather than a hardcoded name list, so a
    # new guard anywhere in the handler is still caught.
    _handler = inspect.getsource(c.WebSocketCallHandler)
    _start = src.find(_handler)
    _end = _start + len(_handler)
    return [
        i for i in range(len(src))
        if src.startswith("_location_is_bookable(", i)
        and not src[max(0, i - 4):i].strip().endswith("def")
        and _start <= i < _end
    ]


def test_all_three_resolution_paths_are_guarded():
    """
    Keypad ("or 2 for Redditch"), caller-said alias, and Haiku resolution —
    every way a caller can choose a clinic on the deterministic path.
    """
    sites = _guard_call_sites(inspect.getsource(c))
    assert len(sites) >= 3, (
        f"expected a bookable check on all three resolution paths, found "
        f"{len(sites)}"
    )


def test_each_guard_speaks_the_redirect_and_stops():
    """
    The guard must not fall through into the ack + day/time question — that is
    the defect. Each arm speaks the redirect and hands control back.
    """
    src = inspect.getsource(c)
    for site in _guard_call_sites(src):
        # Generous window: these arms sit ~40 columns deep, so a third of
        # each line is indentation.
        arm = src[site:site + 3500]
        assert "_NOT_BOOKABLE_REDIRECT" in arm, (
            "a bookable check does not speak the redirect"
        )
        assert ("continue" in arm) or ("return" in arm), (
            "a bookable check falls through into the booking question"
        )


def test_the_redirect_never_asks_for_a_day():
    """The whole point: no booking question for a clinic we cannot book."""
    low = _NOT_BOOKABLE_REDIRECT.lower()
    assert "particular day or time" not in low
    assert "which day" not in low


def test_the_redirect_offers_both_ways_out():
    """Alcester, or a transfer to Mark — as the owner specified."""
    low = _NOT_BOOKABLE_REDIRECT.lower()
    assert "awlstuh" in low
    assert "mark" in low
    assert low.rstrip().endswith("?"), "must end in a question the caller can answer"


def test_the_wording_matches_the_prompt_block():
    """
    Code and prompt must say the same sentence. If the owner rewords one, this
    fails rather than letting the two drift into different scripts.
    """
    from app.prompts import susie_system_prompt as sp

    prompt_src = inspect.getsource(sp)
    # The prompt builds its sentence from adjacent string literals, so only
    # short fragments are contiguous in the source. These three are, and
    # together they pin the script.
    for fragment in (
        "I can't book the Redditch ",
        "clinic myself at the moment",
        "put you straight",
    ):
        assert fragment in prompt_src, (
            f"{fragment!r} is no longer in the prompt's redirect block"
        )
        assert fragment in _NOT_BOOKABLE_REDIRECT, (
            f"{fragment!r} is in the prompt but not in the code's redirect — "
            "the two have drifted into different scripts"
        )
