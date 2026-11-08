"""
cloudsync.py - sync emulator saves with a cloud drive (OneDrive) through rclone,
using the same layout as EmuDeck's CloudSync so a PC and a Steam Deck can share saves.

EmuDeck layout
--------------
  local  : Emulation/saves/<emulator>/...          (EmuDeck keeps these as links)
  cloud  : <remote>:EmuDeck/saves/<emulator>/...
  tools  : Emulation/tools/rclone/rclone(.exe) and rclone.conf

This module never deletes anything: it only runs `rclone copy --update`
(newer file wins), and downloads move any file they would replace into a backup folder.
No GUI dependencies.
"""

import configparser
import io
import os
import platform
import shlex
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

IS_WIN = os.name == "nt"
EXE = "rclone.exe" if IS_WIN else "rclone"
DEFAULT_BASE = "EmuDeck/saves"
APP_DIR = Path.home() / ".emu_save_manager"
OWN_RCLONE_DIR = APP_DIR / "rclone"

# Cloud sub-folder (under <base>/) used by EmuDeck for each emulator location.
EMUDECK_SUBPATHS = {
    ("PCSX2", "Memory Cards"): "pcsx2/saves",
    ("PCSX2", "Save States"): "pcsx2/states",
    ("DuckStation", "Memory Cards"): "duckstation/saves",
    ("DuckStation", "Save States"): "duckstation/states",
    ("RetroArch", "Saves"): "retroarch/saves",
    ("RetroArch", "States"): "retroarch/states",
    ("Dolphin", "GameCube Saves"): "dolphin/GC",
    ("Dolphin", "Wii Saves"): "dolphin/Wii",
    ("Dolphin", "Save States"): "dolphin/StateSaves",
    ("Xemu", "HDD and EEPROM"): "xemu",
}
# Huge files: off by default so a first sync doesn't upload gigabytes.
DISABLED_BY_DEFAULT = {("Xemu", "HDD and EEPROM")}

EXCLUDES = ["desktop.ini", "Thumbs.db", ".DS_Store", "*.esm_tmp", "*.tmp"]


def default_subpath(emulator: str, label: str) -> str:
    sub = EMUDECK_SUBPATHS.get((emulator, label))
    if sub:
        return sub
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in emulator.lower())
    return f"{safe}/saves"


# ---------------------------------------------------------------------------
# Locating rclone and its config
# ---------------------------------------------------------------------------
def emudeck_roots():
    """Existing 'Emulation' folders where EmuDeck keeps saves and tools."""
    home = Path.home()
    cands = [home / "Emulation", home / "EmuDeck" / "Emulation"]
    if IS_WIN:
        for letter in "CDEFGH":
            cands.append(Path(f"{letter}:/Emulation"))
    else:
        cands.append(Path("/run/media/mmcblk0p1/Emulation"))
        for base in (Path("/run/media"), Path("/media"), Path("/mnt")):
            try:
                for sub in base.iterdir():
                    cands.append(sub / "Emulation")
                    try:
                        for sub2 in sub.iterdir():
                            cands.append(sub2 / "Emulation")
                    except OSError:
                        pass
            except OSError:
                pass
    seen, out = set(), []
    for c in cands:
        try:
            if c.is_dir() and str(c) not in seen:
                seen.add(str(c))
                out.append(c)
        except OSError:
            pass
    return out


def find_rclone(saved: str = "") -> str:
    if saved and Path(saved).is_file():
        return saved
    for root in emudeck_roots():
        p = root / "tools" / "rclone" / EXE
        if p.is_file():
            return str(p)
    own = OWN_RCLONE_DIR / EXE
    if own.is_file():
        return str(own)
    return shutil.which("rclone") or ""


def default_rclone_conf() -> Path:
    if IS_WIN:
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "rclone" / "rclone.conf"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "rclone" / "rclone.conf"


def find_config(saved: str = "") -> str:
    if saved and Path(saved).is_file():
        return saved
    for root in emudeck_roots():
        p = root / "tools" / "rclone" / "rclone.conf"
        if p.is_file():
            return str(p)
    d = default_rclone_conf()
    return str(d) if d.is_file() else ""


def list_remotes(conf_path: str):
    """[(name, type)] from an rclone.conf (plain INI; encrypted configs return [])."""
    if not conf_path or not Path(conf_path).is_file():
        return []
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read(conf_path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError, OSError):
        return []
    return [(name, cp[name].get("type", "")) for name in cp.sections()]


def pick_default_remote(remotes) -> str:
    onedrive = [n for n, t in remotes if t == "onedrive"]
    for n in onedrive:
        if n.lower().startswith("emudeck"):
            return n
    return onedrive[0] if onedrive else ""


