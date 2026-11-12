"""Serve the ILS frontend and backend through the same Railway HTTPS origin."""

from pathlib import Path

from flask import Flask, abort, send_from_directory
from invenio_app.wsgi import application as backend
from werkzeug.middleware.proxy_fix import ProxyFix

UI_ROOT = Path("/opt/invenio_app_ils/ui")
frontend = Flask("foslib_ui", static_folder=None)
frontend.config["TRUSTED_HOSTS"] = backend.config["TRUSTED_HOSTS"]


@frontend.route("/", defaults={"path": ""})
@frontend.route("/<path:path>")
def serve_frontend(path):
    """Serve bundled assets and let React handle navigation routes."""
    if path and (UI_ROOT / path).is_file():
        return send_from_directory(UI_ROOT, path)
    if path.startswith(("static/", "images/")) or Path(path).suffix:
        abort(404)
    response = send_from_directory(UI_ROOT, "index.html")
    response.headers["Cache-Control"] = "no-store"
    return response


@frontend.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


BACKEND_PATHS = ("/api", "/account", "/admin", "/oauth", "/ping", "/schemas", "/invenio-assets")


def route_application(environ, start_response):
    path = environ.get("PATH_INFO", "")
    if any(path == prefix or path.startswith(prefix + "/") for prefix in BACKEND_PATHS):
        return backend(environ, start_response)
    return frontend(environ, start_response)


# Trust the client address and HTTPS scheme supplied by Railway's edge proxy.
application = ProxyFix(route_application, x_for=1, x_proto=1, x_host=0)
