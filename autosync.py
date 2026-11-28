"""
autosync.py - two-way cloud sync without the GUI, and scheduling it in the background.

    EmuSaveManager --sync            run one two-way sync with the saved Cloud Sync settings
    EmuSaveManager --sync --dry-run  show what would change, change nothing

The same engine is used by the Cloud Sync window.  A sync is skipped (and retried at
the next scheduled run) while an emulator is running, so a save that is being
written is never synced.  Newest file wins; nothing is deleted:
  * files a download would replace go to <backup folder>/cloud_overwritten/<time>/
  * files an upload would replace go to <remote>:<base parent>/EmuSaveManager-overwritten/

Scheduling uses what the OS already has, so it keeps running while the app is closed:
  * Windows : a Task Scheduler task ("EmuSaveManager Auto-Sync") every N minutes
  * Linux / Steam Deck : a systemd user timer (works in Desktop and Game Mode,
    catches up after sleep)
No GUI dependencies.
"""

import json
import os
import platform
import shlex
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import cloudsync as cs
import savehistory
import syncplan

SYSTEM = platform.system()
IS_WIN = SYSTEM == "Windows"
APP_DIR = Path.home() / ".emu_save_manager"
LOG_DIR = APP_DIR / "logs"
LOG_FILE = LOG_DIR / "autosync.log"
STATUS_FILE = APP_DIR / "autosync_status.json"
LOCK_FILE = APP_DIR / "autosync.lock"
LOCK_STALE_SECONDS = 3 * 3600
LOG_MAX_BYTES = 1_000_000

TASK_NAME = "EmuSaveManager Auto-Sync"
UNIT = "emu-save-manager-sync"
INTERVALS = (5, 10, 15, 30, 60, 120)

# (Save History snapshots each synced folder before and after a sync: savehistory.py)

# Process names (lower case) that mean "an emulator is running - don't sync now".
# Linux names are what /proc/<pid>/comm shows (max 15 characters).  Note: plain
# "dolphin" on Linux is KDE's file manager, so only "dolphin-emu*" counts there.
EMULATOR_PREFIXES = ("pcsx2", "duckstation", "retroarch", "xemu", "dolphin-emu",
                     "yuzu", "azahar", "melonds", "ppsspp", "rpcs3", "xenia",
                     "cemu", "primehack")
WINDOWS_EXTRA = ("dolphin.exe",)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def device_name() -> str:
    try:
        name = socket.gethostname() or "device"
    except OSError:
        name = "device"
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:40]


def _no_window():
    return {"creationflags": 0x08000000} if IS_WIN else {}


def running_emulators():
    """Names of emulator processes that are running right now."""
    return running_processes(EMULATOR_PREFIXES, WINDOWS_EXTRA)


def running_processes(prefixes, windows_extra=()):
    """Lower-case names of running processes that start with one of `prefixes`."""
    prefixes, windows_extra = tuple(prefixes), tuple(windows_extra)
    names = []
    try:
        if IS_WIN:
            out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True,
                                 errors="replace", timeout=20, **_no_window()).stdout
            for line in out.splitlines():
                name = line.split('","', 1)[0].strip('"').lower()
                if name.startswith(prefixes) or name in windows_extra:
                    names.append(name)
        else:
            for d in Path("/proc").iterdir():
                if not d.name.isdigit():
                    continue
                try:
                    comm = (d / "comm").read_text(errors="replace").strip().lower()
                    if comm.startswith(prefixes):
                        state = (d / "stat").read_text(errors="replace").rsplit(")", 1)[1].split()[0]
                        if state in ("Z", "X"):          # exited, not reaped yet
                            continue
                except (OSError, IndexError):
                    continue
                if comm.startswith(prefixes):
                    names.append(comm)
    except (OSError, subprocess.SubprocessError):
        pass
    return sorted(set(names))


class Log:
    """Writes to the log file (and to an optional callback, e.g. the window)."""

    def __init__(self, path: Path = LOG_FILE, echo=None):
        self.path = path
        self.echo = echo
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_file() and path.stat().st_size > LOG_MAX_BYTES:
                old = path.with_suffix(".old.log")
                if old.exists():
                    old.unlink()
                path.rename(old)
        except OSError:
            pass

    def __call__(self, text: str):
        if self.echo:
            self.echo(text)
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {text}\n")
        except OSError:
            pass


STOP_FILE = APP_DIR / "autosync.stop"


def _boot_id() -> str:
    """Changes at every restart, so a lock from before a restart is never trusted."""
    try:
        if IS_WIN:
            import ctypes
            up = ctypes.windll.kernel32.GetTickCount64() / 1000.0
        else:
            try:
                return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            except OSError:
                up = time.monotonic()
        return str(int((time.time() - up) / 60))          # boot time, to the minute
    except Exception:  # noqa
        return ""


