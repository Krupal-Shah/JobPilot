#!/usr/bin/env python3
"""API for the application tracker: list/create tracked jobs and move them
between pipeline stages. Powers the /dashboard page and is what Richard's
kanban board work can build on directly.

Every handler validates the request, calls exactly one service method,
and serializes the resulting DTO to JSON. No business logic here. All
routes require an authenticated session; users only ever see and modify
their own applications.
"""
from __future__ import annotations

import sqlite3

from flask import Blueprint, jsonify, request, session, url_for

from routes.validation import is_http_url, validate_json_body
from routes.auth import login_required
from services import SqliteApplicationService
from services.dto import Application, ApplicationStage, JobPosting
from services.profiles import SqliteProfileService
from services.job_documents import initial_resume_state, render_resume
from services.resumes import load_master_resume
from services.resume_tailoring import TailoringError, compress_latex
import json
from datetime import datetime
import re
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logging.basicConfig(level=logging.DEBUG)


def _parse_deadline_days(text: str) -> int | None:
    """Return days until deadline parsed from text, or None if not found.

    Supports forms like:
    - "apply by September 21, 2026" or "apply by September 21"
    - "apply by 2026-09-21"
    - "apply by 09/21/2026" or "apply by 9/21"
    - "in 3 days" or "3 days left"
    """
    if not text:
        return None
    now = datetime.utcnow().date()
    # Month name patterns
    m = re.search(r"(?:apply by|deadline[:\s]*)\s*(\b[A-Za-z]+ \d{1,2}(?:, \d{4})?)", text, re.I)
    if m:
        grp = m.group(1)
        for fmt in ("%B %d, %Y", "%B %d"):
            try:
                dl = datetime.strptime(grp, fmt)
                if fmt == "%B %d":
                    dl = dl.replace(year=now.year)
                return (dl.date() - now).days
            except Exception:
                continue

    # ISO date YYYY-MM-DD
    # Prefer ISO dates that appear near deadline-related keywords, otherwise fall back to any ISO date.
    m = re.search(r"(?:deadline|application deadline|apply by|apply before)[\s\S]{0,120}?(\d{4}-\d{2}-\d{2})", text, re.I)
    if not m:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        try:
            dl = datetime.strptime(m.group(1), "%Y-%m-%d")
            return (dl.date() - now).days
        except Exception:
            pass

    # Slashed dates MM/DD or MM/DD/YYYY
    # Slashed dates: prefer ones near deadline keywords first.
    m = re.search(r"(?:deadline|application deadline|apply by|apply before)[\s\S]{0,120}?(\d{1,2}/\d{1,2}(?:/\d{2,4})?)", text, re.I)
    if not m:
        m = re.search(r"(\d{1,2}/\d{1,2}(?:/\d{2,4})?)", text)
    if m:
        grp = m.group(1)
        for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m/%d"):
            try:
                dl = datetime.strptime(grp, fmt)
                if fmt == "%m/%d":
                    dl = dl.replace(year=now.year)
                return (dl.date() - now).days
            except Exception:
                continue

    # "in N days" or "N days left"
    m = re.search(r"(in\s+)?(\d{1,3})\s+days?", text, re.I)
    if m:
        try:
            n = int(m.group(2))
            return n
        except Exception:
            pass

    return None

applications_bp = Blueprint("applications", __name__, url_prefix="/api/applications")
_application_service = SqliteApplicationService()
_profile_service = SqliteProfileService()

_REQUIRED_CREATE_FIELDS = ("company", "title", "url")


def _serialize(application: Application, include_deadline_days: bool = False) -> dict:
    out = {
        "id": application.id,
        "company": application.job.company,
        "title": application.job.title,
        "url": application.job.url,
        "description": application.job.description,
        "location": application.job.location,
        "stage": application.stage.value,
        "resume_variant": application.resume_variant,
        "tailoring_error": application.tailoring_error,
        "cover_letter_saved": bool(application.cover_letter),
        "resume_pdf_url": url_for("extension.resume_pdf", url=application.job.url)
        if application.resume_variant else None,
        "notes": application.notes,
        "date_applied": application.date_applied.isoformat() if application.date_applied else None,
    }
    if include_deadline_days:
        out["deadline_days"] = _parse_deadline_days(application.job.description or "")
    return out



applications_bp.before_request(validate_json_body)


