"""ROM Library core: a central folder of ROMs (a network share, a mounted drive or any rclone remote)
that machines check games out of, into their local EmuDeck ROM folder, and check back in.

Layout, the same as EmuDeck's:   <library>/<system>/<game files or game folders>
Shared data lives on the library itself, in <library>/.library/:
    library.json            favourites and other curation, shared by every machine
    machines/<machine>.json what each machine has checked out (each machine writes only its own file)

Nothing in the library is renamed, moved or deleted by this module; it only writes inside .library/
and, when asked to, adds games to the library.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path

import cloudsync

IS_WIN = platform.system() == "Windows"
APP_DIR = Path.home() / ".emu_rom_library"
CONFIG_FILE = APP_DIR / "config.json"
INDEX_CACHE = APP_DIR / "index_cache.json"
LOCAL_MANIFEST = APP_DIR / "checked_out.json"
OWN_RCLONE_CONF = APP_DIR / "rclone.conf"
META_DIR = ".library"
SHARE_REMOTE = "romlibrary"               # the rclone remote made by "Network share" setup

# ---------------------------------------------------------------------------
# Systems: EmuDeck / ES-DE folder names, display names, and other names people use
# ---------------------------------------------------------------------------
SYSTEMS = {
    "3do": "3DO", "amiga": "Amiga", "arcade": "Arcade", "atari2600": "Atari 2600",
    "atari5200": "Atari 5200", "atari7800": "Atari 7800", "atarijaguar": "Atari Jaguar",
    "atarijaguarcd": "Atari Jaguar CD", "atarilynx": "Atari Lynx", "c64": "Commodore 64",
    "dreamcast": "Dreamcast", "fbneo": "FinalBurn Neo", "gamecom": "Game.com", "gamegear": "Game Gear",
    "gb": "Game Boy", "gba": "Game Boy Advance", "gbc": "Game Boy Color", "gc": "GameCube",
    "genesis": "Genesis / Mega Drive", "mame": "MAME", "mastersystem": "Master System",
    "megacd": "Mega-CD", "n3ds": "Nintendo 3DS", "n64": "Nintendo 64", "nds": "Nintendo DS",
    "neogeo": "Neo Geo", "neogeocd": "Neo Geo CD", "nes": "NES", "ngp": "Neo Geo Pocket",
    "ngpc": "Neo Geo Pocket Color", "pcengine": "PC Engine / TurboGrafx-16", "pcfx": "PC-FX",
    "primehack": "PrimeHack", "ps2": "PlayStation 2", "ps3": "PlayStation 3", "ps4": "PlayStation 4",
    "psp": "PSP", "psvita": "PS Vita", "psx": "PlayStation", "saturn": "Saturn", "sega32x": "32X",
    "segacd": "Sega CD", "sg-1000": "SG-1000", "snes": "SNES", "switch": "Switch",
    "virtualboy": "Virtual Boy", "wii": "Wii", "wiiu": "Wii U", "wonderswan": "WonderSwan",
    "wonderswancolor": "WonderSwan Color", "xbox": "Xbox", "xbox360": "Xbox 360",
    "scummvm": "ScummVM", "dos": "DOS", "model2": "Sega Model 2", "model3": "Sega Model 3",
    "naomi": "NAOMI", "atomiswave": "Atomiswave", "pico8": "PICO-8", "msx": "MSX",
}
ALIASES = {
    "jaguar cd": "atarijaguarcd", "jaguarcd": "atarijaguarcd", "jaguar": "atarijaguar",
    "neo geo": "neogeo", "neo geo cd": "neogeocd", "gamecube": "gc", "ngc": "gc",
    "ps1": "psx", "playstation": "psx", "playstation 1": "psx", "playstation 2": "ps2",
    "playstation 3": "ps3", "x360": "xbox360", "xbox 360": "xbox360", "megadrive": "genesis",
    "mega drive": "genesis", "sfc": "snes", "super nintendo": "snes", "famicom": "nes",
    "nintendo 64": "n64", "nintendo ds": "nds", "3ds": "n3ds", "game boy": "gb",
    "game boy advance": "gba", "game boy color": "gbc", "tg16": "pcengine", "vita": "psvita",
    "wii u": "wiiu", "32x": "sega32x", "virtual boy": "virtualboy", "game gear": "gamegear",
    # short names people use for folders
    "dc": "dreamcast", "md": "genesis", "gen": "genesis", "sms": "mastersystem", "master system": "mastersystem",
    "gg": "gamegear", "pce": "pcengine", "turbografx": "pcengine", "turbografx16": "pcengine",
    "sat": "saturn", "scd": "segacd", "mega cd": "megacd", "gcn": "gc", "ds": "nds", "psone": "psx",
    "ps one": "psx", "xbox one": "xboxone", "lynx": "atarilynx", "2600": "atari2600", "5200": "atari5200",
    "7800": "atari7800", "ngcd": "neogeocd", "neogeo pocket": "ngp", "neogeo pocket color": "ngpc",
    "ws": "wonderswan", "wsc": "wonderswancolor", "vb": "virtualboy", "gameboy": "gb",
    "gameboy advance": "gba", "gameboy color": "gbc", "game boy colour": "gbc", "wiiware": "wii",
}
_SQUASH = re.compile(r"[\s_\-\.]+")
_SQUASHED_ALIASES = {}
# "Sega Dreamcast", "Nintendo GameCube", "Sony PlayStation 2": the maker's name in front is dropped
MAKERS = ("sega", "nintendo", "sony", "microsoft", "nec", "snk", "bandai", "commodore")


def canonical(folder: str) -> str:
    """The EmuDeck folder name for a system folder name ('neo geo' -> 'neogeo')."""
    f = folder.strip().lower()
    if f in SYSTEMS:
        return f
    if f in ALIASES:
        return ALIASES[f]
    if not _SQUASHED_ALIASES:
        _SQUASHED_ALIASES.update({_SQUASH.sub("", k): v for k, v in ALIASES.items()})
    squashed = _SQUASH.sub("", f)
    if squashed in SYSTEMS:
        return squashed
    if squashed in _SQUASHED_ALIASES:
        return _SQUASHED_ALIASES[squashed]
    for maker in MAKERS:
        if f.startswith(maker + " ") or f.startswith(maker + "_") or f.startswith(maker + "-"):
            rest = canonical(f[len(maker) + 1:])
            if rest in SYSTEMS:
                return rest
    return f


def system_name(folder: str) -> str:
    c = canonical(folder)
    return SYSTEMS.get(c, folder)


# Files and folders in a system folder that are not games
IGNORE_FILES = {"metadata.txt", "systeminfo.txt", "desktop.ini", "thumbs.db", ".ds_store", "gamelist.xml",
                "readme.txt"}
IGNORE_DIRS = {"media", "images", "videos", "manuals", "snap", "snaps", "downloaded_media", "covers",
               "boxart", "$recycle.bin", "system volume information"}
# Files that list other files of the same game (the others are named after them)
PRIMARY_EXT = {".m3u", ".cue", ".gdi", ".ccd", ".mds", ".toc"}
# Things inside a folder that make the folder one game (PS3, Wii U, Xbox 360, PC-style games ...)
GAME_DIR_MARKERS = {"ps3_game", "usrdir", "param.sfo", "ps3_disc.sfb", "code", "content", "meta",
                    "default.xex", "default.xbe", "sys", "files", "exefs", "romfs"}
MAX_CATEGORY_DEPTH = 3


def human(n: float) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def clean_title(name: str) -> str:
    """'Okami (USA).iso' -> 'Okami (USA)'.  Region and disc tags stay: they tell copies apart."""
    stem = name
    for _ in range(2):                       # .nkit.iso, .tar.gz ...
        s, ext = os.path.splitext(stem)
        if ext and len(ext) <= 6 and " " not in ext:
            stem = s
    return stem.replace("_", " ").strip() or name


# ---------------------------------------------------------------------------
# Grouping a listing into games
# ---------------------------------------------------------------------------
def rekey(key: str) -> str:
    """A game key with its system part made canonical ('dc/Game.chd' -> 'dreamcast/Game.chd').
    Keys saved before a folder name was known as an alias still find their game."""
    system, sep, rest = key.partition("/")
    return canonical(system) + sep + rest if sep else key


def rekey_dict(d: dict) -> dict:
    """{key: value} with canonical keys; if two old keys land on one, the canonical one wins."""
    if not isinstance(d, dict):
        return {}
    out = {}
    for k, v in sorted(d.items(), key=lambda kv: rekey(kv[0]) != kv[0]):
        out.setdefault(rekey(k), v)
    return out


class Item:
    """One game: a file with its companion files, or a folder."""
    __slots__ = ("system", "sysdir", "key", "title", "files", "size", "mtime", "is_dir", "path")

    def __init__(self, sysdir, path, title, files, is_dir):
        self.sysdir = sysdir                  # folder name as it is in this listing
        self.system = canonical(sysdir)
        self.path = path                      # item path relative to the system folder
        self.key = f"{self.system}/{path}"    # same key in the library and on every machine
        self.title = title
        self.files = files                    # {path relative to system folder: (size, mtime)}
        self.size = sum(s for s, _ in files.values())
        self.mtime = max((m for _, m in files.values()), default=0)
        self.is_dir = is_dir

    def to_json(self):
        return {"sysdir": self.sysdir, "path": self.path, "title": self.title, "is_dir": self.is_dir,
                "files": {k: list(v) for k, v in self.files.items()}}

    @classmethod
    def from_json(cls, d):
        return cls(d["sysdir"], d["path"], d["title"], {k: tuple(v) for k, v in d["files"].items()},
                   d["is_dir"])


def _owned_by(primary_stem: str, name: str) -> bool:
    """'Game (Disc 1) (Track 2).bin' belongs to 'Game (Disc 1).cue' and to 'Game.m3u';
    'Game 2.iso' does not belong to 'Game.m3u'."""
    stem = os.path.splitext(name)[0]
    if stem.lower() == primary_stem.lower():
        return True
    if not name.lower().startswith(primary_stem.lower()):
        return False
    rest = name[len(primary_stem):]
    return rest[:1] in (".",) or rest[:2] in (" (", " [")


def _group_files(names):
    """Group file names in one folder: [(lead name, [names])]."""
    names = sorted(names, key=str.lower)
    primaries = sorted((n for n in names if os.path.splitext(n)[1].lower() in PRIMARY_EXT),
                       key=lambda n: (len(os.path.splitext(n)[0]), n.lower()))
    claimed, groups = set(), []
    for p in primaries:                       # shortest name first: Game.m3u claims its discs' .cue files
        if p in claimed:
            continue
        stem = os.path.splitext(p)[0]
        members = [n for n in names if n not in claimed and _owned_by(stem, n)]
        if p not in members:
            members.append(p)
        claimed.update(members)
        groups.append((p, members))
    for n in names:
        if n not in claimed:
            groups.append((n, [n]))
    return groups


HEX8 = re.compile(r"^[0-9A-Fa-f]{8}$")
GAME_EXE_EXT = (".xex", ".xbe")


def _x360_package_dir(name: str, node: dict) -> bool:
    """An Xbox 360 title-ID folder (8 hex digits) holding content-type folders, also 8 hex digits:
    000D0000 (Xbox Live Arcade), 00007000 (Games on Demand), 00000002 (DLC), 000B0000 (update).
    The package files inside have no extension, so without this each one looked like a game."""
    return bool(HEX8.match(name) and node["dirs"] and all(HEX8.match(d) for d in node["dirs"]))


def _is_game_dir(name: str, node: dict) -> bool:
    if name.lower().endswith((".ps3", ".wiiu", ".xbox360", ".xbox", ".pc", ".dosz")):
        return True
    kids = {k.lower() for k in node["dirs"]} | {k.lower() for k in node["files"]}
    if kids & GAME_DIR_MARKERS:
        return True
    if _x360_package_dir(name, node):
        return True                           # 58410A1B/000D0000/<package>
    if sum(1 for d, sub in node["dirs"].items() if _x360_package_dir(d, sub)) == 1:
        return True                           # Game Name/58410A1B/000D0000/<package>
    if any(f.lower().endswith(GAME_EXE_EXT) for f in node["files"]):
        return True                           # an extracted game: one entry, not one per file
    if not node["dirs"] and len(_group_files(list(node["files"]))) <= 1:
        return True                           # a folder holding one game (with its companion files)
    return False


def _tree(entries):
    """entries: [(path with '/', size, mtime, is_dir)] -> nested {'files': {}, 'dirs': {}}"""
    root = {"files": {}, "dirs": {}}
    for path, size, mtime, is_dir in entries:
        parts = [p for p in path.split("/") if p]
        if not parts:
            continue
        node = root
        for p in parts[:-1]:
            node = node["dirs"].setdefault(p, {"files": {}, "dirs": {}})
        if is_dir:
            node["dirs"].setdefault(parts[-1], {"files": {}, "dirs": {}})
        else:
            node["files"][parts[-1]] = (int(size or 0), float(mtime or 0))
    return root


def _all_files(node, prefix):
    out = {}
    for n, v in node["files"].items():
        out[prefix + n] = v
    for d, sub in node["dirs"].items():
        out.update(_all_files(sub, prefix + d + "/"))
    return out


def _items_in(sysdir, node, prefix="", depth=0):
    items = []
    bios = set()
    if depth == 0:                            # a BIOS set MAME wants beside the games isn't a game
        import gamebios
        bios = gamebios.folder_files(canonical(sysdir))
    files = {n: v for n, v in node["files"].items()
             if n.lower() not in IGNORE_FILES and n.lower() not in bios
             and not n.startswith(".") and not n.endswith(".partial")}
    for lead, members in _group_files(list(files)):
        items.append(Item(sysdir, prefix + lead, clean_title(lead),
                          {prefix + m: files[m] for m in members}, False))
    for d, sub in sorted(node["dirs"].items(), key=lambda kv: kv[0].lower()):
        if d.lower() in IGNORE_DIRS or d.startswith("."):
            continue
        if _is_game_dir(d, sub) or depth >= MAX_CATEGORY_DEPTH:
            fl = _all_files(sub, prefix + d + "/")
            if fl:
                items.append(Item(sysdir, prefix + d, clean_title(d), fl, True))
        else:                                 # a category folder (e.g. 'USA', 'Hacks'): look inside
            items += _items_in(sysdir, sub, prefix + d + "/", depth + 1)
    return items


def build_items(entries):
    """A full listing of a ROM root -> {system folder name: [Item]}."""
    root = _tree(entries)
    out = {}
    for sysdir, node in root["dirs"].items():
        if sysdir.startswith(".") or sysdir.lower() in IGNORE_DIRS:
            continue
        items = _items_in(sysdir, node)
        if items or canonical(sysdir) in SYSTEMS:
            out[sysdir] = items
    return out


# ---------------------------------------------------------------------------
# Listing a library (rclone) and a local ROM folder (os)
# ---------------------------------------------------------------------------
def is_remote(location: str) -> bool:
    """'romlibrary:Games/roms' is an rclone remote; 'C:\\x', '\\\\nuc\\share' and '/mnt/x' are folders."""
    if not location:
        return False
    if location.startswith(("\\\\", "/", "~")) or re.match(r"^[A-Za-z]:[\\/]", location) \
            or re.match(r"^[A-Za-z]:$", location):
        return False
    return ":" in location


def join(location: str, *parts: str) -> str:
    loc = location.rstrip("/\\")
    for p in parts:
        p = p.strip("/\\")
        if not p:
            continue
        if is_remote(loc):
            loc = loc + ("" if loc.endswith(":") else "/") + p
        else:
            loc = str(Path(loc) / p)
    return loc


def list_library(runner, location: str):
    """Every file in the library -> [(path, size, mtime, is_dir)].  Raises RuntimeError on failure."""
    for wait in (5, 15, None):                # the share was briefly full: wait and try again
        rc, out = runner.capture(["lsjson", "-R", "--files-only", "--no-mimetype", "--fast-list",
                                  location], timeout=3600, merge=False)
        if rc == 0 or TOO_MANY not in (out or "") or wait is None:
            break
        time.sleep(wait)
    if rc != 0:
        raise RuntimeError(_last_lines(out) or f"rclone exited with {rc}")
    try:
        data = json.loads(out[out.index("["):])
    except (ValueError, json.JSONDecodeError):
        raise RuntimeError("rclone returned something unexpected: " + _last_lines(out))
    entries = []
    for e in data:
        path = e.get("Path", "")
        if path.startswith(META_DIR + "/") or path == META_DIR:
            continue
        entries.append((path, e.get("Size", 0), _mtime(e.get("ModTime", "")), False))
    return entries


def _mtime(s: str) -> float:
    if not s:
        return 0.0
    try:
        from datetime import datetime
        s = re.sub(r"(\.\d{6})\d+", r"\1", s.replace("Z", "+00:00"))
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return 0.0


TOO_MANY = "maximum number of connections"


def _is_listing(line: str) -> bool:
    """A line of rclone's JSON listing (it lands in the error text when a listing fails half-way)."""
    t = line.strip()
    return t.startswith(("{", "[", "]", ",{")) or t in (",", "")


