"""
The SMS call-mode toggle: a clinic texts OFF and their own phone rings first.

Built from docs/plan/SMS_CALL_MODE_TOGGLE_BUILD.md (scoped 2026-08-04). The
call-routing half already existed — `/ms/incoming` emits a human-first <Dial>
with a "press 1" whisper leg. What did not exist was any way to change it
without a commit and a redeploy, because `call_overflow.enabled` is read from a
repo file through an mtime-keyed cache.

Three properties carry the risk, and they are what most of this file tests:

  1. RESOLUTION NEVER RAISES. resolve_overflow sits on the critical path of
     every inbound call. A broken toggle must degrade to the clinic.json
     default — a clinic whose phone stops working because a convenience
     feature had a bad day is far worse than a toggle that stops toggling.

  2. A WRITE THAT CANNOT BE TRUSTED IS REFUSED, NOT FAKED. redis_set_json
     silently degrades to a per-process dict when redis_client is None. Across
     Render workers that is a toggle which works on some requests and not
     others — the practitioner's phone rings or doesn't, at random. set_mode
     returns None instead, so the caller declines to confirm.

  3. A COMMAND IS NEVER A PATIENT TEXT, AND A PATIENT TEXT IS NEVER A COMMAND.
     Exact-match only, authorisation before dispatch, and STOP left well alone
     because Twilio intercepts it at carrier level.
"""

import pytest

from app import clinic_call_mode as ccm
from app.routes import twilio as tw


CLINIC_OFF = {"call_overflow": {"enabled": False, "dial_phone": "+447586605462"}}
CLINIC_ON = {"call_overflow": {"enabled": True, "dial_phone": "+447586605462"}}
CLINIC_BARE = {}  # theorem / demo — legacy CLINICS dict, no overflow block


@pytest.fixture
def redis_live(monkeypatch):
    """Pretend a real Redis connection exists."""
    monkeypatch.setattr(ccm, "_redis_live", lambda: True)


# ── 1 — the resolver ────────────────────────────────────────────────────────


async def test_no_override_uses_config_true(monkeypatch, redis_live):
    monkeypatch.setattr(
        "app.storage.redis_store.redis_get_json", _async_return(None), raising=False
    )
    assert await ccm.resolve_overflow("jv_v1", CLINIC_ON) == (True, "config")


async def test_no_override_uses_config_false(monkeypatch, redis_live):
    monkeypatch.setattr(
        "app.storage.redis_store.redis_get_json", _async_return(None), raising=False
    )
    assert await ccm.resolve_overflow("jv_v1", CLINIC_OFF) == (False, "config")


async def test_override_human_first_beats_config_false(monkeypatch, redis_live):
    monkeypatch.setattr(
        "app.storage.redis_store.redis_get_json",
        _async_return({"mode": "human_first"}), raising=False,
    )
    assert await ccm.resolve_overflow("jv_v1", CLINIC_OFF) == (True, "override")


async def test_override_ai_first_beats_config_true(monkeypatch, redis_live):
    """The toggle must work in BOTH directions. A clinic that has overflow on
    by default and texts ON expects Susie to answer."""
    monkeypatch.setattr(
        "app.storage.redis_store.redis_get_json",
        _async_return({"mode": "ai_first"}), raising=False,
    )
    assert await ccm.resolve_overflow("jv_v1", CLINIC_ON) == (False, "override")


async def test_expired_override_falls_back_to_config(monkeypatch, redis_live):
    """Expiry is Redis SETEX alone — an expired key simply reads as absent.
    There is no scheduler and no cleanup job to go wrong."""
    monkeypatch.setattr(
        "app.storage.redis_store.redis_get_json", _async_return(None), raising=False
    )
    assert await ccm.resolve_overflow("jv_v1", CLINIC_OFF) == (False, "config")


async def test_redis_none_falls_back_to_config(monkeypatch):
    monkeypatch.setattr(ccm, "_redis_live", lambda: False)
    assert await ccm.resolve_overflow("jv_v1", CLINIC_ON) == (
        True, "config:redis_unavailable",
    )


async def test_redis_raising_falls_back_and_does_not_propagate(
    monkeypatch, redis_live
):
    """Property 1. If this ever propagates, /ms/incoming 500s and the clinic's
    number stops answering entirely."""
    async def boom(_key):
        raise RuntimeError("redis exploded")

    monkeypatch.setattr(
        "app.storage.redis_store.redis_get_json", boom, raising=False
    )
    assert await ccm.resolve_overflow("jv_v1", CLINIC_ON) == (
        True, "config:redis_unavailable",
    )


