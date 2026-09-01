"""B-130 - a body part the caller never named reached the operator's SMS.

`extract_condition_label` matched its body-part table by SUBSTRING, so an
ordinary English word containing one produced a clinical label. Found on
2026-09-01 scanning 4,534 stored caller turns:

    "no it's not swollen nor warm"   -> "arm pain"    (w-ARM)
    "just want to get our chip"      -> "hip pain"    (c-HIP)
    "how far towards manchester"     -> "chest pain"  (man-CHEST-er)

The last is why this is not cosmetic: this clinic is IN Manchester, so the
collision word is one every local caller says, and "chest pain" is a red flag
an operator would act on.
"""
import pytest

from app.notifications.smart_sms_router import extract_condition_label


class TestConditionLabelMatchesWholeWords:
    @pytest.mark.parametrize("utterance", [
        "no it's not swollen nor warm",                      # w-ARM
        "just want to get our chip",                         # c-HIP
        "how far towards manchester",                        # man-CHEST-er
    ])
    def test_a_body_part_inside_another_word_is_not_a_body_part(self, utterance):
        got = extract_condition_label(utterance)
        for wrong in ("arm pain", "hip pain", "chest pain"):
            assert got != wrong, "{0!r} -> {1!r}".format(utterance, got)

    def test_manchester_never_reads_as_chest_pain(self):
        """Named separately because this clinic is IN Manchester: the collision
        word is one every local caller says, and the label it produced is a red
        flag an operator would act on."""
        assert extract_condition_label(
            "okay and in terms of distance towards manchester how does that work"
        ) != "chest pain"

    @pytest.mark.parametrize("utterance,expected", [
        ("my knees been so", "knee pain"),
        ("both shoulders ache", "shoulder pain"),
        ("my ankle's sore", "ankle pain"),
        ("left shoulder it's been really sore for a couple of weeks",
         "shoulder pain"),
        ("lower back pain after the gym", "lower back pain"),
    ])
    def test_ordinary_inflections_still_match(self, utterance, expected):
        """Only the LEADING boundary is anchored, deliberately - plurals and
        possessives are what the substring behaviour was buying, and they are
        kept. Measured: 0 legitimate labels changed across 4,534 stored turns."""
        assert extract_condition_label(utterance) == expected

    def test_fracture_and_surgery_framing_survives(self):
        assert extract_condition_label("i broke my foot") == "a broken foot"
