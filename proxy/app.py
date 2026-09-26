#!/usr/bin/env python3
from flask import Flask, jsonify, request, Response
import os, json, pathlib, re, requests, urllib3, subprocess, socket, random, time
from urllib.parse import quote_plus

import plex_events

app = Flask(__name__)

# ---- Config / constants ----
DEFAULT_SECTION = os.environ.get('SECTION_ID', '1')
TIMEOUT = float(os.environ.get('TIMEOUT', '10'))
QUEUE_TIMEOUT = 3.0  # play-queue lookups for "up next" run on the monitor thread
ALLOW_INSECURE_DEFAULT = os.environ.get('ALLOW_INSECURE','').strip().lower() in ('1','true','yes','on')

# Optional server-wide token (client may also send a token)
SERVER_TOKEN = os.environ.get('PLEX_TOKEN','').strip()

# Server-managed config file + admin key
CFG_PATH = pathlib.Path(os.environ.get("PW_CONFIG_PATH", "config.json"))
ADMIN_KEY = os.environ.get("PW_ADMIN_KEY", "").strip()
REPO_DIR = pathlib.Path(__file__).resolve().parents[1]

PLEX_HEADERS = {
    'Accept': 'application/json',
    'X-Plex-Client-Identifier': 'poster-wall-proxy',
    'X-Plex-Product': 'Poster Wall',
    'X-Plex-Version': '1.0',
    'X-Plex-Platform': 'Python',
    'X-Plex-Device': 'Proxy',
}

# ---- Helpers ----
def load_cfg():
    if CFG_PATH.exists():
        with CFG_PATH.open("r", encoding="utf-8") as f:
            try:
                return json.load(f) or {}
            except Exception:
                return {}
    return {}

def save_cfg(obj):
    CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CFG_PATH.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)

def resolve_base(req):
    srv = load_cfg()
    base = (
        os.environ.get('PLEX_URL', '') or
        req.headers.get('X-Plex-Url') or
        req.args.get('url') or
        srv.get('plexUrl', '')
    ).strip().rstrip('/')
    if not base:
        raise ValueError("No Plex URL provided. Set PLEX_URL, save plexUrl in server config, or send X-Plex-Url header.")
    if not (base.startswith('http://') or base.startswith('https://')):
        base = 'http://' + base
    return base

def token_from(req):
    srv = load_cfg()
    return (SERVER_TOKEN or
            (req.headers.get('X-Plex-Token') or req.args.get('token') or '').strip() or
            srv.get('plexToken','').strip())

def insecure_from(req):
    # Header or query (per-request) OR server default OR env default
    srv = load_cfg()
    hdr = (req.headers.get('X-Allow-Insecure') or req.args.get('insecure') or '').strip().lower()
    if hdr in ('1','true','yes','on'):
        return True
    if hdr in ('0','false','no','off'):
        return False
    return bool(srv.get('plexInsecure')) or ALLOW_INSECURE_DEFAULT

