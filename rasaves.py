"""
rasaves.py - RetroArch saves, matched to their games.

RetroArch names every save after the game file it belongs to: "<ROM name>.srm" for the in-game
save, "<ROM name>.state", ".state1" … ".state999" and ".state.auto" for save states, each state
usually with a ".png" screenshot. Where those files go depends on retroarch.cfg:

    savefile_directory / savestate_directory      the base folders
    sort_savefiles_by_content_enable               + "/<name of the ROM's folder>"
    sort_savefiles_enable                          + "/<core name>"   (after the content folder)
    savefiles_in_content_dir                       next to the ROM instead
    (and the same four for states)

so changing those settings, or renaming a ROM, leaves saves behind where RetroArch no longer looks.
This module reads the settings and playlists, finds every save and state, works out which game
(and system) each belongs to, and provides the safe operations the RetroArch Save Manager uses:

  * scan()             games with their saves, states and screenshots, matched to ROMs
  * suggest_roms()     the closest ROM names for saves whose ROM was renamed (orphans)
  * rename_game()      rename a game's files to a ROM's name
  * tidy_plan()/tidy() move scattered files to where RetroArch looks now, resolving duplicates
  * backup_game() / list_backups() / restore() / delete_game() / delete_backup()

Nothing is erased outright: replaced, moved-away and deleted files go to
<backups>/_retroarch_safety/<date-time>/ (the newest 20 changes are kept), and every copy is
written next to its destination, read back and only then put in place.
"""
from __future__ import annotations

import difflib
import json
import os
import platform
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import romlibrary as L
import safecopy

SAFETY_NAME = "_retroarch_safety"
BACKUPS_NAME = "RetroArch saves"
IS_WIN = platform.system() == "Windows"

# ---------------------------------------------------------------------------
# File names
# ---------------------------------------------------------------------------
_SUFFIX = re.compile(r"""^(?P<stem>.+?)(?P<suffix>
      \.state(?P<tslot>\d+|\.auto)?\.png
    | \.state(?P<sslot>\d+|\.auto)?
    | \.replay(?P<rslot>\d+|\.auto)?
    | \.(?P<card>\d)\.mcr
    | \.(?P<ext>srm|rtc|ldci|sav|dsv|eep|sra|fla|mpk|mcr)
)$""", re.I | re.X)
# Next to ROMs only RetroArch's own names count (a .sav there is usually a standalone emulator's)
_CONTENT_EXT = {"srm", "rtc", "ldci"}
# Folders inside the saves folder that cores use for their own system files, not per-game saves
SKIP_DIRS = {"dolphin-emu", "ppsspp", "pcsx2", "lrps2", "citra", "azahar", "melonds", ".netplay", "flycast",
             "fbneo", "mame", "scummvm", "dosbox", "pcsx2-lrps2", "vice", "puae", "np2kai"}
MAX_DEPTH = 2                              # <content folder>/<core>/

# RetroArch core names (library_name, as used for its per-core folders) -> systems they run
CORE_SYSTEMS = {
    "beetle psx hw": {"psx"}, "beetle psx": {"psx"}, "pcsx-rearmed": {"psx"}, "pcsx rearmed": {"psx"},
    "swanstation": {"psx"}, "duckstation": {"psx"},
    "snes9x": {"snes"}, "snes9x 2010": {"snes"}, "snes9x 2005": {"snes"}, "bsnes": {"snes"},
    "bsnes-hd beta": {"snes"}, "bsnes-mercury": {"snes"}, "beetle bsnes": {"snes"}, "mesen-s": {"snes", "gb", "gbc"},
    "mesen": {"nes"}, "fceumm": {"nes"}, "nestopia": {"nes"}, "quicknes": {"nes"},
    "genesis plus gx": {"genesis", "mastersystem", "gamegear", "segacd", "megacd", "sg-1000"},
    "genesis plus gx wide": {"genesis", "mastersystem", "gamegear", "segacd", "megacd"},
    "picodrive": {"genesis", "sega32x", "segacd", "megacd", "mastersystem"}, "blastem": {"genesis"},
    "gearsystem": {"mastersystem", "gamegear", "sg-1000"},
    "gambatte": {"gb", "gbc"}, "sameboy": {"gb", "gbc"}, "gearboy": {"gb", "gbc"}, "tgb dual": {"gb", "gbc"},
    "mgba": {"gba", "gb", "gbc"}, "gpsp": {"gba"}, "vba-m": {"gba", "gb", "gbc"}, "vba next": {"gba"},
    "beetle gba": {"gba"}, "mupen64plus-next": {"n64"}, "parallel n64": {"n64"},
    "melonds": {"nds"}, "melonds ds": {"nds"}, "desmume": {"nds"}, "desmume 2015": {"nds"},
    "beetle pce": {"pcengine"}, "beetle pce fast": {"pcengine"}, "beetle supergrafx": {"pcengine"},
    "beetle saturn": {"saturn"}, "yabause": {"saturn"}, "yabasanshiro": {"saturn"}, "kronos": {"saturn"},
    "flycast": {"dreamcast"}, "ppsspp": {"psp"}, "dolphin": {"gc", "wii"}, "lrps2": {"ps2"}, "pcsx2": {"ps2"},
    "citra": {"n3ds"}, "stella": {"atari2600"}, "stella 2014": {"atari2600"}, "beetle lynx": {"atarilynx"},
    "handy": {"atarilynx"}, "beetle neopop": {"ngp", "ngpc"}, "race": {"ngp", "ngpc"},
    "beetle wonderswan": {"wonderswan", "wonderswancolor"}, "beetle vb": {"virtualboy"},
    "opera": {"3do"}, "virtual jaguar": {"atarijaguar"}, "prosystem": {"atari7800"}, "a5200": {"atari5200"},
    "beetle pc-fx": {"pcfx"}, "finalburn neo": {"fbneo", "arcade", "neogeo"},
}
_LIBRETRO_TO_SYSTEM = {}
try:
    import libmeta
    for _k, _v in libmeta.LIBRETRO_SYSTEM.items():
        _LIBRETRO_TO_SYSTEM.setdefault(_v.lower(), _k)
    LIBRETRO_SYSTEM = libmeta.LIBRETRO_SYSTEM
