#!/usr/bin/env python3
from flask import Flask, jsonify, request, Response
import os, json, pathlib, re, requests, urllib3, subprocess, socket, random, threading, time
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
    # For the first-run setup screen: the address to show, and whether Plex is set up yet
    config['ip'] = lan_ip()
    config['configured'] = is_configured(config)
    
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
        # Computed on every GET; never stored (the settings page re-saves the whole document)
        for key in COMPUTED_CFG_KEYS:
            cfg.pop(key, None)
        # minimal normalization
        if 'plexUrl' in cfg and isinstance(cfg['plexUrl'], str) and cfg['plexUrl'] and not cfg['plexUrl'].startswith(('http://','https://')):
            cfg['plexUrl'] = 'http://' + cfg['plexUrl']
        save_cfg(cfg)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

# ---- First-run setup: Plex sign-in, servers, libraries, players ----
# The settings page signs in with Plex's device-link flow (a 4-letter code entered at
# plex.tv/link), picks a server and the libraries to show, and finds the players to watch,
# so nobody has to dig a token out of XML or look up section IDs and IP addresses.
COMPUTED_CFG_KEYS = ('hostname', 'ip', 'configured')
PLEX_TV = 'https://plex.tv/api/v2'
PLEX_LINK_URL = 'https://plex.tv/link'
CONNECT_TIMEOUT = 3.0


def is_configured(cfg):
    """Plex is set up when there's a server URL and a token, from config or the environment."""
    base = (os.environ.get('PLEX_URL', '') or cfg.get('plexUrl') or '').strip()
    token = (SERVER_TOKEN or cfg.get('plexToken') or '').strip()
    return bool(base and token)


def lan_ip():
    """This machine's LAN address (no packet is sent: connect() on UDP only picks a route)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(('192.0.2.1', 9))
            return sock.getsockname()[0]
    except OSError:
        return ''


def plex_client_id():
    """A stable per-device id for plex.tv: the PIN must be polled with the id that created it,
    and Plex lists the wall under it in the account's devices."""
    try:
        machine = pathlib.Path('/etc/machine-id').read_text().strip()
    except OSError:
        machine = ''
    import hashlib
    return 'poster-wall-' + hashlib.sha1((machine or socket.gethostname()).encode()).hexdigest()[:16]


def plex_tv_headers(token=None):
    h = dict(PLEX_HEADERS)
    h['X-Plex-Client-Identifier'] = plex_client_id()
    h['X-Plex-Device-Name'] = socket.gethostname()
    if token:
        h['X-Plex-Token'] = token
    return h


def admin_forbidden():
    return bool(ADMIN_KEY) and request.headers.get('X-Admin-Key', '') != ADMIN_KEY


def server_candidates(resource):
    """A Plex server's addresses in the order to try from the wall: its LAN address over plain
    http (what a manual setup uses), then the LAN https address, then remote, then Plex's relay."""
    conns = resource.get('connections') or []
    local = [c for c in conns if c.get('local') and not c.get('relay')]
    remote = [c for c in conns if not c.get('local') and not c.get('relay')]
    relay = [c for c in conns if c.get('relay')]
    out = []
    for c in local:
        if c.get('address') and c.get('port') and not c.get('IPv6'):
            out.append(f"http://{c['address']}:{c['port']}")
    out += [c['uri'] for c in local + remote + relay if c.get('uri')]
    seen = set()
    return [u for u in out if not (u in seen or seen.add(u))]


@app.route('/api/plex/pin', methods=['POST', 'OPTIONS'])
def plex_pin_create():
    if request.method == 'OPTIONS': return ('', 204)
    if admin_forbidden(): return jsonify({"error": "forbidden"}), 403
    try:
        r = requests.post(f'{PLEX_TV}/pins', headers=plex_tv_headers(), data={'strong': 'false'}, timeout=TIMEOUT)
        if not r.ok:
            return jsonify({"error": f"plex.tv answered {r.status_code}"}), 502
        pin = r.json()
        return jsonify({"id": pin.get('id'), "code": pin.get('code'), "linkUrl": PLEX_LINK_URL,
                        "expiresIn": pin.get('expiresIn')})
    except Exception as e:
        return jsonify({"error": f"Could not reach plex.tv: {e}"}), 502


