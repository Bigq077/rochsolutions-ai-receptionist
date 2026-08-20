"""request_callback — the durable half of 'I'll pass that on'.

CAc36368cbeb (2026-08-13, vital_edge): caller asked for a quick chat with
Jonathan. Susie took Dylan Wilson's name, confirmed the number, promised
Jonathan would call back — and never notified anyone. The FAQ said she could
'note your details'; it did not force a tool, so the promise was empty.

This pins: (1) the tool exists and texts the clinic owner, (2) both prompt
engines mandate it before a callback promise, (3) Vital Edge's brief-call FAQ
names the tool.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.clinic_config import get_clinic
from app.tools.receptionist_tools import (
    TOOL_SCHEMAS,
    _exec_request_callback,
    _owner_callback_number,
)


def test_request_callback_is_in_the_tool_schema():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert "request_callback" in names


def test_vital_edge_owner_number_is_jonathan():
    clinic = get_clinic("vital_edge")
    assert _owner_callback_number(clinic).endswith("2307")


@pytest.mark.asyncio
async def test_request_callback_texts_owner_and_arms_flags():
    # Dylan's number WAS confirmed on CAc36368cbeb — the miss was that nothing
    # was told, not that the number was unchecked. Stating it here keeps this
    # test about the notification: B-69 (2026-08-20) put the A1 phone gate on
    # request_callback, so an unconfirmed session now buys a confirmation turn
    # instead of a text, and this would be asserting that instead.
    session = {
        "clinic_id": "vital_edge",
        "call_sid": "CAtest",
        "phone_confirmed": True,
    }
    sent = {}

    async def _fake_send(*, to, message, **_kwargs):
        sent["to"] = to
        sent["message"] = message
        return "SMfake"

    with patch("app.notifications.sms.send_sms", new=AsyncMock(side_effect=_fake_send)):
        result = await _exec_request_callback(
            {
                "patient_name": "Dylan Wilson",
                "phone": "+13102695437",
                "notes": "wants a quick chat with Jonathan before booking",
            },
            session,
        )
        # create_task schedules the send — let it run
        await asyncio.sleep(0)

    assert result.get("success") is True
    assert session.get("callback_write_confirmed") is True
    assert session.get("_waitlist_pinged") is True
    assert session.get("human_requested") is True
    assert session.get("collected", {}).get("name") == "Dylan Wilson"
    assert "Dylan Wilson" in sent["message"]
    assert "+13102695437" in sent["message"]
    assert "quick chat" in sent["message"].lower()
    assert sent["to"].endswith("2307")


@pytest.mark.asyncio
async def test_request_callback_refuses_without_name_or_phone():
    session = {"clinic_id": "vital_edge"}
    result = await _exec_request_callback(
        {"patient_name": "", "phone": "+13102695437", "notes": "chat"},
        session,
    )
    assert result.get("success") is False
    assert not session.get("callback_write_confirmed")


def test_template_prompt_mandates_request_callback_before_promise():
    from app.prompts.clinic_template_prompt import build_clinic_prompt

    body = build_clinic_prompt({}, get_clinic("vital_edge"))[0].lower()
    assert "request_callback" in body
    assert "never say" in body and "pass that on" in body


def test_theorem_prompt_mandates_request_callback():
    from app.prompts.susie_system_prompt import build_system_prompt_parts

    parts = build_system_prompt_parts({"clinic_id": "theorem_v3"})
    body = "\n".join(parts).lower()
    assert "request_callback" in body


def test_vital_edge_brief_call_faq_names_the_tool():
    clinic = get_clinic("vital_edge")
    faqs = clinic.get("faq") or []
    brief = next(
        (f for f in faqs if "speak to jonathan before" in (f.get("q") or "").lower()),
        None,
    )
    assert brief is not None
    assert "request_callback" in (brief.get("a") or "")
