from flask import Flask, jsonify, request, Response
import requests, os
import urllib3
from urllib.parse import quote_plus

app = Flask(__name__)

PLEX_TOKEN = os.environ.get('PLEX_TOKEN', '').strip()
DEFAULT_SECTION = os.environ.get('SECTION_ID', '1')
TIMEOUT = float(os.environ.get('TIMEOUT', '10'))
ALLOW_INSECURE_DEFAULT = os.environ.get('ALLOW_INSECURE', '').strip() in ('1','true','yes','on')

@app.route('/api/poster', methods=['GET','OPTIONS'])
def poster():
    if request.method == 'OPTIONS':
        return ('', 204)

    base   = (request.args.get('base') or '').strip().rstrip('/')
    thumb  = request.args.get('thumb') or ''
    token  = request.args.get('token') or ''
    w      = int(request.args.get('w', '600'))
    h      = int(request.args.get('h', '900'))
    insecure = (request.args.get('insecure') or '').lower() in ('1','true','yes','on')

    if not base or not thumb or not token:
        return jsonify({"error":"missing base/thumb/token"}), 400
    if not (base.startswith('http://') or base.startswith('https://')):
        base = 'http://' + base

    verify_tls = not insecure
    if not verify_tls:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    plex_url = (
        f"{base}/photo/:/transcode?"
        f"url={quote_plus(base + thumb)}&width={w}&height={h}&minSize=1&X-Plex-Token={token}"
    )

    try:
        r = requests.get(plex_url, headers=PLEX_HEADERS, stream=True,
                         timeout=TIMEOUT, verify=verify_tls)
    except Exception as e:
        return jsonify({"error": f"Upstream request error: {e}"}), 502

    headers = {
        "Content-Type": r.headers.get('Content-Type', 'image/jpeg'),
        "Cache-Control": "public, max-age=86400"
    }
    return Response(r.iter_content(64*1024), status=r.status_code, headers=headers)

# --- Helpers ---
def resolve_base(req):
    base = (os.environ.get('PLEX_URL', '') or
            req.headers.get('X-Plex-Url') or
            req.args.get('url') or '').strip().rstrip('/')
    if not base:
        raise ValueError("No Plex URL provided. Set PLEX_URL or send X-Plex-Url header.")
    if not (base.startswith('http://') or base.startswith('https://')):
        base = 'http://' + base
    return base

def insecure_from(req):
    hdr = (req.headers.get('X-Allow-Insecure') or '').strip().lower()
    return ALLOW_INSECURE_DEFAULT or hdr in ('1','true','yes','on')

PLEX_HEADERS = {
    'Accept': 'application/json',
    'X-Plex-Client-Identifier': 'poster-wall-proxy',
    'X-Plex-Product': 'Poster Wall',
    'X-Plex-Version': '1.0',
    'X-Plex-Platform': 'Python',
    'X-Plex-Device': 'Proxy',
}

# --- CORS handling ---
@app.after_request
def add_cors(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Plex-Token, X-Plex-Url, X-Allow-Insecure'
    return resp

@app.route('/api/ping', methods=['GET','OPTIONS'])
def ping():
    if request.method == 'OPTIONS':
        return ('',204)
    return 'pong',200

@app.route('/api/movies', methods=['GET','OPTIONS'])
def movies():
    if request.method == 'OPTIONS':
        return ('',204)

    # Paging params (client controls); sane defaults
    start   = int(request.args.get('start', '0'))
    size    = int(request.args.get('size',  request.args.get('limit','500')))  # fallback to 'limit' if present
    size    = max(1, min(size, 1000))  # clamp to avoid silly values

    section = request.args.get('section', DEFAULT_SECTION)
    token   = PLEX_TOKEN or (request.headers.get('X-Plex-Token') or request.args.get('token','')).strip()
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
    total_size = mc.get('totalSize') or mc.get('totalViewed') or None  # Plex sometimes returns totalSize

    insecure_q = '1' if not verify_tls else '0'
    items = []
    for m in metadata:
        thumb = m.get('thumb')
        if not thumb:
            continue
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

    # Don’t re-sort here; Plex gives addedAt:desc already. Keep response small + informative.
    return jsonify({
        'start': start,
        'size': size,
        'returned': len(items),
        'totalSize': total_size,
        'items': items
    })

if __name__ == '__main__':
    # pip install flask requests
    app.run(host='0.0.0.0', port=8811)
