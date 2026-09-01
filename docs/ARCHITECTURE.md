# Architecture

Poster Wall turns a Raspberry Pi and a portrait display into a movie-poster
one-sheet that rotates through a Plex library, and switches to a "Now Showing"
marquee whenever someone starts playback on a device you nominated.

## The three processes

Everything runs as three user systemd services on the Pi, installed by
[`setup.sh`](../setup.sh):

| Service | What it is | Port |
| --- | --- | --- |
| `poster-proxy.service` | Flask app, [`proxy/app.py`](../proxy/app.py) — the Plex proxy and config API | 8811 |
| `poster-web.service` | `python -m http.server` serving [`web/`](../web) verbatim | 8088 |
| `poster-kiosk.service` | Sway compositor launching Chromium at `http://localhost:8088` | — |

```
                    Raspberry Pi
 ┌───────────────────────────────────────────────────────────┐
 │                                                           │
 │  poster-kiosk (Sway + Chromium --kiosk)                    │
 │        │ loads http://localhost:8088/index.html            │
 │        ▼                                                   │
 │  poster-web (:8088)  ──serves──▶ web/*.html .js .css .png  │
 │        │                                                   │
 │  browser JS calls http://<hostname>:8811/api/*             │
 │        ▼                                                   │
 │  poster-proxy (:8811) ◀──reads/writes──▶ proxy/config.json │
 │        │                                                   │
 └────────┼───────────────────────────────────────────────────┘
          │ HTTP + X-Plex-Token
          ▼
    Plex Media Server (:32400, elsewhere on the LAN)
```

The static server and the API are deliberately separate ports. The browser
therefore makes cross-origin requests, which is why the proxy sets permissive
CORS headers on every response (`add_cors`) — and why the kiosk can read poster
pixels back out of a `<canvas>` for auto-dimming.

## Why there is a proxy at all

The proxy exists for four reasons, and each one is load-bearing:

1. **Token custody.** The Plex token lives in `proxy/config.json` on the Pi. The
   kiosk page never needs to be edited to hold a secret.
2. **CORS.** Plex does not send `Access-Control-Allow-Origin`, so the browser
   could not fetch library JSON — or read poster pixels for auto-dimming —
   directly.
3. **Aggregation.** `/api/movies` fans out across every selected library
   section, filters to movies and shows, and shuffles the combined result, so
   the kiosk sees one flat list instead of doing per-library bookkeeping.
4. **Artwork selection.** For episodes, `/api/now-playing` chases down the
   season or show poster instead of letting the display show a video frame.

## Kiosk boot sequence

[`web/app.js`](../web/app.js) is one IIFE with no framework and no build step.

```
init()
 ├─ GET /api/config ................ loadCfg(), normalises + defaults every key
 ├─ applyFontSettings(cfg) ......... writes CSS custom properties on :root
 ├─ if ?preview=nowplaying ......... render a fake session and stop
 ├─ GET /api/movies?start=&size=500  fetchItems(), pages until a short page
 ├─ startRotation(cfg, items) ...... shuffles again client-side, primes poster A,
 │                                   then setInterval(swap, rotateSec * 1000)
 └─ if cfg.plexDevices.length ...... startNowPlayingMonitor(cfg)
                                     first poll after 1s, then every 5s
```

Any thrown error in `init()` replaces the page body with a full-screen message
that names the settings URL — see `showError()`. That is the only error UI; there
is no retry loop, so a proxy that comes up late needs a kiosk restart.

## Display modes

There are exactly two visual states, tracked by the `currentMode` variable:

```
        ┌──────────────────────────────────────────┐
        │              rotation                    │
        │  #stage visible, posters crossfade every  │
        │  rotateSec seconds                        │
        └──────────────────────────────────────────┘
             │                            ▲
   /api/now-playing                       │  /api/now-playing
   returns playing:true                   │  returns playing:false
   (checked every 5s)                     │
             ▼                            │
        ┌──────────────────────────────────────────┐
        │             nowplaying                    │
        │  #stage hidden, #nowShowing.visible with  │
        │  marquee text, live progress bar, poster, │
        │  metadata icons                           │
        └──────────────────────────────────────────┘
```

Two things about `nowplaying` mode are worth knowing:

- The rotation `setInterval` is **not** cleared. Posters keep swapping behind the
  hidden `#stage`, and the wall resumes mid-rotation when playback stops.
