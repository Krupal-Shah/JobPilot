"""Owned, job-specific resume and outreach drafts for the dashboard."""
from __future__ import annotations

import hashlib
import json
import secrets
import zlib
import binascii
import shutil
from io import BytesIO
from pypdf import PdfReader

from flask import Blueprint, jsonify, request, send_file, session, url_for

from routes.auth import login_required
from routes.validation import validate_json_body
from services.applications import SqliteApplicationService
from services.job_documents import (
    COVER_WORD_LIMIT, ai_proposal, cover_word_count, draft_text, initial_resume_state,
    public_resume_state, render_cover_letter, render_resume,
    state_from_existing,
)
from services.profiles import SqliteProfileService
from services.resume_editor import parse_editor_document
from services.resume_tailoring import TailoringError, compress_latex, latex_to_pdf, decompress_latex
from services.resumes import load_master_resume

documents_bp = Blueprint('documents', __name__, url_prefix='/api/applications')
documents_bp.before_request(validate_json_body)
_applications = SqliteApplicationService()
_profiles = SqliteProfileService()


def _owned(application_id):
    return _applications.get_application(application_id, session['user_id'])


def _version(application):
    return hashlib.sha256(((application.resume_state or '') + (application.resume_variant or '')).encode()).hexdigest()


def _csrf_valid():
    expected = session.get('documents_csrf', '')
    supplied = request.headers.get('X-CSRF-Token', '')
    return bool(expected) and secrets.compare_digest(expected, supplied)


def _ensure_resume(application):
    if application.resume_state and application.resume_base and application.resume_variant:
        return application
    master = load_master_resume(session['user_id'])
    if application.resume_variant:
        state = state_from_existing(
            master, decompress_latex(application.resume_variant))
        return _applications.save_resume_metadata(application.id, session['user_id'],
                                                  compress_latex(master), json.dumps(state))
    state, _ = initial_resume_state(application.job.description, master)
    source = render_resume(master, state)
    try:
        return _applications.save_resume_draft(application.id, session['user_id'],
                                               compress_latex(
                                                   source), compress_latex(master),
                                               json.dumps(state), expected=(
                                                   application.resume_variant, application.resume_state))
    except ValueError:
        return _applications.get_application(application.id, session['user_id'])


@documents_bp.get('/<int:application_id>/documents/resume')
@login_required
def resume_draft(application_id):
    application = _owned(application_id)
    if application is None:
        return jsonify({'error': 'Application not found.'}), 404
    try:
        application = _ensure_resume(application)
        base = decompress_latex(application.resume_base)
        state = json.loads(application.resume_state)
        data = public_resume_state(base, state, application.job.description)
    except TailoringError as exc:
        return jsonify({'error': str(exc)}), 422
    except (ValueError, KeyError, TypeError, zlib.error, binascii.Error):
        return jsonify({'error': 'Could not read this resume draft. Check the master resume.'}), 422
    data.update({'state': state, 'version': _version(application),
                 'pdf_url': url_for('extension.resume_pdf', url=application.job.url),
                 'tailoring_error': application.tailoring_error})
    return jsonify(data)


@documents_bp.put('/<int:application_id>/documents/resume')
@login_required
def save_resume_draft(application_id):
    if not _csrf_valid():
        return jsonify({'error': 'Reload the dashboard and try again.'}), 400
    application = _owned(application_id)
    if application is None:
        return jsonify({'error': 'Application not found.'}), 404
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict) or payload.get('version') != _version(application):
        return jsonify({'error': 'This resume changed in another tab. Reload before saving.'}), 409
    state = payload.get('state')
    if not isinstance(state, dict) or not application.resume_base:
        return jsonify({'error': 'Open the resume draft before saving.'}), 400
    try:
        base = decompress_latex(application.resume_base)
        source = render_resume(base, state)
        data = public_resume_state(base, state, application.job.description)
        if shutil.which('pdflatex'):
            latex_to_pdf(source)
    except (TailoringError, ValueError, KeyError, TypeError, zlib.error, binascii.Error) as exc:
        return jsonify({'error': str(exc) or 'Invalid resume draft.'}), 422
    try:
        saved = _applications.save_resume_draft(application_id, session['user_id'],
                                                compress_latex(
                                                    source), application.resume_base,
                                                json.dumps(state), expected=(
                                                    application.resume_variant, application.resume_state))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 409
    return jsonify({'version': _version(saved), 'score': data['score']})


