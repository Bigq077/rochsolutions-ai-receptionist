"""The reply continues the hold phrase instead of restarting after it.

Two defects, one seam, both measured on the 323 stored calls (25 Jul - 21 Aug):

  1. THE DOUBLE OPENER. 95 assistant fragments across 73 calls opened with the
     model's own holding phrase — "Just a moment while I check what's
     available." — on top of a deterministic filler the system had already
     spoken 1-2s earlier. The stripper that fixes this has existed and been
     tested since the fast-path work, armed only by `interim_played`, which no
     live path sets. It was correct code nobody had wired up.

  2. THE RESTART. Every head ends in "..." or ".", a closed sentence, so the
     payload can only begin a new one: "Right with you..." <pause> "Friday 14th
     August at ten in the morning is available." A head ending in a comma or a
     dash is an unfinished clause, and the payload completes it.

join_after_head is pure so the whole corpus can be replayed through it offline.
"""
import pytest

from app.media_streams.llm_stream import join_after_head

DASH = "\u2014"
ELLIPSIS = "\u2026"


class TestTheOpenerIsNotSaidTwice:
    # Verbatim from the obs corpus. Every one of these was spoken on top of a
    # hold phrase the caller had already heard.
    @pytest.mark.parametrize("said", [
        "Just a moment while I check what's available.The available slots for "
        "Tuesday 28th July are - Number 1, five in the evening.",
        "Let me check what's available for morning sports massage slots."
        "I'm afraid the available slots on Saturday 1st August are limited.",
        "Let me look that up for you. I've got you on oh seven seven six nine.",
        "Right with you. The earliest I have is Monday.",
        f"Let me just check what we have for you {DASH} which clinic were you "
        "thinking of, Alcester or Redditch?",
        "Let me get that booked in for you now.And your surname?",
    ])
    def test_a_duplicate_opener_is_removed(self, said):
        out = join_after_head(said, f"Let me see {DASH}")
        low = out.lower()
        assert not low.startswith(("let me check", "let me look", "let me pull",
                                   "let me just check", "let me get that booked",
                                   "just a moment", "right with you", "one moment"))
        assert out, "the whole reply must never be stripped away"

    def test_a_reply_that_is_ONLY_an_opener_is_kept(self):
        """Saying it twice beats saying nothing.

        "Let me pull that up for you." is a whole stored reply with no payload
        behind it. Stripping the opener would leave the turn with no audio at
        all — a dead-end filler, the very defect this work removes. So the
        duplicate is tolerated here and nowhere else.
        """
        said = "Let me pull that up for you."
        assert join_after_head(said, f"Let me see {DASH}") == said

    def test_the_payload_itself_survives(self):
        out = join_after_head(
            "Just a moment while I check what's available.The available slots "
            "for Tuesday are Number 1, five in the evening.",
            f"Let me see {DASH}",
        )
        assert "five in the evening" in out
        assert "Number 1" in out


class TestTheReplyJoinsOntoAnOpenHead:
    def test_an_open_head_decapitalises_the_payload(self):
        # "Let me see - the available slots for Tuesday are..." is one sentence.
        out = join_after_head("The available slots for Tuesday are ready.",
                              f"Let me see {DASH}")
        assert out.startswith("the available slots")

    def test_a_comma_head_also_joins(self):
        out = join_after_head("That's free.", "Right,")
        assert out.startswith("that's free")

    def test_a_closed_head_does_not_decapitalise(self):
        # A head ending in the ellipsis is a finished sentence — the payload
        # must keep its capital or the join reads as a grammatical error.
        out = join_after_head("The available slots are ready.",
                              f"One moment{ELLIPSIS}")
        assert out.startswith("The available slots")

    @pytest.mark.parametrize("payload,first", [
        ("Friday 14th August at ten is available.", "Friday"),
        ("Monday the 10th works.",                  "Monday"),
        ("August is busy, I'm afraid.",             "August"),
        ("I can see an appointment on Friday.",     "I can"),
        ("I'm afraid that's gone.",                 "I'm afraid"),
    ])
    def test_proper_nouns_keep_their_capital(self, payload, first):
        # Day names are how these replies usually start; lowercasing them would
        # trade one audible defect for a written one the judge reads back.
        out = join_after_head(payload, f"Let me see {DASH}")
        assert out.startswith(first)


class TestTheSeamCannotWeldTwoSentences:
    def test_no_missing_space_at_a_sentence_boundary(self):
        # 106 stored fragments read "...what's available.The available slots".
        # Doing the join in one place makes that unrepresentable.
        out = join_after_head(
            "Just a moment while I check what's available.The available slots "
            "for Tuesday are ready.",
            f"Let me see {DASH}",
        )
        import re
        assert not re.search(r"[a-z][.!?][A-Z]", out)


class TestItIsInertWithoutAHead:
    def test_no_head_means_no_change(self):
        # Nothing was spoken, so nothing is a duplicate. A reply that genuinely
        # opens with "Let me check" is legitimate when the caller heard no filler.
        said = "Let me check what's available for you."
        assert join_after_head(said, "") == said

    def test_empty_reply_is_returned_untouched(self):
        assert join_after_head("", f"Let me see {DASH}") == ""
