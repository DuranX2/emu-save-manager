"""
biostargets.py - find where each installed emulator reads its BIOS files, and install
the right files from the vault there.

Folders are found in this order: the emulator's own settings file, the folder next
to the save folders the app already detects, EmuDeck's settings, then defaults.

Install rules
  * copy, never move; never overwrite a different file of the same name (a conflict
    is reported instead)
  * every file the app installs is recorded in a per-device manifest, so "Remove what
    the app installed" touches only those (they go to the backup folder, not the bin)
  * RetroArch gets each identified file under every name libretro cores look for, plus
    folder sets (dc/, Machines/, np2/ ...) at the same relative path; never unknown files
No GUI.
"""

import configparser
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import biosdb
import cloudsync as cs

APP_DIR = Path.home() / ".emu_save_manager"
MANIFEST_FILE = APP_DIR / "bios_installed.json"
HOME = Path.home()
IS_WIN = os.name == "nt"

KINDS = {
    "pcsx2": "PCSX2",
    "duckstation": "DuckStation",
    "retroarch": "RetroArch",
    "xemu": "xemu",
    "dolphin": "Dolphin (GameCube)",
    "emudeck": "EmuDeck BIOS folder",
    "games": "Games folder",          # sets MAME looks for beside the games (gamebios.py)
}


@dataclass
class Target:
    kind: str                         # key of KINDS
    folder: Path                      # where files go
    source: str                       # how it was found, shown to the user
    extra: dict = field(default_factory=dict)   # xemu: configured file paths

    @property
    def name(self) -> str:
        return self.extra.get("label") or KINDS[self.kind]


# ---------------------------------------------------------------------------
# Reading emulator settings (all optional: a missing or odd file just falls through)
# ---------------------------------------------------------------------------
def _ini(path: Path) -> Optional[configparser.ConfigParser]:
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str
    try:
        cp.read(path, encoding="utf-8-sig")
        return cp
    except (configparser.Error, OSError, UnicodeDecodeError):
        return None


def _ini_get(cp, section, key):
    if cp is None:
        return None
    for s in cp.sections():
        if s.lower() == section.lower():
            for k, v in cp[s].items():
                if k.lower() == key.lower():
                    return v.strip().strip('"')
    return None


def pcsx2_bios_folder(base: Path) -> Optional[Path]:
    """PCSX2 keeps [Folders] Bios in inis/PCSX2.ini (relative to its data folder)."""
    for ini in (base / "inis" / "PCSX2.ini", base / "inis" / "PCSX2_ui.ini"):
        if ini.is_file():
            v = _ini_get(_ini(ini), "Folders", "Bios")
            if v:
                p = Path(v)
                return p if p.is_absolute() else base / p
    return base / "bios"


def duckstation_bios_folder(base: Path) -> Path:
    v = _ini_get(_ini(base / "settings.ini"), "BIOS", "SearchDirectory")
    if v:
        p = Path(v)
        return p if p.is_absolute() else base / p
    return base / "bios"


def retroarch_system_folder(cfg_file: Path) -> Optional[Path]:
    """system_directory in retroarch.cfg; ':' at the start means RetroArch's own folder;
    'default' means beside the content, which the app cannot know - use <root>/system."""
    try:
        text = cfg_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r'^\s*system_directory\s*=\s*"([^"]*)"', text, re.M)
    v = m.group(1).strip() if m else "default"
    root = cfg_file.parent
    if v in ("", "default"):
        return root / "system"
    if v.startswith(":"):
        return root / v.lstrip(":").lstrip("\\/")
    if v.startswith("~"):
        return Path(os.path.expanduser(v))
    return Path(v)


def xemu_files(toml_file: Path) -> dict:
    """bootrom_path / flashrom_path from xemu.toml ([sys.files])."""
    out = {}
    try:
        text = toml_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for key in ("bootrom_path", "flashrom_path"):
        m = re.search(rf"^\s*{key}\s*=\s*(['\"])(.*?)\1", text, re.M)
        if m and m.group(2):
            out[key] = Path(m.group(2))
    return out


