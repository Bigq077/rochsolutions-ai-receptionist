"""A turn that spoke only a filler must not be logged as "superseded".

The hold-speech work is judged on one number: how often Susie says "Let me just
check that…" and then never delivers anything. In the obs corpus for 25 Jul -
21 Aug 2026 that happened 32 times, and it was invisible to the latency
instrument, because a turn that never reaches content_t4 is closed by the NEXT
dispatch and was tagged "superseded" — the same tag a harmless split utterance
gets. One bucket held both "the caller heard a promise that went nowhere" and
"nothing was spoken and a newer dispatch took over".

close_outcome splits them. content_t4 is always None at that point (emit() fires
at content_t4 and latches _emitted), so t4 — did the caller hear ANY audio this
turn — is the whole discriminator.
"""
from app.media_streams.latency_timing import close_outcome, summarise


class TestCloseOutcome:
    def test_audio_played_but_no_content_is_a_dead_end(self):
        # t4 stamped: the caller heard something, and it can only have been a
        # filler, because content never arrived.
        assert close_outcome(1234.5) == "no_content"

    def test_nothing_spoken_is_still_superseded(self):
        # No audio at all — a split utterance or deterministic branch replaced
        # the record. Not caller behaviour, must not pollute abandoned-rate.
        assert close_outcome(None) == "superseded"

    def test_t4_of_zero_is_not_treated_as_unstamped(self):
        # Guards the obvious refactor into a truthiness check. monotonic() can
        # legitimately be small, and "is None" is the stamped/unstamped contract
        # TurnTiming.stamp uses everywhere else.
        assert close_outcome(0.0) == "no_content"


class TestSummariseCountsDeadEnds:
    def test_dead_end_turns_are_counted(self):
        turns = [
            {"outcome": "completed",  "ttfa_ms": 900, "content_ttfa_ms": 900,
             "llm_ttft_ms": 400},
            {"outcome": "no_content", "ttfa_ms": 350, "content_ttfa_ms": -1,
             "llm_ttft_ms": -1},
            {"outcome": "no_content", "ttfa_ms": 350, "content_ttfa_ms": -1,
             "llm_ttft_ms": -1},
            {"outcome": "superseded", "ttfa_ms": -1,  "content_ttfa_ms": -1,
             "llm_ttft_ms": -1},
        ]
        s = summarise(turns)
        assert s["no_content_turns"] == 2
        assert s["turns_logged"] == 4

    def test_missing_measurements_never_enter_a_percentile(self):
        # The -1 convention: a dead-end turn reports content_ttfa_ms = -1, and
        # letting that into a percentile is how a latency number becomes a lie.
        turns = [
            {"outcome": "completed",  "ttfa_ms": 1000, "content_ttfa_ms": 1000,
             "llm_ttft_ms": 500},
            {"outcome": "no_content", "ttfa_ms": 350,  "content_ttfa_ms": -1,
             "llm_ttft_ms": -1},
        ]
        s = summarise(turns)
        assert s["turns_measured"] == 2          # both reached audio (t4)
        assert s["content_ttfa_p50_ms"] == 1000  # the -1 excluded, not averaged

    def test_a_call_of_only_dead_ends_reports_no_content_ttfa(self):
        turns = [{"outcome": "no_content", "ttfa_ms": 350,
                  "content_ttfa_ms": -1, "llm_ttft_ms": -1}]
        s = summarise(turns)
        assert s["no_content_turns"] == 1
        assert s["content_ttfa_p50_ms"] is None or s["content_ttfa_p50_ms"] < 0
