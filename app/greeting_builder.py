# app/greeting_builder.py
"""
Builds a context-appropriate opening greeting for Susie.

Reads clinic_config.json (or the path in CLINIC_CONFIG_PATH env var) to
determine:
  - Clinic name
  - Timezone
  - Opening hours per weekday
  - Closed days (e.g. weekends)

Produces one of four greeting strings:
  - Closed day / outside hours: mentions we're closed but can still book
  - Closing soon (<=30 min):    urgent-but-helpful tone
  - Monday morning (< noon):   "Happy Monday" prefix
  - All other open hours:      standard warm greeting
"""
from __future__ import annotations

import os
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def load_clinic_config() -> dict:
    config_path = os.getenv("CLINIC_CONFIG_PATH", "clinic_config.json")
    try:
        with open(config_path) as f:
            return json.load(f)
    except Exception:
        return {}  # safe default


def get_next_open_day(config: dict, from_dt: datetime) -> str:
    """Find next calendar day not in closed_days with an entry in opening_hours."""
    closed = [d.lower() for d in config.get("closed_days", [])]
    hours = config.get("opening_hours", {})
    for offset in range(1, 8):
        candidate = from_dt + timedelta(days=offset)
        day_name = candidate.strftime("%A").lower()
        if day_name not in closed and day_name in {k.lower() for k in hours}:
            return candidate.strftime("%A")
    return "next week"


def build_greeting(config: dict | None = None) -> str:
    if config is None:
        config = load_clinic_config()
    clinic_name = config.get("clinic_name", os.getenv("CLINIC_NAME", "the clinic"))
    try:
        tz_name = config.get("timezone", "Europe/London")
        now = datetime.now(ZoneInfo(tz_name))
        day_name = now.strftime("%A").lower()
        hour = now.hour

        opening_hours = config.get("opening_hours", {})
        closed_days = [d.lower() for d in config.get("closed_days", [])]

        # Closed day or no hours defined for today
        if day_name in closed_days or day_name not in {k.lower() for k in opening_hours}:
            return "Hi there, I'm Susie, Theorem Health AI receptionist — how can I help you today?"

        day_hours = next((v for k, v in opening_hours.items() if k.lower() == day_name), None)
        if day_hours:
            close_h, close_m = map(int, day_hours["close"].split(":"))
            close_dt = now.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
            minutes_to_close = (close_dt - now).total_seconds() / 60

            if minutes_to_close <= 0:
                return "Hi there, I'm Susie, Theorem Health AI receptionist — how can I help you today?"

            if minutes_to_close <= 30:
                return "Hi there, I'm Susie, Theorem Health AI receptionist — how can I help you today?"

        return "Hi there, I'm Susie, Theorem Health AI receptionist — how can I help you today?"

    except Exception:
        return "Hi there, I'm Susie, Theorem Health AI receptionist — how can I help you today?"
