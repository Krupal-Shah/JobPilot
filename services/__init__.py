"""Service layer: interfaces (services.interfaces) + DTOs (services.dto)
+ concrete implementations, exported here.

Nothing in this package runs on import. No background jobs, no
module-level singletons wired at import time. A route (or another
workstream's module) constructs the service it needs and calls it
explicitly, once, per request.
"""
from .ai_answers import LiteLLMAnswerService
from .applications import SqliteApplicationService
from .automation import RuleBasedAutomationService
from .profiles import SqliteProfileService
from .resume_tailoring import ResumeTailoringService
from .scraper import JobScraperService
from .users import SqliteUserService

__all__ = [
    "LiteLLMAnswerService",
    "SqliteApplicationService",
    "RuleBasedAutomationService",
    "SqliteProfileService",
    "ResumeTailoringService",
    "JobScraperService",
    "SqliteUserService",
]
