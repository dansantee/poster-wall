# Configuration reference

All runtime settings live in a single JSON file on the Pi, `proxy/config.json`,
and are edited through the settings page at
`http://<pi-hostname>:8088/settings.html`.

The file is **gitignored** because it holds your Plex token in plain text. There
is no schema and no migration step: unknown keys are preserved, missing keys fall
back to the defaults below.

Nothing reads this file at boot except the proxy. The kiosk reads it over HTTP at
page load, so changing a setting takes effect on the wall when the page reloads —
that is what the **Restart Kiosk** button is for.

## Example

```json
{
  "plexUrl": "http://192.168.1.3:32400",
  "plexToken": "xxxxxxxxxxxxxxxxxxxx",
  "plexInsecure": true,
  "sectionId": ["1", "3"],
  "plexDevices": ["192.168.1.100"],
  "rotateSec": 60,
  "posterTransitions": true,
  "transitionTypes": ["scale-fade"],
  "autoDim": true,
  "autoDimStrength": 0.5,
  "nowShowingText": "NOW SHOWING",
  "nowShowingFont": "'Cinzel', serif",
  "nowShowingFontSize": 22,
  "nowShowingFontWeight": 500,
  "nowShowingKerning": 0.1,
  "nowShowingColor": "#f4e88a",
  "progressBarColor": "#f4e88a",
  "progressTrackColor": "#788496",
  "progressTrackOpacity": 0.92,
  "progressBarPadding": 0.3,
  "progressBarHeight": 0.5
}
```

