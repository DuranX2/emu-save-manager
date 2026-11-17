"""
ps2folder.py - read and write PCSX2 *folder* memory cards.

A folder memory card is a directory (e.g. "Mcd001.ps2") that contains a file
named "_pcsx2_superblock".  Every save is a plain sub-folder holding the save's
files.  Alongside them PCSX2 keeps (see pcsx2/SIO/Memcard/MemoryCardFolder.cpp):

  <save>/_pcsx2_index            YAML: "$ROOT" (or legacy "%ROOT") with the save
                                 folder's timeCreated/timeModified, and one entry
                                 per file with order/timeCreated/timeModified.
                                 Times are Unix seconds of the card's own clock.
  <save>/_pcsx2_meta_directory   raw 512-byte directory entry, only when the
                                 folder's mode/attributes are non-standard or its
                                 name had to be cleaned for the host file system.
  <save>/_pcsx2_meta/<file>      the same for an individual file.
  _pcsx2_deleted_<name>          how PCSX2 itself "deletes" a save.

Anything starting with "_pcsx2_" is ignored by PCSX2 when it builds the card.

FolderCard has the same interface as ps2mc.PS2Card (list_saves, get_save,
import_save, delete_save, save_names, save), so the GUI and
ps2mc.transfer_saves work with both.  Changes are staged in memory and written
by save(); save folders that are replaced or deleted are moved into a trash
folder (when one is given) rather than destroyed.
"""

import calendar
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ps2mc import (DF_DIR, DF_EXISTS, DF_FILE, DF_HIDDEN, DF_PROTECTED, DF_PSX, MODE_DIR,
                   MODE_FILE, CardError, DirEntry, PS2Card, PSUFile, PSUSave, SaveInfo,
                   check_name, now_stamp, pack_entry, parse_entry, stamp_to_dt,
                   title_from_icon_sys)

SUPERBLOCK = "_pcsx2_superblock"
INDEX = "_pcsx2_index"
META_DIR = "_pcsx2_meta_directory"
META_FILES = "_pcsx2_meta"
PREFIX = "_pcsx2_"
TMP_PREFIX = "_pcsx2_tmp_"
SUPERBLOCK_SIZE = 8192                 # one erase block: 16 pages of 512 bytes, no ECC
STD_CAPACITY_KB = 8135                 # data clusters of a standard 8 MB card

_ILLEGAL = '\\%:|"<>'


# ---------------------------------------------------------------------------
# Names and times, exactly as PCSX2 converts them
# ---------------------------------------------------------------------------
def clean_name(name: str) -> str:
    """FileAccessHelper::CleanMemcardFilename: host-safe version of a card name."""
    out = "".join("_" if c in _ILLEGAL else c for c in name)
    stripped = out.rstrip(". ")
    return stripped + "_" * (len(out) - len(stripped))


def stamp_to_unix(stamp: bytes) -> int:
    """MemoryCardFileEntryDateTime::ToTime (the card clock is treated as UTC)."""
    dt = stamp_to_dt(stamp)
    if dt is None:
        return 0
    return calendar.timegm(dt.timetuple())


def unix_to_stamp(t) -> bytes:
    """MemoryCardFileEntryDateTime::FromTime."""
    try:
        g = time.gmtime(int(t))
    except (ValueError, OverflowError, OSError):
        g = time.gmtime(0)
    return bytes((0, g.tm_sec, g.tm_min, g.tm_hour, g.tm_mday, g.tm_mon)) + \
        g.tm_year.to_bytes(2, "little")


# ---------------------------------------------------------------------------
# A tiny reader/writer for the _pcsx2_index YAML files
# ---------------------------------------------------------------------------
# PCSX2 writes them with rapidyaml, either in block style
#     $ROOT:
#       timeCreated: 1700000000
#     icon.sys:
#       order: 1
# or in flow style: {$ROOT: {timeCreated: 0, timeModified: 0}}.  Only a mapping
# of mappings of scalars is ever used, which is all this handles.
def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] == "'":
        return s[1:-1].replace("''", "'")
    if len(s) >= 2 and s[0] == s[-1] == '"':
        return bytes(s[1:-1], "utf-8").decode("unicode_escape", errors="replace")
    return s


