# app/voicecopy.py
from __future__ import annotations

from datetime import datetime
from typing import Sequence

def slow_list_three(items: Sequence[str]) -> str:
    """
    For voice clarity: forces a 1/2/3 list with spacing.
    Caller can reply with speech or DTMF.
    """
    items = list(items)[:3]
    if len(items) == 0:
        return "I couldn't find any times right now."
    if len(items) == 1:
        return f"I have one available appointment time. The first is: {items[0]}. Please say one, or press 1, to choose it."
    if len(items) == 2:
        return (
            "I have two available appointment times. "
            f"The first is: {items[0]}. "
            f"The second is: {items[1]}. "
            "Please say one or two, or press 1 or 2."
        )
    return (
        "I have three available appointment times. "
        f"The first is: {items[0]}. "
        f"The second is: {items[1]}. "
        f"The third is: {items[2]}. "
        "Please say one, two, or three. Or press 1, 2, or 3."
    )

def confirm_phrase(patient_name: str, service: str, start_local_str: str) -> str:
    # Keep crisp, no fluff.
    return (
        f"Just to confirm: {patient_name}, for {service}, on {start_local_str}. "
        "Is that correct? Please say yes or no."
    )

def ask_phone_phrase() -> str:
    # People stumble here; make it slow and explicit.
    return (
        "What is the best phone number to reach you? "
        "Please say the digits slowly. "
        "You can also type it on your keypad."
    )

def ask_name_phrase() -> str:
    return "Who am I booking in today?"

def repair_no_match() -> str:
    return (
        "Sorry about that — please say one, two, or three. Or press 1, 2, or 3."
    )

def manual_fallback_phrase() -> str:
    return (
        "I’m having trouble updating the calendar right now. "
        "I will ask the clinic team to confirm this with you as soon as possible."
    )
