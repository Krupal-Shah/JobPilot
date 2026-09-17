"""Concrete AIAnswerService backed by any LiteLLM provider.

Last-resort Tier 2 for a field build_autofill_plan() (Tier 1, deterministic
regex rules) genuinely could not match: an open-ended essay question, or
wording no field_map/screening_answers pattern anticipated. Uses the
candidate's profile and resume text as context.

Gracefully unavailable, never raises, when AGENT_NAME or AGENT_API_KEY is not set,
so teammates without a key still get the full Tier 1 experience with zero
behavior change. Never called at all for the sensitive categories (EEO,
compliance) regardless of availability; see
automation.is_sensitive_category, checked by the caller before
this service is ever reached.
"""
from __future__ import annotations

import logging

from .llm import configuration, complete_text

from .dto import CandidateProfile
from .interfaces import AIAnswerService

_MAX_TOKENS = 300
_DECLINE_TOKEN = "SKIP"
_logger = logging.getLogger(__name__)


class LiteLLMAnswerService(AIAnswerService):
    """Use the configured LiteLLM provider/model for answer suggestions."""

    def is_available(self) -> bool:
        model, api_key = configuration()
        return bool(model and api_key)

    def suggest_answer(
        self,
        label: str,
        options: list[str] | None,
        profile: CandidateProfile,
        resume_text: str,
    ) -> str | None:
        if not self.is_available():
            return None

        try:
            text = complete_text(
                [{"role": "user", "content": self._build_prompt(
                    label, options, profile, resume_text)}], max_tokens=_MAX_TOKENS,
            )
        except Exception:
            _logger.warning("AI answer unavailable; check provider configuration and retry.")
            return None

        if not text or text.strip().upper() == _DECLINE_TOKEN:
            return None
        if options is not None:
            return next((o for o in options if o.strip().lower() == text.strip().lower()), None)
        return text

    def _build_prompt(
        self,
        label: str,
        options: list[str] | None,
        profile: CandidateProfile,
        resume_text: str,
    ) -> str:
        profile_lines = [
            f"Name: {profile.full_name or f'{profile.first_name} {profile.last_name}'}",
            f"School: {profile.school}",
            f"Degree program: {profile.degree_program}",
        ]
        sections = [
            "You are filling in one field of a job application form on behalf of a candidate.",
            "Candidate profile:\n" + "\n".join(profile_lines),
        ]
        if resume_text:
            sections.append(f"Candidate resume text:\n{resume_text[:4000]}")
        sections.append(f'Form field label: "{label}"')

        if options:
            options_list = "\n".join(f"- {option}" for option in options)
            sections.append(
                "This is a multiple-choice field. Reply with EXACTLY one of these options, "
                f"verbatim, and nothing else:\n{options_list}\n"
                f'If none of them genuinely fit, reply with exactly "{_DECLINE_TOKEN}".'
            )
        else:
            sections.append(
                "Reply with a short, first-person answer suitable for a text field on a job "
                "application (one to three sentences for an open-ended question, or a short "
                "phrase for a factual one). No quotation marks, no preamble. If you cannot "
                f'answer confidently from the context given, reply with exactly "{_DECLINE_TOKEN}".'
            )
        return "\n\n".join(sections)