# ---------------------------------------------------------------------------
# Finding targets on this device
# ---------------------------------------------------------------------------
def _under(p: Path, parent: Path) -> bool:
    try:
        p.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def find_targets(resolve=None) -> List[Target]:
    """resolve(emu) -> {location label: Path} - the app's own save-folder detection."""
    resolve = resolve or (lambda emu: {})
    found: List[Target] = []
    appdata = Path(os.environ.get("APPDATA", HOME / "AppData" / "Roaming"))
    config = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
    data = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local" / "share"))
    flat = HOME / ".var" / "app"
    emudeck_saves = [r / "saves" for r in cs.emudeck_roots()]
    emudeck_app = cs.emudeck_appdata() / "Emulators"

    def from_saves(emu, label):
        p = resolve(emu).get(label)
        if not p or not Path(p).is_dir():
            return None
        if any(_under(Path(p), s) for s in emudeck_saves):
            return None                    # EmuDeck's links: the EmuDeck target covers it
        return Path(p).parent

    # EmuDeck's shared Emulation/bios (PCSX2, DuckStation, RetroArch and xemu all read it)
    settings = cs.emudeck_settings()
    roots = ([Path(settings["emulationPath"])] if settings.get("emulationPath") else []) + list(cs.emudeck_roots())
    seen = set()
    for r in roots:
        b = r / "bios"
        if b.is_dir() and str(b).lower() not in seen:
            seen.add(str(b).lower())
            found.append(Target("emudeck", b, "EmuDeck settings"))

    base = from_saves("PCSX2", "Memory Cards")
    if base:
        found.append(Target("pcsx2", pcsx2_bios_folder(base), f"PCSX2 at {base}"))

    base = from_saves("DuckStation", "Memory Cards")
    if base:
        found.append(Target("duckstation", duckstation_bios_folder(base), f"DuckStation at {base}"))

    # RetroArch: next to its saves folder, the usual config locations, EmuDeck's copy
    cfgs = []
    base = from_saves("RetroArch", "Saves")
    if base:
        cfgs.append(base / "retroarch.cfg")
    cfgs += [emudeck_app / "RetroArch" / "retroarch.cfg", appdata / "RetroArch" / "retroarch.cfg",
             Path("C:/RetroArch-Win64/retroarch.cfg"), Path("C:/RetroArch/retroarch.cfg"),
             config / "retroarch" / "retroarch.cfg",
             flat / "org.libretro.RetroArch" / "config" / "retroarch" / "retroarch.cfg"]
    for c in cfgs:
        if c.is_file():
            s = retroarch_system_folder(c)
            if s:
                found.append(Target("retroarch", s, f"{c}"))

    # xemu: the files its settings point at
    tomls = [appdata / "xemu" / "xemu" / "xemu.toml", emudeck_app / "xemu" / "xemu.toml",
             data / "xemu" / "xemu" / "xemu.toml",
             flat / "app.xemu.xemu" / "data" / "xemu" / "xemu" / "xemu.toml"]
    hdd = resolve("Xemu").get("HDD and EEPROM")
    if hdd:
        tomls.insert(0, Path(hdd) / "xemu.toml")
    for t in tomls:
        if t.is_file():
            files = xemu_files(t)
            if files:
                folder = next(iter(files.values())).parent
                found.append(Target("xemu", folder, str(t), files))

    # Dolphin: <User>/GC/<USA|EUR|JAP>/IPL.bin, beside the GameCube saves
    gc = resolve("Dolphin").get("GameCube Saves")
    for g in ([Path(gc)] if gc else []) + [emudeck_app / "Dolphin-x64" / "User" / "GC"]:
        if g.is_dir():
            found.append(Target("dolphin", g, f"Dolphin GameCube folder {g}"))

    # games folders for the sets MAME wants beside the games (Game.com: Emulation/roms/gamecom)
    found += games_targets()

    # one target per folder; a RetroArch pointed at EmuDeck's bios folder is the EmuDeck target
    out, keys = [], {}
    for t in found:
        k = (str(t.folder).lower().rstrip("\\/"), t.kind if t.kind in ("xemu", "dolphin", "games") else "files")
        if k in keys:
            prev = keys[k]
            if prev.kind == "emudeck" or t.kind == "emudeck":
                prev.kind = "emudeck"
            continue
        keys[k] = t
        out.append(t)
    return out


