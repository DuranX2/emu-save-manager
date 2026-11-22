"""
updater.py - looks for a newer release on GitHub and installs it when the user asks.

  * The build writes the release version into the app (app_version.txt next to the bundled
    modules). Source runs and test builds (0.0.0) have no version and never check.
  * At most once a day the app asks GitHub for the latest published release (drafts and
    pre-releases are never offered). Nothing is downloaded until the user clicks Update.
  * Update downloads the file for this machine and checks its SHA-256 against the digest GitHub
    lists for the asset (or the <file>.sha256 file the build attaches) before running anything:
      - Windows, installed: EmuSaveManager-Setup.exe runs silently over the current install and
        starts the app again afterwards.
      - Windows, portable zip: there is nothing to run, so the release page opens instead.
      - Steam Deck / Linux: the package is unpacked and its install.sh runs once this app has
        closed, then the app starts again.
No Tk in this module (see ui_update.py), so the logic can be tested on its own.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import ssl
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO = "DuranX2/emu-save-manager"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
CHECK_EVERY = 24 * 3600                        # seconds between automatic checks
SYSTEM = platform.system()
IS_WIN = SYSTEM == "Windows"
UPDATE_DIR = Path.home() / ".emu_save_manager" / "updates"
USER_AGENT = "EmuSaveManager-update-check (+https://github.com/" + REPO + ")"

SETUP_ASSET = "EmuSaveManager-Setup.exe"
PORTABLE_ASSET = "EmuSaveManager-windows-portable.zip"
LINUX_ASSET = "EmuSaveManager-linux-x86_64.tar.gz"


class UpdateError(Exception):
    """Something the user should read (no network, bad download, …)."""


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------
def _bundle_dir() -> Path | None:
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else None


def current_version() -> str | None:
    """The release this app was built from, or None (source run or test build)."""
    fake = os.environ.get("ESM_FAKE_VERSION")             # tests
    if fake:
        return fake
    base = _bundle_dir()
    if not getattr(sys, "frozen", False) or base is None:
        return None
    try:
        ver = (base / "app_version.txt").read_text(encoding="utf-8").strip().lstrip("v")
    except OSError:
        return None
    return ver if parse_version(ver) > (0,) else None


def parse_version(s: str) -> tuple:
    """'v1.3.10' -> (1, 3, 10); anything unreadable -> (0,)."""
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)", s or "")
    if not m:
        return (0,)
    nums = tuple(int(x) for x in m.group(1).split("."))
    while len(nums) > 1 and nums[-1] == 0:                # 1.3.0 == 1.3
        nums = nums[:-1]
    return nums


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


# ---------------------------------------------------------------------------
# HTTPS
# ---------------------------------------------------------------------------
_CA_FILES = ("/etc/ssl/certs/ca-certificates.crt",   # Debian / Ubuntu / SteamOS
             "/etc/ssl/cert.pem",                    # Arch / SteamOS / macOS
             "/etc/pki/tls/certs/ca-bundle.crt")     # Fedora


def ssl_context() -> ssl.SSLContext:
    """The system's certificates. A bundle built on Ubuntu looks for them where Ubuntu keeps
    them, which SteamOS doesn't have, so fall back to the usual files."""
    ctx = ssl.create_default_context()
    if not IS_WIN and not ctx.cert_store_stats().get("x509_ca"):
        for f in _CA_FILES:
            if os.path.isfile(f):
                try:
                    ctx.load_verify_locations(cafile=f)
                    break
                except (OSError, ssl.SSLError):
                    continue
    return ctx


def _open(url: str, timeout: float, accept: str = "*/*"):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    ctx = ssl_context() if url.startswith("https:") else None
    return urllib.request.urlopen(req, timeout=timeout, context=ctx)