def _pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    if IS_WIN:
        try:
            import ctypes
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            ok = k.GetExitCodeProcess(h, ctypes.byref(code))
            k.CloseHandle(h)
            return bool(ok) and code.value == 259             # STILL_ACTIVE
        except Exception:  # noqa
            return True
    try:
        os.kill(pid, 0)
        try:                                                   # a finished process not yet reaped
            if Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z":
                return False
        except (OSError, IndexError):
            pass
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _pid_is_ours(pid: int) -> bool:
    """Only ever end a process that is this app (never an unrelated program that reused the pid)."""
    marks = ("emu_save_manager", "emusavemanager", "autosync", "exitwatch")
    try:
        if IS_WIN:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                 creationflags=0x08000000, timeout=15).stdout.lower()
            return any(m in out for m in marks) or "python" in out
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").lower()
        return any(m in cmd for m in marks)
    except Exception:  # noqa
        return False


def _terminate(pid: int):
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, creationflags=0x08000000, timeout=30)
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
    except Exception:  # noqa
        pass


class SyncLock:
    """Only one sync at a time (window, background or a restore).  The lock file says who holds
    it, so a lock left by a sync that died or by a previous boot is cleared at once, and a
    manual sync can ask a running background sync to stop (request_stop)."""

    def __init__(self, path: Path = LOCK_FILE, kind: str = "background"):
        self.path = path
        self.kind = kind
        self.held = False

    def owner(self):
        """{'pid', 'started', 'kind', 'boot'} of the current holder, or None."""
        try:
            text = self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        try:
            info = json.loads(text)
            if isinstance(info, dict):
                return info
        except ValueError:
            pass
        parts = text.split()                                   # older format: "pid time"
        try:
            return {"pid": int(parts[0]), "started": float(parts[1]), "kind": "background", "boot": None}
        except (IndexError, ValueError):
            return {"pid": 0, "started": 0, "kind": "unknown", "boot": None}

    def _stale(self, info) -> bool:
        try:
            if time.time() - self.path.stat().st_mtime > LOCK_STALE_SECONDS:
                return True
        except OSError:
            return True
        if not info:
            return False
        if info.get("boot") and info.get("boot") != _boot_id():
            return True
        return not _pid_alive(int(info.get("pid") or 0))

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(3):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w") as f:
                    json.dump({"pid": os.getpid(), "started": time.time(), "kind": self.kind,
                               "boot": _boot_id()}, f)
                self.held = True
                return True
            except FileExistsError:
                if self._stale(self.owner()):
                    try:
                        self.path.unlink()                     # left behind by a sync that died
                    except OSError:
                        pass
                    continue
                return False
        return False

    def busy_with(self):
        """The live holder's info if another sync is running, else None."""
        info = self.owner()
        if info is None or self._stale(info):
            return None
        return info

    def request_stop(self, timeout: float = 20, log=None) -> bool:
        """Ask the sync holding the lock to stop, wait for it, end it if it doesn't respond.
        Returns True once the lock is free."""
        info = self.busy_with()
        if not info:
            return True
        pid = int(info.get("pid") or 0)
        try:
            STOP_FILE.write_text(str(pid), encoding="utf-8")
        except OSError:
            pass
        end = time.time() + timeout
        while time.time() < end:
            if not self.busy_with():
                break
            time.sleep(0.5)
        else:
            if pid and pid != os.getpid() and _pid_alive(pid) and _pid_is_ours(pid):
                if log:
                    log("The background sync didn't stop by itself - ending it.")
                _terminate(pid)
                for _ in range(20):
                    if not _pid_alive(pid):
                        break
                    time.sleep(0.5)
        try:
            STOP_FILE.unlink()
        except OSError:
            pass
        if not self.busy_with():
            try:
                if self.path.exists() and self._stale(self.owner()):
                    self.path.unlink()
            except OSError:
                pass
            return True
        return False

    def release(self):
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass
            self.held = False


def watch_for_stop(runner, log, interval: float = 1.0):
    """Background syncs: stop cleanly when the window asks (STOP_FILE names this process).
    Returns a function that ends the watching."""
    import threading
    done = threading.Event()

    def loop():
        while not done.wait(interval):
            try:
                if STOP_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
                    log("Stopping: a manual sync from the window is taking over.")
                    runner.cancel()
                    return
            except OSError:
                pass

    threading.Thread(target=loop, daemon=True).start()
    return done.set


