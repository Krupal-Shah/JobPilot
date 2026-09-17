#!/usr/bin/env python3
"""API for the dashboard's "Apply to all bookmarked" button.

Launches apply.py (ported from job-apply-bot) as a detached subprocess,
scoped to the signed-in user's own bookmarked jobs/profile/resume. It
opens its own visible Chrome window and never auto-submits a form without
a human clicking submit first -- this route only starts/stops the
process, all the actual browsing/filling happens there, not here.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from flask import Blueprint, jsonify, session

from routes.auth import login_required
from services.applications import SqliteApplicationService
from services.dto import ApplicationStage

automation_bp = Blueprint("automation", __name__, url_prefix="/api/automation")
_application_service = SqliteApplicationService()

ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = ROOT / "venv" / "bin" / "python"
APPLY_SCRIPT = ROOT / "apply.py"

# user_id -> subprocess.Popen. In-memory only (fine for this single-process
# hackathon app, same assumption db.py's own docstring already makes) --
# guards against double-launching a second browser for the same user
# while one is already running.
_running: dict[int, subprocess.Popen] = {}


@automation_bp.post("/apply-all")
@login_required
def apply_all():
    user_id = session["user_id"]

    existing = _running.get(user_id)
    if existing is not None and existing.poll() is None:
        return jsonify({"error": "Auto-apply is already running for your account."}), 409

    # Size the batch to however many jobs are actually bookmarked right now,
    # instead of apply.py's own --batch default (10) silently truncating a
    # larger board -- "apply to all bookmarked" should mean all of them.
    bookmarked_count = len(_application_service.list_applications(user_id, stage=ApplicationStage.BOOKMARKED))
    batch_size = max(bookmarked_count, 1)

    python_bin = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    process = subprocess.Popen(
        [python_bin, str(APPLY_SCRIPT), "--user-id", str(user_id), "--batch", str(batch_size)],
        cwd=str(ROOT),
        # Its own process group (not the Flask server's) so /stop can kill
        # apply.py AND the Chrome/chromedriver processes it spawns without
        # touching this server. Plain .terminate() only reaches the direct
        # child -- apply.py never exits on its own when a job needs review
        # (by design, to keep the browser open for you), so without this,
        # its Chrome instance keeps an exclusive lock on chrome_user_profile
        # forever and the next run silently fails to open a window at all.
        start_new_session=True,
    )
    _running[user_id] = process
    return jsonify({"ok": True, "pid": process.pid}), 200


@automation_bp.get("/status")
@login_required
def status():
    user_id = session["user_id"]
    process = _running.get(user_id)
    running = process is not None and process.poll() is None
    return jsonify({"running": running}), 200


@automation_bp.post("/stop")
@login_required
def stop_all():
    user_id = session["user_id"]
    process = _running.get(user_id)
    if process is None or process.poll() is not None:
        _running.pop(user_id, None)
        return jsonify({"error": "Auto-apply isn't running for your account."}), 409

    try:
        pgid = os.getpgid(process.pid)
        os.killpg(pgid, signal.SIGTERM)
        for _ in range(20):
            if process.poll() is not None:
                break
            time.sleep(0.25)
        else:
            os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    _running.pop(user_id, None)
    return jsonify({"ok": True}), 200
