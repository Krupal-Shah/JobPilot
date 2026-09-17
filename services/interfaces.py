"""Service interfaces (Python ABCs).

Routes and other teammates' modules should depend on these interfaces and
the DTOs in `dto.py`, not on a concrete implementation class. That keeps
the resume-tailoring workstream, for example, decoupled from *how*
scraping works internally.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .dto import (
    Application,
    ApplicationStage,
    AutofillResult,
    CandidateProfile,
    JobPosting,
    User,
)


class UserService(ABC):
    """Reads and writes registered user accounts."""

    @abstractmethod
    def create_user(self, name: str, email: str, password: str) -> User:
        """Create a new account. Raises ValueError if the email is taken."""

    @abstractmethod
    def get_user(self, user_id: int) -> User | None: ...

    @abstractmethod
    def get_user_by_email(self, email: str) -> User | None: ...

    @abstractmethod
    def verify_credentials(self, email: str, password: str) -> User | None:
        """Return the user if email+password match, else None."""


class ScraperService(ABC):
    """Turns a captured job-posting page into structured data."""

    @abstractmethod
    def get_current_job_description(self, html: str) -> str:
        """Return just the plain-text job description.

        `html` may be full page HTML or plain visible text the caller
        already extracted. This is the single function other workstreams,
        like resume tailoring, can call directly for "what does this
        posting actually ask for."
        """

    @abstractmethod
    def extract_job_posting(
        self,
        html: str,
        url: str,
        visible_text: str | None = None,
        title_override: str | None = None,
    ) -> JobPosting:
        """Return a full JobPosting (company, title, description, url).

        `visible_text`/`title_override` let a caller with real DOM access
        (the extension's content script) pass in a more precise
        description/title than a blind regex/tag-strip over `html` could
        find on its own.
        """


class ApplicationService(ABC):
    """Reads and writes tracked applications (the application tracker).

    Every method takes a `user_id` so each user only ever sees and
    modifies their own applications.
    """

    @abstractmethod
    def list_applications(
        self, user_id: int, stage: ApplicationStage | None = None
    ) -> list[Application]: ...

    @abstractmethod
    def get_application(self, application_id: int, user_id: int) -> Application | None: ...

    @abstractmethod
    def create_application(
        self,
        job: JobPosting,
        user_id: int,
        stage: ApplicationStage = ApplicationStage.BOOKMARKED,
    ) -> Application: ...

    @abstractmethod
    def update_stage(
        self, application_id: int, user_id: int, stage: ApplicationStage
    ) -> Application: ...

    @abstractmethod
    def update_application(
        self,
        application_id: int,
        user_id: int,
        company: str | None = None,
        title: str | None = None,
        url: str | None = None,
        description: str | None = None,
        notes: str | None = None,
    ) -> Application:
        """Edit an application's own fields. Pass only what should change;
        anything left as None keeps its current value."""

    @abstractmethod
    def delete_application(self, application_id: int, user_id: int) -> None:
        """Remove an application from the tracker entirely (unbookmark)."""


class AutomationService(ABC):
    """Decides how to answer detected form fields. No DOM/browser access.

    The actual clicking/typing happens client-side in the extension's
    field_scanner.js/content.js; this service only turns a list of
    detected field labels into a list of answers.
    """

    @abstractmethod
    def build_autofill_plan(
        self, field_labels: list[str], profile: CandidateProfile
    ) -> AutofillResult: ...


class ProfileService(ABC):
    """Reads and writes a user's candidate profile."""

    @abstractmethod
    def get_profile(self, user_id: int) -> CandidateProfile: ...

    @abstractmethod
    def save_profile(self, user_id: int, profile: CandidateProfile) -> CandidateProfile: ...


class AIAnswerService(ABC):
    """Tier 2, last-resort answer for a field build_autofill_plan()
    genuinely could not match to any explicit rule.

    Never called for the sensitive categories (EEO/demographic,
    compliance/legal, financial-regulator questions) regardless of
    availability. See automation.py's SENSITIVE_CATEGORY_PATTERNS
    for the exact exclusion list; a caller should check that separately
    before ever reaching this service.
    """

    @abstractmethod
    def is_available(self) -> bool:
        """False with no API key configured, so callers can skip Tier 2
        entirely rather than making (and catching) a doomed call."""

    @abstractmethod
    def suggest_answer(
        self,
        label: str,
        options: list[str] | None,
        profile: CandidateProfile,
        resume_text: str,
    ) -> str | None:
        """Return a proposed answer, or None if it should decline.

        `options` is the exact set of choices for a select/radio/checkbox
        field; the returned value must be one of them verbatim, or None.
        For a free-text field, `options` is None and any short text answer
        may be returned. Never guesses; returns None on any error or when
        the model itself is not confident.
        """
