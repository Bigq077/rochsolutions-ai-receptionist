"""
Every live branch inherits SHEETS_ENABLED=false from latency-eval, so a clinic
can write no call records at all while every log line reads healthy. The skip
used to be a bare print() — no level, no logger name — so grepping the Render
log for warnings found nothing, and the append's own warning pointed the reader
at three OTHER causes that cannot fire when the flag is off.

These tests pin the diagnosability, not the flag: with SHEETS_ENABLED off the
operator must be able to find the reason from the log alone.
"""

import importlib
import logging

import pytest


def _reload_handoff(monkeypatch, sheets_enabled: str):
    monkeypatch.setenv("SHEETS_ENABLED", sheets_enabled)
    import app.tools.handoff as handoff
    return importlib.reload(handoff)


def test_disabled_sheets_logs_a_warning_naming_the_flag(monkeypatch, caplog):
    """The skip must be a WARNING that names SHEETS_ENABLED — not a print."""
    handoff = _reload_handoff(monkeypatch, "false")

    with caplog.at_level(logging.WARNING, logger=handoff.logger.name):
        assert handoff._get_service() is None

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "disabled Sheets produced no WARNING — the reason is invisible"
    assert any("SHEETS_ENABLED" in r.getMessage() for r in warnings), (
        "the warning must name SHEETS_ENABLED so an operator can act on it; "
        f"got {[r.getMessage() for r in warnings]}"
    )


def test_skip_message_lists_the_flag_among_the_causes(monkeypatch, caplog):
    """
    _append_values tells the reader which warning to look for. If SHEETS_ENABLED
    is not in that list, it sends them hunting for causes that cannot apply.
    """
    handoff = _reload_handoff(monkeypatch, "false")

    with caplog.at_level(logging.WARNING, logger=handoff.logger.name):
        assert handoff._append_values([["a"]], "Messages") is False

    skip = [r.getMessage() for r in caplog.records if "SKIPPED" in r.getMessage()]
    assert skip, "no SKIPPED warning was emitted"
    assert "SHEETS_ENABLED" in skip[0], (
        "the SKIPPED warning must list SHEETS_ENABLED as a possible cause — "
        "it is the only one that fires when the flag is off"
    )


def teardown_module(module):
    """Leave the module in its normal import state for the rest of the suite."""
    import app.tools.handoff as handoff
    importlib.reload(handoff)
