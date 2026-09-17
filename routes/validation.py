"""Request checks shared by JSON blueprints."""
from urllib.parse import urlsplit

from flask import jsonify, request


def validate_json_body():
    if request.method in {"POST", "PATCH"} and not isinstance(request.get_json(silent=True), dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400


def is_http_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        return False
