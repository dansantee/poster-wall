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
 ├─ if ?preview=nowplaying|musicvideo  render a fake session and stop
 ├─ GET /api/movies?start=&size=500  fetchItems(), pages until a short page
 ├─ startRotation(cfg, items) ...... shuffles again client-side, primes poster A,
 │                                   then setInterval(swap, rotateSec * 1000)
 └─ if cfg.plexDevices.length ...... startNowPlayingMonitor(cfg)
                                     polls /api/now-playing every 1s (POLL_MS)
```

Any thrown error in `init()` replaces the page body with a full-screen message
that names the settings URL — see `showError()`. That is the only error UI; there
is no retry loop, so a proxy that comes up late needs a kiosk restart.

## How "now playing" stays current

Two jobs are deliberately separate:

- **Detecting changes** (a start, stop, pause, seek or new item) is push-driven.
  When `app.py` runs as the service, `__main__` starts a
  [`plex_events.NowPlayingMonitor`](../proxy/plex_events.py) thread. It keeps
  Plex's websocket (`/:/websockets/notifications`) open and treats each playback
  notification about a monitored (or not-yet-seen) session as a trigger to
  re-fetch `/status/sessions` and rebuild the response with the normal
  device/library filtering. `/api/now-playing` answers from that cache, so the
  kiosk's 1 s polls never reach Plex. Notifications about other people's
  sessions are ignored once the monitor has seen that they aren't on a
  monitored device.
- **The progress bar** runs locally in the kiosk. Plex only learns the position
  from the player's ~10 s reports, so the kiosk extrapolates from the last
  `viewOffset` and its `offsetAt` timestamp, redrawing every 250 ms, and only
  while `state` is `playing`. Pause freezes it. A seek or a fresh report resyncs
  it.

Measured on the real server with an Xbox (2026-09-26):
- Pause, resume, seek and stop reach Plex within ~1 s.
- Position reports arrive every 10 s.
- `/status/sessions` is ~3–5 KB per active session in the house.
- In a 45 s live run the monitor fetched sessions 6 times; a 1 s poll would have
  fetched 45 times.

Fallbacks and timing guards:
- Every triggered refresh is followed by one more 1.5 s later, in case the
  notification beat `/status/sessions` to the new state.
- While connected, the monitor still does a safety refresh 30 s after its last
  attempt. Its wait is capped at that deadline, so a stream of ignored
  notifications cannot postpone it.
- An idle socket gets a keepalive ping. No frame within 10 s of a ping counts as
  a dead connection.
- If the websocket can't connect (3 s connect timeout) or drops, the monitor
  polls every 2 s and retries the connection every 10 s.
- A cache older than 45 s is ignored, and the endpoint asks Plex directly (the
  path tests always take, because they never start the monitor).

`offsetAt` has a subtlety. Plex repeats the same stale `viewOffset` between
reports, so the monitor keeps the time an offset was *first* seen, not the time of
the latest fetch. The kiosk applies the same rule to its own anchor.

**Up next.** Sessions don't say which play queue they belong to, but the
notifications do (`playQueueID`, `playQueueItemID`, keyed by the player's
`clientIdentifier`). The monitor remembers each player's queue. When the playing
item changes, it fetches `/playQueues/<id>` once (`monitor_queue()` in `app.py`)
and attaches the next three items as `upNext`. Plex moves a queue's "selected"
item only once the next video actually starts, so the lookup is made relative to
the item in the notification, with one item of look-back. A lookup that can't
find that item yet is not cached, and the 1.5 s follow-up refresh retries it.
Two more rules:
- The refreshed playback state is published *before* the queue lookup (3 s
  timeout), so a slow `/playQueues` can never delay the news that the next item
  started.
- A player's recorded queue position is used only while its notification's
  `ratingKey` matches the playing item. Otherwise, such as after a change seen
  only by fallback polling, `upNext` is empty until the next notification.

## Display modes

There are exactly two visual states, tracked by the `currentMode` variable:

```
        ┌──────────────────────────────────────────┐
        │              rotation                    │
        │  #stage visible, posters crossfade every  │
        │  rotateSec seconds                        │
        └──────────────────────────────────────────┘
             │                            ▲
   /api/now-playing                       │  /api/now-playing returns
   returns playing:true                   │  playing:false for 3 s, or
   (checked every 1s)                     │  8 s if more is queued
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
- Subsequent polls only update playback (state, position) **unless the playing
  item changed** (a different `ratingKey`, e.g. the next video in a playlist).
  Then the whole screen is re-rendered for the new item.
- When the session disappears, the bar freezes and the wall waits before
  returning to rotation. Plex drops the old session before the next queued item
  appears. Usually the gap is 0.1–0.5 s, but a slow-loading next item leaves **no
  session for up to ~4.2 s**, which is indistinguishable from a real Stop while it
  is happening (measured 2026-09-26; the queue doesn't move early either). So:
  - **Nothing more queued** (`upNext` empty): back to rotation after 3 s
    (`STOP_GRACE_MS`).
  - **More queued:** wait up to 8 s (`QUEUE_GRACE_MS`), with the "loading" look
    after 1 s (`LOADING_LOOK_AFTER_MS`, so normal gaps never show it): the art
    dims and the pause badge becomes a spinner. The cost is that a real Stop
    mid-queue holds this look for up to 8 s.
  - `?state=loading` on a preview URL shows the loading look.
- **Paused** (all media): the art dims to 55% and a pause badge is centred on it.
  `placePauseBadge()` positions it from the art's bounding box, because the art
  sits differently in the movie and music layouts. The wall stays on the
  now-playing screen while paused. `?state=paused` on a preview URL shows it.
- `nowplaying` has a music-video variant. When `/api/now-playing` reports
  `mediaType: "musicvideo"` (a session from a `musicVideoSectionId` library),
  `#nowShowing` gets the `music` class, and the layout follows Spotify's
  now-playing screen:
  - The marquee and metadata badges are hidden.
  - The square art sits on a solid background. `computeArtColors()` takes a
    16×16 sample of the art and paints the average colour, darkened to 55%, on
    `#nowShowingBackdrop`.
  - It also picks an **accent**: the average of the most vivid pixels
    (saturated, neither near-black nor near-white), lifted to 66% lightness
    and at least 55% saturation so it stands out on the dark background. It's
    set as `--music-accent` on `#nowShowing`. Near-greyscale art keeps the
    white default.
  - Below the art, left-aligned: the song (bold), then the artist, then a slim
    progress bar in the accent colour.
  - Below that, **"Up next"** (label in the accent colour): up to three
    upcoming items from `upNext` as square covers with song and artist, drawn
    by `renderUpNext()`, which HTML-escapes every title.
  - Everything is scoped under `.now-showing.music`, so the movie/TV layout is
    untouched. CSS `order` re-sequences the shared elements rather than the DOM.

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
