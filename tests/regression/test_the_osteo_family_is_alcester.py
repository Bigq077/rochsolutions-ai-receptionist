"""STT's "osteo" / "ooster" family for Alcester resolves without a confirm.

Corpus, 14 Sep 2026 — 29 Theorem answers to the clinic question: 12 came
back from STT as osteo / oosterklinik / ooster / owster / ofta / onsta, every
one of them Alcester, none ever Redditch. In August the Haiku resolver
absorbed them; on 14 Sep (CA7de22277, CA3aee2959) it returned unknown for
"your oosterknecht" and "your osteo clinic" and the caller was made to say
"use this clinic". A shape the corpus has settled is an alias.
"""
from __future__ import annotations

import pytest

from app.media_streams.connection import _ALCESTER_ALIASES, _REDDITCH_ALIASES


@pytest.mark.parametrize("heard", [
    "uh for your oosterknecht", "at your osteo clinic", "uh your oosterklinik",
    "at your ooster clinic", "owster", "ofta", "onsta", "uh for your osteo can i",
])
def test_the_osteo_family_hits_the_alcester_aliases(heard):
    assert any(a in heard for a in _ALCESTER_ALIASES), heard
    assert not any(a in heard for a in _REDDITCH_ALIASES), heard


def test_redditch_is_untouched():
    assert not any(a in "ridditch" for a in _ALCESTER_ALIASES)
