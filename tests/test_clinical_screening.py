# tests/test_clinical_screening.py
"""
JV clinical intelligence — regression suite.

Covers the four consistency layers:
  1. Deterministic classifier (app/media_streams/clinical_screening.py):
     trigger arming, answer classification, escalation, emergency intercept.
  2. Prompt renderers (clinic_template_prompt): TREATMENT KNOWLEDGE +
     CLINICAL SAFETY SCREENING blocks, SCREEN REQUIRED steer, and the
     clinical_depth standard/deep gating (env kill-switch included).
  3. Tool backstop: booking_blocked_reason blocks while a screen is
     unresolved or answered positive.
  4. Sanitiser Gate 5e: diagnostic assertions stripped on the standard tier,
     education and the screening questions themselves preserved.
"""
from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.media_streams import clinical_screening as cs
from app.media_streams.clinical_screening import booking_blocked_reason
from app.prompts.clinic_template_prompt import build_clinic_prompt, _clinical_depth


CAUDA_QUESTION_PROMPT = (
    "Of course. Before we look at the next step, can I ask - do you have any "
    "numbness around the saddle area between your legs, or any changes in "
    "your bladder or bowel control?"
)


@pytest.fixture()
def jv():
    return get_clinic("jv_v1")


@pytest.fixture()
def ve():
    return get_clinic("vital_edge")


