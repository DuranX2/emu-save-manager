"""
wiinand.py - Wii saves in Dolphin's emulated system memory (NAND), and backups of them.

Dolphin keeps each game's save as a folder: Wii/title/<type>/<id>/data/, with banner.bin (the
title, subtitle, banner and up to 8 animated icon frames) and the game's own files. Backups live
in <backup folder>/Wii saves/<TYPE>-<ID>/data/ (one per game, like a copy on an SD card), as plain
folders: no console encryption is involved, so Wii console keys are never needed.

Copying stages the whole folder next to its destination, reads every file back and compares it,
and only then swaps it into place. Nothing is erased outright: replaced, moved-away and deleted
saves go to <backup folder>/_wii_safety/<date-time>/ (newest 20 changes kept). Dolphin gives
files it has no ownership records for read/write access, so restored folders work as they are.
System software (IOS, the System Menu, system channels) is never listed.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import struct
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import saveinfo

SYSTEM_TYPES = {"00000001", "00010002", "00010008"}    # IOS & System Menu, system channels, hidden
TYPE_NAMES = {"00010000": "Disc game", "00010001": "Channel", "00010004": "Disc game channel",
              "00010005": "Downloadable content"}
BNR_SZ, ICON_SZ = 0x60A0, 0x1200
WII_BLOCK = 128 * 1024                                   # the Wii counts save sizes in 128 KB blocks
SAFETY_KEEP = 20
_HEX8 = re.compile(r"^[0-9a-fA-F]{8}$")


class WiiError(Exception):
    pass


@dataclass
class WiiSave:
    hi: str
    lo: str
    path: Path                       # the data folder
    kind: str                        # nand | backup
    title: str = ""
    subtitle: str = ""
    banner: saveinfo.Icon | None = None
    frames: list = field(default_factory=list)          # [(Icon, ticks)]
    bounce: bool = False
    no_copy: bool = False
    size: int = 0
    n_files: int = 0
    modified: datetime | None = None

    @property
    def key(self):
        return f"{self.hi}-{self.lo}".upper()

    @property
    def game_code(self):
        return saveinfo.wii_game_code(self.lo)

    @property
    def blocks(self):
        return max(1, -(-self.size // WII_BLOCK))

    @property
    def type_name(self):
        return TYPE_NAMES.get(self.hi.lower(), "Title")


def parse_banner(data: bytes):
    """(title, subtitle, banner, [(icon, ticks)], bounce, no_copy) or None."""
    if len(data) < BNR_SZ or data[:4] != b"WIBN":
        return None
    flags, speed = struct.unpack_from(">IH", data, 4)

    def u16(b):
        try:
            return " ".join(b.decode("utf-16-be").split("\0", 1)[0].split())
        except UnicodeDecodeError:
            return ""
    title, sub = u16(data[0x20:0x60]), u16(data[0x60:0xA0])
    px = saveinfo._decode_rgb5a3(data[0xA0:BNR_SZ], 192, 64)
    banner = saveinfo.Icon("rgba", px, 192, 64) if px else None
    n_icons = min(8, (len(data) - BNR_SZ) // ICON_SZ)
    frames = []
    for i in range(n_icons):
        ticks = (speed >> (2 * i)) & 3
        if ticks == 0 and i > 0:
            break
        o = BNR_SZ + i * ICON_SZ
        px = saveinfo._decode_rgb5a3(data[o:o + ICON_SZ], 48, 48)
        if px:
            frames.append((saveinfo.Icon("rgba", px, 48, 48), max(1, ticks)))
        if ticks == 0:
            break
    return title, sub, banner, frames, bool(flags & 0x10), bool(flags & 0x01)


def _describe(hi, lo, data_dir: Path, kind) -> WiiSave:
    s = WiiSave(hi.lower(), lo.lower(), data_dir, kind)
    newest = 0.0
    for p in data_dir.rglob("*"):
        if p.is_file():
            st = p.stat()
            s.size += st.st_size
            s.n_files += 1
            newest = max(newest, st.st_mtime)
    s.modified = datetime.fromtimestamp(newest) if newest else None
    b = data_dir / "banner.bin"
    if b.is_file():
        try:
            info = parse_banner(b.read_bytes()[:BNR_SZ + 8 * ICON_SZ])
        except OSError:
            info = None
        if info:
            s.title, s.subtitle, s.banner, s.frames, s.bounce, s.no_copy = info
    return s


def list_nand(wii_root) -> list:
    """Game saves in Dolphin's Wii folder (title/<type>/<id>/data with files in it)."""
    out = []
    root = Path(wii_root) / "title" if wii_root else None
    if root is None or not root.is_dir():
        return out
    for hi_dir in sorted(root.iterdir()):
        if not hi_dir.is_dir() or not _HEX8.match(hi_dir.name) or hi_dir.name.lower() in SYSTEM_TYPES:
            continue
        for lo_dir in sorted(hi_dir.iterdir()):
            data = lo_dir / "data"
            if _HEX8.match(lo_dir.name) and data.is_dir() and any(data.iterdir()):
                out.append(_describe(hi_dir.name, lo_dir.name, data, "nand"))
    out.sort(key=lambda s: ((s.title or s.game_code).lower(), s.key))
    return out


