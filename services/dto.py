"""Structured data objects (DTOs) shared across the service layer.

Plain `dataclasses`, not raw dicts. Every field has a name and a type a
caller (or an IDE) can check, instead of trusting a dict key spelled
correctly at every call site.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class ApplicationStage(str, Enum):
    """Where a single tracked application sits in the pipeline."""

    BOOKMARKED = "bookmarked"
    APPLIED = "applied"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"


class AutofillFieldStatus(str, Enum):
    """Outcome of trying to answer one detected form field."""

    FILLED = "filled"
    UNMAPPED = "unmapped"
    AI_SUGGESTED = "ai_suggested"


@dataclass(frozen=True)
class User:
    """A registered account.

    The password field contains a hash.
    """

    userID: int
    name: str
    email: str
    password: str | None


@dataclass(frozen=True)
class JobPosting:
    """A single job posting, scraped from a page or entered by hand."""

    company: str
    title: str
    url: str
    description: str = ""
    location: str | None = None


@dataclass(frozen=True)
class Application:
    """One tracked application: a job posting plus its pipeline state."""

    id: int
    job: JobPosting
    stage: ApplicationStage
    date_applied: date | None = None
    resume_variant: str | None = None
    resume_base: str | None = None
    resume_state: str | None = None
    tailoring_error: str | None = None
    cover_letter: str | None = None
    notes: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class AutofillField:
    """One form field the automation service was asked to answer."""

    label: str
    status: AutofillFieldStatus
    value: str | None = None


@dataclass
class AutofillResult:
    """Everything the extension needs to show after a fill attempt.

    `ai_suggested` is populated separately from `filled`, never merged
    into it: an AI-generated guess is not the same confidence level as an
    explicit field_map/screening_answers rule, and the popup shows them
    under a distinct "please verify" heading rather than "Completed".
    """

    filled: list[AutofillField] = field(default_factory=list)
    ai_suggested: list[AutofillField] = field(default_factory=list)
    unmapped: list[AutofillField] = field(default_factory=list)


@dataclass(frozen=True)
class FieldMapRule:
    """Maps a detected field label to a direct profile attribute."""

    field_name: str
    profile_key: str
    patterns: list[str]
    select_dropdown: bool = False


@dataclass(frozen=True)
class ScreeningAnswerRule:
    """Maps a Yes/No screening-question label to a fixed answer."""

    pattern: str
    answer: str


@dataclass(frozen=True)
class CandidateProfile:
    """The master profile the automation service fills forms from."""

    first_name: str
    last_name: str
    email: str
    phone: str
    school: str
    full_name: str = ""
    linkedin: str = ""
    github: str = ""
    address_line_1: str = ""
    city: str = ""
    province: str = ""
    postal_code: str = ""
    degree_program: str = ""
    expected_graduation: str = ""
    field_map: list[FieldMapRule] = field(default_factory=list)
    screening_answers: list[ScreeningAnswerRule] = field(default_factory=list)
    desired_titles: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