except Exception:  # noqa - pictures and playlist names are optional
    LIBRETRO_SYSTEM = {}
ROM_SKIP_EXT = {".txt", ".xml", ".png", ".jpg", ".jpeg", ".webp", ".pdf", ".nfo", ".dat", ".db", ".ini", ".cfg",
                ".srm", ".sav", ".rtc", ".ldci", ".state", ".png", ".mp4", ".json", ".lpl", ".sh", ".bat", ".exe",
                ".dll", ".so", ".log", ".md", ".bak", ".tmp"}
ROM_FILE_LIMIT = 200_000


class RAError(Exception):
    pass


def split_name(name: str, content=False):
    """'Game (USA).state3.png' -> ('Game (USA)', '.state3.png', 'thumb', 3); None if not a save file."""
    m = _SUFFIX.match(name)
    if not m:
        return None
    stem, suffix = m.group("stem"), m.group("suffix")
    if m.group("ext") is not None or m.group("card") is not None:
        ext = (m.group("ext") or "mcr").lower()
        if content and ext not in _CONTENT_EXT:
            return None
        return stem, suffix, ("meta" if ext == "ldci" else "sram"), None
    low = suffix.lower()
    if low.endswith(".png"):
        kind, v = "thumb", m.group("tslot")
    elif low.startswith(".state"):
        kind, v = "state", m.group("sslot")
    else:
        kind, v = "replay", m.group("rslot")
    slot = 0 if v is None else (-1 if v.lower() == ".auto" else int(v))
    return stem, suffix, kind, slot


def slot_name(slot):
    return "Auto" if slot == -1 else f"Slot {slot}"