def games_targets(roots=None) -> List[Target]:
    """One target per games folder that needs a BIOS set beside the games."""
    import gamebios
    roots = gamebios.roms_roots() if roots is None else roots
    out = []
    for key, s in gamebios.SETS.items():
        for folder, offline in gamebios.games_folders(key, roots):
            label = f"{s.label} in the games folder" + (" (card not inserted)" if offline else "")
            out.append(Target("games", folder, f"{s.why}", {"set": key, "label": label, "offline": offline}))
    return out


# ---------------------------------------------------------------------------
# What goes where
# ---------------------------------------------------------------------------
def plan(target: Target, entries) -> List[tuple]:
    """[(vault entry, destination path)] for one target."""
    out = []
    by_stem: Dict[str, list] = {}
    for e in entries:
        if e.system == biosdb.PS2:
            by_stem.setdefault(Path(e.sub).stem.lower(), []).append(e)

    def ps2_files(folder: Path):
        for e in entries:
            if e.system == biosdb.PS2 and e.ident.status in ("verified", "identified") and "/" not in e.sub:
                out.append((e, folder / e.path.name))
                for side in by_stem.get(e.path.stem.lower(), []):
                    if side.ident.status == "side-file":
                        out.append((side, folder / side.path.name))
        for e in entries:                      # standalone DVD player ROMs (rom1.bin ...)
            if e.system == biosdb.PS2 and e.path.name.lower() in biosdb.PS2_DVD_ROMS:
                out.append((e, folder / e.path.name))

    def ps1_files(folder: Path):
        for e in entries:
            if e.system == biosdb.PS1 and e.ident.status in ("verified", "identified"):
                out.append((e, folder / e.path.name))

    def retroarch_files(folder: Path):
        for e in entries:
            if "/" in e.sub and e.system != biosdb.UNKNOWN and e.ident.status in ("verified", "identified", "in-set",
                                                                                    "unverified"):
                out.append((e, folder / e.sub))           # folder sets: dc/, Machines/, np2/ ...
            if e.ident.status in ("verified", "identified") and e.ident.names:
                for n in e.ident.names:
                    out.append((e, folder / n))
        taken = {str(d).lower() for _e, d in out}
        for e in entries:                                 # right name and size, not on the list:
            if e.ident.status == "unverified" and e.ident.names:   # only where nothing verified goes
                for n in e.ident.names:
                    d = folder / n
                    if str(d).lower() not in taken:
                        taken.add(str(d).lower())
                        out.append((e, d))

    def xemu_files_into(folder: Path, wanted: dict):
        mcpx = next((e for e in entries if e.system == biosdb.XBOX and e.ident.status == "verified"
                     and e.ident.size == 512), None)
        flashes = [e for e in entries if e.system == biosdb.XBOX and e.ident.status == "identified"
                   and e.ident.size in (262144, 1048576)]
        boot = wanted.get("bootrom_path") or folder / "mcpx_1.0.bin"
        flash_path = wanted.get("flashrom_path") or folder / "Complex_4627v1.03.bin"
        if mcpx:
            out.append((mcpx, Path(boot)))
        if flashes:
            same = next((e for e in flashes if e.path.name.lower() == Path(flash_path).name.lower()), flashes[0])
            out.append((same, Path(flash_path)))

    k, f = target.kind, target.folder
    if k == "games":
        return _games_plan(target, entries)
    if k == "pcsx2":
        ps2_files(f)
    elif k == "duckstation":
        ps1_files(f)
    elif k == "retroarch":
        retroarch_files(f)
    elif k == "xemu":
        xemu_files_into(f, target.extra)
    elif k == "dolphin":
        for e in entries:
            if e.system == biosdb.GC and e.ident.region and e.ident.status in ("verified", "identified"):
                out.append((e, f / e.ident.region / "IPL.bin"))
    elif k == "emudeck":
        ps2_files(f)                       # PCSX2 reads the top of Emulation/bios
        retroarch_files(f)                 # RetroArch's system folder (includes PS1)
        xemu_files_into(f, {})             # EmuDeck's fixed xemu names
    # one source per destination (the first wins); a destination is never written twice
    seen, unique = set(), []
    for e, d in out:
        key = str(d).lower()
        if key not in seen:
            seen.add(key)
            unique.append((e, d))
    return unique


