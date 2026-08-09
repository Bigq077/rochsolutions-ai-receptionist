"""
The theorem_v3 reschedule/cancel flow is model-driven, not code-injected (T-18).

Observed on the second reschedule call ever attempted, 2026-08-05 00:34:13.

    00:34:16  FINAL → "um yeah i'd like to move my appointment"
    00:34:19  [ms_gate5] removed banned phrase (banned_opener)
    00:34:19  synthesise_chunk: "Let's get that moved for you."
    00:34:21  Spec W: turn asked nothing and no question is outstanding
    …                                    seven seconds of dead air …
    00:34:27  caller: 'uh hello'

The prompt mandated the ack "Of course, let's get that moved for you." and
connection.py detected it by literal-matching `_V3_ACK_PHRASES`, which held
"of course, let's get that moved". But conversation_history has stored the
POST-Gate-5 text since 2 Aug, and Gate 5's banned_opener rule strips a leading
"Of course, ". The entry could never match. No ack was detected, no question
was injected, and the flow opened on silence.

The fix ports latency-eval's contract: the model owns the opening turn (ack +
phone readback together) and code injects nothing. These tests pin the
properties that has to keep, all of which are cheap to break by editing one
string in either file.
"""

import ast
import inspect
import re

from app.media_streams import connection as conn
from app.media_streams.turn_handler import _BANNED_SENTENCE_RE
from app.prompts.susie_system_prompt import build_system_prompt_parts


def _banned_opener_re():
    for name, pattern in _BANNED_SENTENCE_RE:
        if name == "banned_opener":
            return pattern
    raise AssertionError("Gate 5 no longer has a 'banned_opener' rule")


def _ack_phrases() -> list:
    """Parse _V3_ACK_PHRASES out of the handler rather than restating it.

    Reads the comment-stripped source: the note above the tuple quotes the
    dead entries it explains, and those must not be read back as live ones.
    """
    literal = re.search(
        r"_V3_ACK_PHRASES = \((.*?)\n\s*\)", _handler_code(), re.DOTALL
    )
    assert literal, "_V3_ACK_PHRASES tuple not found — was it renamed?"
    phrases = re.findall(r'"([^"]+)"', literal.group(1))
    assert phrases, "no phrases parsed out of _V3_ACK_PHRASES"
    return phrases


def _handler_code() -> str:
    """Handler source with comment lines removed.

    These tests assert on what the code does; without this they also match the
    comments that explain what it deliberately stopped doing.
    """
    source = inspect.getsource(conn.WebSocketCallHandler)
    return "\n".join(
        line for line in source.split("\n")
        if not line.lstrip().startswith("#")
    )


_HANDOFF_HELPER = "_v3_hand_location_answer_to_model"


def _connection_tree():
    """connection.py as an AST, with parent links.

    The T-19 assertions below are about control flow — what runs before what,
    and whether a branch can fall through to speech. Regex cannot see that;
    the previous version of this file tried and passed straight through the
    defect. Parsed once per call: these are cheap next to the prompt build.
    """
    tree = ast.parse(inspect.getsource(conn))
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child._parent = parent
    return tree


def _is_reschedule_guard(node) -> bool:
    """`if <anything> in ("reschedule", "cancel"):` — any variable name.

    Matched structurally rather than by name: the four sites read the intent
    into four differently-named locals, and a fifth would be missed by a
    name-based match.
    """
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    if len(node.test.ops) != 1 or not isinstance(node.test.ops[0], ast.In):
        return False
    right = node.test.comparators[0]
    if not isinstance(right, ast.Tuple):
        return False
    values = [
        e.value for e in right.elts
        if isinstance(e, ast.Constant) and isinstance(e.value, str)
    ]
    return values == ["reschedule", "cancel"]


def _reschedule_guards(tree) -> list:
    return [n for n in ast.walk(tree) if _is_reschedule_guard(n)]


def _calls_to(node, dotted: str) -> list:
    """Every Call in this subtree whose func renders as `dotted`."""
    found = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            try:
                rendered = ast.unparse(sub.func)
            except Exception:                       # pragma: no cover
                continue
            if rendered.endswith(dotted):
                found.append(sub)
    return found


