"""Editable text fields for the bundled LaTeX resume structure.

Only text inside known template blocks is editable. Unchanged source spans,
macros, section order, and formatting are preserved verbatim.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .resume_tailoring import COUNTS, TailoringError, _masked_comments, limit_coursework, parse_blocks

CATEGORIES = {
    'experience': ('Experience', 'resumeSubheading', ('Role', 'Dates', 'Organization', 'Location')),
    'projects': ('Projects', 'resumeProjectHeading', ('Project and technologies', 'Dates')),
    'leadership': ('Leadership & activities', 'resumeSubheading', ('Role', 'Dates', 'Organization', 'Location')),
}
MAX_ENTRIES = 20
MAX_BULLETS = 20


@dataclass(frozen=True)
class EditorField:
    name: str
    label: str
    value: str
    start: int = -1
    end: int = -1
    multiline: bool = False
    input_type: str = 'text'
    max_length: int = 1000


@dataclass(frozen=True)
class EditorGroup:
    title: str
    fields: list[EditorField]
    category: str = ''
    entry_id: str = ''


@dataclass(frozen=True)
class EditorDocument:
    groups: list[EditorGroup]
    header_start: int
    header_end: int

    @property
    def fields(self) -> list[EditorField]:
        return [field for group in self.groups for field in group.fields]


_ESCAPED = {
    '\\': r'\textbackslash{}', '{': r'\{', '}': r'\}', '$': r'\$',
    '&': r'\&', '#': r'\#', '%': r'\%', '_': r'\_',
    '~': r'\textasciitilde{}', '^': r'\textasciicircum{}',
}
_UNESCAPED = {'&': '&', '#': '#', '%': '%', '_': '_', '$': '$', '{': '{', '}': '}', '@': '@'}


def _escape(value: str) -> str:
    return ''.join(_ESCAPED.get(char, char) for char in value)


def _plain(value: str) -> str:
    for macro, char in ((r'\textbackslash{}', '\\'), (r'\textasciitilde{}', '~'),
                        (r'\textasciicircum{}', '^')):
        value = value.replace(macro, char)
    previous = None
    while previous != value:
        previous = value
        value = re.sub(r'\\(?:textbf|emph|underline)\{([^{}]*)\}', r'\1', value)
    value = value.replace('$|$', ' | ')
    value = re.sub(r'\\([&#%_${}@])', lambda m: _UNESCAPED[m[1]], value)
    return re.sub(r'[ \t]+', ' ', value).strip()


def _braced(latex: str, opening: int) -> tuple[int, int, int]:
    if opening >= len(latex) or latex[opening] != '{':
        raise TailoringError('The master resume contains an unsupported block.')
    depth = 1
    position = opening + 1
    while position < len(latex):
        char = latex[position]
        if char == '\\':
            position += 2
            continue
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return opening + 1, position, position + 1
        position += 1
    raise TailoringError('The master resume contains an unclosed block.')


def _macro_args(latex: str, start: int, macro: str, count: int) -> list[tuple[int, int]]:
    position = start + len(macro) + 1
    args = []
    for _ in range(count):
        while position < len(latex) and latex[position].isspace():
            position += 1
        a, b, position = _braced(latex, position)
        args.append((a, b))
    return args


def _section_regions(latex: str) -> dict[str, tuple[int, int]]:
    source = _masked_comments(latex)
    body = source.find(r'\begin{document}')
    if body < 0:
        raise TailoringError('Expected one complete LaTeX document.')
    sections = list(re.finditer(r'\\section\{([^{}]+)\}', source[body:]))
    regions = {}
    for index, section in enumerate(sections):
        title = section[1].strip().lower()
        start = body + section.end()
        end = body + sections[index + 1].start() if index + 1 < len(sections) else source.index(r'\end{document}')
        regions[title] = (start, end)
    return regions


def _header(latex: str) -> tuple[EditorGroup, int, int]:
    source = _masked_comments(latex)
    body = source.find(r'\begin{document}')
    match = re.search(r'\\begin\{center\}(.*?)\\end\{center\}', source[body:], re.DOTALL)
    if not match:
        raise TailoringError('Expected a contact header in the master resume.')
    start, end = body + match.start(), body + match.end()
    content = latex[start:end]
    name = re.search(r'\\textbf\{\\(?:Huge\s+\\scshape|Large)\s+', content)
    phone = re.search(r'\\small\s+(.+?)\s+\$\|\$', content, re.DOTALL)
    links = []
    for marker in re.finditer(r'\\href(?=\s*\{)', content):
        args = _macro_args(content, marker.start(), 'href', 2)
        links.append(tuple(_plain(content[a:b]) for a, b in args))
    if not all((name, phone)) or len(links) < 3 or not links[0][0].startswith('mailto:') or not all(
        link[0].startswith(('http://', 'https://')) for link in links[1:]
    ):
        raise TailoringError('The contact header does not match the editable template.')
    name_end = _braced(content, content.index('{', name.start()))[1]
    email_display = links[0][1] or links[0][0][7:]
    fields = [
        EditorField('contact_name', 'Name', _plain(content[name.end():name_end]), max_length=200),
        EditorField('contact_phone', 'Phone', _plain(phone[1]), max_length=100),
        EditorField('contact_email', 'Email', email_display, input_type='email', max_length=200),
        EditorField('contact_linkedin', 'LinkedIn URL', links[1][0], input_type='url', max_length=500),
        EditorField('contact_github', 'GitHub URL', links[2][0], input_type='url', max_length=500),
    ]
    if len(links) > 3:
        fields.append(EditorField('contact_portfolio', 'Portfolio URL', links[3][0],
                                  input_type='url', max_length=500))
    return EditorGroup('Contact', fields), start, end


def _entry_fields(latex: str, start: int, end: int, name: str, heading: str,
                  labels: tuple[str, ...]) -> list[EditorField]:
    region = _masked_comments(latex[start:end])
    marker = re.search(r'\\' + heading + r'\b', region)
    if marker is None:
        raise TailoringError(f'Expected {heading} in the master resume.')
    args = _macro_args(latex, start + marker.start(), heading, len(labels))
    fields = [EditorField(f'{name}_{index}', label, _plain(latex[a:b]), a, b,
                          max_length=500) for index, (label, (a, b)) in enumerate(zip(labels, args))]
    bullets = list(re.finditer(r'\\resumeItem(?=\s*\{)', region))
    for index, marker in enumerate(bullets, 1):
        (a, b), = _macro_args(latex, start + marker.start(), 'resumeItem', 1)
        fields.append(EditorField(f'{name}_bullet_{index}', f'Bullet {index}',
                                  _plain(latex[a:b]), a, b, multiline=True))
    return fields


def parse_editor_document(latex: str) -> EditorDocument:
    blocks = parse_blocks(latex)
    regions = _section_regions(latex)
    header, header_start, header_end = _header(latex)
    groups = [header]

    education = regions.get('education')
    if education is not None:
        education_fields = _entry_fields(latex, *education, 'education', 'resumeSubheading',
                                         ('School', 'Location', 'Degree', 'Graduation'))
        region = _masked_comments(latex[education[0]:education[1]])
        for marker in re.finditer(r'\\resumeItem(?=\s*\{)', region):
            (a, b), = _macro_args(latex, education[0] + marker.start(), 'resumeItem', 1)
            prefix = re.match(r'\s*\\textbf\{Relevant Coursework:?\}:?\s*', latex[a:b])
            if prefix:
                education_fields = [field for field in education_fields
                                    if not field.name.startswith('education_bullet_')]
                education_fields.append(EditorField('education_coursework',
                    'Relevant coursework (comma-separated)', _plain(latex[a + prefix.end():b]),
                    a + prefix.end(), b, multiline=True))
                break
        groups.append(EditorGroup('Education', education_fields))

    for category, (title, heading, labels) in CATEGORIES.items():
        for index, block in enumerate(blocks[category], 1):
            entry_id = f'{category}_{index}'
            fields = _entry_fields(latex, block.start, block.end, entry_id, heading, labels)
            groups.append(EditorGroup(f'{title} {index}', fields, category, entry_id))

    # Keep non-tailored sections editable too. Their source spans remain in place.
    handled = {'education', 'technical skills', 'experience', 'work experience',
               'projects', 'technical projects', 'leadership', 'leadership experiences',
               r'leadership \& activities', 'extra-curricular activities'}
    for title, (start, end) in regions.items():
        if title in handled:
            continue
        fields = []
        region = _masked_comments(latex[start:end])
        for index, marker in enumerate(re.finditer(r'\\resumePublicationItem\b|\\resumeProjectHeading\b|\\resumeItem(?=\s*\{)', region), 1):
            macro = marker[0][1:]
            count = {'resumePublicationItem': 4, 'resumeProjectHeading': 2,
                     'resumeItem': 1}[macro]
            for number, (a, b) in enumerate(_macro_args(latex, start + marker.start(), macro, count), 1):
                fields.append(EditorField(f'extra_{title.replace(" ", "_")}_{index}_{number}',
                    f'{macro.replace("resume", "")} {index} · {number}', _plain(latex[a:b]),
                    a, b, multiline=macro == 'resumeItem'))
        if title == 'profile summary' and not fields:
            match = re.search(r'\\item\s+([^\n]+)', region)
            if match:
                a, b = start + match.start(1), start + match.end(1)
                fields.append(EditorField('profile_summary', 'Summary', _plain(latex[a:b]),
                                          a, b, multiline=True))
        if fields:
            groups.append(EditorGroup(title.title(), fields))
    skills = regions.get('technical skills')
    if skills is not None:
        region = _masked_comments(latex[skills[0]:skills[1]])
        rows = list(re.finditer(r'\\textbf\{([^{}]+)\}\s*&\s*(.*?)\s*\\\\(?=\s|$)', region, re.DOTALL))
        skill_fields = []
        if rows:
            for index, row in enumerate(rows, 1):
                a, b = skills[0] + row.start(2), skills[0] + row.end(2)
                skill_fields.append(EditorField(f'skill_{index}', _plain(row[1]).rstrip(':'),
                                                _plain(latex[a:b]), a, b, multiline=True))
        else:
            for index, marker in enumerate(re.finditer(r'\\textbf\{([^{}]+)\}\s*\{', region), 1):
                a, b, _ = _braced(latex, skills[0] + marker.end() - 1)
                prefix = re.match(r'\s*:\s*', latex[a:b])
                if prefix:
                    a += prefix.end()
                skill_fields.append(EditorField(f'skill_{index}', _plain(marker[1]).rstrip(':'),
                                                _plain(latex[a:b]), a, b,
                                                multiline=True))
        if skill_fields:
            groups.append(EditorGroup('Technical skills', skill_fields))
    return EditorDocument(groups, header_start, header_end)


def _render_header(values: dict[str, str]) -> str:
    def value(name):
        return _escape(values[name])

    def link(name):
        url = value(name)
        display = _escape(re.sub(r'^https?://', '', values[name]).rstrip('/'))
        return f'\\href{{{url}}}{{\\underline{{{display}}}}}'

    portfolio = f' $|$\n    {link("contact_portfolio")}' if values.get('contact_portfolio') else ''
    return (
        '\\begin{center}\n'
        f'    \\textbf{{\\Huge \\scshape {value("contact_name")}}} \\\\ \\vspace{{1pt}}\n'
        f'    \\small {value("contact_phone")} $|$ '
        f'\\href{{mailto:{value("contact_email")}}}{{\\underline{{{value("contact_email")}}}}} $|$\n'
        f'    {link("contact_linkedin")} $|$\n'
        f'    {link("contact_github")}{portfolio}\n'
        '\\end{center}'
    )


def validate_editor_values(document: EditorDocument, values: dict[str, str], *, allow_blank: bool = False) -> dict[str, str]:
    errors = {}
    for field in document.fields:
        value = values.get(field.name)
        if not isinstance(value, str) or (not value.strip() and not allow_blank):
            errors[field.name] = 'Enter a value.'
        elif len(value) > field.max_length:
            errors[field.name] = f'Use {field.max_length} characters or fewer.'
    email = values.get('contact_email', '')
    if email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        errors['contact_email'] = 'Enter a valid email address.'
    for name in ('contact_linkedin', 'contact_github', 'contact_portfolio'):
        url = values.get(name, '')
        try:
            parsed = urlsplit(url)
            valid = parsed.scheme in ('http', 'https') and bool(parsed.hostname)
        except ValueError:
            valid = False
        if url and (not valid or re.search(r'[\s\\{}%#]', url)):
            errors[name] = 'Enter a complete HTTP or HTTPS URL without spaces or special characters.'
    return errors


def apply_editor_values(latex: str, document: EditorDocument, values: dict[str, str]) -> str:
    errors = validate_editor_values(document, values)
    if errors:
        raise TailoringError('Correct the highlighted resume fields before saving.')
    replacements = []
    header_fields = document.groups[0].fields
    if any(values[field.name] != field.value for field in header_fields):
        replacements.append((document.header_start, document.header_end, _render_header(values)))
    for field in document.fields:
        if field.start >= 0 and values[field.name] != field.value:
            replacements.append((field.start, field.end, _escape(values[field.name])))
    updated = latex
    for start, end, replacement in sorted(replacements, reverse=True):
        updated = updated[:start] + replacement + updated[end:]
    parse_blocks(updated)
    limit_coursework(updated)
    return updated


def default_structure(document: EditorDocument) -> dict:
    """Describe entry and bullet order without putting LaTeX in the browser."""
    return {
        category: [
            {'id': group.entry_id, 'bullets': [field.name for field in group.fields
             if '_bullet_' in field.name]}
            for group in document.groups if group.category == category
        ] for category in CATEGORIES
    }


def submitted_document(document: EditorDocument, structure: dict) -> EditorDocument:
    """Accept only original IDs or well-formed new IDs from the editor form."""
    if not isinstance(structure, dict) or set(structure) != set(CATEGORIES):
        raise TailoringError('Reload the editor and try again.')
    originals = {group.entry_id: group for group in document.groups if group.entry_id}
    groups = [group for group in document.groups if not group.category]
    all_seen = set()
    for category, (title, _, labels) in CATEGORIES.items():
        entries = structure[category]
        minimum = min(COUNTS[category], sum(group.category == category for group in document.groups))
        if not isinstance(entries, list) or not minimum <= len(entries) <= MAX_ENTRIES:
            raise TailoringError(f'Keep between {minimum} and {MAX_ENTRIES} {title.lower()} entries.')
        for number, entry in enumerate(entries, 1):
            if not isinstance(entry, dict) or set(entry) != {'id', 'bullets'}:
                raise TailoringError('Reload the editor and try again.')
            entry_id, bullets = entry['id'], entry['bullets']
            if not isinstance(entry_id, str) or not re.fullmatch(r'(?:experience|projects|leadership)_(?:[1-9][0-9]*|new_[1-9][0-9]*)', entry_id) or entry_id in all_seen:
                raise TailoringError('Resume entries must have distinct valid IDs.')
            all_seen.add(entry_id)
            original = originals.get(entry_id)
            source_category = original.category if original else entry_id.split('_', 1)[0]
            if (source_category == 'projects') != (category == 'projects'):
                raise TailoringError('Projects can only be placed in the Projects section.')
            if original is None and not re.fullmatch(r'(?:experience|projects|leadership)_new_[1-9][0-9]*', entry_id):
                raise TailoringError('Reload the editor and try again.')
            heading = ([field for field in original.fields if '_bullet_' not in field.name]
                       if original else [EditorField(f'{entry_id}_{i}', label, '', max_length=500)
                                         for i, label in enumerate(labels)])
            if not isinstance(bullets, list) or not 1 <= len(bullets) <= MAX_BULLETS or len(set(map(str, bullets))) != len(bullets):
                raise TailoringError(f'Keep between 1 and {MAX_BULLETS} points per entry.')
            original_bullets = {field.name: field for field in original.fields if '_bullet_' in field.name} if original else {}
            fields = list(heading)
            for bullet_number, bullet_id in enumerate(bullets, 1):
                if not isinstance(bullet_id, str) or (bullet_id not in original_bullets and
                        not re.fullmatch(rf'{re.escape(entry_id)}_bullet_new_[1-9][0-9]*', bullet_id)):
                    raise TailoringError('Reload the editor and try again.')
                original_field = original_bullets.get(bullet_id)
                fields.append(EditorField(bullet_id, f'Bullet {bullet_number}',
                                          original_field.value if original_field else '',
                                          original_field.start if original_field else -1,
                                          original_field.end if original_field else -1,
                                          multiline=True))
            groups.append(EditorGroup(f'{title} {number}', fields, category, entry_id))
    # Keep the same section order as the source, including Technical skills last.
    skills = next((group for group in groups if group.title == 'Technical skills'), None)
    if skills:
        groups.remove(skills)
        groups.append(skills)
    return EditorDocument(groups, document.header_start, document.header_end)


def apply_structured_values(latex: str, original: EditorDocument,
                            submitted: EditorDocument, structure: dict,
                            values: dict[str, str]) -> str:
    if validate_editor_values(submitted, values):
        raise TailoringError('Correct the highlighted resume fields before saving.')
    if structure == default_structure(original):
        return apply_editor_values(latex, original, values)
    blocks = parse_blocks(latex)
    original_groups = {group.entry_id: group for group in original.groups if group.entry_id}
    replacements = []
    if any(values[field.name] != field.value for field in original.groups[0].fields):
        replacements.append((original.header_start, original.header_end, _render_header(values)))
    for group in original.groups:
        if group.category:
            continue
        for field in group.fields:
            if field.start >= 0 and values[field.name] != field.value:
                replacements.append((field.start, field.end, _escape(values[field.name])))

    for category, (_, heading, labels) in CATEGORIES.items():
        entries = structure[category]
        rendered = []
        for entry in entries:
            entry_id = entry['id']
            group = next(group for group in submitted.groups if group.entry_id == entry_id)
            original_group = original_groups.get(entry_id)
            source_category = original_group.category if original_group else category
            bullet_commands = []
            original_fields = {field.name: field for field in original_group.fields} if original_group else {}
            for bullet_id in entry['bullets']:
                field = original_fields.get(bullet_id)
                if field:
                    command_start = latex.rfind(r'\resumeItem', blocks[source_category][int(entry_id.rsplit('_', 1)[1]) - 1].start, field.start)
                    if command_start < 0:
                        raise TailoringError('The master resume contains an unsupported point.')
                    command = latex[command_start:field.start] + (
                        latex[field.start:field.end] if values[bullet_id] == field.value else _escape(values[bullet_id])
                    ) + latex[field.end:field.end + 1]
                    bullet_commands.append(command)
                else:
                    bullet_commands.append(r'\resumeItem{' + _escape(values[bullet_id]) + '}')
            bullet_body = '\n' + '\n'.join('          ' + command for command in bullet_commands) + '\n      '
            if original_group:
                block = blocks[source_category][int(entry_id.rsplit('_', 1)[1]) - 1]
                source = block.latex
                list_start = source.index(r'\resumeItemListStart') + len(r'\resumeItemListStart')
                list_end = source.index(r'\resumeItemListEnd')
                local = [(list_start, list_end, bullet_body)]
                for field in original_group.fields:
                    if '_bullet_' not in field.name and values[field.name] != field.value:
                        local.append((field.start - block.start, field.end - block.start,
                                      _escape(values[field.name])))
                for start, end, text in sorted(local, reverse=True):
                    source = source[:start] + text + source[end:]
                rendered.append(source)
            else:
                args = [r'{' + _escape(values[f'{entry_id}_{i}']) + '}' for i in range(len(labels))]
                if category == 'projects':
                    args[0] = r'{\textbf{' + _escape(values[f'{entry_id}_0']) + '}}'
                rendered.append('\\' + heading + '\n      ' + ''.join(args[:2]) +
                                ('\n      ' + ''.join(args[2:]) if len(args) > 2 else '') +
                                '\n      \\resumeItemListStart' + bullet_body + r'\resumeItemListEnd')
        category_blocks = blocks[category]
        if category_blocks:
            replacements.append((category_blocks[0].start, category_blocks[-1].end,
                                 '\n\n    '.join(rendered)))
        elif rendered:
            raise TailoringError(f'Add a {title} section to the master resume before adding entries.')

    updated = latex
    for start, end, text in sorted(replacements, reverse=True):
        updated = updated[:start] + text + updated[end:]
    parse_blocks(updated)
    limit_coursework(updated)
    return updated
