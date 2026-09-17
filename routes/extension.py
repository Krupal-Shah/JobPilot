#!/usr/bin/env python3
"""API the Chrome extension calls: capture the current job posting, get an
autofill plan for detected form fields, and log extension-side errors.

Kept separate from applications.py: this blueprint is the
extension's contract, applications.py is the dashboard's.
"""
from __future__ import annotations

import logging
import zlib
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file, session

from routes.validation import is_http_url, validate_json_body
from routes.auth import login_required
from services import (
    JobScraperService,
    LiteLLMAnswerService,
    ResumeTailoringService,
    RuleBasedAutomationService,
    SqliteApplicationService,
    SqliteProfileService,
)
from services.resume_tailoring import (
    TailoringError,
    decompress_latex,
    compress_latex,
    latex_to_pdf,
)
from services.automation import is_sensitive_category
from services.llm import configuration
from services.resumes import load_master_resume

extension_bp = Blueprint("extension", __name__, url_prefix="/api/extension")
_scraper_service = JobScraperService()
_ai_answer_service = LiteLLMAnswerService()
_resume_tailoring_service = ResumeTailoringService(_scraper_service)
_application_service = SqliteApplicationService()
_automation_service = RuleBasedAutomationService()
_profile_service = SqliteProfileService()
_logger = logging.getLogger("extension")

extension_bp.before_request(validate_json_body)


@extension_bp.post("/scrape")
def scrape_current_page():
    """Turn what the content script captured into a JobPosting.

    `text`/`title` are optional, more precise values the content script
    found using real DOM access (a specific description container, an
    actual heading element); when present they win over the blind
    regex/tag-strip fallback over `html`.
    """
    payload = request.get_json(silent=True) or {}
    html = payload.get("html")
    url = payload.get("url")
    if not isinstance(html, str) or not html.strip() or not is_http_url(url):
        return jsonify({"error": "html must be nonempty text and url must be an HTTP or HTTPS URL"}), 400

    if any(key in payload and not isinstance(payload[key], str) for key in ("text", "title")):
        return jsonify({"error": "text and title must be strings"}), 400

    job = _scraper_service.extract_job_posting(
        html, url, visible_text=payload.get("text"), title_override=payload.get("title")
    )
    return jsonify(
        {
            "company": job.company,
            "title": job.title,
            "url": job.url,
            "description": job.description,
            "location": job.location,
        }
    ), 200


@extension_bp.post("/tailor-resume")
@login_required
def tailor_current_resume():
    """Tailor and store a resume variant for a captured job posting."""
    payload = request.get_json(silent=True) or {}
    html = payload.get("html")
    url = payload.get("url")
    if not isinstance(html, str) or not html.strip() or not is_http_url(url):
        return jsonify({"error": "html must be nonempty text and url must be an HTTP or HTTPS URL"}), 400

    if any(key in payload and not isinstance(payload[key], str) for key in ("text", "title")):
        return jsonify({"error": "text and title must be strings"}), 400
    job = _scraper_service.extract_job_posting(
        html, url, visible_text=payload.get("text"), title_override=payload.get("title")
    )
    user_id = session["user_id"]
    application = _application_service.get_application_by_url(url, user_id)
    is_master = not configuration()[1]
    try:
        if application and application.resume_variant:
            pdf = latex_to_pdf(decompress_latex(application.resume_variant))
            variant = application.resume_variant
            is_master = False
        elif is_master:
            source = load_master_resume(session['user_id'])
            pdf = latex_to_pdf(source)
            variant = compress_latex(source)
        else:
            result = _resume_tailoring_service.tailor_resume(
                job.description, load_master_resume(session['user_id'])
            )
            pdf = result.pdf
            variant = result.compressed_latex
    except (TailoringError, ValueError, zlib.error) as exc:
        return jsonify({"error": str(exc)}), 422
    except OSError:
        return jsonify({"error": "Master resume template could not be read."}), 422

    if application is None:
        application = _application_service.create_application(job, user_id)
    if not application.resume_variant:
        application = _application_service.update_resume_variant(
            application.id, user_id, variant, only_if_missing=True
        )
        if application.resume_variant != variant:
            try:
                pdf = latex_to_pdf(decompress_latex(application.resume_variant))
            except (TailoringError, ValueError, zlib.error):
                return jsonify({"error": "Could not compile the saved resume."}), 422
            is_master = False
    if request.accept_mimetypes.best_match(['application/json', 'application/pdf']) == 'application/pdf':
        return _pdf_response(pdf, is_master=is_master)
    return jsonify({"application_id": application.id, "resume_variant": True, "resume_type": "master" if is_master else "tailored"}), 200


