"""
gamebios.py - BIOS files that have to sit beside the games, not in an emulator's BIOS folder.

Some systems are run by MAME, which looks for the system's BIOS set (a zip named after the
system) in the games folder it was given: EmuDeck/ES-DE start Game.com games with
`mame -rompath <games folder> gamecom -cartridge1 <game>`, and RetroArch's MAME core looks next
to the game too. So the set has to be in Emulation/roms/<system>/ on every machine.

The pieces are recognised by contents (size + CRC32, the way MAME checks them), wherever they
are: loose files under any name, inside a zip, or inside a zip inside a zip (No-Intro and TOSEC
packs ship them like that). The set is then written as one zip with MAME's names, always byte
for byte the same, so the copies on every machine match.

Only the user's own files: nothing is downloaded. Nothing is ever overwritten or deleted.
No GUI.
"""

import io
import os
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Optional

MAX_READ = 64 * 1024 * 1024
ZIP_TIME = (1996, 10, 1, 0, 0, 0)          # fixed, so the same pieces always make the same zip


@dataclass(frozen=True)
class GameBios:
    key: str                     # the games folder's system (romlibrary.canonical)
    system: str                  # the vault's folder for it
    zip_name: str                # what MAME looks for
    parts: Dict[str, tuple]      # MAME's name inside the zip -> (size, crc32)
    label: str
    why: str
    part_labels: Dict[str, str] = field(default_factory=dict)


SETS = {
    "gamecom": GameBios(
        key="gamecom", system="Tiger - Game.com", zip_name="gamecom.zip",
        parts={"internal.bin": (0x1000, 0xA0CEC361), "external.bin": (0x40000, 0xE235A589)},
        label="Game.com BIOS",
        why="MAME runs Game.com and looks for gamecom.zip beside the games",
        part_labels={"internal.bin": "internal ROM", "external.bin": "external ROM"}),
}
BY_SYSTEM: Dict[str, GameBios] = {}
_PARTS: Dict[tuple, tuple] = {}            # (size, crc) -> (set, name)


def reindex():
    """Rebuild the lookups after SETS changes (tests add their own sets)."""
    BY_SYSTEM.clear()
    BY_SYSTEM.update({s.system: s for s in SETS.values()})
    _PARTS.clear()
    _PARTS.update({v: (s, name) for s in SETS.values() for name, v in s.parts.items()})


reindex()


def folder_files(key: str) -> set:
    """File names in a games folder that are its BIOS set, not games (lower case)."""
    s = SETS.get(key)
    return {s.zip_name.lower()} if s else set()


# ---------------------------------------------------------------------------
# Finding the pieces
# ---------------------------------------------------------------------------
def _crc(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def pieces_in(name: str, data: bytes, depth: int = 0) -> Dict[str, Dict[str, bytes]]:
    """{set key: {part name: bytes}} found in one file's contents (a zip is looked into, and a
    zip inside it too)."""
    found: Dict[str, Dict[str, bytes]] = {}
    hit = _PARTS.get((len(data), _crc(data)))
    if hit:
        found.setdefault(hit[0].key, {})[hit[1]] = data
        return found
    if depth > 1 or not data[:4] == b"PK\x03\x04":
        return found
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for i in z.infolist():
                if i.is_dir() or i.file_size > MAX_READ:
                    continue
                if (i.file_size, i.CRC) in _PARTS or i.filename.lower().endswith(".zip"):
                    inner = z.read(i)                    # the zip checks the CRC as it reads
                    for k, parts in pieces_in(i.filename, inner, depth + 1).items():
                        found.setdefault(k, {}).update(parts)
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, EOFError):
        pass
    return found


def pieces_in_file(path: Path) -> Dict[str, Dict[str, bytes]]:
    try:
        size = path.stat().st_size
        if size > MAX_READ:
            return {}
        if not path.name.lower().endswith(".zip") and not any(size == v[0] for v in _PARTS):
            return {}                                    # can't be a piece; don't read it
        return pieces_in(path.name, path.read_bytes())
    except OSError:
        return {}


def collect(paths: Iterable[Path]) -> Dict[str, Dict[str, bytes]]:
    out: Dict[str, Dict[str, bytes]] = {}
    for p in paths:
        for k, parts in pieces_in_file(Path(p)).items():
            out.setdefault(k, {}).update(parts)
    return out


def complete(key: str, parts: dict) -> bool:
    return set(SETS[key].parts) <= set(parts)


def identify(name: str, data: bytes):
    """A biosdb.Identity for a piece or a set, or None. (Never installed into emulator BIOS
    folders: names stays empty; the games-folder target installs these.)"""
    import biosdb
    found = pieces_in(name, data)
    if not found:
        return None
    key, parts = next(iter(found.items()))
    s = SETS[key]
    if complete(key, parts):
        label = f"{s.label} (complete: {s.zip_name})"
    else:
        label = f"{s.label}, " + " + ".join(s.part_labels.get(p, p) for p in sorted(parts)) + \
                f" (part of {s.zip_name})"
    return biosdb.Identity(s.system, label, "verified", biosdb.md5_of(data), len(data), (), "")


# ---------------------------------------------------------------------------
# The set as MAME wants it
# ---------------------------------------------------------------------------
def zip_bytes(key: str, parts: dict) -> bytes:
    """The set as one zip with MAME's names; the same pieces always give the same bytes."""
    s = SETS[key]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in s.parts:                             # MAME's order
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, parts[name], compresslevel=9)
    return buf.getvalue()


def check_zip(key: str, path: Path) -> str:
    """'ok' (every piece MAME checks is in it), 'incomplete', 'missing' or 'unreadable'."""
    if not path.exists():
        return "missing"
    try:
        with zipfile.ZipFile(path) as z:
            have = {(i.file_size, i.CRC) for i in z.infolist()}
    except (zipfile.BadZipFile, OSError):
        return "unreadable"
    return "ok" if set(SETS[key].parts.values()) <= have else "incomplete"


# ---------------------------------------------------------------------------
# Games folders on this machine
# ---------------------------------------------------------------------------
def roms_roots(extra=()) -> list:
    """Folders holding one folder per system: EmuDeck's Emulation/roms and the ROM Library's
    local folder (the same folder on most machines)."""
    import cloudsync as cs
    roots = []
    settings = cs.emudeck_settings()
    if settings.get("emulationPath"):
        roots.append(Path(settings["emulationPath"]) / "roms")
    roots += [Path(r) / "roms" for r in cs.emudeck_roots()]
    try:
        import romlibrary
        local = romlibrary.load_config().get("local_roms")
        if local:
            roots.append(Path(local))
    except Exception:
        pass
    roots += [Path(p) for p in extra]
    out, seen = [], set()
    for r in roots:
        k = os.path.normcase(os.path.abspath(str(r)))
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def games_folders(key: str, roots) -> list:
    """[(folder, offline)] for this system under the roots. A folder that's a link to a card
    that isn't in is offline (it's reported, not written to)."""
    import romlibrary
    out, seen = [], set()
    for r in roots:
        try:
            names = sorted(os.listdir(r))
        except OSError:
            continue
        for n in names:
            p = Path(r) / n
            if romlibrary.canonical(n) != key:
                continue
            if p.is_dir():
                k = os.path.normcase(os.path.realpath(p))
                if k not in seen:
                    seen.add(k)
                    out.append((p, False))
            elif p.is_symlink():
                out.append((p, True))
    return out
