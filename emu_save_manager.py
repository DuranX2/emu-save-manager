#!/usr/bin/env python3
"""
Emulator Save Manager
=====================
A cross-platform GUI (tkinter, standard library only) to back up and restore
save data for PCSX2, Xemu, DuckStation and RetroArch, plus custom emulators.

  * PS2 Card Manager : two cards side by side - copy / move saves between them,
                      create cards, import / export (.psu), delete
  * BIOS Vault       : your own BIOS files identified by content, kept in one place,
                       synced between devices and installed into each emulator
  * Save History     : earlier versions of every synced save, kept at each sync;
                       put back one file or a whole folder as it was at a given time
  * Cloud Sync       : two-way sync with OneDrive (or EmuDeck Cloud) via rclone, using
                       EmuDeck's folder layout so a PC and a Steam Deck share the same saves;
                       optional automatic sync in the background on both

Run:  python emu_save_manager.py          (Python 3.8+; keep the other .py files beside it)
      python emu_save_manager.py --sync   one two-way sync without the window
      python emu_save_manager.py --watch  sync after an emulator / ES-DE closes (started by the app)
      EmuSaveManager --add-to-steam      Linux / Steam Deck: put it in the Steam library (Game Mode)
                                         with artwork; --remove-from-steam takes it out again
"""

import copy
import fnmatch
import json
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import threading
import time


def _import_tkinter(tries=40, wait=0.5):
    """Windows: right after an update, Setup or the virus scanner can still have Tk's DLLs open
    for a moment ("being used by another process"), so the first start failed. Wait it out."""
    for n in range(tries):
        try:
            import tkinter  # noqa: F401
            return
        except ImportError as ex:
            text = str(ex).lower()
            if n == tries - 1 or not ("being used by another process" in text or "access is denied" in text):
                raise
            for m in [m for m in sys.modules if m == "tkinter" or m.startswith("tkinter.") or m == "_tkinter"]:
                sys.modules.pop(m, None)
            time.sleep(wait)


_import_tkinter()
import tkinter as tk
import tkinter.font as tkfont
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

APP_NAME = "Emulator Save Manager"
SYSTEM = platform.system()  # "Windows", "Linux", "Darwin"
HOME = Path.home()
CONFIG_DIR = HOME / ".emu_save_manager"
CONFIG_FILE = CONFIG_DIR / "config.json"
MANIFEST = "manifest.json"


