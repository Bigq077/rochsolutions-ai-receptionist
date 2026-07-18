"""End-to-end tests for the dashboard/weekly CLIs against a seeded SQLite store."""
from __future__ import annotations

from app import config
from app.obs import dashboard, store, weekly


def test_list_calls_filters_by_window_and_clinic(seeded_store):
    from datetime import datetime, timedelta, timezone
    since = datetime.now(timezone.utc) - timedelta(days=7)
    week = store.list_calls(since=since)
    assert {c["call_sid"] for c in week} == {"CA1", "CA2", "CA3", "CA4"}  # CA5 is older
    theorem = store.list_calls(since=since, clinic_id="theorem")
    assert {c["call_sid"] for c in theorem} == {"CA1", "CA2", "CA4"}


def test_dashboard_runs_and_shows_clinics(seeded_store, capsys):
    rc = dashboard.main(["--weeks", "8"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "theorem" in out and "jv" in out
    assert "book%" in out  # header rendered


def test_dashboard_html_export(seeded_store, tmp_path, capsys):
    html = tmp_path / "dash.html"
    rc = dashboard.main(["--weeks", "8", "--html", str(html)])
    assert rc == 0
    assert html.exists()
    content = html.read_text(encoding="utf-8")
    assert "<table>" in content and "theorem" in content


def test_weekly_lists_bottom_decile(seeded_store, capsys):
    rc = weekly.main(["--days", "7"])
    out = capsys.readouterr().out
    assert rc == 0
    # CA2 (score 1) is the worst → must appear in the review list.
    assert "CA2" in out
    assert "Bottom-decile" in out
    assert "to_scenario" in out  # points to the next step


def test_dashboard_no_db_returns_2(monkeypatch, capsys):
    monkeypatch.setattr(config, "DATABASE_URL", "")
    store.reset_engine()
    try:
        assert dashboard.main(["--weeks", "8"]) == 2
        err = capsys.readouterr().err
        assert "not set" in err and "DATABASE_URL" in err
    finally:
        store.reset_engine()
