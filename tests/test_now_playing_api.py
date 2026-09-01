"""GET /api/now-playing -- the "Now Showing" takeover.

The kiosk polls this every 5 seconds. It must only ever report playback from the
device IPs the user opted into, and only from the libraries they selected, so the
filtering here is a privacy/behaviour boundary rather than a detail.
"""
import copy

import pytest

from conftest import FakeResponse, plex_container

SESSIONS = "/status/sessions"
METADATA = "/library/metadata/"

BASE = "http://plex.test:32400"
DEVICE = "192.168.1.100"

MOVIE_SESSION = {
    "type": "movie",
    "title": "Arrival",
    "year": 2016,
    "contentRating": "PG-13",
    "duration": 10000,
    "viewOffset": 2500,
    "ratingKey": "101",
    "thumb": "/library/metadata/101/thumb/1",
    "librarySectionID": 1,
    "Player": {"address": DEVICE, "title": "Living Room"},
    "Media": [
        {
            "videoResolution": "4k",
            "videoCodec": "hevc",
            "Part": [
                {
                    "Stream": [
                        {"streamType": 1, "codec": "hevc"},
                        {"streamType": 2, "codec": "eac3", "channels": 6},
                    ]
                }
            ],
        }
    ],
}

EPISODE_SESSION = {
    "type": "episode",
    "title": "Good News About Hell",
    "grandparentTitle": "Severance",
    "parentIndex": 1,
    "index": 2,
    "year": 2022,
    "contentRating": "TV-MA",
    "duration": 20000,
    "viewOffset": 5000,
    "ratingKey": "202",
    "parentRatingKey": "200",
    "thumb": "/library/metadata/202/thumb/episode-frame",
    "parentThumb": "/library/metadata/200/thumb/season",
    "grandparentThumb": "/library/metadata/199/thumb/show",
    "librarySectionID": 3,
    "Player": {"address": DEVICE, "title": "Bedroom"},
    "Media": [{"videoResolution": "1080", "videoCodec": "h264", "Part": []}],
}


def session(template=None, **overrides):
    item = copy.deepcopy(template if template is not None else MOVIE_SESSION)
    item.update(overrides)
    return item


@pytest.fixture
def monitored(write_cfg, plex):
    """A proxy watching one device, including library sections 1 and 3."""
    write_cfg(
        plexUrl=BASE,
        plexToken="tok123",
        plexDevices=[DEVICE],
        sectionId=["1", "3"],
    )
    return plex


def playing(client, monitored, *sessions):
    monitored.route(SESSIONS, FakeResponse(plex_container(*sessions)))
    return client.get("/api/now-playing").get_json()


# --------------------------------------------------------------------------
# Preconditions
# --------------------------------------------------------------------------
def test_no_devices_configured_means_the_feature_is_off(client, write_cfg, plex):
    write_cfg(plexUrl=BASE, plexToken="tok123")
    body = client.get("/api/now-playing").get_json()
    assert body == {"playing": False, "message": "No devices configured"}
    assert plex.calls == [], "Plex must not be polled when no device is monitored"


def test_requires_a_token(client, write_cfg, plex):
    write_cfg(plexUrl=BASE, plexDevices=[DEVICE])
    r = client.get("/api/now-playing")
    assert r.status_code == 400
    assert "PLEX_TOKEN" in r.get_json()["error"]


def test_requires_a_plex_url(client, write_cfg, plex):
    write_cfg(plexToken="tok123", plexDevices=[DEVICE])
    r = client.get("/api/now-playing")
    assert r.status_code == 400
    assert "No Plex URL" in r.get_json()["error"]


def test_polls_the_plex_sessions_endpoint(client, monitored):
    playing(client, monitored)
    call = monitored.calls[0]
    assert call.url == BASE + SESSIONS
    assert call.params["X-Plex-Token"] == "tok123"