# --------------------------------------------------------------------------
# Emulator profiles
# --------------------------------------------------------------------------
def builtin_profiles():
    """Known emulators with candidate save locations for every OS.
    The first candidate that exists on this machine is used."""
    docs = [HOME / "Documents", HOME / "OneDrive" / "Documents"]
    appdata = Path(os.environ.get("APPDATA", HOME / "AppData" / "Roaming"))
    config = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
    data = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local" / "share"))
    mac = HOME / "Library" / "Application Support"
    flat = HOME / ".var" / "app"

    emudeck_dolphin = (HOME / "emudeck" / "EmulationStation-DE" / "Emulators" / "Dolphin-x64")
    dolphin_bases = ([appdata / "Dolphin Emulator"]
                     + [d / "Dolphin Emulator" for d in docs]
                     + [emudeck_dolphin / "User",
                        data / "dolphin-emu", HOME / ".dolphin-emu",
                        mac / "Dolphin",
                        flat / "org.DolphinEmu.dolphin-emu" / "data" / "dolphin-emu"])

    def loc(label, cands, patterns=("*",), recursive=True, exclude_dirs=()):
        d = {
            "label": label,
            "candidates": [str(c) for c in cands],
            "patterns": list(patterns),
            "recursive": recursive,
        }
        if exclude_dirs:
            d["exclude_dirs"] = list(exclude_dirs)    # sub-folders that aren't saves (skipped everywhere)
        return d

    emudeck_emus = appdata / "EmuDeck" / "Emulators"
    # PrimeHack is a Dolphin fork with a User folder of its own (portable next to the program on
    # Windows, the Flatpak's data folder on the Steam Deck).
    primehack_bases = [emudeck_emus / "primehack" / "User",
                       flat / "io.github.shiiion.primehack" / "data" / "dolphin-emu",
                       data / "primehack", appdata / "PrimeHack"]
    # Cemu keeps Wii U saves in mlc01/usr/save. EmuDeck puts mlc01 in Emulation/roms/wiiu on the
    # Steam Deck and in its Emulators/cemu folder on Windows.
    cemu_mlcs = ([emudeck_emus / "cemu" / "mlc01", appdata / "Cemu" / "mlc01"]
                 + [r / "roms" / "wiiu" / "mlc01" for r in _emudeck_roots_safe()]
                 + [data / "Cemu" / "mlc01", mac / "Cemu" / "mlc01",
                    flat / "info.cemu.Cemu" / "data" / "Cemu" / "mlc01"])
    # Xbox 360 content types inside Xenia's content/<profile or 0...0>/<title id>/ folders that are
    # downloads, not saves: DLC, title updates, installed/on-demand/arcade/demo games.
    XENIA_NOT_SAVES = ("00000002", "000B0000", "00004000", "00007000", "000D0000", "00080000")

    return _with_emudeck_folders({
        "PCSX2": {
            "notes": "PlayStation 2. Memory cards and save states. "
                     "Close PCSX2 before restoring. Portable install? Use 'Locations…'.",
            "locations": [
                loc("Memory Cards",
                    [d / "PCSX2" / "memcards" for d in docs]
                    + [config / "PCSX2" / "memcards",
                       mac / "PCSX2" / "memcards",
                       flat / "net.pcsx2.PCSX2" / "config" / "PCSX2" / "memcards"]),
                loc("Save States",
                    [d / "PCSX2" / "sstates" for d in docs]
                    + [config / "PCSX2" / "sstates",
                       mac / "PCSX2" / "sstates",
                       flat / "net.pcsx2.PCSX2" / "config" / "PCSX2" / "sstates"]),
            ],
        },
        "Xemu": {
            "notes": "Original Xbox. Game saves live INSIDE the virtual HDD image "
                     "(xbox_hdd.qcow2), so the whole image is backed up. These files can be "
                     "large. Close xemu before backing up or restoring.",
            "locations": [
                loc("HDD and EEPROM",
                    [appdata / "xemu" / "xemu",
                     data / "xemu" / "xemu",
                     mac / "xemu" / "xemu",
                     mac / "xemu.app",
                     flat / "app.xemu.xemu" / "data" / "xemu" / "xemu"],
                    patterns=("*.qcow2", "eeprom*.bin"), recursive=False),
            ],
        },
        "DuckStation": {
            "notes": "PlayStation 1. Memory cards and save states. "
                     "Close DuckStation before restoring.",
            "locations": [
                loc("Memory Cards",
                    [d / "DuckStation" / "memcards" for d in docs]
                    + [data / "duckstation" / "memcards",
                       mac / "DuckStation" / "memcards",
                       flat / "org.duckstation.DuckStation" / "data" / "duckstation" / "memcards"]),
                loc("Save States",
                    [d / "DuckStation" / "savestates" for d in docs]
                    + [data / "duckstation" / "savestates",
                       mac / "DuckStation" / "savestates",
                       flat / "org.duckstation.DuckStation" / "data" / "duckstation" / "savestates"]),
            ],
        },
        "Dolphin": {
            "notes": "GameCube and Wii. GameCube memory cards, the Wii NAND (Wii game saves plus "
                     "system data) and save states. Close Dolphin before restoring. Portable "
                     "install? Use 'Locations…'. The Wii folder can be large; states are big too.",
            "locations": [
                loc("GameCube Saves", [b / "GC" for b in dolphin_bases]),
                loc("Wii Saves", [b / "Wii" for b in dolphin_bases]),
                loc("Save States", [b / "StateSaves" for b in dolphin_bases]
                    + [emudeck_dolphin / "StateSaves"]),
            ],
        },
        "PrimeHack": {
            "prefer_emudeck": True,
            "notes": "Metroid Prime Trilogy (a Dolphin fork). It keeps its own GameCube and Wii saves "
                     "and save states, separate from Dolphin's. Close PrimeHack before restoring. "
                     "Portable install somewhere else? Use 'Locations…'.",
            "locations": [
                loc("GameCube Saves", [b / "GC" for b in primehack_bases]),
                loc("Wii Saves", [b / "Wii" for b in primehack_bases]),
                # EmuDeck links this as saves/primehack/states on Windows but .../StateSaves on Linux
                loc("Save States", [b / "StateSaves" for b in primehack_bases]
                    + [r / "saves" / "primehack" / "StateSaves" for r in _emudeck_roots_safe()]),
            ],
        },
        "Cemu": {
            "prefer_emudeck": True,
            "notes": "Wii U. Game saves (mlc01/usr/save, one folder per game and user). Cemu has no "
                     "save states. A portable Cemu keeps mlc01 next to the program: use 'Locations…'. "
                     "Close Cemu before restoring.",
            "locations": [
                loc("Saves", [m / "usr" / "save" for m in cemu_mlcs]),
            ],
        },
        "Yuzu": {
            "prefer_emudeck": True,
            "notes": "Nintendo Switch. Game saves (the NAND user save folder) and user profile "
                     "pictures. Close Yuzu before restoring.",
            "locations": [
                loc("Saves",
                    [appdata / "yuzu" / "nand" / "user" / "save",
                     emudeck_emus / "yuzu" / "yuzu-windows-msvc" / "user" / "nand" / "user" / "save",
                     data / "yuzu" / "nand" / "user" / "save",
                     HOME / "Emulation" / "storage" / "yuzu" / "nand" / "user" / "save",
                     flat / "org.yuzu_emu.yuzu" / "data" / "yuzu" / "nand" / "user" / "save"]),
                loc("Profiles",
                    [appdata / "yuzu" / "nand" / "system" / "save" / "8000000000000010" / "su" / "avators",
                     data / "yuzu" / "nand" / "system" / "save" / "8000000000000010" / "su" / "avators"]),
            ],
        },
        "Azahar": {
            "prefer_emudeck": True,
            "notes": "Nintendo 3DS (Citra's successor). The SD card folder (game saves and extra "
                     "data) and save states. Close Azahar before restoring.",
            "locations": [
                loc("Saves",
                    [appdata / "Azahar" / "sdmc",
                     emudeck_emus / "azahar" / "user" / "sdmc",
                     data / "azahar-emu" / "sdmc",
                     HOME / "Emulation" / "storage" / "azahar" / "sdmc",
                     flat / "io.github.azahar.Azahar" / "data" / "azahar-emu" / "sdmc"]),
                loc("Save States",
                    [appdata / "Azahar" / "states",
                     emudeck_emus / "azahar" / "user" / "states",
                     data / "azahar-emu" / "states",
                     flat / "io.github.azahar.Azahar" / "data" / "azahar-emu" / "states"]),
            ],
        },
        "melonDS": {
            "prefer_emudeck": True,
            "notes": "Nintendo DS. Game saves (.sav) and save states. melonDS keeps saves next to "
                     "the ROM unless a save folder is set (EmuDeck sets Emulation/saves/melonds); "
                     "otherwise use 'Locations…'. Close melonDS before restoring.",
            "locations": [
                loc("Saves", [config / "melonDS" / "saves",
                              flat / "net.kuribo64.melonDS" / "data" / "melonDS" / "saves"]),
                loc("Save States", [config / "melonDS" / "states",
                                    flat / "net.kuribo64.melonDS" / "data" / "melonDS" / "states"]),
            ],
        },
        "PPSSPP": {
            "prefer_emudeck": True,
            "notes": "PlayStation Portable. Game saves (PSP/SAVEDATA) and save states "
                     "(PSP/PPSSPP_STATE). Close PPSSPP before restoring.",
            "locations": [
                loc("Saves",
                    [d / "PPSSPP" / "PSP" / "SAVEDATA" for d in docs]
                    + [emudeck_emus / "PPSSPP" / "memstick" / "PSP" / "SAVEDATA",
                       config / "ppsspp" / "PSP" / "SAVEDATA",
                       flat / "org.ppsspp.PPSSPP" / "config" / "ppsspp" / "PSP" / "SAVEDATA"]),
                loc("Save States",
                    [d / "PPSSPP" / "PSP" / "PPSSPP_STATE" for d in docs]
                    + [emudeck_emus / "PPSSPP" / "memstick" / "PSP" / "PPSSPP_STATE",
                       config / "ppsspp" / "PSP" / "PPSSPP_STATE",
                       flat / "org.ppsspp.PPSSPP" / "config" / "ppsspp" / "PSP" / "PPSSPP_STATE"]),
            ],
        },
        "RPCS3": {
            "prefer_emudeck": True,
            "notes": "PlayStation 3. Game saves (dev_hdd0/home/00000001/savedata) and trophies. "
                     "Portable install? Use 'Locations…'. Close RPCS3 before restoring.",
            "locations": [
                loc("Saves",
                    [config / "rpcs3" / "dev_hdd0" / "home" / "00000001" / "savedata",
                     HOME / "Emulation" / "storage" / "rpcs3" / "dev_hdd0" / "home" / "00000001" / "savedata",
                     flat / "net.rpcs3.RPCS3" / "config" / "rpcs3" / "dev_hdd0" / "home" / "00000001" / "savedata",
                     appdata / "rpcs3" / "dev_hdd0" / "home" / "00000001" / "savedata"]),
                loc("Trophies",
                    [config / "rpcs3" / "dev_hdd0" / "home" / "00000001" / "trophy",
                     HOME / "Emulation" / "storage" / "rpcs3" / "dev_hdd0" / "home" / "00000001" / "trophy",
                     flat / "net.rpcs3.RPCS3" / "config" / "rpcs3" / "dev_hdd0" / "home" / "00000001" / "trophy"]),
            ],
        },
        "Xenia": {
            "prefer_emudeck": True,
            "notes": "Xbox 360. Game saves and profiles from Xenia's content folder. Installed DLC, "
                     "title updates and installed games in the same folder are skipped (they can be "
                     "gigabytes). Close Xenia before restoring.",
            "locations": [
                loc("Saves",
                    [d / "Xenia" / "content" for d in docs]
                    + [emudeck_emus / "xenia" / "content"],
                    exclude_dirs=XENIA_NOT_SAVES),
            ],
        },
        "RetroArch": {
            "notes": "Multi-system. In-game saves (.srm etc.) and save states. RetroArch "
                     "install locations vary, so use 'Locations…' if nothing is detected.",
            "locations": [
                loc("Saves",
                    [Path("C:/RetroArch-Win64/saves"), Path("C:/RetroArch/saves"),
                     appdata / "RetroArch" / "saves",
                     config / "retroarch" / "saves",
                     mac / "RetroArch" / "saves",
                     HOME / "Documents" / "RetroArch" / "saves",
                     flat / "org.libretro.RetroArch" / "config" / "retroarch" / "saves"]),
                loc("States",
                    [Path("C:/RetroArch-Win64/states"), Path("C:/RetroArch/states"),
                     appdata / "RetroArch" / "states",
                     config / "retroarch" / "states",
                     mac / "RetroArch" / "states",
                     HOME / "Documents" / "RetroArch" / "states",
                     flat / "org.libretro.RetroArch" / "config" / "retroarch" / "states"]),
            ],
        },
    })