def remote_path(remote: str, base: str, sub: str) -> str:
    parts = [p.strip("/\\") for p in (base, sub)]
    return f"{remote}:" + "/".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Running rclone
# ---------------------------------------------------------------------------
def _display(cmd):
    return subprocess.list2cmdline(cmd) if IS_WIN else " ".join(shlex.quote(c) for c in cmd)


class RcloneRunner:
    def __init__(self, exe: str, conf: str, log):
        self.exe, self.conf, self.log = exe, conf, log
        self.cancelled = False
        self._proc = None

    def run(self, args) -> int:
        cmd = [self.exe] + (["--config", self.conf] if self.conf else []) + list(args)
        self.log("$ " + _display(cmd))
        kw = {"creationflags": 0x08000000} if IS_WIN else {}      # no console window
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          stdin=subprocess.DEVNULL, text=True,
                                          encoding="utf-8", errors="replace", **kw)
        except OSError as ex:
            self.log(f"Could not start rclone: {ex}")
            return 127
        for line in self._proc.stdout:
            self.log(line.rstrip())
        rc = self._proc.wait()
        self._proc = None
        return rc

    def cancel(self):
        self.cancelled = True
        p = self._proc
        if p:
            try:
                p.terminate()
            except OSError:
                pass


def copy_args(src: str, dst: str, dry_run: bool, backup_dir=None):
    args = ["copy", src, dst, "--update", "-v", "--stats", "0", "--transfers", "2"]
    for pat in EXCLUDES:
        args += ["--exclude", pat]
    if dry_run:
        args.append("--dry-run")
    if backup_dir:
        args += ["--backup-dir", str(backup_dir)]
    return args


def sync_pair(runner: RcloneRunner, direction: str, local: Path, remote: str,
              dry_run: bool, backup_dir=None) -> str:
    """direction: 'download' (cloud -> PC) or 'upload' (PC -> cloud).
    Returns 'ok', 'nothing-in-cloud' or 'failed'."""
    if direction == "download":
        rc = runner.run(copy_args(remote, str(local), dry_run, backup_dir))
        if rc == 3:
            return "nothing-in-cloud"
    else:
        rc = runner.run(copy_args(str(local), remote, dry_run))
    return "ok" if rc == 0 else "failed"


def test_connection(runner: RcloneRunner, remote: str) -> bool:
    return runner.run(["lsd", f"{remote}:", "--max-depth", "1"]) == 0


# ---------------------------------------------------------------------------
# Installing rclone / configuring a remote
# ---------------------------------------------------------------------------
def rclone_download_url() -> str:
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
    osname = {"Windows": "windows", "Linux": "linux", "Darwin": "osx"}.get(platform.system())
    if not osname:
        raise OSError("Unsupported operating system")
    return f"https://downloads.rclone.org/rclone-current-{osname}-{arch}.zip"


def install_rclone_from_zip(zip_bytes: bytes, dest_dir: Path = OWN_RCLONE_DIR) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        member = next((n for n in z.namelist() if n == EXE or n.endswith("/" + EXE)), None)
        if member is None:
            raise OSError(f"{EXE} not found in the downloaded archive")
        target = dest_dir / EXE
        with z.open(member) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
    if not IS_WIN:
        os.chmod(target, 0o755)
    return target


def download_rclone(progress=None, dest_dir: Path = OWN_RCLONE_DIR) -> Path:
    """Download the official rclone build for this OS (https://downloads.rclone.org)."""
    with urllib.request.urlopen(rclone_download_url(), timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
        buf = bytearray()
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            buf += chunk
            if progress and total:
                progress(len(buf) / total)
    return install_rclone_from_zip(bytes(buf), dest_dir)


def open_rclone_config_terminal(exe: str, conf: str) -> bool:
    """Open `rclone config` in a terminal window so the user can add a OneDrive remote."""
    cmd = [exe, "config"] + (["--config", conf] if conf else [])
    try:
        if IS_WIN:
            subprocess.Popen(cmd, creationflags=0x00000010)        # CREATE_NEW_CONSOLE
            return True
        if platform.system() == "Darwin":
            script = _display(cmd).replace("\\", "\\\\").replace('"', '\\"')
            subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{script}"'])
            return True
        for term in (["x-terminal-emulator", "-e"], ["konsole", "-e"], ["gnome-terminal", "--"],
                     ["xfce4-terminal", "-x"], ["xterm", "-e"]):
            if shutil.which(term[0]):
                subprocess.Popen(term + cmd)
                return True
    except OSError:
        pass
    return False
