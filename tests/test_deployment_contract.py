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
    # The kiosk's setup and error screens tell the user where the settings page lives: the port
    # the kiosk itself was served from, with the web port as the fallback.
    app_js = source(APP_JS)
    assert "const port = location.port || '" + WEB_PORT + "';" in app_js
    assert ":${port}/settings.html" in app_js


def test_the_kiosk_browser_opens_the_static_site(source):
    setup = source(SETUP_SH)
    assert "http://localhost:$WEB_PORT" in setup
    assert "--kiosk" in setup
    assert "--ozone-platform=wayland" in setup


def test_the_kiosk_starts_with_an_empty_http_cache(source):
    """2026-09-28: after a deploy and kiosk restart the TV still showed the old styles.css.
    http.server sends no cache headers, so Chromium reused its cached copy (Cache_Data dated
    before the deploy). The kiosk clears the cache before launching the browser."""
    setup = source(SETUP_SH)
    assert shell_var(setup, "KIOSK_CACHE_DIR") == "$USER_HOME/.cache/chromium/Default/Cache"
    exec_line = re.search(r"^exec .*http://localhost:\$WEB_PORT$", setup, re.M).group(0)
    # The clear runs first; rm -rf succeeds even when the directory is missing, so the
    # browser always starts. The path is quoted: a home with a space must not split into two
    # rm targets (Astra pass 1 #1).
    assert exec_line.startswith("exec rm -rf '$KIOSK_CACHE_DIR' && $BROWSER_BIN "), exec_line


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


def test_setup_installs_a_cjk_font(source):
    """Montserrat has no Hangul/kana/hanzi, and Raspberry Pi OS ships no fallback that does, so
    an artist like "CHUNG HA (청하)" drew as boxes until fonts-noto-cjk was installed."""
    setup = source(SETUP_SH)
    block = re.search(r"^sudo apt-get install -y \\\n((?:  .*\\\n)*  .*)$", setup, re.M)
    assert block, "expected the main apt-get install block"
    assert "fonts-noto-cjk" in block.group(1).split()


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


# --------------------------------------------------------------------------
# One-line install (install.sh)
# --------------------------------------------------------------------------
INSTALL_SH = "install.sh"


def test_the_installer_is_safe_to_pipe_from_curl(source):
    """curl ... | bash runs whatever arrives: everything is inside main(), and the call is a
    { ... } block bash must read whole before running, so a cut-off download runs nothing."""
    sh = source(INSTALL_SH)
    assert sh.startswith("#!/usr/bin/env bash")
    assert re.search(r"^main\(\) \{\n", sh, re.M)
    assert sh.rstrip().endswith('{ main "$@"; }')
    assert "  set -euo pipefail" in sh


def _git_bash():
    # On Windows a bare "bash" can be WSL's launcher stub (it fails with no distro): prefer Git's
    import os
    import shutil
    git_bash = r"C:\Program Files\Git\bin\bash.exe"
    return git_bash if os.name == "nt" and os.path.exists(git_bash) else shutil.which("bash")


def test_a_cut_off_download_never_starts_the_installer(source):
    """Astra pass 1 #5: with a bare `main "$@"` last line, a download cut off right after `main`
    still ran the installer, without its options. Cut the script at every point from the end of
    main() on, with main's body replaced by a marker, and pipe each to bash as curl would."""
    bash = _git_bash()
    if not bash:
        pytest.skip("bash unavailable")
    sh = source(INSTALL_SH).replace("\r\n", "\n")
    body_start = sh.index("main() {\n") + len("main() {\n")
    body_end = sh.index("\n}\n", body_start)
    stub = sh[:body_start] + "  echo INSTALLER-RAN\n" + sh[body_end:]
    tail_from = stub.index("\n}\n", body_start) + 1
    ran = []
    for cut in range(tail_from, len(stub) + 1):
        r = subprocess.run([bash], input=stub[:cut], capture_output=True, text=True, timeout=20)
        if "INSTALLER-RAN" in r.stdout:
            ran.append(repr(stub[tail_from:cut]))
    assert ran == [repr(stub[tail_from:])] or ran == [repr(stub[tail_from:].rstrip("\n")), repr(stub[tail_from:])], \
        "a truncated download started the installer: " + ", ".join(ran)


def test_the_installer_clones_this_repo_and_runs_setup_with_the_options(source):
    sh = source(INSTALL_SH)
    assert "https://github.com/dansantee/poster-wall.git" in sh
    assert 'git -C "$dir" pull --ff-only' in sh          # re-runnable: updates an existing clone
    assert 'bash ./setup.sh "$@" </dev/null' in sh        # setup must not read the piped script
    assert 'if [[ "$(id -u)" -eq 0 ]]; then' in sh        # the wall's user, not root
    assert "raw.githubusercontent.com/dansantee/poster-wall/main/install.sh | bash" in source("README.md")


def test_the_installer_parses(repo_root):
    bash = _git_bash()
    if not bash:
        pytest.skip("bash unavailable")
    r = subprocess.run([bash, "-n", str(repo_root / INSTALL_SH)], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("path", [INSTALL_SH, SETUP_SH])
def test_the_scripts_are_executable_in_git(repo_root, path):
    try:
        r = subprocess.run(["git", "-C", str(repo_root), "ls-files", "-s", path],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        pytest.skip("git unavailable")
    assert r.stdout.startswith("100755 "), path + " should be committed executable: " + r.stdout
