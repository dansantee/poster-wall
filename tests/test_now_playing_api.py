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
# Music videos (musicVideoSectionId)
# --------------------------------------------------------------------------
MUSIC_VIDEO_SESSION = {
    "type": "movie",
    "subtype": "clip",
    "title": "Weezer - Buddy Holly",
    "year": 2026,
    "duration": 241186,
    "viewOffset": 60296,
    "ratingKey": "239589",
    "thumb": "/library/metadata/239589/thumb/1790221703",
    "librarySectionID": 8,
    "Player": {"address": DEVICE, "title": "Xbox"},
    "Media": [{"videoResolution": "1080", "videoCodec": "h264", "Part": []}],
}


@pytest.fixture
def music_monitored(write_cfg, plex):
    """Movies (1) in the rotation, Music Videos (8) as a music video library."""
    write_cfg(
        plexUrl=BASE,
        plexToken="tok123",
        plexDevices=[DEVICE],
        sectionId=["1"],
        musicVideoSectionId=["8"],
    )
    return plex


def test_music_video_library_is_reported_as_a_music_video(client, music_monitored):
    body = playing(client, music_monitored, session(template=MUSIC_VIDEO_SESSION))
    assert body["playing"] is True
    assert body["mediaType"] == "musicvideo"
    assert body["artist"] == "Weezer"
    assert body["trackTitle"] == "Buddy Holly"
    assert body["title"] == "Weezer - Buddy Holly"
    assert body["progress"] == 25.0


def test_music_video_art_is_the_item_poster(client, music_monitored):
    """The album-art sidecar becomes the Plex poster, so no metadata lookup is needed."""
    body = playing(client, music_monitored, session(template=MUSIC_VIDEO_SESSION))
    assert "239589%2Fthumb%2F1790221703" in body["poster"]
    assert music_monitored.calls_matching(METADATA) == []


def test_music_video_title_splits_on_the_first_separator_only(client, music_monitored):
    body = playing(
        client,
        music_monitored,
        session(template=MUSIC_VIDEO_SESSION, title="The Rookie - Daddy Cop - Part 2"),
    )
    assert body["artist"] == "The Rookie"
    assert body["trackTitle"] == "Daddy Cop - Part 2"


def test_music_video_without_a_separator_is_all_title(client, music_monitored):
    body = playing(client, music_monitored, session(template=MUSIC_VIDEO_SESSION, title="Intro"))
    assert body["artist"] == ""
    assert body["trackTitle"] == "Intro"


def test_music_video_library_does_not_need_to_be_in_section_id(client, music_monitored):
    body = playing(client, music_monitored, session(template=MUSIC_VIDEO_SESSION))
    assert body["playing"] is True


def test_music_video_library_is_off_by_default(client, monitored):
    body = playing(client, monitored, session(template=MUSIC_VIDEO_SESSION))
    assert body["playing"] is False


def test_a_scalar_music_video_section_is_accepted(client, write_cfg, plex):
    write_cfg(plexUrl=BASE, plexToken="tok123", plexDevices=[DEVICE], musicVideoSectionId=8)
    body = playing(client, plex, session(template=MUSIC_VIDEO_SESSION))
    assert body["mediaType"] == "musicvideo"


def test_movies_still_report_as_movies_alongside_music_videos(client, music_monitored):
    body = playing(client, music_monitored, session())
    assert body["mediaType"] == "movie"
    assert body["artist"] == ""
    assert body["trackTitle"] == ""


def test_the_rating_key_is_returned_so_the_kiosk_can_spot_a_track_change(client, music_monitored):
    body = playing(client, music_monitored, session(template=MUSIC_VIDEO_SESSION))
    assert body["ratingKey"] == "239589"


def test_movies_return_their_rating_key_too(client, monitored):
    assert playing(client, monitored, session())["ratingKey"] == "101"


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


def test_a_session_without_media_details_still_reports_playback(client, monitored):
    """An empty ``Media`` list used to raise IndexError and leave the wall in rotation."""
    body = playing(client, monitored, session(Media=[]))
    assert body["playing"] is True
    assert body["videoResolution"] == ""
    assert body["audioCodec"] == ""


# --------------------------------------------------------------------------
# Playback state and offset timing (for the kiosk's locally-driven progress bar)
# --------------------------------------------------------------------------
@pytest.mark.parametrize("state", ["playing", "paused", "buffering"])
def test_player_state_is_passed_through(client, monitored, state):
    body = playing(client, monitored, session(Player={"address": DEVICE, "title": "X", "state": state}))
    assert body["state"] == state


