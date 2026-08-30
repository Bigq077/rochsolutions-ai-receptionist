"""An LLM caller with a persona and a goal, driving the real turn loop.

WHY THIS EXISTS
---------------
The fixed script cannot work, and the plan says so in as many words: the engine
does not ask the same questions in the same order twice, so a list of
pre-written replies desynchronises the moment a turn goes differently -- and
re-ordering the scripts a third time was explicitly ruled out. What is needed is
a caller that READS what Susie said and answers it.

This is what turns "run a full suite of test calls" from an afternoon on the
phone into a command. That afternoon is the whole reason this plan exists:
*"I'm spending hours a day just calling and fixing small bugs."*

THE ONE RULE THAT KEEPS IT HONEST
---------------------------------
**The caller may not decide whether the call passed.** It generates the
conversation and nothing else. Every verdict is a deterministic function of the
transcript -- see `verdicts.py` -- so a suite cannot go green because the caller
was in a generous mood. An LLM that both drives and marks its own test is not a
test.

WHAT IT CANNOT DO
-----------------
It types; it does not speak. STT, barge-in, endpointing and audio are outside
this harness entirely, so a defect that only appears when a caller talks over
Susie will not be found here. `tests/regression/` covers those against recorded
transcripts, and a live call is still the only place prosody is judged.

COST AND SAFETY
---------------
Every turn is two model calls -- one for the caller, one for Susie. The netfence
is installed by the driver and permits `api.anthropic.com` and nothing else, so
a bug here cannot reach Acuity, Google Calendar, Twilio or a phone. Bookings
land in a `FakeDiary`.
"""
from __future__ import annotations

import dataclasses
import os
import re
from typing import Dict, List, Optional, Sequence, Tuple

#: The caller says this when it has finished -- goal met, or given up. Chosen to
#: be something no real caller utterance could contain.
HANG_UP = "[END CALL]"

#: Opus 5 REMOVES temperature: sending it is a 400, not a warning. Variety
#: between runs therefore has to come from the personas themselves rather than
#: from sampling, which is the better place for it anyway -- a persona is
#: reviewable and a temperature is not.
CALLER_MODEL = os.getenv("HARNESS_CALLER_MODEL", "claude-opus-5")


@dataclasses.dataclass(frozen=True)
class Persona:
    """One caller: who they are, what they want, and what they will not say."""

    id: str
    goal: str
    opening: str
    #: Facts the caller knows and may give WHEN ASKED. Never volunteered up
    #: front -- a caller who recites their name, number and preferred day in the
    #: first breath tests a conversation no real person has.
    facts: Dict[str, str] = dataclasses.field(default_factory=dict)
    style: str = "Ordinary British phone manner. Short sentences."
    max_turns: int = 14
    #: What this persona is FOR, in one line, so a report reads without the code.
    covers: str = ""

    def system_prompt(self) -> str:
        facts = "\n".join(f"  - {k}: {v}" for k, v in self.facts.items()) or "  (none)"
        return f"""You are role-playing a member of the public ringing a physiotherapy clinic.
You are NOT an assistant. You are the caller.

YOUR GOAL
{self.goal}

FACTS YOU KNOW ABOUT YOURSELF
{facts}

HOW YOU SPEAK
{self.style}

RULES
1. Reply with ONLY the words you say down the phone. No narration, no quotes,
   no stage directions, no explanation of what you are doing.
2. One or two sentences. Real callers are brief and a little vague.
3. Answer the question you were actually asked. If the receptionist asked for
   your name, give your name -- do not also give your number and your preferred
   day. Volunteer a fact only when it is asked for or genuinely relevant.
4. Never invent a fact about yourself that is not listed above. If you are
   asked something not covered, be vague the way a real person is
   ("I'm not sure, sorry").
5. If the receptionist asks a medical or screening question, answer it HONESTLY
   from the facts above. If a fact is not listed, the answer is no.
6. When your goal is met, or it is clear it will not be, say a brief goodbye
   and then, on a new line, exactly: {HANG_UP}
7. If you are asked the same thing a third time, say so plainly the way an
   irritated caller would, and consider ending the call.

Do not be helpful. Do not summarise. You are a person on the phone."""


def _clean(text: str) -> str:
    """Strip the ways a model wraps speech it was told not to wrap."""
    out = (text or "").strip()
    out = re.sub(r"^(?:caller|you|me)\s*:\s*", "", out, flags=re.IGNORECASE)
    # Surrounding quotes, and stage directions in brackets or asterisks.
    if len(out) >= 2 and out[0] in "\"'" and out[-1] == out[0]:
        out = out[1:-1].strip()
    out = re.sub(r"\*[^*]{0,80}\*", " ", out)
    out = re.sub(r"\((?:sighs|pauses|laughs)[^)]{0,40}\)", " ", out, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", out).strip()


class AdaptiveCaller:
    """Generates the caller's side of one conversation. PURE of assertions."""

    def __init__(
        self,
        persona: Persona,
        client=None,
        model: str = CALLER_MODEL,
        max_tokens: int = 2000,
    ) -> None:
        self.persona = persona
        self.model = model
        self.max_tokens = max_tokens
        self._client = client
        self.turns_taken = 0
        self.ended = False
        #: Every request the caller made, for the cost line in the report.
        self.usage: List[Tuple[int, int]] = []

    def _get_client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic()
        return self._client

    def opening(self) -> str:
        """The first utterance is FIXED, not generated.

        Every call then starts from a known place, so two runs of the same
        persona are comparable even though everything after diverges. It also
        means a suite still exercises the opening turn when the model is
        unavailable.
        """
        self.turns_taken = 1
        return self.persona.opening

    async def reply(self, exchanges: Sequence[Tuple[str, str]]) -> Optional[str]:
        """The next thing the caller says, or None to hang up.

        ``exchanges`` is [(caller_said, susie_said), ...] oldest first.
        """
        if self.ended:
            return None
        if self.turns_taken >= self.persona.max_turns:
            self.ended = True
            return None

        messages: List[dict] = []
        for said, heard in exchanges:
            if said:
                messages.append({"role": "assistant", "content": said})
            if heard:
                messages.append({"role": "user", "content": heard})
        if not messages or messages[0]["role"] != "user":
            # The API requires a leading user turn. The caller spoke first, so
            # the opening is folded into the instruction rather than faked as a
            # receptionist line that was never said.
            messages.insert(0, {
                "role": "user",
                "content": "(The line connects. You speak first.)",
            })

        client = self._get_client()
        response = await client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self.persona.system_prompt(),
            # Low effort deliberately: this is one short line of dialogue, and
            # the caller thinking hard about it produces a monologue rather than
            # a phone call. No `temperature` -- Opus 5 rejects it outright.
            output_config={"effort": "low"},
            messages=messages,
        )
        self.usage.append(
            (response.usage.input_tokens, response.usage.output_tokens)
        )

        if response.stop_reason == "refusal":
            # Not a defect in the engine, and it must not be reported as one.
            self.ended = True
            return None

        raw = "".join(b.text for b in response.content if b.type == "text")
        if HANG_UP in raw:
            self.ended = True
            spoken = _clean(raw.split(HANG_UP)[0])
            self.turns_taken += 1
            return spoken or None

        self.turns_taken += 1
        return _clean(raw) or None
