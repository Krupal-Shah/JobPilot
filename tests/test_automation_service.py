"""Run: python -m unittest discover -s tests -p 'test_*.py'."""
import unittest

from services.automation import RuleBasedAutomationService
from services.dto import AutofillFieldStatus, CandidateProfile, FieldMapRule, ScreeningAnswerRule


class AutomationServiceTests(unittest.TestCase):
    def setUp(self):
        self.automation = RuleBasedAutomationService()
        self.profile = CandidateProfile(
            first_name="Jordan",
            last_name="Rivera",
            email="jordan.rivera@example.com",
            phone="+1 555-0142",
            school="Riverbend University",
            field_map=[
                FieldMapRule(field_name="email", profile_key="email", patterns=["email"]),
                FieldMapRule(field_name="school", profile_key="school", patterns=["school", "university"]),
            ],
            screening_answers=[
                ScreeningAnswerRule(pattern="18 years", answer="Yes"),
                ScreeningAnswerRule(pattern="require.*sponsorship", answer="No"),
            ],
        )

    def test_fills_direct_profile_field(self):
        result = self.automation.build_autofill_plan(["Email address"], self.profile)

        self.assertEqual(len(result.filled), 1)
        self.assertEqual(result.filled[0].value, "jordan.rivera@example.com")
        self.assertEqual(result.filled[0].status, AutofillFieldStatus.FILLED)

    def test_fills_screening_answer(self):
        result = self.automation.build_autofill_plan(["Are you at least 18 years of age?"], self.profile)

        self.assertEqual(result.filled[0].value, "Yes")

    def test_unmapped_field_is_reported(self):
        result = self.automation.build_autofill_plan(["Why do you want to work here?"], self.profile)

        self.assertEqual(len(result.filled), 0)
        self.assertEqual(result.unmapped[0].label, "Why do you want to work here?")

    def test_multiple_fields_split_correctly(self):
        result = self.automation.build_autofill_plan(
            ["Email address", "Why do you want to work here?", "University"], self.profile
        )

        self.assertEqual(len(result.filled), 2)
        self.assertEqual(len(result.unmapped), 1)

    def test_unanchored_field_map_pattern_can_false_positive_inside_another_word(self):
        # Regression: "Race/Ethnicity" contains the literal substring
        # "city" (e-t-h-n-i-CITY), so an unanchored "city" pattern matched
        # it and answered a demographic question with the wrong value
        # entirely (the candidate's city of residence). Word-boundary
        # anchoring is what fixes this class of bug generally.
        rule = FieldMapRule(field_name="city", profile_key="school", patterns=["city"])
        profile = CandidateProfile(
            first_name="Jordan", last_name="Rivera", email="j@example.com",
            phone="555-0142", school="Riverbend University", field_map=[rule],
        )
        result = self.automation.build_autofill_plan(["Race/Ethnicity"], profile)

        self.assertEqual(len(result.filled), 1)  # demonstrates the bug exists...
        self.assertEqual(result.filled[0].value, "Riverbend University")  # ...with a nonsense answer

    def test_word_boundary_pattern_avoids_the_false_positive(self):
        rule = FieldMapRule(field_name="city", profile_key="school", patterns=[r"\bcity\b"])
        profile = CandidateProfile(
            first_name="Jordan", last_name="Rivera", email="j@example.com",
            phone="555-0142", school="Riverbend University", field_map=[rule],
        )
        result = self.automation.build_autofill_plan(["Race/Ethnicity", "City"], profile)

        self.assertEqual(len(result.unmapped), 1)
        self.assertEqual(result.unmapped[0].label, "Race/Ethnicity")
        self.assertEqual(len(result.filled), 1)
        self.assertEqual(result.filled[0].label, "City")

    def test_blank_profile_value_is_treated_as_unmapped(self):
        rule = FieldMapRule(field_name="city", profile_key="city", patterns=["^city$"])
        profile = CandidateProfile(
            first_name="Jordan", last_name="Rivera", email="j@example.com",
            phone="555-0142", school="Riverbend University", field_map=[rule],
        )
        result = self.automation.build_autofill_plan(["City"], profile)

        self.assertEqual(len(result.filled), 0)
        self.assertEqual(result.unmapped[0].label, "City")


if __name__ == "__main__":
    unittest.main()
