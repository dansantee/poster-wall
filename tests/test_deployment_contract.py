"""Contract tests for provisioning, deployment and repo hygiene.

Ports, systemd unit names and the ``SECRETS.md`` field labels are agreed on by
files that never import each other: ``setup.sh``, ``proxy/app.py``, the two
frontend scripts, the example units and the PowerShell remote helper. A change
to one side of any of those agreements breaks the Pi silently, so they are
pinned here.
"""
import re
import subprocess

import pytest

SETUP_SH = "setup.sh"
REMOTE_PS1 = "scripts/poster-wall-remote.ps1"
SYSTEMD_EXAMPLES = "docs/systemd-examples.txt"
PROXY_PY = "proxy/app.py"
APP_JS = "web/app.js"
SETTINGS_JS = "web/settings.js"
GITIGNORE = ".gitignore"
SECRETS_EXAMPLE = "SECRETS-EXAMPLE.md"

PROXY_PORT = "8811"
WEB_PORT = "8088"

SERVICES = ("poster-proxy.service", "poster-web.service", "poster-kiosk.service")

# Files that hold live credentials and must never be committed.
NEVER_COMMITTED = ("SECRETS.md", "proxy/config.json")


def shell_var(sh, name):
    match = re.search(r"^" + name + r'="?([^"\n]+)"?$', sh, re.M)
    assert match, "could not find " + name + " in setup.sh"
    return match.group(1)


# --------------------------------------------------------------------------
# Ports
# --------------------------------------------------------------------------
def test_proxy_port_agrees_everywhere(source):
    assert shell_var(source(SETUP_SH), "PROXY_PORT") == PROXY_PORT
    assert "port=" + PROXY_PORT in source(PROXY_PY)
    # The frontend hardcodes the proxy port relative to its own hostname.
    for path in (APP_JS, SETTINGS_JS):
        assert ":" + PROXY_PORT in source(path), path + " must call the proxy on " + PROXY_PORT


def test_web_port_agrees_everywhere(source):
    assert shell_var(source(SETUP_SH), "WEB_PORT") == WEB_PORT
    assert "http.server $WEB_PORT" in source(SETUP_SH)
    assert "http.server " + WEB_PORT in source(SYSTEMD_EXAMPLES)
    # The kiosk error screen tells the user where the settings page lives.
    assert ":" + WEB_PORT + "/settings.html" in source(APP_JS)


def test_the_kiosk_browser_opens_the_static_site(source):
    setup = source(SETUP_SH)
    assert "http://localhost:$WEB_PORT" in setup
    assert "--kiosk" in setup
    assert "--ozone-platform=wayland" in setup


def test_the_proxy_listens_on_all_interfaces(source):
    """The settings page is opened from another machine on the LAN."""
    assert "host='0.0.0.0'" in source(PROXY_PY)


# --------------------------------------------------------------------------
# systemd units
# --------------------------------------------------------------------------
@pytest.mark.parametrize("service", SERVICES)
def test_setup_installs_and_enables_each_service(source, service):
    setup = source(SETUP_SH)
    assert service in setup
    assert re.search(r"systemctl --user enable[^\n]*" + service, setup), (
        service + " is created but never enabled at boot"
    )


def test_setup_installs_user_units_with_lingering(source):
    setup = source(SETUP_SH)
    assert ".config/systemd/user" in setup
    # Without lingering, user units do not start until someone logs in.
    assert "loginctl enable-linger" in setup


def test_the_proxy_service_runs_the_repo_venv(source):
    setup = source(SETUP_SH)
    assert shell_var(setup, "VENV_DIR") == "$REPO_DIR/.venv"
    assert "ExecStart=$VENV_DIR/bin/python $PROXY_DIR/app.py" in setup


def test_setup_installs_the_proxy_runtime_dependencies(source):
    """app.py imports these at module scope, so a missing one is a boot failure."""
    setup = source(SETUP_SH)
    match = re.search(r"^pip install (flask[^\n>]*)", setup, re.M)
    assert match, "setup.sh must pip install the proxy dependencies"
    installed = match.group(1).split()
    for package in ("flask", "requests"):
        assert package in installed


@pytest.mark.parametrize("service", SERVICES)
def test_the_example_units_cover_each_service(source, service):
    assert service in source(SYSTEMD_EXAMPLES)


def test_the_remote_helper_restarts_all_three_services(source):
    ps1 = source(REMOTE_PS1)
    for action in ("restart", "deploy"):
        assert action in ps1
    for service in SERVICES:
        assert service in ps1


def test_only_the_kiosk_is_restartable_from_the_settings_page(source):
    """Restarting the proxy from inside the proxy would drop the response."""
    proxy = source(PROXY_PY)
    assert "poster-kiosk.service" in proxy
    assert "poster-proxy.service" not in proxy
    assert "poster-web.service" not in proxy


# --------------------------------------------------------------------------
# Display rotation
# --------------------------------------------------------------------------
def test_rotation_accepts_the_four_quarter_turns(source):
    setup = source(SETUP_SH)
    assert "0|90|180|270)" in setup
    # fbcon takes an index, sway takes degrees; they are derived from one flag.
    assert "FBCON_ROTATE=$(( SWAY_ROTATE_DEG / 90 ))" in setup
    assert "output HDMI-A-1 transform $SWAY_ROTATE_DEG" in setup
    assert "fbcon=rotate:$FBCON_ROTATE" in setup


