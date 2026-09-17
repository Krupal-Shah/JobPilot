#!/usr/bin/env python3

import os
import secrets

from dotenv import load_dotenv
from flask import Flask, render_template, redirect, session, url_for

from api import register_api
from routes.auth import auth_bp, current_user, login_required
from routes.profile import profile_bp
from routes.jobs import jobs_bp
import db

load_dotenv()

db.init_db()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or "dev-secret-key-change-me"

register_api(app)
app.register_blueprint(auth_bp)
app.register_blueprint(profile_bp)
app.register_blueprint(jobs_bp)


@app.context_processor
def inject_current_user():
    """Make the logged-in user available to every template as `current_user`."""
    return {"current_user": current_user()}


@app.route('/')
def index():
    return redirect(url_for('dashboard' if current_user() else 'auth.login'))


@app.route('/dashboard')
@login_required
def dashboard():
    """Kanban application tracker. Data is fetched and rendered client-side
    (see static/js/dashboard.js) against the /api/applications API, since
    drag-and-drop between stage columns needs to update the DOM directly
    without a full page reload.
    """
    session.setdefault('documents_csrf', secrets.token_urlsafe(32))
    return render_template('dashboard.html')


if __name__ == '__main__':
    app.run(host="127.0.0.1", port=5050, debug=True)