async def test_clinic_without_overflow_block_returns_false(monkeypatch, redis_live):
    monkeypatch.setattr(
        "app.storage.redis_store.redis_get_json", _async_return(None), raising=False
    )
    assert await ccm.resolve_overflow("theorem", CLINIC_BARE) == (False, "config")


async def test_set_mode_refuses_to_write_without_redis(monkeypatch):
    """Property 2 — the whole reason set_mode can return None."""
    monkeypatch.setattr(ccm, "_redis_live", lambda: False)
    wrote = []
    monkeypatch.setattr(
        "app.storage.redis_store.redis_set_json",
        _async_record(wrote), raising=False,
    )
    assert await ccm.set_mode("jv_v1", "human_first", "+447586605462") is None
    assert wrote == [], (
        "set_mode wrote to the per-process fallback dict — across Render "
        "workers that toggle applies on some requests and not others"
    )


async def test_set_mode_refuses_an_unknown_mode(monkeypatch, redis_live):
    wrote = []
    monkeypatch.setattr(
        "app.storage.redis_store.redis_set_json",
        _async_record(wrote), raising=False,
    )
    assert await ccm.set_mode("jv_v1", "banana", "+447586605462") is None
    assert wrote == []


async def test_ttl_is_bounded_and_targets_midnight(monkeypatch, redis_live):
    captured = {}

    async def fake_set(key, value, ttl_seconds=None):
        captured["ttl"] = ttl_seconds
        captured["value"] = value

    monkeypatch.setattr(
        "app.storage.redis_store.redis_set_json", fake_set, raising=False
    )
    payload = await ccm.set_mode("jv_v1", "human_first", "+447586605462")
    assert payload is not None
    assert ccm._TTL_FLOOR_SECONDS <= captured["ttl"] <= ccm._TTL_CAP_SECONDS, (
        "TTL outside its bounds — the cap stops a clock fault pinning a clinic "
        "into front-desk mode for days"
    )
    assert captured["value"]["mode"] == "human_first"
    assert captured["value"]["set_by"] == "+447586605462"


# ── 2 — command parsing ─────────────────────────────────────────────────────


@pytest.mark.parametrize("body,expected", [
    ("OFF", "human_first"), ("off", "human_first"), ("  Off  ", "human_first"),
    ("SUSIE OFF", "human_first"), ("front desk", "human_first"),
    ("ON", "ai_first"), ("susie on", "ai_first"),
    ("STATUS", "status"), ("Susie Status", "status"),
])
def test_off_on_status_synonyms_parse(body, expected):
    assert tw._parse_call_mode_command(body) == expected


def test_stop_is_not_a_toggle_command():
    """STOP, STOPALL, UNSUBSCRIBE and QUIT are carrier-level opt-out keywords
    Twilio intercepts itself. Binding one here would be both broken and a
    compliance problem."""
    for word in tw._SMS_OPT_OUT:
        assert tw._parse_call_mode_command(word) is None, (
            f"{word!r} is a carrier opt-out keyword and must never toggle routing"
        )


@pytest.mark.parametrize("body", [
    "can you turn it off for tomorrow",
    "I'm off on holiday next week",
    "turn susie off please",
    "status update on my booking?",
    "offer",
    "",
])
def test_substring_does_not_toggle(body):
    """Exact match on the whole body. A practitioner writing a sentence that
    happens to contain 'off' must have it delivered as a text, not silently
    reroute their phone."""
    assert tw._parse_call_mode_command(body) is None


# ── 3 — authorisation ───────────────────────────────────────────────────────


def test_authorised_match_across_all_config_keys():
    for clinic in (
        {"call_overflow": {"dial_phone": "+447586605462"}},
        {"transfer_phone": "+447586605462"},
        {"operational": {"transfer_phone": "+447586605462"}},
        {"operational": {"owner_notification_sms": "+447586605462"}},
    ):
        assert tw._sender_is_authorised("+447586605462", clinic), clinic


def test_owner_notification_sms_is_read_from_operational():
    """get_clinic() does NOT flatten owner_notification_sms — it reads back as
    None at the top level. Written top-level-only as the build plan specified,
    this candidate would silently never match, and a clinic whose owner number
    differs from transfer_phone would find the toggle just did not work."""
    clinic = {"operational": {"owner_notification_sms": "+447700900999"}}
    assert tw._sender_is_authorised("+447700900999", clinic)


def test_empty_config_number_never_matches():
    """An empty candidate must be skipped, not matched — otherwise a clinic
    with no configured numbers authorises every sender."""
    clinic = {"call_overflow": {"dial_phone": ""}, "transfer_phone": None}
    assert not tw._sender_is_authorised("", clinic)
    assert not tw._sender_is_authorised("+447700900123", clinic)