def _split_flow(s: str) -> List[str]:
    """Split the inside of a flow mapping on top-level commas."""
    parts, depth, quote, cur = [], 0, None, ""
    for c in s:
        if quote:
            cur += c
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
            cur += c
        elif c in "{[":
            depth += 1
            cur += c
        elif c in "}]":
            depth -= 1
            cur += c
        elif c == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += c
    if cur.strip():
        parts.append(cur)
    return parts


def _split_key(s: str):
    """'key: value' -> (key, value); handles quoted keys and colons inside names."""
    s = s.strip()
    if s[:1] in "'\"":
        q = s[0]
        i = 1
        while i < len(s):
            if s[i] == q:
                if q == "'" and s[i + 1:i + 2] == "'":
                    i += 2
                    continue
                break
            i += 1
        key, rest = s[:i + 1], s[i + 1:].lstrip()
        if not rest.startswith(":"):
            raise ValueError("bad key")
        return _unquote(key), rest[1:].strip()
    m = re.search(r":(\s|$)", s)
    if not m:
        raise ValueError("no key")
    return s[:m.start()].strip(), s[m.end():].strip()


def _parse_flow_map(s: str) -> Dict:
    s = s.strip()
    if not (s.startswith("{") and s.endswith("}")):
        raise ValueError("not a flow mapping")
    out = {}
    for part in _split_flow(s[1:-1]):
        if not part.strip():
            continue
        k, v = _split_key(part)
        out[k] = _parse_flow_map(v) if v.startswith("{") else _unquote(v)
    return out


def parse_index(text: str) -> Dict[str, Dict[str, str]]:
    text = text.lstrip("﻿")
    body = "\n".join(l for l in text.splitlines()
                     if l.strip() and not l.lstrip().startswith("#") and l.strip() != "---")
    if not body.strip():
        return {}
    if body.lstrip().startswith("{"):
        return _parse_flow_map(body)
    out, current = {}, None
    for line in body.splitlines():
        indent = len(line) - len(line.lstrip())
        k, v = _split_key(line)
        if indent == 0:
            if v.startswith("{"):
                out[k] = _parse_flow_map(v)
                current = None
            elif v:
                out[k] = _unquote(v)
                current = None
            else:
                current = out[k] = {}
        elif current is not None:
            current[k] = _unquote(v)
    return out


_PLAIN_KEY = re.compile(r"^[A-Za-z0-9_$][A-Za-z0-9_$.\- ()+=~^@,;!']*$")


def _yaml_key(k: str) -> str:
    if _PLAIN_KEY.match(k) and not k.endswith(" ") and ": " not in k and " #" not in k \
            and k.lower() not in ("null", "true", "false", "yes", "no", "on", "off", "~"):
        try:
            float(k)            # a key that looks like a number must be quoted
        except ValueError:
            return k
    return "'" + k.replace("'", "''") + "'"


def build_index(root_created: int, root_modified: int, files) -> str:
    """files: [(clean name, order, created, modified)]"""
    lines = ["$ROOT:", f"  timeCreated: {root_created}", f"  timeModified: {root_modified}"]
    for name, order, created, modified in files:
        lines += [f"{_yaml_key(name)}:", f"  order: {order}",
                  f"  timeCreated: {created}", f"  timeModified: {modified}"]
    return "\n".join(lines) + "\n"


def _int(d, key, default=None):
    try:
        return int(str(d.get(key)).strip())
    except (TypeError, ValueError, AttributeError):
        return default


# ---------------------------------------------------------------------------
# Detection and creation
# ---------------------------------------------------------------------------
def is_folder_card(path) -> bool:
    p = Path(path)
    return p.is_dir() and (p / SUPERBLOCK).is_file()


def formatted_superblock() -> bytes:
    """Block 0 of a freshly formatted standard 8 MB card, as PCSX2 stores it."""
    blank = PS2Card.create_blank()
    page0 = bytes(blank.raw[:512])
    return page0 + b"\xFF" * (SUPERBLOCK_SIZE - 512)