# ─────────────────────────────────────────────────────────────────────────
# Layer 1 — deterministic classifier
# ─────────────────────────────────────────────────────────────────────────
class TestScreeningClassifier:
    def test_enabled_for_jv_not_ve(self, jv, ve):
        assert cs.screening_enabled(jv)
        assert not cs.screening_enabled(ve)

    def test_lower_back_arms_cauda_screen(self, jv):
        """Arming now SPEAKS the screen question deterministically (baec415)
        rather than deferring to the SCREEN REQUIRED prompt steer."""
        sess = {}
        r = cs.update_screening_state(
            sess, jv, "Hi, I'm looking for an appointment for lower back pain"
        )
        assert r["action"] == "ask_screen"
        assert "bladder or bowel" in r["speak"]
        assert sess.get("pending_screen") == "cauda_equina"

    def test_sciatica_arms_cauda_screen(self, jv):
        sess = {}
        cs.update_screening_state(sess, jv, "I've got sciatica going down my leg")
        assert sess.get("pending_screen") == "cauda_equina"

    def test_calf_arms_dvt_screen(self, jv):
        sess = {}
        cs.update_screening_state(sess, jv, "my calf has been painful and swollen")
        assert sess.get("pending_screen") == "dvt"

    def test_no_resolution_before_question_asked(self, jv):
        """A 'no' to an unrelated question must not resolve the screen."""
        sess = {}
        cs.update_screening_state(sess, jv, "it's my lower back")
        cs.update_screening_state(sess, jv, "no I have not been before")
        assert sess.get("pending_screen") == "cauda_equina"

    def test_clear_answer_resolves_and_completes(self, jv):
        sess = {}
        cs.update_screening_state(sess, jv, "lower back pain")
        sess["last_bot_prompt"] = CAUDA_QUESTION_PROMPT
        r = cs.update_screening_state(
            sess, jv,
            "No, nothing like that. It just started after lifting yesterday.",
        )
        assert r["action"] == "none"
        assert sess.get("pending_screen") is None
        assert "cauda_equina" in sess.get("screens_completed", [])

    def test_screen_asked_once_per_call(self, jv):
        sess = {"screens_completed": ["cauda_equina"]}
        cs.update_screening_state(sess, jv, "the lower back pain is quite bad")
        assert sess.get("pending_screen") is None

    def test_red_flag_answer_escalates_deterministically(self, jv):
        sess = {}
        cs.update_screening_state(sess, jv, "agony in my lower back and leg")
        sess["last_bot_prompt"] = CAUDA_QUESTION_PROMPT
        r = cs.update_screening_state(
            sess, jv, "actually yes, I've had trouble with my bladder"
        )
        assert r["action"] == "escalate"
        assert "111" in r["speak"] or "A&E" in r["speak"]
        assert sess.get("screen_red_flag") == "cauda_equina"
        assert sess.get("pending_screen") is None

    def test_red_flag_keywords_beat_leading_no(self, jv):
        """'no feeling in both legs' contains 'no' but is a POSITIVE."""
        screen = cs.get_screen(jv, "cauda_equina")
        assert cs.classify_screen_answer("no feeling in both legs", screen) == "red_flag"

    def test_emergency_intercept(self, jv):
        sess = {}
        r = cs.update_screening_state(sess, jv, "I've got chest pain and can't breathe")
        assert r["action"] == "emergency"
        assert "999" in r["speak"]

    def test_emergency_preempts_pending_screen(self, jv):
        sess = {"pending_screen": "cauda_equina"}
        r = cs.update_screening_state(sess, jv, "he's collapsed, I think heart attack")
        assert r["action"] == "emergency"
        assert sess.get("pending_screen") is None

    def test_never_raises_on_bad_input(self, jv):
        assert cs.update_screening_state({}, jv, "")["action"] == "none"
        assert cs.update_screening_state({}, {}, "back pain")["action"] == "none"
        assert cs.update_screening_state({}, None or {}, None or "")["action"] == "none"

    def test_model_asked_screen_is_not_re_asked(self, jv):
        """Call-2 (2026-07-20) P2: the PROMPT layer asked the DVT screen, so
        pending_screen was never armed here; the caller's answer still contained
        the trigger word 'calf', which re-armed and re-asked the same question.
        The answer must be classified instead."""
        sess = {
            "last_bot_prompt": (
                "Before we go further, can I quickly check - is the calf "
                "swollen, warm or red compared with the other side, and have "
                "you had any recent surgery, illness, or a long journey "
                "sitting still?"
            )
        }
        r = cs.update_screening_state(
            sess, jv,
            "yeah i just said i was on a flight for a long time and the calf "
            "like my red calf just a bit red",
        )
        assert r["action"] == "escalate"      # not "ask_screen"
        assert sess.get("screen_red_flag") == "dvt"

    def test_arming_utterance_with_two_red_flags_escalates(self, jv):
        """'heard a crack' + 'swelled straight away' both arms the trauma
        screen AND answers it — don't ask a question already answered."""
        sess = {}
        r = cs.update_screening_state(
            sess, jv,
            "my lad came off his bike, heard a crack and his wrist is swelled "
            "straight away",
        )
        assert r["action"] == "escalate"
        assert sess.get("screen_red_flag") == "trauma_fracture"

    def test_single_weak_keyword_still_asks_the_screen(self, jv):
        """Guard against over-escalation: one keyword in an unprompted
        description is an ordinary strain, not a DVT — ask the screen."""
        sess = {}
        r = cs.update_screening_state(
            sess, jv, "my calf has been painful and swollen"
        )
        assert r["action"] == "ask_screen"
        assert sess.get("pending_screen") == "dvt"
        assert sess.get("screen_red_flag") is None

    def test_junk_fragment_never_advances_screening(self, jv):
        """Call-2 (2026-07-20): a stray 'and' final reached the classifier
        before connection.py's noise filter — a garbled fragment must never
        arm, clear, or resolve a screen."""
        sess = {
            "pending_screen": "dvt",
            "last_bot_prompt": (
                "is the calf swollen, warm or red compared with the other "
                "side, and have you had any recent surgery, illness, or a "
                "long journey sitting still?"
            ),
        }
        for junk in ("and", "er", "um", "s", "ng"):
            r = cs.update_screening_state(sess, jv, junk)
            assert r["action"] == "none", junk
            assert sess["pending_screen"] == "dvt", junk

    def test_decisive_single_words_still_resolve(self, jv):
        """'hot' answers 'is it swollen, warm or red?'; bare 'no' clears —
        the junk gate must not swallow real one-word answers."""
        sess = {
            "pending_screen": "dvt",
            "last_bot_prompt": (
                "is the calf swollen, warm or red compared with the other "
                "side, and have you had any recent surgery, illness, or a "
                "long journey sitting still?"
            ),
        }
        r = cs.update_screening_state(sess, jv, "hot")
        assert r["action"] == "escalate"
        assert sess.get("screen_red_flag") == "dvt"
        sess2 = {
            "pending_screen": "cauda_equina",
            "last_bot_prompt": (
                "do you have any numbness around the saddle area between "
                "your legs, or any changes in your bladder or bowel control?"
            ),
        }
        r2 = cs.update_screening_state(sess2, jv, "no")
        assert r2["action"] == "none"
        assert sess2["pending_screen"] is None
        assert "cauda_equina" in sess2["screens_completed"]

    def test_trauma_question_is_limb_aware_and_grip_is_a_red_flag(self, jv):
        """Call-2: a WRIST caller was asked the weight-bearing-only question,
        and 'can't really grip' wasn't recognised as a red flag."""
        sess = {}
        r = cs.update_screening_state(
            sess, jv, "my lad came off his bike and hurt his wrist"
        )
        assert r["action"] == "ask_screen"
        assert "use it or put weight through it" in r["speak"]
        sess["last_bot_prompt"] = r["speak"]
        r2 = cs.update_screening_state(
            sess, jv, "well yeah swelling and he can't really grip the bike"
        )
        assert r2["action"] == "escalate"
        assert sess.get("screen_red_flag") == "trauma_fracture"

    def test_trauma_screen_arms_and_blocks(self, jv):
        sess = {}
        cs.update_screening_state(
            sess, jv, "I fell off my bike yesterday and my wrist is agony"
        )
        assert sess.get("pending_screen") == "trauma_fracture"
        sess["last_bot_prompt"] = (
            "can I quickly check, are you able to put weight through it, and "
            "is there any marked swelling or does it look out of shape at all?"
        )
        r = cs.update_screening_state(
            sess, jv, "I heard a crack and it swelled straight away"
        )
        assert r["action"] == "escalate"
        assert sess.get("screen_red_flag") == "trauma_fracture"
        assert booking_blocked_reason(sess, jv) is not None

    def test_vbi_requires_neck_AND_neuro_signal(self, jv):
        """Compound trigger: plain neck pain must NOT be over-screened."""
        plain = {}
        cs.update_screening_state(plain, jv, "my neck has been stiff all week")
        assert plain.get("pending_screen") is None
        combo = {}
        cs.update_screening_state(combo, jv, "my neck hurts and I keep getting dizzy")
        assert combo.get("pending_screen") == "vbi_neck"

    def test_inflammatory_is_advisory_not_blocking(self, jv):
        """Positive inflammatory screen advises GP but booking continues."""
        sess = {}
        cs.update_screening_state(sess, jv, "my hands are so stiff in the morning")
        assert sess.get("pending_screen") == "inflammatory"
        sess["last_bot_prompt"] = (
            "is the stiffness at its worst first thing in the morning and "
            "lasting more than half an hour, and is it in several joints or "
            "on both sides?"
        )
        r = cs.update_screening_state(sess, jv, "yes more than an hour, both hands")
        assert r["action"] == "escalate"  # advisory line IS spoken
        assert sess.get("screen_red_flag") is None  # but booking not frozen
        assert booking_blocked_reason(sess, jv) is None
        assert "inflammatory" in sess.get("screens_completed", [])


