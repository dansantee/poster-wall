"""GET /api/movies -- the poster rotation feed.

The kiosk pages through this endpoint at boot, so its response shape, its
filtering rules and the poster URLs it hands back are all load-bearing.
"""
from urllib.parse import parse_qs, urlparse

import pytest

from conftest import FakeResponse, plex_container

SECTION_URL = "/library/sections/"


def movie(title="Arrival", **overrides):
    item = {
        "type": "movie",
        "title": title,
        "year": 2016,
        "addedAt": 1500000000,
        "thumb": "/library/metadata/1/thumb/1",
    }
    item.update(overrides)
    return item


def show(title="Severance", **overrides):
    item = {
        "type": "show",
        "title": title,
        "year": 2022,
        "addedAt": 1600000000,
        "thumb": "/library/metadata/2/thumb/2",
    }
    item.update(overrides)
    return item


def poster_params(poster_url):
    assert poster_url.startswith("/api/poster?")
    return {k: v[0] for k, v in parse_qs(urlparse(poster_url).query).items()}


# --------------------------------------------------------------------------
# Preconditions
# --------------------------------------------------------------------------
def test_requires_a_token(client, write_cfg):
    write_cfg(plexUrl="http://plex.test:32400")
    r = client.get("/api/movies")
    assert r.status_code == 400
    assert "PLEX_TOKEN" in r.get_json()["error"]


def test_requires_a_plex_url(client, write_cfg):
    write_cfg(plexToken="tok123")
    r = client.get("/api/movies")
    assert r.status_code == 400
    assert "No Plex URL" in r.get_json()["error"]


def test_token_may_come_from_a_request_header(client, write_cfg, plex):
    write_cfg(plexUrl="http://plex.test:32400")
    plex.route(SECTION_URL, FakeResponse(plex_container(movie())))
    r = client.get("/api/movies", headers={"X-Plex-Token": "hdr-token"})
    assert r.status_code == 200
    assert plex.calls[0].params["X-Plex-Token"] == "hdr-token"


def test_plex_url_may_come_from_a_request_header(client, write_cfg, plex):
    write_cfg(plexToken="tok123")
    plex.route(SECTION_URL, FakeResponse(plex_container(movie())))
    r = client.get("/api/movies", headers={"X-Plex-Url": "other.test:32400"})
    assert r.status_code == 200
    # A scheme-less host is upgraded to http://.
    assert plex.calls[0].url.startswith("http://other.test:32400/")


def test_environment_plex_url_wins_over_saved_config(client, write_cfg, plex, monkeypatch):
    write_cfg(plexUrl="http://saved.test:32400", plexToken="tok123")
    monkeypatch.setenv("PLEX_URL", "http://env.test:32400")
    plex.route(SECTION_URL, FakeResponse(plex_container(movie())))
    client.get("/api/movies")
    assert plex.calls[0].url.startswith("http://env.test:32400/")


# --------------------------------------------------------------------------
# Section selection
# --------------------------------------------------------------------------
def test_queries_every_configured_section(client, write_cfg, plex):
    write_cfg(plexUrl="http://plex.test:32400", plexToken="tok123", sectionId=["1", "3"])
    plex.route(SECTION_URL, FakeResponse(plex_container(movie())))
    client.get("/api/movies")
    assert plex.urls == [
        "http://plex.test:32400/library/sections/1/all",
        "http://plex.test:32400/library/sections/3/all",
    ]


def test_a_scalar_section_id_is_accepted_for_backward_compatibility(client, write_cfg, plex):
    write_cfg(plexUrl="http://plex.test:32400", plexToken="tok123", sectionId="2")
    plex.route(SECTION_URL, FakeResponse(plex_container(movie())))
    client.get("/api/movies")
    assert plex.urls == ["http://plex.test:32400/library/sections/2/all"]


def test_falls_back_to_the_default_section(client, write_cfg, plex):
    write_cfg(plexUrl="http://plex.test:32400", plexToken="tok123")
    plex.route(SECTION_URL, FakeResponse(plex_container(movie())))
    client.get("/api/movies")
    assert plex.urls == ["http://plex.test:32400/library/sections/1/all"]


def test_section_query_parameter_overrides_the_config(client, write_cfg, plex):
    write_cfg(plexUrl="http://plex.test:32400", plexToken="tok123", sectionId=["1", "3"])
    plex.route(SECTION_URL, FakeResponse(plex_container(movie())))
    client.get("/api/movies?section=9")
    assert plex.urls == ["http://plex.test:32400/library/sections/9/all"]


def test_upstream_request_asks_plex_for_a_large_sorted_batch(client, configured_plex):
    configured_plex.route(SECTION_URL, FakeResponse(plex_container(movie())))
    client.get("/api/movies")
    params = configured_plex.calls[0].params
    assert params["sort"] == "addedAt:desc"
    assert params["X-Plex-Container-Start"] == 0
    assert params["X-Plex-Container-Size"] == 2000


# --------------------------------------------------------------------------
# Filtering
# --------------------------------------------------------------------------
def test_keeps_only_movies_and_shows(client, configured_plex):
    configured_plex.route(
        SECTION_URL,
        FakeResponse(
            plex_container(
                movie("Arrival"),
                show("Severance"),
                {"type": "artist", "title": "Bach", "thumb": "/t/3"},
                {"type": "photo", "title": "Trip", "thumb": "/t/4"},
                {"type": "episode", "title": "Pilot", "thumb": "/t/5"},
            )
        ),
    )
    body = client.get("/api/movies").get_json()
    assert sorted(i["title"] for i in body["items"]) == ["Arrival", "Severance"]