def _body_of(guard) -> ast.Module:
    """The guard's own branch (not its else) as a walkable node."""
    return ast.Module(body=guard.body, type_ignores=[])


def _hands_over(guard) -> bool:
    """Does this branch give the turn to the model rather than end it?

    Two shapes, because the two callers live in different places: inside the
    LLM loop it awaits run_turn via the helper; the DTMF handler is not in the
    loop and cannot, so it re-queues a synthetic transcript instead.
    """
    body = _body_of(guard)
    return bool(
        _calls_to(body, _HANDOFF_HELPER)
        or _calls_to(body, "transcript_queue.put")
    )


def _handoff_guards(tree) -> list:
    """The location-answer guards: hand over, then end the turn."""
    return [
        g for g in _reschedule_guards(tree)
        if _hands_over(g)
        and g.body
        and isinstance(g.body[-1], (ast.Continue, ast.Return))
    ]


def _enclosing_block(node):
    """The statement whose body list holds `node` directly."""
    parent = getattr(node, "_parent", None)
    while parent is not None:
        for field in ("body", "orelse", "finalbody"):
            if node in getattr(parent, field, []):
                return parent
        node, parent = parent, getattr(parent, "_parent", None)
    return None


def _theorem_prompt(**overrides) -> str:
    session = {
        "clinic_id": "theorem_v3",
        "twilio_from_local": "07502211207",
        "turn_count": 3,
        "collected": {},
        "soft_context": {},
    }
    session.update(overrides)
    static, dynamic = build_system_prompt_parts(session)
    return static + "\n" + dynamic


def _reschedule_block(prompt: str) -> str:
    """The flow section itself, not the booking flow's cross-reference to it."""
    start = prompt.index("\nRESCHEDULE / CANCEL FLOW\n")
    return prompt[start:start + 8000]


# ── 1. No ack phrase may begin with an opener Gate 5 strips ──────────────────

def test_no_v3_ack_phrase_starts_with_a_gate5_banned_opener():
    """The bug in one assertion: a phrase Gate 5 rewrites cannot be matched.

    _V3_ACK_PHRASES is compared against the post-Gate-5 text, so an entry
    beginning with "Of course," is dead on arrival. Reads both real sources
    rather than restating them, so the test tracks edits to either.
    """
    banned_opener = _banned_opener_re()
    dead = [p for p in _ack_phrases() if banned_opener.match(p)]
    assert not dead, (
        "these ack phrases start with an opener Gate 5 strips, so they can "
        "never match the post-Gate-5 text they are compared against: "
        f"{dead}. Anchor them on the part of the phrase Gate 5 leaves alone."
    )


def test_reschedule_ack_survives_gate5_and_is_still_detected():
    """End to end: the ack, run through Gate 5, still matches an entry.

    Covers the case even if the model volunteers the banned opener anyway.
    """
    spoken = _banned_opener_re().sub(
        "", "Of course, let's get that moved for you."
    ).lower()
    assert any(p in spoken for p in _ack_phrases()), (
        f"post-Gate-5 ack {spoken!r} matches no entry in _V3_ACK_PHRASES — "
        "the reschedule ack would go undetected exactly as it did at 00:34:13"
    )


# ── 2. The prompt must own the opening turn ─────────────────────────────────

def test_reschedule_flow_asks_which_clinic_first():
    """Owner's call, 2026-08-05: the clinic question is asked, and asked FIRST.

    It is source (2) of the STRICT RULE — an explicit statement from the caller
    naming the clinic — and on a two-site clinic it is the natural opening
    question. What changed in a1c4593 is only WHO asks it: the model, in the
    same turn as the ack, rather than code injecting a separate turn.
    """
    block = _reschedule_block(_theorem_prompt())
    assert "TURN 1" in block
    assert "Was your original appointment at our Awlstuh or Redditch clinic?" in block, (
        "the reschedule flow no longer asks which clinic"
    )
    assert "Was the appointment you'd like to cancel at our Awlstuh or Redditch clinic?" in block, (
        "the cancel flow no longer asks which clinic"
    )