@app.route('/api/plex/pin/<int:pin_id>', methods=['GET', 'OPTIONS'])
def plex_pin_check(pin_id):
    """Not linked yet → {linked: false}. Linked → the account's servers, the ones it owns first,
    each with its own access token and the addresses to try."""
    if request.method == 'OPTIONS': return ('', 204)
    if admin_forbidden(): return jsonify({"error": "forbidden"}), 403
    try:
        r = requests.get(f'{PLEX_TV}/pins/{pin_id}', headers=plex_tv_headers(), timeout=TIMEOUT)
        if r.status_code == 404:
            return jsonify({"linked": False, "expired": True})
        if not r.ok:
            return jsonify({"error": f"plex.tv answered {r.status_code}"}), 502
        account_token = r.json().get('authToken')
        if not account_token:
            return jsonify({"linked": False})
        res = requests.get(f'{PLEX_TV}/resources', params={'includeHttps': 1, 'includeRelay': 1},
                           headers=plex_tv_headers(account_token), timeout=TIMEOUT)
        if not res.ok:
            return jsonify({"error": f"plex.tv answered {res.status_code} for the server list"}), 502
        servers = [
            {"name": d.get('name') or 'Plex Media Server', "owned": bool(d.get('owned')),
             "id": d.get('clientIdentifier') or '',
             "token": d.get('accessToken') or account_token, "candidates": server_candidates(d)}
            for d in res.json() if 'server' in str(d.get('provides', '')).split(',')
        ]
        servers.sort(key=lambda s: not s['owned'])
        return jsonify({"linked": True, "servers": servers})
    except Exception as e:
        return jsonify({"error": f"Could not reach plex.tv: {e}"}), 502


@app.route('/api/plex/connect', methods=['POST', 'OPTIONS'])
def plex_connect():
    """The first of a server's addresses that answers from here as that server: body
    {token, candidates, machineId}. With machineId (plex.tv's clientIdentifier for it), the
    address only counts if /identity there reports the same machineIdentifier: a shared server's
    private address can belong to something else on the wall's LAN (Astra pass 1)."""
    if request.method == 'OPTIONS': return ('', 204)
    if admin_forbidden(): return jsonify({"error": "forbidden"}), 403
    body = request.get_json(silent=True) or {}
    token = str(body.get('token') or '')
    machine_id = str(body.get('machineId') or '')
    tried = []
    for uri in [str(u) for u in (body.get('candidates') or [])][:8]:
        try:
            r = requests.get(f"{uri.rstrip('/')}/identity", params={'X-Plex-Token': token},
                             headers=PLEX_HEADERS, timeout=CONNECT_TIMEOUT, verify=True)
            if not r.ok:
                tried.append(f"{uri}: HTTP {r.status_code}")
                continue
            try:
                found = (r.json().get('MediaContainer') or {}).get('machineIdentifier', '')
            except ValueError:
                found = ''
            if machine_id and found != machine_id:
                tried.append(f"{uri}: a different server" if found else f"{uri}: not a Plex server")
                continue
            return jsonify({"plexUrl": uri.rstrip('/')})
        except Exception as e:
            tried.append(f"{uri}: {type(e).__name__}")
    return jsonify({"error": "None of the server's addresses answered from the wall", "tried": tried}), 502


@app.route('/api/plex/libraries', methods=['GET', 'OPTIONS'])
def plex_libraries():
    """The server's libraries ({key, title, type}), for the settings page's library picker."""
    if request.method == 'OPTIONS': return ('', 204)
    if admin_forbidden(): return jsonify({"error": "forbidden"}), 403
    try:
        base, token = resolve_base(request), token_from(request)
        r = requests.get(f'{base}/library/sections', params={'X-Plex-Token': token}, headers=PLEX_HEADERS,
                         timeout=TIMEOUT, verify=not insecure_from(request))
        if not r.ok:
            return jsonify({"error": f"Plex answered {r.status_code}"}), 502
        dirs = (r.json().get('MediaContainer') or {}).get('Directory') or []
        # agent: an "Other Videos" library (agent ...none) reports type movie too
        return jsonify({"libraries": [{"key": str(d.get('key')), "title": d.get('title', ''), "type": d.get('type', ''),
                                       "agent": d.get('agent', '')}
                                      for d in dirs]})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route('/api/plex/players', methods=['GET', 'OPTIONS'])