def superblock_is_formatted(data: bytes) -> bool:
    """FolderMemoryCard::IsFormatted (plus a length check, as fread would fail)."""
    return len(data) >= SUPERBLOCK_SIZE and data[0x16] == 0x6F


def create_folder_card(path):
    """A new, formatted, empty folder memory card."""
    p = Path(path)
    if p.exists():
        raise CardError(f"{p.name} already exists")
    p.mkdir(parents=True)
    (p / SUPERBLOCK).write_bytes(formatted_superblock())
    return FolderCard(p)


def open_card(path):
    """PS2Card for a card file, FolderCard for a folder card."""
    p = Path(path)
    if p.is_dir():
        if not is_folder_card(p):
            raise CardError(f"{p.name} is not a PCSX2 folder memory card (no {SUPERBLOCK})")
        return FolderCard(p)
    return PS2Card.load(p)


def _kb(nbytes: int) -> int:
    return -(-nbytes // 1024)


def _entries_kb(n_files: int) -> int:
    return -(-(n_files + 2) // 2)


# ---------------------------------------------------------------------------
# The folder card
# ---------------------------------------------------------------------------
class FolderCard:
    is_folder = True
    has_ecc = True
    limited = False          # PCSX2 only loads the running game's saves; no card-wide limit

    def __init__(self, path):
        self.path = Path(path)
        try:
            self._superblock = (self.path / SUPERBLOCK).read_bytes()
        except OSError as ex:
            raise CardError(f"Cannot read {SUPERBLOCK}: {ex}")
        self.formatted = superblock_is_formatted(self._superblock)
        self._dirs: Dict[str, str] = {}       # card name -> host folder name
        self._scan()
        self._added: Dict[str, PSUSave] = {}
        self._deleted: List[str] = []         # host folder names to remove on save()

    # ---- reading -----------------------------------------------------------------
    def _scan(self):
        self._dirs = {}
        try:
            children = sorted(self.path.iterdir(), key=lambda p: p.name.lower())
        except OSError as ex:
            raise CardError(f"Cannot list {self.path.name}: {ex}")
        for d in children:
            if d.name.startswith(PREFIX) or not d.is_dir():
                continue
            name = d.name
            meta = self._read_meta(d / META_DIR)
            if meta and meta.name:
                name = meta.name
            if name in self._dirs:           # two host folders claiming the same card name
                name = d.name
            self._dirs[name] = d.name

    @staticmethod
    def _read_meta(p: Path) -> Optional[DirEntry]:
        try:
            raw = p.read_bytes()
        except OSError:
            return None
        if len(raw) < 0x60:
            return None
        return parse_entry(raw.ljust(512, b"\0")[:512])

    @staticmethod
    def _read_index(folder: Path) -> Dict:
        try:
            return parse_index((folder / INDEX).read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            return {}

    def _files_in(self, folder: Path):
        """[(host file Path)], refusing nested folders, which no PS2 save uses."""
        files = []
        for f in folder.iterdir():
            if f.name.startswith(PREFIX):
                continue
            if f.is_dir():
                raise CardError(f"'{folder.name}' contains a sub-folder ({f.name}); "
                                "nested folders are not supported")
            files.append(f)
        return files

    def _header(self, host: str):
        """(PSUSave without files, parsed index) for a save folder."""
        folder = self.path / host
        index = self._read_index(folder)
        root = index.get("$ROOT") or index.get("%ROOT") or {}
        if not isinstance(root, dict):
            root = {}
        st = folder.stat()
        dmeta = self._read_meta(folder / META_DIR)
        if dmeta:
            mode, attr, name = dmeta.mode, dmeta.attr, dmeta.name or host
            created, modified = dmeta.created, dmeta.modified
        else:
            mode, attr, name = MODE_DIR, 0, host
            created = unix_to_stamp(_int(root, "timeCreated", int(st.st_ctime)))
            modified = unix_to_stamp(_int(root, "timeModified", int(st.st_mtime)))
        return PSUSave(name, mode | DF_EXISTS | DF_DIR, created, modified, attr), index

    def _load_save(self, host: str) -> PSUSave:
        folder = self.path / host
        save, index = self._header(host)
        listed = []
        legacy = 0
        for f in self._files_in(folder):
            entry = index.get(f.name) if isinstance(index.get(f.name), dict) else {}
            order = _int(entry, "order")
            if order is None:
                legacy -= 1
                order = legacy
            listed.append((order, f, entry))
        listed.sort(key=lambda t: (t[0], t[1].name))
        for _order, f, entry in listed:
            fst = f.stat()
            fmeta = self._read_meta(folder / META_FILES / f.name)
            if fmeta:
                fmode, fattr, fname = fmeta.mode, fmeta.attr, fmeta.name or f.name
                fc, fm = fmeta.created, fmeta.modified
            else:
                fmode, fattr, fname = MODE_FILE, 0, f.name
                fc = unix_to_stamp(_int(entry, "timeCreated", int(fst.st_ctime)))
                fm = unix_to_stamp(_int(entry, "timeModified", int(fst.st_mtime)))
            data = f.read_bytes()
            save.files.append(PSUFile(fname, fmode | DF_EXISTS | DF_FILE, fc, fm, fattr, data))
        return save

    # ---- PS2Card-compatible interface ---------------------------------------------------
    def save_names(self) -> List[str]:
        names = [n for n, h in self._dirs.items() if h not in self._deleted]
        return names + [n for n in self._added if n not in names]

    def list_saves(self) -> List[SaveInfo]:
        out = []
        for name in self.save_names():
            if name in self._added:
                s = self._added[name]
                out.append(self._info(s, s.title(), s.size_kb(), len(s.files)))
                continue
            host = self._dirs[name]
            folder = self.path / host
            try:
                files = self._files_in(folder)
                size = _entries_kb(len(files)) + sum(_kb(f.stat().st_size) for f in files)
                icon = next((f for f in files if f.name.lower() == "icon.sys"), None)
                title = title_from_icon_sys(icon.read_bytes()) if icon else ""
                skel, _index = self._header(host)
                out.append(self._info(skel, title, size, len(files)))
            except (CardError, OSError) as ex:
                out.append(SaveInfo(name, f"(cannot read: {ex})", 0, 0, None, False, False, False))
        return out

    @staticmethod
    def _info(s: PSUSave, title, size, nfiles) -> SaveInfo:
        return SaveInfo(s.dirname, title, size, nfiles, stamp_to_dt(s.modified),
                        bool(s.mode & DF_PROTECTED), bool(s.mode & DF_PSX), bool(s.mode & DF_HIDDEN))

    def get_save(self, dirname: str) -> PSUSave:
        if dirname in self._added:
            return self._added[dirname]
        host = self._dirs.get(dirname)
        if host is None or host in self._deleted:
            raise CardError(f"Save '{dirname}' not found")
        try:
            return self._load_save(host)
        except OSError as ex:
            raise CardError(f"Cannot read '{dirname}': {ex}")

    def free_clusters(self) -> int:
        return max(0, STD_CAPACITY_KB - self.used_kb())

    def used_kb(self) -> int:
        return sum(s.size_kb for s in self.list_saves())

    n_alloc = STD_CAPACITY_KB

    def delete_save(self, dirname: str):
        if dirname in self._added:
            del self._added[dirname]
            return
        host = self._dirs.get(dirname)
        if host is None or host in self._deleted:
            raise CardError(f"Save '{dirname}' not found")
        self._deleted.append(host)

    def import_save(self, save: PSUSave, overwrite: bool = False):
        check_name(save.dirname)
        names = [f.name for f in save.files]
        for n in names:
            check_name(n)
        hosts = [clean_name(n).casefold() for n in names]
        if len(set(hosts)) != len(hosts):
            raise CardError(f"'{save.dirname}' has file names that clash once made safe "
                            "for this computer's file system")
        if save.dirname in self.save_names():
            if not overwrite:
                raise CardError(f"'{save.dirname}' already exists on this card")
            self.delete_save(save.dirname)
        target = clean_name(save.dirname).casefold()
        for name in self.save_names():
            host = self._dirs.get(name) if name not in self._added else clean_name(name)
            if host and host.casefold() == target:
                raise CardError(f"'{save.dirname}' would use the same folder as '{name}' "
                                "(names differ only in case or in characters Windows can't use)")
        self._added[save.dirname] = save

    # ---- writing ----------------------------------------------------------------------
    def _write_save_folder(self, folder: Path, save: PSUSave):
        folder.mkdir()
        dir_mode = save.mode & 0xFFFF
        host_dir = clean_name(save.dirname)
        if host_dir != save.dirname or dir_mode != MODE_DIR or save.attr:
            (folder / META_DIR).write_bytes(pack_entry(DirEntry(
                dir_mode, 0, save.created, 0, 0, save.modified, save.attr, save.dirname)))
        index_files = []
        for order, f in enumerate(save.files, 1):
            host = clean_name(f.name)
            (folder / host).write_bytes(f.data)
            fmode = f.mode & 0xFFFF
            if host != f.name or fmode != MODE_FILE or f.attr:
                (folder / META_FILES).mkdir(exist_ok=True)
                (folder / META_FILES / host).write_bytes(pack_entry(DirEntry(
                    fmode, len(f.data), f.created, 0, 0, f.modified, f.attr, f.name)))
            index_files.append((host, order, stamp_to_unix(f.created), stamp_to_unix(f.modified)))
        (folder / INDEX).write_text(build_index(stamp_to_unix(save.created),
                                                stamp_to_unix(save.modified), index_files),
                                    encoding="utf-8", newline="\n")

    def _discard(self, host: str, trash_dir: Optional[Path]):
        src = self.path / host
        if not src.exists():
            return
        if trash_dir is not None:
            trash_dir.mkdir(parents=True, exist_ok=True)
            dest = trash_dir / host
            i = 2
            while dest.exists():
                dest = trash_dir / f"{host}_{i}"
                i += 1
            shutil.move(str(src), str(dest))
        else:                                   # what PCSX2 itself does
            dest = self.path / f"_pcsx2_deleted_{host}"
            if dest.exists():
                shutil.rmtree(dest)
            src.rename(dest)

    def save(self, path=None, trash_dir=None):
        """Apply staged changes.  Removed/replaced save folders go to trash_dir."""
        if path is not None and Path(path).resolve() != self.path.resolve():
            raise CardError("A folder card can only be saved in place")
        trash = Path(trash_dir) if trash_dir else None

        # 1. write new saves into temporary folders PCSX2 ignores
        staged = []
        try:
            for name, s in self._added.items():
                tmp = self.path / f"{TMP_PREFIX}{clean_name(name)}"
                if tmp.exists():
                    shutil.rmtree(tmp)
                self._write_save_folder(tmp, s)
                staged.append((name, tmp))
        except OSError as ex:
            for _n, tmp in staged:
                shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(self.path / f"{TMP_PREFIX}{clean_name(name)}", ignore_errors=True)
            raise CardError(f"Could not write to {self.path.name}: {ex}")

        # 2. a card PCSX2 created but never formatted would ignore every save
        if self._added and not self.formatted:
            (self.path / SUPERBLOCK).write_bytes(formatted_superblock())
            self.formatted = True

        # 3. remove deleted / replaced saves, 4. move the new ones into place
        try:
            for host in self._deleted:
                self._discard(host, trash)
            for name, tmp in staged:
                final = self.path / clean_name(name)
                if final.exists():            # a same-named folder not known as a save
                    self._discard(final.name, trash)
                tmp.rename(final)
        except OSError as ex:
            raise CardError(f"Could not update {self.path.name}: {ex}")

        self._added, self._deleted = {}, []
        self._scan()


# ---------------------------------------------------------------------------
# Whole-card helpers
# ---------------------------------------------------------------------------
def card_kind(path) -> str:
    return "folder" if Path(path).is_dir() else "file"