def build_info():
    try:
        commit = subprocess.run(
            ["git", "-C", str(REPO_DIR), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True
        ).stdout.strip()
        short_commit = subprocess.run(
            ["git", "-C", str(REPO_DIR), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(REPO_DIR), "status", "--short"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True
        ).stdout.strip()
        return {
            "commit": commit,
            "shortCommit": short_commit,
            "dirty": bool(status),
            "status": status.splitlines() if status else []
        }
    except Exception as e:
        return {
            "error": str(e),
            "dirty": None,
            "status": []
        }

# ---- CORS ----
@app.after_request
def add_cors(resp):
    resp.headers['Access-Control-Allow-Origin']  = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, PUT, POST, OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Plex-Token, X-Plex-Url, X-Allow-Insecure, X-Admin-Key'
    return resp

# ---- Plex metadata helpers ----
def get_season_poster(base, token, rating_key, verify_tls=True):
    """Fetch season metadata to get its poster. Returns tuple (thumb, grandparentThumb)."""
    try:
        # First try to get the season's metadata (it has the parent show too)
        url = f"{base}/library/metadata/{rating_key}"
        r = requests.get(url, params={'X-Plex-Token': token}, 
                        headers=PLEX_HEADERS, timeout=TIMEOUT, verify=verify_tls)
        if not r.ok:
            return None, None
            
        data = r.json()
        item = data.get('MediaContainer', {}).get('Metadata', [{}])[0]
        
        # Return (season poster, show poster) so caller can choose
        return item.get('thumb'), item.get('parentThumb')
    except:
        return None, None

# ---- Health ----
@app.route('/api/ping', methods=['GET','OPTIONS'])
def ping():
    if request.method == 'OPTIONS': return ('',204)
    return 'pong',200

@app.route('/api/build-info', methods=['GET','OPTIONS'])
def api_build_info():
    if request.method == 'OPTIONS': return ('',204)
    info = build_info()
    info["hostname"] = socket.gethostname()
    return jsonify(info)

# ---- Server config (GET/PUT) ----
@app.route("/api/config", methods=["GET", "OPTIONS"])
def cfg_get():
    if request.method == "OPTIONS": return ("", 204)
    config = load_cfg() or {}  # Ensure we always have a dict
    # Always add hostname to config for client use
    config['hostname'] = socket.gethostname()
    
    # Add default transition settings if not present
    config.setdefault('posterTransitions', False)
    config.setdefault('transitionTypes', ['crossfade'])  # Array of selected transitions
    
    return jsonify(config)

@app.route("/api/config", methods=["PUT", "OPTIONS"])
def cfg_put():
    if request.method == "OPTIONS": return ("", 204)
    if ADMIN_KEY and request.headers.get("X-Admin-Key","") != ADMIN_KEY:
        return jsonify({"error":"forbidden"}), 403
    try:
        cfg = request.get_json(force=True)
        if not isinstance(cfg, dict):
            return jsonify({"error":"invalid body"}), 400
        # minimal normalization
        if 'plexUrl' in cfg and isinstance(cfg['plexUrl'], str) and cfg['plexUrl'] and not cfg['plexUrl'].startswith(('http://','https://')):
            cfg['plexUrl'] = 'http://' + cfg['plexUrl']
        save_cfg(cfg)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

# ---- Movies (paged) ----
@app.route('/api/movies', methods=['GET','OPTIONS'])
def movies():
    if request.method == 'OPTIONS': return ('',204)

    # paging (client controls)
    start = int(request.args.get('start', '0'))
    size  = int(request.args.get('size',  request.args.get('limit','500')))
    size  = max(1, min(size, 1000))  # clamp reasonable size

    srv = load_cfg()
    # Handle multiple section IDs from config
    sections = srv.get('sectionId', [DEFAULT_SECTION])
    if not isinstance(sections, list):
        sections = [sections]  # Convert single value to list for backward compatibility
    
    # Also check for section parameter (for individual requests)
    section_param = request.args.get('section')
    if section_param:
        sections = [section_param]  # Override with specific section if requested
        
    token = token_from(request)
    if not token:
        return jsonify({"error":"PLEX_TOKEN not configured server-side; client token missing"}), 400

    try:
        base = resolve_base(request)
    except ValueError as e:
        return jsonify({"error":str(e)}), 400

    verify_tls = not insecure_from(request)
    if not verify_tls:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # Get all content from all configured sections
    all_items = []
    
    for section in sections:
        url = f"{base}/library/sections/{section}/all"
        
        # Get all content from this section (don't specify type - get everything)
        params = {
            'sort': 'addedAt:desc',
            'X-Plex-Token': token,
            'X-Plex-Container-Start': 0,
            'X-Plex-Container-Size': 2000  # Get a large batch
        }

        try:
            r = requests.get(url, params=params, headers=PLEX_HEADERS, timeout=TIMEOUT, verify=verify_tls)
            if r.ok:
                ctype = (r.headers.get('Content-Type') or '').lower()
                if 'json' in ctype:
                    data = r.json()
                    mc = data.get('MediaContainer', {}) or {}
                    metadata = mc.get('Metadata') or []
                    # Filter to only movies and TV shows (exclude music, photos, etc.)
                    filtered_metadata = [item for item in metadata if item.get('type') in ['movie', 'show']]
                    all_items.extend(filtered_metadata)
        except Exception as e:
            print(f"Error fetching from section {section}: {e}")
            continue  # Skip this section if it fails
    
    # Shuffle all items randomly to mix movies and TV shows
    random.shuffle(all_items)
    
    # Apply pagination to shuffled results
    paginated_items = all_items[start:start + size]
    total_size = len(all_items)

    insecure_q = '1' if not verify_tls else '0'
    items = []
    for m in paginated_items:
        thumb = m.get('thumb')
        if not thumb: continue
        poster = (
            "/api/poster?"
            f"base={quote_plus(base)}&thumb={quote_plus(thumb)}"
            f"&token={quote_plus(token)}&w=1200&h=1800&insecure={insecure_q}"
        )
        items.append({
            'title':   m.get('title'),
            'year':    m.get('year'),
            'addedAt': m.get('addedAt'),
            'poster':  poster,
            'type':    m.get('type'),  # 'movie' or 'show'
            'mediaType': 'movie' if m.get('type') == 'movie' else 'show'  # normalized type
        })

    return jsonify({
        'start': start,
        'size': size,
        'returned': len(items),
        'totalSize': total_size,
        'items': items
    })

# ---- Poster proxy/stream ----
@app.route('/api/poster', methods=['GET','OPTIONS'])
def poster():
    if request.method == 'OPTIONS': return ('',204)

    # Accept explicit params OR derive sensible defaults from server config
    base   = (request.args.get('base') or '').strip().rstrip('/')
    thumb  = request.args.get('thumb') or ''
    token  = request.args.get('token') or token_from(request)
    w      = int(request.args.get('w', '600'))
    h      = int(request.args.get('h', '900'))
    insecure_q = (request.args.get('insecure') or '').strip().lower()
    insecure = insecure_q in ('1','true','yes','on') or insecure_from(request)

    if not base:
        try:
            base = resolve_base(request)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    if not thumb or not token:
        return jsonify({"error":"missing base/thumb/token"}), 400
    if not (base.startswith('http://') or base.startswith('https://')):
        base = 'http://' + base

    verify_tls = not insecure
    if not verify_tls:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    plex_url = (f"{base}/photo/:/transcode?"
                f"url={quote_plus(base + thumb)}&width={w}&height={h}&minSize=1&X-Plex-Token={token}")

    try:
        r = requests.get(plex_url, headers=PLEX_HEADERS, stream=True, timeout=TIMEOUT, verify=verify_tls)
    except Exception as e:
        return jsonify({"error": f"Upstream request error: {e}"}), 502

    return Response(
        r.iter_content(64*1024),
        status=r.status_code,
        headers={
            'Content-Type': r.headers.get('Content-Type', 'image/jpeg'),
            'Cache-Control': 'public, max-age=86400'
        }
    )

# ---- Now Playing (check monitored devices) ----
# When the proxy runs as the Pi service, a plex_events.NowPlayingMonitor keeps this state
# fresh from Plex's websocket and the endpoint answers from its cache (see __main__). Tests
# and anything importing the module leave it None, so the endpoint asks Plex directly.
MONITOR = None

@app.route('/api/now-playing', methods=['GET','OPTIONS'])
def now_playing():
    if request.method == 'OPTIONS': return ('',204)

    srv = load_cfg()
    devices = srv.get('plexDevices', [])

    if not devices:
        return jsonify({"playing": False, "message": "No devices configured"})

    if MONITOR is not None:
        cached = MONITOR.snapshot()
        if cached is not None:
            return jsonify(cached)

    token = token_from(request)
    if not token:
        return jsonify({"error": "PLEX_TOKEN not configured"}), 400

    try:
        base = resolve_base(request)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    verify_tls = not insecure_from(request)
    if not verify_tls:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    body, _sessions = now_playing_body(srv, base, token, verify_tls)
    return jsonify(body)


def monitor_settings():
    """(base, token, verify_tls) for the background monitor, or None if there is nothing to watch.

    Same precedence as the request helpers, minus the per-request headers the kiosk sends
    (which carry the same config values anyway).
    """
    srv = load_cfg()
    if not srv.get('plexDevices'):
        return None
    token = SERVER_TOKEN or srv.get('plexToken', '').strip()
    base = (os.environ.get('PLEX_URL', '') or srv.get('plexUrl', '')).strip().rstrip('/')
    if not token or not base:
        return None
    if not (base.startswith('http://') or base.startswith('https://')):
        base = 'http://' + base
    verify_tls = not (bool(srv.get('plexInsecure')) or ALLOW_INSECURE_DEFAULT)
    return (base, token, verify_tls)


def poster_url(base, token, thumb, verify_tls, w=1200, h=1800):
    """Relative /api/poster URL for a Plex thumb, as the kiosk expects it."""
    insecure_q = '1' if not verify_tls else '0'
    return (
        f"/api/poster?base={quote_plus(base)}&thumb={quote_plus(thumb)}"
        f"&token={quote_plus(token)}&w={w}&h={h}&insecure={insecure_q}"
    )


_FEATURING = re.compile(r"\s+(?:ft\.?|feat\.?|featuring)\s.*$", re.IGNORECASE)


def short_title(title):
    """A title without its extras, for the up-next tiles.

    Drops bracketed groups that follow other text ("(from the series ...)", "[Remastered]"),
    including nested groups and groups right after a dropped one, then anything after "ft.",
    "feat." or "featuring". A leading group is part of the name ("(Don't Fear) The Reaper")
    and a group glued to a word ("Baby(One More Time)") is left alone. A title that would
    trim to nothing is returned whole.
    """
    t = (title or '').strip()
    out, depth, dropping, last_dropped_end = [], 0, False, None
    for i, ch in enumerate(t):
        if ch in '([':
            if depth == 0:
                follows_space = i > 0 and t[i - 1].isspace()
                follows_dropped = last_dropped_end is not None and last_dropped_end == i - 1
                dropping = follows_space or follows_dropped
            depth += 1
            if not dropping:
                out.append(ch)
        elif ch in ')]' and depth:
            depth -= 1
            if not dropping:
                out.append(ch)
            elif depth == 0:
                last_dropped_end = i
        elif depth == 0 or not dropping:
            out.append(ch)
    short = _FEATURING.sub('', ' '.join(''.join(out).split())).strip()
    return short or t


def monitor_queue(base, token, verify_tls, queue_id, current_item_id, count=3):
    """The ``count`` items after ``current_item_id`` in Plex play queue ``queue_id`` ("up next").

    Returns None when the current item is not in the fetched window: Plex moves the queue's
    selected item only once the new video actually starts (measured 2026-09-26), so a
    lookup made while it buffers can lag, and the caller should try again on its next refresh.
    Raises on an HTTP failure.
    """
    # Short timeout: the monitor thread waits on this lookup (Astra pass 1 #1).
    r = requests.get(f"{base}/playQueues/{queue_id}",
                     params={'X-Plex-Token': token, 'window': count + 3, 'includeBefore': 1},
                     headers=PLEX_HEADERS, timeout=min(TIMEOUT, QUEUE_TIMEOUT), verify=verify_tls)
    if not r.ok:
        raise RuntimeError(f"Play queue request failed: {r.status_code}")
    items = r.json().get('MediaContainer', {}).get('Metadata', []) or []
    ids = [str(i.get('playQueueItemID')) for i in items]
    if str(current_item_id) not in ids:
        return None
    srv = load_cfg()
    music = srv.get('musicVideoSectionId', [])
    music = [str(s).strip() for s in (music if isinstance(music, list) else [music])]
    upcoming = []
    for item in items[ids.index(str(current_item_id)) + 1:][:count]:
        title = item.get('title', '')
        artist, sep, track = title.partition(' - ')
        is_music = str(item.get('librarySectionID', '')) in music and sep
        thumb = item.get('thumb')
        upcoming.append({
            # The kiosk matches the next playing item against upNext[0] by this, to animate
            # the first up-next cover into the main art slot.
            "ratingKey": str(item.get('ratingKey', '')),
            "title": title,
            "artist": artist if is_music else '',
            "trackTitle": track if is_music else title,
            "shortTitle": short_title(track if is_music else title),
            "poster": poster_url(base, token, thumb, verify_tls, 400, 400) if thumb else None,
        })
    return upcoming


def monitor_fetch(base, token, verify_tls):
    """One refresh for the monitor. Raises on failure so the monitor keeps its last good state."""
    if not verify_tls:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    body, sessions = now_playing_body(load_cfg(), base, token, verify_tls)
    if 'error' in body:
        raise RuntimeError(body['error'])
    return body, sessions


def now_playing_body(srv, base, token, verify_tls):
    """The /api/now-playing body, plus {sessionKey: on a monitored device} for every session.

    The session map lets the monitor ignore push notifications about other people's playback.
    """
    devices = srv.get('plexDevices', [])
    seen = {}

    # Check Plex sessions for any of the monitored devices
    sessions_url = f"{base}/status/sessions"
    try:
        r = requests.get(sessions_url, params={'X-Plex-Token': token},
                        headers=PLEX_HEADERS, timeout=TIMEOUT, verify=verify_tls)
        if not r.ok:
            return {"playing": False, "error": f"Sessions request failed: {r.status_code}"}, seen

        sessions_data = r.json()
        sessions = sessions_data.get('MediaContainer', {}).get('Metadata', [])

        # Normalize device IPs for comparison (strip whitespace, lowercase)
        monitored_ips = [device.strip().lower() for device in devices]
        for session in sessions:
            address = (session.get('Player', {}).get('address') or '').strip().lower()
            seen[str(session.get('sessionKey', ''))] = address in monitored_ips

        # Look for active sessions on monitored devices
        for session in sessions:
            player = session.get('Player', {})
            player_address = player.get('address', '').strip().lower()

            # STRICT IP FILTERING: Only match if player IP is in monitored IPs list
            if player_address not in monitored_ips:
                continue  # Skip sessions from non-monitored IPs
            
            # Check if this session is from an included library (whitelist approach)
            included_sections = srv.get('sectionId', ['1'])  # Default to section 1 if not configured
            if not isinstance(included_sections, list):
                included_sections = [included_sections]  # Convert single value to list for backward compatibility
            included_sections = [str(s) for s in included_sections]

            # Music video libraries take over the wall with album-art layout. They are
            # separate from sectionId so they never join the poster rotation.
            music_sections = srv.get('musicVideoSectionId', [])
            if not isinstance(music_sections, list):
                music_sections = [music_sections]
            music_sections = [str(s).strip() for s in music_sections if str(s).strip()]

            library_section_id = str(session.get('librarySectionID', ''))
            is_music_video = library_section_id in music_sections
            if library_section_id not in included_sections and not is_music_video:
                continue  # Skip sessions from non-included libraries

            # Extract media information
            media_type = session.get('type')
            if media_type not in ['movie', 'episode']:
                continue  # Skip music, photos, etc.

            # Get detailed media info
            title = session.get('title', 'Unknown Title')
            artist = ''
            track_title = ''
            if is_music_video:
                # Music videos are "Artist - Title" files in an Other Videos library.
                artist, sep, track_title = title.partition(' - ')
                if not sep:
                    artist, track_title = '', title
                media_type = 'musicvideo'
            if media_type == 'episode':
                show_title = session.get('grandparentTitle', '')
                season_episode = f"S{session.get('parentIndex', '?')}E{session.get('index', '?')}"
                title = f"{show_title} - {season_episode} - {title}"
            
            year = session.get('year')
            rating = session.get('contentRating', '')
            duration = int(session.get('duration', 0))  # milliseconds
            view_offset = int(session.get('viewOffset', 0))  # milliseconds
            rating_key = session.get('ratingKey')  # needed to fetch metadata
            
            # For episodes: actively fetch season/show poster art to avoid video frames
            thumb = None
            if media_type == 'episode':
                # Try to get season poster using parentRatingKey (season's rating key)
                parent_rating_key = session.get('parentRatingKey')
                if parent_rating_key:
                    season_thumb, show_thumb = get_season_poster(base, token, parent_rating_key, verify_tls)
                    thumb = season_thumb or show_thumb  # prefer season poster but use show poster if needed
                
                # If that didn't work, try session poster fields (already available in session data)
                if not thumb:
                    thumb = (
                        session.get('parentThumb') or    # season art
                        session.get('grandparentThumb')  # show art
                    )
            else:
                # For movies, use the movie's own poster. For music videos this is the
                # album-art sidecar ("Artist - Title.jpg") Plex picked up as the poster.
                thumb = session.get('thumb')
            
            # Last resort: use episode thumb (might be a frame) - only if nothing else worked
            if not thumb:
                thumb = session.get('thumb')

            poster_url = None
            if thumb:
                insecure_q = '1' if not verify_tls else '0'
                poster_url = (
                    f"/api/poster?base={quote_plus(base)}&thumb={quote_plus(thumb)}"
                    f"&token={quote_plus(token)}&w=1200&h=1800&insecure={insecure_q}"
                )
            
            # Get media streams for audio/video info (Plex can send an empty Media list)
            media_info = (session.get('Media') or [{}])[0]
            video_resolution = media_info.get('videoResolution', '')
            video_codec = media_info.get('videoCodec', '')
            
            # Get audio info from first audio stream
            audio_codec = ''
            audio_channels = ''
            for part in media_info.get('Part', []):
                for stream in part.get('Stream', []):
                    if stream.get('streamType') == 2:  # Audio stream
                        audio_codec = stream.get('codec', '').upper()
                        channels = stream.get('channels', 0)
                        if channels:
                            audio_channels = f"{channels}.1" if channels > 2 else f"{channels}.0"
                        break
                if audio_codec:
                    break
            
            # Calculate progress percentage
            progress = 0
            if duration > 0:
                progress = min(100, max(0, (view_offset / duration) * 100))
            
            return {
                "playing": True,
                # Plex's player state: playing, paused or buffering. The kiosk advances the
                # bar only while "playing".
                "state": player.get('state') or 'playing',
                # When this viewOffset was observed (ms since the epoch, proxy clock = kiosk
                # clock on the Pi). The monitor keeps the first-seen time while Plex repeats
                # an unchanged offset between the client's ~10 s reports.
                "offsetAt": int(time.time() * 1000),
                "title": title,
                "year": year,
                "rating": rating,
                "poster": poster_url,
                "progress": round(progress, 1),
                "duration": duration,
                "viewOffset": view_offset,
                "videoResolution": video_resolution,
                "videoCodec": video_codec.upper(),
                "audioCodec": audio_codec,
                "audioChannels": audio_channels,
                "playerTitle": player.get('title', ''),
                # Matches the clientIdentifier on Plex's notifications, which is where the
                # monitor learns this player's play queue (sessions don't carry it).
                "playerId": player.get('machineIdentifier', ''),
                "mediaType": media_type,
                "ratingKey": rating_key,
                "artist": artist,
                "trackTitle": track_title
            }, seen

        return {"playing": False, "message": "No active sessions on monitored devices"}, seen

    except Exception as e:
        return {"playing": False, "error": f"Sessions check failed: {str(e)}"}, seen

# ---- Restart kiosk service ----
@app.route("/api/restart-kiosk", methods=["POST", "OPTIONS"])
def restart_kiosk():
    if request.method == "OPTIONS": return ("", 204)
    if ADMIN_KEY and request.headers.get("X-Admin-Key","") != ADMIN_KEY:
        return jsonify({"error":"forbidden"}), 403
    
    try:
        # Run the systemctl command
        result = subprocess.run(
            ["systemctl", "--user", "restart", "poster-kiosk.service"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return jsonify({"ok": True, "message": "Kiosk service restart initiated"})
        else:
            return jsonify({"error": f"Command failed: {result.stderr}"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Restart command timed out"}), 500
    except Exception as e:
        return jsonify({"error": f"Failed to restart kiosk: {str(e)}"}), 500

# ---- Debug (optional) ----
@app.route('/debug/routes')
def debug_routes():
    return '\n'.join(sorted(str(r) for r in app.url_map.iter_rules())), 200, {'Content-Type': 'text/plain'}

if __name__ == '__main__':
    # pip install flask requests
    print("Starting Poster Wall Proxy from:", __file__)
    MONITOR = plex_events.NowPlayingMonitor(fetch=monitor_fetch, settings=monitor_settings,
                                            queue_fetch=monitor_queue)
    MONITOR.start()
    app.run(host='0.0.0.0', port=8811, threaded=True)
