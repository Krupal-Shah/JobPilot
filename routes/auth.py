#!/usr/bin/env python3
"""Local account registration, login, and logout."""
from __future__ import annotations

from functools import wraps
from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

from services import SqliteUserService

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
_user_service = SqliteUserService()


def login_required(view):
    """Require an authenticated session.

    For page requests, redirect to the login page. For API requests
    (paths starting with /api/), return a JSON 401 instead.
    """

    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            session.pop("user_id", None)
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def current_user():
    """Return the logged-in User DTO, or None."""
    user_id = session.get("user_id")
    if user_id is None:
        return None
    return _user_service.get_user(user_id)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not name or not email or not password:
            flash("Name, email, and password are all required.", "error")
        else:
            try:
                user = _user_service.create_user(name, email, password)
            except ValueError as exc:
                flash(str(exc), "error")
            else:
                session.clear()
                session["user_id"] = user.userID
                return redirect(url_for("dashboard"))

    return render_template("register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        user = _user_service.verify_credentials(email, password)
        if user is None:
            flash("Invalid email or password.", "error")
        else:
            session.clear()
            session["user_id"] = user.userID
            next_url = request.args.get("next")
            return redirect(next_url if next_url and next_url.startswith("/")
                            and not next_url.startswith("//") and "\\" not in next_url
                            and not any(ord(c) < 32 for c in next_url)
                            else url_for("dashboard"))

    return render_template("login.html")


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