# ---------------------------------------------------------------------------
# retroarch.cfg
# ---------------------------------------------------------------------------
def read_cfg(path: Path) -> dict:
    out = {}
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        m = re.match(r'^\s*([A-Za-z0-9_]+)\s*=\s*"?(.*?)"?\s*$', line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def config_candidates(extra_dirs=()):
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    dirs = list(extra_dirs) + [
        home / ".var/app/org.libretro.RetroArch/config/retroarch", home / ".config/retroarch",
        home / "snap/retroarch/current/.config/retroarch", home / "Library/Application Support/RetroArch",
        appdata / "EmuDeck/Emulators/RetroArch", appdata / "RetroArch",
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Steam/steamapps/common/RetroArch",
        Path("C:/RetroArch-Win64"), Path("C:/RetroArch"),
    ]
    return [Path(d) / "retroarch.cfg" for d in dirs]


def find_config(extra_dirs=()):
    found = find_configs(extra_dirs)
    return found[0] if found else None


def find_configs(extra_dirs=()):
    """Every retroarch.cfg on this computer, in the order they're looked for (no duplicates)."""
    out, seen = [], set()
    for c in config_candidates(extra_dirs):
        try:
            if not c.is_file():
                continue
            k = os.path.normcase(os.path.realpath(c))
        except OSError:
            continue
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def same_dir(a, b) -> bool:
    """The same folder, following links and junctions (EmuDeck links RetroArch's saves folder)."""
    if not a or not b:
        return False
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except OSError:
        return False


@dataclass
class Settings:
    saves_dir: Path | None = None
    states_dir: Path | None = None
    sort_saves_core: bool = False
    sort_saves_content: bool = False
    sort_states_core: bool = False
    sort_states_content: bool = False
    saves_in_content: bool = False
    states_in_content: bool = False
    playlists_dir: Path | None = None
    thumbs_dir: Path | None = None
    cfg_path: Path | None = None
    cfg_saves_dir: Path | None = None        # what the settings file itself says (or implies)
    cfg_states_dir: Path | None = None
    other_cfg: Path | None = None            # a settings file found that uses other folders
    other_dirs: tuple = ()                   # …and those folders (not shown or changed)

    def area_dir(self, area):
        return self.saves_dir if area == "saves" else self.states_dir

    def target_dir(self, area: str, core: str = "", content_dir: str = "", rom_dir: Path | None = None):
        """Where RetroArch reads this game's saves (area 'saves') or states now; None if unknowable.

        Raises RAError with a reason when a needed piece (the core or the ROM's folder) isn't known."""
        in_content = self.saves_in_content if area == "saves" else self.states_in_content
        by_core = self.sort_saves_core if area == "saves" else self.sort_states_core
        by_content = self.sort_saves_content if area == "saves" else self.sort_states_content
        base = self.area_dir(area)
        if base is None and not in_content:
            raise RAError(f"RetroArch's {'saves' if area == 'saves' else 'states'} folder wasn't found")
        if in_content:
            if rom_dir is None:
                raise RAError("RetroArch keeps these next to the game file, and its file wasn't found")
            base = Path(rom_dir)
        if by_content:
            if not content_dir:
                raise RAError("RetroArch sorts these by the game file's folder, and its file wasn't found")
            base = base / content_dir
        if by_core:
            if not core:
                raise RAError("RetroArch sorts these by core, and the core this game uses isn't known")
            base = base / core
        return base


def _cfg_path_value(v: str, cfg_dir: Path):
    if not v or v.lower() == "default":
        return None
    if v.startswith(":"):                                   # relative to RetroArch's own folder
        return cfg_dir / v[1:].lstrip("/\\")
    return Path(os.path.expanduser(v))


def load_settings(cfg_path=None, saves_fallback=None, states_fallback=None) -> Settings:
    s = Settings(cfg_path=Path(cfg_path) if cfg_path else None)
    cfg = read_cfg(cfg_path) if cfg_path else {}
    base = Path(cfg_path).parent if cfg_path else None

    def on(k):
        return cfg.get(k, "false").strip().lower() == "true"

    def path(k):
        p = _cfg_path_value(cfg.get(k, ""), base) if base else None
        return p if p is not None and p.is_dir() else None

    s.cfg_saves_dir = path("savefile_directory") or (base / "saves" if base and (base / "saves").is_dir() else None)
    s.cfg_states_dir = path("savestate_directory") or (base / "states" if base and (base / "states").is_dir() else None)
    s.saves_dir = path("savefile_directory") or (Path(saves_fallback) if saves_fallback else None) \
        or (base / "saves" if base and (base / "saves").is_dir() else None)
    s.states_dir = path("savestate_directory") or (Path(states_fallback) if states_fallback else None) \
        or (base / "states" if base and (base / "states").is_dir() else None)
    s.sort_saves_core, s.sort_saves_content = on("sort_savefiles_enable"), on("sort_savefiles_by_content_enable")
    s.sort_states_core, s.sort_states_content = on("sort_savestates_enable"), on("sort_savestates_by_content_enable")
    s.saves_in_content, s.states_in_content = on("savefiles_in_content_dir"), on("savestates_in_content_dir")
    s.playlists_dir = path("playlist_directory") or (base / "playlists" if base and (base / "playlists").is_dir() else None)
    s.thumbs_dir = path("thumbnails_directory") or (base / "thumbnails" if base and (base / "thumbnails").is_dir() else None)
    return s


def choose_settings(saves=None, states=None, extra_dirs=(), cfg_path=None) -> Settings:
    """Settings for the saves and states folders the app itself uses (the main window's).

    The retroarch.cfg whose folders are those folders is used for the sorting settings, playlists
    and thumbnails. If no settings file uses them (another RetroArch install, say), the app's folders
    are still used, with RetroArch's default sorting, and the other file is reported, never touched."""
    if cfg_path:                                          # chosen by hand
        return load_settings(cfg_path, saves, states)
    app_saves = Path(saves) if saves and Path(saves).is_dir() else None
    app_states = Path(states) if states and Path(states).is_dir() else None
    cands = find_configs(extra_dirs)
    if app_saves is None and app_states is None:
        return load_settings(cands[0] if cands else None)
    for c in cands:
        s = load_settings(c, app_saves, app_states)
        if same_dir(s.cfg_saves_dir, app_saves) or same_dir(s.cfg_states_dir, app_states):
            s.saves_dir = app_saves or s.saves_dir
            s.states_dir = app_states or s.states_dir
            return s
    s = load_settings(None, app_saves, app_states)
    if cands:
        other = load_settings(cands[0])
        s.other_cfg = cands[0]
        s.other_dirs = tuple(d for d in (other.cfg_saves_dir, other.cfg_states_dir) if d)
        s.playlists_dir, s.thumbs_dir = other.playlists_dir, other.thumbs_dir      # read only: names, art
    return s


def _set_cfg_values(raw: bytes, values: dict) -> bytes:
    """retroarch.cfg with some keys set (added when missing); line endings kept."""
    nl = b"\r\n" if b"\r\n" in raw else b"\n"
    lines = raw.split(nl)
    seen = set()
    for i, line in enumerate(lines):
        m = re.match(rb"^\s*([A-Za-z0-9_]+)\s*=", line)
        if m and m.group(1).decode() in values:
            k = m.group(1).decode()
            seen.add(k)
            lines[i] = f'{k} = "{values[k]}"'.encode("utf-8")
    tail = 1 if lines and lines[-1] == b"" else 0
    for k, v in values.items():
        if k not in seen:
            lines.insert(len(lines) - tail, f'{k} = "{v}"'.encode("utf-8"))
    return nl.join(lines)


def fix_folders(s: Settings, backup_dir) -> Report:
    """When RetroArch (s.other_cfg) keeps its saves somewhere other than the app's folders:
    point it at the app's folders, with per-core and per-folder sorting off (EmuDeck's layout),
    and copy its own saves and states there. When a file is in both places the newer one is kept
    and the other goes to the safety folder. retroarch.cfg is backed up next to itself first;
    RetroArch's old folders are left as they are."""
    rep = Report()
    if not s.other_cfg or not s.saves_dir:
        rep.refused.append(("RetroArch", "nothing to fix"))
        return rep
    cfg = Path(s.other_cfg)
    old = load_settings(cfg)
    safety = safecopy.Safety(backup_dir, SAFETY_NAME)
    raw = cfg.read_bytes()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safecopy.write_file_verified(raw, cfg.with_name(f"{cfg.name}.esm-backup-{stamp}"))
    pairs = [("saves", old.cfg_saves_dir, s.saves_dir), ("states", old.cfg_states_dir, s.states_dir)]
    for area, src, dst in pairs:
        if not src or not dst or same_dir(src, dst):
            continue
        for f in sorted(scan_area(src, area), key=lambda f: f.mtime):
            dest = Path(dst) / f.name                        # sorting off: straight into the folder
            try:
                if dest.exists():
                    if dest.read_bytes() == f.path.read_bytes():
                        continue
                    if dest.stat().st_mtime >= f.mtime:
                        rep.skipped.append(f)                # the one here is newer: keep it
                        continue
                safecopy.copy_file_verified(f.path, dest, safety, "replaced by newer RetroArch saves")
                rep.done.append(f)
            except (OSError, safecopy.CopyError) as ex:
                rep.refused.append((f.name, str(ex)))
    values = {"savefile_directory": str(s.saves_dir), "sort_savefiles_enable": "false",
              "sort_savefiles_by_content_enable": "false", "savefiles_in_content_dir": "false",
              "sort_savestates_enable": "false", "sort_savestates_by_content_enable": "false",
              "savestates_in_content_dir": "false"}
    if s.states_dir:
        values["savestate_directory"] = str(s.states_dir)
    safecopy.write_file_verified(_set_cfg_values(raw, values), cfg, safety, "retroarch.cfg before the fix")
    rep.safety_dir = safety.dir
    safety.prune()
    return rep


# ---------------------------------------------------------------------------
# Playlists and ROMs
# ---------------------------------------------------------------------------
@dataclass
class Rom:
    path: Path
    system: str = ""
    label: str = ""
    core: str = ""
    db_name: str = ""

    @property
    def stem(self):
        return rom_stem(str(self.path))


def rom_stem(path: str) -> str:
    """The name RetroArch gives a game's saves: the file name without its (last) extension."""
    name = re.split(r"[\\/]", path.split("#")[-1] if "#" in path else path)[-1]
    return os.path.splitext(name)[0]


def system_from_db(db_name: str) -> str:
    n = db_name[:-4] if db_name.lower().endswith(".lpl") else db_name
    return _LIBRETRO_TO_SYSTEM.get(n.lower(), "")


def system_from_path(path) -> str:
    for part in reversed(Path(path).parts[:-1]):
        c = L.canonical(part)
        if c in L.SYSTEMS:
            return c
    return ""


def read_playlists(folder) -> list:
    """Entries of every .lpl in folder (JSON, or the old six-lines-per-game format)."""
    out = []
    if not folder or not Path(folder).is_dir():
        return out
    files = sorted(Path(folder).glob("*.lpl"), key=lambda p: (p.name.startswith("content_"), p.name.lower()))
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        entries = []
        try:
            data = json.loads(text)
            entries = [(e.get("path", ""), e.get("label", ""), e.get("core_name", ""), e.get("db_name", ""))
                       for e in data.get("items", []) if isinstance(e, dict)]
        except ValueError:
            lines = text.splitlines()
            for i in range(0, len(lines) - 5, 6):
                entries.append((lines[i], lines[i + 1], lines[i + 3], lines[i + 5]))
        for path, label, core, db in entries:
            if not path:
                continue
            db = db or ("" if f.name.startswith("content_") else f.name)
            core = "" if core in ("DETECT", "N/A") else core
            out.append(Rom(Path(path.split("#")[0]) if "#" in path else Path(path), system_from_db(db)
                           or system_from_path(path.split("#")[0]), label, core, db[:-4] if db.lower().endswith(".lpl") else db))
            if "#" in path:                                 # saves follow the file inside the archive
                out[-1].label = out[-1].label or rom_stem(path)
                out.append(Rom(Path(path), out[-1].system, out[-1].label, core, out[-1].db_name))
    return out


@dataclass
class RomIndex:
    by_stem: dict = field(default_factory=dict)          # lower stem -> [Rom]
    content_files: list = field(default_factory=list)    # RetroArch saves found next to ROMs
    dirs: list = field(default_factory=list)
    truncated: bool = False

    def add(self, rom: Rom):
        lst = self.by_stem.setdefault(rom.stem.lower(), [])
        for i, r in enumerate(lst):
            if r.path == rom.path or (str(r.path).lower() == str(rom.path).lower()):
                for k in ("system", "label", "core", "db_name"):        # playlists add details
                    if not getattr(r, k) and getattr(rom, k):
                        setattr(r, k, getattr(rom, k))
                return
        lst.append(rom)

    def all(self):
        return [r for lst in self.by_stem.values() for r in lst]

    def __len__(self):
        return len(self.by_stem)


def build_rom_index(rom_dirs=(), playlist_roms=(), scan_content=True) -> RomIndex:
    idx = RomIndex(dirs=[Path(d) for d in rom_dirs if d and Path(d).is_dir()])
    for r in playlist_roms:
        if r.path.exists() or "#" in str(r.path):
            idx.add(r)
    count = 0
    for top in idx.dirs:
        for dirpath, dirnames, filenames in os.walk(top):
            rel_depth = len(Path(dirpath).relative_to(top).parts)
            dirnames[:] = [d for d in dirnames if d.lower() not in L.IGNORE_DIRS and not d.startswith(".")
                           and rel_depth < 4]
            system = system_from_path(Path(dirpath) / "x")
            for fn in filenames:
                count += 1
                if count > ROM_FILE_LIMIT:
                    idx.truncated = True
                    return idx
                p = Path(dirpath) / fn
                low = fn.lower()
                if scan_content:
                    sp = split_name(fn, content=True)
                    if sp:
                        idx.content_files.append((p, top, sp))
                        continue
                ext = os.path.splitext(low)[1]
                if ext in ROM_SKIP_EXT or low in L.IGNORE_FILES or not ext or ".state" in low:
                    continue
                idx.add(Rom(p, system))
    return idx


# ---------------------------------------------------------------------------
# Saves and games
# ---------------------------------------------------------------------------
@dataclass
class RAFile:
    path: Path
    area: str                  # saves | states | content
    rel: tuple                 # folders between the area's base and the file
    stem: str
    suffix: str
    kind: str                  # sram | state | thumb | replay | meta
    slot: int | None
    size: int = 0
    mtime: float = 0.0

    @property
    def name(self):
        return self.path.name

    @property
    def target_area(self):
        return "saves" if self.kind in ("sram", "meta") else "states"


@dataclass
class RAGame:
    stem: str
    system: str = ""
    files: list = field(default_factory=list)
    rom: Rom | None = None
    core: str = ""
    title: str = ""
    hint_systems: set | None = None
    content_dir_hint: str = ""           # for a backup whose ROM isn't here: its folder's name

    @property
    def key(self):
        return f"{self.system}|{self.stem.lower()}"

    @property
    def system_name(self):
        return L.SYSTEMS.get(self.system, "Unknown system") if self.system else "Unknown system"

    @property
    def srams(self):
        return [f for f in self.files if f.kind == "sram"]

    @property
    def states(self):
        return sorted((f for f in self.files if f.kind == "state"),
                      key=lambda f: (f.slot if f.slot is not None and f.slot >= 0 else 10_000, f.mtime))

    def thumb_for(self, state: RAFile):
        for f in self.files:
            if f.kind == "thumb" and f.slot == state.slot and f.path.parent == state.path.parent:
                return f
        return None

    @property
    def size(self):
        return sum(f.size for f in self.files)

    @property
    def modified(self):
        m = max((f.mtime for f in self.files if f.kind in ("sram", "state")), default=0)
        return datetime.fromtimestamp(m) if m else None

    @property
    def places(self):
        return sorted({str(f.path.parent) for f in self.files})

    @property
    def content_dir(self):
        return self.rom.path.parent.name if self.rom else self.content_dir_hint

    @property
    def rom_dir(self):
        return self.rom.path.parent if self.rom else None


def _hint(parts):
    systems, core = None, ""
    for p in parts:
        low = p.lower()
        if low in CORE_SYSTEMS:
            core, s = p, CORE_SYSTEMS[low]
        else:
            c = L.canonical(p)
            if c not in L.SYSTEMS:
                continue
            s = {c}
        systems = set(s) if systems is None else (systems & s or set(s))
    return systems, core


def scan_area(base, area) -> list:
    out = []
    if not base or not Path(base).is_dir():
        return out
    base = Path(base)
    for dirpath, dirnames, filenames in os.walk(base):
        rel = Path(dirpath).relative_to(base).parts
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith(".")
                       and len(rel) < MAX_DEPTH]
        for fn in filenames:
            sp = split_name(fn)
            if not sp:
                continue
            p = Path(dirpath) / fn
            try:
                st = p.stat()
            except OSError:
                continue
            out.append(RAFile(p, area, rel, sp[0], sp[1], sp[2], sp[3], st.st_size, st.st_mtime))
    return out