def plex_players():
    """Players with something playing now ({title, address, product, playing}), so the settings
    page can add a TV to the monitored devices without looking up its IP address."""
    if request.method == 'OPTIONS': return ('', 204)
    if admin_forbidden(): return jsonify({"error": "forbidden"}), 403
    try:
        base, token = resolve_base(request), token_from(request)
        r = requests.get(f'{base}/status/sessions', params={'X-Plex-Token': token}, headers=PLEX_HEADERS,
                         timeout=TIMEOUT, verify=not insecure_from(request))
        if not r.ok:
            return jsonify({"error": f"Plex answered {r.status_code}"}), 502
        players = []
        for m in (r.json().get('MediaContainer') or {}).get('Metadata') or []:
            pl = m.get('Player') or {}
            if pl.get('address'):
                players.append({"title": pl.get('title', ''), "address": pl['address'],
                                "product": pl.get('product', ''), "playing": m.get('title', '')})
        return jsonify({"players": players})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


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


# A bracketed group after the title is a tag, not part of the song's name, when it opens with a
# credit or source word or a year, or names a video/version/edit. Dan, 2026-09-29: hide
# "(Videoclip)" in the now-playing title but keep "Undone (The Sweater Song)".
_TAG_GROUP = re.compile(
    r"^(?:feat\.?|ft\.?|featuring|with|from|starring|\d{4})\b"
    r"|\b(?:video|videoclip|version|cut|audio|season|parts?|remaster(?:ed)?)\b",
    re.IGNORECASE)
_REMIX = re.compile(r"\bremix\b", re.IGNORECASE)   # a remix sounds different: always kept


def display_title(title):
    """The now-playing title without tag groups ("(Videoclip)", "(feat. Khalid)",
    "(from Aladdin)", "(Director's Cut)", "(1987)").

    Only top-level groups that follow a space or another group are candidates, so a leading
    group ("(Don't Fear) The Reaper") and a glued one ("Baby(One More Time)") stay whole,
    nested groups included. Groups that are part of the name stay ("(What Does The Fox
    Say)"), and so do remixes. A title that would trim to nothing is returned whole. Known
    limit: a name part opening with a tag word ("(With or Without You)") is dropped too.
    """
    t = (title or '').strip()
    out, i = [], 0
    while i < len(t):
        ch = t[i]
        if ch not in '([':
            out.append(ch)
            i += 1
            continue
        depth, j = 0, i                     # find the matching close of this top-level group
        while j < len(t):
            depth += t[j] in '(['
            depth -= t[j] in ')]'
            if depth == 0:
                break
            j += 1
        group = t[i:j + 1]
        content = group[1:-1].strip()
        after_gap = i > 0 and (t[i - 1].isspace() or t[i - 1] in ')]')
        if after_gap and depth == 0 and _TAG_GROUP.search(content) and not _REMIX.search(content):
            pass                            # a tag: drop it
        else:
            out.append(group)
        i = j + 1
    return ' '.join(''.join(out).split()) or t


# Plex's audioChannelLayout values ("5.1(side)", "stereo") and, when a stream has none, its
# channel count (LFE included, so 6 is 5.1) as the speaker layout the badge shows. Counts
# that could be either (4 is quad or 3.1, 5 is 5.0 or 4.1) give "" rather than a guess.
_LAYOUT_NAMES = {'mono': '1.0', 'stereo': '2.0', 'quad': '4.0'}
_CHANNEL_LAYOUTS = {1: '1.0', 2: '2.0', 3: '2.1', 6: '5.1', 7: '6.1', 8: '7.1'}


def audio_layout(stream):
    """"5.1", "7.1", "2.0", ... for an audio stream, or "" when Plex gives nothing usable."""
    layout = str(stream.get('audioChannelLayout') or '').lower()
    layout = _LAYOUT_NAMES.get(layout, layout.split('(')[0])
    if re.fullmatch(r'\d\.\d', layout):
        return layout
    try:
        channels = int(stream.get('channels') or 0)
    except (TypeError, ValueError):
        return ''
    return _CHANNEL_LAYOUTS.get(channels, '')


def dynamic_range(stream):
    """"Dolby Vision", "HDR10+", "HDR10", "HLG" or "" (SDR) for a video stream. Dolby Vision
    wins when a file also carries HDR10/HDR10+ ("4K DoVi/HDR10+")."""
    title = str(stream.get('displayTitle') or '')
    trc = str(stream.get('colorTrc') or '').lower()
    if stream.get('DOVIPresent') or 'DoVi' in title:
        return 'Dolby Vision'
    if 'HDR10+' in title:
        return 'HDR10+'
    if trc == 'arib-std-b67' or 'HLG' in title:   # before the generic "HDR" (Astra pass 1 #1)
        return 'HLG'
    if trc == 'smpte2084' or 'HDR' in title:
        return 'HDR10'
    return ''


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


