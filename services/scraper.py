"""Concrete ScraperService.

Works only from what the extension already captured in the browser: raw
page HTML, or plain visible text if the content script extracted it
first. No network access, no headless browser, no new dependency. That
keeps this side of the integration usable by any teammate's module with a
plain `pip install -r requirements.txt`.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urlparse

from .dto import JobPosting
from .interfaces import ScraperService

_TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_REPEATED_SPACE_RE = re.compile(r"[ \t]+")
_REPEATED_BLANK_LINE_RE = re.compile(r"\n{3,}")
_ATS_SUBDOMAINS = {"boards", "jobs", "apply", "careers", "greenhouse", "lever", "myworkdayjobs"}
_UNTITLED_ROLE = "Untitled role"


class _VisibleTextExtractor(HTMLParser):
    """Strips tags/scripts/styles, keeping only visible page text."""

    _SKIPPED_TAGS = frozenset({"script", "style", "noscript", "template"})

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIPPED_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIPPED_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._chunks.append(data.strip())

    def visible_text(self) -> str:
        return "\n".join(self._chunks)


def _looks_like_html(text: str) -> bool:
    return "<" in text and ">" in text


def _normalize_whitespace(raw_text: str) -> str:
    collapsed_spaces = _REPEATED_SPACE_RE.sub(" ", raw_text)
    return _REPEATED_BLANK_LINE_RE.sub("\n\n", collapsed_spaces).strip()


def _guess_company_from_url(url: str) -> str:
    """Best-effort company name from the URL's host.

    Known limitation: on a shared ATS vendor domain (boards.greenhouse.io,
    jobs.lever.co) the real company name is a URL *path* segment, not part
    of the host, so this heuristic returns the vendor's name instead. Good
    enough for a company with its own careers domain (careers.acme.com);
    the dashboard lets the bookmarked company name be corrected by hand.
    """
    host = urlparse(url).netloc.lower().removeprefix("www.")
    labels = host.split(".")
    if labels and labels[0] in _ATS_SUBDOMAINS and len(labels) > 1:
        labels = labels[1:]
    company_slug = labels[0] if labels else ""
    return company_slug.replace("-", " ").title()


class JobScraperService(ScraperService):
    """Default, dependency-free job-posting scraper."""

    def get_current_job_description(self, html: str) -> str:
        if not html:
            return ""
        if not _looks_like_html(html):
            return _normalize_whitespace(html)

        extractor = _VisibleTextExtractor()
        extractor.feed(html)
        return _normalize_whitespace(extractor.visible_text())

    def extract_job_posting(
        self,
        html: str,
        url: str,
        visible_text: str | None = None,
        title_override: str | None = None,
    ) -> JobPosting:
        page_title = title_override.strip() if title_override and title_override.strip() else None
        if page_title is None:
            title_match = _TITLE_TAG_RE.search(html) if html else None
            page_title = title_match.group(1).strip() if title_match else _UNTITLED_ROLE

        return JobPosting(
            company=_guess_company_from_url(url),
            title=page_title,
            url=url,
            description=self.get_current_job_description(visible_text or html),
        )