# ─────────────────────────────────────────────────────────────────────────
# Layer 2 — prompt renderers
# ─────────────────────────────────────────────────────────────────────────
class TestPromptRender:
    def test_standard_tier_blocks(self, jv, monkeypatch):
        monkeypatch.delenv("JV_CLINICAL_DEPTH", raising=False)
        static, _ = build_clinic_prompt({"clinic_id": "jv_v1", "turn_count": 1}, jv)
        assert "TREATMENT KNOWLEDGE" in static
        assert "CLINICAL SAFETY SCREENING" in static
        assert "bladder or bowel control" in static
        assert "DEEP-CLINICAL MODE" not in static
        assert "That's one for Marcus" in static  # deflection retained

    def test_pending_screen_dynamic_steer(self, jv):
        _, dynamic = build_clinic_prompt(
            {"clinic_id": "jv_v1", "turn_count": 2, "pending_screen": "cauda_equina"},
            jv,
        )
        assert "SCREEN REQUIRED" in dynamic
        assert "bladder or bowel" in dynamic

    def test_deep_tier_env_kill_switch(self, jv, monkeypatch):
        monkeypatch.setenv("JV_CLINICAL_DEPTH", "deep")
        static, _ = build_clinic_prompt({"clinic_id": "jv_v1", "turn_count": 1}, jv)
        assert "DEEP-CLINICAL MODE" in static
        assert "CLINICAL SAFETY SCREENING" in static  # screening persists in deep
        assert "That's one for Marcus" not in static  # deflection replaced

    def test_env_forces_standard_over_config(self, jv, monkeypatch):
        monkeypatch.setenv("JV_CLINICAL_DEPTH", "standard")
        c = dict(jv)
        c["clinical_depth"] = "deep"
        assert _clinical_depth(c) == "standard"

    def test_depth_defaults_standard(self, monkeypatch):
        monkeypatch.delenv("JV_CLINICAL_DEPTH", raising=False)
        assert _clinical_depth({}) == "standard"
        assert _clinical_depth({"clinical_depth": "bogus"}) == "standard"

    def test_other_template_clinic_unaffected(self, ve, monkeypatch):
        monkeypatch.delenv("JV_CLINICAL_DEPTH", raising=False)
        static, _ = build_clinic_prompt({"clinic_id": "vital_edge", "turn_count": 1}, ve)
        assert "CLINICAL SAFETY SCREENING" not in static
        assert "DEEP-CLINICAL MODE" not in static
        assert "CONDITION FLUENCY" not in static

    def test_fluent_spine_replaces_generic_reassurance(self, jv, monkeypatch):
        """With a condition_knowledge library, the spine's generic-reassurance
        step and self-care deflections must be replaced by the fluent,
        educational variants — no generic escape hatches left."""
        monkeypatch.delenv("JV_CLINICAL_DEPTH", raising=False)
        static, _ = build_clinic_prompt({"clinic_id": "jv_v1", "turn_count": 1}, jv)
        # generic wording gone
        assert "Reassure GENERALLY" not in static
        assert "No self-care advice" not in static
        # fluent replacements present
        assert "SPECIFIC understanding here using the CONDITION FLUENCY library" in static
        assert "answer with genuine, GENERAL education" in static
        # query-type coverage: rest/move, ice/heat, imaging, session count
        assert "staying gently active within comfort" in static
        assert "cold tends to suit the first day or two" in static
        assert "need imaging before" in static
        assert "Never quote a number of sessions" in static

    def test_non_fluency_clinic_keeps_original_spine(self, ve, monkeypatch):
        """Clinics without condition_knowledge keep the original deflection
        wording byte-for-byte."""
        monkeypatch.delenv("JV_CLINICAL_DEPTH", raising=False)
        static, _ = build_clinic_prompt({"clinic_id": "vital_edge", "turn_count": 1}, ve)
        assert "Reassure GENERALLY" in static
        assert "No self-care advice" in static
        assert "GENERAL education" not in static

    def test_library_covers_broad_presentations(self, jv):
        """The breadth angle: common presentations beyond the original 29."""
        conds = {c["name"] for c in jv["condition_knowledge"]["conditions"]}
        for expected in (
            "TMJ / jaw pain",
            "Desk-related / RSI arm and neck pain",
            "Migraine with neck involvement",
            "Thoracic / rib pain",
            "Hamstring strain",
            "Hypermobility",
            "Osteoporosis / bone health",
            "Adolescent growth-plate pain (Osgood-Schlatter / Sever's)",
        ):
            assert expected in conds, expected
        assert len(conds) >= 39

    def test_condition_fluency_block(self, jv, monkeypatch):
        """The library renders with hallmark-feature specificity and the
        anti-generic standard."""
        monkeypatch.delenv("JV_CLINICAL_DEPTH", raising=False)
        static, _ = build_clinic_prompt({"clinic_id": "jv_v1", "turn_count": 1}, jv)
        assert "CONDITION FLUENCY" in static
        # hallmark features, not generic filler
        assert "cinema sign" in static                  # patellofemoral
        assert "FIRST steps out of bed" in static       # plantar fasciitis
        assert "shake your hand out" in static          # carpal tunnel
        assert "putting socks and shoes on" in static   # hip OA
        # the anti-generic rule itself
        assert "would fit every condition equally" in static
        # every screen renders, including the three added later
        for label in (
            "Lower back / leg (cauda equina)", "Calf (DVT)",
            "Significant injury (fracture)", "Neck with neuro/vascular signs",
            "Inflammatory joint pattern",
        ):
            assert label in static, label


