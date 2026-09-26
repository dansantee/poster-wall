"""proxy/plex_events.py -- the push-driven now-playing monitor and its tiny websocket client.

Timings these tests encode were measured against the real Plex server with an Xbox
(2026-09-26): steady playback is reported every ~10 s, while pause, resume, seek, stop
and track changes arrive within ~1 s as websocket notifications.
"""
import json
import socket
import struct

import pytest

import plex_events
from plex_events import MiniWebSocket, NowPlayingMonitor, WebSocketClosed, notifications_url


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------
class FakeSocket:
    """Serves scripted chunks to recv(); a chunk of ``socket.timeout`` simulates idle."""

    def __init__(self, *chunks):
        self.chunks = list(chunks)
        self.sent = []
        self.timeout = None
        self.closed = False

    def settimeout(self, t):
        self.timeout = t

    def recv(self, _n):
        if not self.chunks:
            return b""
        item = self.chunks.pop(0)
        if item is socket.timeout:
            raise socket.timeout()
        return item

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True


def frame(payload, opcode=0x1, fin=True):
    if isinstance(payload, str):
        payload = payload.encode()
    head = bytes([(0x80 if fin else 0) | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack("!H", n)
    else:
        head += bytes([127]) + struct.pack("!Q", n)
    return head + payload


def unmask_client_frame(data):
    assert data[1] & 0x80, "client frames must be masked"
    n = data[1] & 0x7F
    mask, body = data[2:6], data[6:6 + n]
    return data[0] & 0x0F, bytes(c ^ mask[i % 4] for i, c in enumerate(body))


def playing_note(session_key, state="playing", offset=1000, rating_key="1"):
    return json.dumps({"NotificationContainer": {"type": "playing", "size": 1,
        "PlaySessionStateNotification": [{"sessionKey": session_key, "ratingKey": rating_key,
                                          "state": state, "viewOffset": offset}]}})


# --------------------------------------------------------------------------
# MiniWebSocket
# --------------------------------------------------------------------------
def test_reads_a_text_message():
    ws = MiniWebSocket(FakeSocket(frame("hello")))
    assert ws.recv_message(timeout=1) == "hello"


def test_reassembles_fragmented_messages_split_across_reads():
    data = frame("hel", fin=False) + frame("lo", opcode=0x0)
    ws = MiniWebSocket(FakeSocket(data[:3], data[3:]))
    assert ws.recv_message(timeout=1) == "hello"


def test_reads_the_16_bit_length_form():
    big = "x" * 300
    assert MiniWebSocket(FakeSocket(frame(big))).recv_message(timeout=1) == big


def test_answers_a_ping_with_a_masked_pong_and_keeps_reading():
    sock = FakeSocket(frame(b"abc", opcode=0x9) + frame("after"))
    assert MiniWebSocket(sock).recv_message(timeout=1) == "after"
    assert unmask_client_frame(sock.sent[0]) == (0xA, b"abc")


def test_idle_at_a_frame_boundary_returns_none():
    assert MiniWebSocket(FakeSocket(socket.timeout)).recv_message(timeout=1) is None


def test_a_timeout_mid_frame_is_a_broken_connection():
    sock = FakeSocket(frame("hello")[:3], socket.timeout)
    with pytest.raises(WebSocketClosed):
        MiniWebSocket(sock).recv_message(timeout=1)


def test_server_close_raises():
    with pytest.raises(WebSocketClosed):
        MiniWebSocket(FakeSocket(frame(b"\x03\xe8", opcode=0x8))).recv_message(timeout=1)


def test_eof_raises():
    with pytest.raises(WebSocketClosed):
        MiniWebSocket(FakeSocket()).recv_message(timeout=1)


def test_handshake_accepts_101_and_keeps_bytes_after_the_headers(monkeypatch):
    sock = FakeSocket(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n" + frame("first"))
    monkeypatch.setattr(plex_events.socket, "create_connection", lambda addr, timeout: sock)
    ws = MiniWebSocket.connect("ws://plex.test:32400/:/websockets/notifications?X-Plex-Token=t")
    request = sock.sent[0].decode()
    assert request.startswith("GET /:/websockets/notifications?X-Plex-Token=t HTTP/1.1\r\n")
    assert "Upgrade: websocket" in request and "Sec-WebSocket-Version: 13" in request
    assert ws.recv_message(timeout=1) == "first"


def test_handshake_refusal_raises(monkeypatch):
    sock = FakeSocket(b"HTTP/1.1 401 Unauthorized\r\n\r\n")
    monkeypatch.setattr(plex_events.socket, "create_connection", lambda addr, timeout: sock)
    with pytest.raises(ConnectionError):
        MiniWebSocket.connect("ws://plex.test:32400/x")


@pytest.mark.parametrize("base,expected", [
    ("http://192.168.1.3:32400", "ws://192.168.1.3:32400/:/websockets/notifications?X-Plex-Token=a%2Fb"),
    ("https://plex.example/", "wss://plex.example/:/websockets/notifications?X-Plex-Token=a%2Fb"),
])
def test_notifications_url(base, expected):
    assert notifications_url(base, "a/b") == expected


# --------------------------------------------------------------------------
# NowPlayingMonitor
# --------------------------------------------------------------------------
CFG = ("http://plex.test:32400", "tok", True)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


PONG = object()  # script marker: a control frame arrives (counts as liveness), no message


class ScriptedWS:
    """recv_message() returns scripted items; when they run out the monitor is stopped and
    the connection ends (so the script's last item is the last thing the monitor sees).

    ``None`` = idle for the whole timeout; ``PONG`` = a pong arrives, then idle; a string =
    a message arrives instantly; an Exception is raised.
    """

    def __init__(self, monitor, clock, *items):
        self.monitor, self.clock, self.items = monitor, clock, list(items)
        self.pings = 0
        self.closed = False
        self.frames_received = 0
        self.timeouts = []

    def recv_message(self, timeout=None):
        self.timeouts.append(timeout)
        if not self.items:
            self.monitor.stop()
            raise WebSocketClosed("script finished")
        item = self.items.pop(0)
        if item is None or item is PONG:
            self.clock.t += timeout
            if item is PONG:
                self.frames_received += 1
            return None
        if isinstance(item, Exception):
            raise item
        self.frames_received += 1
        return item

    def ping(self):
        self.pings += 1

    def close(self):
        self.closed = True


def make_monitor(bodies, sessions=None, settings=CFG, **kw):
    """A monitor whose fetch() pops ``bodies`` in order (repeating the last one)."""
    clock = Clock()
    calls = []

    def fetch(*cfg):
        calls.append(cfg)
        body = bodies.pop(0) if len(bodies) > 1 else bodies[0]
        if isinstance(body, Exception):
            raise body
        return body, dict(sessions or {"1": True, "2": False})

    m = NowPlayingMonitor(fetch=fetch, settings=(settings if callable(settings) else (lambda: settings)),
                          clock=clock, sleep=clock.sleep, **kw)
    return m, clock, calls


PLAYING = {"playing": True, "ratingKey": "5", "viewOffset": 1000, "state": "playing"}


def run_with(monitor, clock, *items):
    ws = ScriptedWS(monitor, clock, *items)
    monitor._connect = lambda url, verify: ws
    monitor.run()
    return ws


def test_connecting_refreshes_once_and_serves_the_result():
    m, clock, calls = make_monitor([PLAYING])
    ws = run_with(m, clock)
    assert calls == [CFG]
    assert m.snapshot()["viewOffset"] == 1000
    assert ws.closed


def test_notifications_for_monitored_sessions_trigger_a_refresh():
    m, clock, calls = make_monitor([PLAYING])
    run_with(m, clock, playing_note("1"), playing_note("1", state="paused"))
    assert len(calls) == 3


def test_notifications_for_other_peoples_sessions_are_ignored():
    m, clock, calls = make_monitor([PLAYING])
    run_with(m, clock, playing_note("2"), playing_note("2"))
    assert len(calls) == 1


def test_an_unknown_session_triggers_a_refresh_to_learn_about_it():
    m, clock, calls = make_monitor([PLAYING])
    run_with(m, clock, playing_note("77"))
    assert len(calls) == 2


def test_non_playback_notifications_are_ignored():
    m, clock, calls = make_monitor([PLAYING])
    run_with(m, clock, json.dumps({"NotificationContainer": {"type": "timeline"}}), "not json")
    assert len(calls) == 1


def test_idle_triggers_a_safety_refresh_and_a_keepalive_ping():
    m, clock, calls = make_monitor([PLAYING], safety_poll=30)
    ws = run_with(m, clock, None)
    assert len(calls) == 2
    assert ws.pings == 1


def test_offset_at_keeps_the_first_seen_time_while_plex_repeats_the_offset():
    later = dict(PLAYING, viewOffset=11000)
    m, clock, calls = make_monitor([PLAYING, dict(PLAYING), later])
    ws = ScriptedWS(m, clock, playing_note("1"), playing_note("1"))
    m._connect = lambda url, verify: ws
    first_seen = int(clock.t * 1000)

    stamps = []
    real_refresh = m.refresh

    def recording_refresh(cfg):
        real_refresh(cfg)
        stamps.append(m.snapshot()["offsetAt"])
        clock.t += 4  # time passes between notifications

    m.refresh = recording_refresh
    m.run()
    assert stamps[0] == first_seen
    assert stamps[1] == first_seen, "an unchanged offset must keep its original timestamp"
    assert stamps[2] == first_seen + 8000, "a new offset is stamped when it arrives"


def test_a_state_change_restamps_the_offset():
    paused = dict(PLAYING, state="paused")
    m, clock, _ = make_monitor([PLAYING, paused])
    m.refresh(CFG)
    clock.t += 5
    m.refresh(CFG)
    assert m.snapshot()["offsetAt"] == int(clock.t * 1000)


def test_a_failed_refresh_keeps_the_last_good_state():
    m, clock, _ = make_monitor([PLAYING, RuntimeError("plex down")])
    m._safe_refresh(CFG)
    m._safe_refresh(CFG)
    assert m.snapshot()["viewOffset"] == 1000
    assert "plex down" in m.last_error


def test_snapshot_expires_after_max_age():
    m, clock, _ = make_monitor([PLAYING], max_age=45)
    m.refresh(CFG)
    clock.t += 46
    assert m.snapshot() is None


def test_a_connect_failure_falls_back_to_polling():
    m, clock, calls = make_monitor([PLAYING], fallback_poll=2, reconnect_after=10)

    def refuse(url, verify):
        m.stop() if len(calls) >= 5 else None
        raise ConnectionError("refused")

    m._connect = refuse
    m.run()
    assert m.mode == "poll"
    assert len(calls) == 5, "one refresh every fallback_poll seconds for reconnect_after seconds"
    assert "refused" in m.last_error


def test_a_dropped_socket_polls_briefly_then_reconnects():
    m, clock, calls = make_monitor([PLAYING], fallback_poll=2)
    connects = []

    def connect(url, verify):
        connects.append(url)
        if len(connects) == 1:
            return ScriptedWS(m, clock, WebSocketClosed("gone"))
        return ScriptedWS(m, clock)

    m._connect = connect
    m.run()
    assert len(connects) == 2


def test_a_triggered_refresh_is_followed_by_one_more_in_case_sessions_lagged():
    """Astra pass 1 #2: the notification can arrive before /status/sessions shows the change."""
    m, clock, calls = make_monitor([PLAYING], followup_delay=1.5)
    ws = run_with(m, clock, playing_note("1"), None)
    assert len(calls) == 3, "connect, the notification, and the follow-up"
    assert ws.timeouts[1] == pytest.approx(1.5), "the wait is cut short for the follow-up"


def test_ignored_notifications_cannot_postpone_the_safety_refresh():
    """Astra pass 1 #4: the wait is capped at the next deadline, not restarted per message."""
    m, clock, calls = make_monitor([PLAYING], safety_poll=30)

    def other_persons_note_every_10s():
        clock.t += 10
        return playing_note("2")

    ws = ScriptedWS(m, clock)
    notes = [other_persons_note_every_10s for _ in range(3)]

    def recv(timeout=None):
        ws.timeouts.append(timeout)
        if notes:
            return notes.pop(0)()
        m.stop()
        raise WebSocketClosed("done")

    ws.recv_message = recv
    m._connect = lambda url, verify: ws
    m.run()
    assert ws.timeouts[:3] == [pytest.approx(30), pytest.approx(20), pytest.approx(10)]
    assert len(calls) == 2, "the safety refresh ran at 30 s despite three ignored notifications"


def test_no_frame_after_a_keepalive_ping_means_the_socket_is_dead():
    """Astra pass 1 #5: a silently broken socket must fall back instead of staying in push."""
    m, clock, calls = make_monitor([PLAYING], safety_poll=30, pong_timeout=10)
    connects = []

    def connect(url, verify):
        connects.append(url)
        if len(connects) == 1:
            return ScriptedWS(m, clock, None, None)  # idle -> ping, then silence
        return ScriptedWS(m, clock)

    m._connect = connect
    m.run()
    assert len(connects) == 2, "the dead socket was replaced"


def test_a_pong_keeps_the_socket_alive():
    m, clock, calls = make_monitor([PLAYING], safety_poll=30, pong_timeout=10)
    connects = []

    def connect(url, verify):
        connects.append(url)
        return ScriptedWS(m, clock, None, PONG, None)

    m._connect = connect
    m.run()
    assert len(connects) == 1


def test_a_failing_plex_is_not_hammered_while_connected():
    """Deadlines key off the last attempt, so failed refreshes don't spin the loop."""
    m, clock, calls = make_monitor([RuntimeError("plex down")], safety_poll=30, pong_timeout=10)
    ws = run_with(m, clock, None, PONG)
    assert len(calls) == 2, "connect + the 30 s safety attempt, not a tight retry loop"
    assert all(t >= 1 for t in ws.timeouts)


def test_the_default_connect_uses_a_short_timeout(monkeypatch):
    """Astra pass 1 #3: a hanging upgrade must not hold up fallback polling for long."""
    seen = {}

    def fake_connect(url, timeout=None, verify=True):
        seen["timeout"] = timeout
        raise ConnectionError("x")

    monkeypatch.setattr(plex_events.MiniWebSocket, "connect", staticmethod(fake_connect))
    m = NowPlayingMonitor(fetch=lambda *a: ({}, {}), settings=lambda: CFG)
    with pytest.raises(ConnectionError):
        m._connect("ws://x/", True)
    assert seen["timeout"] == 3.0


class DripSocket(FakeSocket):
    """Each chunk arrives ``gap`` simulated seconds after the previous read. A read whose
    timeout is shorter than the gap raises socket.timeout, like a real socket."""

    def __init__(self, clock, gap, *chunks):
        super().__init__(*chunks)
        self.clock, self.gap = clock, gap

    def recv(self, n):
        if self.timeout is not None and self.gap > self.timeout:
            self.clock.t += self.timeout
            raise socket.timeout()
        self.clock.t += self.gap
        return super().recv(n)


@pytest.fixture
def mono(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(plex_events.time, "monotonic", clock)
    return clock


def test_the_handshake_timeout_bounds_the_whole_upgrade(monkeypatch, mono):
    """Astra pass 2 #3: a reply dripping in under the per-read timeout must still time out."""
    reply = b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
    chunks = [reply[i:i + 8] for i in range(0, len(reply), 8)]
    sock = DripSocket(mono, 2.0, *chunks)
    monkeypatch.setattr(plex_events.socket, "create_connection", lambda addr, timeout: sock)
    start = mono.t
    with pytest.raises(WebSocketClosed):
        MiniWebSocket.connect("ws://plex.test:32400/x", timeout=3.0)
    assert mono.t - start <= 3.0 + 1e-9


def test_control_frames_do_not_extend_the_receive_deadline(mono):
    """Astra pass 2 #4: pings every 10 s must not keep a 30 s wait from ever returning."""
    pings = [frame(b"p", opcode=0x9) for _ in range(6)]
    sock = DripSocket(mono, 10.0, *pings)
    ws = MiniWebSocket(sock)
    start = mono.t
    assert ws.recv_message(timeout=30) is None
    assert mono.t - start == pytest.approx(30)
    assert len(sock.sent) == 3, "each ping inside the window was still answered"


def test_a_frame_that_has_started_gets_time_to_finish(mono):
    data = frame("hello")
    sock = DripSocket(mono, 0.5, data[:2], data[2:])
    ws = MiniWebSocket(sock)
    assert ws.recv_message(timeout=0.6) == "hello"


def test_frames_received_counts_control_frames():
    ws = MiniWebSocket(FakeSocket(frame(b"", opcode=0xA) + frame("hi")))
    assert ws.recv_message(timeout=1) == "hi"
    assert ws.frames_received == 2


# --------------------------------------------------------------------------
# Up next (play queue)
# --------------------------------------------------------------------------
XBOX = "vqjl5hn59g1c4sdloc5tetxq"
PLAYING_XBOX = dict(PLAYING, playerId=XBOX)


def note_dict(item_id, rating_key="5", state="playing", session_key="1", queue_id=47799):
    return {"sessionKey": session_key, "clientIdentifier": XBOX, "ratingKey": rating_key, "state": state,
            "viewOffset": 0, "playQueueID": queue_id, "playQueueItemID": item_id}


def queue_note(item_id, rating_key="5", **kw):
    return json.dumps({"NotificationContainer": {"type": "playing",
                                                 "PlaySessionStateNotification": [note_dict(item_id, rating_key, **kw)]}})


def with_queue(queue_results):
    """A queue_fetch that pops results in order and records its calls."""
    calls = []

    def queue_fetch(base, token, verify, queue_id, item_id):
        calls.append((queue_id, item_id))
        result = queue_results.pop(0) if len(queue_results) > 1 else queue_results[0]
        if isinstance(result, Exception):
            raise result
        return result

    return queue_fetch, calls


UP = [{"title": "Fiona Apple - Criminal"}, {"title": "Sublime - Santeria"}]


def test_notifications_record_each_players_queue_and_refresh_attaches_up_next():
    queue_fetch, qcalls = with_queue([UP])
    m, clock, calls = make_monitor([PLAYING_XBOX], queue_fetch=queue_fetch)
    m.wants_refresh(queue_note(1963398))
    m.refresh(CFG)
    assert m.snapshot()["upNext"] == UP
    assert qcalls == [("47799", "1963398")]


def test_up_next_is_fetched_once_per_queue_item():
    queue_fetch, qcalls = with_queue([UP])
    m, clock, calls = make_monitor([PLAYING_XBOX], queue_fetch=queue_fetch)
    m.wants_refresh(queue_note(1963398))
    m.refresh(CFG)
    m.refresh(CFG)
    m.wants_refresh(queue_note(1963399))
    m.refresh(CFG)
    assert qcalls == [("47799", "1963398"), ("47799", "1963399")]


def test_a_lagging_queue_is_not_cached_and_the_row_is_kept_for_the_same_item():
    queue_fetch, qcalls = with_queue([UP, None, UP[:1]])
    m, clock, calls = make_monitor([PLAYING_XBOX], queue_fetch=queue_fetch)
    m.wants_refresh(queue_note(1))
    m.refresh(CFG)
    m.wants_refresh(queue_note(2))                   # same ratingKey in PLAYING_XBOX
    m.refresh(CFG)
    assert m.snapshot()["upNext"] == UP, "a lookup that can't place the item keeps the old row"
    m.refresh(CFG)
    assert m.snapshot()["upNext"] == UP[:1], "the None was not cached, so it was retried"
    assert qcalls == [("47799", "1"), ("47799", "2"), ("47799", "2")]


def test_a_lagging_queue_for_a_new_item_shows_no_row_rather_than_the_old_one():
    queue_fetch, _ = with_queue([UP, None])
    m, clock, calls = make_monitor([PLAYING_XBOX, dict(PLAYING_XBOX, ratingKey="6")], queue_fetch=queue_fetch)
    m.wants_refresh(queue_note(1))
    m.refresh(CFG)
    m.wants_refresh(queue_note(2, rating_key="6"))
    m.refresh(CFG)
    assert m.snapshot()["upNext"] == []


def test_state_is_published_before_a_slow_queue_lookup():
    """Astra pass 1 #1: the next item's playing state must not wait on /playQueues."""
    seen_during_lookup = []
    m, clock, calls = make_monitor([PLAYING_XBOX])

    def slow_queue_fetch(base, token, verify, queue_id, item_id):
        seen_during_lookup.append(m.snapshot())
        return UP

    m._queue_fetch = slow_queue_fetch
    m.wants_refresh(queue_note(1))
    m.refresh(CFG)
    assert seen_during_lookup[0]["playing"] is True, "already published while the lookup ran"
    assert seen_during_lookup[0]["upNext"] == []
    assert m.snapshot()["upNext"] == UP, "then patched in"


def test_every_notification_in_a_batch_is_recorded():
    """Astra pass 1 #2: a batch of A then B leaves the player at B."""
    queue_fetch, qcalls = with_queue([UP])
    m, clock, calls = make_monitor([dict(PLAYING_XBOX, ratingKey="B")], queue_fetch=queue_fetch)
    batch = json.dumps({"NotificationContainer": {"type": "playing", "PlaySessionStateNotification": [
        note_dict(1, "A"), note_dict(2, "B")]}})
    assert m.wants_refresh(batch) is True
    m.refresh(CFG)
    assert qcalls == [("47799", "2")]


def test_a_queue_position_for_a_different_item_is_not_used():
    """Astra pass 1 #3: after a change seen only by polling, the stored position is stale."""
    queue_fetch, qcalls = with_queue([UP])
    m, clock, calls = make_monitor([PLAYING_XBOX, dict(PLAYING_XBOX, ratingKey="6")], queue_fetch=queue_fetch)
    m.wants_refresh(queue_note(1))
    m.refresh(CFG)
    assert m.snapshot()["upNext"] == UP
    m.refresh(CFG)                                   # sessions moved on to ratingKey 6, no notification yet
    assert m.snapshot()["upNext"] == [], "no stale list listing the new item as its own next"
    assert qcalls == [("47799", "1")], "and no lookup with the stale position"


def test_no_queue_known_yet_means_no_up_next_and_no_lookup():
    queue_fetch, qcalls = with_queue([UP])
    m, clock, calls = make_monitor([PLAYING_XBOX], queue_fetch=queue_fetch)
    m.refresh(CFG)
    assert m.snapshot()["upNext"] == []
    assert qcalls == []


def test_a_failing_queue_lookup_is_recorded_and_does_not_break_the_refresh():
    queue_fetch, _ = with_queue([RuntimeError("queue 404")])
    m, clock, calls = make_monitor([PLAYING_XBOX], queue_fetch=queue_fetch)
    m.wants_refresh(queue_note(1))
    m.refresh(CFG)
    assert m.snapshot()["playing"] is True
    assert m.snapshot()["upNext"] == []
    assert "queue 404" in m.last_error


def test_nothing_to_watch_means_idle_and_no_cache():
    m, clock, calls = make_monitor([PLAYING], settings=None)
    m._sleep = lambda s: m.stop()
    m.run()
    assert m.mode == "idle"
    assert m.snapshot() is None
    assert calls == []


def test_changed_settings_reconnect():
    settings = [CFG]
    m, clock, calls = make_monitor([PLAYING], settings=lambda: settings[0])
    connects = []

    def connect(url, verify):
        connects.append(url)
        if len(connects) == 1:
            settings[0] = ("http://other:32400", "tok", True)
        return ScriptedWS(m, clock, playing_note("1"))

    m._connect = connect
    m.run()
    assert connects[0].startswith("ws://plex.test:32400/")
    assert connects[1].startswith("ws://other:32400/")