def _validate_fields(payload, required=()):
    for field in required:
        if field not in payload:
            return f"Missing required field: {field}"
    for field in ("company", "title", "url", "description", "location", "notes"):
        if field not in payload:
            continue
        value = payload[field]
        if field == "location" and value is None:
            continue
        if not isinstance(value, str):
            return f"{field} must be text"
        if field in _REQUIRED_CREATE_FIELDS and not value.strip():
            return f"{field} must not be blank"
        if field == "url" and not is_http_url(value):
            return "url must be an HTTP or HTTPS URL"
    return None


@applications_bp.get("")
@login_required
def list_applications():
    user_id = session["user_id"]
    stage_param = request.args.get("stage")
    try:
        stage = ApplicationStage(stage_param) if stage_param else None
    except ValueError:
        valid_stages = ", ".join(s.value for s in ApplicationStage)
        return jsonify({"error": f"stage must be one of: {valid_stages}"}), 400

    # Support server-side sorting via `sort` query param.
    sort_param = request.args.get("sort", "best")
    applications = _application_service.list_applications(user_id, stage=stage)

    # Only reorder bookmarked stage lists when requested.
    if stage == ApplicationStage.BOOKMARKED:
        profile = _profile_service.get_profile(user_id)
        def score_for(a: Application) -> float:
            s = 0.0
            title = (a.job.title or "").lower()
            desc = (a.job.description or "").lower()
            # title exact match
            for phrase in profile.desired_titles:
                if phrase and phrase.lower() in title:
                    s += 50
            # title token matches
            title_tokens = set(re.findall(r"\w+", title))
            for phrase in profile.desired_titles:
                for tok in re.findall(r"\w+", phrase.lower()):
                    if tok in title_tokens:
                        s += 30
                        break
            # skill matches
            for skill in profile.skills:
                if skill and (skill.lower() in title or skill.lower() in desc):
                    s += 20
            # location match: check job.location if present, otherwise fall back to description
            try:
                # Build a set of tokens found in the job text (location, title, description).
                def _tokens_from(text: str):
                    if not text:
                        return set()
                    # split on non-word characters and semicolons/commas
                    toks = re.split(r"[\W;,:]+", text.lower())
                    return set(t for t in toks if t)

                loc_tokens = _tokens_from(a.job.location or "")
                desc_tokens = _tokens_from(a.job.description or "")
                title_tokens = _tokens_from(a.job.title or "")
                all_tokens = loc_tokens | desc_tokens | title_tokens

                # canonical Canadian province abbreviations and names (lowercase)
                province_map = {
                    "ab": "alberta",
                    "bc": "british columbia",
                    "mb": "manitoba",
                    "nb": "new brunswick",
                    "nl": "newfoundland and labrador",
                    "ns": "nova scotia",
                    "nt": "northwest territories",
                    "nu": "nunavut",
                    "on": "ontario",
                    "pe": "prince edward island",
                    "qc": "quebec",
                    "sk": "saskatchewan",
                    "yt": "yukon",
                }

                def _normalize_prov(p: str):
                    if not p:
                        return p
                    p = p.lower().strip()
                    if p in province_map:
                        return province_map[p]
                    # map common abbreviations without dot
                    p2 = p.replace(".", "")
                    if p2 in province_map:
                        return province_map[p2]
                    return p

                city = (profile.city or "").lower().strip()
                prov = _normalize_prov(profile.province or "")

                in_city = city and any(city == t or city in t or t in city for t in all_tokens)
                in_prov = False
                if prov:
                    # check both normalized province name and raw tokens
                    prov_name = _normalize_prov(prov)
                    in_prov = prov_name in all_tokens or any(prov_name in t or t in prov_name for t in all_tokens)

                if in_city or in_prov:
                    s += 20
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug(
                            "location_match: title=%s, city=%s, prov=%s, tokens_sample=%s, in_city=%s, in_prov=%s",
                            a.job.title,
                            city,
                            prov,
                            list(all_tokens)[:10],
                            in_city,
                            in_prov,
                        )
            except Exception:
                pass
            # recency (days since created_at)
            try:
                created = a.created_at
                days = (datetime.utcnow() - created).days if created else 9999
                # linear scale: 0 days -> +30, 30+ days -> 0
                s += max(0, 30 * (1 - min(days, 30)/30))
            except Exception:
                pass
            # deadline urgency: look for "apply by <date>" patterns
            days_until = _parse_deadline_days(a.job.description or "")
            if days_until is not None:
                if days_until <= 3:
                    s += 60
                elif days_until <= 7:
                    s += 40
                elif days_until <= 14:
                    s += 20
            # Debug log the score components for visibility when debugging sorting.
            try:
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug("score_for: title=%s, score=%.1f, days_until=%s", a.job.title, s, days_until)
            except Exception:
                pass
            return s

        if sort_param == "title":
            applications.sort(key=lambda a: score_for(a), reverse=True)
        elif sort_param == "skill":
            # weight only skill matches
            def skill_score(a):
                c = 0
                title = (a.job.title or "").lower()
                desc = (a.job.description or "").lower()
                for skill in profile.skills:
                    if skill and (skill.lower() in title or skill.lower() in desc):
                        c += 1
                return c
            applications.sort(key=lambda a: skill_score(a), reverse=True)
        
        elif sort_param == "deadline":
            # jobs with soonest deadline first; no-deadline == far future
            def deadline_key(a):
                days = _parse_deadline_days(a.job.description or "")
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug("deadline_key: title=%s, days=%s", a.job.title, days)
                return days if days is not None else 999999

            applications.sort(key=deadline_key)
        else:
            applications.sort(key=lambda a: score_for(a), reverse=True)

    include_deadline = (stage == ApplicationStage.BOOKMARKED and sort_param in ("deadline", "best"))
    return jsonify([_serialize(a, include_deadline_days=include_deadline) for a in applications]), 200


