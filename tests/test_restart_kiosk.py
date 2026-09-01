"""POST /api/restart-kiosk -- the settings page's "Restart Kiosk" button.

This is the one endpoint that runs a command on the Pi, so both the exact
command and the admin-key gate matter.
"""
import subprocess

import pytest


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def systemctl(proxy_app, monkeypatch):
    """Capture the command the endpoint would run."""
    calls = []
    result = {"value": FakeCompleted()}

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        outcome = result["value"]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(proxy_app.subprocess, "run", fake_run)
    return type("Systemctl", (), {"calls": calls, "result": result})()


def test_restarts_the_kiosk_unit_only(client, systemctl):
    r = client.post("/api/restart-kiosk")
    assert r.status_code == 200
    assert r.get_json() == {
        "ok": True,
        "message": "Kiosk service restart initiated",
    }
    cmd, kwargs = systemctl.calls[0]
    # A user unit, not a system one -- setup.sh installs the services under
    # ~/.config/systemd/user with lingering enabled.
    assert cmd == ["systemctl", "--user", "restart", "poster-kiosk.service"]
    assert kwargs["timeout"] == 10
    assert kwargs["capture_output"] is True


def test_the_proxy_never_restarts_itself(client, systemctl):
    """Restarting poster-proxy here would kill the request mid-flight."""
    client.post("/api/restart-kiosk")
    cmd, _ = systemctl.calls[0]
    assert "poster-proxy.service" not in cmd
    assert "poster-web.service" not in cmd


def test_a_failed_command_returns_500_with_stderr(client, systemctl):
    systemctl.result["value"] = FakeCompleted(returncode=1, stderr="Unit not found")
    r = client.post("/api/restart-kiosk")
    assert r.status_code == 500
    assert "Unit not found" in r.get_json()["error"]


def test_a_hung_command_returns_500(client, systemctl):
    systemctl.result["value"] = subprocess.TimeoutExpired("systemctl", 10)
    r = client.post("/api/restart-kiosk")
    assert r.status_code == 500
    assert r.get_json()["error"] == "Restart command timed out"


def test_a_missing_systemctl_returns_500(client, systemctl):
    systemctl.result["value"] = FileNotFoundError("systemctl")
    r = client.post("/api/restart-kiosk")
    assert r.status_code == 500
    assert "Failed to restart kiosk" in r.get_json()["error"]


def test_options_preflight_does_not_run_anything(client, systemctl):
    r = client.options("/api/restart-kiosk")
    assert r.status_code == 204
    assert systemctl.calls == []


class TestAdminKey:
    def test_forbidden_without_the_key(self, client, systemctl, proxy_app, monkeypatch):
        monkeypatch.setattr(proxy_app, "ADMIN_KEY", "s3cret")
        r = client.post("/api/restart-kiosk")
        assert r.status_code == 403
        assert systemctl.calls == [], "no command may run for an unauthorised caller"

    def test_forbidden_with_the_wrong_key(self, client, systemctl, proxy_app, monkeypatch):
        monkeypatch.setattr(proxy_app, "ADMIN_KEY", "s3cret")
        r = client.post("/api/restart-kiosk", headers={"X-Admin-Key": "nope"})
        assert r.status_code == 403
        assert systemctl.calls == []

    def test_allowed_with_the_right_key(self, client, systemctl, proxy_app, monkeypatch):
        monkeypatch.setattr(proxy_app, "ADMIN_KEY", "s3cret")
        r = client.post("/api/restart-kiosk", headers={"X-Admin-Key": "s3cret"})
        assert r.status_code == 200
        assert len(systemctl.calls) == 1

    def test_open_by_default(self, client, systemctl):
        """With no PW_ADMIN_KEY the endpoint is unauthenticated by design."""
        assert client.post("/api/restart-kiosk").status_code == 200
