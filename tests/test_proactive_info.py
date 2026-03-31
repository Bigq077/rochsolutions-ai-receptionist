# tests/test_proactive_info.py
"""
Tests for app/proactive_info.py

Coverage:
  1. is_first_visit=True  → first_visit_prep block included
  2. is_first_visit=False → first_visit_prep excluded
  3. Missing KB key       → that block dropped, others still included
  4. Output over 60 words → truncated at last complete sentence
  5. Exception in get_proactive_info → returns "", no raise
  6. append_if_fits: combined > max_chars → confirmation unchanged
  7. append_if_fits: combined ≤ max_chars → extra appended
"""
from __future__ import annotations

import pytest


# Minimal KB that covers all block fields
GOOD_KB = {
    "parking":       "Free on-site parking is available directly outside.",
    "clinic_address": "1 Example Street, Alcester.",
}


# ---------------------------------------------------------------------------
# 1. is_first_visit=True → first_visit_prep block included
# ---------------------------------------------------------------------------

def test_first_visit_true_includes_prep():
    from app.proactive_info import get_proactive_info
    result = get_proactive_info({"is_first_visit": True}, GOOD_KB)
    assert "five minutes early" in result


# ---------------------------------------------------------------------------
# 2. is_first_visit=False → first_visit_prep excluded
# ---------------------------------------------------------------------------

def test_first_visit_false_excludes_prep():
    from app.proactive_info import get_proactive_info
    result = get_proactive_info({"is_first_visit": False}, GOOD_KB)
    assert "five minutes early" not in result


def test_first_visit_false_still_has_always_blocks():
    from app.proactive_info import get_proactive_info
    result = get_proactive_info({"is_first_visit": False}, GOOD_KB)
    # "always" blocks must still fire
    assert len(result) > 0


# ---------------------------------------------------------------------------
# 3. Missing KB key → that block dropped, others still included
# ---------------------------------------------------------------------------

def test_missing_kb_key_drops_block_not_others():
    from app.proactive_info import get_proactive_info
    # KB without "parking" — parking block should be dropped
    partial_kb = {"clinic_address": "1 Example Street, Alcester."}
    result = get_proactive_info({"is_first_visit": False}, partial_kb)
    # parking block gone
    assert "parking" not in result.lower() or "Free on-site" not in result
    # address block still present
    assert "Example Street" in result


def test_missing_all_kb_keys_returns_first_visit_only():
    from app.proactive_info import get_proactive_info
    # No keys at all — only condition-based (no-format) blocks can fire
    result = get_proactive_info({"is_first_visit": True}, {})
    # first_visit_prep has no {key} placeholders so it should still appear
    assert "five minutes early" in result


# ---------------------------------------------------------------------------
# 4. Output over 60 words → truncated at last complete sentence
# ---------------------------------------------------------------------------

def test_over_60_words_truncated_at_sentence_boundary():
    from app.proactive_info import get_proactive_info

    # parking value starts with a short complete sentence (so rfind(".") hits it),
    # then has >60 filler words so the overall output exceeds the 60-word cap.
    long_value = "Parking available here. " + ("Word " * 70).strip()
    long_kb = {
        "parking":        long_value,
        "clinic_address": "Short address.",
    }
    result = get_proactive_info({"is_first_visit": False}, long_kb)
    assert len(result.split()) <= 60
    # Must end at a sentence boundary (period/exclamation/question mark)
    assert result[-1] in {".", "!", "?"}


# ---------------------------------------------------------------------------
# 5. Exception inside get_proactive_info → returns "", never raises
# ---------------------------------------------------------------------------

def test_exception_returns_empty_string():
    from app.proactive_info import get_proactive_info
    # Pass a non-iterable KB to force an internal error
    result = get_proactive_info(None, None)  # type: ignore[arg-type]
    assert result == ""


def test_exception_does_not_raise():
    from app.proactive_info import get_proactive_info
    # Should never raise regardless of input
    try:
        get_proactive_info(object(), 42)  # type: ignore[arg-type]
    except Exception as exc:
        pytest.fail(f"get_proactive_info raised unexpectedly: {exc}")


# ---------------------------------------------------------------------------
# 6. append_if_fits: combined > max_chars → confirmation unchanged
# ---------------------------------------------------------------------------

def test_append_if_fits_too_long_returns_confirmation_only():
    from app.proactive_info import append_if_fits
    confirmation = "Your booking is confirmed."
    extra = "x" * 400  # combined will exceed 400 chars
    result = append_if_fits(confirmation, extra, max_chars=400)
    assert result == confirmation


def test_append_if_fits_too_long_extra_not_in_result():
    from app.proactive_info import append_if_fits
    confirmation = "Your booking is confirmed."
    extra = "x" * 400
    result = append_if_fits(confirmation, extra, max_chars=400)
    assert extra not in result


# ---------------------------------------------------------------------------
# 7. append_if_fits: combined ≤ max_chars → extra appended
# ---------------------------------------------------------------------------

def test_append_if_fits_within_limit_appends_extra():
    from app.proactive_info import append_if_fits
    confirmation = "Confirmed."
    extra = "Parking is available."
    result = append_if_fits(confirmation, extra, max_chars=400)
    assert extra in result
    assert result == f"{confirmation} {extra}"


def test_append_if_fits_empty_extra_returns_confirmation():
    from app.proactive_info import append_if_fits
    confirmation = "Confirmed."
    result = append_if_fits(confirmation, "")
    assert result == confirmation