FACTS_TIMEOUT = 5.0
FACTS_TTL = 600       # seconds, for entries without an updatedAt (an edit can't be detected)
MAX_FACTS = 10
_facts_lock = threading.Lock()
_facts_cache = {}     # (base, ratingKey, updatedAt) -> (facts, fetched_at); updatedAt changes on an edit
_facts_inflight = set()


def _run_in_background(fn):
    threading.Thread(target=fn, daemon=True).start()


def facts_from_summary(summary):
    """A music video's fun facts are stored one per line in its Plex summary."""
    return [line.strip() for line in str(summary or '').splitlines() if line.strip()][:MAX_FACTS]


def music_video_facts(base, token, verify_tls, session):
    """The playing music video's fun facts, for the kiosk's Pop-Up Video style bubbles.

    Taken from the session when Plex includes the summary there. Otherwise the item's metadata
    is fetched in the background, one lookup per item (and per edit) at a time, and this
    returns what's cached: nothing the first time. The lookup never runs inside the refresh
    that announces a new song (Astra pass 1: even a short timeout held that news back); the
    next refresh carries the facts and the kiosk adopts them. A failed lookup isn't cached.
    """
    if session.get('summary'):
        return facts_from_summary(session['summary'])
    rating_key = str(session.get('ratingKey') or '')
    if not rating_key:
        return []
    updated = str(session.get('updatedAt') or '')
    key = (base, rating_key, updated)
    with _facts_lock:
        hit = _facts_cache.get(key)
        if hit and (updated or time.time() - hit[1] < FACTS_TTL):
            return hit[0]
        stale = hit[0] if hit else []
        if key in _facts_inflight:
            return stale
        _facts_inflight.add(key)

    def fetch():
        try:
            r = requests.get(f"{base}/library/metadata/{rating_key}", params={'X-Plex-Token': token},
                             headers=PLEX_HEADERS, timeout=min(TIMEOUT, FACTS_TIMEOUT), verify=verify_tls)
            if r.ok:
                items = r.json().get('MediaContainer', {}).get('Metadata', []) or []
                facts = facts_from_summary(items[0].get('summary') if items else '')
                with _facts_lock:
                    if len(_facts_cache) > 256:
                        _facts_cache.clear()
                    _facts_cache[key] = (facts, time.time())
        except Exception:
            pass
        finally:
            with _facts_lock:
                _facts_inflight.discard(key)

    _run_in_background(fetch)
    with _facts_lock:
        hit = _facts_cache.get(key)
    return hit[0] if hit else stale


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
            
            # Audio from the first audio stream, HDR from the first video stream
            audio_codec = ''
            audio_channels = ''
            audio_profile = ''
            video_range = None
            for part in media_info.get('Part', []):
                for stream in part.get('Stream', []):
                    if stream.get('streamType') == 1 and video_range is None:
                        video_range = dynamic_range(stream)
                    if stream.get('streamType') == 2 and not audio_codec:
                        audio_codec = stream.get('codec', '').upper()
                        audio_channels = audio_layout(stream)
                        # "dolby truehd + dolby atmos", "ma + dts:x", "lc": what the badge names
                        audio_profile = str(stream.get('profile') or '').lower()
                if audio_codec and video_range is not None:
                    break
            
            # Calculate progress percentage
            progress = 0
            if duration > 0:
                progress = min(100, max(0, (view_offset / duration) * 100))
            
            body = {
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
                "videoDynamicRange": video_range or '',
                "audioCodec": audio_codec,
                "audioChannels": audio_channels,
                "audioProfile": audio_profile,
                "playerTitle": player.get('title', ''),
                # Matches the clientIdentifier on Plex's notifications, which is where the
                # monitor learns this player's play queue (sessions don't carry it).
                "playerId": player.get('machineIdentifier', ''),
                "mediaType": media_type,
                "ratingKey": rating_key,
                "artist": artist,
                "trackTitle": track_title,
                "displayTitle": display_title(track_title)
            }
            if is_music_video:
                body["facts"] = music_video_facts(base, token, verify_tls, session)
            return body, seen

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