# --------------------------------------------------------------------------
# Device filtering
# --------------------------------------------------------------------------
def test_reports_playback_on_a_monitored_device(client, monitored):
    body = playing(client, monitored, session())
    assert body["playing"] is True
    assert body["title"] == "Arrival"


def test_ignores_playback_on_an_unmonitored_device(client, monitored):
    other = session(Player={"address": "10.0.0.5", "title": "Someone Else"})
    body = playing(client, monitored, other)
    assert body == {
        "playing": False,
        "message": "No active sessions on monitored devices",
    }


def test_device_matching_ignores_case_and_padding(client, write_cfg, plex):
    write_cfg(
        plexUrl=BASE,
        plexToken="tok123",
        plexDevices=["  Apple-TV.Local  "],
        sectionId=["1"],
    )
    body = playing(
        client, plex, session(Player={"address": "apple-tv.local", "title": "ATV"})
    )
    assert body["playing"] is True


def test_no_sessions_at_all(client, monitored):
    body = playing(client, monitored)
    assert body["playing"] is False


def test_the_first_matching_session_wins(client, monitored):
    body = playing(
        client,
        monitored,
        session(Player={"address": "10.0.0.5"}),
        session(title="Dune"),
        session(title="Tenet"),
    )
    assert body["title"] == "Dune"


# --------------------------------------------------------------------------
# Library and media-type filtering
# --------------------------------------------------------------------------
def test_ignores_playback_from_a_library_that_is_not_selected(client, monitored):
    body = playing(client, monitored, session(librarySectionID=8))
    assert body["playing"] is False


def test_section_ids_are_compared_as_strings(client, write_cfg, plex):
    write_cfg(plexUrl=BASE, plexToken="tok123", plexDevices=[DEVICE], sectionId=["1"])
    assert playing(client, plex, session(librarySectionID="1"))["playing"] is True


def test_a_scalar_section_id_is_accepted_for_backward_compatibility(client, write_cfg, plex):
    write_cfg(plexUrl=BASE, plexToken="tok123", plexDevices=[DEVICE], sectionId="1")
    assert playing(client, plex, session(librarySectionID=1))["playing"] is True


@pytest.mark.parametrize("media_type", ["track", "photo", "clip", "show"])
def test_ignores_non_movie_non_episode_playback(client, monitored, media_type):
    body = playing(client, monitored, session(type=media_type))
    assert body["playing"] is False


# --------------------------------------------------------------------------
# Movie payload
# --------------------------------------------------------------------------
def test_movie_payload(client, monitored):
    body = playing(client, monitored, session())
    assert body["playing"] is True
    assert body["title"] == "Arrival"
    assert body["year"] == 2016
    assert body["rating"] == "PG-13"
    assert body["mediaType"] == "movie"
    assert body["playerTitle"] == "Living Room"
    assert body["duration"] == 10000
    assert body["viewOffset"] == 2500
    assert body["progress"] == 25.0
    assert body["videoResolution"] == "4k"
    assert body["videoCodec"] == "HEVC"
    assert body["audioCodec"] == "EAC3"
    assert body["audioChannels"] == "6.1"
    assert body["poster"].startswith("/api/poster?")


def test_movie_poster_comes_from_the_session_thumb(client, monitored):
    body = playing(client, monitored, session())
    assert "101%2Fthumb%2F1" in body["poster"]
    # Movie artwork needs no extra metadata lookup.
    assert monitored.calls_matching(METADATA) == []


def test_a_movie_without_artwork_has_no_poster(client, monitored):
    body = playing(client, monitored, session(thumb=None))
    assert body["playing"] is True
    assert body["poster"] is None


# --------------------------------------------------------------------------
# Progress
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "duration,offset,expected",
    [
        (10000, 0, 0),
        (10000, 5000, 50.0),
        (10000, 10000, 100.0),
        (10000, 999999, 100.0),
        (10000, -50, 0),
        (3000, 1000, 33.3),
        (0, 5000, 0),
    ],
)
def test_progress_is_a_clamped_rounded_percentage(client, monitored, duration, offset, expected):
    body = playing(client, monitored, session(duration=duration, viewOffset=offset))
    assert body["progress"] == expected


