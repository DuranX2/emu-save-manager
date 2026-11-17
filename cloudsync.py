"""
cloudsync.py - sync emulator saves with the cloud through rclone, using the same
layout as EmuDeck's CloudSync so a PC and a Steam Deck can share saves.  Works
with a OneDrive remote and with EmuDeck Cloud (EmuDeck's own Patreon storage).

EmuDeck layout
--------------
  local  : Emulation/saves/<emulator>/...          (EmuDeck keeps these as links)
  cloud  : <remote>:<cs_user>Emudeck/saves/<emulator>/...
  tools  : Emulation/tools/rclone/rclone(.exe) and rclone.conf

EmuDeck Cloud (from EmuDeck's functions/ToolScripts/emuDeckCloudSync.sh)
-------------------------------------------------------------------------
  The Patreon token ("<user>---<secret>") is POSTed as {"token": "<user>|||<secret>"}
  to https://cloud.emudeck.com/register.php, which answers with storage keys:
    "Emudeck-cloud"  : rclone type b2 (Backblaze),  base  cs<user>/Emudeck/saves
    "Emudeck-cloud2" : rclone type s3 (Wasabi),     base  emudeck-saves/cs<user>/Emudeck/saves
  A device only downloads an emulator's saves when <base>/<emulator>/.hash in the
  cloud differs from its own, so after uploading, a fresh .hash is written there.

This module never deletes anything: it only runs `rclone copy --update`
(newer file wins), and downloads move any file they would replace into a backup folder.
No GUI dependencies.
"""

import configparser
import hashlib
import io
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.error
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
    ("Dolphin", "Save States"): "dolphin/states",
    ("Xemu", "HDD and EEPROM"): "xemu",
}
# Cloud folders earlier versions used by mistake; a saved setting equal to one of these
# means "the default" (1.0.0 used dolphin/StateSaves, but EmuDeck keeps dolphin/states).
LEGACY_SUBPATHS = {
    ("Dolphin", "Save States"): ("dolphin/StateSaves",),
}


def effective_subpath(emu: str, label: str, saved: str = "") -> str:
    """The cloud sub folder for a row: the user's own choice, else EmuDeck's layout."""
    saved = (saved or "").strip("/ ")
    if not saved or saved in LEGACY_SUBPATHS.get((emu, label), ()):
        return default_subpath(emu, label)
    return saved
# Huge files: off by default so a first sync doesn't upload gigabytes.
DISABLED_BY_DEFAULT = {("Xemu", "HDD and EEPROM")}

EXCLUDES = ["desktop.ini", "Thumbs.db", ".DS_Store", "*.esm_tmp", "*.tmp",
            # EmuDeck CloudSync's own bookkeeping files
            ".hash", ".token", ".temp", ".last_upload", ".last_download", ".fail_upload",
            ".fail_download", ".pending_upload", ".no_upload", "cloudsync.emudeck",
            "dl_cloudsync.emudeck"]

EMUDECK_CLOUD_REGISTER_URL = "https://cloud.emudeck.com/register.php"
# remote name -> rclone settings (keys filled in from register.php) and cloud base folder
EMUDECK_CLOUD_VARIANTS = {
    "Emudeck-cloud": {
        "label": "EmuDeck Cloud (Backblaze B2)",
        "settings": {"type": "b2", "hard_delete": "false"},
        "keys": ("account", "key"),
        "base": "cs{user}/Emudeck/saves",
    },
    "Emudeck-cloud2": {
        "label": "EmuDeck Cloud (Wasabi)",
        "settings": {"type": "s3", "provider": "Wasabi", "region": "eu-west-1",
                     "endpoint": "s3.eu-west-1.wasabisys.com"},
        "keys": ("access_key_id", "secret_access_key"),
        "base": "emudeck-saves/cs{user}/Emudeck/saves",
    },
}


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
    cands = []
    ep = emudeck_settings().get("emulationPath")
    if ep:
        cands.append(Path(ep))
    cands += [home / "Emulation", home / "EmuDeck" / "Emulation"]
    if IS_WIN:
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZB":
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


def emudeck_appdata() -> Path:
    """EmuDeck's own data folder (its Electron app data and settings file)."""
    if IS_WIN:
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "EmuDeck"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "EmuDeck"