# ─────────────────────────────────────────────────────────────────────────
# Outcome classification — safety escalations must never read as "abandoned"
# ─────────────────────────────────────────────────────────────────────────
class TestSafetyEscalationOutcome:
    def test_red_flag_overrides_llm_abandoned_label(self):
        from app.tools.call_summary import infer_call_outcome
        out = infer_call_outcome(
            {"screen_red_flag": "cauda_equina", "call_outcome_logged": "abandoned"},
            {},
        )
        assert out == "safety_escalation"

    def test_emergency_flag_alone_is_safety_escalation(self):
        from app.tools.call_summary import infer_call_outcome
        assert infer_call_outcome({"safety_escalation": True}, {}) == "safety_escalation"

    def test_yields_to_genuine_transactional_outcome(self):
        """Emergency false-alarm caller who then reschedules keeps the
        transactional outcome (CALL 13 in the playbook)."""
        from app.tools.call_summary import infer_call_outcome
        out = infer_call_outcome(
            {"safety_escalation": True},
            {"appointment": {"calendar": {"status": "patched"}}},
        )
        assert out == "rescheduled"

    def test_summary_text_exists(self):
        from app.tools.actionable_summary import _build_summary_text
        t = _build_summary_text("safety_escalation", "", "", 226)
        assert "SAFETY ESCALATION" in t and "urgent care" in t


