"""Behaviour tests for scripts/pin-display.sh (lets the kiosk boot with the TV off).

2026-11-17: the wall rebooted while its TV was off and came up on the tty1 login prompt instead
of the kiosk. The Pi 5's KMS driver ignores config.txt's hdmi_force_hotplug, so the script saves
the TV's EDID and pins the connector on the kernel cmdline. These tests run the real script
against a fake root (sysfs, /boot/firmware, /etc, /usr/lib/firmware) with logging stand-ins for
systemctl and update-initramfs.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = "scripts/pin-display.sh"
CONNECTOR = "HDMI-A-1"
EDID_REL = "usr/lib/firmware/edid/poster-wall-HDMI-A-1.bin"
CMDLINE_REL = "boot/firmware/cmdline.txt"
HOOK_REL = "etc/initramfs-tools/hooks/poster-wall-edid"
BASE_CMDLINE = "console=tty1 root=PARTUUID=4038fe9c-02 rootwait consoleblank=0 fbcon=rotate:1"
PIN = "drm.edid_firmware=HDMI-A-1:edid/poster-wall-HDMI-A-1.bin video=HDMI-A-1:D"


def _bash():
    git_bash = r"C:\Program Files\Git\bin\bash.exe"
    return git_bash if os.name == "nt" and os.path.exists(git_bash) else shutil.which("bash")


def make_edid(blocks=2, serial=1, declared=None):
    """`declared` overrides the base block's extension count (byte 126), default blocks - 1."""
    data = bytearray()
    for b in range(blocks):
        block = bytearray(128)
        if b == 0:
            block[0:8] = bytes([0, 255, 255, 255, 255, 255, 255, 0])
            block[12] = serial
            block[126] = blocks - 1 if declared is None else declared
        else:
            block[0] = 0x02
        block[127] = (-sum(block[:127])) % 256
        data += block
    return bytes(data)


class FakePi:
    def __init__(self, root, repo_root):
        self.root = root
        self.script = repo_root / SCRIPT
        self.log = root / "calls.log"
        bin_dir = root / "fakebin"
        bin_dir.mkdir()
        for name in ("systemctl", "update-initramfs"):
            stub = bin_dir / name
            # update-initramfs fails while a fail-<name> file exists in the fake root.
            stub.write_text('#!/bin/sh\necho "' + name + ' $*" >> "' + str(self.log) + '"\n'
                            '[ ! -e "' + str(root / ("fail-" + name)) + '" ]\n')
            stub.chmod(0o755)
        self.bin_dir = bin_dir
        (root / "etc/initramfs-tools").mkdir(parents=True)
        self.cmdline = root / CMDLINE_REL
        self.cmdline.parent.mkdir(parents=True)
        self.cmdline.write_text(BASE_CMDLINE + "\n")
        self.connector = root / "sys/class/drm/card1-HDMI-A-1"
        self.connector.mkdir(parents=True)
        self.tv(on=True)

    def tv(self, on, edid=None):
        (self.connector / "status").write_text("connected\n" if on else "disconnected\n")
        (self.connector / "edid").write_bytes((edid if edid is not None else make_edid()) if on else b"")

    def run(self, *args, ok=True):
        env = dict(os.environ, PW_ROOT=str(self.root), PW_SUDO="",
                   PW_SYSTEMCTL=str(self.bin_dir / "systemctl"),
                   PW_UPDATE_INITRAMFS=str(self.bin_dir / "update-initramfs"))
        r = subprocess.run([_bash(), str(self.script), *args], capture_output=True, text=True,
                           env=env, timeout=30)
        assert (r.returncode == 0) == ok, r.stdout + r.stderr
        return r.stdout

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def backups(self):
        return sorted(self.cmdline.parent.glob("cmdline.txt.bak.*"))


@pytest.fixture
def pi(tmp_path, repo_root):
    if not _bash() or os.name == "nt":
        pytest.skip("needs a Linux bash")
    return FakePi(tmp_path, repo_root)


def test_with_the_tv_on_it_saves_the_edid_and_pins_the_connector(pi):
    pi.run()
    assert (pi.root / EDID_REL).read_bytes() == make_edid()
    assert pi.cmdline.read_text() == BASE_CMDLINE + " " + PIN + "\n"
    assert len(pi.backups()) == 1
    hook = pi.root / HOOK_REL
    assert "copy_file firmware" in hook.read_text() and os.access(hook, os.X_OK)
    assert "update-initramfs -u -k all" in pi.calls()
    assert "systemctl mask --now getty@tty1.service autovt@tty1.service" in pi.calls()


def test_a_rerun_changes_nothing(pi):
    pi.run()
    before = pi.cmdline.read_text()
    pi.log.unlink()
    pi.run()
    assert pi.cmdline.read_text() == before
    assert len(pi.backups()) == 1
    assert not any(c.startswith("update-initramfs") for c in pi.calls())


def test_a_rerun_with_the_tv_off_keeps_the_pin(pi):
    pi.run()
    pi.tv(on=False)
    pi.cmdline.write_text(BASE_CMDLINE + "\n")  # e.g. cmdline.txt replaced by an OS update
    pi.run()
    assert pi.cmdline.read_text() == BASE_CMDLINE + " " + PIN + "\n"
    assert (pi.root / EDID_REL).read_bytes() == make_edid()


def test_with_the_tv_off_and_nothing_saved_it_changes_nothing(pi):
    pi.tv(on=False)
    out = pi.run()
    assert "re-run" in out
    assert pi.cmdline.read_text() == BASE_CMDLINE + "\n"
    assert not (pi.root / EDID_REL).exists()
    assert pi.calls() == []


