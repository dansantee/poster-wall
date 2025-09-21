#!/usr/bin/env python3
from flask import Flask, jsonify, request, Response
import os, json, pathlib, requests, urllib3
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
    resp.headers['Access-Control-Allow-Methods'] = 'GET, PUT, OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Plex-Token, X-Plex-Url, X-Allow-Insecure, X-Admin-Key'
    return resp

# ---- Health ----
@app.route('/api/ping', methods=['GET','OPTIONS'])
def ping():
    if request.method == 'OPTIONS': return ('',204)
    return 'pong',200

# ---- Server config (GET/PUT) ----
@app.route("/api/config", methods=["GET", "OPTIONS"])
def cfg_get():
    if request.method == "OPTIONS": return ("", 204)
    return jsonify(load_cfg())

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
    params = {
        'type': 1,
        'sort': 'addedAt:desc',
        'X-Plex-Token': token,
        'X-Plex-Container-Start': start,
        'X-Plex-Container-Size': size
    }

    try:
        r = requests.get(url, params=params, headers=PLEX_HEADERS, timeout=TIMEOUT, verify=verify_tls)
    except Exception as e:
        return jsonify({"error": f"Upstream request error: {e}"}), 502

    if not r.ok:
        return Response(r.content, status=r.status_code, content_type=r.headers.get('Content-Type','text/plain'))

    ctype = (r.headers.get('Content-Type') or '').lower()
    if 'json' not in ctype:
        return Response(f"Upstream did not return JSON. Content-Type: {ctype}\n\n{r.text[:1000]}", 502, mimetype='text/plain')

    data = r.json()
    mc = data.get('MediaContainer', {}) or {}
    metadata = mc.get('Metadata') or []
    total_size = mc.get('totalSize') or None

    insecure_q = '1' if not verify_tls else '0'
    items = []
    for m in metadata:
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
            'poster':  poster
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

# ---- Debug (optional) ----
@app.route('/debug/routes')
def debug_routes():
    return '\n'.join(sorted(str(r) for r in app.url_map.iter_rules())), 200, {'Content-Type': 'text/plain'}

if __name__ == '__main__':
    # pip install flask requests
    print("Starting Poster Wall Proxy from:", __file__)
    app.run(host='0.0.0.0', port=8811)
