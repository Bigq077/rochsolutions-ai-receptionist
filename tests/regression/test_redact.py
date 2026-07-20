"""Unit tests for app.obs.redact — the PII redactor and its hard assertion."""
from __future__ import annotations

import pytest

from app.obs import redact


@pytest.mark.parametrize("phone", [
    "+447870166861",
    "+44 7870 166861",
    "07870166861",
    "07870 166 861",
    "0121 496 0000",
])
def test_phone_numbers_are_redacted(phone):
    out = redact.redact_text(f"Call me on {phone} please")
    assert phone not in out
    assert "[PHONE]" in out
    assert redact.find_pii(out) == []


def test_email_is_redacted():
    out = redact.redact_text("email jane.doe@example.co.uk to confirm")
    assert "@" not in out
    assert "[EMAIL]" in out


def test_known_names_are_redacted_case_insensitively():
    out = redact.redact_text("Hello, this is quentin ROCH speaking", names=["Quentin Roch"])
    assert "quentin" not in out.lower()
    assert "roch" not in out.lower()
    assert out.count("[NAME]") == 2


def test_non_pii_text_is_untouched():
    text = "I'd like to book a physio appointment for Monday morning."
    assert redact.redact_text(text) == text


def test_assert_no_pii_raises_on_leak():
    with pytest.raises(redact.PIILeakError):
        redact.assert_no_pii("reach me at 07870166861")
    with pytest.raises(redact.PIILeakError):
        redact.assert_no_pii("mail me at a@b.com")


def test_assert_no_pii_passes_when_clean():
    redact.assert_no_pii(redact.redact_text("ring 07870166861 or a@b.com"))  # no raise


def test_redact_transcript_and_assert_clean():
    turns = [
        {"role": "assistant", "text": "What's the best number for you?"},
        {"role": "user", "text": "It's 07870 166861 and jane@example.com, I'm Jane Smith"},
    ]
    red = redact.redact_transcript(turns, names=["Jane Smith"])
    redact.assert_transcript_clean(red)  # must not raise
    joined = " ".join(t["text"] for t in red)
    assert "07870" not in joined and "@" not in joined and "jane" not in joined.lower()