def _last_lines(text: str, n=3) -> str:
    lines = []
    for l in (text or "").strip().splitlines():
        if _is_listing(l):
            # a JSON object can run straight into rclone's log line: keep what follows the last '}'
            if "}" in l and l.rstrip()[-1:] not in ("}", ",", "]"):
                l = l[l.rindex("}") + 1:]
            else:
                continue
        if l.strip():
            lines.append(l.strip())
    msg = " / ".join(lines[-n:])
    if TOO_MANY in msg:
        msg = explain_too_many()
    return msg


def explain_too_many() -> str:
    return ("The computer sharing the library refused another connection (\"maximum number of "
            "connections\").\n\nOn that computer, check the share's user limit: right-click the shared "
            "folder > Properties > Sharing > Advanced Sharing > \"Limit the number of simultaneous users "
            "to\". Set it to the highest number allowed (20 on Windows 10/11), or in PowerShell as "
            "administrator:  Set-SmbShare -Name <share> -ConcurrentUserLimit 0 -Force")


def list_local(root: Path):
    """Every file under a local ROM folder -> [(path, size, mtime, is_dir)].
    Linked folders are followed (e.g. Emulation/roms/ps2 -> a folder on an SD card), each real
    folder once, so a link that points back up can't loop."""
    entries = []
    root = Path(root)
    if not root.is_dir():
        return entries
    seen = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        try:
            real = os.path.realpath(dirpath)
        except OSError:
            real = dirpath
        key = os.path.normcase(real)
        if key in seen:                       # reached again through another link: skip
            dirnames[:] = []
            continue
        seen.add(key)
        rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
        rel = "" if rel == "." else rel + "/"
        dirnames[:] = [d for d in dirnames if not (rel == "" and d == META_DIR)]
        for f in filenames:
            try:
                st = os.stat(os.path.join(dirpath, f))
            except OSError:
                continue
            entries.append((rel + f, st.st_size, st.st_mtime, False))
    return entries


