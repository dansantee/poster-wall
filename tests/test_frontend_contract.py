"""Contract tests for the static frontend.

``web/`` has no build step and no JS test runner, and its three files talk to
each other only through string keys: element ids, CSS class names, CSS custom
properties, icon filenames and config keys. Those joins are exactly what breaks
silently during a rename, so they are asserted here from the source text.

These tests check that the names on both sides of each join still line up. One
exception runs JavaScript: the music backdrop colour test executes app.js's own
colour functions in Node, and skips itself when Node isn't installed.
"""
import colorsys
import json
import re
import shutil
import subprocess

import pytest

APP_JS = "web/app.js"
SETTINGS_JS = "web/settings.js"
INDEX_HTML = "web/index.html"
SETTINGS_HTML = "web/settings.html"
STYLES_CSS = "web/styles.css"
PROXY_PY = "proxy/app.py"

# Ids referenced defensively by settings.js (guarded by `has(...)`) that are
# deliberately absent from the HTML. Adding to this list is a design decision.
OPTIONAL_IDS = {"adminKey"}

# The kiosk cannot render without these.
REQUIRED_INDEX_IDS = {
    "stage",
    "posterA",
    "posterB",
    "nowShowing",
    "nowShowingTitle",
    "nowShowingProgressBar",
    "nowShowingPoster",
    "nowShowingMetadataIcons",
    "nowShowingBackdrop",
    "nowShowingArtist",
    "nowShowingSong",
    "nowShowingPauseBadge",
    "nowShowingUpNext",
}

# The settings page cannot save without these.
REQUIRED_SETTINGS_IDS = {
    "buildInfo",
    "sectionId",
    "rotateSec",
    "plexUrl",
    "plexToken",
    "plexDevices",
    "plexInsecure",
    "btnSave",
    "btnRestart",
    "btnPing",
    "btnTry",
    "btnPreviewRotation",
    "btnPreviewNowPlaying",
    "btnPreviewMusicVideo",
    "musicVideoSectionId",
    "testOut",
}

# Config keys the proxy supplies but the settings page must never write back.
SERVER_OWNED_KEYS = {"hostname"}


# --------------------------------------------------------------------------
# Extraction helpers
# --------------------------------------------------------------------------
def js_element_ids(js):
    """Ids looked up via getElementById, or the has()/el() shorthands."""
    pattern = r"""(?:getElementById|\bhas|\bel)\(\s*['"]([^'"]+)['"]\s*\)"""
    return set(re.findall(pattern, js))


def html_ids(html):
    return set(re.findall(r"""\bid=["']([^"']+)["']""", html))


def js_array_literal(js, name):
    """Parse `const <name> = ['a', 'b'];` into a list of strings."""
    match = re.search(name + r"\s*=\s*\[([^\]]*)\]", js)
    assert match, "could not find the " + name + " array in the source"
    return re.findall(r"""['"]([^'"]+)['"]""", match.group(1))


@pytest.fixture
def transition_types(source):
    types = js_array_literal(source(SETTINGS_JS), "transitionCheckboxes")
    assert types, "settings.js must list the selectable transitions"
    return types


# --------------------------------------------------------------------------
# Element ids
# --------------------------------------------------------------------------
def test_kiosk_uses_only_ids_that_exist_in_index_html(source):
    referenced = js_element_ids(source(APP_JS))
    available = html_ids(source(INDEX_HTML))
    assert referenced <= available, (
        "app.js looks up ids that index.html does not define: "
        + repr(sorted(referenced - available))
    )


def test_index_html_defines_every_required_id(source):
    assert REQUIRED_INDEX_IDS <= html_ids(source(INDEX_HTML))


def test_settings_page_uses_only_ids_that_exist_in_settings_html(source, transition_types):
    referenced = js_element_ids(source(SETTINGS_JS))
    referenced |= {"transition-" + t for t in transition_types}
    available = html_ids(source(SETTINGS_HTML)) | OPTIONAL_IDS
    assert referenced <= available, (
        "settings.js looks up ids that settings.html does not define: "
        + repr(sorted(referenced - available))
    )


def test_settings_html_defines_every_required_id(source):
    assert REQUIRED_SETTINGS_IDS <= html_ids(source(SETTINGS_HTML))


def test_every_settings_input_is_read_by_settings_js(source, transition_types):
    """A control nobody reads is a control that silently does nothing."""
    html = source(SETTINGS_HTML)
    input_ids = set(
        re.findall(r"""<(?:input|select|textarea)[^>]*\bid=["']([^"']+)["']""", html)
    )
    read = js_element_ids(source(SETTINGS_JS)) | {
        "transition-" + t for t in transition_types
    }
    assert input_ids <= read, (
        "settings.html has controls settings.js never reads: "
        + repr(sorted(input_ids - read))
    )


def test_html_ids_are_unique(source):
    for path in (INDEX_HTML, SETTINGS_HTML):
        found = re.findall(r"""\bid=["']([^"']+)["']""", source(path))
        assert len(found) == len(set(found)), "duplicate id in " + path


# --------------------------------------------------------------------------
# Poster transitions: settings.js <-> settings.html <-> styles.css <-> app.js
# --------------------------------------------------------------------------
def test_every_transition_has_a_checkbox(source, transition_types):
    html = source(SETTINGS_HTML)
    for name in transition_types:
        assert 'id="transition-' + name + '"' in html, name + " has no checkbox"


def test_every_transition_has_css(source, transition_types):
    css = source(STYLES_CSS)
    for name in transition_types:
        assert ".poster.transition-" + name in css, (
            name + " is selectable but styles.css has no rule for it, so the "
            "poster would jump instead of animating"
        )


def test_directional_transitions_define_all_three_states(source, transition_types):
    """app.js drives transitions with `entering` -> `visible` -> `exiting`."""
    css = source(STYLES_CSS)
    for name in transition_types:
        if name == "crossfade":
            continue  # crossfade is opacity-only and needs no entering state
        for state in ("entering", "visible", "exiting"):
            selector = ".poster.transition-" + name + "." + state
            assert selector in css, "missing CSS rule " + selector


