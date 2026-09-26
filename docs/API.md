# Proxy API reference

Everything is served by [`proxy/app.py`](../proxy/app.py) on port **8811**.
There is no versioning and no authentication except the optional admin key on
the two mutating endpoints.

All responses carry permissive CORS headers:

```
Access-Control-Allow-Origin:  *
Access-Control-Allow-Methods: GET, PUT, POST, OPTIONS
Access-Control-Allow-Headers: Content-Type, X-Plex-Token, X-Plex-Url, X-Allow-Insecure, X-Admin-Key
```

Every endpoint below also answers `OPTIONS` with `204` so browser preflights
succeed.

## How the proxy decides which Plex to call

Three inputs are resolved per request, each with its own precedence order.

**Plex base URL** (`resolve_base`) — first non-empty wins:

1. `PLEX_URL` environment variable
2. `X-Plex-Url` request header
3. `?url=` query parameter
4. `plexUrl` in `config.json`

A value with no scheme gets `http://` prepended. If all four are empty the
request fails with `400`.

**Plex token** (`token_from`) — first non-empty wins:

1. `PLEX_TOKEN` environment variable
2. `X-Plex-Token` request header
3. `?token=` query parameter
4. `plexToken` in `config.json`

**TLS verification** (`insecure_from`):

1. `X-Allow-Insecure` header or `?insecure=` query parameter — `1/true/yes/on`
   disables verification, `0/false/no/off` **forces it back on**
2. otherwise `plexInsecure` in `config.json`
3. otherwise the `ALLOW_INSECURE` environment variable

Note the asymmetry on `/api/poster`: it treats its own `?insecure=` flag as an
override in *both* directions, so `insecure=0` re-enables verification even when
the saved config disables it.

---

## `GET /api/ping`

Liveness check. Returns `200` with the plain-text body `pong`. Backs the
settings page's **Test Proxy** button.

---

## `GET /api/build-info`

What code the Pi is actually running. Shells out to `git` in the repo root.

```json
{
  "commit": "175def0d0a1f…",
  "shortCommit": "175def0",
  "dirty": true,
  "status": [" M web/app.js", "?? notes.txt"],
  "hostname": "poster-wall"
}
```

| Field | Notes |
| --- | --- |
| `commit` | Full 40-char `HEAD` SHA |
| `shortCommit` | Abbreviated SHA, shown in the UI |
| `dirty` | `true` when `git status --short` is non-empty; `null` on failure |
| `status` | `git status --short` split into lines; `[]` when clean |
| `hostname` | `socket.gethostname()`, always present |
If git is missing or fails, the response is still `200` but carries
`{"error": "<message>", "dirty": null, "status": []}`. The settings page renders
`error` verbatim.

This is how you tell a **direct deploy** apart from a repo deploy: after a
direct deploy the commit is unchanged but `dirty` is `true`.

---

## `GET /api/config`

Returns `config.json` as-is, plus two things the file may not contain:

- `hostname` — always injected
- `posterTransitions` (default `false`) and `transitionTypes` (default
  `["crossfade"]`) — filled in only if absent

A missing or corrupt config file yields the defaults rather than an error, so a
fresh install still renders. Never gated by the admin key.

See [CONFIGURATION.md](CONFIGURATION.md) for every key.

---

## `PUT /api/config`

Body: a JSON object. Replaces the file wholesale.

| Response | When |
| --- | --- |
| `200 {"ok": true}` | Saved |
| `400 {"error": "invalid body"}` | Body is valid JSON but not an object |
| `400 {"error": "…"}` | Body is not parseable JSON |
| `403 {"error": "forbidden"}` | `PW_ADMIN_KEY` is set and `X-Admin-Key` does not match |

The only normalisation applied is on `plexUrl`: a non-empty value without a
scheme gets `http://`. Unknown keys are preserved verbatim, which is what keeps
old installs working across upgrades.

Because this is a replace and not a merge, a client must send the whole
document. `web/settings.js` does that by spreading the config it loaded.

```powershell
$body = @{ rotateSec = 45; sectionId = @('1','3') } | ConvertTo-Json
Invoke-RestMethod -Method Put -Uri http://poster-wall:8811/api/config `
  -ContentType 'application/json' -Body $body
