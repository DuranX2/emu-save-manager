"""
exitwatch.py - sync right after an emulator or EmulationStation (ES-DE) closes.

    EmuSaveManager --watch     keep running quietly; sync when a watched program closes

Switched on in Cloud Sync ("Also sync right after...").  It is started at sign-in:
  * Windows : a value under HKCU\\...\\CurrentVersion\\Run (no admin rights needed)
  * Linux / Steam Deck : a systemd user service (Desktop and Game Mode)
It checks the running programs every few seconds.  When a watched one closes it waits a
moment (so the emulator has finished writing and ES-DE is fully gone) and runs the same
two-way sync as the timer.  If another emulator is running by then, it waits for that one.
It stops by itself when both options are switched off.  No GUI dependencies.
"""

import os
import socket
import subprocess
import time

import autosync

POLL_SECONDS = 5
DELAY_SECONDS = 15
SINGLE_INSTANCE_PORT = 47613          # 127.0.0.1 only; the bind doubles as "one watcher at a time"

# ES-DE: "es-de" (AppImage / ES-DE.exe), older builds and EmuDeck's launcher script
# "emulationstation…" (Linux comm is cut to 15 characters).
FRONTEND_PREFIXES = ("es-de", "emulationstatio")

WATCH_UNIT = "emu-save-manager-watch"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "EmuSaveManager Sync-on-Exit"


def options(cfg: dict) -> dict:
    o = (cfg.get("cloud") or {}).get("exit_sync") or {}
    return {"emulators": bool(o.get("emulators")), "frontend": bool(o.get("frontend"))}


def snapshot() -> dict:
    """One process scan, split into emulators and ES-DE."""
    names = autosync.running_processes(autosync.EMULATOR_PREFIXES + FRONTEND_PREFIXES,
                                       autosync.WINDOWS_EXTRA)
    fe = {n for n in names if n.startswith(FRONTEND_PREFIXES)}
    return {"emulators": set(names) - fe, "frontends": fe}


def pretty(name: str) -> str:
    n = name.lower()
    for key, label in (("pcsx2", "PCSX2"), ("duckstation", "DuckStation"), ("retroarch", "RetroArch"),
                       ("xemu", "xemu"), ("primehack", "PrimeHack"), ("dolphin", "Dolphin"),
                       ("cemu", "Cemu"), ("yuzu", "Yuzu"), ("azahar", "Azahar"),
                       ("melonds", "melonDS"), ("ppsspp", "PPSSPP"), ("rpcs3", "RPCS3"),
                       ("xenia", "Xenia"), ("es-de", "ES-DE"),
                       ("emulationstatio", "ES-DE")):
        if n.startswith(key):
            return label
    return name


class Watcher:
    """The decision logic, separate from the loop so it can be tested with fake clocks."""

    def __init__(self, first: dict, delay=DELAY_SECONDS):
        self.prev = first
        self.delay = delay
        self.due = None
        self.reason = []

    def step(self, cur: dict, opts: dict, now: float) -> bool:
        """Feed one snapshot; True means 'sync now'."""
        closed = []
        if opts["emulators"]:
            closed += sorted(self.prev["emulators"] - cur["emulators"])
        if opts["frontend"]:
            closed += sorted(self.prev["frontends"] - cur["frontends"])
        self.prev = cur
        if closed:
            self.due = now + self.delay
            for c in map(pretty, closed):
                if c not in self.reason:
                    self.reason.append(c)
        if self.due is None or now < self.due:
            return False
        if cur["emulators"]:            # a game was started meanwhile: wait until it closes
            self.due = None if opts["emulators"] else now + self.delay
            return False
        self.due = None
        return True


def single_instance():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def watcher_running() -> bool:
    s = single_instance()
    if s is None:
        return True
    s.close()
    return False