def test_app_js_builds_the_transition_class_names_css_defines(source):
    assert "`transition-${randomTransitionType}`" in source(APP_JS)


def test_crossfade_is_the_documented_fallback(source, transition_types):
    assert "crossfade" in transition_types
    # Proxy default, kiosk default and settings-page fallback must agree.
    assert "'transitionTypes', ['crossfade']" in source(PROXY_PY)
    assert "['crossfade']" in source(APP_JS)
    assert "['crossfade']" in source(SETTINGS_JS)


def test_unused_css_transitions_are_flagged(source, transition_types):
    """Catches a transition that was styled but never wired into the UI."""
    css = source(STYLES_CSS)
    styled = set(re.findall(r"\.poster\.transition-([a-z-]+?)(?:\.|\s|,|\{)", css))
    unreachable = styled - set(transition_types)
    assert unreachable == {"blur-transition"}, (
        "styles.css transitions that no setting can select changed: "
        + repr(sorted(unreachable))
        + " -- either expose them in settings.js or drop the CSS"
    )


# --------------------------------------------------------------------------
# CSS custom properties written by app.js
# --------------------------------------------------------------------------
def test_every_custom_property_app_js_sets_is_declared_in_root(source):
    app_js = source(APP_JS)
    css = source(STYLES_CSS)
    root = re.search(r":root\s*\{(.*?)\}", css, re.S)
    assert root, "styles.css must declare a :root block with the defaults"
    declared = set(re.findall(r"(--[a-z-]+)\s*:", root.group(1)))
    written = set(re.findall(r"""setProperty\(\s*['"](--[a-z-]+)['"]""", app_js))
    assert written, "app.js should theme the page through custom properties"
    assert written <= declared, (
        "app.js sets custom properties with no :root default, so the kiosk "
        "renders unstyled until config loads: " + repr(sorted(written - declared))
    )


def test_poster_dimming_is_driven_by_the_configured_strength(source):
    css = source(STYLES_CSS)
    assert ".poster.dim" in css
    assert "brightness(var(--poster-dim-brightness" in css
    assert "setProperty('--poster-dim-brightness'" in source(APP_JS)


def test_progress_track_default_matches_the_hex_default(source):
    """The :root rgba() and the settings-page hex default must be the same colour."""
    css = source(STYLES_CSS)
    match = re.search(
        r"--progress-track-color:\s*rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)", css
    )
    assert match, "styles.css must default --progress-track-color to an rgba()"
    r, g, b, alpha = int(match.group(1)), int(match.group(2)), int(match.group(3)), float(match.group(4))
    assert (r, g, b) == (0x78, 0x84, 0x96), "should match the #788496 default"
    assert alpha == 0.92, "should match the progressTrackOpacity default"


# --------------------------------------------------------------------------
# Metadata icons
# --------------------------------------------------------------------------
def test_every_referenced_icon_exists_on_disk(source, repo_root):
    referenced = set(re.findall(r"info-icons/[A-Za-z0-9._-]+\.png", source(APP_JS)))
    assert referenced, "app.js should map metadata to icon files"
    missing = [
        name for name in sorted(referenced) if not (repo_root / "web" / name).is_file()
    ]
    assert missing == [], "app.js references icons that are not in the repo: " + repr(missing)


def test_unrated_content_has_a_fallback_icon(source, repo_root):
    assert "info-icons/Rated-NA.png" in source(APP_JS)
    assert (repo_root / "web/info-icons/Rated-NA.png").is_file()


def test_icon_maps_cover_what_the_proxy_can_report(source):
    """/api/now-playing reports Plex's raw values; app.js must map them."""
    app_js = source(APP_JS)
    for resolution in ("sd", "720", "1080", "4k"):
        assert "'" + resolution + "':" in app_js, "no icon for videoResolution " + resolution
    # The proxy emits "<channels>.0" or "<channels>.1"; 2.0 and 5.1 are the
    # common cases and must resolve to an icon.
    assert "'2.0'" in app_js
    assert "5.1" in app_js


# --------------------------------------------------------------------------
# Fonts
# --------------------------------------------------------------------------
def test_every_specially_styled_font_is_offered_in_the_dropdown(source):
    """app.js applies per-font tweaks by substring; the option must exist."""
    app_js = source(APP_JS)
    options = source(SETTINGS_HTML).lower()
    families = set(re.findall(r"""fontFamily\.includes\(['"]([^'"]+)['"]\)""", app_js))
    assert families, "app.js should special-case at least one font"
    missing = [f for f in sorted(families) if f not in options]
    assert missing == [], "app.js styles fonts that cannot be selected: " + repr(missing)


def test_google_fonts_used_by_the_dropdown_are_preloaded(source):
    """A font offered but not linked would silently fall back to a system face."""
    settings_html = source(SETTINGS_HTML)
    index_html = source(INDEX_HTML)
    google_link = re.search(r"fonts\.googleapis\.com/css2\?([^\"']+)", index_html)
    assert google_link, "index.html must link the Google Fonts stylesheet"
    linked = google_link.group(1).lower()
    for family in ("anton", "bebas+neue", "cinzel", "oswald", "playfair+display", "raleway", "libre+baskerville"):
        assert family in linked, family + " is missing from the kiosk font link"
        assert family.replace("+", " ") in settings_html.lower()


# --------------------------------------------------------------------------
# Config keys: proxy <-> kiosk <-> settings page
# --------------------------------------------------------------------------
def kiosk_config_keys(app_js):
    """Keys the kiosk reads out of /api/config."""
    body = re.search(r"const config = \{(.*?)\n    \};", app_js, re.S)
    assert body, "could not find the loadCfg() config object in app.js"
    return set(re.findall(r"\bj\.([A-Za-z][A-Za-z0-9]*)", body.group(1)))


