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
- Until the new item's own list is known, `upNext` is what followed that item
  in the player's last non-empty list (kept across the few seconds with no
  session between videos). Without it, the first body of the next song carried
  an empty row, and the kiosk's advance animation blanked the row for a moment.
  An item not in the old list gets `[]`, and a looked-up list, even an empty
  one at the end of the queue, always wins.

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
  - **Nothing more queued** (`upNext` empty), or a movie or episode: back to
    rotation after 3 s (`STOP_GRACE_MS`). An episode's queue is the show's next
    episodes, so the hold made backing out of a show sit on the loading look for 8 s
    (Dan, 2026-09-30). The cost: when the next episode auto-plays 3–8 s later (Plex's
    5-second post-play countdown, say), posters show in between.
  - **More music videos queued:** wait up to 8 s (`QUEUE_GRACE_MS`), with the "loading" look
    after 1 s (`LOADING_LOOK_AFTER_MS`, so normal gaps never show it): the art
    dims and the pause badge becomes a spinner: a ring that fades around its
    length, turning every 1.5 s. It was designed when the wall ran 4K at
    **30 Hz**, where a solid arc turning once a second visibly stepped (it now
    runs 1080p60). The cost is that a real Stop
    mid-queue holds this look for up to 8 s.
  - `?state=loading` on a preview URL shows the loading look.
- The movie/TV layout is one centred column with a single `1.5vw` gap between
  the marquee, the progress bar, the poster and the badge row. The bar, poster and
  badges run edge to edge (Dan, 2026-09-30). A 2:3 poster is 1620 px tall at 1080
  wide, so the gaps are kept tight enough to leave it that height; with the old
  `3vw` margins and `2.5vw` gaps it was height-bound and drew 32 px of black down
  each side. The poster has a set width (`100vw`), so a small poster still scales
  up to the edges. Each badge's box is the drawn badge, so the
  space around the row is real. The rules are `.now-showing:not(.music)`, so music
  mode is unaffected.
- **The poster always spans the width** (Dan, 2026-09-30: no black side bars, whatever
  else changes).
  - When the stack leaves the poster a box a little shorter than the poster, the box
    stays 100vw wide and `fitPoster()` adds `.fill` (`object-fit: cover`). The poster then
    loses a sliver top and bottom instead of drawing bars.
  - It runs on the poster's load, after the details line changes, and on resize.
  - The crop is capped at 10% (`POSTER_FILL_MAX_CROP`). Anything needing more, such as a
    16:9 episode frame used as last-resort artwork, is still drawn whole.
- **The marquee and bar take the poster's colour.** `computeArtColors()`, the music
  screen's accent, picks the poster's vivid colour. That colour becomes `--movie-accent`
  on `#nowShowing`, and the title (with its glow), the bar and the end time use it.
  - The previous colour stays until the new one is known.
  - A poster with no vivid colour, one that fails to load, or the way back to the
    rotation clears it. The configured `nowShowingColor` / `progressBarColor` apply
    then: `:root` holds `--movie-accent: initial`, so the `var()` fallbacks win.
- **The details line** sits between the bar and the poster. It shows what's on
  (`S2 · E9 · Wax Patrol` from the episode title, or `Ghosted · 2023`) and, at the
  right, `Ends 11:47 PM`.
  - The end time is recomputed on every progress tick from the local playback clock,
    so a pause pushes it out.
  - Negative margins tuck the line into the stack's gaps, so it costs the poster as
    little height as possible.
  - It is hidden in music mode.