# ---------------------------------------------------------------------------
# The latest release
# ---------------------------------------------------------------------------
@dataclass
class Release:
    version: str
    tag: str
    name: str
    notes: str
    page: str
    published: str = ""
    assets: dict = field(default_factory=dict)        # name -> {"url", "size", "digest"}

    def to_json(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_json(cls, d: dict) -> "Release | None":
        try:
            return cls(**{k: d[k] for k in ("version", "tag", "name", "notes", "page")},
                       published=d.get("published", ""), assets=dict(d.get("assets") or {}))
        except (KeyError, TypeError):
            return None


def parse_release(data: dict) -> Release:
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        raise UpdateError("GitHub returned no published release.")
    tag = str(data.get("tag_name") or "")
    if parse_version(tag) <= (0,):
        raise UpdateError(f"The latest release has no version number ({tag or 'no tag'}).")
    assets = {}
    for a in data.get("assets") or []:
        name, url = a.get("name"), a.get("browser_download_url") or ""
        if name and url.startswith(DOWNLOAD_PREFIX):
            assets[name] = {"url": url, "size": int(a.get("size") or 0),
                            "digest": str(a.get("digest") or "")}
    page = str(data.get("html_url") or RELEASES_PAGE)
    if not page.startswith(f"https://github.com/{REPO}/"):
        page = RELEASES_PAGE
    return Release(version=re.match(r"^\s*v?(\d+(?:\.\d+)*)", tag).group(1), tag=tag, name=str(data.get("name") or tag), notes=str(data.get("body") or ""),
                   page=page, published=str(data.get("published_at") or ""), assets=assets)


def fetch_latest(url: str = API_LATEST, timeout: float = 15) -> Release:
    try:
        with _open(url, timeout, "application/vnd.github+json") as r:
            data = json.loads(r.read(2_000_000).decode("utf-8"))
    except urllib.error.HTTPError as ex:
        if ex.code == 404:
            raise UpdateError("No releases have been published yet.") from ex
        if ex.code in (403, 429):
            raise UpdateError("GitHub is limiting requests right now. Try again later.") from ex
        raise UpdateError(f"GitHub answered {ex.code}.") from ex
    except (OSError, ValueError) as ex:
        raise UpdateError(f"Couldn't reach GitHub ({getattr(ex, 'reason', ex)}).") from ex
    return parse_release(data)


# ---------------------------------------------------------------------------
# When to check (settings live in config["updates"])
# ---------------------------------------------------------------------------
def settings(cfg: dict) -> dict:
    u = cfg.setdefault("updates", {})
    u.setdefault("auto", True)
    u.setdefault("last_check", 0)
    u.setdefault("skip", "")
    return u


def check_due(cfg: dict, now: float | None = None) -> bool:
    u = settings(cfg)
    now = time.time() if now is None else now
    last = float(u.get("last_check") or 0)
    return bool(u["auto"]) and (now - last >= CHECK_EVERY or last > now + 3600)  # clock went back


def record_check(cfg: dict, release: Release | None, now: float | None = None):
    u = settings(cfg)
    u["last_check"] = time.time() if now is None else now
    if release is not None:
        u["latest"] = release.to_json()


def offer(cfg: dict, current: str | None) -> Release | None:
    """The newer release to show in the update bar, if any (respects 'Skip this version')."""
    if not current:
        return None
    rel = Release.from_json(settings(cfg).get("latest") or {})
    if rel is None or not is_newer(rel.version, current):
        return None
    if settings(cfg).get("skip") and parse_version(settings(cfg)["skip"]) == parse_version(rel.version):
        return None
    return rel


# ---------------------------------------------------------------------------
# What to download on this machine
# ---------------------------------------------------------------------------
def install_kind() -> str:
    """'setup' (Windows installer), 'linux' (install.sh package) or 'page' (open the release page)."""
    if not getattr(sys, "frozen", False) and not os.environ.get("ESM_FAKE_VERSION"):
        return "page"
    exe_dir = Path(sys.executable).resolve().parent
    if IS_WIN:
        return "setup" if any(exe_dir.glob("unins*.exe")) else "page"
    if SYSTEM == "Linux":
        return "linux"
    return "page"


def asset_for(kind: str) -> str | None:
    return {"setup": SETUP_ASSET, "linux": LINUX_ASSET}.get(kind)


def expected_sha256(rel: Release, name: str, timeout: float = 15) -> str:
    """GitHub's own digest for the asset, else the <name>.sha256 file the build attaches."""
    a = rel.assets.get(name) or {}
    m = re.fullmatch(r"sha256:([0-9a-fA-F]{64})", a.get("digest") or "")
    if m:
        return m.group(1).lower()
    side = rel.assets.get(name + ".sha256")
    if side:
        try:
            with _open(side["url"], timeout) as r:
                text = r.read(4096).decode("utf-8", "replace")
        except OSError as ex:
            raise UpdateError(f"Couldn't download the checksum for {name} ({ex}).") from ex
        m = re.search(r"\b([0-9a-fA-F]{64})\b", text)
        if m:
            return m.group(1).lower()
    raise UpdateError(f"The release has no checksum for {name}, so it can't be installed "
                      "automatically. Download it from the release page instead.")


def download(rel: Release, name: str, progress=None, cancelled=lambda: False,
             timeout: float = 30, dest_dir: Path | None = None) -> Path:
    """Download one asset into the updates folder and verify its SHA-256. Returns the file."""
    a = rel.assets.get(name)
    if not a:
        raise UpdateError(f"Version {rel.version} has no {name} to download.")
    want = expected_sha256(rel, name)
    dest_dir = Path(dest_dir or UPDATE_DIR)
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / f"{rel.version}-{name}"
    part = final.with_name(final.name + ".part")
    h, got = hashlib.sha256(), 0
    try:
        with _open(a["url"], timeout) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or a.get("size") or 0)
            while True:
                if cancelled():
                    raise UpdateError("Download cancelled.")
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if progress:
                    progress(got, total)
    except UpdateError:
        part.unlink(missing_ok=True)
        raise
    except OSError as ex:
        part.unlink(missing_ok=True)
        raise UpdateError(f"The download failed ({getattr(ex, 'reason', ex)}).") from ex
    if a.get("size") and got != a["size"]:
        part.unlink(missing_ok=True)
        raise UpdateError(f"The download is incomplete ({got} of {a['size']} bytes).")
    if h.hexdigest() != want:
        part.unlink(missing_ok=True)
        raise UpdateError("The downloaded file doesn't match its checksum, so it wasn't installed. "
                          "Try again later.")
    os.replace(part, final)
    return final