```

---

## `GET /api/movies`

The poster rotation feed: every movie and show from the selected libraries,
shuffled, with a ready-to-use poster URL each.

**Query parameters**

| Name | Default | Notes |
| --- | --- | --- |
| `start` | `0` | Offset into the shuffled list |
| `size` | `500` | Page size, clamped to 1–1000. `limit` is an accepted alias |
| `section` | — | Overrides the configured sections with this single one |

**Response**

```json
{
  "start": 0,
  "size": 500,
  "returned": 412,
  "totalSize": 418,
  "items": [
    {
      "title": "Arrival",
      "year": 2016,
      "addedAt": 1500000000,
      "type": "movie",
      "mediaType": "movie",
      "poster": "/api/poster?base=http%3A%2F%2F…&thumb=%2Flibrary%2F…&token=…&w=1200&h=1800&insecure=0"
    }
  ]
}
```

- `poster` is a **relative** URL. The kiosk prefixes it with the proxy origin
  (`prox()` in `app.js`).
- `type` is Plex's own value; `mediaType` is the same value normalised to
  `movie` or `show`.
- `returned` counts items in this page; `totalSize` counts what Plex returned
  before the artwork filter, so `totalSize` can exceed the number of renderable
  items.

**Behaviour**

- Asks each section for up to 2000 items sorted `addedAt:desc`, then keeps only
  `type` of `movie` or `show` — music, photos and loose episodes are dropped.
- Items with no `thumb` are dropped, since there would be nothing to show.
- A section that errors or returns non-JSON is skipped; the other sections still
  return. One unreachable library never fails the whole request.
- The combined list is shuffled **on every request**. Consequence: paging is not
  stable. For libraries under 500 items — one page — this never shows.

**Errors**

| Response | When |
| --- | --- |
| `400 {"error": "PLEX_TOKEN not configured server-side; client token missing"}` | No token from any source |
| `400 {"error": "No Plex URL provided…"}` | No base URL from any source |

---

## `GET /api/poster`

Streams a poster image, transcoded by Plex to the requested size. Every image
the kiosk shows comes through here.

**Query parameters**

| Name | Default | Notes |
| --- | --- | --- |
| `base` | resolved from config | Plex base URL |
| `thumb` | **required** | Plex art path, e.g. `/library/metadata/101/thumb/1` |
| `token` | resolved from config | Plex token |
| `w` | `600` | Width; `/api/movies` emits `1200` |
| `h` | `900` | Height; `/api/movies` emits `1800` |
| `insecure` | from config | `1` to skip TLS verification, `0` to force it |

Upstream call:

```
GET {base}/photo/:/transcode?url={base+thumb}&width={w}&height={h}&minSize=1&X-Plex-Token={token}
```

**Response** — the upstream body, streamed in 64 KiB chunks, with the upstream
status code, the upstream `Content-Type` (defaulting to `image/jpeg`) and
`Cache-Control: public, max-age=86400`.

| Response | When |
| --- | --- |
| `400 {"error": "missing base/thumb/token"}` | No `thumb`, or no token from any source |
| `400 {"error": "No Plex URL provided…"}` | No base from any source |
| `502 {"error": "Upstream request error: …"}` | Plex unreachable |
| upstream status | Anything else, passed through unchanged |

---

## `GET /api/now-playing`

Reports playback, but **only** from the device addresses listed in
`plexDevices` and **only** from libraries listed in `sectionId` or
`musicVideoSectionId`. Both filters are whitelists; anything unlisted is
invisible to the wall.

The kiosk polls this every second, so it always answers `200` — upstream
problems are reported in the body, not as an HTTP error.

When the proxy runs as the service, it answers from a cache that its background
monitor keeps fresh from Plex's websocket (see ARCHITECTURE.md, "How now
playing stays current"), so these polls do not reach Plex. With no monitor, or a
cache older than 45 s, it asks Plex directly on every call. The device switch
below is checked first either way.

**Not playing**

```json
{ "playing": false, "message": "No devices configured" }
{ "playing": false, "message": "No active sessions on monitored devices" }
{ "playing": false, "error": "Sessions request failed: 500" }
{ "playing": false, "error": "Sessions check failed: …" }
```

With no `plexDevices` configured the proxy returns immediately and never
contacts Plex at all.

**Playing**

```json
{
  "playing": true,
  "state": "playing",
  "offsetAt": 1790411725120,
  "title": "Severance - S1E2 - Good News About Hell",
  "year": 2022,
  "rating": "TV-MA",
  "poster": "/api/poster?…",
  "progress": 25.0,
  "duration": 20000,
  "viewOffset": 5000,
  "videoResolution": "1080",
  "videoCodec": "H264",
  "audioCodec": "EAC3",
  "audioChannels": "6.1",
  "playerTitle": "Bedroom",
  "mediaType": "episode",
  "ratingKey": "202",
  "artist": "",
  "trackTitle": "",
  "playerId": "vqjl5hn59g1c4sdloc5tetxq",
  "upNext": [
    { "title": "Severance - S1E3 - In Perpetuity", "artist": "", "trackTitle": "Severance - S1E3 - In Perpetuity",
      "poster": "/api/poster?…&w=400&h=400&…" }
  ]
}
```

| Field | Notes |
| --- | --- |
| `title` | Movies use the title; episodes become `Show - S<season>E<episode> - Episode`, with `?` for missing indices |
| `progress` | `viewOffset / duration * 100`, clamped to 0–100, rounded to 1 decimal; `0` when duration is 0 |
| `duration`, `viewOffset` | Milliseconds, as Plex reports them |
| `videoCodec`, `audioCodec` | Uppercased |
| `audioChannels` | `"<n>.0"` for mono/stereo, `"<n>.1"` above that, `""` when unknown. Plex counts LFE, so a 5.1 track arrives as 6 channels and is reported as `6.1`; `app.js` maps that back to the 5.1 icon |
| `poster` | Relative proxy URL, or `null` if no artwork could be found |
| `state` | Plex's `Player.state`: `playing`, `paused` or `buffering` (`playing` if absent). The kiosk advances the bar only while `playing` and shows the pause treatment for `paused` |
| `offsetAt` | When `viewOffset` was observed, in ms since the epoch (proxy clock, which is the kiosk's clock on the Pi). The monitor keeps the *first* time an unchanged offset was seen, because Plex repeats a stale offset between the player's ~10 s reports |
| `mediaType` | `movie`, `episode`, or `musicvideo` for a session from a `musicVideoSectionId` library |
| `ratingKey` | Plex's id for the playing item. The kiosk compares it between polls to notice a playlist moving to the next item without a stop |
| `artist`, `trackTitle` | Music videos only (empty otherwise): the title split on its first `" - "`. A title with no separator is all `trackTitle` |
| `playerId` | The player's `machineIdentifier`. Plex's notifications identify players by it (as `clientIdentifier`), which is how the monitor finds the session's play queue |
| `upNext` | Only from the monitor's cache (absent on the direct path). Up to 3 items after the current one in the player's Plex play queue, in queue order (so shuffle is respected). Each item has `title`, `artist`/`trackTitle` (split as above for music-video libraries; otherwise `artist` is empty and `trackTitle` is the whole title) and a 400×400 `poster`. `[]` when nothing more is queued or the queue isn't known yet. The kiosk also uses a non-empty list as its sign that another item is coming: when the session disappears it waits up to 8 s instead of 3 s (see ARCHITECTURE.md) |

**Filtering order** — a session must pass all of these:

1. `Player.address` is in `plexDevices` (compared lowercased and stripped)
2. `librarySectionID` is in `sectionId` or `musicVideoSectionId` (compared as strings)
3. `type` is `movie` or `episode` (music videos in an "Other Videos" library arrive as `movie`)

**Music video artwork** is the session `thumb`, i.e. the item's Plex poster. An
"Other Videos" library has no metadata agent, so that poster is a video frame
unless an `Artist - Title.jpg` sidecar sits next to the video file; Plex then
uses the sidecar. The kiosk draws the art square over a blurred copy of itself.

The first session that passes wins. Everything else is ignored.

**Episode artwork** is chosen in this order, because an episode's own `thumb` is
usually a video frame:

1. `thumb` from a metadata lookup on `parentRatingKey` (the season)
2. `parentThumb` from that same lookup (the show)
3. `parentThumb` on the session (season art)
4. `grandparentThumb` on the session (show art)
5. `thumb` on the session — the video frame, as a last resort

The extra lookup is `GET {base}/library/metadata/{parentRatingKey}`. If it fails
the fallbacks still apply; movies skip it entirely.

---

## `POST /api/restart-kiosk`

Runs `systemctl --user restart poster-kiosk.service` with a 10-second timeout.
Only the kiosk unit — restarting the proxy from inside the proxy would drop the
response.

| Response | When |
| --- | --- |
| `200 {"ok": true, "message": "Kiosk service restart initiated"}` | Command exited 0 |
| `403 {"error": "forbidden"}` | `PW_ADMIN_KEY` set and `X-Admin-Key` mismatched |
| `500 {"error": "Command failed: …"}` | Non-zero exit, with stderr |
| `500 {"error": "Restart command timed out"}` | Exceeded 10s |
| `500 {"error": "Failed to restart kiosk: …"}` | `systemctl` missing, etc. |

Use this after changing settings that the kiosk only reads at page load.

---

## `GET /debug/routes`

Plain-text list of every registered route. Useful for confirming which build of
the proxy is live.

---

## Environment variables

Read by `proxy/app.py`. All optional. The three marked *import-time* are read
once at startup, so changing them means restarting `poster-proxy.service`.

| Variable | Default | Effect |
| --- | --- | --- |
| `PLEX_URL` | — | Base URL; **outranks** the saved config |
| `PLEX_TOKEN` | — | Token; **outranks** the saved config (*import-time*) |
| `SECTION_ID` | `1` | Fallback section when the config has none (*import-time*) |
| `TIMEOUT` | `10` | Seconds for every upstream Plex request (*import-time*) |
| `ALLOW_INSECURE` | unset | `1/true/yes/on` disables TLS verification by default (*import-time*) |
| `PW_CONFIG_PATH` | `config.json` | Config file location (*import-time*) |
| `PW_ADMIN_KEY` | unset | When set, `PUT /api/config` and `POST /api/restart-kiosk` require `X-Admin-Key` (*import-time*) |

`PW_CONFIG_PATH` defaults to a **relative** path, so it resolves against the
process working directory. The systemd unit sets `WorkingDirectory` to `proxy/`,
which is why the file lands at `proxy/config.json`. Start the proxy from
anywhere else and it will read and write a different file.

Setting `PW_ADMIN_KEY` currently locks the settings page out of saving — see
"Known rough edges" in [ARCHITECTURE.md](ARCHITECTURE.md).