def _emudeck_roots_safe():
    try:
        import cloudsync
        return list(cloudsync.emudeck_roots())
    except Exception:  # noqa - never let detection break the app
        return []


def _with_emudeck_folders(profiles):
    """EmuDeck points every emulator at Emulation/saves/<emulator>/... (on the Steam Deck these
    are the folders the emulators really use). Prefer them on Linux, and for emulators usually run
    through EmuDeck (prefer_emudeck); elsewhere try them last."""
    try:
        import cloudsync
        bases = [r / "saves" for r in cloudsync.emudeck_roots()]
        subs = cloudsync.EMUDECK_SUBPATHS
    except Exception:  # noqa - never let detection break the app
        return profiles
    for emu, prof in profiles.items():
        for loc in prof["locations"]:
            sub = subs.get((emu, loc["label"]))
            if not sub:
                continue
            extra = [str(b / sub) for b in bases]
            first = SYSTEM == "Linux" or prof.get("prefer_emudeck")
            loc["candidates"] = extra + loc["candidates"] if first else loc["candidates"] + extra
    return profiles

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
def load_config():
    cfg = {"backup_dir": str(CONFIG_DIR / "backups"), "overrides": {}, "custom": {}, "cloud": {}}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
@dataclass
class Entry:
    location: str
    rel: Path
    path: Path
    size: int
    mtime: float


def human_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def safe_name(name):
    return re.sub(r"[^\w.\- ]", "_", name)


def is_within(child, parent):
    try:
        Path(os.path.abspath(child)).relative_to(Path(os.path.abspath(parent)))
        return True
    except ValueError:
        return False


def in_game_mode() -> bool:
    """True in the Steam Deck's Game Mode (gamescope), which shows only one window per app."""
    if SYSTEM != "Linux":
        return False
    return ("gamescope" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
            or os.environ.get("SteamGamepadUI") == "1" or bool(os.environ.get("GAMESCOPE_WAYLAND_DISPLAY")))


def launch_library(*args):
    """Start the ROM Library (this program with --rom-library) in its own process; returns the
    process, or None if it couldn't be started."""
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--rom-library", *args]
    else:
        cmd = [sys.executable, str(Path(__file__).resolve()), "--rom-library", *args]
    try:
        return subprocess.Popen(cmd, close_fds=True)
    except OSError:
        return None


def open_folder(path):
    path = str(path)
    if SYSTEM == "Windows":
        os.startfile(path)  # noqa
    elif SYSTEM == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def matches(name, patterns):
    low = name.lower()
    return any(fnmatch.fnmatchcase(low, p.lower()) for p in patterns)


def scan_location(label, base, patterns, recursive, exclude_dirs=()):
    out = []
    if not base or not base.is_dir():
        return out
    skip = {d.lower() for d in exclude_dirs}
    iterator = base.rglob("*") if recursive else base.glob("*")
    for p in iterator:
        try:
            if skip and any(part.lower() in skip for part in p.relative_to(base).parts[:-1]):
                continue
            if p.is_file() and matches(p.name, patterns):
                st = p.stat()
                out.append(Entry(label, p.relative_to(base), p, st.st_size, st.st_mtime))
        except OSError:
            continue
    out.sort(key=lambda e: str(e.rel).lower())
    return out