def emudeck_settings() -> dict:
    """A few values from EmuDeck's settings file (settings.ps1 on Windows, settings.sh on Linux)."""
    out = {}
    cands = [emudeck_appdata() / "settings.ps1", emudeck_appdata() / "settings.sh",
             Path.home() / "EmuDeck" / "settings.ps1",
             Path.home() / ".config" / "EmuDeck" / "settings.sh", Path.home() / "emudeck" / "settings.sh"]
    for f in cands:
        try:
            text = f.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = re.match(r'^\s*\$?(emulationPath|savesPath|toolsPath|cloud_sync_provider|cs_user)\s*=\s*"?([^"]*)"?\s*$',
                         line)
            if m and m.group(2) and m.group(1) not in out:
                out[m.group(1)] = m.group(2)
        if out:
            break
    return out


_FULL_TOKEN = re.compile(rb"([A-Za-z0-9_\-]{3,32})\|\|\|([A-Za-z0-9_\-]{20,128})")


def find_emudeck_signins():
    """Full EmuDeck Cloud tokens ('<account id>|||<secret>') that the EmuDeck app on this
    computer has stored after a Patreon sign-in, newest first.  EmuDeck keeps it in its
    Chromium 'Local Storage' under the key 'patreon_token'."""
    folder = emudeck_appdata() / "Local Storage" / "leveldb"
    found = []
    try:
        files = sorted(folder.iterdir(), key=lambda f: f.stat().st_mtime)
    except OSError:
        return []
    for f in files:
        if f.suffix.lower() not in (".log", ".ldb"):
            continue
        try:
            data = f.read_bytes()
        except OSError:
            continue
        for utf16, blob in ((False, data), (True, data.replace(b"\x00", b""))):
            for m in re.finditer(rb"patreon_token", blob):
                t = _FULL_TOKEN.search(blob, m.end(), m.end() + 400)
                if not t:
                    continue
                tok = t.group(0).decode("ascii")
                # values may be stored as UTF-16; only trust such a match if it really is
                if utf16 and tok.encode("utf-16-le") not in data:
                    continue
                found.append(tok)
    out = []
    for tok in reversed(found):                 # newest file / latest write first
        if tok not in out:
            out.append(tok)
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


_CRED_KEYS = {
    "onedrive": ("token",), "drive": ("token",), "dropbox": ("token",), "box": ("token",),
    "pcloud": ("token",), "b2": ("account", "key"), "s3": ("access_key_id", "secret_access_key"),
    "webdav": ("url",), "sftp": ("host",), "smb": ("host",),
}


def remote_info(conf_path: str):
    """[(name, type, ready)] - ready = has the sign-in / keys it needs.  EmuDeck's
    rclone.conf ships a template section for every provider, most of them empty."""
    if not conf_path or not Path(conf_path).is_file():
        return []
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read(conf_path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError, OSError):
        return []
    out = []
    for name in cp.sections():
        sec = cp[name]
        rtype = sec.get("type", "")
        keys = _CRED_KEYS.get(rtype)
        ready = all(sec.get(k, "").strip() for k in keys) if keys else bool(rtype)
        out.append((name, rtype, ready))
    return out


