"""Shared fixtures for the poster-wall test suite.

The proxy (``proxy/app.py``) reads several settings into module-level constants
at import time, so this module scrubs the relevant environment variables
*before* importing it. That keeps a developer's own ``PLEX_URL`` / ``PLEX_TOKEN``
out of the tests.
"""
import json
import os
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PROXY_DIR = REPO_ROOT / "proxy"
WEB_DIR = REPO_ROOT / "web"

# Captured into module constants by app.py at import time (or read from the
# process environment at request time) -- clear them for a deterministic import.
_LEAKY_ENV = (
    "PLEX_URL",
    "PLEX_TOKEN",
    "SECTION_ID",
    "TIMEOUT",
    "ALLOW_INSECURE",
    "PW_ADMIN_KEY",
    "PW_CONFIG_PATH",
)
for _var in _LEAKY_ENV:
    os.environ.pop(_var, None)

sys.path.insert(0, str(PROXY_DIR))
import app as app_module  # noqa: E402  (import must follow the env scrub)


# --------------------------------------------------------------------------
# Fake Plex server
# --------------------------------------------------------------------------
class RecordedCall:
    """One outbound ``requests.get`` made by the proxy."""

    def __init__(self, url, params, headers, timeout, verify, stream):
        self.url = url
        self.params = params or {}
        self.headers = headers or {}
        self.timeout = timeout
        self.verify = verify
        self.stream = stream

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"<RecordedCall {self.url} verify={self.verify}>"


class FakeResponse:
    def __init__(self, json_data=None, status_code=200, headers=None,
                 content=b"", ok=None, content_type="application/json"):
        self._json = json_data if json_data is not None else {}
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.headers.setdefault("Content-Type", content_type)
        self._content = content
        self.ok = (status_code < 400) if ok is None else ok

    def json(self):
        if isinstance(self._json, Exception):
            raise self._json
        return self._json

    def iter_content(self, chunk_size=1):
        yield self._content


class FakePlex:
    """Routes the proxy's outbound calls by URL substring and records them."""

    def __init__(self):
        self.calls = []
        self._routes = []
        self.default = FakeResponse(status_code=404, ok=False)

    def route(self, url_substring, response):
        """``response`` may be a FakeResponse, a callable, or an Exception."""
        self._routes.append((url_substring, response))
        return self

    def get(self, url, params=None, headers=None, timeout=None, verify=True,
            stream=False, **_kwargs):
        call = RecordedCall(url, params, headers, timeout, verify, stream)
        self.calls.append(call)
        for substring, response in self._routes:
            if substring in url:
                if isinstance(response, Exception):
                    raise response
                if callable(response):
                    return response(call)
                return response
        return self.default

    # -- assertions helpers ------------------------------------------------
    @property
    def urls(self):
        return [c.url for c in self.calls]

    def calls_matching(self, url_substring):
        return [c for c in self.calls if url_substring in c.url]


def plex_container(*items):
    """Wrap metadata items in Plex's MediaContainer envelope."""
    return {"MediaContainer": {"size": len(items), "Metadata": list(items)}}


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def proxy_app():
    """The Flask app module under test."""
    return app_module


@pytest.fixture(autouse=True)
def isolated_proxy(tmp_path, monkeypatch):
    """Point the proxy at a throwaway config file with a clean environment.

    Autouse so that no test can accidentally read or write the developer's real
    ``proxy/config.json``.
    """
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(app_module, "CFG_PATH", cfg_path)
    monkeypatch.setattr(app_module, "SERVER_TOKEN", "")
    monkeypatch.setattr(app_module, "ADMIN_KEY", "")
    monkeypatch.setattr(app_module, "ALLOW_INSECURE_DEFAULT", False)
    monkeypatch.setattr(app_module, "DEFAULT_SECTION", "1")
    for var in _LEAKY_ENV:
        monkeypatch.delenv(var, raising=False)
    return cfg_path


@pytest.fixture
def write_cfg(isolated_proxy):
    """Write the proxy's server-side config file."""

    def _write(**values):
        isolated_proxy.write_text(json.dumps(values), encoding="utf-8")
        return isolated_proxy

    return _write


@pytest.fixture
def read_cfg(isolated_proxy):
    def _read():
        if not isolated_proxy.exists():
            return None
        return json.loads(isolated_proxy.read_text(encoding="utf-8"))

    return _read


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def plex(monkeypatch):
    """Install a FakePlex in place of the ``requests`` module app.py uses."""
    fake = FakePlex()
    monkeypatch.setattr(app_module.requests, "get", fake.get)
    return fake


@pytest.fixture
def configured_plex(write_cfg, plex):
    """A proxy configured with a Plex URL + token, and a fake Plex to talk to."""
    write_cfg(
        plexUrl="http://plex.test:32400",
        plexToken="tok123",
        sectionId=["1"],
    )
    return plex


# --------------------------------------------------------------------------
# Source-file fixtures used by the contract tests
# --------------------------------------------------------------------------
@pytest.fixture(scope="session")
def repo_root():
    return REPO_ROOT


@pytest.fixture(scope="session")
def source():
    """Lazily read repo source files as text, keyed by repo-relative path."""
    cache = {}

    def _read(relative_path):
        if relative_path not in cache:
            cache[relative_path] = (REPO_ROOT / relative_path).read_text(
                encoding="utf-8"
            )
        return cache[relative_path]

    return _read