def settings_written_keys(settings_js):
    """Keys the settings page writes back via PUT /api/config."""
    return set(re.findall(r"\bnext\.([A-Za-z][A-Za-z0-9]*)\s*=", settings_js))


def test_the_settings_page_can_set_everything_the_kiosk_reads(source):
    read = kiosk_config_keys(source(APP_JS))
    written = settings_written_keys(source(SETTINGS_JS))
    orphans = read - written - SERVER_OWNED_KEYS
    assert orphans == set(), (
        "the kiosk reads config keys the settings page cannot set: "
        + repr(sorted(orphans))
    )


def test_the_settings_page_does_not_write_server_owned_keys(source):
    written = settings_written_keys(source(SETTINGS_JS))
    assert written & SERVER_OWNED_KEYS == set(), (
        "hostname is injected by the proxy on GET; writing it back persists a "
        "stale value into config.json"
    )


def test_the_settings_page_writes_nothing_the_kiosk_ignores(source):
    read = kiosk_config_keys(source(APP_JS))
    written = settings_written_keys(source(SETTINGS_JS))
    assert written <= read, (
        "the settings page saves keys nothing consumes: " + repr(sorted(written - read))
    )


def test_both_save_paths_write_the_same_keys(source):
    """settings.js duplicates its save payload for the button and the form submit.

    Until that duplication is factored out, every key must appear in both copies
    or one save path will quietly drop a setting.
    """
    settings_js = source(SETTINGS_JS)
    assignments = re.findall(r"\bnext\.([A-Za-z][A-Za-z0-9]*)\s*=", settings_js)
    counts = {}
    for key in assignments:
        counts[key] = counts.get(key, 0) + 1
    odd = sorted(k for k, n in counts.items() if n != 2)
    assert odd == [], (
        "these keys are not written by both save paths in settings.js: " + repr(odd)
    )


def test_the_settings_page_resaves_the_whole_config_document(source):
    """PUT /api/config replaces the file, so the page must round-trip everything.

    A side effect: the spread also carries back keys the proxy *injected* on GET
    (notably ``hostname``), which is how they end up persisted in config.json.
    See "Known quirks" in docs/CONFIGURATION.md.
    """
    assert source(SETTINGS_JS).count("{ ...cfg }") == 2


def test_the_proxy_defaults_the_keys_the_kiosk_needs_before_first_save(source):
    proxy = source(PROXY_PY)
    for key in ("posterTransitions", "transitionTypes"):
        assert "setdefault('" + key + "'" in proxy


# --------------------------------------------------------------------------
# Defaults must agree across the three layers
# --------------------------------------------------------------------------
DEFAULTS = [
    ("nowShowingText", "'NOW SHOWING'"),
    ("nowShowingFont", "\"'Bebas Neue', sans-serif\""),
    ("nowShowingFontSize", "9"),
    ("nowShowingKerning", "0.1"),
    ("nowShowingFontWeight", "700"),
    ("nowShowingColor", "'#F4E88A'"),
    ("progressBarColor", "'#F4E88A'"),
    ("progressTrackColor", "'#788496'"),
    ("progressTrackOpacity", "0.92"),
    ("progressBarPadding", "1.5"),
    ("progressBarHeight", "2.5"),
    ("autoDimStrength", "0.5"),
    ("rotateSec", "10"),
]


@pytest.mark.parametrize("key,literal", DEFAULTS, ids=[d[0] for d in DEFAULTS])
def test_kiosk_and_settings_page_agree_on_defaults(source, key, literal):
    """A default that drifts makes the preview differ from the live wall."""
    for path in (APP_JS, SETTINGS_JS):
        text = source(path)
        assert key in text, key + " is not referenced in " + path
        line = [ln for ln in text.splitlines() if key in ln and literal in ln]
        assert line, (
            "expected default " + literal + " for " + key + " in " + path
        )


@pytest.mark.parametrize(
    "key,literal",
    [
        ("nowShowingColor", "#F4E88A"),
        ("progressBarColor", "#F4E88A"),
        ("progressTrackColor", "#788496"),
        ("progressTrackOpacity", "0.92"),
        ("progressBarPadding", "1.5"),
        ("progressBarHeight", "2.5"),
        ("autoDimStrength", "0.5"),
    ],
)
def test_settings_html_shows_the_same_defaults(source, key, literal):
    html = source(SETTINGS_HTML)
    match = re.search(r"""id=["']""" + key + r"""["'][^>]*""", html)
    assert match, key + " has no control in settings.html"
    tag_start = html.rfind("<", 0, match.start())
    tag = html[tag_start : html.index(">", match.end()) + 1]
    assert literal.lower() in tag.lower(), (
        key + " control should default to " + literal + ", got: " + tag
    )


def test_the_minimum_rotation_interval_is_consistent(source):
    """3s is the floor everywhere: HTML min, kiosk clamp and settings clamp."""
    assert 'id="rotateSec"' in source(SETTINGS_HTML)
    assert 'min="3"' in source(SETTINGS_HTML)
    assert "Math.max(3, Number(j.rotateSec)" in source(APP_JS)
    assert "Math.max(3, Number(el('rotateSec').value)" in source(SETTINGS_JS)


def test_music_video_mode_is_driven_by_the_proxy_media_type(source):
    """The proxy labels music videos "musicvideo"; the kiosk and CSS key off that."""
    assert '"musicvideo"' in source(PROXY_PY) or "'musicvideo'" in source(PROXY_PY)
    assert "data.mediaType === 'musicvideo'" in source(APP_JS)
    assert ".now-showing.music " in source(STYLES_CSS)
    assert "classList.toggle('music'" in source(APP_JS)


def test_the_kiosk_rerenders_when_the_playing_item_changes(source):
    """A playlist moves to the next video without a stop; the wall must follow."""
    app_js = source(APP_JS)
    assert "nowPlayingKey(nowPlayingData) !== currentItemKey" in app_js
    assert '"ratingKey": rating_key' in source(PROXY_PY)


