"""Signed-in account details and the master used for resume tailoring."""
import shutil
import secrets
import re
import hashlib
import json
from dataclasses import replace
from io import BytesIO

from flask import Blueprint, Response, flash, redirect, render_template, request, send_file, session, url_for

from routes.auth import login_required
from routes.validation import is_http_url
from services.profiles import SqliteProfileService
from services.resumes import get_master_pdf, load_master_resume, save_master_resume
from services.resume_import import MAX_MASTER_BYTES, profile_updates_from_latex
from services.resume_editor import (
    apply_structured_values, default_structure, parse_editor_document,
    submitted_document, validate_editor_values,
)
from services.resume_tailoring import TailoringError

profile_bp = Blueprint('profile', __name__, url_prefix='/profile')
_profile_service = SqliteProfileService()

_DETAIL_FIELDS = (
    ('First name', 'first_name'), ('Last name', 'last_name'),
    ('Email', 'email'), ('Phone', 'phone'), ('School', 'school'),
    ('Degree program', 'degree_program'), ('Expected graduation', 'expected_graduation'),
    ('Desired titles (comma-separated)',
     'desired_titles'), ('Skills (comma-separated)', 'skills'),
    ('Address', 'address_line_1'), ('City',
                                    'city'), ('Province / state', 'province'),
    ('Postal code', 'postal_code'), ('LinkedIn', 'linkedin'), ('GitHub', 'github'),
)


def _profile_values(profile):
    return {key: ', '.join(getattr(profile, key) or []) if key in {'skills', 'desired_titles'}
            else getattr(profile, key) for _, key in _DETAIL_FIELDS}


def _render_profile(profile, *, values=None, errors=None, upload_error=None, status=200):
    session.setdefault('profile_csrf_token', secrets.token_urlsafe(32))
    try:
        load_master_resume(session['user_id'])
        master_error = None
    except TailoringError as exc:
        master_error = str(exc)
    return render_template(
        'profile.html', fields=_DETAIL_FIELDS, values=values if values is not None else _profile_values(profile),
        errors=errors or {}, upload_error=upload_error, master_error=master_error,
        compiler_available=bool(shutil.which('pdflatex')),
    ), status


@profile_bp.after_request
def private_response(response):
    response.headers['Cache-Control'] = 'private, no-store'
    return response


@profile_bp.route('', methods=['GET', 'POST'])
@login_required
def index():
    profile = _profile_service.get_profile(session['user_id'])
    values = _profile_values(profile)
    errors = {}
    status = 200
    if request.method == 'POST':
        token = session.get('profile_csrf_token', '')
        if not token or not secrets.compare_digest(token.encode(), request.form.get('csrf_token', '').encode()):
            return Response('This form has expired. Reload your profile and try again.', status=400)
        parsed_lists = {}
        for label, key in _DETAIL_FIELDS:
            raw = request.form.get(key, '')
            value = raw.strip()
            if key in {'skills', 'desired_titles'}:
                items = [s.strip() for s in value.split(',') if s.strip()]
                values[key] = ', '.join(items)
                parsed_lists[key] = items
            else:
                values[key] = value
            max_length = 1000 if key in {'skills', 'desired_titles'} else 500
            if len(value) > max_length:
                errors[key] = f'{label} must be {max_length} characters or fewer.'
            elif key == 'email' and value and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
                errors[key] = 'Enter a valid email address.'
            elif key in {'linkedin', 'github'} and value and not is_http_url(value):
                errors[key] = 'Enter a complete HTTP or HTTPS URL.'
        if not errors:
            values['full_name'] = ' '.join(part for part in (
                values['first_name'], values['last_name']) if part)
            save_vals = dict(values)
            save_vals.update(parsed_lists)
            _profile_service.save_profile(
                session['user_id'], replace(profile, **save_vals))
            flash('Autofill details saved. Your refreshed profile is shown below, and the extension will use these values on its next fill.', 'success')
            return redirect(url_for('profile.index'))
        status = 400
    return _render_profile(profile, values=values, errors=errors, status=status)