@applications_bp.post("")
@login_required
def create_application():
    user_id = session["user_id"]
    payload = request.get_json(silent=True) or {}
    error = _validate_fields(payload, _REQUIRED_CREATE_FIELDS)
    if error:
        return jsonify({"error": error}), 400

    job = JobPosting(
        company=payload["company"],
        title=payload["title"],
        url=payload["url"],
        description=payload.get("description", ""),
        location=payload.get("location"),
    )
    application = _application_service.create_application(job, user_id)
    if not application.resume_variant:
        if application.job.description.strip():
            try:
                master = load_master_resume(user_id)
                state, _ = initial_resume_state(application.job.description, master)
                source = render_resume(master, state)
                try:
                    application = _application_service.save_resume_draft(
                        application.id, user_id, compress_latex(source),
                        compress_latex(master), json.dumps(state),
                        expected=(application.resume_variant, application.resume_state))
                except ValueError:
                    application = _application_service.get_application(application.id, user_id)
            except TailoringError as exc:
                _application_service.set_tailoring_error(application.id, user_id, str(exc))
                application = _application_service.get_application(application.id, user_id)
        else:
            _application_service.set_tailoring_error(
                application.id, user_id, 'Add the job description to tailor this resume.')
            application = _application_service.get_application(application.id, user_id)
    return jsonify(_serialize(application)), 201


@applications_bp.patch("/<int:application_id>/stage")
@login_required
def update_stage(application_id: int):
    user_id = session["user_id"]
    payload = request.get_json(silent=True) or {}
    stage_value = payload.get("stage")
    try:
        stage = ApplicationStage(stage_value)
    except ValueError:
        valid_stages = ", ".join(s.value for s in ApplicationStage)
        return jsonify({"error": f"stage must be one of: {valid_stages}"}), 400

    try:
        application = _application_service.update_stage(application_id, user_id, stage)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify(_serialize(application)), 200


@applications_bp.patch("/<int:application_id>")
@login_required
def edit_application(application_id: int):
    user_id = session["user_id"]
    payload = request.get_json(silent=True) or {}
    error = _validate_fields(payload)
    if error:
        return jsonify({"error": error}), 400
    try:
        application = _application_service.update_application(
            application_id,
            user_id,
            company=payload.get("company"),
            title=payload.get("title"),
            url=payload.get("url"),
            description=payload.get("description"),
            notes=payload.get("notes"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except sqlite3.IntegrityError:
        return jsonify({"error": "That posting URL is already tracked by another application."}), 400
    return jsonify(_serialize(application)), 200


@applications_bp.delete("/<int:application_id>")
@login_required
def delete_application(application_id: int):
    user_id = session["user_id"]
    if _application_service.get_application(application_id, user_id) is None:
        return jsonify({"error": "Application not found"}), 404
    _application_service.delete_application(application_id, user_id)
    return "", 204