## Plex connection

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `plexUrl` | string | `""` | Base URL of the Plex server. Saved without a scheme, `http://` is added — by the settings page and again by the proxy on `PUT`. Overridden by the `PLEX_URL` environment variable. |
| `plexToken` | string | `""` | Plex auth token, stored in plain text. Overridden by the `PLEX_TOKEN` environment variable. See [the README](../README.md#getting-your-plex-token) for how to find it. |
| `plexInsecure` | boolean | `false` | Skip TLS certificate verification on every Plex call. Needed for self-signed certs; leave off for plain `http://`. |
| `sectionId` | array of strings | `["1"]` | Plex library section IDs to include. Entered comma-separated in the UI. A bare string is still accepted for backward compatibility. Controls **both** the poster rotation and which libraries "Now Playing" reacts to. |
| `plexDevices` | array of strings | `[]` | IP addresses or hostnames whose playback triggers "Now Showing" mode. One per line in the UI. **Empty disables the feature entirely** — the proxy never even polls Plex. Matched case-insensitively against `Player.address`. |

Finding a section ID: open `http://<plex>:32400/library/sections?X-Plex-Token=<token>`
and read the `key` attribute of each `Directory`.

## Rotation

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `rotateSec` | number | `10` | Seconds per poster. Floor of 3 is enforced in three places: the HTML `min`, the settings-page save, and the kiosk's own normalisation. The UI caps it at 3600. |
| `posterTransitions` | boolean | `false` | Off means a plain opacity crossfade. On enables the animated transitions below. |
| `transitionTypes` | array of strings | `["crossfade"]` | Pool of effects; one is chosen at random per poster change. Never saved empty — the settings page falls back to `["crossfade"]`. |

Valid `transitionTypes` values — each is half of a CSS class name, so they must
match `.poster.transition-<value>` rules in `styles.css`:

`crossfade`, `slide-left`, `slide-right`, `slide-up`, `slide-down`, `flip`,
`scale-fade`

`styles.css` also contains rules for `blur-transition`, which no setting can
select. `tests/test_frontend_contract.py` asserts that list stays as-is, so
exposing or removing it is a deliberate change.

## Auto-dimming

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `autoDim` | boolean | `false` | Measure each poster's average brightness and dim the bright ones. |
| `autoDimStrength` | number | `0.5` | CSS `brightness()` multiplier applied to a poster that trips the threshold. Clamped to 0.2–1; `1` means no visible dimming. Lower is darker. |

The threshold itself is not configurable: a poster is dimmed when its average
Rec. 601 luma over a 32×32 sample is ≥ 200 of 255.

## "Now Showing" marquee

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `nowShowingText` | string | `"NOW SHOWING"` | The marquee text. This is *all* the text shown — the movie or episode title is not rendered. |
| `nowShowingFont` | string | `"'Bebas Neue', sans-serif"` | A CSS `font-family` value, chosen from the dropdown. Google Fonts options are preloaded by `index.html`; anything else must be installed on the Pi. |
| `nowShowingFontSize` | number | `9` | Size in `vw`. Rendered as `clamp(size*5.33px, size vw, size*10.67px)` so it stays sane on odd resolutions. UI range 5–20. |
| `nowShowingKerning` | number | `0.1` | `letter-spacing` in `em`. UI range −0.5 to 1. |
| `nowShowingFontWeight` | number | `700` | 300–900 in steps of 100. Only weights the loaded font actually ships will look different. |
| `nowShowingColor` | string | `"#F4E88A"` | Marquee text colour, written to the `--now-showing-color` custom property. |

Some fonts get extra treatment in `applyFontSettings()` — `text-transform:
uppercase` for the display faces, plus `small-caps` for Playfair Display and
Libre Baskerville, and `font-stretch: condensed` for the Impact combination.

## Progress bar

Shown only in "Now Showing" mode, reflecting `viewOffset / duration`.

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `progressBarColor` | string | `"#F4E88A"` | The filled portion (`--progress-bar-color`). |
| `progressTrackColor` | string | `"#788496"` | The unfilled track. Combined with the opacity below into an `rgba()` value for `--progress-track-color`. Must be a 6-digit hex; anything else is ignored and the CSS default stands. |
| `progressTrackOpacity` | number | `0.92` | Track alpha, clamped to 0.1–1. |
| `progressBarPadding` | number | `1.5` | Vertical space around the bar, in `vh`. |
| `progressBarHeight` | number | `2.5` | Bar thickness, in `vh`. |

## Server-injected and legacy keys

| Key | Notes |
| --- | --- |
| `hostname` | Added by the proxy on every `GET /api/config`; the kiosk uses it to print the settings URL on its error screen. Never set it by hand. It does get written back into the file (see below), which is harmless. |
| `transitionType` | Singular. A pre-multi-select leftover; nothing reads it. |
| `excludedLibraries` | An abandoned blacklist approach; library selection is now a whitelist via `sectionId`. Nothing reads it. |

Both legacy keys are preserved on save rather than stripped. Deleting them by
hand is safe.

## Known quirks

- **`PUT` replaces, it does not merge.** A client must send the whole document.
  `settings.js` does this by spreading the config it loaded.
- **Server-injected keys are written back.** Because of that spread, `hostname`
  lands in `config.json`. `GET` always overwrites it, so it is cosmetic.
- **Zero is not a settable value for several numbers.** The settings page saves
  with `Number(value) || default`, so entering `0` for `progressBarPadding`,
  `progressBarHeight`, `nowShowingKerning` or `nowShowingFontSize` silently
  stores the default instead. Use a small non-zero value such as `0.01`.
- **The settings page cannot authenticate.** `settings.js` will send an
  `X-Admin-Key` header if an `adminKey` input exists, but `settings.html` has no
  such field. With `PW_ADMIN_KEY` set on the proxy, saving from the UI returns
  403; use `curl`/`Invoke-RestMethod` with the header instead.
- **Saving does not reload the wall.** The kiosk only re-reads config on page
  load. Press **Restart Kiosk** after saving.

## Environment variables

Several settings can be forced from the environment, which is how the example
units in [`systemd-examples.txt`](systemd-examples.txt) pin a server without a
config file. Environment values **win** over the saved config, so a stray
`PLEX_URL` in a unit file makes the settings-page field look ignored. The full
list is in [API.md](API.md#environment-variables).