def _title(stem, rom):
    if rom and rom.label:
        return rom.label
    return stem.replace("_", " ").strip() or stem


@dataclass
class Library:
    settings: Settings
    games: list
    roms: RomIndex

    def find(self, key):
        return next((g for g in self.games if g.key == key), None)

    @property
    def systems(self):
        return sorted({g.system for g in self.games}, key=lambda s: L.SYSTEMS.get(s, "~" + s))


def scan(settings: Settings, rom_dirs=(), playlist_roms=None) -> Library:
    if playlist_roms is None:
        playlist_roms = read_playlists(settings.playlists_dir)
    roms = build_rom_index(rom_dirs, playlist_roms)
    files, seen = [], set()
    for area, base in (("saves", settings.saves_dir), ("states", settings.states_dir)):
        for f in scan_area(base, area):
            k = os.path.normcase(str(f.path))
            if k not in seen:
                seen.add(k)
                files.append(f)
    for p, top, sp in roms.content_files:
        k = os.path.normcase(str(p))
        if k in seen:
            continue
        seen.add(k)
        try:
            st = p.stat()
        except OSError:
            continue
        files.append(RAFile(p, "content", Path(p).parent.relative_to(top).parts, sp[0], sp[1], sp[2], sp[3],
                            st.st_size, st.st_mtime))

    by_stem = {}
    for f in files:
        by_stem.setdefault(f.stem.lower(), []).append(f)
    games = []
    for low, fs in by_stem.items():
        candidates = roms.by_stem.get(low, [])
        groups = {}                      # system ('' unknown) -> RAGame
        for f in fs:
            if f.area == "content":
                hs, core = ({system_from_path(f.path)} - {""}) or None, ""
            else:
                hs, core = _hint(f.rel)
            rom = None
            if candidates:
                ok = [r for r in candidates if hs is None or (r.system and r.system in hs) or not r.system]
                if f.area == "content":
                    ok = [r for r in candidates if r.path.parent == f.path.parent] or ok
                rom = ok[0] if ok else None
            system = (rom.system if rom and rom.system else
                      (next(iter(hs)) if hs and len(hs) == 1 else ""))
            g = groups.get(system)
            if g is None:
                g = groups[system] = RAGame(f.stem, system, rom=rom, hint_systems=hs)
            elif g.rom is None and rom is not None:
                g.rom = rom
            if hs and g.hint_systems is not None:
                g.hint_systems = (g.hint_systems & hs) or g.hint_systems
            if core and not g.core:
                g.core = core
            g.files.append(f)
        # files with no system hint join the one game this name has, if there is exactly one
        if "" in groups and len(groups) == 2:
            other = next(v for k, v in groups.items() if k)
            other.files += groups.pop("").files
            if other.rom is None:
                other.rom = next((r for r in candidates if r.system == other.system), None)
        for g in groups.values():
            if g.rom is None and candidates:
                g.rom = next((r for r in candidates if not g.system or r.system == g.system), None)
                if g.rom and not g.system:
                    g.system = g.rom.system
            if not g.core and g.rom and g.rom.core:
                g.core = g.rom.core
            g.title = _title(g.stem, g.rom)
            games.append(g)
    games.sort(key=lambda g: (L.SYSTEMS.get(g.system, "~"), g.title.lower()))
    return Library(settings, games, roms)