def test_the_clinic_answer_is_stored():
    """Asking is only half of it — the answer has to reach the session.

    collect_and_store(field="location") syncs selected_location and is
    persisted to Redis by save_session; without it the answer is spoken into
    the void and downstream tools fall back to the alcester default.
    """
    block = _reschedule_block(_theorem_prompt())
    assert 'collect_and_store(field="location"' in block
    for value in ("alcester", "redditch"):
        assert value in block, f"no stored value given for {value}"


def test_the_phone_readback_shares_the_turn_with_the_clinic_ack():
    """The dead-air shape, one step later.

    "Right, Awlstuh." on its own is a bare acknowledgement with nothing for the
    caller to answer — exactly what left three calls in silence at the ack.
    """
    block = _reschedule_block(_theorem_prompt())
    assert "is that the number the appointment was booked under?" in block
    assert "NEVER end turn 2 on the clinic acknowledgement alone" in block


def test_the_appointment_beats_the_callers_memory():
    """Both sources can now exist and disagree. The booking record wins."""
    block = _reschedule_block(_theorem_prompt())
    assert "WHEN BOTH SOURCES EXIST, THE APPOINTMENT WINS" in block
    assert "Never move or cancel an appointment at a site it is not at" in block


def test_prompt_does_not_promise_that_code_asks_for_the_number():
    """No block may tell the model the system will ask the phone question.

    Two blocks of one prompt disagreeing about who asks for the number is the
    shape that produced this defect.
    """
    prompt = _theorem_prompt()
    assert "the system asks for the clinic and then the phone number" not in prompt


def test_call_state_does_not_forbid_reading_the_number_back():
    """CALL STATE states facts; the flow blocks set policy.

    It used to append "no readback needed" to the caller phone, which
    contradicted both the booking keypad readback and this flow's opening turn.
    """
    prompt = _theorem_prompt()
    assert "caller phone (pre-loaded from caller ID): 07502211207" in prompt
    assert "no readback needed" not in prompt


# ── 3. Code must not inject into this flow ──────────────────────────────────

def test_use_this_number_is_not_injected_on_reschedule_or_cancel():
    """The banned set-phrase question is gone from EVERY reschedule branch.

    'If so, just say "use this number"' asks the caller to reason about a
    number they cannot hear — banned by the owner on the other branches on
    3 Aug. The booking flow's own keypad prompts keep their copies of the
    phrase, so this asserts on the reschedule branches only.

    Every one of them, now. This used to regex out the FIRST
    `if _intent in ("reschedule", "cancel")` in the file and assert on that
    alone — which, despite the docstring naming the booking-ack handler, was
    the DTMF location handler. Six other branches went unchecked.
    """
    guards = _reschedule_guards(_connection_tree())
    assert len(guards) >= 4, (
        f"only {len(guards)} reschedule/cancel branches found — the intent "
        "gates have been renamed or removed"
    )
    for guard in guards:
        body = ast.unparse(_body_of(guard))
        where = f"line {guard.lineno}"
        assert "use this number" not in body, (
            f"the reschedule branch at {where} is injecting the banned "
            "set-phrase question again"
        )
        assert "v3_awaiting_phone_confirm" not in body, (
            f"the reschedule branch at {where} arms the deterministic "
            "phone-confirm intercept, which assumes a question this flow no "
            "longer asks — it would swallow the caller's plain 'yes' to the "
            "readback"
        )


# ── 3a. T-19: the code must not SPEAK into this flow either ─────────────────
#
# Suppressing the injected question was only half of it. Three intercepts and
# the DTMF handler still spoke a bare clinic ack — "Awlstuh." — and then ended
# the turn without calling run_turn. The model never got the turn in which the
# prompt tells it to ack, read the number back, and look the patient up.
#
#   CAba035928b6fe0d135ff95ce920bf9073, 2026-08-08, 26 s, outcome='abandoned'
#   21:38:22  "Was your original appointment at our Awlstuh or Redditch cli…"
#   21:38:29  FINAL 'uh ooster'
#   21:38:30  "Awlstuh."        ← the last thing the caller ever heard
#   21:38:31  WATCHDOG_START wait=10.0s
#   21:38:37  stop event — hung up 3.1 s before the backstop would have fired
#
# Silence and not-asking-a-question are different properties. The gap between
# them is this bug, and the T-18 test above only ever pinned the second.


