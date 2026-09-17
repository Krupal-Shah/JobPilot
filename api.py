"""Entry point for registering the application's JSON APIs.

Feature handlers live in routes/; keep Flask application setup in app.py and
business logic in services/. Register all API blueprints here so new features
have one integration point.
"""
from routes.applications import applications_bp
from routes.automation import automation_bp
from routes.extension import extension_bp
from routes.documents import documents_bp

def register_api(app):
    """Register JSON routes, preserving their URL prefixes and endpoint names."""
    for blueprint in (applications_bp, automation_bp, extension_bp, documents_bp):
        app.register_blueprint(blueprint)