def pick_default_remote(remotes) -> str:
    """Prefer a signed-in OneDrive, then EmuDeck Cloud with keys, then anything signed in.
    remotes: [(name, type)] or [(name, type, ready)]."""
    items = [(r[0], r[1], r[2] if len(r) > 2 else True) for r in remotes]
    ready = [(n, t) for n, t, ok in items if ok]
    for n, t in ready:
        if t == "onedrive":
            return n
    for n, _t in ready:
        if n in EMUDECK_CLOUD_VARIANTS:
            return n
    return ready[0][0] if ready else ""


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

    def capture(self, args):
        """Run rclone quietly; returns (exit code, output text).  Output is not logged."""
        cmd = [self.exe] + (["--config", self.conf] if self.conf else []) + list(args)
        kw = {"creationflags": 0x08000000} if IS_WIN else {}
        try:
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                               errors="replace", timeout=120, **kw)
        except (OSError, subprocess.TimeoutExpired) as ex:
            return 127, str(ex)
        return r.returncode, r.stdout

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
    backup_dir: where replaced files go - a local folder for downloads, a path on the
    same remote ('remote:folder') for uploads.
    Returns 'ok', 'nothing-in-cloud' or 'failed'."""
    if direction == "download":
        rc = runner.run(copy_args(remote, str(local), dry_run, backup_dir))
        if rc == 3:
            return "nothing-in-cloud"
    else:
        rc = runner.run(copy_args(str(local), remote, dry_run, backup_dir))
    return "ok" if rc == 0 else "failed"


def remote_backup_root(remote: str, base: str) -> str:
    """'remote:<parent of base>/EmuSaveManager-overwritten' - beside, never inside, the synced folders."""
    parts = [p for p in base.strip("/\\").split("/") if p]
    parent = "/".join(parts[:-1])
    return f"{remote}:" + (f"{parent}/" if parent else "") + "EmuSaveManager-overwritten"


def test_connection(runner: RcloneRunner, remote: str, base: str = "") -> bool:
    if base and is_emudeck_cloud(remote):
        # EmuDeck Cloud keys may only list the user's own folder, not the whole bucket
        return runner.run(["lsf", f"{remote}:{base.strip('/')}", "--max-depth", "1"]) == 0
    return runner.run(["lsd", f"{remote}:", "--max-depth", "1"]) == 0


# ---------------------------------------------------------------------------
# EmuDeck Cloud
# ---------------------------------------------------------------------------
class EmuDeckCloudError(Exception):
    pass


def is_emudeck_cloud(remote: str) -> bool:
    return remote in EMUDECK_CLOUD_VARIANTS


def parse_token(token: str):
    """'<user>---<secret>' (as EmuDeck shows/stores it) -> (token for the API, user id)."""
    t = (token or "").strip().strip('"').strip()
    t = t.replace("---", "|||")
    user = t.split("|", 1)[0].strip()
    if t and "|||" not in t and re.fullmatch(r"[A-Za-z0-9_\-]{20,}", t):
        raise EmuDeckCloudError(
            "This is the short Patreon login token (from the .token file). EmuDeck Cloud needs the "
            "full token, which also contains your account ID (it looks like  ABCD1234|||<secret> ).\n\n"
            "Easiest: sign in to Patreon in the EmuDeck app on this computer, then use "
            "'Set up EmuDeck Cloud…' again and let it use that sign-in.")
    if not user or "|||" not in t or any(c.isspace() for c in t):
        raise EmuDeckCloudError("That doesn't look like an EmuDeck Cloud token. It should look like "
                                "<account id>|||<secret>  (or with --- instead of |||).")
    return t, user


EMUDECK_CLOUD_LOGIN_URL = "https://cloud.emudeck.com/login.php"


def emudeck_cloud_type(token: str, url: str = EMUDECK_CLOUD_LOGIN_URL, timeout=20):
    """Ask EmuDeck which cloud this account uses, like the EmuDeck app does:
    'cloud1' -> Emudeck-cloud (B2), anything else -> Emudeck-cloud2 (Wasabi).
    Returns the remote name, or None if the question could not be answered."""
    api_token, _user = parse_token(token)
    req = urllib.request.Request(url, data=json.dumps({"token": api_token}).encode(), method="POST",
                                 headers={"Content-Type": "text/plain;charset=UTF-8",
                                          "User-Agent": HTTP_HEADERS["User-Agent"]})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:  # noqa - optional: detection falls back to looking for the saves
        return None
    cloud = data.get("cloud") if isinstance(data, dict) else None
    if not cloud:
        return None
    return "Emudeck-cloud" if str(cloud) == "cloud1" else "Emudeck-cloud2"


def discover_emudeck_cloud_user(runner: "RcloneRunner", remote: str) -> str:
    """The account id for an EmuDeck Cloud remote whose keys are set but whose folder is
    unknown.  Backblaze names the allowed bucket (cs<id>) when the key is used anywhere
    else: 'you must use bucket(s) [{"<bucket id>" "cs1234"}] with this application key'."""
    rc, out = runner.capture(["lsf", f"{remote}:", "--max-depth", "1"])
    m = re.search(r'bucket\(s\)\s*\[\{"[^"]*"\s+"cs([A-Za-z0-9_-]+)"\}', out)
    if m:
        return m.group(1)
    if rc == 0:                                  # listing worked: a cs<id> folder or bucket
        for line in out.splitlines():
            m = re.match(r"cs([A-Za-z0-9_-]+)/?$", line.strip())
            if m:
                return m.group(1)
    return ""


def emudeck_cloud_base_ok(remote: str, base: str) -> bool:
    b = base.strip("/ ")
    return not is_emudeck_cloud(remote) or b.startswith("cs") or b.startswith("emudeck-saves/cs")


def emudeck_cloud_base(remote: str, user: str) -> str:
    return EMUDECK_CLOUD_VARIANTS[remote]["base"].format(user=user)


def register_emudeck_cloud(token: str, url: str = EMUDECK_CLOUD_REGISTER_URL, timeout=30):
    """Exchange the EmuDeck (Patreon) token for storage keys, exactly like EmuDeck does.
    Returns {"user", "cloud_key_id", "cloud_key", "cloud"}.  The token is not kept."""
    api_token, user = parse_token(token)
    body = json.dumps({"token": api_token}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": HTTP_HEADERS["User-Agent"]})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as ex:
        raise EmuDeckCloudError(f"EmuDeck Cloud refused the token (HTTP {ex.code}).")
    except (urllib.error.URLError, OSError) as ex:
        raise EmuDeckCloudError(f"Could not reach cloud.emudeck.com: {ex}")
    try:
        data = json.loads(raw)
    except ValueError:
        raise EmuDeckCloudError("Unexpected answer from cloud.emudeck.com: " + raw[:200])
    kid, key = str(data.get("cloud_key_id") or ""), str(data.get("cloud_key") or "")
    if not kid or not key or kid == "null" or key == "null":
        raise EmuDeckCloudError("EmuDeck Cloud did not return storage keys. Check the token and "
                                "that your Patreon membership includes EmuDeck Cloud.")
    return {"user": user, "cloud_key_id": kid, "cloud_key": key, "cloud": data.get("cloud")}


def write_emudeck_cloud_remotes(conf_path: str, keys: dict):
    """Add/refresh the Emudeck-cloud and Emudeck-cloud2 remotes in an rclone.conf,
    keeping every other remote.  Returns {remote name: cloud base folder}."""
    path = Path(conf_path)
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str
    if path.is_file():
        try:
            cp.read(path, encoding="utf-8")
        except (configparser.Error, UnicodeDecodeError) as ex:
            raise EmuDeckCloudError(f"Cannot read {path}: {ex}")
    bases = {}
    for name, v in EMUDECK_CLOUD_VARIANTS.items():
        sec = dict(v["settings"])
        id_key, secret_key = v["keys"]
        sec[id_key], sec[secret_key] = keys["cloud_key_id"], keys["cloud_key"]
        cp[name] = sec
        bases[name] = emudeck_cloud_base(name, keys["user"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        cp.write(f)
    if path.is_file():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    os.replace(tmp, path)
    return bases


def detect_emudeck_cloud(runner: RcloneRunner, bases: dict, hint=None):
    """Which EmuDeck Cloud variant holds this account's saves?
    Returns (remote name or None, {remote: 'has-saves'|'empty'|'error: ...'})."""
    report = {}
    for name, base in bases.items():
        rc, out = runner.capture(["lsf", f"{name}:{base}", "--max-depth", "1"])
        lines = [l for l in out.splitlines() if l.strip()]
        if rc == 0:
            report[name] = "has-saves" if lines else "empty"
        elif rc == 3 or "directory not found" in out.lower():
            report[name] = "empty"
        else:
            report[name] = "error: " + (lines[-1] if lines else f"exit code {rc}")
    with_saves = [n for n, r in report.items() if r == "has-saves"]
    if len(with_saves) == 1:
        return with_saves[0], report
    if hint in bases and not with_saves and report.get(hint) == "empty":
        return hint, report
    if hint in bases and hint in with_saves:
        return hint, report
    hint = str(hint or "").lower()
    if not with_saves:
        working = [n for n, r in report.items() if r == "empty"]
        if "wasabi" in hint or "s3" in hint:
            return ("Emudeck-cloud2" if "Emudeck-cloud2" in working else None), report
        if "b2" in hint or "backblaze" in hint:
            return ("Emudeck-cloud" if "Emudeck-cloud" in working else None), report
        return (working[0] if len(working) == 1 else None), report
    return None, report


def emulator_folder(sub: str) -> str:
    """'pcsx2/saves' -> 'pcsx2' (the level EmuDeck keeps its .hash at)."""
    return sub.strip("/\\").split("/", 1)[0]


def bump_emudeck_hash(runner: RcloneRunner, remote: str, base: str, emu_dirs) -> bool:
    """Write a fresh .hash for each emulator (and the root) so EmuDeck devices download."""
    token = hashlib.sha256(f"{time.time()}-{os.getpid()}-{os.urandom(8).hex()}".encode()).hexdigest()
    fd, tmp = tempfile.mkstemp(suffix=".hash")
    ok = True
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(token + "\n")
        for d in sorted(set(emu_dirs)) + [""]:
            target = remote_path(remote, base, f"{d}/.hash" if d else ".hash")
            ok = runner.run(["copyto", tmp, target]) == 0 and ok
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return ok


# ---------------------------------------------------------------------------
# Installing rclone / configuring a remote
# ---------------------------------------------------------------------------
RCLONE_PINNED = "v1.69.0"          # the version EmuDeck installs; used as a last resort
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EmuSaveManager; +https://rclone.org)",
                "Accept": "*/*"}


def _platform_tag() -> str:
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
    osname = {"Windows": "windows", "Linux": "linux", "Darwin": "osx"}.get(platform.system())
    if not osname:
        raise OSError("Unsupported operating system")
    return f"{osname}-{arch}"


def rclone_download_url() -> str:
    return f"https://downloads.rclone.org/rclone-current-{_platform_tag()}.zip"


def _github_latest_url(tag: str, timeout=20) -> str:
    req = urllib.request.Request("https://api.github.com/repos/rclone/rclone/releases/latest",
                                 headers=dict(HTTP_HEADERS, Accept="application/vnd.github+json"))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    for a in data.get("assets", []):
        name = a.get("name", "")
        if name.endswith(f"-{tag}.zip"):
            return a["browser_download_url"]
    raise OSError("no matching file in the latest GitHub release")


def rclone_download_candidates():
    """(description, url or callable returning a url), tried in order."""
    tag = _platform_tag()
    return [
        ("downloads.rclone.org", rclone_download_url()),
        ("GitHub (latest release)", lambda: _github_latest_url(tag)),
        (f"GitHub ({RCLONE_PINNED}, the version EmuDeck uses)",
         f"https://github.com/rclone/rclone/releases/download/{RCLONE_PINNED}/"
         f"rclone-{RCLONE_PINNED}-{tag}.zip"),
    ]


def _fetch(url: str, progress=None, timeout=60) -> bytes:
    req = urllib.request.Request(url, headers=HTTP_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        total = int(r.headers.get("Content-Length") or 0)
        buf = bytearray()
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            buf += chunk
            if progress and total:
                progress(len(buf) / total)
    return bytes(buf)


def install_rclone_from_zip(zip_bytes: bytes, dest_dir: Path = OWN_RCLONE_DIR) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        raise OSError("the download was not a zip file")
    with z:
        member = next((n for n in z.namelist() if n == EXE or n.endswith("/" + EXE)), None)
        if member is None:
            raise OSError(f"{EXE} not found in the downloaded archive")
        target = dest_dir / EXE
        tmp = dest_dir / (EXE + ".part")
        with z.open(member) as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.replace(tmp, target)
    if not IS_WIN:
        os.chmod(target, 0o755)
    return target


def download_rclone(progress=None, dest_dir: Path = OWN_RCLONE_DIR, log=None, candidates=None) -> Path:
    """Download the official rclone build for this OS, trying rclone's own download
    server first and its GitHub releases after that."""
    errors = []
    for desc, url in (candidates or rclone_download_candidates()):
        try:
            if callable(url):
                url = url()
            if log:
                log(f"Trying {desc}: {url}")
            return install_rclone_from_zip(_fetch(url, progress), dest_dir)
        except Exception as ex:  # noqa - any failure: try the next source
            errors.append(f"{desc}: {ex}")
            if log:
                log(f"  failed: {ex}")
    raise OSError("rclone could not be downloaded automatically.\n  " + "\n  ".join(errors)
                  + "\nDownload it from https://rclone.org/downloads/ , unzip it, and use Browse… "
                    "to pick rclone" + (".exe" if IS_WIN else "") + ".")


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