def test_music_video_background_is_a_solid_colour_from_the_art(source):
    """Spotify-style: one averaged colour, not a blurred copy of the image."""
    app_js = source(APP_JS)
    assert "computeArtColors(prox(data.poster))" in app_js
    assert "backdrop.style.backgroundColor = colors.backdrop" in app_js
    assert "backgroundImage" not in app_js


def test_music_video_accent_colour_comes_from_the_art_with_a_white_fallback(source):
    app_js = source(APP_JS)
    assert "nowShowing.style.setProperty('--music-accent', colors.accent)" in app_js
    assert "nowShowing.style.removeProperty('--music-accent')" in app_js
    # Astra pass 1 #4: a late result for an earlier item must not recolour the current one
    assert "if (!colors || currentItemKey !== colorsFor) return;" in app_js
    css = source(STYLES_CSS)
    assert "--music-accent: #ffffff;" in css
    assert "background-color: var(--music-accent);" in css


def test_a_session_gap_holds_longer_when_more_is_queued(source):
    """Measured 2026-09-26: a slow-loading next item leaves no session for 3.9-4.2 s, which
    looks exactly like a Stop; only the queue says more is coming."""
    app_js = source(APP_JS)
    assert "const STOP_GRACE_MS = 3000;" in app_js
    assert "const QUEUE_GRACE_MS = 8000;" in app_js
    assert "const LOADING_LOOK_AFTER_MS = 1000;" in app_js
    assert "gone >= (queueHasMore ? QUEUE_GRACE_MS : STOP_GRACE_MS)" in app_js
    assert "queueHasMore = Array.isArray(data.upNext) && data.upNext.length > 0;" in app_js
    css = source(STYLES_CSS)
    assert ".now-showing.loading .now-showing-pause::before" in css
    assert "@keyframes now-showing-spin" in css


def test_song_and_artist_stay_on_one_line_and_scroll_when_too_long(source):
    """Dan: a title wrapping onto a second line threw the layout off badly."""
    css = source(STYLES_CSS)
    rule = re.search(r"\.now-showing-artist,\s*\.now-showing-song \{([^}]*)\}", css)
    assert rule and "white-space: nowrap" in rule.group(1) and "overflow: hidden" in rule.group(1)
    assert "-webkit-line-clamp: 2" not in rule.group(1)
    app_js = source(APP_JS)
    assert "setScrollingText(songEl," in app_js and "setScrollingText(artistEl," in app_js
    body = app_js[app_js.index("function setScrollingText"):app_js.index("function escapeHtml")]
    assert "span.textContent = text" in body, "text, not HTML"
    # Astra pass 1 #1: fonts.ready can already be settled before the web font starts loading,
    # so the line's own font is loaded explicitly and the text measured again afterwards.
    assert "document.fonts.load(`${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`, text)" in body
    assert ".then(() => requestAnimationFrame(measure)" in body
    assert "document.fonts.ready.then" not in body
    assert "span.getAnimations().forEach(a => a.cancel())" in body, "re-measuring restarts, never stacks"
    assert "iterations: Infinity" in body


def test_up_next_titles_wrap_evenly_and_keep_the_row_aligned(source):
    """On the 4K wall "This Is What You Came / For" left one word alone, and one-line titles
    left the artist lines at different heights across the row."""
    css = source(STYLES_CSS)
    rule = re.search(r"\.now-showing-upnext-song \{([^}]*)\}", css)
    assert rule, "no .now-showing-upnext-song rule"
    assert "text-wrap: balance;" in rule.group(1)
    assert "min-height: 2.3em;" in rule.group(1), "two lines reserved (2 x line-height 1.15)"
    assert "line-height: 1.15;" in rule.group(1)
    assert "-webkit-line-clamp: 2;" in rule.group(1)


def test_up_next_puts_the_artist_above_a_trimmed_song_title(source):
    """Dan: a one-line title left a gap before its artist that looked unintentional. The
    artist now comes first, so the spare reserved line falls at the bottom of the tile, and
    titles drop their "(...)" / "ft." extras."""
    app_js = source(APP_JS)
    body = app_js[app_js.index("function upNextTileHtml"):app_js.index("function renderUpNext")]
    assert body.index("now-showing-upnext-artist") < body.index("now-showing-upnext-song")
    # The trimming itself lives in the proxy (app.short_title, behaviour-tested in
    # test_now_playing_api.py); the kiosk only displays it.
    assert "escapeHtml(item.shortTitle || item.trackTitle || item.title)" in body


def test_the_loading_spinner_is_a_fading_ring_that_turns_slowly(source):
    """The wall runs 4K at 30 Hz; a solid arc turning once a second stepped visibly."""
    css = source(STYLES_CSS)
    rule = re.search(r"\.now-showing\.loading \.now-showing-pause::before \{([^}]*)\}", css)
    assert rule
    body = rule.group(1)
    assert "conic-gradient(" in body and "mask: radial-gradient(" in body
    assert "animation: now-showing-spin 1.5s linear infinite;" in body
    assert "border-top-color" not in body


def test_a_song_change_in_the_music_layout_goes_through_the_transition(source):
    """The first up-next cover flies into the main art when that item starts (matched by
    ratingKey); other changes crossfade; the poll loop waits while a transition runs."""
    app_js = source(APP_JS)
    tick = app_js[app_js.index("async function tick()"):]
    assert "if (trackAnimating) {" in tick[:200], "the poll loop waits for a running transition"
    assert "await changeTrack(nowPlayingData, cfg, () => checkNowPlaying(cfg));" in tick
    change = app_js[app_js.index("function changeTrack"):app_js.index("async function settleTrack")]
    assert "String(lastUpNext[0].ratingKey) === String(data.ratingKey)" in change
    assert "animateAdvance(data, cfg, askAgain) : animateCrossfade(data, cfg)" in change
    assert "data.mediaType !== 'musicvideo'" in change, "movies/TV keep the instant swap"
    css = source(STYLES_CSS)
    assert ".now-showing-fly {" in css and "transform-origin: top left;" in css