- The row's three badges (resolution, audio, rating) are drawn, not loaded:
  `videoBadge()`, `audioBadge()` and `ratingBadge()` in `app.js` turn the
  now-playing fields into a spec, and `badgeHtml()` renders it in the look of the
  PNG set they replaced (2026-09-30). That look is a gold or silver band with a lead
  label and a black box, or a rating-coloured band with the rating, a divider and its
  meaning.
  - Sizes are in `cqw` of the badge (`container-type: inline-size`, aspect
    1408:238). A label's `--chars` shrinks it to fit.
  - The font is League Spartan, served from `web/fonts/`.
  - Dolby formats (Atmos, TrueHD, Digital, Digital+, Vision) carry the Dolby
    double-D mark, an inline SVG from Simple Icons (CC0). Other audio gets a speaker.
  - Video: `UHD | 4K`, `HD | 1080`, `SD | 480`. With HDR it's the resolution and
    then the format (`4K | HDR10+`, `4K | ◖◗ VISION`).
  - A rating with no entry gets a grey `RATED <x>` badge. A blank rating gets `N/A`.
  - Why: the PNGs went by channel count alone. Atmos showed "5.1 SURROUND", 7.1
    tracks had no badge, and 480p, HDR and most TV ratings had no icon.
  - At 1080p, `nowShowingFontSize` 11 (the wall's setting) just fits the marquee on one
    line. At 10 it has side margins.
  - A size too large for one line wraps.
- **Paused** (all media): the art dims to 55% and a pause badge is centred on it.
  `placePauseBadge()` positions it from the art's bounding box, because the art
  sits differently in the movie and music layouts. The wall stays on the
  now-playing screen while paused. `?state=paused` on a preview URL shows it.
- `nowplaying` has a music-video variant. When `/api/now-playing` reports
  `mediaType: "musicvideo"` (a session from a `musicVideoSectionId` library),
  `#nowShowing` gets the `music` class, and the layout follows Spotify's
  now-playing screen:
  - The marquee and metadata badges are hidden.
  - The art sits in a square frame on a solid background. A non-square cover is
    drawn whole, and its bars are a darker shade of the background
    (`--music-art-bars`, 55% black over it), so the square still reads as a frame.
    The up-next tiles and the cover flying up on a song change use the same fit and
    bars, so the shape never jumps. About
    1 in 9 library covers is non-square: CD scans around 1.15:1, film posters and
    16:9 frames. `computeArtColors()` takes a
    16×16 sample of the art and paints the average colour, darkened to 55%, on
    `#nowShowingBackdrop` (`backdropColor()`).
    - When the sample's darkest side is dark (the mean HSL lightness of its top,
      bottom, left or right row, under `BACKDROP_DARK_EDGE`, 0.12), the backdrop
      is kept at least `BACKDROP_EDGE_GAP` (0.18) lighter than that side, with its
      own hue and saturation.
    - Without that, a cover with a black border or black design (Billie Jean,
      TOOL's Sober) melted into a near-black backdrop.
    - It uses the darkest side rather than the whole ring because of Levitating:
      its top and right edges are black while its left and bottom are bright. The
      ring average was just over the limit, so its backdrop stayed rgb(23,21,20).
    - Covers whose backdrop is already that much lighter keep the plain darkened
      average.
    - `test_the_music_backdrop_stays_lighter_than_a_dark_edged_cover` runs this
      code in Node when it's installed.
  - It also picks an **accent**: the average of the most vivid pixels
    (saturated, neither near-black nor near-white), lifted to 66% lightness
    and at least 55% saturation so it stands out on the dark background. It's
    set as `--music-accent` on `#nowShowing`. Near-greyscale art keeps the
    white default.
  - Below the art, left-aligned: the song (bold), then the artist, then a slim
    progress bar in the accent colour.
  - The song and artist lines each stay on **one line**. When one is too wide,
    `setScrollingText()` ping-pongs it with the Web Animations API: a 2 s pause,
    a slide to its end at 70 px/s, a 2 s pause, a slide back, repeat. It loads the
    line's own font explicitly (`document.fonts.load`) and measures again once
    that font is ready, because Montserrat is wider than its fallback and only
    starts loading once music text is on screen. Wrapping to a second line used to push the rest of the layout
    down.
  - Below that, **"Up next"** (label in the accent colour): up to three
    upcoming items from `upNext` as covers in square tiles with song and artist, drawn
    by `renderUpNext()`, which HTML-escapes every title.
    - Each tile shows the **artist above the song**. The song is the item's
      `shortTitle` from the proxy (`short_title()` in `app.py`), which drops the
      extras:
      - bracketed groups that follow other text ("(from the series ...)",
        "[Remastered]"), including nested groups and groups right after a
        dropped one
      - anything after "ft.", "feat." or "featuring"

      A leading group, as in "(Don't Fear) The Reaper", stays, and so does a
      group glued to a word ("Baby(One More Time)"). A title that would trim to
      nothing is kept whole. The playing song shows `displayTitle`, a lighter trim.
      It drops only tag groups such as "(Videoclip)", "(feat. …)", "(from Aladdin)" and
      "(Director's Cut)". Name parts such as "(The Sweater Song)" and remixes stay. The
      rules are in API.md.
      About 1 in 5 library titles gets shortened.
    - Up-next song titles stay still (three more scrolling lines would be too
      much motion). They get up to two lines with an ellipsis, balanced
      wrapping (`text-wrap: balance`, so no single word on its own line), and a
      fixed two-line height. Because the song comes after the artist, a
      one-line title's spare line falls at the bottom of the tile, where it
      reads as margin rather than as a gap before the artist.
    - The fixed height also keeps the whole screen still. The music layout is
      centred vertically, so a row whose height depended on its titles moved
      everything by ~27 px (at 4K) whenever the queue changed between short
      and long names.
  - **Song changes animate** (`changeTrack()`; music layout only, movies and TV
    still swap instantly). The poll loop waits while a transition runs.
    - When the new song is the first up-next item (same `ratingKey`), that
      cover **flies up into the main art slot**: a `.now-showing-fly` clone of
      the full-size poster sits over the main art and is transformed from the
      tile's box to none over 800 ms. The old art fades and shrinks, the other
      two tiles slide one slot left, and the text fades out. The new content
      then goes in and the song/artist text rises into place.
    - The row moves like a conveyor: the two remaining tiles slide one slot
      left and the new third slides in from the right edge, all with the same
      distance, duration and easing. The new tile is a temporary one pinned to
      grid cell `1 / 3` alongside the leaving one, discarded when the row is
      re-rendered.
    - The top never waits; the row may. On real Plex the new song is usually
      published before its queue lookup answers (the proxy carries over two
      items and sets `upNextPending`; the lookup adds the third ~0.25 s later).
      Then the cover flies up at once, and the row holds, with its first slot
      empty, while `waitForUpNext()` asks the proxy again every 150 ms for up to
      1 s (`THIRD_WAIT_MS`), so all three slide together. It gives up early if
      the song changes or stops, or the proxy's answer has fewer than three
      (the end of the queue).
    - If the answer comes later still, the two slide on their own, the kiosk
      polls again 250 ms after the transition (`AFTER_CHANGE_POLL_MS`), and
      `renderUpNext` slides in any tiles that extend the row already on screen
      (matched by `ratingKey`) at the same pace.
    - Any other change (a skip, a queue we don't know) crossfades the art and
      text instead, and the third tile fades in with the new content.
    - The fly is appended to `#nowShowing`, so it's excluded from the
      `.now-showing > *` rule that makes children `position: relative`;
      otherwise it lands in the flow and pushes the art down.
  - **Fun-fact bubbles** (Pop-Up Video style). A music item's `facts` (a list
    of short strings; none means no bubbles) pop up over a corner of the art,
    alternating top-right and bottom-left, with a white balloon whose tail
    points into the art and a "bloop" scale pop in and out. The first comes at
    15 s, then one per 35 s slot, each held for its reading time (3 s plus 12
    characters a second).
    - The slots cycle through the facts, and each fact shows up to twice
      (`POPUP_REPEATS`), a slow stream for anyone who missed one.
    - `updatePopup()` runs on the progress tick from the playback position, so
      a pause holds a bubble and a seek picks that slot's fact. Only a jump
      back of more than 3 s counts as a seek, since each Plex report
      re-anchors the clock and can step it back a little.
    - Nothing starts in a song's last 15 s or during a song change, and the
      old song's bubble pops out when a change starts.
    - Each bubble's hold is capped at 20 s, so even a very long fact fits in
      its slot.
    - When a pop-out finishes, the bubble is hidden and the pop-out animation
      cancelled, so its `scale(0)` fill can't linger.
    - On the Pi's kiosk, every bubble after the first vanished about 0.5 s in,
      around when its pop-in ended. The suspected cause is that lingering
      fill; headless Chromium doesn't show it, and the fix hasn't been
      confirmed on the device yet.
    - Clock corrections from Plex reports measured only a few ms, so the 3 s
      seek margin is a safety net, not the cause.
    - The facts live in the video's **Plex summary, one per line** (locked so a
      metadata refresh keeps them). `music_video_facts()` in `app.py` reads
      them from the session if Plex includes the summary there, otherwise
      with a `/library/metadata/<ratingKey>` lookup.
    - The lookup runs in a background thread and never inside the refresh
      that announces a new song. That refresh gets what's cached, nothing the
      first time. Only one lookup per item is in flight at a time.
    - Results are cached by `(server, ratingKey, updatedAt)`, so an edit is
      picked up. Entries without an `updatedAt` expire after 10 minutes, and a
      failed lookup isn't cached.
    - The next refresh (the monitor's follow-up comes 1.5 s after a change)
      carries the facts. The kiosk adopts facts arriving on a later poll
      (`adoptLateFacts`) if the song has none yet.
    - `?preview=musicvideo&demo=popup` shows four hardcoded facts.
  - The wall is **1080p portrait (1080×1920, scale 1.0) at 60 Hz** on a 4K TV,
    which upscales. At 4K the Pi 5 drew only 15-20 fps (see RASPBERRY-PI.md,
    `--mode`). Most font sizes are `clamp(min, Nvw, max)`, which hit their pixel
    caps at 4K and not at 1080, so check layout changes at 1080×1920 (and at
    2160×3840 if the wall ever goes back to 4K).
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
web/fonts/          League Spartan (OFL) for the badges; Bebas Neue (OFL), the marquee default
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
- **The marquee never shows the title.** Only the configured `nowShowingText` is
  displayed there. The title goes in the details line under the bar
  (`detailsText()`), and the old `.now-showing-movie-title` rule is unused.
- **No retry on boot.** If the proxy is not answering when Chromium loads the
  page, the kiosk shows the error screen until it is reloaded.