def unique_path(path):
    if not path.exists():
        return path
    i = 2
    while True:
        cand = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not cand.exists():
            return cand
        i += 1


# --------------------------------------------------------------------------
# Backup / restore engine
# --------------------------------------------------------------------------
def create_backup(emu, entries, locations, note, backup_dir, progress):
    dest_dir = Path(backup_dir) / safe_name(emu)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    zpath = unique_path(dest_dir / f"{stamp}.zip")
    total = sum(e.size for e in entries) or 1
    done = 0
    manifest = {
        "emulator": emu,
        "created": datetime.now().isoformat(timespec="seconds"),
        "note": note,
        "locations": locations,
        "files": [{"location": e.location, "rel": e.rel.as_posix(), "size": e.size}
                  for e in entries],
    }
    try:
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as z:
            for e in entries:
                z.write(e.path, f"{e.location}/{e.rel.as_posix()}")
                done += e.size
                progress(done / total)
            z.writestr(MANIFEST, json.dumps(manifest, indent=2))
    except Exception:
        _safe_unlink(zpath)
        raise
    return zpath


def _safe_unlink(p):
    try:
        p.unlink()
    except OSError:
        pass


def read_manifest(zpath):
    with zipfile.ZipFile(zpath) as z:
        return json.loads(z.read(MANIFEST).decode("utf-8"))


def plan_restore(zpath, resolved):
    """Work out where each file in a backup will be written."""
    manifest = read_manifest(zpath)
    recorded = manifest.get("locations", {})
    bases = {}
    plan = []
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            if info.is_dir() or info.filename == MANIFEST:
                continue
            label, _, rel = info.filename.partition("/")
            if not rel:
                continue
            base = resolved.get(label)
            if base is None and label in recorded:
                base = Path(recorded[label])
            if base is None:
                continue
            target = base / rel
            if not is_within(target, base):
                raise ValueError(f"Unsafe path in backup: {info.filename}")
            bases[label] = str(base)
            plan.append((info.filename, label, rel, target, info.file_size))
    return manifest, plan, bases


def execute_restore(zpath, plan, emu, bases, backup_dir, progress):
    # Safety copy of anything that will be overwritten
    existing = []
    for _name, label, rel, target, _size in plan:
        if target.is_file():
            st = target.stat()
            existing.append(Entry(label, Path(rel), target, st.st_size, st.st_mtime))
    if existing:
        create_backup(emu, existing, bases, "Automatic backup before restore",
                      backup_dir, lambda f: progress(f * 0.3))

    total = sum(p[4] for p in plan) or 1
    done = 0
    with zipfile.ZipFile(zpath) as z:
        for name, _label, _rel, target, size in plan:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".esm_tmp")
            with z.open(name) as src, open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            os.replace(tmp, target)
            done += size
            progress(0.3 + 0.7 * done / total)
    return len(plan)


