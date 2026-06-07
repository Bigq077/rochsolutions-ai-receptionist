# app/tone_detector.py
"""
Caller tone detection for Susie.

Tracks the word count of the first two caller utterances and classifies
the caller's communication style as "brief", "standard", or "warm".  The
tone is locked after turn 2 and injected as the last line of the system
prompt every turn so Claude adjusts its verbosity accordingly.

One ToneDetector per call, stored as session["tone_detector"].
Serialisable state is mirrored to session["_tone_state"] (plain dict) so
it survives Redis round-trips.

Public API:
    td = ToneDetector()
    td.record_utterance(text)     # call once per caller turn
    td.get_tone()                 # "brief" | "standard" | "warm"
    td.get_instruction()          # TONE: ... string for system prompt
    td.to_dict()                  # serialisable state
    ToneDetector.from_dict(d)     # restore from serialised state

Helper:
    get_tone_instruction_from_session(session) -> str
        Safe wrapper; never raises; used inside get_system_prompt().
"""
from __future__ import annotations


class ToneDetector:
    BRIEF_THRESHOLD = 4    # avg words per utterance → "brief"
    WARM_THRESHOLD  = 12   # avg words per utterance → "warm"

    def __init__(self) -> None:
        self._word_counts: list[int] = []
        self._tone: str = "standard"
        self._locked: bool = False

    # ------------------------------------------------------------------
    # Core methods
    # ------------------------------------------------------------------

    def record_utterance(self, utterance: str) -> None:
        """Record one caller utterance. Ignores input once tone is locked."""
        if self._locked:
            return
        words = len(utterance.strip().split())
        self._word_counts.append(words)
        if len(self._word_counts) >= 2:
            avg = sum(self._word_counts[:2]) / 2
            if avg <= self.BRIEF_THRESHOLD:
                self._tone = "brief"
            elif avg >= self.WARM_THRESHOLD:
                self._tone = "warm"
            else:
                self._tone = "standard"
            self._locked = True  # tone is fixed after turn 2

    def get_tone(self) -> str:
        """Return current tone: "brief" | "standard" | "warm"."""
        return self._tone

    def get_instruction(self) -> str:
        """Return the TONE instruction line for the system prompt."""
        instructions: dict[str, str] = {
            "brief": (
                "TONE: The caller is using very short responses. Match their energy"
                " — keep all responses concise and direct. Skip filler phrases."
                " Get to the point immediately."
            ),
            "standard": (
                "TONE: Use your natural warm but efficient tone."
            ),
            "warm": (
                "TONE: The caller is chatty and conversational. You can be warmer"
                " and slightly more expansive. Brief friendly acknowledgements"
                " are welcome."
            ),
        }
        return instructions[self._tone]

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return JSON-serialisable state dict for Redis persistence."""
        return {
            "word_counts": list(self._word_counts),
            "tone":        self._tone,
            "locked":      self._locked,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ToneDetector":
        """Restore a ToneDetector from a previously serialised state dict."""
        td = cls()
        if isinstance(d, dict):
            td._word_counts = list(d.get("word_counts") or [])
            td._tone        = d.get("tone", "standard")
            td._locked      = bool(d.get("locked", False))
        return td


# ------------------------------------------------------------------
# Session helper — used inside get_system_prompt()
# ------------------------------------------------------------------

def get_tone_instruction_from_session(session: dict) -> str:
    """
    Return the TONE instruction string for the given session.

    Rebuilds ToneDetector from session["_tone_state"] if the live instance
    is not present (e.g. after a Redis round-trip).  Never raises.
    """
    try:
        td = session.get("tone_detector")
        if not isinstance(td, ToneDetector):
            td = ToneDetector.from_dict(session.get("_tone_state") or {})
        return td.get_instruction()
    except Exception:
        return ToneDetector().get_instruction()  # returns "standard" safely