def test_state_defaults_to_playing_when_plex_omits_it(client, monitored):
    assert playing(client, monitored, session())["state"] == "playing"


def test_offset_at_is_the_current_time_in_milliseconds(client, monitored, proxy_app, monkeypatch):
    monkeypatch.setattr(proxy_app.time, "time", lambda: 1790000000.5)
    assert playing(client, monitored, session())["offsetAt"] == 1790000000500


# --------------------------------------------------------------------------
# The background monitor's view of the same logic
# --------------------------------------------------------------------------
def test_body_function_maps_every_session_to_whether_it_is_monitored(monitored, proxy_app, write_cfg):
    monitored.route(SESSIONS, FakeResponse(plex_container(
        session(sessionKey="7", Player={"address": "10.0.0.5"}),
        session(sessionKey="9"),
    )))
    body, seen = proxy_app.now_playing_body(proxy_app.load_cfg(), BASE, "tok123", True)
    assert body["playing"] is True
    assert seen == {"7": False, "9": True}


def test_monitor_fetch_raises_on_a_plex_error_so_the_last_state_is_kept(monitored, proxy_app):
    monitored.route(SESSIONS, FakeResponse(status_code=500, ok=False))
    with pytest.raises(RuntimeError):
        proxy_app.monitor_fetch(BASE, "tok123", True)


def test_monitor_settings_need_devices_url_and_token(write_cfg, proxy_app):
    write_cfg(plexUrl="plex.test:32400", plexToken="tok123")
    assert proxy_app.monitor_settings() is None
    write_cfg(plexUrl="plex.test:32400", plexToken="tok123", plexDevices=[DEVICE])
    assert proxy_app.monitor_settings() == ("http://plex.test:32400", "tok123", True)
    write_cfg(plexUrl="plex.test:32400", plexToken="tok123", plexDevices=[DEVICE], plexInsecure=True)
    assert proxy_app.monitor_settings()[2] is False


def test_player_id_is_returned_so_the_monitor_can_find_its_play_queue(client, monitored):
    body = playing(client, monitored, session(Player={"address": DEVICE, "title": "X", "machineIdentifier": "vqjl"}))
    assert body["playerId"] == "vqjl"


QUEUES = "/playQueues/"


def queue_items(*pairs, section=8):
    return {"MediaContainer": {"Metadata": [
        {"playQueueItemID": item_id, "title": title, "thumb": f"/library/metadata/{item_id}/thumb/1",
         "librarySectionID": section}
        for item_id, title in pairs]}}


def test_up_next_is_the_items_after_the_current_one(write_cfg, plex, proxy_app):
    write_cfg(musicVideoSectionId=["8"])
    plex.route(QUEUES, FakeResponse(queue_items(
        (100, "Savage Garden - Truly Madly Deeply"), (101, "Fiona Apple - Criminal"),
        (102, "Will Smith - Prince Ali"), (103, "Spice Girls - Say You'll Be There"), (104, "Duran Duran - Come Undone"))))
    items = proxy_app.monitor_queue(BASE, "tok123", True, "47799", "101")
    assert [i["trackTitle"] for i in items] == ["Prince Ali", "Say You'll Be There", "Come Undone"]
    assert items[0]["artist"] == "Will Smith"
    assert "w=400&h=400" in items[0]["poster"]
    call = plex.calls_matching(QUEUES)[0]
    assert call.url == BASE + "/playQueues/47799"
    assert call.params["includeBefore"] == 1, "the queue can lag one item behind while the next one buffers"
    assert call.timeout == 3.0, "the monitor thread waits on this lookup"


def test_up_next_items_carry_a_short_title(write_cfg, plex, proxy_app):
    write_cfg(musicVideoSectionId=["8"])
    plex.route(QUEUES, FakeResponse(queue_items(
        (1, "Weezer - Buddy Holly"), (2, "Shakira - Hips Don't Lie (featuring Wyclef Jean) ft. Wyclef Jean"))))
    item = proxy_app.monitor_queue(BASE, "tok123", True, "5", "1")[0]
    assert item["trackTitle"] == "Hips Don't Lie (featuring Wyclef Jean) ft. Wyclef Jean"
    assert item["shortTitle"] == "Hips Don't Lie"