def test_the_new_third_tile_slides_in_with_the_row(source):
    """Dan: the third tile faded and slid in after the rest, like an extra step; then: slide it
    "at the same pace as the other two", with the current song's fly-up going right away and
    the row waiting "as needed" for the queue's third item, "so what's up top is always
    correct"."""
    app_js = source(APP_JS)
    assert "upNextEnterLast" not in app_js and "translateX(4vw)" not in app_js
    advance = app_js[app_js.index("async function animateAdvance"):app_js.index("async function animateCrossfade")]
    # The top animations start before any wait for the queue.
    wait = advance.index("await waitForUpNext(data, askAgain)")
    assert advance.index("anims.push(fly.animate(") < wait
    assert advance.index("anims.push(track.animate(") < wait
    assert "if (!known && tiles.length === 3 && data.upNextPending && askAgain) {" in advance
    # The row: the same slide options for the two leaving tiles and the entering one.
    row = advance[wait:advance.index("await Promise.all(anims.map(a => a.finished));")]
    assert "const slide = { duration: TRACK_ANIM_MS * 0.75, delay: 60, easing: TRACK_EASE };" in row
    assert "[{ transform: 'translateX(0)' }, { transform: `translateX(${-step}px)` }], { ...slide, fill: 'forwards' }" in row
    assert "entering.animate([{ transform: `translateX(${step}px)` }, { transform: 'none' }]" in row
    assert "{ ...slide, fill: 'backwards' }" in row and "gridArea" in row
    assert "opacity" not in row, "slides, no fade"
    # The wait is bounded, polls, and gives up if the song changed or stopped.
    helper = app_js[app_js.index("async function waitForUpNext"):app_js.index("async function animateAdvance")]
    assert "const until = Date.now() + THIRD_WAIT_MS;" in helper
    assert "String(fresh.ratingKey) !== String(data.ratingKey)) return null;" in helper
    assert "if (!fresh.upNextPending || (fresh.upNext || []).length >= 3) return fresh.upNext || [];" in helper
    assert "const THIRD_WAIT_MS = 1000;" in app_js
    # Later still: renderUpNext slides tiles that extend the row in, at the same pace.
    render = app_js[app_js.index("function renderUpNext"):app_js.index("// ---- song-change transition")]
    assert "const grows = before.length > 0 && before.length < list.length" in render
    assert "tile.animate([{ transform: `translateX(${step}px)` }, { transform: 'none' }]" in render
    assert "{ duration: TRACK_ANIM_MS * 0.75, easing: TRACK_EASE }" in render
    # Astra pass 1 (earlier): a late third item arrives by poll, so the first poll after a
    # change is soon, and a crossfade still fades its third tile in.
    assert "const AFTER_CHANGE_POLL_MS = 250;" in app_js
    tick = app_js[app_js.index("async function tick()"):]
    change = tick[tick.index("await changeTrack(nowPlayingData, cfg, () => checkNowPlaying(cfg));"):]
    assert change.index("setTimeout(tick, AFTER_CHANGE_POLL_MS)") < change.index("return;") < change.index("} else {")
    crossfade = app_js[app_js.index("async function animateCrossfade"):app_js.index("function holdForNextItem")]
    assert "await settleTrack(data, cfg, anims, true);" in crossfade
    # Astra pass 2: the fade starts as the row is drawn, before the (up to 500 ms) decode wait.
    settle = app_js[app_js.index("async function settleTrack"):app_js.index("async function animateAdvance")]
    # Conveyor pass 1: a crossfade's row is new content, so renderUpNext's "row grows" slide
    # must not also run on it (the third tile would slide and fade at once).
    assert settle.index("if (fadeThirdTile) lastUpNext = [];") < settle.index("showNowPlaying(data, cfg);")
    assert (settle.index("showNowPlaying(data, cfg);")
            < settle.index("third.animate([{ opacity: 0 }, { opacity: 1 }]")
            < settle.index("await Promise.race([poster.decode()"))


def test_a_failed_or_stalled_transition_cleans_up_and_frees_the_poll_loop(source):
    """Astra pass 1 #1/#2: an exception mid-animation must not leave the clone over the art or
    animations running, and a poster that never decodes must not hold trackAnimating forever."""
    app_js = source(APP_JS)
    advance = app_js[app_js.index("async function animateAdvance"):app_js.index("async function animateCrossfade")]
    cleanup = advance[advance.index("} finally {"):]
    assert "anims.forEach(a => a.cancel());" in cleanup and "fly.remove();" in cleanup
    assert "tiles[0].style.visibility = '';" in cleanup
    assert advance.index("try {") < advance.index("nowShowing.appendChild(fly);")
    crossfade = app_js[app_js.index("async function animateCrossfade"):app_js.index("function holdForNextItem")]
    assert "anims.forEach(a => a.cancel());" in crossfade[crossfade.index("} finally {"):]
    settle = app_js[app_js.index("async function settleTrack"):app_js.index("async function animateAdvance")]
    assert "await Promise.race([poster.decode()" in settle and "TRACK_DECODE_WAIT_MS" in settle
    assert "await poster.decode()" not in settle


