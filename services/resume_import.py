"""Validate an uploaded template-compatible master and read profile details."""
from __future__ import annotations

import re

from .resume_editor import (
    default_structure, parse_editor_document, submitted_document, validate_editor_values,
)
from .resume_tailoring import TailoringError, _masked_comments

MAX_MASTER_BYTES = 1024 * 1024
_UNSAFE_COMMAND = re.compile(
    r'\\(?:write18|write|openin|openout|read|catcode|csname|endcsname|'
    r'directlua|include|includeonly|import|immediate|special|pdfobj|pdfannot)\b',
    re.IGNORECASE,
)
_INPUT_COMMAND = re.compile(r'\\input\s*(?:\{([^{}]*)\}|([^\s{}]+))', re.IGNORECASE)


def profile_updates_from_latex(source: str) -> dict:
    """Return only unambiguous, nonblank profile fields from a supported resume."""
    if not source.strip() or '\x00' in source:
        raise TailoringError('Upload a nonempty UTF-8 .tex resume.')
    visible = _masked_comments(source)
    if _UNSAFE_COMMAND.search(visible) or any(
        (match[1] or match[2]).strip() != 'glyphtounicode'
        for match in _INPUT_COMMAND.finditer(visible)
    ):
        raise TailoringError('The resume contains TeX commands that cannot be uploaded safely.')
    try:
        document = parse_editor_document(source)
        submitted_document(document, default_structure(document))
    except (ValueError, KeyError, IndexError) as exc:
        raise TailoringError('Use a .tex resume based on the JobPilot master template.') from exc
    values = {field.name: field.value.strip() for field in document.fields}
    if validate_editor_values(document, values, allow_blank=True):
        raise TailoringError('Use valid contact details in the uploaded resume.')

    updates = {}
    name = values.get('contact_name', '')
    if name:
        parts = name.rsplit(' ', 1)
        updates.update(first_name=parts[0], last_name=parts[1] if len(parts) == 2 else '',
                       full_name=name)
    for field, key in (
        ('contact_email', 'email'), ('contact_phone', 'phone'),
        ('contact_linkedin', 'linkedin'), ('contact_github', 'github'),
        ('education_0', 'school'), ('education_2', 'degree_program'),
        ('education_3', 'expected_graduation'),
    ):
        if values.get(field):
            updates[key] = values[field]
    location = values.get('education_1', '')
    if ',' in location:
        city, province = (part.strip() for part in location.split(',', 1))
        if city and province:
            updates.update(city=city, province=province)

    skills = []
    seen = set()
    for field, value in values.items():
        if not re.fullmatch(r'skill_\d+', field):
            continue
        for item in re.split(r'[,;]', value):
            item = item.strip()
            if item and item.casefold() not in seen:
                candidate = skills + [item]
                if len(', '.join(candidate)) > 1000:
                    break
                skills.append(item)
                seen.add(item.casefold())
    if skills:
        updates['skills'] = skills
    return updates