@documents_bp.post('/<int:application_id>/documents/resume/preview')
@login_required
def preview_resume_draft(application_id):
    """Compile an unsaved editor state without changing the job's stored resume."""
    if not _csrf_valid():
        return jsonify({'error': 'Reload the dashboard and try again.'}), 400
    application = _owned(application_id)
    if application is None:
        return jsonify({'error': 'Application not found.'}), 404
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict) or payload.get('version') != _version(application):
        return jsonify({'error': 'This resume changed in another tab. Reload before previewing.'}), 409
    if not isinstance(payload.get('state'), dict) or not application.resume_base:
        return jsonify({'error': 'Open the resume draft before previewing.'}), 400
    try:
        source = render_resume(decompress_latex(
            application.resume_base), payload['state'])
        pdf = latex_to_pdf(source)
    except (TailoringError, ValueError, KeyError, TypeError, zlib.error, binascii.Error) as exc:
        return jsonify({'error': str(exc) or 'Invalid resume draft.'}), 422
    response = send_file(BytesIO(pdf), mimetype='application/pdf', as_attachment=False,
                         download_name='resume_preview.pdf')
    response.headers['Cache-Control'] = 'private, no-store'
    return response


@documents_bp.post('/<int:application_id>/documents/resume/reset')
@login_required
def reset_resume_draft(application_id):
    if not _csrf_valid():
        return jsonify({'error': 'Reload the dashboard and try again.'}), 400
    application = _owned(application_id)
    if application is None:
        return jsonify({'error': 'Application not found.'}), 404
    try:
        master = load_master_resume(session['user_id'])
        state, _ = initial_resume_state(application.job.description, master)
        source = render_resume(master, state)
    except TailoringError as exc:
        return jsonify({'error': str(exc)}), 422
    _applications.save_resume_draft(application.id, session['user_id'],
                                    compress_latex(
                                        source), compress_latex(master),
                                    json.dumps(state))
    return resume_draft(application_id)


@documents_bp.get('/<int:application_id>/documents/cover-letter')
@login_required
def cover_letter_draft(application_id):
    application = _owned(application_id)
    if application is None:
        return jsonify({'error': 'Application not found.'}), 404
    return jsonify({'text': application.cover_letter or draft_text(
        'cover_letter', application, _profiles.get_profile(session['user_id'])),
        'saved': bool(application.cover_letter)})


@documents_bp.put('/<int:application_id>/documents/cover-letter')
@login_required
def save_cover_letter(application_id):
    if not _csrf_valid():
        return jsonify({'error': 'Reload the dashboard and try again.'}), 400
    if _owned(application_id) is None:
        return jsonify({'error': 'Application not found.'}), 404
    payload = request.get_json(silent=True) or {}
    text = payload.get('text') if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip() or len(text) > 15000 or '\x00' in text:
        return jsonify({'error': 'Enter a cover letter of 15,000 characters or fewer.'}), 400
    if cover_word_count(text) > COVER_WORD_LIMIT:
        return jsonify({'error': f'Keep the cover letter to {COVER_WORD_LIMIT} words or fewer.'}), 400
    _applications.save_cover_letter(
        application_id, session['user_id'], text.strip())
    return jsonify({'saved': True})


