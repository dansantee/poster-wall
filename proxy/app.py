#!/usr/bin/env python3
from flask import Flask, jsonify, request, Response
import os, json, pathlib, requests, urllib3, subprocess, socket
from urllib.parse import quote_plus

app = Flask(__name__)

# ---- Config / constants ----
DEFAULT_SECTION = os.environ.get('SECTION_ID', '1')
TIMEOUT = float(os.environ.get('TIMEOUT', '10'))
ALLOW_INSECURE_DEFAULT = os.environ.get('ALLOW_INSECURE','').strip().lower() in ('1','true','yes','on')

# Optional server-wide token (client may also send a token)
SERVER_TOKEN = os.environ.get('PLEX_TOKEN','').strip()

# Server-managed config file + admin key
CFG_PATH = pathlib.Path(os.environ.get("PW_CONFIG_PATH", "config.json"))
ADMIN_KEY = os.environ.get("PW_ADMIN_KEY", "").strip()

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

# ---- Server config (GET/PUT) ----
@app.route("/api/config", methods=["GET", "OPTIONS"])
def cfg_get():
    if request.method == "OPTIONS": return ("", 204)
    config = load_cfg() or {}  # Ensure we always have a dict
    # Always add hostname to config for client use
    config['hostname'] = socket.gethostname()
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
    section = request.args.get('section') or srv.get('sectionId') or DEFAULT_SECTION
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

    url = f"{base}/library/sections/{section}/all"
    
    # Try to fetch both movies (type=1) and TV shows (type=2) to support mixed or TV-only libraries
    all_items = []
    for content_type in [1, 2]:  # 1=movies, 2=TV shows
        params = {
            'type': content_type,
            'sort': 'addedAt:desc',
            'X-Plex-Token': token,
            'X-Plex-Container-Start': 0,  # Always start from 0 to get full list for sorting
            'X-Plex-Container-Size': 1000  # Get a large batch to sort properly
        }

        try:
            r = requests.get(url, params=params, headers=PLEX_HEADERS, timeout=TIMEOUT, verify=verify_tls)
            if r.ok:
                ctype = (r.headers.get('Content-Type') or '').lower()
                if 'json' in ctype:
                    data = r.json()
                    mc = data.get('MediaContainer', {}) or {}
                    metadata = mc.get('Metadata') or []
                    all_items.extend(metadata)
        except Exception:
            continue  # Skip this type if it fails
    
    # Sort combined results by addedAt (newest first)
    all_items.sort(key=lambda x: x.get('addedAt', 0), reverse=True)
    
    # Apply pagination to combined results
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
@app.route('/api/now-playing', methods=['GET','OPTIONS'])
def now_playing():
    if request.method == 'OPTIONS': return ('',204)
    
    srv = load_cfg()
    devices = srv.get('plexDevices', [])
    
    if not devices:
        return jsonify({"playing": False, "message": "No devices configured"})
    
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
    
    # Check Plex sessions for any of the monitored devices
    sessions_url = f"{base}/status/sessions"
    try:
        r = requests.get(sessions_url, params={'X-Plex-Token': token}, 
                        headers=PLEX_HEADERS, timeout=TIMEOUT, verify=verify_tls)
        if not r.ok:
            return jsonify({"playing": False, "error": f"Sessions request failed: {r.status_code}"})
        
        sessions_data = r.json()
        sessions = sessions_data.get('MediaContainer', {}).get('Metadata', [])
        
        # Look for active sessions on monitored devices
        for session in sessions:
            player = session.get('Player', {})
            player_address = player.get('address', '')
            player_title = player.get('title', '').lower()
            
            # Check if this session is from one of our monitored devices
            device_match = False
            for device in devices:
                device = device.lower().strip()
                if (device in player_address.lower() or 
                    device in player_title or
                    player_address.lower() in device):
                    device_match = True
                    break
            
            if not device_match:
                continue
            
            # Check if this session is from an included library (whitelist approach)
            included_sections = srv.get('sectionId', ['1'])  # Default to section 1 if not configured
            if not isinstance(included_sections, list):
                included_sections = [included_sections]  # Convert single value to list for backward compatibility
            
            library_section_id = str(session.get('librarySectionID', ''))
            if library_section_id not in included_sections:
                continue  # Skip sessions from non-included libraries
            
            # Extract media information
            media_type = session.get('type')
            if media_type not in ['movie', 'episode']:
                continue  # Skip music, photos, etc.
            
            # Get detailed media info
            title = session.get('title', 'Unknown Title')
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
                # For movies, use the movie's own poster
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
            
            # Get media streams for audio/video info
            media_info = session.get('Media', [{}])[0]
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
            
            return jsonify({
                "playing": True,
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
                "mediaType": media_type
            })
        
        return jsonify({"playing": False, "message": "No active sessions on monitored devices"})
        
    except Exception as e:
        return jsonify({"playing": False, "error": f"Sessions check failed: {str(e)}"})

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
    app.run(host='0.0.0.0', port=8811)