def test_drops_items_with_no_artwork(client, configured_plex):
    configured_plex.route(
        SECTION_URL,
        FakeResponse(plex_container(movie("Arrival"), movie("No Art", thumb=None))),
    )
    body = client.get("/api/movies").get_json()
    assert [i["title"] for i in body["items"]] == ["Arrival"]
    # totalSize counts what Plex returned, before the artwork filter, so it can
    # exceed the number of items actually rendered.
    assert body["totalSize"] == 2
    assert body["returned"] == 1


def test_ignores_a_non_json_response(client, configured_plex):
    configured_plex.route(
        SECTION_URL, FakeResponse(status_code=200, content_type="text/html")
    )
    body = client.get("/api/movies").get_json()
    assert body["items"] == []
    assert body["totalSize"] == 0


def test_ignores_an_error_response(client, configured_plex):
    configured_plex.route(SECTION_URL, FakeResponse(status_code=401, ok=False))
    body = client.get("/api/movies").get_json()
    assert body["items"] == []


def test_one_unreachable_section_does_not_fail_the_request(client, write_cfg, plex):
    write_cfg(plexUrl="http://plex.test:32400", plexToken="tok123", sectionId=["1", "3"])
    plex.route("/sections/1/", ConnectionError("boom"))
    plex.route("/sections/3/", FakeResponse(plex_container(movie("Arrival"))))
    r = client.get("/api/movies")
    assert r.status_code == 200
    assert [i["title"] for i in r.get_json()["items"]] == ["Arrival"]


# --------------------------------------------------------------------------
# Response shape
# --------------------------------------------------------------------------
def test_item_shape(client, configured_plex):
    configured_plex.route(SECTION_URL, FakeResponse(plex_container(movie("Arrival"))))
    item = client.get("/api/movies").get_json()["items"][0]
    assert set(item) == {"title", "year", "addedAt", "poster", "type", "mediaType"}
    assert item["title"] == "Arrival"
    assert item["year"] == 2016
    assert item["type"] == "movie"
    assert item["mediaType"] == "movie"


def test_shows_are_normalized_to_media_type_show(client, configured_plex):
    configured_plex.route(SECTION_URL, FakeResponse(plex_container(show())))
    item = client.get("/api/movies").get_json()["items"][0]
    assert item["type"] == "show"
    assert item["mediaType"] == "show"


def test_poster_url_is_a_self_referential_proxy_url(client, configured_plex):
    configured_plex.route(SECTION_URL, FakeResponse(plex_container(movie())))
    item = client.get("/api/movies").get_json()["items"][0]
    params = poster_params(item["poster"])
    assert params["base"] == "http://plex.test:32400"
    assert params["thumb"] == "/library/metadata/1/thumb/1"
    assert params["token"] == "tok123"
    # 1200x1800 is the 2:3 one-sheet aspect the kiosk renders.
    assert params["w"] == "1200"
    assert params["h"] == "1800"
    assert params["insecure"] == "0"


def test_poster_url_carries_the_insecure_flag(client, write_cfg, plex):
    write_cfg(plexUrl="https://plex.test:32400", plexToken="tok123", plexInsecure=True)
    plex.route(SECTION_URL, FakeResponse(plex_container(movie())))
    item = client.get("/api/movies").get_json()["items"][0]
    assert poster_params(item["poster"])["insecure"] == "1"
    assert plex.calls[0].verify is False


def test_tls_is_verified_by_default(client, configured_plex):
    configured_plex.route(SECTION_URL, FakeResponse(plex_container(movie())))
    client.get("/api/movies")
    assert configured_plex.calls[0].verify is True


# --------------------------------------------------------------------------
# Paging
# --------------------------------------------------------------------------
def _many(count):
    return plex_container(*[movie("Movie %d" % i) for i in range(count)])


def test_paging_defaults(client, configured_plex):
    configured_plex.route(SECTION_URL, FakeResponse(_many(3)))
    body = client.get("/api/movies").get_json()
    assert body["start"] == 0
    assert body["size"] == 500
    assert body["returned"] == 3
    assert body["totalSize"] == 3


def test_paging_honours_start_and_size(client, configured_plex):
    configured_plex.route(SECTION_URL, FakeResponse(_many(10)))
    body = client.get("/api/movies?start=4&size=3").get_json()
    assert body["start"] == 4
    assert body["size"] == 3
    assert body["returned"] == 3
    assert body["totalSize"] == 10


def test_limit_is_an_alias_for_size(client, configured_plex):
    configured_plex.route(SECTION_URL, FakeResponse(_many(10)))
    body = client.get("/api/movies?limit=2").get_json()
    assert body["size"] == 2
    assert body["returned"] == 2


def test_paging_past_the_end_returns_an_empty_page(client, configured_plex):
    configured_plex.route(SECTION_URL, FakeResponse(_many(3)))
    body = client.get("/api/movies?start=100").get_json()
    assert body["items"] == []
    assert body["totalSize"] == 3


@pytest.mark.parametrize(
    "requested,clamped", [("0", 1), ("-5", 1), ("1", 1), ("1000", 1000), ("5000", 1000)]
)
def test_size_is_clamped(client, configured_plex, requested, clamped):
    configured_plex.route(SECTION_URL, FakeResponse(_many(1)))
    body = client.get("/api/movies?size=" + requested).get_json()
    assert body["size"] == clamped


def test_results_are_shuffled_server_side(client, configured_plex, proxy_app, monkeypatch):
    """The kiosk relies on the proxy mixing libraries together."""
    shuffled = []
    monkeypatch.setattr(proxy_app.random, "shuffle", lambda seq: shuffled.append(list(seq)))
    configured_plex.route(SECTION_URL, FakeResponse(_many(5)))
    client.get("/api/movies")
    assert len(shuffled) == 1, "the section results must be shuffled exactly once"