@pytest.mark.parametrize("title,expected", [
    ("Cups (Pitch Perfect’s When I’m Gone) (Director's Cut)", "Cups"),
    ("Hips Don't Lie (featuring Wyclef Jean) ft. Wyclef Jean", "Hips Don't Lie"),
    ("Enemy (from the series Arcane League of Legends)", "Enemy"),
    ("Lean On (feat. MØ)", "Lean On"),
    ("Mood ft. iann dior", "Mood"),
    ("Titanium feat. Sia", "Titanium"),
    ("Smooth [Remastered]", "Smooth"),
    # Astra pass 1 #1: adjacent and nested groups go whole
    ("Cups (Pitch Perfect)(Official Video)", "Cups"),
    ("Song (Live [HD])", "Song"),
    ("Hold Me (Something) Tonight", "Hold Me Tonight"),
    # a leading group is part of the name, even with stray whitespace (Astra pass 1 #2)
    ("(Don't Fear) The Reaper", "(Don't Fear) The Reaper"),
    (" (Don't Fear) The Reaper", "(Don't Fear) The Reaper"),
    # left alone
    ("Hit Me Baby(One More Time)", "Hit Me Baby(One More Time)"),
    ("Left Behind", "Left Behind"),
    ("Daft Punk Is Playing At My House", "Daft Punk Is Playing At My House"),
    ("Featuring Nobody", "Featuring Nobody"),
    ("This Is What You Came For", "This Is What You Came For"),
    # nothing would be left: keep the title
    ("(Intro)", "(Intro)"),
    ("", ""),
    (None, ""),
])
def test_short_title(proxy_app, title, expected):
    assert proxy_app.short_title(title) == expected


def test_up_next_is_none_while_the_queue_has_not_caught_up(write_cfg, plex, proxy_app):
    """Plex moves the queue only once the new video starts; the caller retries later."""
    plex.route(QUEUES, FakeResponse(queue_items((100, "A - a"), (101, "B - b"))))
    assert proxy_app.monitor_queue(BASE, "tok123", True, "47799", "999") is None


def test_up_next_titles_outside_music_libraries_are_not_split(write_cfg, plex, proxy_app):
    write_cfg(musicVideoSectionId=["8"])
    plex.route(QUEUES, FakeResponse(queue_items((1, "Severance - S1E1"), (2, "Severance - S1E2"), section=3)))
    items = proxy_app.monitor_queue(BASE, "tok123", True, "5", "1")
    assert items == [{"ratingKey": "", "title": "Severance - S1E2", "artist": "", "trackTitle": "Severance - S1E2",
                      "shortTitle": "Severance - S1E2", "poster": items[0]["poster"]}]


def test_up_next_items_carry_their_rating_key(write_cfg, plex, proxy_app):
    """The kiosk animates the first up-next cover into place when that item starts, matched
    by ratingKey against the next session."""
    items = queue_items((1, "A - a"), (2, "B - b"))
    items["MediaContainer"]["Metadata"][1]["ratingKey"] = "239322"
    plex.route(QUEUES, FakeResponse(items))
    assert proxy_app.monitor_queue(BASE, "tok123", True, "5", "1")[0]["ratingKey"] == "239322"


def test_up_next_at_the_end_of_the_queue_is_empty(write_cfg, plex, proxy_app):
    plex.route(QUEUES, FakeResponse(queue_items((1, "A - a"))))
    assert proxy_app.monitor_queue(BASE, "tok123", True, "5", "1") == []


def test_a_failed_queue_request_raises(plex, proxy_app):
    plex.route(QUEUES, FakeResponse(status_code=404, ok=False))
    with pytest.raises(RuntimeError):
        proxy_app.monitor_queue(BASE, "tok123", True, "5", "1")


class _CachedMonitor:
    def __init__(self, body):
        self.body = body

    def snapshot(self):
        return self.body


def test_the_endpoint_answers_from_the_monitor_cache_without_calling_plex(client, monitored, proxy_app, monkeypatch):
    monkeypatch.setattr(proxy_app, "MONITOR", _CachedMonitor({"playing": True, "title": "cached"}))
    body = client.get("/api/now-playing").get_json()
    assert body == {"playing": True, "title": "cached"}
    assert monitored.calls == []


def test_a_stale_monitor_falls_back_to_asking_plex(client, monitored, proxy_app, monkeypatch):
    monkeypatch.setattr(proxy_app, "MONITOR", _CachedMonitor(None))
    assert playing(client, monitored, session())["title"] == "Arrival"


def test_the_monitor_cache_never_bypasses_the_devices_switch(client, write_cfg, plex, proxy_app, monkeypatch):
    write_cfg(plexUrl=BASE, plexToken="tok123")
    monkeypatch.setattr(proxy_app, "MONITOR", _CachedMonitor({"playing": True}))
    assert client.get("/api/now-playing").get_json()["playing"] is False


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
