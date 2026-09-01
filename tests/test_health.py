"""Health, build-info, CORS and routing surface.

These tests pin the proxy's public shape: the kiosk and the settings page are
served from a different origin/port than the API, so the CORS headers and the
route list are part of the contract, not incidental details.
"""
import subprocess

import pytest

# Every route the frontend (or an operator) is allowed to rely on. Adding an
# endpoint should be a deliberate act that updates this list and docs/API.md.
EXPECTED_ROUTES = {
    "/api/ping",
    "/api/build-info",
    "/api/config",
    "/api/movies",
    "/api/poster",
    "/api/now-playing",
    "/api/restart-kiosk",
    "/debug/routes",
    "/static/<path:filename>",
}

# Endpoints the browser preflights, and which therefore must answer OPTIONS.
PREFLIGHTED = [
    "/api/ping",
    "/api/build-info",
    "/api/config",
    "/api/movies",
    "/api/poster",
    "/api/now-playing",
]


def test_ping_returns_pong(client):
    r = client.get("/api/ping")
    assert r.status_code == 200
    assert r.get_data(as_text=True) == "pong"


@pytest.mark.parametrize("route", PREFLIGHTED)
def test_options_preflight_returns_204(client, route):
    r = client.options(route)
    assert r.status_code == 204


def test_cors_headers_allow_the_kiosk_origin_and_custom_headers(client):
    r = client.get("/api/ping")
    assert r.headers["Access-Control-Allow-Origin"] == "*"
    allowed_methods = r.headers["Access-Control-Allow-Methods"]
    for method in ("GET", "PUT", "POST", "OPTIONS"):
        assert method in allowed_methods
    allowed_headers = r.headers["Access-Control-Allow-Headers"]
    for header in ("X-Plex-Token", "X-Plex-Url", "X-Allow-Insecure", "X-Admin-Key"):
        assert header in allowed_headers, f"{header} must survive CORS preflight"


def test_route_surface_is_unchanged(proxy_app):
    rules = {str(rule) for rule in proxy_app.app.url_map.iter_rules()}
    assert rules == EXPECTED_ROUTES


def test_debug_routes_lists_every_route(client):
    r = client.get("/debug/routes")
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("text/plain")
    body = r.get_data(as_text=True)
    for route in EXPECTED_ROUTES:
        assert route in body


def test_build_info_reports_commit_and_tree_state(client):
    r = client.get("/api/build-info")
    assert r.status_code == 200
    info = r.get_json()
    assert info["hostname"]
    # The repo under test is a git checkout, so this is the happy path.
    assert "error" not in info, info.get("error")
    assert len(info["commit"]) == 40
    assert info["commit"].startswith(info["shortCommit"])
    assert isinstance(info["dirty"], bool)
    assert isinstance(info["status"], list)
    assert info["dirty"] == bool(info["status"])


def test_build_info_degrades_gracefully_without_git(client, proxy_app, monkeypatch):
    def boom(*_args, **_kwargs):
        raise FileNotFoundError("git not on PATH")

    monkeypatch.setattr(proxy_app.subprocess, "run", boom)
    r = client.get("/api/build-info")
    assert r.status_code == 200
    info = r.get_json()
    # The settings page renders `error` verbatim, so it must be a string.
    assert isinstance(info["error"], str) and info["error"]
    assert info["dirty"] is None
    assert info["status"] == []
    assert info["hostname"]


def test_build_info_survives_a_git_failure(client, proxy_app, monkeypatch):
    def failing(*_args, **_kwargs):
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(proxy_app.subprocess, "run", failing)
    info = client.get("/api/build-info").get_json()
    assert "error" in info
    assert info["dirty"] is None