def game_location(lib: Library, g: RAGame, area: str) -> Path:
    """Where this game's files of an area belong now (RAError says why it can't be told)."""
    return lib.settings.target_dir(area, g.core, g.content_dir, g.rom_dir)


def scattered(lib: Library, g: RAGame) -> list:
    """The game's files that aren't where RetroArch looks for them now."""
    out = []
    for f in g.files:
        try:
            want = game_location(lib, g, f.target_area)
        except RAError:
            continue
        if os.path.normcase(str(f.path.parent)) != os.path.normcase(str(want)):
            out.append(f)
    return out


# ---------------------------------------------------------------------------
# Orphans: saves whose ROM was renamed
# ---------------------------------------------------------------------------
def _norm(name: str) -> str:
    n = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", name.lower())
    n = n.replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", n).split())


def is_orphan(lib: Library, g: RAGame) -> bool:
    return g.rom is None and len(lib.roms) > 0


def suggest_roms(lib: Library, g: RAGame, n=3) -> list:
    """[(Rom, score 0..1, has_saves)] closest to the game's name, same system first."""
    target, full = _norm(g.stem), g.stem.lower()
    taken = {x.stem.lower() for x in lib.games if x.rom is not None}
    scored = []
    for r in lib.roms.all():
        if g.system and r.system and r.system != g.system:
            continue
        if g.hint_systems and r.system and r.system not in g.hint_systems:
            continue
        rn = _norm(r.stem)
        if not rn:
            continue
        s = difflib.SequenceMatcher(None, target, rn).ratio()
        if rn == target:
            s = 0.97 + 0.03 * difflib.SequenceMatcher(None, full, r.stem.lower()).ratio()
        elif target and (rn.startswith(target) or target.startswith(rn)):
            s = max(s, 0.85)
        if s >= 0.6:
            scored.append((r, round(min(s, 1.0), 3), r.stem.lower() in taken))
    scored.sort(key=lambda t: (-t[1], str(t[0].path)))
    out, names = [], set()
    for r, s, t in scored:
        if r.stem.lower() not in names:
            names.add(r.stem.lower())
            out.append((r, s, t))
        if len(out) >= n:
            break
    return out