def test_fun_fact_bubbles_pop_over_the_art_on_the_agreed_clock(source):
    """Dan approved the Pop-Up Video style bubbles on the TV (2026-09-26): classic pop, first at
    15 s, one per 30-40 s, each held for its reading time, none in a song's last 15 s or during
    a song change; a song without facts just has none."""
    app_js = source(APP_JS)
    popup = app_js[app_js.index("// ---- fun-fact bubbles"):app_js.index("// Centre the pause badge")]
    assert "let popupTiming = { first: 15000, every: 35000 };" in popup
    assert "const POPUP_END_QUIET_MS = 15000;" in popup
    # Dan: a little more time; capped so a long fact still fits its 35 s slot (Astra).
    assert "return Math.min(POPUP_MAX_READ_MS, 3000 + String(text).length / 12 * 1000);" in popup
    assert "const POPUP_MAX_READ_MS = 20000;" in popup
    # Driven by the playback position (a pause holds the bubble; a seek picks the slot).
    assert "const pos = positionMs();" in popup
    assert "const slot = Math.floor((pos - popupTiming.first) / popupTiming.every);" in popup
    assert "playback.duration - POPUP_END_QUIET_MS" in popup
    # Astra pass 1: every shown slot is remembered (a seek back doesn't repeat a fact); the
    # start position is stored, not recomputed (a paused bubble must stay); and new bubbles
    # wait out a change's last animations.
    assert "popupShown.has(slot)" in popup and "popupShown.add(slot);" in popup
    # Dan: the facts loop, each repeating once, a slow stream for anyone who missed one.
    assert "const POPUP_REPEATS = 2;" in popup
    assert "slot >= popupFacts.length * POPUP_REPEATS" in popup
    assert "el.textContent = popupFacts[slot % popupFacts.length];" in popup
    assert "popupReadMs(popupFacts[slot % popupFacts.length])" in popup
    assert "popupShownSlot" not in popup
    assert "popupShownAt = pos;" in popup
    # A Plex report re-anchoring the clock steps the position back a little; only a real seek
    # (more than 3 s back) may end a bubble (they lasted ~0.5 s on real playback).
    assert "pos < popupShownAt - POPUP_SEEK_BACK_MS" in popup
    assert "const POPUP_SEEK_BACK_MS = 3000;" in popup
    # The pop-out's scale(0) fill must not linger: every bubble after the first vanished ~0.5 s
    # in (when its pop-in ended) on the Pi's kiosk. Hidden, then the pop-out is cancelled.
    out = popup[popup.index("function popOutPopup"):popup.index("function popInPopup")]
    assert out.index("el.classList.remove('showing');") < out.index("anim.cancel();")
    assert "popupHideAt - popupReadMs" not in popup
    assert "if (trackAnimating || Date.now() < popupNotBefore) return;" in popup
    assert "popupNotBefore = Date.now() + POPUP_SETTLE_MS;" in popup
    # The classic pop, in and out, with transforms only.
    assert "{ transform: 'scale(1.08)', offset: 0.65 }" in popup
    assert "{ transform: 'scale(0)' }" in popup
    # Wired in: ticks with the progress bar; reset per item (music facts only), on rotation;
    # the old bubble pops out when a song change starts.
    render = app_js[app_js.index("function renderProgress"):app_js.index("// ---- fun-fact bubbles")]
    assert "updatePopup();" in render
    assert "if (newItem) resetPopups(data.mediaType === 'musicvideo' ? data.facts : []);" in app_js
    # The proxy's facts lookup has a short timeout, so facts can arrive a poll or two into the
    # song; they're taken up if the song has none yet.
    assert "else if (data.mediaType === 'musicvideo') adoptLateFacts(data.facts);" in app_js
    assert "if (popupFacts.length || !Array.isArray(facts)) return;" in popup
    rotation = app_js[app_js.index("function showRotation"):app_js.index("// ---- up next and the gap")]
    assert "resetPopups([]);" in rotation
    change = app_js[app_js.index("function changeTrack"):app_js.index("async function settleTrack")]
    assert change.index("popOutPopup();") < change.index("trackAnimating = true;")
    css = source(STYLES_CSS)
    for rule in (".now-showing-popup {", ".now-showing-popup.showing {", ".now-showing-popup.corner-tr {",
                 ".now-showing-popup.corner-bl {", ".now-showing-popup.corner-tr::after {",
                 ".now-showing-popup.corner-bl::after {"):
        assert rule in css, rule


def test_the_flying_cover_is_not_forced_back_into_the_layout(source):
    """The fly is appended to #nowShowing, whose children get position: relative from a more
    specific rule; unless it's excluded, the fly lands in the flow and shoves the art down."""
    css = source(STYLES_CSS)
    relative_rules = re.findall(r"([^{}]*)\{[^}]*position:\s*relative", css)
    child_rules = [sel for sel in relative_rules if ".now-showing >" in sel]
    assert child_rules, "expected the .now-showing > child rule"
    for sel in child_rules:
        assert ":not(.now-showing-fly)" in sel, sel.strip()
        # The fact bubble is positioned the same way (fixed, from the art's box).
        assert ":not(.now-showing-popup)" in sel, sel.strip()


def test_up_next_titles_are_html_escaped(source):
    """Library titles contain &, quotes and apostrophes, and the row is built as HTML."""
    app_js = source(APP_JS)
    body = app_js[app_js.index("function upNextTileHtml"):app_js.index("function renderUpNext")]
    assert "escapeHtml(item.shortTitle || item.trackTitle || item.title)" in body
    assert "escapeHtml(item.artist)" in body
    assert "escapeHtml(prox(item.poster))" in body
    # Both places that build tiles go through it (the advance adds the incoming tile).
    assert "list.map(upNextTileHtml)" in app_js
    assert "insertAdjacentHTML('beforeend', upNextTileHtml(incoming))" in app_js


def test_the_music_stack_leaves_the_art_a_side_sized_top_margin(source):
    """Dan: at 1080p the art sat too close to the top. These paddings make the centred stack
    leave it 54 px from the top at 1080x1920, the same as the 5vw sides (measured in Edge)."""
    css = source(STYLES_CSS)
    track = re.search(r"\.now-showing\.music \.now-showing-track \{([^}]*)\}", css).group(1)
    upnext = re.search(r"\.now-showing\.music \.now-showing-upnext \{([^}]*)\}", css).group(1)
    assert "padding: 3.5vh 5vw 3vh;" in track
    assert "padding: 2.5vh 5vw 0;" in upnext