def clean_old_downloads(keep: Path | None = None, dest_dir: Path | None = None):
    d = Path(dest_dir or UPDATE_DIR)
    if not d.is_dir():
        return
    for p in d.iterdir():
        if keep is not None and p.resolve() == Path(keep).resolve():
            continue
        try:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Installing (the caller closes the app right after these return)
# ---------------------------------------------------------------------------
def _program_files_dirs():
    return [Path(os.environ[v]).resolve() for v in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")
            if os.environ.get(v)]


def setup_command(setup: Path, exe: Path | None = None) -> list:
    """Run the new Setup.exe silently over this install. /RELAUNCH=1 makes it start the app again
    afterwards (see packaging/installer.iss); running programs, such as the sync-on-exit watcher,
    are closed by Setup first."""
    exe = Path(exe or sys.executable).resolve()
    all_users = any(str(exe).lower().startswith(str(p).lower() + os.sep) for p in _program_files_dirs())
    return [str(setup), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
            "/FORCECLOSEAPPLICATIONS", "/ALLUSERS" if all_users else "/CURRENTUSER", "/RELAUNCH=1"]


def run_setup(setup: Path):
    flags = 0x00000008 | 0x00000200                       # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen(setup_command(setup), creationflags=flags, close_fds=True)


def unpack_linux(archive: Path, dest: Path) -> Path:
    """Unpack the Linux package safely; returns the folder that holds install.sh."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    try:
        with tarfile.open(archive, "r:gz") as t:
            root = dest.resolve()
            for m in t.getmembers():
                target = (dest / m.name).resolve()
                if root != target and root not in target.parents:
                    raise UpdateError(f"The package has an unsafe path: {m.name}")
                if m.issym() or m.islnk() or m.isdev():
                    raise UpdateError(f"The package has an unexpected link: {m.name}")
            if hasattr(tarfile, "data_filter"):
                t.extractall(dest, filter="data")
            else:                                                       # Python < 3.12
                t.extractall(dest)
    except (tarfile.TarError, OSError) as ex:
        raise UpdateError(f"The package couldn't be unpacked ({ex}).") from ex
    for sh in sorted(dest.glob("*/install.sh")) + [dest / "install.sh"]:
        if sh.is_file() and (sh.parent / "app").is_dir():
            return sh.parent
    raise UpdateError("The package has no install.sh.")


def installed_linux_exe() -> Path:
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return data / "EmuSaveManager" / "EmuSaveManager"


def linux_script(pkg: Path, pid: int, log: Path, relaunch: Path) -> str:
    q = shlex.quote
    return f"""#!/bin/sh
# Emulator Save Manager update: wait for the app to close, install, start it again.
while kill -0 {pid} 2>/dev/null; do sleep 0.5; done
{{
  echo "== update $(date) =="
  chmod +x {q(str(pkg / 'install.sh'))} {q(str(pkg / 'app' / 'EmuSaveManager'))}
  "$(command -v bash || echo sh)" {q(str(pkg / 'install.sh'))} --no-steam </dev/null
  rc=$?
  echo "install.sh finished ($rc)"
  # the sync-on-exit watcher still runs the old program: restart it if it's switched on
  command -v systemctl >/dev/null && systemctl --user try-restart emu-save-manager-watch.service
}} >>{q(str(log))} 2>&1
exec {q(str(relaunch))} >/dev/null 2>&1
"""


def run_linux_update(archive: Path, dest_dir: Path | None = None) -> Path:
    """Unpack now (so errors show while the app is still open), then start a small script that
    installs once this process has exited. Returns the log file."""
    dest_dir = Path(dest_dir or UPDATE_DIR)
    pkg = unpack_linux(archive, dest_dir / "unpacked")
    log = dest_dir / "update.log"
    script = dest_dir / "apply-update.sh"
    script.write_text(linux_script(pkg, os.getpid(), log, installed_linux_exe()), encoding="utf-8")
    script.chmod(0o755)
    subprocess.Popen(["/bin/sh", str(script)], start_new_session=True, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return log
