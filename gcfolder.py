"""
gcfolder.py - GameCube saves in Dolphin's GCI folders (Dolphin's default memory card type).

Dolphin keeps each GameCube save as its own .gci file in User/GC/<region>/Card A (and Card B).
This module lists those saves and copies, moves, deletes and exports them safely. No Tk here.

  * Copies are written to a temporary file, flushed, read back and compared before they replace
    anything; the finished file is read back once more.
  * Nothing is erased outright: replaced, moved-away and deleted saves go to
    <backup folder>/_gc_card_safety/<date-time>/ (the newest 20 changes are kept).
  * Saves keep the game's own rules: a save marked "no copy" isn't copied to another card and one
    marked "no move" isn't moved (exporting a backup to the exports folder is always allowed).
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import saveinfo

REGIONS = ("USA", "EUR", "JAP")
SLOTS = ("Card A", "Card B")
SAFETY_KEEP = 20
GC_EPOCH = datetime(2000, 1, 1)
REGION_OF_LETTER = {"E": "USA", "J": "JAP", "P": "EUR", "D": "EUR", "F": "EUR", "S": "EUR",
                    "I": "EUR", "U": "EUR", "X": "EUR", "Y": "EUR", "H": "EUR"}


class GCError(Exception):
    pass


@dataclass
class Location:
    label: str
    path: Path
    kind: str = "card"               # card | exports
    region: str = ""
    slot: str = ""

    @property
    def exists(self):
        return self.path.is_dir()


@dataclass
class GCFile:
    path: Path
    save: saveinfo.GCSave
    size: int

    @property
    def name(self):
        return self.path.name

    @property
    def key(self):
        """Which save this is on a card: game ID + the save's own file name."""
        return (self.save.game_id, self.save.filename)

    @property
    def modified(self):
        return GC_EPOCH + timedelta(seconds=self.save.modified) if self.save.modified else None

    @property
    def region(self):
        return REGION_OF_LETTER.get(self.save.game_id[3:4], "")


@dataclass
class Report:
    done: list = field(default_factory=list)          # GCFile copied / moved / deleted / exported
    skipped: list = field(default_factory=list)
    refused: list = field(default_factory=list)       # (GCFile, reason)
    replaced: list = field(default_factory=list)
    cancelled: bool = False
    safety: Path | None = None


def find_locations(gc_root, exports_dir=None) -> list:
    """Card A / Card B for each region that exists (Card A also when the region is new), plus the
    exports folder."""
    out = []
    root = Path(gc_root) if gc_root else None
    if root is not None and root.is_dir():
        regions = [r for r in REGIONS if (root / r).is_dir()]
        extra = sorted(p.name for p in root.iterdir() if p.is_dir() and p.name not in REGIONS
                       and any((p / s).is_dir() for s in SLOTS))
        if not regions and not extra:
            regions = ["USA"]
        for region in regions + extra:
            for slot in SLOTS:
                path = root / region / slot
                out.append(Location(f"{slot} ({region})", path, "card", region, slot))
    if exports_dir is not None:
        out.append(Location("Exported saves", Path(exports_dir), "exports"))
    return out


def list_folder(folder) -> tuple:
    """([GCFile] sorted by title, [(file name, problem)])."""
    files, problems = [], []
    folder = Path(folder)
    if not folder.is_dir():
        return files, problems
    for p in sorted(folder.iterdir()):
        if not p.is_file() or p.suffix.lower() != ".gci":
            continue
        s = saveinfo.parse_gci(p)
        if s is None:
            problems.append((p.name, "not a GameCube save"))
            continue
        files.append(GCFile(p, s, p.stat().st_size))
    files.sort(key=lambda f: ((f.save.title or f.save.game_id).lower(), f.save.detail.lower(), f.name))
    return files, problems


def blocks_used(files) -> int:
    return sum(f.save.blocks for f in files)


