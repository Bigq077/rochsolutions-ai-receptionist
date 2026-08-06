"""
Never announce a transfer that cannot be placed.

The theorem_v3 greeting invites the caller to "press 1 to speak to Mark
directly".  On digit 1 the old code spoke "Transferring you to Mark now — one
moment." and *then* called _on_transfer_request().  Two configurations make that
a lie, and in both the caller hears a promise followed by silence until they
give up:

  * TRANSFER_DISABLED set — the sweep kill-switch left on, or set on the wrong
    Render service.  _handle_transfer returns without dialing anyone.
  * no dial target — theorem/clinic.json carries NO 'transfer_phone', so
    TRANSFER_FALLBACK_NUMBER is the only source.  Blank it and the TwiML is
    <Dial></Dial>, which drops the caller instantly.

The fix does not trust the config in either direction: it resolves the target
BEFORE speaking and stays on the line when there isn't one.

Companion to test_transfer_disabled_gate.py — that file proves no leg is placed,
this one proves the caller is not told otherwise.
"""

import importlib
import inspect

import pytest


def _reload_config(monkeypatch, *, disabled=None, fallback=None):
    if disabled is None:
        monkeypatch.delenv("TRANSFER_DISABLED", raising=False)
    else:
        monkeypatch.setenv("TRANSFER_DISABLED", disabled)
    if fallback is None:
        monkeypatch.delenv("TRANSFER_FALLBACK_NUMBER", raising=False)
    else:
        monkeypatch.setenv("TRANSFER_FALLBACK_NUMBER", fallback)
    import app.config
    return importlib.reload(app.config)


# ── resolve_transfer_target: the single source of truth ─────────────────────

def test_returns_none_when_kill_switch_is_on(monkeypatch):
    _reload_config(monkeypatch, disabled="1", fallback="+447870166861")
    from app.routes.realtime import resolve_transfer_target

    assert resolve_transfer_target({"clinic_id": "theorem"}) is None


def _no_clinic_transfer_phone(monkeypatch):
    """A clinic whose config carries no transfer_phone — env is then the only source."""
    import app.clinic_config

    monkeypatch.setattr(
        app.clinic_config, "get_clinic", lambda _cid: {}, raising=False
    )


def test_returns_none_when_no_dial_target(monkeypatch):
    _reload_config(monkeypatch, disabled=None, fallback="")
    _no_clinic_transfer_phone(monkeypatch)
    from app.routes.realtime import resolve_transfer_target

    assert resolve_transfer_target({"clinic_id": "theorem"}) is None


def test_whitespace_only_target_is_not_a_target(monkeypatch):
    _reload_config(monkeypatch, disabled=None, fallback="   ")
    _no_clinic_transfer_phone(monkeypatch)
    from app.routes.realtime import resolve_transfer_target

    assert resolve_transfer_target({"clinic_id": "theorem"}) is None


def test_theorem_has_a_real_dial_target_today(monkeypatch):
    """
    Pressing 1 must reach Mark even with TRANSFER_FALLBACK_NUMBER unset.
    The number is hardcoded in app/clinic_config.py (NOT clinic.json, and NOT
    the env var — the env default is a different number entirely). If this
    fails, press-1 is dialing someone who is not Mark.
    """
    _reload_config(monkeypatch, disabled=None, fallback=None)
    from app.routes.realtime import resolve_transfer_target

    assert resolve_transfer_target({"clinic_id": "theorem_v3"}) == "+447870166861"


def test_returns_the_number_when_configured(monkeypatch):
    """Guard against a fix that suppresses every transfer unconditionally."""
    _reload_config(monkeypatch, disabled=None, fallback="+447870166861")
    from app.routes.realtime import resolve_transfer_target

    assert resolve_transfer_target({"clinic_id": "theorem"}) == "+447870166861"


def test_clinic_transfer_phone_wins_over_the_env_fallback(monkeypatch):
    _reload_config(monkeypatch, disabled=None, fallback="+447000000000")
    from app.routes.realtime import resolve_transfer_target

    session = {"clinic_id": "theorem"}
    with_clinic = {"transfer_phone": "+447870166861"}
    import app.clinic_config

    monkeypatch.setattr(
        app.clinic_config, "get_clinic", lambda _cid: with_clinic, raising=False
    )
    assert resolve_transfer_target(session) == "+447870166861"


# ── the press-1 site must consult it before speaking ───────────────────────

def _intro_dtmf_source():
    """The theorem_v3 intro-DTMF branch of the digit handler."""
    from app.media_streams import connection

    src = inspect.getsource(connection)
    start = src.index('if self.session.get("v3_intro_dtmf_active"):')
    end = src.index("theorem_v3 slot / time selection", start)
    return src[start:end]


def test_target_is_resolved_before_the_announcement():
    """
    The check must come FIRST. Speaking is irreversible — TTS is already on the
    wire by the time _on_transfer_request decides it cannot dial.
    """
    branch = _intro_dtmf_source()
    assert "resolve_transfer_target" in branch, (
        "the press-1 path no longer checks whether a transfer can actually happen"
    )
    assert branch.index("resolve_transfer_target") < branch.index(
        "Transferring you to Mark now"
    ), "the transfer is announced before the target is resolved"


def test_no_target_means_no_promise_and_no_dial():
    branch = _intro_dtmf_source()
    guard = branch.index("if not _target:")
    promise = branch.index("Transferring you to Mark now")
    assert guard < promise
    # Inside the no-target arm, the caller is kept — not promised and dropped.
    no_target_arm = branch[guard:promise]
    assert "return" in no_target_arm
    assert "transfer_requested_by_caller" not in no_target_arm, (
        "the no-target path still flags a transfer request"
    )
    assert "_on_transfer_request" not in no_target_arm


def test_fallback_question_arms_the_watchdog():
    """
    The fallback ends in a question. DTMF never reaches on_transcript_received,
    so an unarmed watchdog reproduces the dead air this guard exists to prevent.
    """
    branch = _intro_dtmf_source()
    guard = branch.index("if not _target:")
    promise = branch.index("Transferring you to Mark now")
    no_target_arm = branch[guard:promise]
    assert "on_question_asked" in no_target_arm


# ── _handle_transfer must not emit an empty <Dial> ─────────────────────────

async def test_handle_transfer_aborts_rather_than_dialing_nothing(monkeypatch):
    _reload_config(monkeypatch, disabled=None, fallback="")
    _no_clinic_transfer_phone(monkeypatch)
    from app.routes import realtime
    from unittest.mock import patch

    with patch("twilio.rest.Client") as mock_client:
        await realtime._handle_transfer("CA_test", {"clinic_id": "theorem"})

    assert not mock_client.called, "an empty <Dial> would have been sent to Twilio"