def missing_links(root: Path) -> dict:
    """System folders in a local ROM folder that are links to somewhere that isn't there right now
    (an SD card that isn't inserted): {folder name: where it points}."""
    out = {}
    root = Path(root)
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for n in names:
        p = root / n
        try:
            if p.is_symlink() and not p.exists():
                out[n] = os.readlink(p)
        except OSError:
            continue
    return out


def drive_of(path) -> tuple:
    """(device id, mount point) of the drive a path is on, following links; (None, '') if missing."""
    try:
        real = Path(os.path.realpath(path))
        while not real.exists() and real.parent != real:
            real = real.parent
        dev = real.stat().st_dev
        mount = real
        while mount.parent != mount and os.path.ismount(str(mount)) is False:
            mount = mount.parent
        return dev, str(mount)
    except OSError:
        return None, ""


def drive_label(mount: str, dev=None) -> str:
    """'internal' for the drive the home folder is on, else the card's (mount point's) name."""
    if dev is None:
        dev = drive_of(mount)[0]
    if dev is not None and dev == drive_of(Path.home())[0]:
        return "internal"
    return Path(mount).name or mount


def default_local_roms() -> str:
    for r in cloudsync.emudeck_roots():
        if (r / "roms").is_dir():
            return str(r / "roms")
    return ""


