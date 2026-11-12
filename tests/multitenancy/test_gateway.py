# SPDX-License-Identifier: MIT
"""Legacy search/export endpoints fail closed in the tenant deployment."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest
from flask import Flask
from werkzeug.test import Client
from werkzeug.wrappers import Response


@pytest.fixture
def gateway(monkeypatch):
    backend = Flask("gateway-stub")
    backend.config.update(TRUSTED_HOSTS=["localhost"], FOSLIB_MULTITENANT_ENABLED=True)

    @backend.route("/", defaults={"path": ""}, methods=["GET", "POST"])
    @backend.route("/<path:path>", methods=["GET", "POST"])
    def handler(path):
        return "backend reached"

    module = types.ModuleType("invenio_app.wsgi")
    module.application = backend
    monkeypatch.setitem(sys.modules, "invenio_app.wsgi", module)
    path = Path(__file__).parents[2] / "railway_wsgi.py"
    spec = importlib.util.spec_from_file_location("foslib_gateway_test", path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return Client(loaded.application, Response), backend


@pytest.mark.parametrize(
    "path",
    [
        "/api/items",
        "/api/patrons",
        "/api/loans",
        "/api/documents",
        "/api/resolver/documents/shared-book/circulation",
        "/api/stats",
        "/admin/",
    ],
)
def test_legacy_search_and_operational_routes_are_closed(gateway, path):
    c, _ = gateway
    assert c.get(path).status_code == 403


def test_school_assets_and_existing_authentication_are_reachable(gateway):
    c, _ = gateway
    assert b"FOS Library Management System" in c.get("/").data
    assert c.get("/foslib-assets/app.js").status_code == 200
    assert c.get("/login/").data == b"backend reached"
    assert c.post("/login/").data == b"backend reached"
    assert c.get("/logout/").data == b"backend reached"
    assert c.get("/api/foslib/me").data == b"backend reached"


def test_maintenance_prevents_access_during_ownership_rollout(gateway):
    c, backend = gateway
    backend.config["FOSLIB_MAINTENANCE"] = True
    assert c.get("/").status_code == 503
    assert c.get("/api/foslib/me").status_code == 503
    assert c.get("/ping").status_code == 200
