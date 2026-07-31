# tests/regression/test_build_sha_on_the_call_record.py
"""
Every call records the commit that served it, instead of it being inferred.

Until 2026-07-31, "which build served this call?" was arithmetic:
scripts/detect_defects.py holds deploy timestamps and buckets each call by the
most recent boundary before it started. That list misattributed calls four times.
The last one, on 31 Jul, set a boundary from push time plus the usual 3-6 minute
Render estimate and put CA42486ff4 on the previous build — the deploy had landed
in about a minute. The call only disproved it because it happened to carry guard
counters that existed on no earlier build.

A wrong build label inverts the conclusion: a fix looks unproven when it worked,
or proven when it never ran. Both send someone to make a phone call for nothing.

The boundary list stays — it is the only answer for the calls already recorded
without a SHA, and the fallback whenever one is unavailable.
"""
from __future__ import annotations

import pytest

import app.build_info as build_info


@pytest.fixture(autouse=True)
def _clear_cache():
    """build_sha() caches for the process; each test needs a clean resolve."""
    build_info._cached = None
    yield
    build_info._cached = None


class TestResolution:
    def test_prefers_the_build_time_constant(self, monkeypatch):
        """app/_version.py is written by scripts/write_version.py at build time —
        the production path, and the only one guaranteed present in a container
        with no .git and no git binary."""
        import sys
        import types

        mod = types.ModuleType("app._version")
        mod.GIT_COMMIT = "abc1234"          # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "app._version", mod)
        monkeypatch.setenv("RENDER_GIT_COMMIT", "should-not-win")
        assert build_info.build_sha() == "abc1234"

    def test_falls_back_to_the_render_environment_variable(self, monkeypatch):
        import sys

        # Simulate a container where the build step did not write _version.py.
        monkeypatch.setitem(sys.modules, "app._version", None)
        monkeypatch.setenv("RENDER_GIT_COMMIT", "0123456789abcdef0123")
        assert build_info.build_sha() == "0123456789ab", "truncated to fit the column"

    def test_resolves_something_in_this_repo(self):
        """A real answer locally, via git — not the 'unknown' placeholder."""
        sha = build_info.build_sha()
        assert sha and sha != build_info.UNKNOWN
        assert len(sha) <= 12

    def test_never_raises_when_nothing_is_available(self, monkeypatch):
        """This runs on call teardown. An exception would lose the call record,
        which is far worse than an unlabelled one."""
        import sys

        monkeypatch.setitem(sys.modules, "app._version", None)
        monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
        monkeypatch.setattr(
            build_info.subprocess, "check_output",
            lambda *a, **k: (_ for _ in ()).throw(OSError("no git")),
        )
        assert build_info.build_sha() == build_info.UNKNOWN

    def test_result_is_cached(self, monkeypatch):
        """Called once per teardown; the answer cannot change while the process
        lives."""
        first = build_info.build_sha()
        monkeypatch.setattr(build_info, "_resolve", lambda: "different")
        assert build_info.build_sha() == first


class TestItReachesTheCallRecord:
    def test_the_column_is_migrated_idempotently(self):
        """Added to _ADDED_COLUMNS, not just to the model — create_all only
        creates missing tables, never missing columns on an existing one, and the
        production table already exists."""
        from app.obs.store import _ADDED_COLUMNS

        assert _ADDED_COLUMNS.get("build_sha") == "VARCHAR(16)"

    def test_the_row_mapping_carries_it(self):
        from app.obs.store import _row_from_record

        row = _row_from_record({"call_sid": "CAtest", "build_sha": "deadbee"}, [])
        assert row.build_sha == "deadbee"

    def test_a_record_without_a_sha_is_still_written(self):
        """Historic replays and any caller that predates the field must not
        break — the column is nullable and the fallback covers them."""
        from app.obs.store import _row_from_record

        assert _row_from_record({"call_sid": "CAold"}, []).build_sha is None


class TestScoringPrefersTheRecordedSha:
    @staticmethod
    def _build_of(call):
        import scripts.detect_defects as dd

        return dd.build_of(call)

    def test_a_recorded_sha_wins_and_is_never_ambiguous(self):
        """A recorded SHA is not near a boundary — it IS the answer, so the
        ambiguity window that exists to protect boundary arithmetic must not
        suppress it."""
        from datetime import datetime, timezone

        label, ambiguous = self._build_of({
            "build_sha": "cdc2177",
            # Deliberately one second after a boundary: arithmetic would call
            # this ambiguous and drop it from the live-defect list.
            "start_utc": datetime(2026, 7, 31, 1, 18, 1, tzinfo=timezone.utc),
        })
        assert label == "cdc2177"
        assert ambiguous is False

    @pytest.mark.parametrize("sha", [None, "", "unknown"])
    def test_falls_back_to_the_boundary_list_without_one(self, sha):
        from datetime import datetime, timezone

        label, _ = self._build_of({
            "build_sha": sha,
            "start_utc": datetime(2026, 7, 30, 23, 30, tzinfo=timezone.utc),
        })
        assert label == "ad09f3e", "pre-SHA calls still come from BUILDS"