def test_missing_duration_and_offset_default_to_zero(client, monitored):
    body = playing(client, monitored, session(duration=0, viewOffset=0))
    assert body["duration"] == 0
    assert body["viewOffset"] == 0
    assert body["progress"] == 0


# --------------------------------------------------------------------------
# Audio stream selection
# --------------------------------------------------------------------------
def _with_audio(**stream):
    media = [
        {
            "videoResolution": "1080",
            "videoCodec": "h264",
            "Part": [{"Stream": [{"streamType": 1, "codec": "h264"}, dict(streamType=2, **stream)]}],
        }
    ]
    return session(Media=media)


@pytest.mark.parametrize(
    "channels,expected",
    [
        (1, "1.0"),
        (2, "2.0"),
        (6, "6.1"),
        (8, "8.1"),
        (0, ""),
        (None, ""),
    ],
)
def test_audio_channel_labels(client, monitored, channels, expected):
    """Note the quirk: any layout above stereo is labelled ``<channels>.1``.

    Plex's channel count includes the LFE channel, so a 5.1 track arrives as 6
    channels and is reported as "6.1". ``web/app.js`` normalises that back to a
    5.1 icon; see ``getAudioChannelIcon``.
    """
    stream = {"codec": "eac3"}
    if channels is not None:
        stream["channels"] = channels
    body = playing(client, monitored, _with_audio(**stream))
    assert body["audioChannels"] == expected


def test_audio_codec_is_uppercased(client, monitored):
    body = playing(client, monitored, _with_audio(codec="truehd", channels=8))
    assert body["audioCodec"] == "TRUEHD"


def test_only_audio_streams_are_considered(client, monitored):
    media = [
        {
            "videoResolution": "1080",
            "videoCodec": "h264",
            "Part": [
                {
                    "Stream": [
                        {"streamType": 3, "codec": "srt"},
                        {"streamType": 2, "codec": "ac3", "channels": 2},
                    ]
                }
            ],
        }
    ]
    body = playing(client, monitored, session(Media=media))
    assert body["audioCodec"] == "AC3"


def test_no_audio_stream_leaves_the_fields_blank(client, monitored):
    body = playing(client, monitored, session(template=EPISODE_SESSION))
    assert body["audioCodec"] == ""
    assert body["audioChannels"] == ""


def test_a_session_without_media_details_reports_an_error(client, monitored):
    """Known rough edge: an empty ``Media`` list is not handled defensively.

    The IndexError is swallowed by the endpoint's catch-all, so the kiosk simply
    stays in rotation instead of crashing -- but the cause is only visible here.
    """
    body = playing(client, monitored, session(Media=[]))
    assert body["playing"] is False
    assert "Sessions check failed" in body["error"]


# --------------------------------------------------------------------------
# Episode payload and poster selection
# --------------------------------------------------------------------------
def test_episode_title_is_show_season_episode(client, monitored):
    monitored.route(METADATA, FakeResponse(status_code=404, ok=False))
    body = playing(client, monitored, session(template=EPISODE_SESSION))
    assert body["title"] == "Severance - S1E2 - Good News About Hell"
    assert body["mediaType"] == "episode"


def test_episode_title_tolerates_missing_season_numbers(client, monitored):
    monitored.route(METADATA, FakeResponse(status_code=404, ok=False))
    episode = session(template=EPISODE_SESSION)
    del episode["parentIndex"]
    del episode["index"]
    body = playing(client, monitored, episode)
    assert body["title"] == "Severance - S?E? - Good News About Hell"