def list_backups(folder) -> list:
    out = []
    folder = Path(folder)
    if not folder.is_dir():
        return out
    for d in sorted(folder.iterdir()):
        m = re.match(r"^([0-9a-fA-F]{8})-([0-9a-fA-F]{8})$", d.name)
        if m and (d / "data").is_dir() and any((d / "data").iterdir()):
            out.append(_describe(m.group(1), m.group(2), d / "data", "backup"))
    out.sort(key=lambda s: ((s.title or s.game_code).lower(), s.key))
    return out


def nand_path(wii_root, s: WiiSave) -> Path:
    return Path(wii_root) / "title" / s.hi / s.lo / "data"


def backup_path(folder, s: WiiSave) -> Path:
    return Path(folder) / s.key / "data"


# ---------------------------------------------------------------------------
# Safe copying
# ---------------------------------------------------------------------------
class Safety:
    def __init__(self, backup_dir):
        self.base = Path(backup_dir) / "_wii_safety"
        self.dir = None

    def keep(self, data_dir: Path, label: str):
        if self.dir is None:
            self.dir = self.base / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            self.dir.mkdir(parents=True, exist_ok=True)
        dest = self.dir / label / "data"
        n = 2
        while dest.exists():
            dest = self.dir / f"{label} ({n})" / "data"
            n += 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(data_dir), str(dest))
        return dest

    def prune(self):
        if self.base.is_dir():
            for d in sorted(d for d in self.base.iterdir() if d.is_dir())[:-SAFETY_KEEP]:
                shutil.rmtree(d, ignore_errors=True)


def _copy_tree_verified(src: Path, dest: Path):
    """Copy src into a staging folder next to dest, verify every file, then put it in place."""
    stage = dest.with_name(dest.name + ".copying")
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    files = [p for p in src.rglob("*") if p.is_file()]
    for p in files:
        rel = p.relative_to(src)
        t = stage / rel
        t.parent.mkdir(parents=True, exist_ok=True)
        data = p.read_bytes()
        with open(t, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        shutil.copystat(p, t)
    for p in files:                                       # read everything back
        if (stage / p.relative_to(src)).read_bytes() != p.read_bytes():
            shutil.rmtree(stage, ignore_errors=True)
            raise WiiError(f"{p.name}: the copy didn't read back correctly")
    return stage, len(files)


@dataclass
class Report:
    done: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    refused: list = field(default_factory=list)
    cancelled: bool = False
    safety: Path | None = None


def transfer(saves, dest_for, move: bool, backup_dir, existing=None, conflict=None, info=True) -> Report:
    """Copy (or move) save folders. dest_for(save) -> destination data folder;
    existing(save) -> the WiiSave already there (or None); conflict(save, old) -> replace/skip/cancel."""
    rep = Report()
    safety = Safety(backup_dir)
    for s in saves:
        dest = Path(dest_for(s))
        if dest.resolve() == s.path.resolve():
            rep.refused.append((s, "it's already there"))
            continue
        old = existing(s) if existing else (_describe(s.hi, s.lo, dest, "") if dest.is_dir() else None)
        if old is not None:
            choice = conflict(s, old) if conflict else "skip"
            if choice == "cancel":
                rep.cancelled = True
                break
            if choice != "replace":
                rep.skipped.append(s)
                continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        stage, _n = _copy_tree_verified(s.path, dest)
        if dest.exists():
            safety.keep(dest, f"{s.key}-replaced")
        os.replace(stage, dest)
        if info and dest.parent.name.upper() == s.key:    # a backup: note what it is
            meta = {"title": s.title, "subtitle": s.subtitle, "game": s.game_code,
                    "saved": datetime.now().isoformat(timespec="seconds")}
            (dest.parent / "info.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        if move:
            safety.keep(s.path, f"{s.key}-moved")
            _tidy_backup(s)
        rep.done.append(s)
    rep.safety = safety.dir
    safety.prune()
    return rep


def _tidy_backup(s: WiiSave):
    """After a backup's data folder has gone, drop its note and the empty folder."""
    if s.kind != "backup":
        return
    folder = s.path.parent
    (folder / "info.json").unlink(missing_ok=True)
    try:
        folder.rmdir()
    except OSError:
        pass


def delete(saves, backup_dir) -> Report:
    rep = Report()
    safety = Safety(backup_dir)
    for s in saves:
        safety.keep(s.path, f"{s.key}-deleted")
        _tidy_backup(s)
        rep.done.append(s)
    rep.safety = safety.dir
    safety.prune()
    return rep