def test_the_reschedule_location_branches_speak_nothing():
    """Nothing may reach TTS from these branches. The model owns the turn."""
    guards = _handoff_guards(_connection_tree())
    assert len(guards) == 4, (
        f"expected 4 location-answer handoffs (DTMF, use-this-clinic, alias, "
        f"Haiku), found {len(guards)} at "
        f"{[g.lineno for g in guards]} — a site has been added or has stopped "
        "handing the turn to the model"
    )
    for guard in guards:
        spoken = _calls_to(_body_of(guard), "tts_text_queue.put")
        assert not spoken, (
            f"the reschedule branch at line {guard.lineno} queues speech "
            f"(line {spoken[0].lineno}). A bare ack with no turn behind it is "
            "what left CAba0359 in dead air — store the location and hand the "
            "utterance to the model, which acks it in its own turn."
        )


def test_no_reschedule_branch_can_fall_through_to_the_bare_ack():
    """The guard must sit ABOVE the ack, not merely suppress the question.

    The alias site is why this is asserted on position rather than presence:
    its intent gate was nested three levels below `await
    tts_text_queue.put(_ack)`, so it could suppress the follow-up question and
    still speak. Every bare-ack site must be unreachable on this intent.
    """
    tree = _connection_tree()
    guards = _handoff_guards(tree)
    acks = [
        call for call in _calls_to(tree, "tts_text_queue.put")
        if call.args
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "_ack"
    ]
    assert len(acks) == 4, (
        f"expected 4 bare clinic-ack sites, found {len(acks)} at "
        f"{[a.lineno for a in acks]} — a new one needs a handoff guard above it"
    )
    for ack in acks:
        earlier = [g for g in guards if g.lineno < ack.lineno]
        assert earlier, (
            f"the bare clinic ack at line {ack.lineno} has no reschedule "
            "handoff above it — on this intent it speaks one word and the "
            "turn ends"
        )
        guard = max(earlier, key=lambda g: g.lineno)
        block = _enclosing_block(guard)
        assert block is not None and ack in ast.walk(block), (
            f"the bare clinic ack at line {ack.lineno} is not inside the "
            f"block guarded at line {guard.lineno} — the nearest handoff "
            "belongs to a different site, so this ack is ungated"
        )


def test_the_handoff_gives_the_model_a_real_turn():
    """Storing the answer silently is not enough — someone has to speak.

    The helper has to (1) run the model turn, (2) drain gate 5's deferred
    fallback, because its callers `continue` past the post-turn block that
    normally emits it, and (3) re-arm the watchdog on the question the MODEL
    just asked. Without (3) the backstop stays pointed at the clinic question
    the caller has already answered, and re-asks "Awlstuh or Redditch?" over
    the phone readback.
    """
    tree = _connection_tree()
    helper = next(
        (
            n for n in ast.walk(tree)
            if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
            and n.name == _HANDOFF_HELPER
        ),
        None,
    )
    assert helper is not None, f"{_HANDOFF_HELPER} is gone"
    body = ast.unparse(helper)
    assert _calls_to(helper, "llm.run_turn"), (
        "the handoff no longer runs a model turn — the caller hears nothing"
    )
    assert "_gate5_fallback_pending" in body, (
        "the handoff `continue`s past the post-turn block that emits gate 5's "
        "deferred fallback, so it must drain it here — otherwise a turn that "
        "produces no speech is the same dead air again"
    )
    assert _calls_to(helper, "on_question_asked"), (
        "the watchdog is left armed on the clinic question the caller just "
        "answered"
    )


def test_exactly_one_asker_owns_the_clinic_question():
    """The flow asks which clinic; the MODEL asks it, not code.

    Either can ask. Both must not. When code owned it, the question arrived as
    an injected turn and its re-queue collapsed the 00:08:43 call, and the
    injection depended on literal-matching the model's ack, which left three
    calls in dead air. The prompt now carries the question; this pins the code
    side to silence so they cannot both fire.
    """
    code = _handler_code()
    assert re.search(
        r'if _v3_gate_fired and _gate_intent in \(\s*\n?\s*"reschedule", "cancel"\s*\n?\s*\):',
        code,
    ), "the code location gate is asking on this flow again — it would double-ask"
    assert "Was your original appointment at" not in code, (
        "the clinic question is back in the location gate; it belongs to the "
        "prompt now, and two askers means the caller is asked twice"
    )