@pytest.mark.parametrize("edid", [
    make_edid()[:100],                                   # truncated
    b"\x01" + make_edid()[1:],                           # wrong header
    make_edid()[:127] + bytes([make_edid()[127] ^ 1]) + make_edid()[128:],  # bad checksum
    make_edid(blocks=1, declared=1),                     # declares an extension it doesn't have
    make_edid(blocks=2, declared=0),                     # an extra block it doesn't declare
])
def test_a_damaged_edid_is_never_saved(pi, edid):
    pi.tv(on=True, edid=edid)
    pi.run()
    assert not (pi.root / EDID_REL).exists()
    assert pi.cmdline.read_text() == BASE_CMDLINE + "\n"


def test_a_different_tv_replaces_the_saved_edid_with_a_backup(pi):
    pi.run()
    pi.tv(on=True, edid=make_edid(serial=2))
    pi.log.unlink()
    pi.run()
    assert (pi.root / EDID_REL).read_bytes() == make_edid(serial=2)
    assert len(list((pi.root / EDID_REL).parent.glob("*.bin.bak.*"))) == 1
    assert "update-initramfs -u -k all" in pi.calls()


def test_an_existing_mode_and_other_connectors_are_kept(pi):
    pi.cmdline.write_text(BASE_CMDLINE + " video=HDMI-A-1:1920x1080@60e video=HDMI-A-2:d\n")
    pi.run()
    assert pi.cmdline.read_text() == (
        BASE_CMDLINE + " video=HDMI-A-2:d drm.edid_firmware=HDMI-A-1:edid/poster-wall-HDMI-A-1.bin"
        " video=HDMI-A-1:1920x1080@60D\n")


def test_other_connectors_edid_mappings_are_kept(pi):
    """Astra pass 1 #5: pinning (or unpinning) HDMI-A-1 dropped HDMI-A-2's EDID mapping."""
    pi.cmdline.write_text(BASE_CMDLINE + " drm.edid_firmware=HDMI-A-2:edid/other.bin,HDMI-A-1:edid/old.bin\n")
    pi.run()
    assert pi.cmdline.read_text() == (
        BASE_CMDLINE + " drm.edid_firmware=HDMI-A-2:edid/other.bin,HDMI-A-1:edid/poster-wall-HDMI-A-1.bin"
        " video=HDMI-A-1:D\n")
    pi.run("--unpin")
    assert pi.cmdline.read_text() == BASE_CMDLINE + " drm.edid_firmware=HDMI-A-2:edid/other.bin\n"


def test_a_failed_initramfs_rebuild_is_retried_and_leaves_cmdline_alone(pi):
    """Astra pass 1 #2: after a failed rebuild, a rerun skipped it and pinned the cmdline."""
    (pi.root / "fail-update-initramfs").touch()
    pi.run(ok=False)
    assert pi.cmdline.read_text() == BASE_CMDLINE + "\n"
    (pi.root / "fail-update-initramfs").unlink()
    pi.log.unlink()
    pi.run()
    assert "update-initramfs -u -k all" in pi.calls()
    assert pi.cmdline.read_text() == BASE_CMDLINE + " " + PIN + "\n"


def test_cmdline_is_replaced_whole_and_never_without_root(pi):
    """Astra pass 1 #1: cmdline.txt was truncated in place by tee; a bad write could stop boot."""
    pi.run()
    assert not (pi.cmdline.parent / "cmdline.txt.new").exists()
    no_root = "console=tty1 rootwait"
    pi.cmdline.write_text(no_root + "\n")
    pi.run(ok=False)
    assert pi.cmdline.read_text() == no_root + "\n"


def _run_hook(pi, copy_file_body):
    """Run the installed initramfs hook with a stand-in hook-functions and a fake DESTDIR."""
    pi.run()
    hook = (pi.root / HOOK_REL).read_text()
    funcs = pi.root / "hook-functions"
    funcs.write_text("copy_file() { " + copy_file_body + " }\n")
    hook = hook.replace("/usr/share/initramfs-tools/hook-functions", str(funcs))
    hook = hook.replace("/usr/lib/firmware/edid/", str(pi.root / "usr/lib/firmware/edid") + "/")
    script = pi.root / "hook.sh"
    script.write_text(hook)
    dest = pi.root / "dest"
    return subprocess.run([_bash(), str(script)], env=dict(os.environ, DESTDIR=str(dest)),
                          capture_output=True, text=True, timeout=10), dest


def test_the_initramfs_hook_fails_when_the_edid_is_not_copied(pi):
    """Astra pass 1 #4: the hook ignored copy_file failures and exited 0."""
    r, _ = _run_hook(pi, "return 1;")
    assert r.returncode == 1


def test_the_initramfs_hook_fails_on_a_partial_copy(pi):
    """Astra pass 2 #4: a failed copy that left a truncated file passed the existence check."""
    r, _ = _run_hook(pi, 'mkdir -p "$DESTDIR$(dirname "$2")"; head -c 64 "$2" > "$DESTDIR$2"; return 2;')
    assert r.returncode == 1


def test_the_initramfs_hook_accepts_an_edid_already_in_the_image(pi):
    r, _ = _run_hook(pi, 'mkdir -p "$DESTDIR$(dirname "$2")"; cp "$2" "$DESTDIR$2"; return 1;')
    assert r.returncode == 0, r.stderr


def test_unpin_removes_the_cmdline_entries_and_unmasks_the_console(pi):
    pi.run()
    pi.run("--unpin")
    assert pi.cmdline.read_text() == BASE_CMDLINE + "\n"
    assert "systemctl unmask getty@tty1.service autovt@tty1.service" in pi.calls()
    assert (pi.root / EDID_REL).exists()


def test_setup_runs_it(source):
    assert 'bash "$REPO_DIR/scripts/pin-display.sh"' in source("setup.sh")