@dataclass
class Report:
    done: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    refused: list = field(default_factory=list)     # (what, why)
    safety_dir: Path | None = None


def _ask(conflict, *a):
    ans = conflict(*a) if conflict else "replace"
    if ans == "cancel":
        raise _Cancelled()
    return ans


class _Cancelled(Exception):
    pass


def _move_file(src: Path, dest: Path):
    """Move a file, verified when it has to be copied to another drive."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dest)
        return
    except OSError:
        pass
    safecopy.copy_file_verified(src, dest)
    os.remove(src)


def rename_game(lib: Library, g: RAGame, new_stem: str, backup_dir, conflict=None) -> Report:
    """Rename all of a game's files to new_stem (in their folders). conflict(game, [existing]) ->
    'replace' | 'skip' | 'cancel'; replaced files go to the safety folder."""
    rep = Report()
    safety = safecopy.Safety(backup_dir, SAFETY_NAME)
    pairs = [(f, f.path.with_name(new_stem + f.suffix)) for f in g.files]
    existing = [d for f, d in pairs if d.exists() and os.path.normcase(str(d)) != os.path.normcase(str(f.path))]
    try:
        if existing and _ask(conflict, g, existing) == "skip":
            rep.skipped.append(g)
            return rep
    except _Cancelled:
        rep.skipped.append(g)
        return rep
    for f, d in pairs:
        try:
            if d.exists() and os.path.normcase(str(d)) != os.path.normcase(str(f.path)):
                safety.keep(d, "replaced")
            _move_file(f.path, d)
            rep.done.append(f)
        except OSError as ex:
            rep.refused.append((f.name, str(ex)))
    rep.safety_dir = safety.dir
    safety.prune()
    return rep


# ---------------------------------------------------------------------------
# Tidying: files RetroArch no longer looks at, and duplicates
# ---------------------------------------------------------------------------
@dataclass
class TidyPlan:
    moves: list = field(default_factory=list)       # (game, RAFile, dest Path)
    dupes: list = field(default_factory=list)       # (game, keep RAFile|None (file at dest), [older RAFile])
    unknown: list = field(default_factory=list)     # (game, reason)

    @property
    def empty(self):
        return not (self.moves or self.dupes)

    def summary_lines(self, limit=8):
        lines = []
        games = {}
        for g, f, d in self.moves:
            games.setdefault(g.key, [g, 0, 0])[1] += 1
        for g, keep, older in self.dupes:
            games.setdefault(g.key, [g, 0, 0])[2] += len(older)
        for g, mv, du in list(games.values())[:limit]:
            bits = []
            if mv:
                bits.append(f"{mv} file{'s' if mv != 1 else ''} to move")
            if du:
                bits.append(f"{du} older duplicate{'s' if du != 1 else ''}")
            lines.append(f"• {g.title} ({g.system_name}): " + ", ".join(bits))
        if len(games) > limit:
            lines.append(f"• …and {len(games) - limit} more games")
        return lines


def tidy_plan(lib: Library, games=None) -> TidyPlan:
    plan = TidyPlan()
    per_game = []
    for g in games if games is not None else lib.games:
        wanted = {}
        try:
            for f in g.files:
                d = game_location(lib, g, f.target_area)
                wanted.setdefault(os.path.normcase(str(d / f.name)), (d / f.name, []))[1].append(f)
        except RAError as ex:
            plan.unknown.append((g, str(ex)))
            continue
        per_game.append((g, wanted))
    # two games (same name, different systems) that would end up in the same file
    claims = {}
    for g, wanted in per_game:
        for k in wanted:
            claims.setdefault(k, set()).add(g.key)
    for x in lib.games:                      # files already there that belong to another game
        for f in x.files:
            claims.setdefault(os.path.normcase(str(f.path)), set()).add(x.key)
    for g, wanted in per_game:
        clash = [k for k in wanted if len(claims.get(k, ())) > 1]
        if clash:
            others = sorted({lib.find(k).system_name for c in clash for k in claims[c]
                             if k != g.key and lib.find(k)})
            plan.unknown.append((g, "another game with the same name (" + ", ".join(others) +
                                 ") would use the same file there; RetroArch's per-core folders keep them apart"))
            continue
        for _k, (dest, fs) in wanted.items():
            at_dest = next((f for f in fs if os.path.normcase(str(f.path)) == os.path.normcase(str(dest))), None)
            others = [f for f in fs if f is not at_dest]
            if not others:
                continue
            if at_dest is None and len(others) == 1:
                plan.moves.append((g, others[0], dest))
                continue
            ranked = sorted(fs, key=lambda f: f.mtime, reverse=True)
            newest = ranked[0]
            if newest is not at_dest:
                plan.moves.append((g, newest, dest))
            plan.dupes.append((g, newest, ranked[1:]))
    return plan


def tidy(plan: TidyPlan, backup_dir) -> Report:
    """Carry out a plan. Older duplicates (and anything a move would replace) go to the safety folder."""
    rep = Report()
    safety = safecopy.Safety(backup_dir, SAFETY_NAME)
    for g, keep, older in plan.dupes:
        for f in older:
            try:
                if f.path.exists():
                    safety.keep(f.path, "older duplicates")
                    rep.done.append(f)
            except OSError as ex:
                rep.refused.append((f.name, str(ex)))
    for g, f, dest in plan.moves:
        try:
            if dest.exists():
                safety.keep(dest, "replaced")
            _move_file(f.path, dest)
            rep.done.append(f)
        except OSError as ex:
            rep.refused.append((f.name, str(ex)))
    rep.safety_dir = safety.dir
    safety.prune()
    return rep


# ---------------------------------------------------------------------------
# One-game backups
# ---------------------------------------------------------------------------
@dataclass
class RABackup:
    path: Path
    info: dict
    files: list                     # [(Path, area, kind, slot)]
    created: datetime | None = None

    @property
    def stem(self):
        return self.info.get("stem", "")

    @property
    def system(self):
        return self.info.get("system", "")

    @property
    def title(self):
        return self.info.get("title") or self.stem

    @property
    def system_name(self):
        return L.SYSTEMS.get(self.system, "Unknown system") if self.system else "Unknown system"

    @property
    def key(self):
        return f"{self.system}|{self.stem.lower()}"

    @property
    def size(self):
        return sum(p.stat().st_size for p, *_ in self.files if p.exists())


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "game"


def backups_root(backup_dir) -> Path:
    return Path(backup_dir) / BACKUPS_NAME


def backup_game(g: RAGame, backup_dir, note="") -> Path:
    """Copy every file of the game into a new dated backup folder; returns it."""
    root = backups_root(backup_dir) / (g.system or "unknown") / _safe(g.stem)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = root / stamp
    n = 2
    while dest.exists():
        dest = root / f"{stamp}-{n}"
        n += 1
    newest = {}
    for f in g.files:                                # duplicates (scattered copies): keep the newest
        k = (f.target_area, f.name.lower())
        if k not in newest or f.mtime > newest[k].mtime:
            newest[k] = f
    pairs, listing = [], []
    for (area, _n), f in sorted(newest.items()):
        pairs.append((f.path, Path(area) / f.name))
        listing.append({"name": f.name, "area": area, "kind": f.kind, "slot": f.slot, "size": f.size,
                        "mtime": f.mtime, "from": str(f.path.parent)})
    info = {"stem": g.stem, "title": g.title, "system": g.system, "core": g.core,
            "rom": str(g.rom.path) if g.rom else "", "content_dir": g.content_dir,
            "created": datetime.now().isoformat(timespec="seconds"), "note": note, "files": listing}
    safecopy.copy_files_verified(pairs, dest, extra={"info.json": json.dumps(info, indent=1).encode("utf-8")})
    return dest


def read_backup(folder: Path) -> RABackup | None:
    try:
        info = json.loads((folder / "info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    files = []
    for e in info.get("files", []):
        p = folder / e.get("area", "saves") / e.get("name", "")
        if p.is_file():
            files.append((p, e.get("area", "saves"), e.get("kind", "sram"), e.get("slot")))
    try:
        created = datetime.fromisoformat(info.get("created", ""))
    except ValueError:
        created = None
    return RABackup(folder, info, files, created)


def list_backups(backup_dir) -> list:
    root = backups_root(backup_dir)
    out = []
    if not root.is_dir():
        return out
    for sysdir in root.iterdir():
        if not sysdir.is_dir():
            continue
        for gdir in sysdir.iterdir():
            if not gdir.is_dir():
                continue
            for b in gdir.iterdir():
                if b.is_dir() and not b.name.endswith(".copying"):
                    r = read_backup(b)
                    if r:
                        out.append(r)
    out.sort(key=lambda b: (L.SYSTEMS.get(b.system, "~"), b.title.lower(),
                            -(b.created.timestamp() if b.created else 0)))
    return out


def restore(lib: Library, b: RABackup, backup_dir, conflict=None) -> Report:
    """Copy a backup's files to where RetroArch looks for that game now.

    conflict(backup, [existing paths]) -> 'replace' | 'skip' | 'cancel'. Replaced files go to the
    safety folder; states in RetroArch that the backup doesn't have are left alone."""
    rep = Report()
    g = lib.find(b.key)
    if g is None:
        rom = None
        rp = b.info.get("rom") or ""
        if rp and Path(rp).exists():
            rom = Rom(Path(rp), b.system)
        else:
            for r in lib.roms.by_stem.get(b.stem.lower(), []):
                if not b.system or r.system in ("", b.system):
                    rom = r
                    break
        g = RAGame(b.stem, b.system, rom=rom, core=b.info.get("core", ""), title=b.title,
                   content_dir_hint=b.info.get("content_dir", ""))
    targets = []
    try:
        for p, area, kind, slot in b.files:
            d = game_location(lib, g, area)
            targets.append((p, d / p.name))
    except RAError as ex:
        rep.refused.append((b.title, str(ex)))
        return rep
    existing = [t for _p, t in targets if t.exists()]
    try:
        if existing and _ask(conflict, b, existing) == "skip":
            rep.skipped.append(b)
            return rep
    except _Cancelled:
        rep.skipped.append(b)
        raise
    safety = safecopy.Safety(backup_dir, SAFETY_NAME)
    for p, t in targets:
        try:
            safecopy.copy_file_verified(p, t, safety, "replaced by a restore")
            rep.done.append(t)
        except (OSError, safecopy.CopyError) as ex:
            rep.refused.append((p.name, str(ex)))
    rep.safety_dir = safety.dir
    safety.prune()
    return rep


