"""Push-driven "now playing" state for the proxy.

Plex announces every play / pause / seek / stop, plus each client's ~10 s progress report,
on a websocket (``/:/websockets/notifications``). The monitor here keeps that socket open in
a background thread and treats each relevant notification as a *trigger* to refresh from
``/status/sessions``. The existing device and library filtering therefore stays the single
source of truth, and the full session list is only fetched when something changes. The
kiosk's 1 s polls are answered from the cached result, without touching Plex.

Measured on the LAN (2026-09-26): ``/status/sessions`` is ~3-5 KB per active session and
the notifications are a few hundred bytes, so push is 50-100x less traffic than a 1 s poll.

If the socket cannot be opened or drops, the monitor polls every ``fallback_poll`` seconds
until it reconnects, and while connected it still does a slow safety refresh every
``safety_poll`` seconds in case a notification is missed.

The websocket client is the small subset of RFC 6455 this needs, on the standard library,
so the Pi's venv needs no new package.
"""
import base64
import json
import os
import socket
import ssl
import struct
import threading
import time
from urllib.parse import quote, urlsplit


class WebSocketClosed(Exception):
    """The connection ended, or broke mid-frame."""


class MiniWebSocket:
    """Reads text messages (with fragmentation), answers pings and handles close.

    Client-to-server frames are masked as the RFC requires. ``recv_message`` returns
    ``None`` when nothing arrives within its timeout at a frame boundary, which the
    monitor uses as its idle tick.
    """

    MID_FRAME_TIMEOUT = 5.0

    def __init__(self, sock):
        self.sock = sock
        self._buf = b""
        self.frames_received = 0  # every frame, pongs included: the monitor's liveness signal

    @classmethod
    def connect(cls, url, timeout=10.0, verify=True):
        parts = urlsplit(url)
        secure = parts.scheme in ("wss", "https")
        host = parts.hostname
        port = parts.port or (443 if secure else 80)
        raw = socket.create_connection((host, port), timeout=timeout)
        if secure:
            ctx = ssl.create_default_context()
            if not verify:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            raw = ctx.wrap_socket(raw, server_hostname=host)
        path = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        raw.sendall(
            (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "\r\n"
            ).encode("ascii")
        )
        ws = cls(raw)
        # `timeout` bounds the whole handshake, not each read: a reply dripping in under the
        # per-read timeout must not hold the caller up for longer (Astra pass 2 #3).
        head = ws._read_until(b"\r\n\r\n", deadline=time.monotonic() + timeout)
        status = head.split(b"\r\n", 1)[0].split()
        if len(status) < 2 or status[1] != b"101":
            raw.close()
            raise ConnectionError(
                "websocket upgrade refused: " + head.split(b"\r\n", 1)[0].decode("latin-1")
            )
        return ws

    # -- reading ---------------------------------------------------------------------------
    def _fill(self, at_boundary):
        try:
            chunk = self.sock.recv(65536)
        except socket.timeout:
            if at_boundary and not self._buf:
                raise
            raise WebSocketClosed("timed out mid-frame")
        except OSError as e:
            raise WebSocketClosed(str(e))
        if not chunk:
            raise WebSocketClosed("connection closed by server")
        self._buf += chunk

    def _read_exact(self, n, at_boundary=False):
        while len(self._buf) < n:
            self._fill(at_boundary and not self._buf)
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _read_until(self, delimiter, deadline):
        while delimiter not in self._buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WebSocketClosed("handshake timed out")
            self.sock.settimeout(remaining)
            self._fill(False)
        head, self._buf = self._buf.split(delimiter, 1)
        return head

    def recv_message(self, timeout=None):
        """Next text message as ``str``, or ``None`` if none completes within ``timeout`` seconds.

        ``timeout`` is an overall deadline: control frames (pings, pongs) arriving in between
        do not restart it (Astra pass 2 #4). Once a frame has started, the rest of it gets at
        least MID_FRAME_TIMEOUT to arrive.
        """
        end = None if timeout is None else time.monotonic() + timeout
        fragments = []
        while True:
            if end is None:
                self.sock.settimeout(None)
            else:
                remaining = end - time.monotonic()
                if remaining <= 0 and not fragments and not self._buf:
                    return None
                self.sock.settimeout(max(0.01, remaining) if not fragments
                                     else max(self.MID_FRAME_TIMEOUT, remaining))
            try:
                b1, b2 = self._read_exact(2, at_boundary=not fragments)
            except socket.timeout:
                return None
            if end is not None:
                self.sock.settimeout(max(self.MID_FRAME_TIMEOUT, end - time.monotonic()))
            self.frames_received += 1
            opcode = b1 & 0x0F
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if b2 & 0x80 else None
            payload = self._read_exact(length)
            if mask:
                payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
            if opcode == 0x8:  # close
                try:
                    self._send(0x8, payload[:2])
                except OSError:
                    pass
                raise WebSocketClosed("server sent close")
            if opcode == 0x9:  # ping
                self._send(0xA, payload)
                continue
            if opcode == 0xA:  # pong
                continue
            fragments.append(payload)
            if b1 & 0x80:  # FIN
                return b"".join(fragments).decode("utf-8", "replace")

    # -- writing ---------------------------------------------------------------------------
    def _send(self, opcode, payload=b""):
        header = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", n)
        mask = os.urandom(4)
        masked = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def ping(self):
        self._send(0x9, b"pw")

    def close(self):
        try:
            self._send(0x8, struct.pack("!H", 1000))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def notifications_url(base, token):
    """ws(s):// URL of Plex's notification stream for an http(s):// server base."""
    scheme = "wss" if base.startswith("https://") else "ws"
    host = base.split("://", 1)[1].rstrip("/")
    return f"{scheme}://{host}/:/websockets/notifications?X-Plex-Token={quote(token, safe='')}"


