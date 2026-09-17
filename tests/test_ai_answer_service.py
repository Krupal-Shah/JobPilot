"""Run: python -m unittest discover -s tests -p 'test_*.py'."""
import os
import unittest
from unittest import mock

from services.ai_answers import LiteLLMAnswerService
from services.automation import is_sensitive_category


class SensitiveCategoryTests(unittest.TestCase):
    def test_eeo_and_compliance_questions_are_flagged_sensitive(self):
        sensitive_labels = [
            "Sex *",
            "Gender Identity *",
            "LGBTQ+ Community Member",
            "Do you consider yourself to be a person with a disability?",
            "Veteran/Military Status",
            "Race/Ethnicity *",
            "Are you a Politically Exposed Person (PEP)?",
            "Have you ever worked for Price Waterhouse Coopers?",
            "Are you registered with FINRA?",
            "Are you legally eligible to work in this country?",
            "Do any of your immediate family members work here?",
        ]
        for label in sensitive_labels:
            with self.subTest(label=label):
                self.assertTrue(is_sensitive_category(label))

    def test_ordinary_questions_are_not_flagged_sensitive(self):
        ordinary_labels = ["First Name", "Why do you want to work here?", "Expected graduation date"]
        for label in ordinary_labels:
            with self.subTest(label=label):
                self.assertFalse(is_sensitive_category(label))


class LiteLLMAnswerServiceTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("services.llm.dotenv_values", return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_unavailable_without_an_api_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            service = LiteLLMAnswerService()
            self.assertFalse(service.is_available())

    def test_suggest_answer_returns_none_when_unavailable(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            service = LiteLLMAnswerService()
            result = service.suggest_answer("Why do you want to work here?", None, mock.Mock(), "")
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