def restore_many(lib, backups, backup_dir, conflict=None) -> Report:
    total = Report()
    for b in backups:
        try:
            r = restore(lib, b, backup_dir, conflict)
        except _Cancelled:
            total.skipped.append(b)
            break
        total.done += r.done
        total.skipped += r.skipped
        total.refused += r.refused
    return total


def delete_game(g: RAGame, backup_dir) -> Report:
    rep = Report()
    safety = safecopy.Safety(backup_dir, SAFETY_NAME)
    for f in g.files:
        try:
            safety.keep(f.path, f"deleted/{_safe(g.stem)}")
            rep.done.append(f)
        except OSError as ex:
            rep.refused.append((f.name, str(ex)))
    rep.safety_dir = safety.dir
    safety.prune()
    return rep


def delete_backup(b: RABackup, backup_dir) -> Report:
    rep = Report()
    safety = safecopy.Safety(backup_dir, SAFETY_NAME)
    try:
        safety.keep(b.path, f"deleted backups/{_safe(b.stem)}")
        rep.done.append(b)
        try:
            b.path.parent.rmdir()                     # the game's folder, when that was its last backup
        except OSError:
            pass
    except OSError as ex:
        rep.refused.append((b.title, str(ex)))
    rep.safety_dir = safety.dir
    safety.prune()
    return rep


