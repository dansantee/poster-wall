"""First-run setup: the config fields the setup screen needs, and the Plex sign-in, server,
library and player endpoints the settings page uses (Dan, 2026-10-02: make setup easy enough
that nobody digs a token out of XML or looks up section IDs and IP addresses)."""
import pytest

from conftest import FakeResponse

RESOURCES = [
    {"name": "Cliff", "provides": "server", "owned": False, "accessToken": "shared-tok",
     "connections": [{"protocol": "https", "address": "192.168.1.165", "port": 32400, "local": True,
                      "relay": False, "uri": "https://192-168-1-165.abc.plex.direct:32400"}]},
    {"name": "Media", "provides": "server", "owned": True, "accessToken": "own-tok",
     "connections": [
         {"protocol": "https", "address": "104.237.138.40", "port": 8443, "local": False, "relay": True,
          "uri": "https://104-237-138-40.abc.plex.direct:8443"},
         {"protocol": "https", "address": "136.33.249.107", "port": 32400, "local": False, "relay": False,
          "uri": "https://136-33-249-107.abc.plex.direct:32400"},
         {"protocol": "https", "address": "192.168.1.3", "port": 32400, "local": True, "relay": False,
          "uri": "https://192-168-1-3.abc.plex.direct:32400"}]},
    {"name": "XBOX", "provides": "client,player", "owned": True, "accessToken": "x",
     "connections": []},
]


# --------------------------------------------------------------------------
# GET/PUT /api/config: what the setup screen reads
# --------------------------------------------------------------------------
def test_an_unconfigured_wall_says_so(client):
    body = client.get("/api/config").get_json()
    assert body["configured"] is False
    assert "ip" in body


def test_a_url_and_token_make_it_configured(client, write_cfg):
    write_cfg(plexUrl="http://192.168.1.3:32400", plexToken="tok")
    assert client.get("/api/config").get_json()["configured"] is True


def test_a_url_without_a_token_is_not_configured(client, write_cfg):
    write_cfg(plexUrl="http://192.168.1.3:32400")
    assert client.get("/api/config").get_json()["configured"] is False


def test_computed_keys_are_never_saved(client, read_cfg):
    """The settings page re-saves the whole document it loaded, computed keys included."""
    r = client.put("/api/config", json={"rotateSec": 30, "hostname": "x", "ip": "1.2.3.4", "configured": True})
    assert r.status_code == 200
    assert read_cfg() == {"rotateSec": 30}


# --------------------------------------------------------------------------
# Plex sign-in (plex.tv PIN / device-link flow)
# --------------------------------------------------------------------------
def test_sign_in_starts_with_a_pin_and_the_link_page(client, plex):
    plex.route("plex.tv/api/v2/pins", FakeResponse({"id": 42, "code": "ABCD", "expiresIn": 900}, status_code=201))
    body = client.post("/api/plex/pin").get_json()
    assert body == {"id": 42, "code": "ABCD", "linkUrl": "https://plex.tv/link", "expiresIn": 900}
    call = plex.calls_matching("plex.tv/api/v2/pins")[0]
    assert call.headers["X-Plex-Client-Identifier"].startswith("poster-wall-")
    assert call.headers["X-Plex-Product"] == "Poster Wall"


def test_the_pin_is_checked_with_the_same_client_id(client, plex):
    plex.route("plex.tv/api/v2/pins", FakeResponse({"id": 42, "code": "ABCD"}, status_code=201))
    client.post("/api/plex/pin")
    client.get("/api/plex/pin/42")
    ids = {c.headers["X-Plex-Client-Identifier"] for c in plex.calls_matching("plex.tv/api/v2/pins")}
    assert len(ids) == 1


def test_an_unclaimed_pin_is_not_linked_yet(client, plex):
    plex.route("plex.tv/api/v2/pins/42", FakeResponse({"id": 42, "authToken": None}))
    assert client.get("/api/plex/pin/42").get_json() == {"linked": False}


def test_an_expired_pin_says_so(client, plex):
    plex.route("plex.tv/api/v2/pins/42", FakeResponse(status_code=404, ok=False))
    assert client.get("/api/plex/pin/42").get_json() == {"linked": False, "expired": True}


def test_a_linked_pin_lists_servers_owned_first_with_addresses_to_try(client, plex):
    plex.route("plex.tv/api/v2/pins/42", FakeResponse({"id": 42, "authToken": "account-tok"}))
    plex.route("plex.tv/api/v2/resources", FakeResponse(RESOURCES))
    body = client.get("/api/plex/pin/42").get_json()
    assert body["linked"] is True
    assert [s["name"] for s in body["servers"]] == ["Media", "Cliff"]   # players left out; owned first
    media = body["servers"][0]
    assert media["token"] == "own-tok"   # the server's own token, not the account's
    assert media["candidates"] == [
        "http://192.168.1.3:32400",                         # LAN, plain http first
        "https://192-168-1-3.abc.plex.direct:32400",        # LAN https
        "https://136-33-249-107.abc.plex.direct:32400",     # remote
        "https://104-237-138-40.abc.plex.direct:8443",      # relay last
    ]
    assert plex.calls_matching("plex.tv/api/v2/resources")[0].headers["X-Plex-Token"] == "account-tok"


def test_plex_tv_unreachable_is_a_clear_error(client, plex):
    plex.route("plex.tv", ConnectionError("no route"))
    r = client.post("/api/plex/pin")
    assert r.status_code == 502
    assert "plex.tv" in r.get_json()["error"]