def test_episode_prefers_the_season_poster_over_the_episode_frame(client, monitored):
    """Episode thumbs are video frames, so the season/show poster is fetched."""
    monitored.route(
        METADATA,
        FakeResponse(
            plex_container(
                {
                    "thumb": "/library/metadata/200/thumb/season-art",
                    "parentThumb": "/library/metadata/199/thumb/show-art",
                }
            )
        ),
    )
    body = playing(client, monitored, session(template=EPISODE_SESSION))
    metadata_calls = monitored.calls_matching(METADATA)
    assert metadata_calls[0].url == BASE + "/library/metadata/200"
    assert "season-art" in body["poster"]


def test_episode_falls_back_to_the_show_poster(client, monitored):
    monitored.route(
        METADATA,
        FakeResponse(
            plex_container({"parentThumb": "/library/metadata/199/thumb/show-art"})
        ),
    )
    body = playing(client, monitored, session(template=EPISODE_SESSION))
    assert "show-art" in body["poster"]


def test_episode_falls_back_to_the_session_season_thumb(client, monitored):
    monitored.route(METADATA, FakeResponse(status_code=500, ok=False))
    body = playing(client, monitored, session(template=EPISODE_SESSION))
    assert "thumb%2Fseason" in body["poster"]


def test_episode_falls_back_to_the_session_show_thumb(client, monitored):
    monitored.route(METADATA, FakeResponse(status_code=500, ok=False))
    episode = session(template=EPISODE_SESSION)
    del episode["parentThumb"]
    body = playing(client, monitored, episode)
    assert "thumb%2Fshow" in body["poster"]


def test_episode_last_resort_is_the_episode_frame(client, monitored):
    monitored.route(METADATA, FakeResponse(status_code=500, ok=False))
    episode = session(template=EPISODE_SESSION)
    del episode["parentThumb"]
    del episode["grandparentThumb"]
    body = playing(client, monitored, episode)
    assert "episode-frame" in body["poster"]


def test_a_broken_metadata_lookup_does_not_fail_the_request(client, monitored):
    monitored.route(METADATA, ConnectionError("boom"))
    body = playing(client, monitored, session(template=EPISODE_SESSION))
    assert body["playing"] is True
    assert "thumb%2Fseason" in body["poster"]


def test_no_metadata_lookup_without_a_parent_rating_key(client, monitored):
    episode = session(template=EPISODE_SESSION)
    del episode["parentRatingKey"]
    body = playing(client, monitored, episode)
    assert monitored.calls_matching(METADATA) == []
    assert "thumb%2Fseason" in body["poster"]


# --------------------------------------------------------------------------
# Poster URL and TLS
# --------------------------------------------------------------------------
def test_poster_url_carries_the_insecure_flag(client, write_cfg, plex):
    write_cfg(
        plexUrl="https://plex.test:32400",
        plexToken="tok123",
        plexDevices=[DEVICE],
        sectionId=["1"],
        plexInsecure=True,
    )
    body = playing(client, plex, session())
    assert "insecure=1" in body["poster"]
    assert plex.calls[0].verify is False


def test_tls_is_verified_by_default(client, monitored):
    playing(client, monitored, session())
    assert monitored.calls[0].verify is True
    assert "insecure=0" in playing(client, monitored, session())["poster"]


# --------------------------------------------------------------------------
# Upstream failure
# --------------------------------------------------------------------------
def test_a_failed_sessions_request_is_reported_without_an_http_error(client, monitored):
    monitored.route(SESSIONS, FakeResponse(status_code=500, ok=False))
    body = client.get("/api/now-playing").get_json()
    assert body["playing"] is False
    assert body["error"] == "Sessions request failed: 500"


def test_an_unreachable_plex_is_reported_without_an_http_error(client, monitored):
    monitored.route(SESSIONS, ConnectionError("connection refused"))
    r = client.get("/api/now-playing")
    # Always 200 so the kiosk's poll loop treats it as "not playing".
    assert r.status_code == 200
    body = r.get_json()
    assert body["playing"] is False
    assert "Sessions check failed" in body["error"]