class NowPlayingMonitor:
    """Keeps the latest /api/now-playing body, refreshed on Plex push notifications.

    ``settings()`` returns ``(base, token, verify_tls)``, or ``None`` when there is nothing
    to watch (no devices, no Plex URL or no token). ``fetch(base, token, verify)`` returns
    ``(body, sessions)``, where ``sessions`` maps each current Plex ``sessionKey`` to whether
    it is on a monitored device. It raises on failure, and the previous state is then kept.
    """

    def __init__(self, fetch, settings, connect=None, safety_poll=30.0, fallback_poll=2.0,
                 reconnect_after=10.0, max_age=45.0, followup_delay=1.5, pong_timeout=10.0,
                 connect_timeout=3.0, clock=time.time, sleep=time.sleep):
        self._fetch = fetch
        self._settings = settings
        # A short connect timeout bounds how long a hanging upgrade can hold up fallback polling.
        self._connect = connect or (lambda url, verify: MiniWebSocket.connect(
            url, timeout=connect_timeout, verify=verify))
        self.safety_poll = safety_poll
        self.fallback_poll = fallback_poll
        self.reconnect_after = reconnect_after
        self.max_age = max_age
        # A notification can beat /status/sessions to the new state, so every triggered refresh
        # is followed by one more after this delay.
        self.followup_delay = followup_delay
        # With no frame at all within this long of a keepalive ping, the socket is dead.
        self.pong_timeout = pong_timeout
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._body = None
        self._updated = 0.0
        self._attempted = 0.0
        self._sessions = {}
        self.mode = "starting"   # starting | idle | push | poll
        self.last_error = None

    # -- state -----------------------------------------------------------------------------
    def snapshot(self):
        """The cached body, or ``None`` if there is none or it is older than ``max_age``."""
        with self._lock:
            if self._body is None or self._clock() - self._updated > self.max_age:
                return None
            return dict(self._body)

    def refresh(self, cfg):
        body, sessions = self._fetch(*cfg)
        body = dict(body)
        now_ms = int(self._clock() * 1000)
        with self._lock:
            prev = self._body
            unchanged = bool(
                prev and prev.get("playing") and body.get("playing")
                and all(prev.get(k) == body.get(k) for k in ("ratingKey", "viewOffset", "state"))
                and "offsetAt" in prev
            )
            # offsetAt is when this viewOffset was first seen. Plex repeats a stale offset
            # between the client's 10 s reports, so an unchanged offset keeps its original
            # time, and the kiosk extrapolates from the moment the report actually arrived.
            body["offsetAt"] = prev["offsetAt"] if unchanged else now_ms
            self._body = body
            self._updated = self._clock()
            self._sessions = dict(sessions)

    def _safe_refresh(self, cfg):
        self._attempted = self._clock()  # deadlines key off attempts, so a failing Plex is not hammered
        try:
            self.refresh(cfg)
        except Exception as e:  # keep the last good state; snapshot() ages it out
            self.last_error = f"refresh: {e}"

    def wants_refresh(self, message):
        """True for a playback notification that concerns a monitored or unknown session."""
        try:
            container = json.loads(message).get("NotificationContainer") or {}
        except (ValueError, AttributeError):
            return False
        if container.get("type") != "playing":
            return False
        for note in container.get("PlaySessionStateNotification") or []:
            key = str(note.get("sessionKey", ""))
            with self._lock:
                known = key in self._sessions
                monitored = self._sessions.get(key, False)
            if not known or monitored:
                return True
        return False

    # -- loop ------------------------------------------------------------------------------
    def stop(self):
        self._stop.set()

    def start(self):
        t = threading.Thread(target=self.run, name="now-playing-monitor", daemon=True)
        t.start()
        return t

    def run(self):
        while not self._stop.is_set():
            cfg = self._settings()
            if cfg is None:
                self.mode = "idle"
                with self._lock:
                    self._body = None
                self._sleep(self.safety_poll)
                continue
            try:
                ws = self._connect(notifications_url(cfg[0], cfg[1]), cfg[2])
            except Exception as e:
                self.last_error = f"connect: {e}"
                self._poll_for(cfg, self.reconnect_after)
                continue
            self.mode = "push"
            try:
                self._safe_refresh(cfg)
                self._listen(ws, cfg)
            except Exception as e:
                self.last_error = f"socket: {e}"
            finally:
                ws.close()
            if not self._stop.is_set():
                self._poll_for(cfg, self.fallback_poll)

    def _listen(self, ws, cfg):
        followup_at = None     # one more refresh shortly after each triggered one
        ping_sent_at = None    # outstanding keepalive, with the frame count when it was sent
        frames_at_ping = 0
        while not self._stop.is_set():
            if self._settings() != cfg:
                return  # Plex URL, token or TLS setting changed: reconnect with the new ones
            # Wait no longer than the next thing that must happen, so a stream of ignored
            # notifications can never push the safety refresh (or a follow-up) back.
            deadlines = [self._attempted + self.safety_poll]
            if followup_at is not None:
                deadlines.append(followup_at)
            if ping_sent_at is not None:
                deadlines.append(ping_sent_at + self.pong_timeout)
            message = ws.recv_message(timeout=max(0.05, min(deadlines) - self._clock()))
            now = self._clock()

            if ping_sent_at is not None and ws.frames_received > frames_at_ping:
                ping_sent_at = None
            if ping_sent_at is not None and now - ping_sent_at >= self.pong_timeout:
                raise WebSocketClosed("no frame within %ss of a keepalive ping" % self.pong_timeout)

            if message is not None and self.wants_refresh(message):
                self._safe_refresh(cfg)
                followup_at = self._clock() + self.followup_delay
            elif followup_at is not None and now >= followup_at:
                self._safe_refresh(cfg)
                followup_at = None
            elif now - self._attempted >= self.safety_poll:
                self._safe_refresh(cfg)
                if ping_sent_at is None:
                    ws.ping()
                    ping_sent_at, frames_at_ping = self._clock(), ws.frames_received

    def _poll_for(self, cfg, seconds):
        self.mode = "poll"
        end = self._clock() + seconds
        while not self._stop.is_set() and self._clock() < end:
            self._safe_refresh(cfg)
            self._sleep(self.fallback_poll)
