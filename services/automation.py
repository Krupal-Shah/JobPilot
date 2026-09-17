"""Concrete AutomationService.

Matches detected form-field labels against the candidate's field_map and
screening_answers rules and returns what to fill. Never touches a browser
or the DOM directly. That stays entirely client-side in the extension's
field_scanner.js/content.js. This is what "automation" means on the
backend: turning a list of labels into a list of answers.
"""
from __future__ import annotations

import re

from .dto import AutofillField, AutofillFieldStatus, AutofillResult, CandidateProfile
from .interfaces import AutomationService

_DIRECT_PROFILE_ATTRIBUTES = frozenset(
    {
        "first_name",
        "last_name",
        "full_name",
        "email",
        "phone",
        "school",
        "linkedin",
        "github",
        "address_line_1",
        "city",
        "province",
        "postal_code",
        "degree_program",
        "expected_graduation",
    }
)

# Categories the AI fallback (services/ai_answers.py) must never
# answer, no matter how confident it claims to be: EEO/demographic
# self-identification, and compliance/legal questions (PEP disclosures,
# financial-regulator registration, work eligibility). A wrong guess here
# is a real legal/compliance risk, not a UX inconvenience, so these always
# stay "needs review" for a human when explicit rules do not answer them.
SENSITIVE_CATEGORY_PATTERN = re.compile(
    r"\bsex\b|\bgender\b|lgbtq|disability|veteran|military status"
    r"|race|ethnicity|hispanic or latino"
    r"|government official|politically exposed|public office holder|\bpep\b"
    r"|price waterhouse coopers|external auditor"
    r"|financial regulator|finra|iiroc|securities regulator"
    r"|legally eligible to work|authorized to work|sponsorship"
    r"|immediate family members",
    re.IGNORECASE,
)


def is_sensitive_category(label: str) -> bool:
    """True for EEO/demographic or legal-compliance questions the AI
    fallback must never guess at, regardless of confidence."""
    return bool(SENSITIVE_CATEGORY_PATTERN.search(label))


class RuleBasedAutomationService(AutomationService):
    """Default AutomationService: deterministic regex-rule matching."""

    def build_autofill_plan(
        self, field_labels: list[str], profile: CandidateProfile
    ) -> AutofillResult:
        result = AutofillResult()
        for label in field_labels:
            value = self._match_field_map(label, profile) or self._match_screening_answer(
                label, profile
            )
            if value is not None:
                result.filled.append(AutofillField(label, AutofillFieldStatus.FILLED, value))
            else:
                result.unmapped.append(AutofillField(label, AutofillFieldStatus.UNMAPPED))
        return result

    def _match_field_map(self, label: str, profile: CandidateProfile) -> str | None:
        for rule in profile.field_map:
            if rule.profile_key not in _DIRECT_PROFILE_ATTRIBUTES:
                continue
            if not any(re.search(pattern, label, re.IGNORECASE) for pattern in rule.patterns):
                continue
            value = getattr(profile, rule.profile_key)
            if value:
                return value
        return None

    def _match_screening_answer(self, label: str, profile: CandidateProfile) -> str | None:
        for rule in profile.screening_answers:
            if re.search(rule.pattern, label, re.IGNORECASE):
                return rule.answer
        return None