def disk_space(path: str):
    """(total, used, free) bytes of the drive holding path, or None."""
    p = Path(path) if path else None
    while p and not p.exists():
        p = p.parent if p.parent != p else None
    if not p:
        return None
    try:
        return tuple(shutil.disk_usage(p))
    except OSError:
        return None


def same_or_nested(a: str, b: str) -> bool:
    """True if two folders are the same, or one is inside the other (never allowed for library vs local)."""
    if not a or not b or is_remote(a) or is_remote(b):
        return False
    try:
        pa, pb = Path(a).resolve(), Path(b).resolve()
    except OSError:
        return False
    return pa == pb or pa in pb.parents or pb in pa.parents


# ---------------------------------------------------------------------------
# This machine
# ---------------------------------------------------------------------------
def machine_id() -> str:
    try:
        name = socket.gethostname() or "machine"
    except OSError:
        name = "machine"
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:40]


def is_steam_deck() -> bool:
    if IS_WIN:
        return False
    try:
        board = Path("/sys/devices/virtual/dmi/id/board_name").read_text().strip()
        if board in ("Jupiter", "Galileo"):
            return True
    except OSError:
        pass
    return Path("/home/deck").is_dir() and Path.home() == Path("/home/deck")


def default_machine_label() -> str:
    return "Steam Deck" if is_steam_deck() else machine_id()