def _games_entries(target: Target, entries):
    import gamebios
    s = gamebios.SETS[target.extra["set"]]
    # by what the file is, so it's found in any folder (e.g. a RetroArch-style flat "system" folder)
    return [e for e in entries if s.system in (e.system, e.ident.system) and e.ident.status == "verified"]


def _games_plan(target: Target, entries):
    """[(entry, <games folder>/<set>.zip)] when the vault has every piece; the zip is built from
    the vault's files at install time (they may be loose, zipped, or zipped twice)."""
    import gamebios
    key = target.extra["set"]
    mine = _games_entries(target, entries)
    if not mine or not gamebios.complete(key, gamebios.collect(e.path for e in mine).get(key, {})):
        return []
    return [(mine[0], target.folder / gamebios.SETS[key].zip_name)]


# ---------------------------------------------------------------------------
# Installing
# ---------------------------------------------------------------------------
def load_manifest() -> dict:
    try:
        return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_manifest(m: dict):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, MANIFEST_FILE)


def _md5_file(p: Path) -> Optional[str]:
    try:
        return biosdb.md5_of(p.read_bytes())
    except OSError:
        return None


def status_of(target: Target, entries) -> dict:
    """{'present': n, 'missing': n, 'conflict': n, 'items': [(entry, dest, state)]}"""
    items, counts = [], {"present": 0, "missing": 0, "conflict": 0}
    for e, d in plan(target, entries):
        if target.kind == "games":
            import gamebios                     # any zip with the right pieces will do for MAME
            chk = gamebios.check_zip(target.extra["set"], d)
            state = {"ok": "present", "missing": "missing"}.get(chk, "conflict")
        elif not d.exists():
            state = "missing"
        elif e.ident.status == "unverified":
            state = "present"                  # something's there already; an unverified file never replaces it
        else:
            state = "present" if _md5_file(d) == e.ident.md5 else "conflict"
        counts[state] += 1
        items.append((e, d, state))
    counts["items"] = items
    return counts


def install(target: Target, entries, log=print, dry=False) -> dict:
    """Copy what is missing.  Returns counts {'installed', 'present', 'conflict', 'failed'}."""
    manifest = load_manifest()
    counts = {"installed": 0, "present": 0, "conflict": 0, "failed": 0}
    if target.kind == "games" and target.extra.get("offline"):
        log(f"  {target.folder} is a link to a card that isn't inserted - skipped")
        return counts
    for e, d, state in status_of(target, entries)["items"]:
        if state == "present":
            counts["present"] += 1
            continue
        if state == "conflict":
            counts["conflict"] += 1
            log(f"  conflict: {d} is a different file - left as it is")
            continue
        if dry:
            log(f"  would install {d.name}  ({e.ident.label})")
            counts["installed"] += 1
            continue
        try:
            d.parent.mkdir(parents=True, exist_ok=True)
            tmp = d.with_name(d.name + ".part")
            if target.kind == "games":
                import gamebios
                key = target.extra["set"]
                data = gamebios.zip_bytes(key, gamebios.collect(x.path for x in _games_entries(target, entries))[key])
                tmp.write_bytes(data)
                md5 = biosdb.md5_of(data)
            else:
                shutil.copyfile(e.path, tmp)
                md5 = e.ident.md5
            os.replace(tmp, d)
            manifest[str(d)] = md5
            counts["installed"] += 1
            log(f"  installed {d}")
        except OSError as ex:
            counts["failed"] += 1
            log(f"  could not install {d}: {ex}")
    if not dry:
        save_manifest(manifest)
    return counts


REPORT_NAME = "bios_install_report.txt"