def write_status(**info):
    info.setdefault("time", datetime.now().isoformat(timespec="seconds"))
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        STATUS_FILE.write_text(json.dumps(info, indent=2), encoding="utf-8")
    except OSError:
        pass


def read_status() -> dict:
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# The sync itself (shared by the window and the background task)
# ---------------------------------------------------------------------------
def build_jobs(cfg: dict, profiles: dict, resolve):
    """Enabled emulator folders that exist on this device:
    [{'emu', 'label', 'local': Path, 'sub'}]."""
    pairs = cfg.get("cloud", {}).get("pairs", {})
    jobs = []
    for emu, prof in profiles.items():
        resolved = resolve(emu)
        for loc in prof["locations"]:
            label = loc["label"]
            saved = pairs.get(f"{emu}::{label}", {})
            enabled = saved.get("enabled", (emu, label) not in cs.DISABLED_BY_DEFAULT)
            local = resolved.get(label)
            if enabled and local and Path(local).is_dir():
                jobs.append({"emu": emu, "label": label, "local": Path(local),
                             "sub": cs.effective_subpath(emu, label, saved.get("sub")),
                             "exclude": list(loc.get("exclude_dirs", ()))})
    return jobs


def run_jobs(runner, jobs, remote: str, base: str, mode: str, dry: bool, backup_dir: Path, log,
             history=None):
    """mode: 'download', 'upload' or 'both' (download first, then upload).
    history: the 'history' settings (see savehistory.DEFAULTS); None = defaults.
    Returns (steps ok, steps failed)."""
    if not dry:
        n = savehistory.snapshot_jobs(jobs, backup_dir, history, "local", log)
        if n:
            log(f"History: kept {n} changed file(s) from this device.")
    try:
        return _run_jobs(runner, jobs, remote, base, mode, dry, backup_dir, log)
    finally:
        if not dry:
            n = savehistory.snapshot_jobs(jobs, backup_dir, history, "cloud", log)
            if n:
                log(f"History: kept {n} file(s) that arrived from the cloud.")


def _run_jobs(runner, jobs, remote: str, base: str, mode: str, dry: bool, backup_dir: Path, log):
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    local_backup = Path(backup_dir) / "cloud_overwritten" / stamp
    remote_backup = f"{cs.remote_backup_root(remote, base)}/{device_name()}/{stamp}"
    emudeck_layout = base.strip("/ ").lower().endswith("emudeck/saves")
    ok = failed = 0
    uploaded = set()
    for p in jobs:
        if runner.cancelled:
            break
        rpath = cs.remote_path(remote, base, p["sub"])
        log(f"== {p['emu']} / {p['label']} ==")
        log({"download": "Downloading", "upload": "Uploading", "both": "Syncing"}[mode] + f"  {rpath}")
        res = syncplan.sync_job(runner, p, rpath, mode, dry,
                                local_backup / p["emu"] / p["label"],
                                f"{remote_backup}/{p['emu']}/{p['label']}", log)
        if res["nothing_in_cloud"]:
            log("Nothing in the cloud for this folder yet.")
        if res["ok"]:
            ok += 1
            if res["uploaded"]:
                uploaded.add(cs.emulator_folder(p["sub"]))
        else:
            failed += 1
    if uploaded and emudeck_layout and not dry and not runner.cancelled:
        log("Telling EmuDeck devices there are new saves (.hash)…")
        if not cs.bump_emudeck_hash(runner, remote, base, uploaded):
            log("Could not update .hash (EmuDeck's own CloudSync may not notice these changes).")
    return ok, failed