# ---------------------------------------------------------------------------
# Config and small JSON files
# ---------------------------------------------------------------------------
def _read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def load_config() -> dict:
    cfg = _read_json(CONFIG_FILE, {})
    cfg.setdefault("library", "")
    cfg.setdefault("local_roms", "")
    cfg.setdefault("rclone", "")
    cfg.setdefault("machine_label", "")
    cfg.setdefault("share", {})               # host, share, folder, user (never the password)
    return cfg


def save_config(cfg: dict):
    _write_json(CONFIG_FILE, cfg)


# Windows 10/11 accept only 20 incoming file-sharing connections in total, and every rclone
# checker, transfer and multi-thread stream can be its own connection.  Keep the library's share
# use small, so a Deck and a PC (plus any interrupted runs Windows hasn't dropped yet) fit.
LIBRARY_FLAGS = ["--checkers", "2", "--transfers", "2", "--multi-thread-streams", "1"]
SHARE_IDLE_TIMEOUT = "15s"         # rclone's SMB default keeps idle connections for a minute


# At most this many rclone commands talk to the library at once (per app). Each one opens its own
# connections to the share (up to --transfers + --checkers), and Windows 10/11 accept only 20 in
# all, from every machine. Before 1.7.2 the cover pictures, the artwork upload, a checkout and the
# library listing could all run together, and the pictures' own --transfers 4 overrode the limit.
SHARE_COMMANDS = 3
SHARE_GATE = threading.BoundedSemaphore(SHARE_COMMANDS)