@profile_bp.post('/resume/upload')
@login_required
def upload_resume():
    """Replace one account's master file and import recognizable profile details."""
    token = session.get('profile_csrf_token', '')
    if not token or not secrets.compare_digest(token, request.form.get('csrf_token', '')):
        return Response('This form has expired. Reload your profile and try again.', status=400)
    user_id = session['user_id']
    profile = _profile_service.get_profile(user_id)
    uploaded = request.files.get('master_resume')
    if uploaded is None or not uploaded.filename:
        return _render_profile(profile, upload_error='Choose a .tex resume to upload.', status=400)
    if not uploaded.filename.lower().endswith('.tex'):
        return _render_profile(profile, upload_error='Choose a .tex resume file.', status=400)
    content = uploaded.read(MAX_MASTER_BYTES + 1)
    if len(content) > MAX_MASTER_BYTES:
        return _render_profile(profile, upload_error='The .tex resume must be 1 MB or smaller.', status=413)
    try:
        source = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        return _render_profile(profile, upload_error='The .tex resume must use UTF-8 text.', status=400)
    try:
        updates = profile_updates_from_latex(source)
    except TailoringError as exc:
        return _render_profile(profile, upload_error=str(exc), status=422)

    previous_source = load_master_resume(user_id)
    try:
        save_master_resume(user_id, source)
        _profile_service.save_profile(user_id, replace(profile, **updates))
    except Exception:
        # Keep the previous master if the profile write fails after the resume update.
        save_master_resume(user_id, previous_source)
        raise
    flash('Master resume uploaded. Recognized details were added to your profile.', 'success')
    return redirect(url_for('profile.index'))


@profile_bp.route('/resume/edit', methods=['GET', 'POST'])
@login_required
def edit_resume():
    """Edit the signed-in user's master using fields parsed from its LaTeX."""
    user_id = session['user_id']
    session.setdefault('resume_csrf_token', secrets.token_urlsafe(32))
    try:
        source = load_master_resume(user_id)
        document = parse_editor_document(source)
    except TailoringError as exc:
        return render_template('resume_edit.html', groups=[], values={}, errors={},
                               editor_error=str(exc), compiler_available=False), 422

    values = {field.name: field.value for field in document.fields}
    structure = default_structure(document)
    display_document = document
    source_version = hashlib.sha256(source.encode('utf-8')).hexdigest()
    errors = {}
    if request.method == 'POST':
        token = request.form.get('csrf_token', '')
        if not secrets.compare_digest(session['resume_csrf_token'], token):
            return Response('This form has expired. Reload the editor and try again.', status=400)
        if request.form.get('source_version') != source_version:
            return Response('Your master resume changed in another tab. Reload the editor before saving.', status=409)
        try:
            structure = json.loads(request.form.get('structure', ''))
            display_document = submitted_document(document, structure)
        except (ValueError, TypeError, TailoringError) as exc:
            return Response(str(exc) or 'Reload the editor and try again.', status=400)
        values = {field.name: request.form.get(field.name, '').strip()
                  for field in display_document.fields}
        errors = validate_editor_values(display_document, values)
        if not errors:
            try:
                updated = apply_structured_values(source, document, display_document,
                                                  structure, values)
            except TailoringError as exc:
                return render_template('resume_edit.html', groups=display_document.groups,
                                       values=values, errors={}, editor_error=str(exc),
                                       compiler_available=bool(
                                           shutil.which('pdflatex')),
                                       source_version=source_version, structure=structure), 422
            save_master_resume(user_id, updated)
            flash('Master resume saved. The refreshed PDF preview and future tailored resumes use your edits.', 'success')
            return redirect(url_for('profile.index'))

    return render_template('resume_edit.html', groups=display_document.groups, values=values,
                           errors=errors, editor_error=None,
                           compiler_available=bool(shutil.which('pdflatex')),
                           source_version=source_version, structure=structure), (400 if errors else 200)


@profile_bp.get('/master-resume.tex')
@login_required
def master_source():
    try:
        source = load_master_resume(session['user_id'])
    except TailoringError as exc:
        return Response(str(exc), status=422, mimetype='text/plain')
    return send_file(BytesIO(source.encode('utf-8')), mimetype='text/plain',
                     as_attachment=True, download_name='master_resume.tex')


@profile_bp.get('/master-resume.pdf')
@login_required
def master_pdf():
    try:
        pdf = get_master_pdf(session['user_id'])
    except TailoringError as exc:
        return Response(str(exc), status=422, mimetype='text/plain')
    return send_file(BytesIO(pdf), mimetype='application/pdf', download_name='master_resume.pdf')