def test_unauthorised_sender_is_not_authorised():
    assert not tw._sender_is_authorised("+447700900123", CLINIC_OFF)


# ── 4 — the command handler end to end ──────────────────────────────────────
#
# §2 of the build plan is the rule that matters here: "the override is applied
# only if the confirmation SMS returns a Twilio SID. No SID → delete the key."
# A toggle that silently succeeds is worse than no toggle, because the clinic
# does not know whether their phone is about to ring.


@pytest.fixture
def sent(monkeypatch):
    """Capture confirmation SMS. Returns the list plus a settable SID."""
    box = {"messages": [], "sid": "SM_ok"}

    async def fake_send(to, message, from_number=None):
        box["messages"].append({"to": to, "message": message, "from": from_number})
        return box["sid"]

    monkeypatch.setattr(
        "app.notifications.sms.send_sms", fake_send, raising=False
    )
    return box


async def test_off_writes_override_and_confirms(monkeypatch, redis_live, sent):
    store = {}
    _install_fake_redis(monkeypatch, store)

    resp = await tw._handle_call_mode_command(
        cmd="human_first", sender="+447586605462", clinic=CLINIC_OFF,
        clinic_id="jv_v1", to_number="+447367002651",
    )

    assert resp.status_code == 200
    assert store["call_mode:jv_v1"]["mode"] == "human_first"
    assert len(sent["messages"]) == 1
    body = sent["messages"][0]["message"]
    assert "Front desk mode on" in body
    assert "text ON" in body, "the clinic must be told how to undo it"
    assert sent["messages"][0]["to"] == "+447586605462", (
        "the confirmation must go to the SENDER — a locum toggling from their "
        "own phone would otherwise get nothing"
    )


async def test_no_sid_reverts_override(monkeypatch, redis_live, sent):
    """The §2 rule. Twilio accepted nothing, so the clinic was never told —
    the routing must not be left changed behind their back."""
    store = {}
    _install_fake_redis(monkeypatch, store)
    sent["sid"] = None

    await tw._handle_call_mode_command(
        cmd="human_first", sender="+447586605462", clinic=CLINIC_OFF,
        clinic_id="jv_v1", to_number="+447367002651",
    )

    assert "call_mode:jv_v1" not in store, (
        "the override survived a confirmation SMS that never sent — the clinic "
        "has no idea their phone is about to ring"
    )


async def test_redis_down_reports_failure_and_claims_nothing(monkeypatch, sent):
    monkeypatch.setattr(ccm, "_redis_live", lambda: False)

    await tw._handle_call_mode_command(
        cmd="human_first", sender="+447586605462", clinic=CLINIC_OFF,
        clinic_id="jv_v1", to_number="+447367002651",
    )

    body = sent["messages"][0]["message"]
    assert "couldn't change that" in body
    assert "still being answered as normal" in body, (
        "the failure copy must state the ACTUAL routing, not just apologise"
    )
    assert "Front desk mode on" not in body


async def test_status_is_read_only(monkeypatch, redis_live, sent):
    store = {}
    _install_fake_redis(monkeypatch, store)

    await tw._handle_call_mode_command(
        cmd="status", sender="+447586605462", clinic=CLINIC_OFF,
        clinic_id="jv_v1", to_number="+447367002651",
    )

    assert store == {}, "STATUS wrote something"
    assert "I'm answering all calls" in sent["messages"][0]["message"]


async def test_on_puts_susie_back_and_confirms(monkeypatch, redis_live, sent):
    store = {}
    _install_fake_redis(monkeypatch, store)

    await tw._handle_call_mode_command(
        cmd="ai_first", sender="+447586605462", clinic=CLINIC_ON,
        clinic_id="jv_v1", to_number="+447367002651",
    )

    assert store["call_mode:jv_v1"]["mode"] == "ai_first"
    assert "Back on" in sent["messages"][0]["message"]


# ── helpers ─────────────────────────────────────────────────────────────────


def _install_fake_redis(monkeypatch, store: dict):
    """A dict standing in for Redis, wired into all three helpers."""
    async def _get(key):
        return store.get(key)

    async def _set(key, value, ttl_seconds=None):
        store[key] = value

    async def _del(key):
        store.pop(key, None)

    monkeypatch.setattr("app.storage.redis_store.redis_get_json", _get, raising=False)
    monkeypatch.setattr("app.storage.redis_store.redis_set_json", _set, raising=False)
    monkeypatch.setattr("app.storage.redis_store.redis_delete_key", _del, raising=False)


def _async_return(value):
    async def _f(*a, **k):
        return value
    return _f


def _async_record(sink):
    async def _f(key, value, ttl_seconds=None):
        sink.append((key, value, ttl_seconds))
    return _f