def limit_runner(runner):
    """Apply the library's connection limits to an RcloneRunner (idempotent)."""
    extra = list(getattr(runner, "extra", []) or [])
    if "--checkers" not in extra:
        runner.extra = extra + LIBRARY_FLAGS
    runner.gate = SHARE_GATE
    return runner


def ensure_share_options(path=None) -> bool:
    """Add idle_timeout to an existing [romlibrary] SMB remote that predates it.  Returns True if
    the file changed.  Only that one setting is added; nothing else in the file is touched."""
    path = Path(path or OWN_RCLONE_CONF)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    cur, start, end, is_smb, has = None, None, None, False, False
    for i, line in enumerate(lines):
        t = line.strip()
        if t.startswith("[") and t.endswith("]"):
            if cur == SHARE_REMOTE and end is None:
                end = i
            cur = t[1:-1]
            if cur == SHARE_REMOTE:
                start = i
        elif cur == SHARE_REMOTE and "=" in t:
            k, v = (x.strip() for x in t.split("=", 1))
            is_smb |= k == "type" and v == "smb"
            has |= k == "idle_timeout"
    if start is None or not is_smb or has:
        return False
    if end is None:
        end = len(lines)
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    lines.insert(end, f"idle_timeout = {SHARE_IDLE_TIMEOUT}")
    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def rclone_conf() -> str:
    return str(OWN_RCLONE_CONF)


def setup_share(runner, host: str, share: str, folder: str, user: str, password: str, domain: str = ""):
    """Write the [romlibrary] SMB remote into this app's own rclone.conf; returns the library location.
    The password goes to rclone through stdin to be obscured, and is never logged or kept in config.json."""
    import subprocess
    kw = {"creationflags": 0x08000000} if IS_WIN else {}
    obscured = ""
    if not password:                          # blank keeps the password saved earlier
        obscured = _conf_value(OWN_RCLONE_CONF, SHARE_REMOTE, "pass")
    if password:
        r = subprocess.run([runner.exe, "obscure", "-"], input=password, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, timeout=30, **kw)
        if r.returncode != 0 or not r.stdout.strip():
            raise RuntimeError("rclone could not store the password: " + _last_lines(r.stderr))
        obscured = r.stdout.strip()
    lines = [f"[{SHARE_REMOTE}]", "type = smb", f"host = {host.strip()}"]
    if user:
        lines.append(f"user = {user.strip()}")
    if obscured:
        lines.append(f"pass = {obscured}")
    if domain:
        lines.append(f"domain = {domain.strip()}")
    lines.append(f"idle_timeout = {SHARE_IDLE_TIMEOUT}")
    _replace_conf_section(OWN_RCLONE_CONF, SHARE_REMOTE, "\n".join(lines) + "\n")
    return f"{SHARE_REMOTE}:" + "/".join(p.strip("/\\") for p in (share, folder) if p.strip("/\\"))


def _conf_value(path: Path, section: str, key: str) -> str:
    cur = None
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        t = line.strip()
        if t.startswith("[") and t.endswith("]"):
            cur = t[1:-1]
        elif cur == section and "=" in t and t.split("=", 1)[0].strip() == key:
            return t.split("=", 1)[1].strip()
    return ""


def share_has_password() -> bool:
    return bool(_conf_value(OWN_RCLONE_CONF, SHARE_REMOTE, "pass"))