- Only the *progress bar* is refreshed on subsequent polls. If someone switches
  to a different movie without stopping first, the poster and icons stay stale
  until playback stops and restarts.

## Poster transitions

`swap()` has three code paths:

1. `posterTransitions` off — a plain opacity crossfade between the two `<img>`
   elements, driven entirely by `.poster.visible`.
2. `posterTransitions` on and the random pick is `crossfade` — same idea, but
   through the `.transition-crossfade` classes.
3. Anything else — the two elements get `.transition-<name>`, the incoming image
   starts at `.entering`, then on the next frame it becomes `.visible` while the
   outgoing one becomes `.exiting`. Classes are cleared 1000 ms later and the
   front/back references swap.

All of the motion lives in [`web/styles.css`](../web/styles.css) as `transform`
and `opacity` transitions, which the Pi 5 composites on the GPU. The transition
name in the config is literally half of a CSS class name, which is why
`tests/test_frontend_contract.py` asserts that every selectable transition has
matching CSS.

## Auto-dimming

Bright posters — white backgrounds especially — are painful on a wall at night.
When `autoDim` is on, `computeBrightness()` draws each poster into a 32×32
offscreen canvas, averages Rec. 601 luma, and if the average is ≥ 200 (out of
255) adds `.dim` to the image. The CSS then applies
`filter: brightness(var(--poster-dim-brightness))`, whose value comes from the
`autoDimStrength` setting.

This is the second reason the proxy's CORS headers matter: `getImageData()` on a
cross-origin image throws unless the response is CORS-approved and the `<img>`
was created with `crossOrigin = 'anonymous'`.

## Configuration flow

There is exactly one source of truth, `proxy/config.json`, and both pages read
it over HTTP:

```
 settings.html ──PUT /api/config──▶ proxy/config.json ──GET /api/config──▶ index.html
      ▲                                                                        │
      └──────────────── GET /api/config (to populate the form) ────────────────┘
```

`PUT` is a **full replace**, not a merge. The settings page compensates by
spreading the document it loaded (`{ ...cfg }`) and overwriting the fields it
manages, which is what keeps unknown and legacy keys alive. See
[CONFIGURATION.md](CONFIGURATION.md) for every key and its default.

Nothing is stored in `localStorage`; a browser refresh always re-reads the
server. Saving settings does **not** restart anything — the kiosk picks up new
values on its next page load, which is what the "Restart Kiosk" button is for.

## Where things live

```
proxy/app.py         Flask proxy + config API (the only backend)
proxy/config.json    live settings and the Plex token (gitignored)
web/index.html       kiosk page: two <img> for rotation, one #nowShowing overlay
web/app.js           kiosk logic: config, paging, rotation, transitions, dimming
web/settings.html    settings form
web/settings.js      loads/saves config, build status, test + preview buttons
web/styles.css       everything visual, including all transition keyframes
web/info-icons/      resolution / audio / content-rating badges (PNG)
setup.sh             one-shot Pi provisioning; safe to re-run
scripts/…-remote.ps1 Windows-side deploy/restart helper over SSH
docs/                this documentation
tests/               pytest suite (see TESTING.md)
```

## Known rough edges

These are real, reproduced behaviours, not speculation. Several are pinned by
tests so a future change is deliberate.

- **Paged fetches reshuffle.** `/api/movies` shuffles the combined result on
  *every* request, then slices `start:start+size`. A library larger than one
  page (500 items) therefore yields overlaps and gaps across pages, because
  page 2 is sliced out of a different shuffle than page 1.
- **`Media: []` breaks a session.** `/api/now-playing` indexes `Media[0]`
  unguarded; the resulting `IndexError` is swallowed by the endpoint's
  catch-all, so the kiosk just stays in rotation.
- **The settings page cannot send an admin key.** `settings.js` looks for an
  `adminKey` input that `settings.html` does not contain. If `PW_ADMIN_KEY` is
  set on the proxy, saving from the UI fails with 403.
- **`hostname` gets persisted.** The proxy injects `hostname` on `GET`, and the
  settings page spreads the whole document back on `PUT`, so a stale hostname
  ends up in `config.json`. Harmless — `GET` always overwrites it.
- **The marquee never shows the title.** `/api/now-playing` returns a formatted
  `title` and `styles.css` has a `.now-showing-movie-title` rule, but nothing
  renders it. Only the configured `nowShowingText` is displayed.
- **No retry on boot.** If the proxy is not answering when Chromium loads the
  page, the kiosk shows the error screen until it is reloaded.
