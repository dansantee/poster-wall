"""GET /api/poster -- the image proxy.

Every poster the kiosk shows is streamed through here, which is what lets the
browser fetch Plex artwork without CORS trouble and without knowing the token.
"""
from urllib.parse import parse_qs, urlparse

import pytest

from conftest import FakeResponse

TRANSCODE = "/photo/:/transcode"

BASE = "http://plex.test:32400"
THUMB = "/library/metadata/1/thumb/1"


def upstream_query(call):
    return {k: v[0] for k, v in parse_qs(urlparse(call.url).query).items()}


def poster_request(client, **params):
    query = "&".join("%s=%s" % (k, v) for k, v in params.items())
    return client.get("/api/poster?" + query)


# --------------------------------------------------------------------------
# Preconditions
# --------------------------------------------------------------------------
def test_requires_a_thumb(client, configured_plex):
    r = poster_request(client, base=BASE, token="tok123")
    assert r.status_code == 400
    assert r.get_json()["error"] == "missing base/thumb/token"


def test_requires_a_token(client, write_cfg, plex):
    write_cfg(plexUrl=BASE)
    r = poster_request(client, base=BASE, thumb=THUMB)
    assert r.status_code == 400
    assert r.get_json()["error"] == "missing base/thumb/token"


def test_requires_a_resolvable_base(client, plex):
    r = poster_request(client, thumb=THUMB, token="tok123")
    assert r.status_code == 400
    assert "No Plex URL" in r.get_json()["error"]


def test_base_falls_back_to_the_saved_config(client, configured_plex):
    configured_plex.route(TRANSCODE, FakeResponse(content=b"jpeg"))
    r = poster_request(client, thumb=THUMB)
    assert r.status_code == 200
    assert configured_plex.calls[0].url.startswith(BASE + TRANSCODE)


def test_token_falls_back_to_the_saved_config(client, configured_plex):
    configured_plex.route(TRANSCODE, FakeResponse(content=b"jpeg"))
    poster_request(client, base=BASE, thumb=THUMB)
    assert upstream_query(configured_plex.calls[0])["X-Plex-Token"] == "tok123"


def test_a_scheme_less_base_is_upgraded_to_http(client, configured_plex):
    configured_plex.route(TRANSCODE, FakeResponse(content=b"jpeg"))
    poster_request(client, base="plex.test:32400", thumb=THUMB, token="tok123")
    assert configured_plex.calls[0].url.startswith("http://plex.test:32400/")


# --------------------------------------------------------------------------
# Upstream request
# --------------------------------------------------------------------------
def test_asks_plex_to_transcode_the_artwork(client, configured_plex):
    configured_plex.route(TRANSCODE, FakeResponse(content=b"jpeg"))
    poster_request(client, base=BASE, thumb=THUMB, token="tok123", w=1200, h=1800)
    call = configured_plex.calls[0]
    assert call.url.startswith(BASE + TRANSCODE + "?")
    query = upstream_query(call)
    assert query["url"] == BASE + THUMB
    assert query["width"] == "1200"
    assert query["height"] == "1800"
    assert query["minSize"] == "1"
    assert query["X-Plex-Token"] == "tok123"
    # Streamed, so a large poster never buffers fully in the Pi's memory.
    assert call.stream is True


def test_default_dimensions(client, configured_plex):
    configured_plex.route(TRANSCODE, FakeResponse(content=b"jpeg"))
    poster_request(client, base=BASE, thumb=THUMB, token="tok123")
    query = upstream_query(configured_plex.calls[0])
    assert query["width"] == "600"
    assert query["height"] == "900"


def test_tls_is_verified_by_default(client, configured_plex):
    configured_plex.route(TRANSCODE, FakeResponse(content=b"jpeg"))
    poster_request(client, base=BASE, thumb=THUMB, token="tok123")
    assert configured_plex.calls[0].verify is True


@pytest.mark.parametrize("flag", ["1", "true", "yes", "on"])
def test_insecure_query_flag_disables_tls_verification(client, configured_plex, flag):
    configured_plex.route(TRANSCODE, FakeResponse(content=b"jpeg"))
    poster_request(client, base=BASE, thumb=THUMB, token="tok123", insecure=flag)
    assert configured_plex.calls[0].verify is False


def test_saved_insecure_setting_applies_when_the_url_says_nothing(client, write_cfg, plex):
    write_cfg(plexUrl=BASE, plexToken="tok123", plexInsecure=True)
    plex.route(TRANSCODE, FakeResponse(content=b"jpeg"))
    poster_request(client, base=BASE, thumb=THUMB, token="tok123")
    assert plex.calls[0].verify is False


@pytest.mark.parametrize("flag", ["0", "false", "no", "off"])
def test_an_explicit_insecure_zero_overrides_the_saved_setting(client, write_cfg, plex, flag):
    """``insecure=0`` re-enables verification even when the config disables it."""
    write_cfg(plexUrl=BASE, plexToken="tok123", plexInsecure=True)
    plex.route(TRANSCODE, FakeResponse(content=b"jpeg"))
    poster_request(client, base=BASE, thumb=THUMB, token="tok123", insecure=flag)
    assert plex.calls[0].verify is True


# --------------------------------------------------------------------------
# Response
# --------------------------------------------------------------------------
def test_streams_the_image_through(client, configured_plex):
    configured_plex.route(
        TRANSCODE,
        FakeResponse(content=b"\xff\xd8jpegbytes", content_type="image/jpeg"),
    )
    r = poster_request(client, base=BASE, thumb=THUMB, token="tok123")
    assert r.status_code == 200
    assert r.get_data() == b"\xff\xd8jpegbytes"
    assert r.headers["Content-Type"] == "image/jpeg"


def test_posters_are_cached_for_a_day(client, configured_plex):
    configured_plex.route(TRANSCODE, FakeResponse(content=b"jpeg"))
    r = poster_request(client, base=BASE, thumb=THUMB, token="tok123")
    assert r.headers["Cache-Control"] == "public, max-age=86400"


def test_content_type_defaults_to_jpeg(client, configured_plex):
    untyped = FakeResponse(content=b"jpeg")
    del untyped.headers["Content-Type"]
    configured_plex.route(TRANSCODE, untyped)
    r = poster_request(client, base=BASE, thumb=THUMB, token="tok123")
    assert r.headers["Content-Type"] == "image/jpeg"


def test_upstream_status_is_passed_through(client, configured_plex):
    configured_plex.route(TRANSCODE, FakeResponse(status_code=404, ok=False, content=b""))
    r = poster_request(client, base=BASE, thumb=THUMB, token="tok123")
    assert r.status_code == 404


def test_an_unreachable_plex_returns_502(client, configured_plex):
    configured_plex.route(TRANSCODE, ConnectionError("connection refused"))
    r = poster_request(client, base=BASE, thumb=THUMB, token="tok123")
    assert r.status_code == 502
    assert "Upstream request error" in r.get_json()["error"]
