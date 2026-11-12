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
    if backend.config.get("FOSLIB_MULTITENANT_ENABLED"):
        school_ui = Path(__file__).parent / "invenio_app_ils/multitenancy/web"
        if path.startswith("foslib-assets/"):
            return send_from_directory(school_ui, path.removeprefix("foslib-assets/"))
        if not path or not Path(path).suffix:
            response = send_from_directory(school_ui, "index.html")
            response.headers["Cache-Control"] = "no-store"
            return response
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


BACKEND_PATHS = (
    "/api",
    "/account",
    "/admin",
    "/oauth",
    "/ping",
    "/schemas",
    "/invenio-assets",
)


def route_application(environ, start_response):
    path = environ.get("PATH_INFO", "")
    if backend.config.get("FOSLIB_MAINTENANCE") and path != "/ping":
        start_response(
            "503 Service Unavailable",
            [
                ("Content-Type", "text/html; charset=utf-8"),
                ("Cache-Control", "no-store"),
                ("Retry-After", "120"),
            ],
        )
        return [
            b'<!doctype html><title>FOS Library Management System</title><main style="max-width:36rem;margin:15vh auto;font:18px system-ui;color:#0b5d66"><h1>FOS Library Management System</h1><p>Your library is being prepared. Please try again shortly.</p></main>'
        ]
    if backend.config.get("FOSLIB_MULTITENANT_ENABLED") and path in (
        "/login/",
        "/logout/",
    ):
        return backend(environ, start_response)
    if backend.config.get("FOSLIB_MULTITENANT_ENABLED") and (
        path == "/admin"
        or path.startswith("/admin/")
        or (
            path.startswith("/api/")
            and not path.startswith(("/api/foslib/", "/api/ping"))
        )
    ):
        # Legacy search, resolvers and exports contain operational aggregates
        # outside PostgreSQL RLS. They stay closed in tenant mode until adapted.
        start_response(
            "403 Forbidden",
            [("Content-Type", "application/json"), ("Cache-Control", "no-store")],
        )
        return [b'{"error":"Use the authorised school library API"}']
    if any(path == prefix or path.startswith(prefix + "/") for prefix in BACKEND_PATHS):
        return backend(environ, start_response)
    return frontend(environ, start_response)


# Trust the client address and HTTPS scheme supplied by Railway's edge proxy.
application = ProxyFix(route_application, x_for=1, x_proto=1, x_host=0)