def test_the_display_mode_can_be_pinned_and_a_pin_survives_a_rerun(source):
    """The wall's Vizio lists 4K30 as preferred even with 4K60 on offer, so Sway came up at
    30 Hz; --mode pins 3840x2160@60Hz. A later `./setup.sh --rotate 90` must not drop it."""
    setup = source(SETUP_SH)
    assert "--mode)" in setup
    assert 'SWAY_MODE_LINE="output HDMI-A-1 mode $OUTPUT_MODE"' in setup
    assert "$SWAY_MODE_LINE" in setup[setup.index('cat >"$SWAY_CONFIG" <<EOF'):]
    # without --mode, the previous pin is read back from the existing sway config
    assert "sed -n 's/^output HDMI-A-1 mode \\(.*\\)$/\\1/p' \"$SWAY_CONFIG\"" in setup
    assert 'if [[ $MODE_GIVEN -eq 0 && -f "$SWAY_CONFIG" ]]; then' in setup, "read back only when --mode is omitted"
    # --mode auto removes the pin: the exact guard in front of the mode line (Astra pass 1 #2)
    guard = 'if [[ -n "$OUTPUT_MODE" && "$OUTPUT_MODE" != "auto" ]]; then\n  SWAY_MODE_LINE="output HDMI-A-1 mode $OUTPUT_MODE"'
    assert guard in setup.replace("\r\n", "\n")
    # values are validated, and an explicitly empty --mode is rejected (Astra pass 1 #1)
    assert 'if [[ $MODE_GIVEN -eq 1 && "$OUTPUT_MODE" != "auto" && ! "$OUTPUT_MODE" =~ ^[0-9]+x[0-9]+(@[0-9]+(\\.[0-9]+)?Hz)?$ ]]; then' in setup


def test_setup_defaults_to_portrait(source):
    assert shell_var(source(SETUP_SH), "ROTATE_DEG").startswith("90")


def test_setup_disables_screen_blanking(source):
    assert "consoleblank=0" in source(SETUP_SH)


def test_setup_is_written_to_be_rerunnable(source):
    setup = source(SETUP_SH)
    assert "set -euo pipefail" in setup
    # Boot files are backed up before editing, and edits are guarded by greps.
    assert ".bak.$(date +%s)" in setup


# --------------------------------------------------------------------------
# Remote helper <-> SECRETS.md
# --------------------------------------------------------------------------
def test_the_helper_exposes_the_documented_actions(source):
    ps1 = source(REMOTE_PS1)
    match = re.search(r"\[ValidateSet\(([^)]*)\)\]", ps1)
    assert match, "the -Action parameter must stay validated"
    actions = set(re.findall(r"'([^']+)'", match.group(1)))
    assert actions == {"ssh", "pull", "restart", "deploy", "direct-deploy", "reboot"}


def test_secret_labels_match_what_the_helper_looks_up(source):
    """Get-SecretValue parses ``- <Label>: `value` `` lines out of SECRETS.md."""
    ps1 = source(REMOTE_PS1)
    example = source(SECRETS_EXAMPLE)
    labels = set(re.findall(r"Get-SecretValue '([^']+)'", ps1))
    assert labels == {"Host", "SSH user", "Repo path", "SSH password"}
    for label in sorted(labels):
        assert re.search(r"^- " + re.escape(label) + r": `.+`$", example, re.M), (
            "SECRETS-EXAMPLE.md is missing a parseable line for " + label
        )


def test_the_helper_reads_secrets_from_the_repo_root(source):
    ps1 = source(REMOTE_PS1)
    assert "Join-Path $repoRoot 'SECRETS.md'" in ps1
    assert "throw \"SSH password not found" in ps1


def test_direct_deploy_warns_that_it_desyncs_the_pi(source):
    """The Pi's git commit no longer describes what is running after this."""
    ps1 = source(REMOTE_PS1)
    assert "Write-Warning" in ps1
    assert "without changing the Pi's git commit" in ps1


def test_direct_deploy_uploads_source_files_as_text(source):
    """Text files go through Set-SFTPContent; anything else is a binary copy."""
    ps1 = source(REMOTE_PS1)
    match = re.search(r"\$ext -in @\(([^)]*)\)", ps1)
    assert match, "direct deploy must classify uploads by extension"
    extensions = set(re.findall(r"'([^']+)'", match.group(1)))
    for needed in (".py", ".js", ".html", ".css", ".json", ".md", ".sh"):
        assert needed in extensions, needed + " would be uploaded as binary"
    assert ".png" not in extensions, "the metadata icons must stay binary uploads"


def test_deploy_uses_a_fast_forward_only_pull(source):
    """A merge commit on the Pi would leave it permanently diverged."""
    assert "git pull --ff-only" in source(REMOTE_PS1)


# --------------------------------------------------------------------------
# Repo hygiene
# --------------------------------------------------------------------------
@pytest.mark.parametrize("path", NEVER_COMMITTED)
def test_secret_files_are_ignored(source, path):
    ignored = [
        line.strip() for line in source(GITIGNORE).splitlines() if line.strip()
    ]
    assert path in ignored, path + " holds live credentials and must be gitignored"


@pytest.mark.parametrize("path", NEVER_COMMITTED)
def test_secret_files_are_not_tracked(repo_root, path):
    try:
        tracked = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", path],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        pytest.skip("git unavailable")
    assert tracked.stdout.strip() == "", path + " is tracked by git"


def test_the_secrets_template_is_committed_but_holds_no_real_values(source):
    example = source(SECRETS_EXAMPLE)
    assert "Do not commit `SECRETS.md`" in example
    assert "your-username" in example
    assert "your-password" in example


def test_the_venv_is_ignored(source):
    assert ".venv/" in source(GITIGNORE)