# --------------------------------------------------------------------------
# Picking an address, libraries, players
# --------------------------------------------------------------------------
def test_connect_uses_the_first_address_that_answers(client, plex):
    plex.route("http://192.168.1.3:32400/identity", ConnectionError("refused"))
    plex.route("https://192-168-1-3.abc.plex.direct:32400/identity", FakeResponse({"MediaContainer": {}}))
    body = client.post("/api/plex/connect", json={"token": "own-tok", "candidates": [
        "http://192.168.1.3:32400", "https://192-168-1-3.abc.plex.direct:32400", "https://remote:32400"]}).get_json()
    assert body == {"plexUrl": "https://192-168-1-3.abc.plex.direct:32400"}
    assert plex.calls_matching("remote") == []   # stops at the first that answers


def test_connect_reports_every_address_when_none_answer(client, plex):
    plex.route("/identity", FakeResponse(status_code=401, ok=False))
    r = client.post("/api/plex/connect", json={"token": "t", "candidates": ["http://a:32400", "http://b:32400"]})
    assert r.status_code == 502
    assert r.get_json()["tried"] == ["http://a:32400: HTTP 401", "http://b:32400: HTTP 401"]


def test_connect_only_takes_the_server_that_was_picked(client, plex):
    """Astra pass 1 #4: a shared server's private address can be something else on the wall's
    LAN, and anything answering 200 was taken. /identity must report the picked server's id."""
    plex.route("http://192.168.1.165:32400/identity",
               FakeResponse({"MediaContainer": {"machineIdentifier": "someone-elses-plex"}}))
    plex.route("http://192.168.1.166:32400/identity", FakeResponse(ValueError("not JSON"), content_type="text/html"))
    plex.route("https://remote:14243/identity", FakeResponse({"MediaContainer": {"machineIdentifier": "cliff-id"}}))
    r = client.post("/api/plex/connect", json={"token": "t", "machineId": "cliff-id", "candidates": [
        "http://192.168.1.165:32400", "http://192.168.1.166:32400", "https://remote:14243"]})
    assert r.get_json() == {"plexUrl": "https://remote:14243"}


def test_connect_never_sends_the_token_to_an_address_it_hasnt_verified(client, plex):
    """Astra pass 3: the token went to every candidate before its identity was checked, so a
    different machine at a shared server's private address received it. /identity needs none."""
    plex.route("http://192.168.1.165:32400/identity",
               FakeResponse({"MediaContainer": {"machineIdentifier": "someone-elses-plex"}}))
    plex.route("https://remote:14243/identity", FakeResponse({"MediaContainer": {"machineIdentifier": "cliff-id"}}))
    client.post("/api/plex/connect", json={"token": "secret-tok", "machineId": "cliff-id", "candidates": [
        "http://192.168.1.165:32400", "https://remote:14243"]})
    for call in plex.calls:
        assert "secret-tok" not in repr(call.params) and "secret-tok" not in repr(call.headers), call.url
        assert "secret-tok" not in call.url


def test_connect_reports_a_different_server_or_none(client, plex):
    plex.route("http://a:32400/identity", FakeResponse({"MediaContainer": {"machineIdentifier": "other"}}))
    plex.route("http://b:32400/identity", FakeResponse(ValueError("not JSON")))
    r = client.post("/api/plex/connect", json={"token": "t", "machineId": "mine",
                                               "candidates": ["http://a:32400", "http://b:32400"]})
    assert r.status_code == 502
    assert r.get_json()["tried"] == ["http://a:32400: a different server", "http://b:32400: not a Plex server"]


def test_servers_carry_their_id_for_the_identity_check(client, plex):
    resources = [dict(RESOURCES[1], clientIdentifier="media-id")]
    plex.route("plex.tv/api/v2/pins/42", FakeResponse({"id": 42, "authToken": "account-tok"}))
    plex.route("plex.tv/api/v2/resources", FakeResponse(resources))
    assert client.get("/api/plex/pin/42").get_json()["servers"][0]["id"] == "media-id"


def test_libraries_are_listed_for_the_picker(client, plex):
    plex.route("/library/sections", FakeResponse({"MediaContainer": {"Directory": [
        {"key": 1, "title": "Movies", "type": "movie"}, {"key": "2", "title": "TV Shows", "type": "show"}]}}))
    body = client.get("/api/plex/libraries", headers={"X-Plex-Url": "http://pms:32400", "X-Plex-Token": "t"}).get_json()
    assert body == {"libraries": [{"key": "1", "title": "Movies", "type": "movie", "agent": ""},
                                  {"key": "2", "title": "TV Shows", "type": "show", "agent": ""}]}
    assert plex.calls[0].url == "http://pms:32400/library/sections"


def test_players_playing_now_are_listed_with_their_addresses(client, plex):
    plex.route("/status/sessions", FakeResponse({"MediaContainer": {"Metadata": [
        {"title": "Wax Patrol", "Player": {"title": "Living Room TV", "address": "192.168.1.50", "product": "Plex for LG"}},
        {"title": "No Address", "Player": {"title": "Phone"}}]}}))
    body = client.get("/api/plex/players", headers={"X-Plex-Url": "http://pms:32400", "X-Plex-Token": "t"}).get_json()
    assert body == {"players": [{"title": "Living Room TV", "address": "192.168.1.50",
                                 "product": "Plex for LG", "playing": "Wax Patrol"}]}


@pytest.mark.parametrize("method,route", [
    ("post", "/api/plex/pin"), ("get", "/api/plex/pin/1"), ("post", "/api/plex/connect"),
    ("get", "/api/plex/libraries"), ("get", "/api/plex/players")])
def test_the_setup_endpoints_honour_the_admin_key(client, plex, proxy_app, monkeypatch, method, route):
    """They hand out Plex tokens, so they're guarded like saving the config."""
    monkeypatch.setattr(proxy_app, "ADMIN_KEY", "secret")
    assert getattr(client, method)(route).status_code == 403
    assert plex.calls == []
