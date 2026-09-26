"""Contract tests for the static frontend.

``web/`` has no build step and no JS test runner, and its three files talk to
each other only through string keys: element ids, CSS class names, CSS custom
properties, icon filenames and config keys. Those joins are exactly what breaks
silently during a rename, so they are asserted here from the source text.

None of these tests run JavaScript. They check that the names on both sides of
each join still line up.
"""
import re

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
    assert "computeBackdropColor(prox(data.poster))" in app_js
    assert "backdrop.style.backgroundColor" in app_js
    assert "backgroundImage" not in app_js


def test_music_video_mode_hides_the_marquee(source):
    css = source(STYLES_CSS)
    rule = re.search(r"\.now-showing\.music \.now-showing-title[^{]*\{([^}]*)\}", css)
    assert rule and "display: none" in rule.group(1)


def test_music_video_preview_mode_exists(source):
    assert "preview=musicvideo" in source(SETTINGS_JS)
    assert "previewMode === 'musicvideo'" in source(APP_JS)


def test_auto_dim_threshold_is_pinned(source):
    """Posters brighter than this average luma get dimmed."""
    assert "return avgLuma >= 200" in source(APP_JS)