def test_the_movie_stack_has_even_gaps_and_shared_side_margins(source):
    """Dan, 2026-09-28: the movie/TV spacing. Measured in Edge at 1080x1920 before: the title
    clipped at the top edge, 6 px from bar to poster, icons 56 px tall in a 160 px box."""
    css = source(STYLES_CSS)
    rule = lambda sel: re.search(re.escape(sel) + r" \{([^}]*)\}", css).group(1)
    stack = rule(".now-showing:not(.music)")
    assert "justify-content: center;" in stack and "gap: 2.5vw;" in stack
    assert "padding: 3vw 0 3.5vw;" in stack
    title = rule(".now-showing:not(.music) .now-showing-title")
    assert "margin: 0;" in title and "line-height: 1;" in title
    # A big nowShowingFontSize (UI max 20) must wrap, not run off the edges (Astra pass 1 #1).
    assert "nowrap" not in title
    # The bar keeps its configurable vertical padding; bar, poster and icons share 3vw sides.
    assert "margin: var(--progress-bar-padding, 1.5vh) 3vw;" in rule(".now-showing:not(.music) .now-showing-progress")
    poster = rule(".now-showing:not(.music) .now-showing-poster")
    # A set width: with only max-width, a poster smaller than the screen stayed at its own size.
    # (?<!-): "max-width: 94vw;" alone must not satisfy it (Astra pass 1 #2).
    assert re.search(r"(?<![-\w])width: 94vw;", poster) and "flex: 0 1 auto;" in poster
    assert "margin: 0 3vw;" in rule(".now-showing:not(.music) .now-showing-info")
    assert "height: auto;" in rule(".now-showing:not(.music) .now-showing-metadata-icon")


def _sample(ring, centre):
    """A 16x16 RGBA sample: `ring` on the outer pixels, `centre` inside."""
    data = []
    for y in range(16):
        for x in range(16):
            edge = x in (0, 15) or y in (0, 15)
            data += list(ring if edge else centre) + [255]
    return data


def _two_sides_black(bright):
    """Levitating-like: top and right edges black, the rest of the image `bright`."""
    data = []
    for y in range(16):
        for x in range(16):
            data += ([0, 0, 0] if y == 0 or x == 15 else list(bright)) + [255]
    return data


def _one_side_dark(side, bright=(90, 90, 90), row=((0, 0, 0), (40, 40, 40))):
    """Only `side` (top/bottom/left/right) is dark, its pixels alternating through `row`, so
    the side's mean differs from its darkest pixel (Astra pass 1 #1, P2)."""
    data = []
    for y in range(16):
        for x in range(16):
            on = {"top": y == 0, "bottom": y == 15, "left": x == 0, "right": x == 15}[side]
            k = x if side in ("top", "bottom") else y
            data += list(row[k % len(row)] if on else bright) + [255]
    return data


def _side_means(data):
    light = lambda x, y: _lightness(*data[(y * 16 + x) * 4:(y * 16 + x) * 4 + 3])
    return [sum(light(k, 0) for k in range(16)) / 16, sum(light(k, 15) for k in range(16)) / 16,
            sum(light(0, k) for k in range(16)) / 16, sum(light(15, k) for k in range(16)) / 16]