def install_all(entries, resolve=None, log=print, dry=False, report_dir=None) -> dict:
    """Install into every target. With report_dir, a report of what went where (and what each
    emulator still lacks) is written there as bios_install_report.txt; total["report"] is its path."""
    total = {"installed": 0, "present": 0, "conflict": 0, "failed": 0}
    lines = []

    def both(text):
        lines.append(text)
        log(text)

    targets = find_targets(resolve)
    if not targets:
        both("No emulator BIOS folders were found on this device.")
    for t in targets:
        both(f"{t.name}: {t.folder}")
        c = install(t, entries, both, dry)
        for k in total:
            total[k] += c[k]
        st = status_of(t, entries)
        n = st["present"] + st["missing"] + st["conflict"]
        lines.append(f"  in place: {st['present']} of {n}" + (" (preview: nothing was copied)" if dry else ""))
        for note in coverage(t, entries):
            lines.append(f"  still missing: {note}")
        lines.append("")
    both(f"BIOS install: {total['installed']} installed, {total['present']} already in place, "
         f"{total['conflict']} conflicts, {total['failed']} failed.")
    if report_dir:
        import platform
        from datetime import datetime as _dt
        path = Path(report_dir) / REPORT_NAME
        head = [f"BIOS install report - {platform.node() or 'this device'} - {_dt.now():%Y-%m-%d %H:%M}",
                f"Vault files: {len(entries)}", ""]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(head + lines) + "\n", encoding="utf-8")
            total["report"] = str(path)
        except OSError:
            pass
    return total


def uninstall(backup_dir: Path, log=print) -> int:
    """Move every file the app installed (and that is still unchanged) to the backup folder."""
    manifest = load_manifest()
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    dest_root = Path(backup_dir) / "bios_uninstalled" / stamp
    moved, keep = 0, {}
    for path, md5 in manifest.items():
        p = Path(path)
        if not p.exists():
            continue
        if _md5_file(p) != md5:
            keep[path] = md5           # changed since; not ours any more
            continue
        rel = re.sub(r"[:\\/]+", "_", str(p.parent)).strip("_")
        dst = dest_root / rel / p.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(dst))
        moved += 1
    save_manifest(keep)
    log(f"Removed {moved} installed BIOS file(s); they are in {dest_root}.")
    return moved


# ---------------------------------------------------------------------------
# Coverage: what a gap means in game terms
# ---------------------------------------------------------------------------
def coverage(target: Target, entries) -> List[str]:
    notes = []
    st = status_of(target, entries)
    installed = {e.ident.md5 for e, d, s in st["items"] if s == "present"}
    k = target.kind
    if k in ("duckstation", "retroarch", "emudeck"):
        regions = {e.ident.region for e in entries if e.system == biosdb.PS1
                   and e.ident.status in ("verified", "identified")}
        missing = [r for r in ("Japan", "USA", "Europe") if r not in regions]
        if missing:
            notes.append("No PS1 BIOS for " + ", ".join(missing) + " games in the vault")
    if k in ("retroarch", "emudeck"):
        names = {Path(n).name.lower() for e in entries if e.ident.status == "verified" for n in e.ident.names}
        lack = [n for n in ("scph5500.bin", "scph5501.bin", "scph5502.bin") if n not in names]
        if lack:
            notes.append("Beetle PSX needs " + ", ".join(lack))
    if k in ("pcsx2", "emudeck") and not any(e.system == biosdb.PS2 and e.ident.status in ("verified", "identified")
                                              for e in entries):
        notes.append("No PS2 BIOS in the vault")
    if k in ("xemu", "emudeck"):
        if not any(e.system == biosdb.XBOX and e.ident.size == 512 and e.ident.status == "verified" for e in entries):
            notes.append("No good MCPX boot ROM in the vault")
        if not any(e.system == biosdb.XBOX and e.ident.size in (262144, 1048576) for e in entries):
            notes.append("No Xbox flash BIOS in the vault")
    if k == "games":
        import gamebios
        s = gamebios.SETS[target.extra["set"]]
        if target.extra.get("offline"):
            notes.append("The card with these games isn't inserted")
        elif not st["items"]:
            have = gamebios.collect(e.path for e in _games_entries(target, entries)).get(target.extra["set"], {})
            if not have:
                notes.append(f"No {s.label} in the vault ({s.why})")
            else:
                lack = [s.part_labels.get(p, p) for p in s.parts if p not in have]
                notes.append(f"The vault has only part of the {s.label}: missing the {' and '.join(lack)}")
    if k == "dolphin" and not any(e.system == biosdb.GC for e in entries):
        notes.append("No GameCube IPL (optional: Dolphin boots games without it)")
    if st["conflict"]:
        notes.append(f"{st['conflict']} file(s) with the same name but different contents left alone")
    return notes