def sync_from_config(cfg: dict, profiles: dict, resolve, log, mode="both", dry=False,
                     respect_emulators=True) -> int:
    """One complete sync with the saved Cloud Sync settings.  Exit code:
    0 ok, 1 some steps failed, 2 not set up, 3 skipped (emulator running / already syncing)."""
    c = cfg.get("cloud", {})
    exe = (cs.usable_rclone(c.get("rclone", ""), log, conf=c.get("config", ""), remote=c.get("remote", ""))
           if c.get("remote") else "")
    conf, remote = c.get("config", ""), c.get("remote", "")
    if remote and (not conf or not Path(conf).is_file() or remote not in dict(cs.list_remotes(conf))):
        conf = cs.find_config("")                        # e.g. EmuDeck's file was reset: the app's own copy
    conf = cs.adopt_config(conf, remote)
    base = c.get("base") or cs.DEFAULT_BASE
    if not exe or not Path(exe).is_file() or not remote:
        log("Cloud Sync is not set up yet (open Cloud Sync in the app first).")
        write_status(result="not-set-up", message="Cloud Sync is not set up")
        return 2
    if not cs.emudeck_cloud_base_ok(remote, base):
        user = cs.discover_emudeck_cloud_user(cs.RcloneRunner(exe, conf, lambda s: None), remote)
        if not user:
            log(f"Skipped: {remote} is EmuDeck Cloud, but the cloud folder '{base}' is not your EmuDeck "
                "Cloud folder. Open Cloud Sync and pick your OneDrive remote, or use 'Set up EmuDeck Cloud…'.")
            write_status(result="failed", message="EmuDeck Cloud folder not set - open Cloud Sync")
            return 1
        base = cs.emudeck_cloud_base(remote, user)
        log(f"Using your EmuDeck Cloud folder {base}.")
    if respect_emulators:
        emus = running_emulators()
        if emus:
            log(f"Skipped: an emulator is running ({', '.join(emus)}). Will try again next time.")
            write_status(result="skipped", message=f"emulator running: {', '.join(emus)}")
            return 3
    lock = SyncLock()
    if not lock.acquire():
        log("Skipped: another sync is already running.")
        write_status(result="skipped", message="another sync was running")
        return 3
    runner = cs.RcloneRunner(exe, conf, log)
    stop_watching = watch_for_stop(runner, log)
    try:
        jobs = build_jobs(cfg, profiles, resolve)
        ok = failed = 0
        if jobs:
            log(f"Sync ({mode}{', preview' if dry else ''}) on {device_name()} with {remote}:{base}")
            ok, failed = run_jobs(runner, jobs, remote, base, mode, dry,
                                  Path(cfg.get("backup_dir") or APP_DIR / "backups"), log,
                                  history=cfg.get("history"))
        else:
            log("No enabled save folder exists on this device.")
        if not runner.cancelled:
            failed += bios_vault_steps(cfg, resolve, log, dry, runner)
        if runner.cancelled:
            log(f"Stopped early: {ok} step(s) OK, {failed} failed.")
            write_status(result="stopped", ok=ok, failed=failed,
                         message="stopped for a manual sync from the window")
            return 3
        n_conf = syncplan.conflict_count()
        log(f"Finished: {ok} step(s) OK, {failed} failed." +
            (f" {n_conf} conflict(s) need your choice - open Cloud Sync." if n_conf else ""))
        write_status(result="ok" if not failed and not n_conf else "failed", ok=ok, failed=failed,
                     conflicts=n_conf,
                     message=f"{ok} step(s) OK, {failed} failed" + (" (preview)" if dry else "") +
                             (f", {n_conf} conflict(s) to review" if n_conf else ""))
        return 0 if not failed else 1
    finally:
        stop_watching()
        lock.release()


def bios_vault_steps(cfg, resolve, log, dry=False, runner=None) -> int:
    """The BIOS Vault part of a sync, when switched on in the BIOS Vault window.
    Returns the number of failed steps."""
    bv = cfg.get("bios_vault", {})
    if dry or not (bv.get("sync") or bv.get("auto_install")):
        return 0
    import biostargets
    import biosvault
    failed = 0
    if bv.get("sync"):
        log("== BIOS Vault ==")
        if not biosvault.sync_cloud(cfg, log, runner=runner):
            failed += 1
    if bv.get("auto_install"):
        entries = biosvault.scan(biosvault.vault_dir(cfg))
        if entries:
            res = biostargets.install_all(entries, resolve, log)
            failed += res["failed"]
    return failed


def load_app():
    """(cfg, profiles, resolve) from the saved settings, without the window."""
    import emu_save_manager as esm          # profiles and config live in the main module
    cfg = esm.load_config()
    profiles = esm.builtin_profiles()
    profiles.update(cfg.get("custom", {}))

    def resolve(emu):
        res = {}
        for loc in profiles[emu]["locations"]:
            override = cfg.get("overrides", {}).get(f"{emu}::{loc['label']}")
            res[loc["label"]] = Path(override) if override else next(
                (Path(c) for c in loc["candidates"] if Path(c).is_dir()), None)
        return res

    return cfg, profiles, resolve


def main_cli(argv) -> int:
    """Entry point for `EmuSaveManager --sync` (no window)."""
    log = Log(echo=(print if sys.stdout and sys.stdout.isatty() else None))
    cfg, profiles, resolve = load_app()
    mode = "both"
    for m in ("download", "upload"):
        if f"--{m}" in argv:
            mode = m
    try:
        return sync_from_config(cfg, profiles, resolve, log, mode=mode, dry="--dry-run" in argv,
                                respect_emulators="--force" not in argv)
    except Exception as ex:  # noqa - a background task must log, not crash silently
        log(f"Error: {ex}")
        write_status(result="failed", message=str(ex))
        return 1


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------
def sync_command(flag="--sync"):
    """The command the scheduler runs: [program, args...]."""
    if getattr(sys, "frozen", False):
        return [sys.executable, flag]
    py = Path(sys.executable)
    if IS_WIN and py.name.lower() == "python.exe" and (py.parent / "pythonw.exe").is_file():
        py = py.parent / "pythonw.exe"           # no console window every few minutes
    script = Path(__file__).resolve().parent / "emu_save_manager.py"
    return [str(py), str(script), flag]


