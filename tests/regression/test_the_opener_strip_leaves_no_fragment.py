r"""The interim-opener strip must not leave a dangling clause behind.

`_strip_interim_opener` removes a hold phrase the caller has already heard
("Let me check…", "Bear with me…") from the front of the model's first chunk,
so it is not spoken twice. When that phrase was the HEAD of a subordinate
clause rather than a sentence of its own, removing it leaves the remainder
stranded:

    "Bear with me while I look that up."   ->  "While I look that up."
    "Let me check what's available for…"   ->  "What's available for Saturday."

Both were spoken to real callers — six of the first shape on 21-22 Aug 2026.
Gate 5b deletes such a sentence for exactly this reason, but this strip runs
first and leaves the wreckage downstream of it.

`_ORPHAN_LEAD` is the guard. It was added on canonical listing only the
ADVERBIAL leads (while / whilst / as I / so I / until) and stopped one word
short of the COMPLEMENT ones, so "Let me check what's…" — the commonest opener
in the whole obs corpus — fell straight through it. This branch is getting the
widened list, which is the only version worth having.

Ported from canonical 2026-08-30. The guard was canonical-only; the strip it
guards is live on all four branches, so every live clinic could speak
"While I look that up." to a patient until this landed.
"""
from __future__ import annotations

import pytest

from app.media_streams.llm_stream import _ORPHAN_LEAD, _strip_interim_opener


# (opener + remainder, the sentence that must survive)
DANGLERS = [
    # The shape the guard was originally written for — adverbial lead.
    (
        "Bear with me while I look that up. Thursday at ten is free.",
        "Thursday at ten is free.",
    ),
    (
        "One moment while I check the diary. I have half past nine.",
        "I have half past nine.",
    ),
    # The shape the original list missed — complement lead. This is the
    # commonest opener in the corpus.
    (
        "Let me check what's available for Saturday. "
        "I'm afraid Saturday is fully booked.",
        "I'm afraid Saturday is fully booked.",
    ),
    (
        "Let me check when Marcus is next free. He has Tuesday at two.",
        "He has Tuesday at two.",
    ),
    (
        "Just a moment whether that time is still open. It is.",
        "It is.",
    ),
]


@pytest.mark.parametrize("payload,survivor", DANGLERS)
def test_a_stripped_opener_never_leaves_a_fragment(payload, survivor):
    out = _strip_interim_opener(payload)
    assert out == survivor, (
        f"a dangling clause survived the strip: {out!r}"
    )


def test_a_fragment_with_nothing_after_it_is_dropped_entirely():
    """No sentence follows the dangling clause, so there is nothing to say.

    Speaking the fragment alone is the defect; saying nothing here is correct —
    the model's remaining chunks still arrive, and Gate 5b would have deleted
    this sentence anyway.
    """
    assert _strip_interim_opener("Bear with me while I look that up") == ""
    assert _strip_interim_opener("Let me check what's free on Saturday") == ""


def test_a_reply_that_merely_begins_with_one_of_those_words_is_untouched():
    """The guard only runs when an opener was ACTUALLY stripped.

    Without this the guard would eat ordinary questions — "What time suits
    you?" and "When would you like to come in?" both start with a listed word
    and both are complete sentences.
    """
    for intact in (
        "What time suits you?",
        "When would you like to come in?",
        "Which clinic is easier for you, Alcester or Redditch?",
        "How does Thursday morning sound?",
        "While I have you, can I take a contact number?",
    ):
        assert _strip_interim_opener(intact) == intact


def test_the_word_list_covers_the_complements_and_not_only_the_adverbials():
    """State the widening out loud, so a future edit cannot silently undo it.

    The original list was adverbial-only and the commonest live opener fell
    through it. If someone trims this back, this test names what breaks.
    """
    for adverbial in ("while", "whilst", "as I", "so I", "until"):
        assert _ORPHAN_LEAD.match(adverbial + " something")
    for complement in ("what", "whether", "which", "how", "when", "where", "if"):
        assert _ORPHAN_LEAD.match(complement + " something"), complement


def test_the_guard_is_anchored_to_a_word_boundary():
    """`what` must not match `whatever` mid-word, or a legitimate reply that
    happens to start with a longer word gets its first sentence deleted."""
    assert not _ORPHAN_LEAD.match("whatsoever")
    assert not _ORPHAN_LEAD.match("iffy")
    assert not _ORPHAN_LEAD.match("whiles")


def test_neither_call_site_can_be_left_with_nothing_to_say():
    """The guard may consume the WHOLE chunk — the call sites must survive that.

    Canonical never has this problem: it reaches the stripper only through
    `join_after_head`, which returns the original chunk when the strip leaves
    nothing ("saying the phrase twice is a much smaller fault than saying
    nothing"). These branches call the stripper directly, so the fallback is
    written at the two call sites instead, as `... or chunk`.

    Without it, a reply that is nothing but the opener plus a dangling clause
    would be dropped entirely and the turn would go silent — which is a worse
    defect than the fragment this guard exists to remove.
    """
    import ast
    import inspect

    from app.media_streams import llm_stream

    src = inspect.getsource(llm_stream)
    tree = ast.parse(src)

    bare = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name)
                and node.func.id == "_strip_interim_opener"):
            continue
        # The call must sit inside a `X or Y` expression, i.e. its parent is a
        # BoolOp with op=Or. Find it by walking down from the module again.
        bare.append(node.lineno)

    guarded = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            for value in node.values:
                if (isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Name)
                        and value.func.id == "_strip_interim_opener"):
                    guarded.add(value.lineno)

    unguarded = sorted(set(bare) - guarded)
    assert not unguarded, (
        "_strip_interim_opener is called without an `or <original>` fallback at "
        f"llm_stream.py line(s) {unguarded} — the _ORPHAN_LEAD guard can return "
        "an empty string there and the turn goes silent"
    )