# ── 3b. No OTHER block may re-teach the code-driven contract ────────────────
#
# The port fixed the RESCHEDULE / CANCEL FLOW block and the code, and the flow
# still died on a bare ack for three more live calls, because three other
# blocks were still teaching the old behaviour. Editing the block you are
# looking at is not enough in a 100k-char prompt: what reaches the model is the
# UNION of every block, and the model obeys the most emphatic one it finds.


def test_no_block_claims_the_system_finishes_the_turn():
    """The model owns its whole turn now — nothing is appended by code.

    The banned-openers block used to exempt "Of course, let's get that moved
    for you." with "— the system handles that automatically". Both halves were
    wrong: Gate 5 strips that opener, and no system injection remains. Left in,
    it told the model to ack and stop.
    """
    p = _theorem_prompt()
    assert "the system handles that automatically" not in p, (
        "a block still tells the model the system finishes its reschedule "
        "turn — it will ack and stop, and the caller hears silence"
    )
    assert "No acknowledgement is handled for you" in p
    # "The system injects the next question" is deliberately NOT asserted away:
    # it is still true of the new-booking flow's first step, which really is
    # code-injected. Only the reschedule ack lost its injection.


def test_ack_and_stop_is_not_the_global_default():
    """Two blocks used to contradict each other outright.

    ONE QUESTION PER TURN said "the acknowledgement is its own turn — the next
    question goes on the following turn". The ACKNOWLEDGEMENT RULE said "The
    acknowledgement and the next question are delivered in the same turn —
    never as separate turns". The model resolved it by stopping.
    """
    p = _theorem_prompt()
    assert "acknowledgement is its own turn" not in p, (
        "ONE QUESTION PER TURN has gone back to making ack-and-stop the "
        "default, contradicting the ACKNOWLEDGEMENT RULE"
    )
    assert "The acknowledgement and the next question are delivered in the same turn" in p
    assert "An acknowledgement is NOT a turn of its own" in p


def test_reschedule_intent_escapes_the_new_booking_flow_first_step():
    """Booking step 1 is the first thing an existing patient hits.

    Until 5 Aug only a near-miss of "cancel" escaped it; "I'd like to
    reschedule my appointment" fell through to 'acknowledge simply "Right —"
    and NOTHING ELSE'. The reschedule flow's opening turn was never reached.
    """
    p = _theorem_prompt()
    step1 = p[p.index("1. Caller signals booking intent"):]
    step1 = step1[:step1.index('acknowledge simply')]
    assert "MOVED" in step1, (
        "booking step 1 no longer hands reschedule intent to the reschedule "
        "flow — a bare 'Right —' will swallow it again"
    )
    for word in ("reschedule", "rearrange", "move", "change"):
        assert word in step1.lower(), f"{word!r} does not escape booking step 1"
    assert "do NOT stop after an acknowledgement" in step1


def test_the_reschedule_ack_is_taught_without_the_banned_opener():
    """Gate 5 deletes a leading "Of course, ". The taught ack must not have one."""
    p = _theorem_prompt()
    banned = _banned_opener_re()
    assert not banned.match("Let's get that moved for you.")
    # The old form may appear only as an explicit negative example.
    for m in re.finditer(r"[^.\n]*Of course, let's get that moved[^.\n]*", p):
        assert "never" in m.group().lower(), (
            f"the banned-opener form is still taught as the ack: {m.group()[:120]!r}"
        )


# ── 4. The not-found path must not dead-end on a transfer ───────────────────

def test_lookup_not_found_offers_alternatives_before_transferring():
    block = _reschedule_block(_theorem_prompt())
    assert (
        "Are you sure the number you're calling on is the one your "
        "booking is under?"
    ) in block
    assert "let me put you through to the team" not in block, (
        "exhausting one number's appointments must not transfer — the booking "
        "is usually just under a different number"
    )