# ─────────────────────────────────────────────────────────────────────────
# Layer 3 — booking backstop
# ─────────────────────────────────────────────────────────────────────────
class TestBookingBackstop:
    def test_pending_screen_blocks(self, jv):
        reason = booking_blocked_reason({"pending_screen": "cauda_equina"}, jv)
        assert reason and "bladder" in reason

    def test_red_flag_blocks(self, jv):
        reason = booking_blocked_reason({"screen_red_flag": "cauda_equina"}, jv)
        assert reason

    def test_clean_session_not_blocked(self, jv):
        assert booking_blocked_reason({}, jv) is None
        assert booking_blocked_reason({"screens_completed": ["cauda_equina"]}, jv) is None

    def test_disabled_clinic_not_blocked(self, ve):
        assert booking_blocked_reason({"pending_screen": "cauda_equina"}, ve) is None


# ─────────────────────────────────────────────────────────────────────────
# Layer 3b — sanitiser Gate 5e
# ─────────────────────────────────────────────────────────────────────────
class TestDiagnosisLeakStrip:
    def test_strips_diagnostic_assertion(self):
        from app.media_streams.turn_handler import sanitise_response
        sess = {"clinic_id": "jv_v1"}
        out = sanitise_response(
            "It sounds like you have a slipped disc. An assessment with "
            "Marcus would pin down what's going on.",
            sess,
        )
        assert "slipped disc" not in out
        assert "assessment with Marcus" in out

    def test_keeps_screening_question(self):
        from app.media_streams.turn_handler import sanitise_response
        out = sanitise_response(
            "Before we look at the next step, do you have any numbness, "
            "weakness, or changes in bladder or bowel control?",
            {"clinic_id": "jv_v1"},
        )
        assert "bladder" in out

    def test_keeps_general_education(self):
        from app.media_streams.turn_handler import sanitise_response
        out = sanitise_response(
            "Sciatica is one of the most common things we see, and it "
            "usually responds really well to physio.",
            {"clinic_id": "jv_v1"},
        )
        assert "Sciatica" in out

    def test_empty_guard_keeps_whole_diagnostic_response(self):
        from app.media_streams.turn_handler import sanitise_response
        out = sanitise_response(
            "It sounds like you have sciatica.", {"clinic_id": "jv_v1"}
        )
        assert out.strip()