def watch(log=None, poll=POLL_SECONDS, delay=DELAY_SECONDS) -> int:
    """Entry point for `EmuSaveManager --watch`."""
    log = log or autosync.Log()
    guard = single_instance()
    if guard is None:
        return 0                                 # already running
    cfg, profiles, resolve = autosync.load_app()
    opts = options(cfg)
    if not any(opts.values()):
        return 0
    what = " or ".join(t for t, on in (("an emulator", opts["emulators"]), ("ES-DE", opts["frontend"])) if on)
    log(f"Sync-on-exit watcher started: will sync after {what} closes.")
    w = Watcher(snapshot(), delay)
    while True:
        time.sleep(poll)
        try:
            cfg, profiles, resolve = autosync.load_app()      # picks up changes made in the window
        except Exception:                                      # noqa - half-written settings file
            continue
        opts = options(cfg)
        if not any(opts.values()):
            log("Sync-on-exit watcher stopped (switched off in Cloud Sync).")
            return 0
        if w.step(snapshot(), opts, time.monotonic()):
            log(f"== Sync after {', '.join(w.reason)} closed ==")
            w.reason = []
            try:
                autosync.sync_from_config(cfg, profiles, resolve, log)
            except Exception as ex:                            # noqa - keep watching
                log(f"Error: {ex}")
                autosync.write_status(result="failed", message=str(ex))


# ---------------------------------------------------------------------------
# Starting it at sign-in
# ---------------------------------------------------------------------------
def watch_command():
    return autosync.sync_command("--watch")


def systemd_unit(command=None) -> str:
    import shlex
    cmd = " ".join(shlex.quote(c) for c in (command or watch_command()))
    return ("[Unit]\n"
            "Description=Emulator Save Manager - sync when an emulator or ES-DE closes\n\n"
            "[Service]\n"
            "Type=simple\n"
            f"ExecStart={cmd}\n"
            "Restart=on-failure\n"
            "RestartSec=30\n"
            "Nice=10\n\n"
            "[Install]\n"
            "WantedBy=default.target\n")


def _start_detached(cmd):
    flags = 0x00000008 | 0x08000000 | 0x00000200   # DETACHED_PROCESS | CREATE_NO_WINDOW | NEW_PROCESS_GROUP
    subprocess.Popen(cmd, creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def enable_watch() -> str:
    """Start the watcher now and at every sign-in.  Raises OSError with a readable message."""
    cmd = watch_command()
    if autosync.IS_WIN:
        import winreg
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(cmd))
        except OSError as ex:
            raise OSError(f"Could not add the sign-in entry: {ex}") from ex
        if not watcher_running():
            _start_detached(cmd)
        return "running now and at every sign-in"
    if autosync.SYSTEM == "Linux":
        if not autosync.shutil_which("systemctl"):
            raise OSError("systemd was not found, so the watcher can't be started here.")
        d = autosync._systemd_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{WATCH_UNIT}.service").write_text(systemd_unit(cmd), encoding="utf-8")
        for args in (["daemon-reload"], ["enable", f"{WATCH_UNIT}.service"],
                     ["restart", f"{WATCH_UNIT}.service"]):
            rc, out = autosync._run(["systemctl", "--user"] + args)
            if rc != 0:
                raise OSError(f"systemctl --user {' '.join(args)} failed: {out}")
        return f"systemd user service '{WATCH_UNIT}', running now and at every login"
    raise OSError("Sync-on-exit is only available on Windows and Linux / Steam Deck.")


def ensure_running(cfg: dict) -> bool:
    """Windows: start the watcher if sync-on-exit is on but it isn't running (for example after
    an update closed it). Linux's systemd service restarts by itself. True if one was started."""
    if not autosync.IS_WIN or not any(options(cfg).values()) or watcher_running():
        return False
    try:
        _start_detached(watch_command())
        return True
    except OSError:
        return False


def disable_watch():
    """Remove the sign-in entry.  A running watcher notices the switched-off setting and exits."""
    if autosync.IS_WIN:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, RUN_NAME)
        except FileNotFoundError:
            pass
        return
    if autosync.SYSTEM == "Linux" and autosync.shutil_which("systemctl"):
        autosync._run(["systemctl", "--user", "disable", "--now", f"{WATCH_UNIT}.service"])
        try:
            (autosync._systemd_dir() / f"{WATCH_UNIT}.service").unlink()
        except OSError:
            pass
        autosync._run(["systemctl", "--user", "daemon-reload"])


def watch_status() -> str:
    try:
        if autosync.SYSTEM == "Linux" and autosync.shutil_which("systemctl") and not autosync.IS_WIN:
            rc, out = autosync._run(["systemctl", "--user", "is-active", f"{WATCH_UNIT}.service"])
            return "watching" if out.strip() == "active" else ""
    except (OSError, subprocess.SubprocessError):
        return ""
    return "watching" if watcher_running() else ""