def _replace_conf_section(path: Path, name: str, section: str):
    path = Path(path)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    out, skip = [], False
    for line in text.splitlines():
        if line.strip().startswith("[") and line.strip().endswith("]"):
            skip = line.strip()[1:-1] == name
        if not skip:
            out.append(line)
    body = "\n".join(out).strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((body + "\n\n" if body else "") + section, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# The library: index, shared curation, per-machine checkouts
# ---------------------------------------------------------------------------
class Library:
    def __init__(self, runner, location: str, local_roms: str, machine: str = "", label: str = ""):
        self.runner = limit_runner(runner)
        if getattr(runner, "conf", "") and Path(runner.conf) == OWN_RCLONE_CONF:
            ensure_share_options(OWN_RCLONE_CONF)
        self.location = location
        self.local_roms = local_roms
        self.machine = machine or machine_id()
        self.label = label or default_machine_label()
        self.items = {}            # key -> Item (library)
        self.shadowed = []         # Items hidden because another system folder has the same game
        self.local = {}            # key -> Item (this machine's ROM folder)
        self.lib_sysdirs = {}      # canonical system -> folder name in the library
        self.local_sysdirs = {}    # canonical system -> folder name on this machine
        self.offline = {}          # canonical system -> (folder name, link target): card not inserted
        self.shared = {"items": {}}
        self.machines = {}         # machine id -> its checkouts file
        self.indexed_at = 0.0
        self.error = ""
        self.lock = threading.RLock()   # the queue thread updates items/local while the window reads them

    # -- index ---------------------------------------------------------------
    def load_cache(self) -> bool:
        c = _read_json(INDEX_CACHE, {})
        if c.get("location") != self.location or c.get("version") != 1:
            return False
        items, shadowed = {}, []
        for d in c.get("items", []):
            it = Item.from_json(d)                # keys are worked out again (folder aliases)
            (shadowed.append(it) if it.key in items else items.__setitem__(it.key, it))
        self.items, self.shadowed = items, shadowed
        self.lib_sysdirs = {}
        for sysdir in c.get("sysdirs", {}).values():
            self.lib_sysdirs.setdefault(canonical(sysdir), sysdir)
        self.indexed_at = c.get("at", 0)
        return True

    def refresh(self):
        """List the library (can take minutes for a big one), then this machine and the shared files."""
        entries = list_library(self.runner, self.location)
        by_sys = build_items(entries)
        items, sysdirs, shadowed = {}, {}, []
        # two folders for one system ('Dreamcast' and 'DC') are one system; the canonical folder name
        # comes first, so it's where 'Add to library' puts games
        for sysdir, lst in sorted(by_sys.items(), key=lambda kv: (kv[0].lower() != canonical(kv[0]), kv[0])):
            sysdirs.setdefault(canonical(sysdir), sysdir)
            for it in lst:
                if it.key in items:
                    shadowed.append(it)           # the same game in both folders: shown once
                else:
                    items[it.key] = it
        with self.lock:
            self.items, self.lib_sysdirs, self.shadowed = items, sysdirs, shadowed
        self.indexed_at = time.time()
        _write_json(INDEX_CACHE, {"version": 1, "location": self.location, "at": self.indexed_at,
                                  "sysdirs": sysdirs,
                                  "items": [dict(it.to_json(), key=it.key)
                                            for it in list(items.values()) + shadowed]})
        self.refresh_local()
        self.load_shared()

    def refresh_local(self):
        local, sysdirs, offline = {}, {}, {}
        if self.local_roms:
            for sysdir, lst in build_items(list_local(Path(self.local_roms))).items():
                sysdirs.setdefault(canonical(sysdir), sysdir)
                for it in lst:
                    local[it.key] = it
            for sysdir, target in missing_links(Path(self.local_roms)).items():
                offline[canonical(sysdir)] = (sysdir, target)
                sysdirs.setdefault(canonical(sysdir), sysdir)
        with self.lock:
            self.local, self.local_sysdirs, self.offline = local, sysdirs, offline

    # -- drives (a system folder can be a link to an SD card) ---------------------
    def system_path(self, system: str) -> Path:
        return Path(self.local_roms) / self.local_dir_for(system)

    def system_space(self, system: str):
        """(total, used, free) of the drive this system's games go to; None if its card is out."""
        if system in self.offline or not self.local_roms:
            return None
        return disk_space(os.path.realpath(self.system_path(system)))

    def drives(self) -> list:
        """[(label, total, used, free, [systems])] for each drive this machine's ROMs are on:
        the ROM folder's own drive first, then any card a system folder links to."""
        if not self.local_roms:
            return []
        out = {}
        root_dev, root_mount = drive_of(self.local_roms)
        if root_dev is not None:
            out[root_dev] = [root_mount, []]
        for system, sysdir in sorted(self.local_sysdirs.items()):
            if system in self.offline:
                continue
            dev, mount = drive_of(Path(self.local_roms) / sysdir)
            if dev is None:
                continue
            out.setdefault(dev, [mount, []])[1].append(system)
        res = []
        for dev, (mount, systems) in out.items():
            sp = disk_space(mount)
            if sp:
                res.append((drive_label(mount, dev), sp[0], sp[1], sp[2], systems))
        return res

    # -- shared files on the library ------------------------------------------
    def _meta(self, *parts):
        return join(self.location, META_DIR, *parts)

    def _cat_json(self, location, default):
        rc, out = self.runner.capture(["cat", location], merge=False)
        if rc != 0:
            return default
        try:
            return json.loads(out)
        except ValueError:
            return default

    def _put_json(self, location, data) -> bool:
        fd, tmp = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
        try:
            rc, out = self.runner.capture(["copyto", tmp, location])
        finally:
            os.unlink(tmp)
        if rc != 0:
            self.error = _last_lines(out)
        return rc == 0

    def load_shared(self):
        self.shared = self._cat_json(self._meta("library.json"), {"items": {}})
        self.shared["items"] = rekey_dict(self.shared.get("items", {}))
        self.machines = {}
        rc, out = self.runner.capture(["lsjson", "--files-only", "--no-mimetype", self._meta("machines")],
                                      merge=False)
        if rc == 0:
            try:
                names = [e["Name"] for e in json.loads(out[out.index("["):]) if e["Name"].endswith(".json")]
            except (ValueError, KeyError):
                names = []
            for n in names:
                d = self._cat_json(self._meta("machines", n), None)
                if isinstance(d, dict):
                    d["items"] = rekey_dict(d.get("items", {}))
                    self.machines[n[:-5]] = d
        self._publish_mine(only_if_changed=True)

    def set_favourite(self, keys, on: bool) -> bool:
        latest = self._cat_json(self._meta("library.json"), {"items": {}})   # merge with other machines
        latest["items"] = rekey_dict(latest.get("items", {}))
        for k in keys:
            e = latest["items"].setdefault(k, {})
            if on:
                e["fav"] = True
            else:
                e.pop("fav", None)
                if not e:
                    latest["items"].pop(k, None)
        self.shared = latest
        return self._put_json(self._meta("library.json"), latest)

    def is_favourite(self, key) -> bool:
        return bool(self.shared.get("items", {}).get(key, {}).get("fav"))

    # -- this machine's checkouts ----------------------------------------------
    def manifest(self) -> dict:
        m = _read_json(LOCAL_MANIFEST, {})
        return rekey_dict(m.get(self.location, {})) if isinstance(m, dict) else {}

    def _save_manifest(self, mine: dict):
        m = _read_json(LOCAL_MANIFEST, {})
        if not isinstance(m, dict):
            m = {}
        m[self.location] = mine
        _write_json(LOCAL_MANIFEST, m)

    def record(self, key, present: bool):
        mine = self.manifest()
        if present:
            it = self.items.get(key) or self.local.get(key)
            mine[key] = {"size": it.size if it else 0, "at": int(time.time())}
        else:
            mine.pop(key, None)
        self._save_manifest(mine)
        self._publish_mine()

    def _publish_mine(self, only_if_changed=False):
        mine = {k: v for k, v in self.manifest().items()
                if self.status(k)[0] in ("here", "partial", "card_out")}
        drives = self.drives()
        doc = {"machine": self.machine, "label": self.label, "items": mine,
               "free": sum(d[3] for d in drives) if drives else None,
               "total": sum(d[1] for d in drives) if drives else None}
        old = self.machines.get(self.machine, {})
        if only_if_changed and {k: old.get(k) for k in ("label", "items")} == \
                {k: doc[k] for k in ("label", "items")}:
            return
        doc["updated"] = int(time.time())
        self.machines[self.machine] = doc
        self._put_json(self._meta("machines", self.machine + ".json"), doc)

    # -- status ------------------------------------------------------------------
    def local_dir_for(self, system: str) -> str:
        """The folder name this machine uses for a system (its existing one, else EmuDeck's name)."""
        return self.local_sysdirs.get(system) or system

    def lib_dir_for(self, system: str) -> str:
        return self.lib_sysdirs.get(system) or system

    def status(self, key):
        """('here' | 'partial' | 'not_here' | 'local_only' | 'card_out', detail).
        card_out: checked out to a system folder that links to a card that isn't inserted."""
        lib, loc = self.items.get(key), self.local.get(key)
        if loc and not lib:
            return "local_only", "Only on this machine"
        if not loc:
            system = key.split("/", 1)[0]
            if system in self.offline and key in self.manifest():
                return "card_out", self.offline[system][1]
            return "not_here", ""
        want = {p: s for p, (s, _) in lib.files.items()}
        have = {p: s for p, (s, _) in loc.files.items()}
        if all(have.get(p) == s for p, s in want.items()):
            return "here", ""
        got = sum(min(have.get(p, 0), s) for p, s in want.items())
        return "partial", f"{human(got)} of {human(lib.size)}"

    def elsewhere(self, key):
        """Labels of other machines that have this game checked out."""
        return sorted(d.get("label") or mid for mid, d in self.machines.items()
                      if mid != self.machine and key in d.get("items", {}))

    def all_keys(self):
        with self.lock:
            return sorted(set(self.items) | set(self.local),
                      key=lambda k: ((self.items.get(k) or self.local.get(k)).system,
                                     (self.items.get(k) or self.local.get(k)).title.lower()))

    def get(self, key):
        return self.items.get(key) or self.local.get(key)

    def checked_out_size(self) -> tuple:
        n, total = 0, 0
        with self.lock:
            keys = list(self.items)
        mine = self.manifest()
        for k in keys:
            st = self.status(k)[0]
            if st in ("here", "partial"):
                n += 1
                total += self.local[k].size
            elif st == "card_out":
                n += 1
                total += mine.get(k, {}).get("size", 0)
        return n, total
