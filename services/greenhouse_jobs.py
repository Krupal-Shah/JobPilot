"""Public Greenhouse board listings for junior software-related roles."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

API_ROOT = 'https://boards-api.greenhouse.io/v1/boards'
DEFAULT_BOARDS = (
    ('Affirm', 'affirm'), ('Cloudflare', 'cloudflare'), ('Coinbase', 'coinbase'),
    ('Cresta', 'cresta'), ('Databricks', 'databricks'), ('DRW', 'drweng'),
    ('Duolingo', 'duolingo'), ('Figma', 'figma'), ('MongoDB', 'mongodb'),
    ('Roblox', 'roblox'), ('Samsara', 'samsara'), ('Scale AI', 'scaleai'),
    ('StackAdapt', 'stackadapt-confidential'), ('Stripe', 'stripe'),
    ('BitGo', 'bitgowinter2027'),
)
_BOARD_NAMES = {token: name for name, token in DEFAULT_BOARDS}
_TOKEN = re.compile(r'^[a-zA-Z0-9_-]+$')
_ROLE = re.compile(
    r'\b(?:software|back[ -]?end|front[ -]?end|full[ -]?stack|platform|'
    r'infrastructure|site reliability|sre|devops|data|database|data science|'
    r'machine learning|ml|ai|artificial intelligence|computer vision|'
    r'cybersecurity|security|mobile|ios|android|cloud|qa|automation|'
    r'systems|network|product|analytics|application|research)\s+'
    r'(?:engineer(?:ing)?|developer|scientist|analyst|administrator|tester)\b'
    r'|\bsoftware\s+development\s+engineer\b|\b(?:sde|swe|sre)\b'
    r'|\b(?:software|database|data|machine learning|ml|ai|cybersecurity)\s+'
    r'(?:intern|co-?op)\b'
    r'|\b(?:engineer(?:ing)?|developer)\s*,?\s*(?:software|back[ -]?end|'
    r'front[ -]?end|full[ -]?stack|data|database|machine learning|ml|ai|platform)\b'
    r'|\b(?:machine learning|data science)\s+intern\b', re.I,
)
_JUNIOR = re.compile(
    r'\b(?:intern(?:ship)?|new\s+grad(?:uate)?|graduate|entry[ -]?level|'
    r'junior|associate|early\s+career|university\s+grad(?:uate)?|co-?op|'
    r'apprentice|engineer\s+i|developer\s+i|swe\s+i|level\s+1|l1)\b', re.I,
)
_SENIOR = re.compile(
    r'\b(?:senior|sr\.?|staff|principal|lead|manager|director|head|'
    r'vice\s+president|vp|architect|distinguished|mid[ -]?level|'
    r'engineer\s+(?:ii|iii|iv|v)|developer\s+(?:ii|iii|iv|v))\b', re.I,
)
_CANADA_COUNTRY = re.compile(r'\b(?:canada|canadian)\b', re.I)
_CANADA_PROVINCES = re.compile(
    r'\b(?:alberta|british columbia|manitoba|new brunswick|newfoundland(?: and labrador)?|'
    r'nova scotia|northwest territories|nunavut|ontario|prince edward island|'
    r'qu[eé]bec|saskatchewan|yukon)\b', re.I,
)
_CANADA_PROVINCE_CODES = re.compile(
    r'(?:^|[,;/()])\s*(?:AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT)\b'
)
_CANADA_CITIES = re.compile(
    r'\b(?:toronto|vancouver|montreal|montréal|ottawa|calgary|edmonton|'
    r'winnipeg|halifax|qu[eé]bec city|kitchener|mississauga|'
    r'burnaby|markham|kanata)\b', re.I,
)
_cache: dict[tuple, tuple[float, list[dict], list[str]]] = {}
_cache_lock = threading.Lock()
CACHE_SECONDS = 15 * 60


def configured_boards() -> tuple[tuple[str, str], ...]:
    """GREENHOUSE_BOARDS is an optional comma-separated list of board tokens."""
    raw = os.environ.get('GREENHOUSE_BOARDS')
    if raw is None:
        return DEFAULT_BOARDS
    tokens = [token.strip().lower() for token in raw.split(',')]
    if len(tokens) > 30 or any(not _TOKEN.fullmatch(token) for token in tokens):
        raise ValueError('GREENHOUSE_BOARDS must contain 1–30 comma-separated board tokens.')
    unique = dict.fromkeys(tokens)
    return tuple((_BOARD_NAMES.get(token, token.replace('-', ' ').title()), token)
                 for token in unique)


def is_junior_software_title(title: str) -> bool:
    return bool(_ROLE.search(title) and _JUNIOR.search(title) and not _SENIOR.search(title))


def is_canadian_location(location: str) -> bool:
    """Include only postings with a clear Canadian location or work eligibility."""
    if not isinstance(location, str) or not location.strip():
        return False
    # Remove ambiguous US city/state pairs before checking Canadian signals.
    location = re.sub(
        r'\b(?:Ontario\s*,\s*(?:CA|California)|'
        r'Vancouver\s*,\s*(?:WA|Washington)|'
        r'Ottawa\s*,\s*(?:IL|Illinois))\b', '', location, flags=re.I,
    )
    return bool(_CANADA_COUNTRY.search(location) or _CANADA_PROVINCES.search(location)
                or _CANADA_PROVINCE_CODES.search(location) or _CANADA_CITIES.search(location))


def _safe_url(url: object) -> str | None:
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        return None
    return url


def _fetch_board(board: tuple[str, str]) -> tuple[list[dict], str | None]:
    company, token = board
    request = Request(f'{API_ROOT}/{token}/jobs', headers={
        'Accept': 'application/json', 'User-Agent': 'JobOrganizer/1.0',
    })
    try:
        with urlopen(request, timeout=10) as response:
            payload = response.read(5_000_001)
        if len(payload) > 5_000_000:
            raise ValueError('Board response is too large')
        jobs = json.loads(payload).get('jobs')
        if not isinstance(jobs, list):
            raise ValueError('Board response is missing jobs')
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, UnicodeError, AttributeError):
        return [], company

    found = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        title = job.get('title')
        url = _safe_url(job.get('absolute_url'))
        if not isinstance(title, str) or not url or not is_junior_software_title(title):
            continue
        location = job.get('location')
        location_name = location.get('name') if isinstance(location, dict) else None
        updated_at = job.get('updated_at')
        found.append({
            'id': job.get('id'), 'company': company, 'title': title.strip(),
            'location': location_name.strip() if isinstance(location_name, str) else '',
            'url': url, 'updated_at': updated_at if isinstance(updated_at, str) else '',
        })
    return found, None


def list_jobs(boards: tuple[tuple[str, str], ...] | None = None,
              *, refresh: bool = False) -> tuple[list[dict], list[str]]:
    boards = configured_boards() if boards is None else boards
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(boards)
        if cached and cached[0] > now and not refresh:
            return cached[1], cached[2]
    with ThreadPoolExecutor(max_workers=min(6, len(boards) or 1)) as pool:
        responses = list(pool.map(_fetch_board, boards))
    seen = set()
    jobs = []
    failed = []
    for board_jobs, error in responses:
        if error:
            failed.append(error)
        for job in board_jobs:
            if job['url'] not in seen:
                seen.add(job['url'])
                jobs.append(job)
    with _cache_lock:
        _cache[boards] = (now + (60 if failed else CACHE_SECONDS), jobs, failed)
    return jobs, failed


def _terms(text: str) -> set[str]:
    words = re.findall(r'[a-z0-9]+', text.lower())
    return {'engineer' if word in ('engineering', 'engineers') else word for word in words}


def title_match_score(title: str, desired_titles: list[str]) -> int:
    title_terms = _terms(title)
    scores = []
    for desired in desired_titles:
        desired_terms = _terms(desired)
        if desired_terms and desired_terms <= title_terms:
            scores.append(len(desired_terms))
    return max(scores, default=0)


def prioritize_jobs(jobs: list[dict], desired_titles: list[str]) -> list[dict]:
    """Return new display records so cached board data stays account-neutral."""
    def updated_at(job):
        try:
            return datetime.fromisoformat(job['updated_at'].replace('Z', '+00:00')).timestamp()
        except (TypeError, ValueError, OverflowError):
            return 0

    ranked = [{**job, 'title_match': title_match_score(job['title'], desired_titles)}
              for job in jobs]
    ranked.sort(key=lambda job: (-job['title_match'], -updated_at(job),
                                 job['company'].lower(), job['title'].lower()))
    return ranked
