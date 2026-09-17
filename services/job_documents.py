"""Job-specific resume selection, editing, matching feedback, and draft text."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

from .llm import complete_text, configuration
from .resume_editor import (
    _escape, apply_structured_values, default_structure, parse_editor_document,
    submitted_document, validate_editor_values,
)
from .resume_tailoring import COUNTS, TailoringError, _masked_comments, limit_coursework, parse_blocks, rank_resume

PROMPTS = Path(__file__).resolve().parent.parent / 'assets' / 'prompts'
STOP = frozenset(('with', 'from', 'that', 'this', 'have', 'will', 'your', 'their', 'into',
                  'about', 'work', 'team', 'role', 'experience', 'years', 'using', 'skills',
                  'required', 'preferred', 'candidate', 'ability', 'strong', 'company'))
SKILLS = ('python', 'sql', 'java', 'javascript', 'typescript', 'react', 'flask', 'django',
          'aws', 'azure', 'docker', 'kubernetes', 'linux', 'git', 'api', 'rest', 'html',
          'css', 'postgresql', 'mongodb', 'machine learning', 'data analysis',
          'communication', 'leadership', 'testing', 'ci/cd', 'c++', 'c#', 'go')
COVER_WORD_LIMIT = 350
POINT_LIMITS = {'experience': 4, 'projects': 3, 'leadership': 3}
OPTIONAL_SECTIONS = {'profile summary': 'Profile summary', 'profile': 'Profile',
                     'certifications': 'Certifications', 'publications': 'Publications'}


def _terms(value: str) -> set[str]:
    return {word for word in re.findall(r'[a-z][a-z0-9+#-]{3,}', value.lower())
            if word not in STOP}


def _heuristic_rank(description: str, blocks: dict) -> dict:
    terms = _terms(description)
    return {category: [block.id for block in sorted(items, key=lambda block: (
        -len(terms & _terms(block.latex)), block.start))]
            for category, items in blocks.items()}


def _rank_points(description: str, document) -> dict[str, list[str]]:
    """Rank existing points by job overlap, keeping source order for ties."""
    terms = _terms(description)
    ranked = {}
    for group in document.groups:
        if not group.category:
            continue
        points = [field for field in group.fields if '_bullet_' in field.name]
        ranked[group.entry_id] = [field.name for field in sorted(
            points, key=lambda field: -len(terms & _terms(field.value)))]
    return ranked


def _optional_section_spans(latex: str) -> dict[str, tuple[int, int]]:
    source = _masked_comments(latex)
    sections = list(re.finditer(r'\\section\{([^{}]+)\}', source))
    end_document = source.index(r'\end{document}')
    return {match[1].strip().lower():
            (match.start(), sections[index + 1].start() if index + 1 < len(sections) else end_document)
            for index, match in enumerate(sections) if match[1].strip().lower() in OPTIONAL_SECTIONS}


def initial_resume_state(description: str, master: str) -> tuple[dict, str]:
    """Select whole blocks using the existing AI ranker, with a local fallback."""
    document = parse_editor_document(master)
    blocks = parse_blocks(master)
    method = 'keyword'
    if all(configuration()) and description.strip():
        try:
            rankings = rank_resume(description, blocks)
            method = 'AI'
        except TailoringError:
            rankings = _heuristic_rank(description, blocks)
    else:
        rankings = _heuristic_rank(description, blocks)
    # The saved state retains every original block so the user can unhide it.
    state = {
        'structure': default_structure(document),
        'values': {field.name: field.value for field in document.fields},
        'visible': {category: rankings[category][:COUNTS[category]] for category in COUNTS},
        'visible_points': {entry_id: point_ids[:POINT_LIMITS[entry_id.split('_')[0]]]
                           for entry_id, point_ids in _rank_points(description, document).items()},
        'visible_sections': {name: False for name in _optional_section_spans(master)},
        'selection_method': method,
    }
    return state, method


def state_from_existing(master: str, saved: str) -> dict:
    """Recover selected block IDs from a previous source-only resume variant."""
    document = parse_editor_document(master)
    original = parse_blocks(master)
    previous = parse_blocks(saved)
    visible = {}
    for category, blocks in original.items():
        wanted = Counter(block.latex for block in previous[category])
        visible[category] = []
        for block in blocks:
            if wanted[block.latex] > 0:
                visible[category].append(block.id)
                wanted[block.latex] -= 1
        if any(wanted.values()):
            raise TailoringError('The saved resume no longer matches this master. Download its PDF before replacing it.')
    original_sections = _optional_section_spans(master)
    previous_sections = _optional_section_spans(saved)
    return {'structure': default_structure(document),
            'values': {field.name: field.value for field in document.fields},
            'visible': visible, 'selection_method': 'existing',
            'visible_sections': {name: name in previous_sections for name in original_sections},
            'visible_points': {group.entry_id: [field.name for field in group.fields
                                if '_bullet_' in field.name] for group in document.groups if group.entry_id}}


def render_resume(master: str, state: dict) -> str:
    try:
        document = parse_editor_document(master)
        structure = state['structure']
        display = submitted_document(document, structure)
        values = state['values']
        if not isinstance(values, dict) or validate_editor_values(display, values):
            raise TailoringError('Correct the highlighted resume points before saving.')
        visible = state['visible']
        if not isinstance(visible, dict) or set(visible) != set(COUNTS):
            raise TailoringError('Choose which resume blocks to show.')
        for category, entries in structure.items():
            allowed = {f'{category}:{index}' for index in range(len(entries))}
            chosen = visible.get(category)
            if not isinstance(chosen, list) or len(chosen) != len(set(chosen)) or not set(chosen) <= allowed:
                raise TailoringError('The resume selection is invalid.')
            minimum = min(COUNTS[category], len(entries))
            if len(chosen) < minimum:
                raise TailoringError(f'Show at least {minimum} {category} blocks.')
        revised = apply_structured_values(master, document, display, structure, values)
        point_visibility = state.get('visible_points')
        if point_visibility is not None:
            if not isinstance(point_visibility, dict):
                raise TailoringError('Choose which resume points to show.')
            revised_document = parse_editor_document(revised)
            revised_blocks = parse_blocks(revised)
            point_removals = []
            category_index = {category: 0 for category in COUNTS}
            for group in revised_document.groups:
                if not group.category:
                    continue
                index = category_index[group.category]
                category_index[group.category] += 1
                entry = structure[group.category][index]
                bullet_ids = entry['bullets']
                bullet_fields = [field for field in group.fields if '_bullet_' in field.name]
                if len(bullet_fields) != len(bullet_ids):
                    raise TailoringError('The resume point selection is invalid.')
                selected = point_visibility.get(entry['id'])
                if (not isinstance(selected, list) or len(selected) != len(set(selected)) or
                        not set(selected) <= set(bullet_ids)):
                    raise TailoringError('The resume point selection is invalid.')
                if f'{group.category}:{index}' in visible[group.category] and not selected:
                    raise TailoringError('Show at least one point in each visible block.')
                block = revised_blocks[group.category][index]
                for bullet_id, field in zip(bullet_ids, bullet_fields):
                    if bullet_id not in selected:
                        command_start = revised.rfind(r'\resumeItem', block.start, field.start)
                        if command_start < 0:
                            raise TailoringError('The resume contains an unsupported point.')
                        point_removals.append((command_start, field.end + 1))
            for start, end in sorted(point_removals, reverse=True):
                revised = revised[:start] + revised[end:]
        blocks = parse_blocks(revised)
        removals = [(block.start, block.end) for category, items in blocks.items()
                    for block in items if block.id not in visible[category]]
        for start, end in sorted(removals, reverse=True):
            revised = revised[:start] + revised[end:]
        sections = state.get('visible_sections')
        if sections is not None:
            known = _optional_section_spans(revised)
            if not isinstance(sections, dict) or set(sections) != set(known) or any(
                not isinstance(value, bool) for value in sections.values()
            ):
                raise TailoringError('The resume section selection is invalid.')
            for start, end in sorted((known[name] for name, shown in sections.items()
                                      if not shown), reverse=True):
                revised = revised[:start] + revised[end:]
        return limit_coursework(revised)
    except (KeyError, TypeError, IndexError, ValueError) as exc:
        if isinstance(exc, TailoringError):
            raise
        raise TailoringError('The resume draft is invalid. Reload and try again.') from None


def public_resume_state(master: str, state: dict, description: str) -> dict:
    document = parse_editor_document(master)
    display = submitted_document(document, state['structure'])
    entries = []
    for category in COUNTS:
        for index, item in enumerate(state['structure'][category]):
            group = next(group for group in display.groups if group.entry_id == item['id'])
            entries.append({
                'id': f'{category}:{index}', 'category': category,
                'title': ' · '.join(state['values'][field.name] for field in group.fields
                                    if '_bullet_' not in field.name)[:180],
                'visible': f'{category}:{index}' in state['visible'][category],
                'bullets': [{'id': field.name, 'text': state['values'][field.name],
                             'visible': field.name in state.get('visible_points', {}).get(
                                 group.entry_id, [item.name for item in group.fields if '_bullet_' in item.name])}
                            for field in group.fields if '_bullet_' in field.name],
            })
    skills_text = ' '.join(value for key, value in state['values'].items() if key.startswith('skill_'))
    sections = _optional_section_spans(master)
    return {'entries': entries,
            'sections': [{'id': name, 'title': OPTIONAL_SECTIONS[name],
                          'visible': state.get('visible_sections', {}).get(name, True)}
                         for name in sections],
            'score': score_resume(description, entries, skills_text),
            'selection_method': state.get('selection_method', 'keyword')}


def score_resume(description: str, entries: list[dict], skills_text: str = '') -> dict:
    """Explain lexical coverage; this is guidance, not an employer ATS score."""
    job = description.lower()
    resume = (skills_text + ' ' + ' '.join(
        entry['title'] + ' ' + ' '.join(point['text'] for point in entry['bullets'] if point.get('visible', True))
        for entry in entries if entry['visible'])).lower()
    requested = [skill for skill in SKILLS if re.search(r'(?<!\w)' + re.escape(skill) + r'(?!\w)', job)]
    matched = [skill for skill in requested if re.search(r'(?<!\w)' + re.escape(skill) + r'(?!\w)', resume)]
    missing = [skill for skill in requested if skill not in matched]
    job_terms = sorted(_terms(description), key=lambda term: (-job.count(term), term))[:20]
    resume_terms = _terms(resume)
    keypoints = [term for term in job_terms if term in resume_terms]
    if not requested and not job_terms:
        return {'percent': None, 'matched_skills': [], 'missing_skills': [],
                'missing_keypoints': [],
                'feedback': 'Add a job description to see match feedback.'}
    skill_score = len(matched) / len(requested) if requested else None
    point_score = len(keypoints) / len(job_terms) if job_terms else None
    fractions = [fraction for fraction in (skill_score, point_score) if fraction is not None]
    percent = round(100 * sum(fractions) / len(fractions))
    feedback = (f'{len(matched)} of {len(requested)} named skills and '
                f'{len(keypoints)} of {len(job_terms)} key terms appear in visible blocks. '
                'Review missing terms and add only claims your experience supports.')
    return {'percent': percent, 'matched_skills': matched, 'missing_skills': missing,
            'missing_keypoints': [term for term in job_terms if term not in keypoints][:8],
            'feedback': feedback}


def draft_text(kind: str, application, profile) -> str:
    name = profile.full_name or '[Your name]'
    if kind == 'cover_letter':
        return (f'Dear Hiring Manager,\n\n'
                f'I am writing to apply for the {application.job.title} position at {application.job.company}. '
                '[Explain where you found the role and why it interests you.]\n\n'
                '[Connect one or two verified achievements from your resume to the job requirements.]\n\n'
                '[Thank the reader and briefly express interest in speaking further.]\n\n'
                f'Sincerely,\n{name}')
    template = (PROMPTS.parent / 'cold_email_template.txt').read_text(encoding='utf-8')
    return (template.replace('[Your name]', name)
            .replace('[I applied—confirm before sending]',
                     'I applied' if application.stage.value in
                     ('applied', 'interview', 'offer', 'rejected') else
                     '[I applied—confirm before sending]')
            .replace('[position]', application.job.title)
            .replace('[company]', application.job.company))


def cover_word_count(value: str) -> int:
    return len(re.findall(r"\b[\w]+(?:[-'][\w]+)*\b", value))


def render_cover_letter(text: str, company: str, master: str) -> str:
    """Fill the owned resume header and plain-text paragraphs into the TeX template."""
    if cover_word_count(text) > COVER_WORD_LIMIT:
        raise TailoringError(f'Keep the cover letter to {COVER_WORD_LIMIT} words or fewer.')
    document = parse_editor_document(master)
    header = master[document.header_start:document.header_end]
    paragraphs = [part.strip() for part in re.split(r'\n\s*\n', text.strip()) if part.strip()]
    body = '\n'.join('\\lettercontent{' + _escape(part.replace('\n', ' ')) + '}'
                     for part in paragraphs)
    template = (PROMPTS.parent / 'CoverLetterTemplate.tex').read_text(encoding='utf-8')
    return (template.replace('%%JOBPILOT_HEADER%%', header)
            .replace('%%JOBPILOT_COMPANY%%', _escape(company))
            .replace('%%JOBPILOT_DATE%%', _escape(date.today().strftime('%B %d, %Y')))
            .replace('%%JOBPILOT_BODY%%', body))


def ai_proposal(kind: str, description: str, current: str, instruction: str,
                *, point_id: str | None = None, mode: str = 'suggestion',
                source_facts: str = '', application_stage: str = '') -> str:
    if mode not in ('suggestion', 'inline'):
        raise TailoringError('Unknown AI editing mode.')
    filename = 'inline_edit.md' if mode == 'inline' else 'suggestion.md'
    prompt = (PROMPTS / kind / filename).read_text(encoding='utf-8')
    payload = {'job_description': description, 'current_text': current,
               'instruction': instruction, 'point_id': point_id,
               'resume_facts': source_facts[:12000],
               'application_stage': application_stage}
    try:
        result = complete_text([{'role': 'system', 'content': prompt},
                                {'role': 'user', 'content': json.dumps(payload)}], max_tokens=1600)
    except Exception:
        raise TailoringError('AI suggestions are unavailable. Check the model settings and retry.') from None
    return result[:12000]
