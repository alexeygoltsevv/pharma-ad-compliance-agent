from . import (
    art24_other,
    art24_p1_minors,
    art24_p2_specific_cases,
    art24_p3_no_side_effects,
    art24_p4_doctor_recommendation,
    art24_p5_mandatory_disclaimer,
)

ALL_CHECKERS = (
    art24_p1_minors.check,
    art24_p2_specific_cases.check,
    art24_p3_no_side_effects.check,
    art24_p4_doctor_recommendation.check,
    art24_p5_mandatory_disclaimer.check,
    art24_other.check,
)

__all__ = ["ALL_CHECKERS"]