# ---------------------------------------------------------------------------
# Safe writing
# ---------------------------------------------------------------------------
def _fsync_dir(d: Path):
    if os.name == "nt":
        return
    try:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def write_verified(data: bytes, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    if tmp.read_bytes() != data:
        tmp.unlink(missing_ok=True)
        raise GCError(f"{dest.name}: the copy didn't read back correctly")
    os.replace(tmp, dest)
    _fsync_dir(dest.parent)
    if dest.read_bytes() != data:
        raise GCError(f"{dest.name}: the copy didn't read back correctly")


class Safety:
    """One change's safety folder, created on first use."""

    def __init__(self, backup_dir):
        self.base = Path(backup_dir) / "_gc_card_safety"
        self.dir = None

    def keep(self, path: Path, where: str) -> Path:
        if self.dir is None:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            self.dir = self.base / stamp
            self.dir.mkdir(parents=True, exist_ok=True)
        dest = self.dir / where / path.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        n = 2
        while dest.exists():
            dest = dest.with_name(f"{path.stem} ({n}){path.suffix}")
            n += 1
        shutil.move(str(path), str(dest))
        return dest

    def prune(self):
        if not self.base.is_dir():
            return
        dirs = sorted((d for d in self.base.iterdir() if d.is_dir()), key=lambda d: d.name)
        for d in dirs[:-SAFETY_KEEP]:
            shutil.rmtree(d, ignore_errors=True)


def _where(path: Path) -> str:
    """'USA-Card A' for a card folder (so the safety folder says where a file came from)."""
    return f"{path.parent.parent.name}-{path.parent.name}"


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------
def transfer(files, dest_dir, move: bool, backup_dir, conflict=None) -> Report:
    """Copy (or move) saves into dest_dir. conflict(file, existing GCFile) -> 'replace' | 'skip' |
    'cancel' decides what happens when the save is already there (default: skip)."""
    rep = Report()
    dest_dir = Path(dest_dir)
    safety = Safety(backup_dir)
    existing, _ = list_folder(dest_dir)
    by_key = {f.key: f for f in existing}
    by_name = {f.name.lower(): f for f in existing}
    for f in files:
        if f.path.parent.resolve() == dest_dir.resolve() if dest_dir.exists() else False:
            rep.refused.append((f, "it's already on this card"))
            continue
        if move and f.save.no_move:
            rep.refused.append((f, "the game marked it \"no move\""))
            continue
        if not move and f.save.no_copy:
            rep.refused.append((f, "the game marked it \"no copy\""))
            continue
        old = by_key.get(f.key) or by_name.get(f.name.lower())
        if old is not None:
            choice = conflict(f, old) if conflict else "skip"
            if choice == "cancel":
                rep.cancelled = True
                break
            if choice != "replace":
                rep.skipped.append(f)
                continue
        data = f.path.read_bytes()
        target = dest_dir / f.name
        if old is not None:
            safety.keep(old.path, _where(old.path) + "-replaced")
            rep.replaced.append(old)
            if old.path != target and target.exists():
                safety.keep(target, _where(target) + "-replaced")
        write_verified(data, target)
        if move:
            if target.read_bytes() != data:                # belt and braces before touching the source
                raise GCError(f"{f.name}: the copy couldn't be verified, so the original was kept")
            safety.keep(f.path, _where(f.path) + "-moved")
        rep.done.append(f)
        new = GCFile(target, f.save, len(data))
        by_key[f.key] = new
        by_name[target.name.lower()] = new
    rep.safety = safety.dir
    safety.prune()
    return rep


def delete(files, backup_dir) -> Report:
    rep = Report()
    safety = Safety(backup_dir)
    for f in files:
        safety.keep(f.path, _where(f.path) + "-deleted")
        rep.done.append(f)
    rep.safety = safety.dir
    safety.prune()
    return rep


def export(files, dest_dir) -> Report:
    """Copy saves into a backup folder. An identical file already there is skipped; a different
    one with the same name is kept and the new copy gets a numbered name."""
    rep = Report()
    dest_dir = Path(dest_dir)
    for f in files:
        data = f.path.read_bytes()
        target = dest_dir / f.name
        n = 2
        while target.exists():
            if target.read_bytes() == data:
                break
            target = dest_dir / f"{f.path.stem} ({n}){f.path.suffix}"
            n += 1
        if target.exists():
            rep.skipped.append(f)
            continue
        write_verified(data, target)
        rep.done.append(f)
    return rep
