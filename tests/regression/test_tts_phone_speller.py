"""P22c — phone numbers are spelled as words before synthesis.

eleven_flash_v2_5 runs with text normalization OFF, so a bare "07502 211 207"
in the caller-ID readback is synthesised as one rushed digit run.  The template
prompt asks the model to say the digits in word-groups, but that is prompt-side
only; when the model emits numerals the readback muddles and the caller cannot
check the number.

The half of this that matters MORE than the spelling is the false-positive set:
every other digit-carrying thing Susie says on a call — dates, times, durations,
prices, postcodes — must pass through untouched.  A rule that reads "45 minutes"
as "four five minutes" is worse than the bug it fixes.
"""
import pytest

from app.media_streams.tts_stream import (
    _apply_tts_substitutions_elevenlabs as _subs,
    _apply_tts_substitutions_openai as _subs_openai,
)


# ── The defect: numerals in the readback ────────────────────────────────────

@pytest.mark.parametrize("raw", [
    "07502 211 207",
    "07502211207",
    "07502 211207",
    "07502-211-207",
])
def test_uk_mobile_is_spelled_and_grouped(raw):
    """Every spacing the model might emit lands on the same spoken form."""
    out = _subs(f"I've got you on {raw} — is that the best number for the booking?")
    assert "oh seven five oh two, two one one, two oh seven" in out
    assert raw not in out


def test_plus_44_is_spoken_in_the_familiar_0_form():
    """A caller checks against the number in their own phone, which starts 0."""
    out = _subs("I've got you on +447502211207 — is that right?")
    assert "oh seven five oh two, two one one, two oh seven" in out
    assert "44" not in out


def test_uk_landline_is_spelled():
    out = _subs("You can reach us on 01527 123456.")
    assert "oh one five two seven, one two three, four five six" in out


def test_landline_keeps_the_grouping_it_was_written_with():
    """Imposing the mobile 5/3/3 on 0121 496 0000 gives "oh one two one four,
    nine six oh, oh oh oh" — harder to check than the bug being fixed."""
    out = _subs("Our other site is on 0121 496 0000.")
    assert "oh one two one, four nine six, oh oh oh oh" in out


def test_long_group_is_broken_up_even_if_written_as_one():
    """However it arrives, no group runs more than five digits without a pause."""
    out = _subs("Call 0800 1234567 for the out-of-hours line.")
    spoken = out[out.index("oh eight"):].rstrip(" for the out-of-hours line.")
    assert max(len(g.split()) for g in spoken.split(", ")) <= 5


def test_grouping_pauses_survive_for_pacing():
    """The commas ARE the fix — without them it is still one rushed run."""
    out = _subs("I've got you on 07502211207.")
    assert out.count(",") == 2


# ── The important half: everything else must survive untouched ──────────────

@pytest.mark.parametrize("phrase", [
    "So that's Quentin, Tuesday the 4th of August at half past six.",
    "That's on 6:30 in the evening.",
    "The first appointment is 45 minutes.",
    "A 90 minute session works out cheaper.",
    "We're at B49 5AB, just off the high street.",
    "That's 20 minutes before your 10:00 slot.",
    "I can do the 1st, the 2nd or the 15th.",
    "It's 2026 pricing.",
])
def test_non_phone_digits_are_never_spelled(phrase):
    assert _subs(phrase) == phrase


def test_price_list_without_pound_sign_is_not_a_phone_number():
    """"125, 175, 200" is 9 digits and comma-separated — the 0/+44 prefix rule
    is what keeps it out, so guard it explicitly."""
    phrase = "The options are 125, 175, 200 depending on the practitioner."
    assert _subs(phrase) == phrase


def test_currency_still_wins_and_is_not_double_processed():
    out = _subs("The assessment is £175.")
    assert "one hundred and seventy-five pounds" in out
    assert "one seven five" not in out


def test_a_number_that_is_too_short_is_left_alone():
    phrase = "Call extension 0752 if you need us."
    assert _subs(phrase) == phrase


# ── Idempotence and no-ops ──────────────────────────────────────────────────

def test_already_spoken_form_is_untouched():
    """When the model DOES follow step 8, there are no digits to convert."""
    phrase = "I've got you on oh seven five oh two, two one one, two oh seven."
    assert _subs(phrase) == phrase


def test_applying_twice_changes_nothing():
    """The WS path applies substitutions per chunk; a re-application must be
    a no-op rather than mangling the words it produced last time."""
    once  = _subs("I've got you on 07502211207.")
    twice = _subs(once)
    assert once == twice


def test_openai_fallback_path_matches_elevenlabs():
    """The dev bypass must not sound different from production."""
    text = "I've got you on 07502 211 207."
    assert _subs_openai(text) == _subs(text)


def test_empty_and_plain_text_are_unaffected():
    assert _subs("") == ""
    assert _subs("Perfect, I'll book that in.") == "Perfect, I'll book that in."