# ---------------------------------------------------------------------------
# Box art on this machine (RetroArch's own thumbnails, ES-DE's downloads)
# ---------------------------------------------------------------------------
def _safe_thumb(name: str) -> str:
    return re.sub(r'[&*/:`<>?\\|"]', "_", name)


def art_names(title: str, stem: str):
    names = []
    for n in (title, stem):
        if n and n not in names:
            names.append(n)
        nd = re.sub(r"\s*\((Disc|Disk|CD)\s*\d+[^)]*\)", "", n or "", flags=re.I).strip()
        if nd and nd not in names:
            names.append(nd)
    return names


def local_art_candidates(settings: Settings, system, title, stem, db_name="", esde_media=()):
    out = []
    names = art_names(title, stem)
    dbs = [d for d in (db_name, LIBRETRO_SYSTEM.get(system, "")) if d]
    if settings.thumbs_dir:
        for db in dict.fromkeys(dbs):
            for n in names:
                out.append(Path(settings.thumbs_dir) / db / "Named_Boxarts" / (_safe_thumb(n) + ".png"))
    for media in esde_media:
        for sysdir_name in (system,) + tuple(k for k, v in L.ALIASES.items() if v == system):
            for n in (stem, title):
                for ext in (".png", ".jpg"):
                    out.append(Path(media) / sysdir_name / "covers" / (n + ext))
    return out