@extension_bp.post("/autofill-plan")
@login_required
def autofill_plan():
    """Given labels the content script detected, return filled/unmapped fields.

    `field_options` is an optional {label: [option, ...]} map for
    select/radio-group/checkbox-group fields; when the AI fallback (Tier
    2) answers one of those, the answer is guaranteed to be one of the
    options given, verbatim.

    Tier 1 (field_map/screening_answers) always runs first and is what
    "filled" means. Tier 2 (an LLM call) only runs for whatever Tier 1
    left unmapped, only when AGENT_NAME and AGENT_API_KEY are configured, and never
    for EEO/demographic or compliance/legal questions regardless -- those
    always stay unmapped for a human to answer.
    """
    payload = request.get_json(silent=True) or {}
    field_labels = payload.get("field_labels")
    field_options = payload.get("field_options", {})
    if not isinstance(field_labels, list) or any(not isinstance(label, str) for label in field_labels):
        return jsonify({"error": "field_labels must be a list of strings"}), 400

    if not isinstance(field_options, dict) or any(
        not isinstance(options, list) or any(not isinstance(option, str) for option in options)
        for options in field_options.values()
    ):
        return jsonify({"error": "field_options must map labels to lists of strings"}), 400

    user_id = session["user_id"]
    profile = _profile_service.get_profile(user_id)
    result = _automation_service.build_autofill_plan(field_labels, profile)

    ai_suggested = []
    still_unmapped = []
    has_profile_details = any((
        profile.first_name, profile.last_name, profile.email, profile.phone,
        profile.school, profile.linkedin, profile.github, profile.address_line_1,
        profile.city, profile.province, profile.postal_code, profile.degree_program,
        profile.expected_graduation, profile.desired_titles, profile.skills,
    ))
    if has_profile_details and _ai_answer_service.is_available():
        try:
            resume_text = load_master_resume(user_id).split(r"\begin{document}", 1)[-1]
        except TailoringError:
            resume_text = ""
        for unmapped_field in result.unmapped:
            if is_sensitive_category(unmapped_field.label):
                still_unmapped.append(unmapped_field)
                continue
            options = field_options.get(unmapped_field.label)
            answer = _ai_answer_service.suggest_answer(
                unmapped_field.label, options, profile, resume_text)
            if answer:
                ai_suggested.append(
                    {"label": unmapped_field.label, "value": answer})
            else:
                still_unmapped.append(unmapped_field)
    else:
        still_unmapped = result.unmapped

    return jsonify(
        {
            "filled": [{"label": f.label, "value": f.value} for f in result.filled],
            "ai_suggested": ai_suggested,
            "unmapped": [f.label for f in still_unmapped],
        }
    ), 200


@extension_bp.get("/resume")
@login_required
def resume_pdf():
    """Serve only the signed-in user's tailored resume for the exact posting."""
    url = request.args.get("url")
    if not is_http_url(url):
        return jsonify({"error": "A job posting URL is required."}), 400
    application = _application_service.get_application_by_url(url, session["user_id"])
    if application is None or not application.resume_variant:
        return jsonify({"error": "No tailored resume is saved for this posting. Run Autofill to create one."}), 404
    try:
        pdf = latex_to_pdf(decompress_latex(application.resume_variant))
    except (TailoringError, ValueError, OSError, zlib.error):
        return jsonify({"error": "Could not compile the saved resume. Check the LaTeX compiler and saved source."}), 422
    return _pdf_response(pdf)


def _pdf_response(pdf, *, is_master=False):
    response = send_file(BytesIO(pdf), mimetype="application/pdf", download_name="master_resume.pdf" if is_master else "tailored_resume.pdf")
    response.headers["X-Resume-Type"] = "master" if is_master else "tailored"
    response.headers["Cache-Control"] = "private, no-store"
    return response


@extension_bp.post("/log")
def log_extension_error():
    """Best-effort error relay so extension bugs show up in a server log."""
    payload = request.get_json(silent=True) or {}
    _logger.warning(
        "extension error: source=%s message=%s url=%s",
        payload.get("source"),
        payload.get("message"),
        payload.get("url"),
    )
    return jsonify({"ok": True}), 200