# --------------------------------------------------------------------------
# Dialogs
# --------------------------------------------------------------------------
class LocationsDialog(tk.Toplevel):
    def __init__(self, app, emu):
        super().__init__(app)
        self.app, self.emu = app, emu
        self.title(f"Locations: {emu}")
        self.transient(app)
        self.resizable(True, False)
        self.vars = {}

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        ttk.Label(frm, text="Point each entry at the correct folder if auto-detection "
                            "missed it.", wraplength=600).grid(row=0, column=0, columnspan=3,
                                                               sticky="w", pady=(0, 8))
        resolved = app.resolve(emu)
        for i, loc in enumerate(app.profiles[emu]["locations"], start=1):
            label = loc["label"]
            ttk.Label(frm, text=label).grid(row=i, column=0, sticky="w", padx=(0, 8), pady=3)
            var = tk.StringVar(value=str(resolved[label] or ""))
            self.vars[label] = var
            ttk.Entry(frm, textvariable=var, width=70).grid(row=i, column=1, sticky="ew", pady=3)
            ttk.Button(frm, text="Browse…",
                       command=lambda v=var: self.browse(v)).grid(row=i, column=2, padx=(6, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=99, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Reset to auto-detect", command=self.reset).pack(side="left")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="Save", command=self.save).pack(side="right")
        self.grab_set()

    def browse(self, var):
        d = filedialog.askdirectory(parent=self, initialdir=var.get() or str(HOME))
        if d:
            var.set(d)

    def reset(self):
        for loc in self.app.profiles[self.emu]["locations"]:
            self.app.cfg["overrides"].pop(f"{self.emu}::{loc['label']}", None)
        save_config(self.app.cfg)
        self.destroy()
        self.app.refresh_all()

    def save(self):
        for loc in self.app.profiles[self.emu]["locations"]:
            label = loc["label"]
            key = f"{self.emu}::{label}"
            value = self.vars[label].get().strip()
            auto = self.app.autodetect(loc)
            if not value or (auto and Path(value) == auto):
                self.app.cfg["overrides"].pop(key, None)
            else:
                self.app.cfg["overrides"][key] = value
        save_config(self.app.cfg)
        self.destroy()
        self.app.refresh_all()


# --------------------------------------------------------------------------
# Main application
# --------------------------------------------------------------------------
class App(tk.Tk):
    def _set_icon(self):
        """Window and taskbar icon for this window and every dialog it opens."""
        try:
            import steamshortcut
            art = steamshortcut.art_dir()
            self._icons = [tk.PhotoImage(file=str(art / f"icon-{n}.png")) for n in (256, 64, 32, 16)
                           if (art / f"icon-{n}.png").is_file()]
            if self._icons:
                self.iconphoto(True, *self._icons)
        except Exception:  # noqa - an icon must never stop the app
            pass

    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1120x700")
        self.minsize(920, 580)
        self._set_icon()
        if SYSTEM == "Linux" and self.winfo_screenheight() < 900:    # Steam Deck: use the screen
            try:
                self.attributes("-zoomed", True)
            except tk.TclError:
                pass

        self.cfg = load_config()
        self.profiles = self.build_profiles()
        self.current = None
        self.entries = {}
        self.backup_paths = {}
        self.busy = False
        self.q = queue.Queue()

        self.titles = None
        self._build_ui()
        self.refresh_emulator_list()
        self.pad = None
        self._start_controller()
        self.after(100, self._poll)
        self._start_titles()
        self._start_updates()
        self.after(2500, self._ensure_watcher)
        self.after(1500, self._steam_art)

    def _start_controller(self):
        """Controller navigation in this window (and its questions). Off with "controller": false."""
        if not self.cfg.get("controller", True):
            return
        try:
            import mainpad
            self.pad = mainpad.MainPad(self)
        except Exception as ex:  # noqa - a controller problem must never stop the app
            print("Controller:", ex)
            self.pad = None
            return
        self.pad.watch_hint(self.pad_hint)

    def destroy(self):
        if getattr(self, "pad", None):
            self.pad.stop()
            self.pad = None
        super().destroy()

    def _steam_art(self):
        """Give this app's Steam shortcut (however it was added) its library artwork."""
        try:
            import steamshortcut
            written = steamshortcut.refresh_art()
        except Exception:  # noqa - artwork must never get in the way
            return
        if written:
            self.set_status("Added this app's artwork to its Steam shortcut "
                            "(it shows in the library after Steam restarts).")

    # ---- updates -----------------------------------------------------------
    def _start_updates(self):
        try:
            import ui_update
            self.updates = ui_update.UpdateController(self, lambda: save_config(self.cfg))
        except Exception as ex:  # noqa - an update check must never stop the app
            self.updates = None
            print("Update check unavailable:", ex)
        ver = getattr(self.updates, "current", None)
        self.version_label.config(text=f"Version {ver}" if ver else "Development version")

    def open_updates(self):
        if self.updates is not None:
            self.updates.open_dialog()

    def quit_for_update(self):
        """Close every window so the installer can replace the program."""
        self.destroy()

    def _ensure_watcher(self):
        try:
            import exitwatch
            exitwatch.ensure_running(self.cfg)
        except Exception:  # noqa
            pass

    # ---- profile / path handling -----------------------------------------
    def build_profiles(self):
        profiles = builtin_profiles()
        for name, prof in self.cfg.get("custom", {}).items():
            profiles[name] = copy.deepcopy(prof)
        return profiles

    @property
    def backup_dir(self):
        return Path(self.cfg["backup_dir"])

    @staticmethod
    def autodetect(loc):
        for c in loc["candidates"]:
            if Path(c).is_dir():
                return Path(c)
        return None

    def resolve(self, emu):
        res = {}
        for loc in self.profiles[emu]["locations"]:
            override = self.cfg["overrides"].get(f"{emu}::{loc['label']}")
            res[loc["label"]] = Path(override) if override else self.autodetect(loc)
        return res

    # ---- UI --------------------------------------------------------------
    def _build_ui(self):
        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill="x")
        self.updates = None
        ttk.Label(top, text="Backup folder:").pack(side="left")
        self.backup_dir_label = ttk.Label(top, text=str(self.backup_dir))
        self.backup_dir_label.pack(side="left", padx=6)
        ttk.Button(top, text="Change…", command=self.change_backup_dir).pack(side="left")
        ttk.Button(top, text="Open", command=self.open_backup_dir).pack(side="left", padx=4)
        ttk.Button(top, text="Cloud Sync…", command=self.open_cloud).pack(side="right")
        ttk.Button(top, text="Save History…", command=self.open_history).pack(side="right", padx=(6, 0))
        ttk.Button(top, text="BIOS Vault…", command=self.open_bios_vault).pack(side="right", padx=(6, 0))
        cards = ttk.Menubutton(top, text="Memory Cards", width=13)
        cards_menu = tk.Menu(cards, tearoff=False)
        cards_menu.add_command(label="PS2 Card Manager…", command=self.open_ps2_cards)
        cards_menu.add_command(label="PS1 Card Manager…", command=self.open_ps1_cards)
        cards_menu.add_command(label="GameCube Card Manager…", command=self.open_gc_cards)
        cards_menu.add_command(label="Wii Save Manager…", command=self.open_wii_saves)
        cards_menu.add_command(label="PSP Save Manager…", command=self.open_psp_saves)
        cards_menu.add_separator()
        cards_menu.add_command(label="RetroArch Save Manager…", command=self.open_ra_saves)
        cards["menu"] = cards_menu
        cards.pack(side="right", padx=6)
        self.cards_menu = cards_menu

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self.update_bar_before = panes            # the update bar goes above the panes

        # Left: emulator list
        left = ttk.Frame(panes, padding=(0, 0, 8, 0))
        panes.add(left, weight=0)
        ttk.Label(left, text="Emulators").pack(anchor="w")
        self.emu_list = tk.Listbox(left, exportselection=False, width=22, activestyle="none",
                                   font=("TkDefaultFont", 11))
        self.emu_list.pack(fill="both", expand=True, pady=4)
        self.emu_list.bind("<<ListboxSelect>>", self.on_select_emulator)
        row = ttk.Frame(left)
        row.pack(fill="x")
        ttk.Button(row, text="Add custom…", command=self.add_custom).pack(side="left")
        ttk.Button(row, text="Remove", command=self.remove_custom).pack(side="left", padx=4)

        # Right
        right = ttk.Frame(panes)
        panes.add(right, weight=1)

        big = tkfont.nametofont("TkDefaultFont").copy()
        big.configure(size=16, weight="bold")
        self.title_label = ttk.Label(right, text="", font=big)
        self.title_label.pack(anchor="w")
        self.notes_label = ttk.Label(right, text="", wraplength=760, foreground="#555")
        self.notes_label.pack(anchor="w", pady=(2, 2))
        self.loc_label = ttk.Label(right, text="", justify="left", foreground="#555")
        self.loc_label.pack(anchor="w", pady=(0, 6))

        vpanes = ttk.PanedWindow(right, orient="vertical")
        vpanes.pack(fill="both", expand=True)

        # Current saves
        saves = ttk.LabelFrame(vpanes, text="Current save data", padding=6)
        vpanes.add(saves, weight=1)
        import ui_saves
        self.save_list = ui_saves.SaveList(saves, grouped=self.cfg.get("group_saves", True))
        self.save_list.grid(row=0, column=0, columnspan=2, sticky="nsew")
        self.save_tree = self.save_list.tree
        saves.rowconfigure(0, weight=1)
        saves.columnconfigure(0, weight=1)
        self.saves_summary = ttk.Label(saves, text="")
        self.saves_summary.grid(row=1, column=0, sticky="w", pady=(4, 0))
        btns = ttk.Frame(saves)
        btns.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(btns, text="Back up selected", command=self.backup_selected).pack(side="left")
        ttk.Button(btns, text="Back up all", command=self.backup_all).pack(side="left", padx=4)
        ttk.Button(btns, text="Locations…", command=self.edit_locations).pack(side="left")
        ttk.Button(btns, text="Open folder", command=self.open_save_folder).pack(side="left", padx=4)
        ttk.Button(btns, text="Refresh", command=self.refresh_all).pack(side="left")
        ttk.Checkbutton(btns, text="Group by game", variable=self.save_list.grouped,
                        command=self._toggle_grouping).pack(side="left", padx=(12, 0))

        # Backups
        backups = ttk.LabelFrame(vpanes, text="Backups", padding=6)
        vpanes.add(backups, weight=1)
        bcols = ("created", "note", "files", "size")
        self.backup_tree = ttk.Treeview(backups, columns=bcols, show="headings", height=8,
                                        selectmode="browse")
        for c, text, w, anchor in (("created", "Created", 150, "w"), ("note", "Note", 380, "w"),
                                   ("files", "Files", 60, "e"), ("size", "Size", 100, "e")):
            self.backup_tree.heading(c, text=text)
            self.backup_tree.column(c, width=w, anchor=anchor)
        sb2 = ttk.Scrollbar(backups, orient="vertical", command=self.backup_tree.yview)
        self.backup_tree.configure(yscrollcommand=sb2.set)
        self.backup_tree.grid(row=0, column=0, sticky="nsew")
        sb2.grid(row=0, column=1, sticky="ns")
        backups.rowconfigure(0, weight=1)
        backups.columnconfigure(0, weight=1)
        bbtns = ttk.Frame(backups)
        bbtns.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(bbtns, text="Restore", command=self.restore_selected).pack(side="left")
        ttk.Button(bbtns, text="Delete", command=self.delete_selected).pack(side="left", padx=4)
        ttk.Button(bbtns, text="Export copy…", command=self.export_selected).pack(side="left")
        ttk.Button(bbtns, text="Import…", command=self.import_backup).pack(side="left", padx=4)

        # Status bar
        bottom = ttk.Frame(self, padding=(10, 0, 10, 8))
        # packed before the panes so a short window (or the update bar) never pushes it off-screen
        bottom.pack(fill="x", side="bottom", before=panes)
        self.status = ttk.Label(bottom, text="Ready")
        self.status.pack(side="left")
        self.pad_hint = ttk.Label(bottom, text="", foreground="#1f5fa8")   # shown with a controller
        self.progress = ttk.Progressbar(bottom, length=260, maximum=100)
        self.progress.pack(side="right")
        ttk.Button(bottom, text="Updates…", command=self.open_updates).pack(side="right", padx=(0, 10))
        self.version_label = ttk.Label(bottom, foreground="#666")
        self.version_label.pack(side="right", padx=(0, 6))

    # ---- emulator list ---------------------------------------------------
    def refresh_emulator_list(self, select=None):
        self.emu_list.delete(0, "end")
        names = list(self.profiles.keys())
        for n in names:
            self.emu_list.insert("end", n)
        pick = select if select in names else (self.current if self.current in names else names[0])
        idx = names.index(pick)
        self.emu_list.selection_set(idx)
        self.current = pick
        self.refresh_all()

    def on_select_emulator(self, _evt=None):
        sel = self.emu_list.curselection()
        if sel:
            self.current = self.emu_list.get(sel[0])
            self.refresh_all()

    # ---- refresh ---------------------------------------------------------
    def refresh_all(self):
        if not self.current:
            return
        emu = self.current
        prof = self.profiles[emu]
        self.title_label.config(text=emu)
        self.notes_label.config(text=prof.get("notes", ""))
        resolved = self.resolve(emu)
        lines = []
        for loc in prof["locations"]:
            p = resolved[loc["label"]]
            if p is None:
                state = "not found"
            else:
                state = str(p) if p.is_dir() else f"{p} (missing)"
            lines.append(f"{loc['label']}: {state}")
        self.loc_label.config(text="\n".join(lines))
        self.refresh_saves(resolved)
        self.refresh_backups()

    def refresh_saves(self, resolved=None):
        emu = self.current
        if emu is None:
            return
        resolved = resolved or self.resolve(emu)
        located = []
        for loc in self.profiles[emu]["locations"]:
            base = resolved[loc["label"]]
            located.append((loc["label"], base,
                            scan_location(loc["label"], base, loc.get("patterns", ["*"]),
                                          loc.get("recursive", True), loc.get("exclude_dirs", ()))))
        self.save_list.populate(emu, located, self.titles)
        self.entries = self.save_list.entries
        self.saves_summary.config(text=self.save_list.summary())

    def _toggle_grouping(self):
        self.cfg["group_saves"] = bool(self.save_list.grouped.get())
        save_config(self.cfg)
        self.refresh_saves()

    # ---- game names (titledb) ----------------------------------------------
    def _start_titles(self):
        """Load game names in the background (emulators' lists, Cemu's cache, ROM headers), then
        refresh the list so save states and title-ID folders get names."""
        import threading
        import titledb
        self.titles = titledb.TitleDB()
        installs, cemu, ps3 = set(), set(), set()
        for emu in self.profiles:
            try:
                res = self.resolve(emu)
            except Exception:  # noqa
                continue
            for p in res.values():
                if not p:
                    continue
                for parent in list(Path(p).parents)[:4]:      # portable installs keep saves inside
                    installs.add(parent)
                if emu == "Cemu":
                    for parent in list(Path(p).parents)[:4]:
                        if parent.name.lower() == "mlc01":
                            cemu.add(parent.parent)
                if emu == "RPCS3":
                    for parent in Path(p).parents:
                        if parent.name.lower() == "dev_hdd0":
                            ps3.add(parent / "game")
                            break
        self._titles_done = False

        def work():
            try:
                self.titles.load(install_extra=sorted(installs, key=str), cemu_extra=sorted(cemu, key=str),
                                 ps3_game_dirs=sorted(ps3, key=str))
            except Exception as ex:  # noqa - names are a nicety
                print("Loading game names failed:", ex)
            self._titles_done = True
        threading.Thread(target=work, daemon=True).start()
        self.after(400, self._titles_check)

    def _titles_check(self):
        if not self._titles_done:
            self.after(400, self._titles_check)
            return
        try:
            self.titles.save()
            self.refresh_saves()
        except tk.TclError:
            pass

    def list_backups(self, emu):
        folder = self.backup_dir / safe_name(emu)
        out = []
        if folder.is_dir():
            for z in folder.glob("*.zip"):
                try:
                    out.append((z, read_manifest(z), z.stat().st_size))
                except Exception:
                    continue
        out.sort(key=lambda t: t[1].get("created", ""), reverse=True)
        return out

    def refresh_backups(self):
        self.backup_tree.delete(*self.backup_tree.get_children())
        self.backup_paths = {}
        for i, (path, man, size) in enumerate(self.list_backups(self.current)):
            iid = str(i)
            self.backup_paths[iid] = path
            created = man.get("created", "")
            try:
                created = datetime.fromisoformat(created).strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
            self.backup_tree.insert("", "end", iid=iid, values=(
                created, man.get("note", ""), len(man.get("files", [])), human_size(size)))

    # ---- background tasks ------------------------------------------------
    def set_status(self, text):
        self.status.config(text=text)

    def run_task(self, label, func, on_done=None):
        if self.busy:
            messagebox.showinfo(APP_NAME, "Please wait for the current operation to finish.")
            return
        self.busy = True
        self.progress["value"] = 0
        self.set_status(label)

        def work():
            try:
                res = func(lambda f: self.q.put(("progress", f)))
                self.q.put(("done", res, None, on_done))
            except Exception as ex:  # noqa
                self.q.put(("done", None, ex, on_done))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "progress":
                    self.progress["value"] = max(0, min(1, msg[1])) * 100
                else:
                    _, res, err, cb = msg
                    self.busy = False
                    self.progress["value"] = 0
                    if err:
                        self.set_status("Failed")
                        messagebox.showerror(APP_NAME, f"Operation failed:\n\n{err}")
                    else:
                        self.set_status("Done")
                        if cb:
                            cb(res)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    # ---- backup actions --------------------------------------------------
    def backup_selected(self):
        sel = self.save_list.selected_entries()
        if not sel:
            messagebox.showinfo(APP_NAME, "Select one or more saves or files first, or use 'Back up all'.")
            return
        self._do_backup(sel)

    def backup_all(self):
        self._do_backup(self.save_list.all_entries())

    def _do_backup(self, entries):
        if not entries:
            messagebox.showinfo(APP_NAME, "There is no save data to back up.")
            return
        note = simpledialog.askstring(APP_NAME, "Optional note for this backup:", parent=self)
        if note is None:
            return
        emu = self.current
        locations = {k: str(v) for k, v in self.resolve(emu).items() if v}
        total = sum(e.size for e in entries)

        def done(p):
            self.refresh_backups()
            self.set_status(f"Backup created: {p.name} ({human_size(total)} source data)")

        self.run_task(f"Backing up {emu}…",
                      lambda prog: create_backup(emu, entries, locations, note.strip(),
                                                 self.backup_dir, prog), done)

    def _selected_backup(self):
        sel = self.backup_tree.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Select a backup first.")
            return None
        return self.backup_paths[sel[0]]

    def restore_selected(self):
        zpath = self._selected_backup()
        if not zpath:
            return
        emu = self.current
        try:
            _man, plan, bases = plan_restore(zpath, self.resolve(emu))
        except Exception as ex:  # noqa
            messagebox.showerror(APP_NAME, f"Could not read this backup:\n\n{ex}")
            return
        if not plan:
            messagebox.showinfo(APP_NAME, "This backup has no restorable files for this emulator.")
            return
        dests = "\n".join(f"  {label}: {path}" for label, path in bases.items())
        if not messagebox.askyesno(
                APP_NAME,
                f"Restore {len(plan)} file(s) to:\n{dests}\n\n"
                f"Make sure {emu} is closed. Existing files will be replaced, but a safety "
                f"backup of them is made first.\n\nContinue?"):
            return

        def done(count):
            self.refresh_all()
            self.set_status(f"Restored {count} file(s)")
            messagebox.showinfo(APP_NAME, f"Restored {count} file(s).")

        self.run_task(f"Restoring {emu}…",
                      lambda prog: execute_restore(zpath, plan, emu, bases, self.backup_dir, prog),
                      done)

    def delete_selected(self):
        zpath = self._selected_backup()
        if not zpath:
            return
        if messagebox.askyesno(APP_NAME, f"Permanently delete backup {zpath.name}?"):
            try:
                zpath.unlink()
            except OSError as ex:
                messagebox.showerror(APP_NAME, str(ex))
            self.refresh_backups()

    def export_selected(self):
        zpath = self._selected_backup()
        if not zpath:
            return
        dest = filedialog.asksaveasfilename(
            parent=self, defaultextension=".zip", initialfile=f"{safe_name(self.current)}_{zpath.name}",
            filetypes=[("Zip archive", "*.zip")])
        if dest:
            self.run_task("Exporting…", lambda prog: shutil.copy2(zpath, dest),
                          lambda _r: self.set_status(f"Exported to {dest}"))

    def import_backup(self):
        src = filedialog.askopenfilename(parent=self, filetypes=[("Zip archive", "*.zip")])
        if not src:
            return
        try:
            man = read_manifest(src)
            emu = man["emulator"]
        except Exception:
            messagebox.showerror(APP_NAME, "That file isn't a backup made by this program.")
            return
        dest_dir = self.backup_dir / safe_name(emu)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = unique_path(dest_dir / Path(src).name)

        def done(_r):
            if emu in self.profiles:
                self.refresh_emulator_list(select=emu)
            self.set_status(f"Imported backup for {emu}")

        self.run_task("Importing…", lambda prog: shutil.copy2(src, dest), done)

    # ---- misc actions ----------------------------------------------------
    def save_cfg(self):
        save_config(self.cfg)

    def open_folder(self, path):
        open_folder(path)

    def open_ps2_cards(self):
        from ui_ps2cards import PS2CardDialog
        PS2CardDialog(self)

    def open_ps1_cards(self):
        from ui_ps1cards import PS1CardDialog
        PS1CardDialog(self)

    def open_wii_saves(self):
        from ui_wiisaves import WiiSaveDialog
        WiiSaveDialog(self, self.current if self.current in ("Dolphin", "PrimeHack") else "Dolphin")

    def open_psp_saves(self):
        from ui_pspsaves import PSPSaveDialog
        PSPSaveDialog(self)

    def open_ra_saves(self):
        from ui_rasaves import RASaveDialog
        RASaveDialog(self)

    def open_gc_cards(self):
        from ui_gccards import GCCardDialog
        GCCardDialog(self, self.current if self.current in ("Dolphin", "PrimeHack") else "Dolphin")

    def open_bios_vault(self):
        """The BIOS Vault now lives in the ROM Library: open it there (same settings)."""
        proc = launch_library("--bios")
        if not proc:
            from ui_biosvault import BiosVaultDialog          # couldn't start it: open it here
            BiosVaultDialog(self)
        else:
            self.set_status("Opening the BIOS Vault in the ROM Library…")
            self._hand_over(proc)

    def _hand_over(self, proc):
        """Game Mode shows only one of this app's windows and won't switch to the Library's, so the
        Library stayed hidden behind this window. Step aside while it's open and come back after."""
        if not in_game_mode():
            return
        self.withdraw()
        started = __import__("time").monotonic()

        def check():
            if proc.poll() is None:
                self.after(500, check)
                return
            self.deiconify()
            self.lift()
            self.focus_force()
            if __import__("time").monotonic() - started < 3:
                self.set_status("The ROM Library closed straight away: see ~/.emu_rom_library/logs/library.log")
            else:
                self.set_status("Ready.")
        self.after(500, check)

    def open_cloud(self):
        from ui_cloud import CloudSyncDialog
        return CloudSyncDialog(self)

    def open_library(self):
        proc = launch_library()
        if proc:
            self.set_status("Opening the ROM Library…")
            self._hand_over(proc)

    def open_history(self):
        from ui_history import SaveHistoryDialog
        SaveHistoryDialog(self, self.current)

    def open_empty_saves(self):
        from ui_emptysaves import EmptySavesDialog
        return EmptySavesDialog(self)

    def change_backup_dir(self):
        d = filedialog.askdirectory(parent=self, initialdir=str(self.backup_dir))
        if d:
            self.cfg["backup_dir"] = d
            save_config(self.cfg)
            self.backup_dir_label.config(text=d)
            self.refresh_backups()

    def open_backup_dir(self):
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        open_folder(self.backup_dir)

    def edit_locations(self):
        LocationsDialog(self, self.current)

    def open_save_folder(self):
        for p in self.resolve(self.current).values():
            if p and p.is_dir():
                open_folder(p)
                return
        messagebox.showinfo(APP_NAME, "No save folder found. Use 'Locations…' to set one.")

    def add_custom(self):
        name = simpledialog.askstring(APP_NAME, "Emulator name:", parent=self)
        if not name or not name.strip():
            return
        name = name.strip()
        if name in self.profiles:
            messagebox.showerror(APP_NAME, "An emulator with that name already exists.")
            return
        folder = filedialog.askdirectory(parent=self, title="Choose the save-data folder")
        if not folder:
            return
        pats = simpledialog.askstring(
            APP_NAME, "File patterns to include (comma separated):", initialvalue="*", parent=self)
        patterns = [p.strip() for p in (pats or "*").split(",") if p.strip()] or ["*"]
        self.cfg["custom"][name] = {
            "notes": "Custom emulator.",
            "locations": [{"label": "Saves", "candidates": [folder],
                           "patterns": patterns, "recursive": True}],
        }
        save_config(self.cfg)
        self.profiles = self.build_profiles()
        self.refresh_emulator_list(select=name)

    def remove_custom(self):
        if self.current not in self.cfg.get("custom", {}):
            messagebox.showinfo(APP_NAME, "Only custom emulators can be removed.")
            return
        if messagebox.askyesno(APP_NAME, f"Remove '{self.current}' from the list?\n"
                                         f"Its existing backups are kept on disk."):
            del self.cfg["custom"][self.current]
            save_config(self.cfg)
            self.profiles = self.build_profiles()
            self.current = None
            self.refresh_emulator_list()


def _fix_frozen_env():
    """PyInstaller on Linux points LD_LIBRARY_PATH at its bundle, which breaks the
    system programs we launch (xdg-open, terminals, rclone). Restore the original."""
    import sys
    if getattr(sys, "frozen", False) and SYSTEM == "Linux":
        orig = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
        if orig:
            os.environ["LD_LIBRARY_PATH"] = orig
        else:
            os.environ.pop("LD_LIBRARY_PATH", None)


if __name__ == "__main__":
    import sys
    _fix_frozen_env()
    if "--sync" in sys.argv:            # background two-way cloud sync, no window
        import autosync
        sys.exit(autosync.main_cli(sys.argv))
    if "--watch" in sys.argv:           # sync after an emulator / ES-DE closes, no window
        import exitwatch
        sys.exit(exitwatch.watch())
    if "--add-to-steam" in sys.argv or "--remove-from-steam" in sys.argv:   # Steam Deck Game Mode
        import steamshortcut
        sys.exit(steamshortcut.cli("add" if "--add-to-steam" in sys.argv else "remove",
                                   assume_yes="--yes" in sys.argv))
    if "--rom-library" in sys.argv:     # the ROM Library app (same program, its own window and shortcuts)
        import ui_library
        ui_library.main()
        sys.exit(0)
    if "--bios-scan" in sys.argv:       # identify BIOS files in a folder, change nothing
        import biosvault
        folder = sys.argv[sys.argv.index("--bios-scan") + 1:] or ["."]
        for e in biosvault.scan_any(folder[0]):
            print(f"{e[0]:<38} {e[1]:<11} {e[2]:<52} {e[3]}")
        sys.exit(0)
    App().mainloop()
