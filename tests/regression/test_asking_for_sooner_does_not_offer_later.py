"""B-137 — "I can't wait a week" must not be answered with days further away.

CA5685a2ab, 4 Sep 2026, theorem_v3, Redditch, build 4eda31f3c8c9.

Redditch runs Thursdays, so the 30-day sweep found 10, 17, 24 September and
1 October. Susie offered the 10th. The caller said:

    "no i need it as soon as possible i can't wait a week"

`choose_presented_days` leads with the days the caller has NOT heard -- right
for "what else have you got", exactly inverted here, because the day he had
heard was the only one that could possibly satisfy him. She read out the 17th,
the 24th and the 1st of October. He hung up seven seconds in.

Two halves, tested separately:
  * the SELECTION must lead with the earliest days;
  * the SENTENCE must say why, and offer the clinic that runs more days --
    as a question about its rota, never as a claim about its slots.
"""
from datetime import date, timedelta

import pytest

from app.tools.slot_followup import (
    acknowledge_sparse_rota,
    caller_wants_soonest,
    choose_presented_days,
    record_spoken_slots,
)


def _heard_the_tenth(days, **extra):
    """Session state after the 10th was read out, via the real writer.

    Hand-building `slot_starts_spoken` is not enough: the reader declines any
    record with no `_SPOKEN_FP_KEY` fingerprint beside it, so a hand-built
    session silently reads as "heard nothing" and the control case below
    passes for the wrong reason.
    """
    session = {"available_days": days}
    session.update(extra)
    record_spoken_slots(session, days[0]["slots"])
    return session


def _days(*isos):
    """available_days shaped as the availability payload builds them."""
    return [
        {
            "date": iso,
            "day_label": date.fromisoformat(iso).strftime("%A %d %B"),
            "slot_times": ["09:00", "13:00"],
            "slot_times_spoken": ["nine in the morning", "one in the afternoon"],
            "slots": [
                {"start": f"{iso}T09:00:00+01:00"},
                {"start": f"{iso}T13:00:00+01:00"},
            ],
        }
        for iso in isos
    ]


# The four Thursdays the sweep actually returned on the call.
THURSDAYS = ("2026-09-10", "2026-09-17", "2026-09-24", "2026-10-01")


class TestSelection:
    def test_the_call_that_lost_it(self):
        """The exact state at 09:54:35: the 10th heard, ASAP captured."""
        days = _days(*THURSDAYS)
        # the 10th was read out at 09:54:15
        session = _heard_the_tenth(days, day_preference="as soon as possible")
        kept = choose_presented_days(session, days, 3)
        dates = [d["date"] for d in kept]

        # Before the fix this was ['2026-09-17', '2026-09-24', '2026-10-01'].
        assert dates[0] == "2026-09-10", (
            f"asked for the soonest and led with {dates[0]} — the earliest day "
            f"found was 2026-09-10"
        )
        assert "2026-10-01" not in dates

    def test_what_else_still_leads_with_the_unheard(self):
        """The B-116/B-119 behaviour this must not disturb."""
        days = _days(*THURSDAYS)
        session = _heard_the_tenth(days)
        assert session.get("slot_starts_spoken"), "the writer recorded nothing"
        kept = choose_presented_days(session, days, 3)
        assert [d["date"] for d in kept] == [
            "2026-09-17", "2026-09-24", "2026-10-01",
        ]

    def test_chronological_order_survives(self):
        """The keypad map is built from this order — 1 must be the earliest."""
        session = {"day_preference": "as soon as possible"}
        kept = choose_presented_days(session, _days(*THURSDAYS), 3)
        dates = [d["date"] for d in kept]
        assert dates == sorted(dates)

    @pytest.mark.parametrize(
        "pref", ["as soon as possible", "today", "tomorrow", "this week"],
    )
    def test_every_soonest_phrasing_counts(self, pref):
        assert caller_wants_soonest({"day_preference": pref})

    @pytest.mark.parametrize("pref", ["next week", "whenever", "thursday", "", None])
    def test_a_scoped_request_is_not_a_soonest_request(self, pref):
        assert not caller_wants_soonest({"day_preference": pref})