@documents_bp.post('/<int:application_id>/documents/cover-letter/preview')
@login_required
def preview_cover_letter(application_id):
    if not _csrf_valid():
        return jsonify({'error': 'Reload the dashboard and try again.'}), 400
    application = _owned(application_id)
    if application is None:
        return jsonify({'error': 'Application not found.'}), 404
    payload = request.get_json(silent=True) or {}
    text = payload.get('text') if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip() or len(text) > 15000 or '\x00' in text:
        return jsonify({'error': 'Enter a cover letter of 15,000 characters or fewer.'}), 400
    if cover_word_count(text) > COVER_WORD_LIMIT:
        return jsonify({'error': f'Keep the cover letter to {COVER_WORD_LIMIT} words or fewer.'}), 400
    try:
        master = load_master_resume(session['user_id'])
        pdf = latex_to_pdf(render_cover_letter(
            text.strip(), application.job.company, master))
        if len(PdfReader(BytesIO(pdf)).pages) != 1:
            raise TailoringError(
                'The cover letter exceeds one page. Shorten it before previewing.')
    except TailoringError as exc:
        return jsonify({'error': str(exc)}), 422
    response = send_file(BytesIO(pdf), mimetype='application/pdf', as_attachment=False,
                         download_name='cover_letter_preview.pdf')
    response.headers['Cache-Control'] = 'private, no-store'
    return response


@documents_bp.get('/<int:application_id>/documents/cover-letter.pdf')
@login_required
def cover_letter_pdf(application_id):
    application = _owned(application_id)
    if application is None or not application.cover_letter:
        return jsonify({'error': 'No cover letter is saved for this application.'}), 404
    try:
        master = load_master_resume(session['user_id'])
        source = render_cover_letter(
            application.cover_letter, application.job.company, master)
        pdf = latex_to_pdf(source)
        if len(PdfReader(BytesIO(pdf)).pages) != 1:
            raise TailoringError(
                'The cover letter exceeds one page. Shorten it before downloading.')
    except TailoringError as exc:
        return jsonify({'error': str(exc)}), 422
    response = send_file(BytesIO(pdf), mimetype='application/pdf', as_attachment=True,
                         download_name='cover_letter.pdf')
    response.headers['Cache-Control'] = 'private, no-store'
    return response


@documents_bp.get('/<int:application_id>/documents/cold-email')
@login_required
def cold_email_draft(application_id):
    application = _owned(application_id)
    if application is None:
        return jsonify({'error': 'Application not found.'}), 404
    return jsonify({'text': draft_text('cold_email', application,
                                       _profiles.get_profile(session['user_id']))})


@documents_bp.post('/<int:application_id>/documents/<kind>/suggest')
@login_required
def suggest(application_id, kind):
    if not _csrf_valid():
        return jsonify({'error': 'Reload the dashboard and try again.'}), 400
    kind = kind.replace('-', '_')
    if kind not in ('resume', 'cover_letter', 'cold_email'):
        return jsonify({'error': 'Unknown document type.'}), 404
    application = _owned(application_id)
    if application is None:
        return jsonify({'error': 'Application not found.'}), 404
    payload = request.get_json(silent=True) or {}
    current = payload.get('current') if isinstance(payload, dict) else None
    instruction = payload.get('instruction', '') if isinstance(
        payload, dict) else None
    point_id = payload.get('point_id') if isinstance(payload, dict) else None
    mode = payload.get('mode', 'suggestion') if isinstance(
        payload, dict) else None
    if (not isinstance(current, str) or len(current) > 12000 or
            not isinstance(instruction, str) or len(instruction) > 2000 or
            mode not in ('suggestion', 'inline') or
            point_id is not None and (not isinstance(point_id, str) or len(point_id) > 100)):
        return jsonify({'error': 'Enter a shorter draft or instruction.'}), 400
    try:
        master = load_master_resume(session['user_id'])
        source_facts = '\n'.join(f'{field.label}: {field.value}' for field in
                                 parse_editor_document(master).fields)
        proposal = ai_proposal(kind, application.job.description, current, instruction,
                               point_id=point_id, mode=mode, source_facts=source_facts,
                               application_stage=application.stage.value)
    except TailoringError as exc:
        return jsonify({'error': str(exc)}), 503
    return jsonify({'proposal': proposal})