def _lightness(r, g, b):
    return colorsys.rgb_to_hls(r / 255, g / 255, b / 255)[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js to run app.js's colour code")
def test_the_music_backdrop_stays_lighter_than_a_dark_edged_cover(source):
    """Dan, 2026-09-29: covers with a black border or black design (Billie Jean) melted into a
    near-black backdrop and looked puny. The backdrop keeps a lightness gap of at least
    BACKDROP_EDGE_GAP above the art's outer ring; covers without a dark edge keep the plain
    darkened average. Runs app.js's own rgbToHsl/backdropColor in Node (Astra pass 1 #2)."""
    body = source(APP_JS)
    code = body[body.index("function rgbToHsl"):body.index("function computeArtColors")]
    cases = {
        "black": _sample((1, 1, 1), (1, 1, 1)),                # uniform near-black (Astra's case)
        "black_frame_purple": _sample((0, 0, 0), (40, 5, 35)),  # Sober-like
        "black_frame_white": _sample((0, 0, 0), (255, 255, 255)),  # dark edge, light middle
        "red": _sample((200, 30, 40), (200, 30, 40)),           # Toto-like: must not change
        "just_above": _sample((32, 32, 32), (32, 32, 32)),      # edge L 0.125 > 0.12: no lift
        # Levitating (Dan, 2026-09-29): two black sides, the rest a dark grey. The whole-ring
        # average is ~0.13 (> 0.12, so a ring rule leaves it), but the darkest side is black.
        "two_black_sides": _two_sides_black((70, 70, 70)),
        # each side darkest in turn, with a mixed row, so the lift must use that side's MEAN
        **{f"only_{s}": _one_side_dark(s) for s in ("top", "bottom", "left", "right")},
    }
    script = code + "\nconst cases = " + json.dumps(cases) + ";\n" \
        "const out = {}; for (const k in cases) out[k] = backdropColor(cases[k]);\n" \
        "console.log(JSON.stringify(out));"
    # On stdin: the script outgrows the Windows command-line limit when passed with -e
    out = json.loads(subprocess.run(["node"], input=script, capture_output=True, text=True,
                                    check=True).stdout)
    gap = float(re.search(r"const BACKDROP_EDGE_GAP = ([\d.]+);", body).group(1))

    def js_round(x):   # JavaScript's Math.round: halves go up (Python's round() goes to even)
        return int(x + 0.5)

    def darkened(rgb_px):
        return tuple(js_round(c * 0.55) for c in rgb_px)

    def mean(data, k):
        return sum(data[k::4]) / 256

    lifted_cases = ("black", "black_frame_purple", "two_black_sides",
                    "only_top", "only_bottom", "only_left", "only_right")
    for name in lifted_cases:
        data = cases[name]
        lifted = re.fullmatch(r"hsl\(([\d.]+), ([\d.]+)%, ([\d.]+)%\)", out[name])
        assert lifted, (name, "not lifted", out[name])
        h, s, l = map(float, lifted.groups())
        edge = min(_side_means(data))                             # the darkest side's mean
        assert l / 100 >= edge + gap, (name, out[name])          # never under the gap
        assert l / 100 < edge + gap + 0.0011, (name, out[name])  # and no more (0.1% round-up)
        avg = [mean(data, k) * 0.55 for k in range(3)]
        hh, ll, ss = colorsys.rgb_to_hls(*(c / 255 for c in avg))
        assert abs(h - hh * 360) < 0.01 and abs(s - ss * 100) < 0.01, (name, out[name])  # own hue
    # no dark edge, or an already-light backdrop: exactly the darkened average
    assert out["red"] == "rgb(%d, %d, %d)" % darkened((200, 30, 40))
    assert out["just_above"] == "rgb(%d, %d, %d)" % darkened((32, 32, 32))
    avg = [js_round(mean(cases["black_frame_white"], k) * 0.55) for k in range(3)]
    assert out["black_frame_white"] == "rgb(%d, %d, %d)" % tuple(avg)


def test_a_non_square_cover_keeps_its_shape_from_tile_to_main_art(source):
    """Dan, 2026-09-29: "Another Night" (1200x1048) was square in up next, then jumped to its
    true shape with bars once playing. The tile, the flying copy and the main art must all fit
    the cover the same way (contain), with no background of their own in the bars."""
    css = source(STYLES_CSS)
    rule = lambda sel: re.search(re.escape(sel) + r" \{([^}]*)\}", css).group(1)
    assert "object-fit: contain;" in rule(".now-showing-poster")        # the main art (base rule)
    assert "object-fit: contain;" in rule(".now-showing-fly")
    assert "object-fit: contain;" in rule(".now-showing-upnext-item img")
    assert "object-fit: cover" not in css[css.index(".now-showing-upnext-item img"):]
    for sel in (".now-showing-fly", ".now-showing-upnext-item img",
                ".now-showing-upnext-item img,\n.now-showing-upnext-blank"):
        assert "background" not in rule(sel), sel
    assert "background" not in rule(".now-showing.music .now-showing-poster")
    # The empty placeholder (no poster) keeps its faint fill (Astra pass 1 #1, P2).
    # its own rule, not the shared "img,\n.now-showing-upnext-blank {" one
    blank = re.search(r"(?<!,)\n\.now-showing-upnext-blank \{([^}]*)\}", css).group(1)
    assert "background: rgba(255, 255, 255, 0.08);" in blank


def test_the_playing_song_shows_the_proxy_display_title(source):
    """Dan, 2026-09-29: hide tags like "(Videoclip)" in the now-playing title. The proxy strips
    them into displayTitle; the kiosk falls back to trackTitle for an older proxy."""
    assert ("isMusicVideo ? (data.displayTitle || data.trackTitle || data.title || '')"
            in source(APP_JS))


def test_music_video_mode_hides_the_marquee(source):
    css = source(STYLES_CSS)
    rule = re.search(r"\.now-showing\.music \.now-showing-title[^{]*\{([^}]*)\}", css)
    assert rule and "display: none" in rule.group(1)


def test_the_kiosk_polls_the_proxy_every_second_with_a_three_second_stop_grace(source):
    """Measured 2026-09-26: events reach Plex within ~1 s, and a playlist's next item appears
    ~1 s after the previous session vanishes. 1 s polls are cheap because the proxy answers
    from its websocket-fed cache; the grace period stops the wall flashing posters between
    songs."""
    app_js = source(APP_JS)
    assert "const POLL_MS = 1000;" in app_js
    assert "const STOP_GRACE_MS = 3000;" in app_js
    assert "setTimeout(tick, POLL_MS)" in app_js
    assert "}, 5000);" not in app_js, "the old 5 s poll is gone"


def test_the_progress_bar_runs_from_the_last_report_and_only_while_playing(source):
    app_js = source(APP_JS)
    assert "Number(data.offsetAt)" in app_js
    assert "if (playback.state === 'playing') position += Date.now() - playback.at;" in app_js
    assert "anchorOffset = playback.offset;\n      anchorAt = playback.at;" in app_js.replace("\r\n", "\n"), (
        "a repeated report must keep the whole anchor (offset and timestamp)")
    assert "playback.reported === offset" in app_js, "repeats compare against the reported offset"
    # Astra pass 1 #1: a state change on an unchanged report freezes/resumes from the
    # on-screen position instead of snapping back to the stale offset.
    assert "anchorOffset = positionMs();" in app_js
    assert "transition: width 0.25s linear" in source(STYLES_CSS)


def test_pause_dims_the_art_and_shows_a_badge_for_all_media(source):
    css = source(STYLES_CSS)
    assert ".now-showing.paused .now-showing-poster" in css
    assert ".now-showing.paused .now-showing-pause" in css
    assert "classList.toggle('paused', state === 'paused')" in source(APP_JS)


def test_the_proxy_starts_the_push_monitor_only_as_the_service(source):
    proxy = source(PROXY_PY)
    main = proxy[proxy.index("if __name__ == '__main__':"):]
    assert "NowPlayingMonitor(" in main and "MONITOR.start()" in main
    assert "MONITOR = None" in proxy[: proxy.index("if __name__ == '__main__':")]


def test_music_video_preview_mode_exists(source):
    assert "preview=musicvideo" in source(SETTINGS_JS)
    assert "previewMode === 'musicvideo'" in source(APP_JS)


def test_auto_dim_threshold_is_pinned(source):
    """Posters brighter than this average luma get dimmed."""
    assert "return avgLuma >= 200" in source(APP_JS)