class TestSentence:
    NOTE = {
        "location_label": "Redditch",
        "open_days_phrase": "Thursdays",
        "other_location_label": "Alcester",
        "other_open_days_word": "five",
    }
    OFFER = "Number 1, Thursday 10th September — nine in the morning."

    def test_it_names_the_rota_and_offers_the_other_clinic(self):
        out, action = acknowledge_sparse_rota(self.OFFER, self.NOTE)
        assert action == "applied"
        assert out.startswith("The only days I've got at Redditch are Thursdays.")
        assert out.endswith("Alcester runs five days a week. Shall I check there instead?")
        assert self.OFFER in out, "the times themselves must be untouched"

    def test_it_never_claims_the_other_clinic_has_slots(self):
        """The whole reason this ends in a question: nothing has queried Alcester."""
        out, _ = acknowledge_sparse_rota(self.OFFER, self.NOTE)
        low = out.lower()
        for promise in (
            "sooner at alcester", "i have something at alcester",
            "alcester has", "available at alcester", "earlier at alcester",
        ):
            assert promise not in low, f"promised Alcester availability: {promise!r}"

    def test_it_is_idempotent(self):
        once, _ = acknowledge_sparse_rota(self.OFFER, self.NOTE)
        twice, action = acknowledge_sparse_rota(once, self.NOTE)
        assert action == "unchanged"
        assert twice == once

    @pytest.mark.parametrize(
        "note",
        [
            None, {}, "Redditch",
            {"location_label": "Redditch"},                      # no other clinic
            {"location_label": "Redditch", "open_days_phrase": "Thursdays"},
        ],
    )
    def test_a_half_built_note_says_nothing(self, note):
        out, action = acknowledge_sparse_rota(self.OFFER, note)
        assert action == "unchanged"
        assert out == self.OFFER

    def test_it_stays_short_enough_to_hold(self):
        """B-31: last_bot_prompt is capped at 200 chars and the '?' falls off."""
        out, _ = acknowledge_sparse_rota(self.OFFER, self.NOTE)
        assert len(out) < 260, f"{len(out)} chars is a wall of speech"

    def test_no_em_dash_in_the_added_sentences(self):
        """' — ' splits the TTS chunker mid-phrase — the added text must not."""
        out, _ = acknowledge_sparse_rota("times.", self.NOTE)
        assert " — " not in out


class TestTheOfferIsAnswerable:
    """B-137 — "Shall I check Alcester?" must not be refused by the next guard.

    The already_retrieved guard has ONE escape hatch,
    `_caller_requests_different_day`, and a clinic is not a day. Measured on
    the pre-fix tree: every phrasing of "yes, check Alcester" returned False
    from it while `last_offered_slots` survived (the DTMF map is live, so
    `_should_clear_slot_cache` stands down) -- so the guard answered with
    "present the existing slots" and Susie re-read the same Thursdays.

    Inviting a request the next guard refuses is worse than never offering,
    so the offer and this predicate must ship together.
    """

    SESSION = {"clinic_id": "theorem", "location": "redditch"}

    @staticmethod
    def _asks(utterance, session=None):
        from app.media_streams.llm_stream import (
            _caller_requests_different_location,
        )
        return _caller_requests_different_location(
            [{"role": "user", "content": utterance}],
            session if session is not None else TestTheOfferIsAnswerable.SESSION,
        )

    @pytest.mark.parametrize("utterance", [
        "yes please check alcester",
        "yeah try alcester",
        "go on then alcester",
        "can you look at alcester instead",
        "yes check the other clinic",
        "what about the other one",
    ])
    def test_accepting_the_offer_reaches_a_real_lookup(self, utterance):
        assert self._asks(utterance), (
            f"{utterance!r} would be answered by re-reading the Redditch times"
        )

    @pytest.mark.parametrize("utterance", [
        "yes please",
        "that works for me",
        "number 2",
        "nine in the morning",
        "yeah the 10th is fine",
        "redditch is fine",
        "no i can't wait a week",
    ])
    def test_it_does_not_reopen_the_duplicate_lookup_hole(self, utterance):
        """The guard exists because the model re-called on an acceptance."""
        assert not self._asks(utterance)

    def test_a_location_name_inside_another_word_does_not_fire(self):
        assert not self._asks("i'll take the alcestershire one")

    def test_a_single_site_clinic_can_never_satisfy_it(self):
        assert not self._asks(
            "check the other clinic",
            {"clinic_id": "vital_edge", "location": "main"},
        )

    def test_the_current_location_is_not_a_different_location(self):
        assert not self._asks("redditch please")
