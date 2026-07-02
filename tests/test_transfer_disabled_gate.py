"""
tests/test_transfer_disabled_gate.py
------------------------------------
TDD red-phase tests for a TRANSFER_DISABLED safety gate.

GOAL (staging safety). During the production sign-off sweep the staging service
shares production's Twilio account, so a red-flag / "let me speak to a human"
call bridges a REAL outbound call leg to the clinic's transfer_phone
(+447870166861 — a real staff/Mark number) AND fires a real "Susie is
transferring a patient" SMS to it. Call 6 of the sweep did exactly this. We want
a single env flag, TRANSFER_DISABLED, that neutralises both on staging while
leaving production (flag unset) completely unchanged.

Two emitters are gated here — both currently resolve to clinic["transfer_phone"]:
  1. app.routes.realtime._handle_transfer            -> the live <Dial> REST redirect
  2. app.tools.receptionist_tools._exec_transfer_to_human -> the heads-up SMS

(The third emitter — connection.py's staff-notify SMS — is already env-gated via
THEOREM_NOTIFICATION_SMS, which was cleared on staging separately.)

RED until the gate is implemented:
  * the two *_when_disabled tests FAIL now (no gate exists, so the dial/SMS still
    fire);
  * the two control (*_when_not_disabled) tests PASS now and guard against a
    regression that would suppress transfers in production.
"""
from __future__ import annotations

import sys
import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Emitter 1: the live Twilio <Dial> redirect (_handle_transfer)
# ---------------------------------------------------------------------------
class TestHandleTransferDialGate:
    @pytest.mark.asyncio
    async def test_no_dial_when_disabled(self):
        """TRANSFER_DISABLED set -> no Twilio Client is ever constructed, so no
        call leg is placed to anyone (true 'transfers to no one')."""
        from app.routes import realtime

        session = {"clinic_id": "theorem_v3", "twilio_from": "+33617769867"}
        with patch("app.config.TRANSFER_DISABLED", True, create=True), \
             patch("twilio.rest.Client") as MockClient:
            await realtime._handle_transfer("CAtest", session)

        MockClient.assert_not_called()

    @pytest.mark.asyncio
    async def test_dial_attempted_when_not_disabled(self):
        """Control: flag off -> a Twilio dial is still attempted (Client built).
        The mock's .calls().fetch().status is not 'in-progress', so the code
        stops before .update(); constructing the Client proves the attempt."""
        from app.routes import realtime

        session = {"clinic_id": "theorem_v3", "twilio_from": "+33617769867"}
        with patch("app.config.TRANSFER_DISABLED", False, create=True), \
             patch("twilio.rest.Client") as MockClient:
            await realtime._handle_transfer("CAtest", session)

        MockClient.assert_called()


# ---------------------------------------------------------------------------
# Emitter 2: the heads-up "Susie is transferring a patient" SMS
# ---------------------------------------------------------------------------
class TestExecTransferSmsGate:
    @pytest.mark.asyncio
    async def test_no_sms_when_disabled(self):
        from app.tools import receptionist_tools

        session = {
            "clinic_id": "theorem_v3",
            "twilio_from": "+33617769867",
            "collected": {},
        }
        with patch("app.config.TRANSFER_DISABLED", True, create=True), \
             patch("app.notifications.sms.send_sms", new_callable=AsyncMock) as mock_sms, \
             patch("app.tools.handoff.send_to_sheet", MagicMock()):
            await receptionist_tools._exec_transfer_to_human({"reason": "test"}, session)
            await asyncio.sleep(0)  # let any scheduled tasks run

        mock_sms.assert_not_called()

    @pytest.mark.asyncio
    async def test_sms_sent_when_not_disabled(self):
        """Control: flag off -> the heads-up SMS still fires to transfer_phone."""
        from app.tools import receptionist_tools

        session = {
            "clinic_id": "theorem_v3",
            "twilio_from": "+33617769867",
            "collected": {},
        }
        with patch("app.config.TRANSFER_DISABLED", False, create=True), \
             patch("app.notifications.sms.send_sms", new_callable=AsyncMock) as mock_sms, \
             patch("app.tools.handoff.send_to_sheet", MagicMock()):
            await receptionist_tools._exec_transfer_to_human({"reason": "test"}, session)
            await asyncio.sleep(0)

        mock_sms.assert_called_once()