def _systemd_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd" / "user"


def systemd_units(minutes: int, command=None):
    """(service text, timer text) for the Linux / Steam Deck timer."""
    cmd = " ".join(shlex.quote(c) for c in (command or sync_command()))
    service = (
        "[Unit]\n"
        "Description=Emulator Save Manager - two-way cloud sync of emulator saves\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={cmd}\n"
        "Nice=10\n"
    )
    timer = (
        "[Unit]\n"
        "Description=Emulator Save Manager - automatic cloud sync\n\n"
        "[Timer]\n"
        "OnStartupSec=2min\n"
        f"OnUnitActiveSec={int(minutes)}min\n"
        "AccuracySec=30s\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    return service, timer


def _run(cmd, timeout=30):
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout,
                       **_no_window())
    return r.returncode, (r.stdout + r.stderr).strip()


def schedule_supported() -> bool:
    return IS_WIN or (SYSTEM == "Linux" and bool(shutil_which("systemctl")))


def shutil_which(name):
    import shutil
    return shutil.which(name)


def enable_schedule(minutes: int):
    """Turn on (or change) the background sync.  Raises OSError with a readable message."""
    minutes = int(minutes)
    if IS_WIN:
        cmd = sync_command()
        tr = subprocess.list2cmdline(cmd)
        rc, out = _run(["schtasks", "/Create", "/F", "/TN", TASK_NAME, "/SC", "MINUTE",
                        "/MO", str(minutes), "/TR", tr])
        if rc != 0:
            raise OSError(f"Task Scheduler refused: {out}")
        return f"Windows Task Scheduler task '{TASK_NAME}', every {minutes} min"
    if SYSTEM == "Linux":
        if not shutil_which("systemctl"):
            raise OSError("systemd was not found, so automatic sync can't be scheduled here.")
        d = _systemd_dir()
        d.mkdir(parents=True, exist_ok=True)
        service, timer = systemd_units(minutes)
        (d / f"{UNIT}.service").write_text(service, encoding="utf-8")
        (d / f"{UNIT}.timer").write_text(timer, encoding="utf-8")
        for args in (["daemon-reload"], ["enable", "--now", f"{UNIT}.timer"],
                     ["restart", f"{UNIT}.timer"]):
            rc, out = _run(["systemctl", "--user"] + args)
            if rc != 0:
                raise OSError(f"systemctl --user {' '.join(args)} failed: {out}")
        return f"systemd user timer '{UNIT}.timer', every {minutes} min"
    raise OSError("Automatic sync is only available on Windows and Linux / Steam Deck.")


def disable_schedule():
    if IS_WIN:
        rc, out = _run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME])
        if rc != 0 and "cannot find" not in out.lower() and "does not exist" not in out.lower():
            raise OSError(f"Task Scheduler refused: {out}")
        return
    if SYSTEM == "Linux" and shutil_which("systemctl"):
        _run(["systemctl", "--user", "disable", "--now", f"{UNIT}.timer"])
        d = _systemd_dir()
        for f in (d / f"{UNIT}.timer", d / f"{UNIT}.service"):
            try:
                f.unlink()
            except OSError:
                pass
        _run(["systemctl", "--user", "daemon-reload"])


def schedule_status():
    """(enabled: bool, description)."""
    try:
        if IS_WIN:
            rc, out = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"])
            if rc != 0:
                return False, "off"
            nxt = next((l.split(":", 1)[1].strip() for l in out.splitlines()
                        if l.lower().startswith("next run time")), "")
            return True, f"on (next run {nxt})" if nxt else "on"
        if SYSTEM == "Linux" and shutil_which("systemctl"):
            rc, _out = _run(["systemctl", "--user", "is-enabled", f"{UNIT}.timer"])
            if rc != 0:
                return False, "off"
            rc, out = _run(["systemctl", "--user", "list-timers", f"{UNIT}.timer", "--no-legend"])
            parts = out.split()
            return True, (f"on (next run {' '.join(parts[:4])})" if rc == 0 and len(parts) > 4 else "on")
    except (OSError, subprocess.SubprocessError):
        pass
    return False, "off"
