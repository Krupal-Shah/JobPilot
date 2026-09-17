"""A signed-in user's junior software jobs from configured Greenhouse boards."""
from flask import Blueprint, render_template, request, session

from routes.auth import login_required
from services.greenhouse_jobs import (
    configured_boards, is_canadian_location, list_jobs, prioritize_jobs,
)
from services.profiles import SqliteProfileService

jobs_bp = Blueprint('jobs', __name__, url_prefix='/jobs')
_profiles = SqliteProfileService()


@jobs_bp.get('')
@login_required
def index():
    profile = _profiles.get_profile(session['user_id'])
    saved_titles = profile.desired_titles if isinstance(profile.desired_titles, list) else []
    desired_titles = [title.strip() for title in saved_titles
                      if isinstance(title, str) and title.strip()]
    try:
        boards = configured_boards()
        jobs, failed_boards = list_jobs(boards)
        error = None
    except ValueError as exc:
        boards, jobs, failed_boards, error = (), [], [], str(exc)
    canadian_jobs = [job for job in jobs if is_canadian_location(job.get('location', ''))]
    ranked = prioritize_jobs(canadian_jobs, desired_titles)
    query = request.args.get('q', '').strip()[:80]
    if query:
        needle = query.casefold()
        ranked = [job for job in ranked if needle in ' '.join(
            (job['title'], job['company'], job['location'])).casefold()]
    return render_template(
        'jobs.html', jobs=ranked, query=query, error=error,
        all_boards_failed=bool(boards) and len(failed_boards) == len(boards),
    )
