"""GET/PUT /api/config -- the single source of truth for runtime settings.

The settings page reads this document, mutates a few fields and PUTs the whole
thing back, so anything the proxy injects on GET and anything it rewrites on PUT
is observable behaviour that the UI depends on.
"""


def test_get_on_a_fresh_install_returns_defaults(client):
    body = client.get("/api/config").get_json()
    assert body["hostname"]
    # Defaults the kiosk needs in order to render at all.
    assert body["posterTransitions"] is False
    assert body["transitionTypes"] == ["crossfade"]


def test_get_injects_hostname_without_persisting_it(client, write_cfg, read_cfg):
    write_cfg(rotateSec=60)
    body = client.get("/api/config").get_json()
    assert body["hostname"]
    assert body["rotateSec"] == 60
    # Reading config must never write to disk.
    assert read_cfg() == {"rotateSec": 60}


def test_get_does_not_override_saved_values_with_defaults(client, write_cfg):
    write_cfg(posterTransitions=True, transitionTypes=["flip", "slide-up"])
    body = client.get("/api/config").get_json()
    assert body["posterTransitions"] is True
    assert body["transitionTypes"] == ["flip", "slide-up"]


def test_get_tolerates_a_corrupt_config_file(client, isolated_proxy):
    isolated_proxy.write_text("{ this is not json", encoding="utf-8")
    body = client.get("/api/config").get_json()
    # Falls back to an empty document rather than 500-ing the kiosk.
    assert body["transitionTypes"] == ["crossfade"]


def test_put_then_get_round_trips(client, read_cfg):
    payload = {
        "sectionId": ["1", "3"],
        "rotateSec": 45,
        "plexUrl": "http://plex.test:32400",
        "plexToken": "tok123",
        "plexDevices": ["192.168.1.100"],
        "autoDim": True,
        "transitionTypes": ["scale-fade"],
    }
    r = client.put("/api/config", json=payload)
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}
    assert read_cfg() == payload
    fetched = client.get("/api/config").get_json()
    for key, value in payload.items():
        assert fetched[key] == value


def test_put_creates_the_config_file_if_missing(client, isolated_proxy, read_cfg):
    assert not isolated_proxy.exists()
    client.put("/api/config", json={"rotateSec": 10})
    assert isolated_proxy.exists()
    assert read_cfg() == {"rotateSec": 10}


def test_put_is_a_full_replace_not_a_merge(client, write_cfg, read_cfg):
    write_cfg(rotateSec=60, plexToken="tok123")
    client.put("/api/config", json={"rotateSec": 10})
    # The settings page compensates by PUTting a copy of the whole document.
    assert read_cfg() == {"rotateSec": 10}


def test_put_saves_indented_json(client, isolated_proxy):
    client.put("/api/config", json={"rotateSec": 10})
    assert "\n  " in isolated_proxy.read_text(encoding="utf-8")


class TestPlexUrlNormalization:
    """A bare host:port is accepted and given a scheme."""

    def _put(self, client, read_cfg, url):
        client.put("/api/config", json={"plexUrl": url})
        return read_cfg()["plexUrl"]

    def test_adds_http_scheme(self, client, read_cfg):
        assert self._put(client, read_cfg, "192.168.1.5:32400") == "http://192.168.1.5:32400"

    def test_keeps_http(self, client, read_cfg):
        assert self._put(client, read_cfg, "http://plex.test:32400") == "http://plex.test:32400"

    def test_keeps_https(self, client, read_cfg):
        assert self._put(client, read_cfg, "https://plex.test:32400") == "https://plex.test:32400"

    def test_leaves_empty_string_alone(self, client, read_cfg):
        assert self._put(client, read_cfg, "") == ""


def test_put_rejects_a_non_object_body(client, isolated_proxy):
    r = client.put("/api/config", json=["not", "a", "dict"])
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid body"
    assert not isolated_proxy.exists()


def test_put_rejects_malformed_json(client, isolated_proxy):
    r = client.put(
        "/api/config", data="{not json", content_type="application/json"
    )
    assert r.status_code == 400
    assert "error" in r.get_json()
    assert not isolated_proxy.exists()


class TestAdminKey:
    """PW_ADMIN_KEY, when set, gates every mutating endpoint."""

    def test_put_is_forbidden_without_the_key(self, client, proxy_app, monkeypatch, isolated_proxy):
        monkeypatch.setattr(proxy_app, "ADMIN_KEY", "s3cret")
        r = client.put("/api/config", json={"rotateSec": 10})
        assert r.status_code == 403
        assert r.get_json() == {"error": "forbidden"}
        assert not isolated_proxy.exists()

    def test_put_is_forbidden_with_the_wrong_key(self, client, proxy_app, monkeypatch):
        monkeypatch.setattr(proxy_app, "ADMIN_KEY", "s3cret")
        r = client.put(
            "/api/config", json={"rotateSec": 10}, headers={"X-Admin-Key": "nope"}
        )
        assert r.status_code == 403

    def test_put_succeeds_with_the_right_key(self, client, proxy_app, monkeypatch, read_cfg):
        monkeypatch.setattr(proxy_app, "ADMIN_KEY", "s3cret")
        r = client.put(
            "/api/config", json={"rotateSec": 10}, headers={"X-Admin-Key": "s3cret"}
        )
        assert r.status_code == 200
        assert read_cfg() == {"rotateSec": 10}

    def test_get_is_never_gated(self, client, proxy_app, monkeypatch):
        monkeypatch.setattr(proxy_app, "ADMIN_KEY", "s3cret")
        assert client.get("/api/config").status_code == 200


def test_put_does_not_sanitize_unknown_keys(client, read_cfg):
    """Legacy/unknown keys survive a save, so old installs keep working."""
    client.put("/api/config", json={"excludedLibraries": ["8"], "transitionType": "crossfade"})
    saved = read_cfg()
    assert saved["excludedLibraries"] == ["8"]
    assert saved["transitionType"] == "crossfade"


def test_secrets_are_stored_in_plaintext_by_design(client, isolated_proxy):
    """Documented, tested reality: config.json holds the Plex token verbatim.

    This is why ``proxy/config.json`` is gitignored -- see test_repo_hygiene.
    """
    client.put("/api/config", json={"plexToken": "tok123"})
    assert "tok123" in isolated_proxy.read_text(encoding="utf-8")
