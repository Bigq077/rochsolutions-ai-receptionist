"""
The name fallback must not read a name out of Susie's own adverbs.

build_actionable_summary_row() has a last-resort name recovery that scans the
ASSISTANT side of the transcript for a name-confirmation phrase. Because the
text it scans is Susie's own speech, every word she can utter after "thanks" or
"so that's" is a candidate, and the pattern accepted any [A-Za-z] token.

Call CA8ebb258b (22 Aug, latency-eval line) recorded:

    [actionable_summary] name recovered from history (persist hook missed it): 'Now'
    Row built — outcome=human_requested name=Now

from her own sentence "I can put you through to the clinic team right now —
shall I do that?". A sweep of her routine phrasing found five false names in
seven lines: 'Now' and 'Then' from "Right ...", 'For' from "Thanks for
calling", 'Very' from "Thanks very much", 'Fine' from "So it's fine to ...".

With SMS_ENABLED — the default on all three clinic branches — the follow-up
template greets the caller by that name: "Hi Now, you asked for a callback".
That is the "Hi PENDING" defect reached from a different direction.

The fix is structural, not another stoplist entry: generated prose capitalises
a name and lower-cases an adverb. These tests compile the pattern OUT OF THE
SHIPPED SOURCE so they check what actually runs, not a copy that can drift.
"""

import inspect
import re

import pytest

from app.tools import actionable_summary as mod


def _shipped_pattern() -> re.Pattern:
    """Compile the _NAME_RE literal exactly as it appears in the source."""
    src = inspect.getsource(mod.build_actionable_summary_row)
    m = re.search(r"_NAME_RE = _re\.compile\((.*?)\n\s*\)", src, re.S)
    assert m, "could not find _NAME_RE in the shipped source"
    return re.compile(eval("(" + m.group(1) + ")"))


def _name(text):
    m = _shipped_pattern().search(text)
    if not m:
        return None
    cand = next((g for g in m.groups() if g), "")
    return cand.capitalize() if cand else None


# Susie's own routine phrasing — every one of these is a line she really emits.
@pytest.mark.parametrize(
    "utterance",
    [
        "I can put you through to the clinic team right now — shall I do that?",
        "Right now — I'll get that sorted.",
        "Right then — let me check that for you.",
        "Thanks for calling, is there anything else?",
        "Thanks very much, we'll see you then.",
        "So it's fine to come in wearing shorts.",
        "So that's booked, see you then.",
    ],
)
def test_her_own_words_are_never_a_patient_name(utterance):
    assert _name(utterance) is None, (
        "scraped a name out of Susie's own sentence: %r" % (_name(utterance),)
    )


def test_the_exact_sentence_from_CA8ebb258b():
    """THE regression, verbatim from the call."""
    assert _name(
        "I can put you through to the clinic team right now — shall I do that?"
    ) != "Now"


@pytest.mark.parametrize(
    "utterance,expected",
    [
        ("Thanks Sarah — I've got that booked in.", "Sarah"),
        ("Thanks Ana, that's booked.", "Ana"),
        ("So that's Michael, is that right?", "Michael"),
        ("So it's Priya — let me confirm.", "Priya"),
    ],
)
def test_a_real_confirmation_still_recovers_the_name(utterance, expected):
    """The fallback must keep working — precision, not deletion."""
    assert _name(utterance) == expected


def test_the_right_arm_is_not_reinstated():
    """
    "Right" is a filler this system emits constantly — 'Right —' opened three
    separate turns of CA8ebb258b — and it is not a name-confirmation shape the
    way "Thanks X" and "So that's X" are. It produced two of the five false
    names. Tightening it to [A-Z] would still match "Right Now" at a sentence
    start, so it is removed rather than repaired.
    """
    src = inspect.getsource(mod.build_actionable_summary_row)
    assert "[Rr]ight" not in src, "the Right-X arm is back"


def test_the_pattern_requires_a_capital():
    """
    The discriminator, asserted directly. A stoplist can only ever name the
    false positives someone has already hit: extending _FP would have fixed
    'Now' and left 'For' and 'Very' live.
    """
    src = inspect.getsource(mod.build_actionable_summary_row)
    assert "[A-Za-z][a-z]{1,25}" not in src, (
        "the name group accepts a lower-case initial again — every adverb "
        "Susie can say after 'thanks' becomes a patient name"
    )
