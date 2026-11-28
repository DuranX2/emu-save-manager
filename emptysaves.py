"""emptysaves.py - find save files that are empty (0 bytes) and the copies that can replace them.

An empty save holds no data at all. They were left behind by the 2026-11-17 sync problem (an
old rclone uploaded only empty files to OneDrive, and the next download put those over the real
saves), and can also come from a crash while an emulator was writing.

Where a good copy can still be:
  * <backup folder>/cloud_overwritten/<when>/<emulator>/<location>/<file> - every file a sync
    replaced with what came from the cloud is kept there first;
  * Save History - earlier versions kept at each sync.

Restoring never deletes anything: the empty file goes to <backup folder>/empty_saves/<when>/...,
and the restored copy gets the current time so the next sync uploads it everywhere.
No GUI dependencies.
"""

import fnmatch
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

import savehistory as sh

OVERWRITTEN = "cloud_overwritten"
SAFETY = "empty_saves"
STAMP_FORMAT = "%Y-%m-%d_%H-%M-%S"

# Empty on purpose: sync markers, placeholders and lock files, not saves.
NOT_SAVES = sh.SKIP_PATTERNS + [".gitkeep", ".keep", "*.lock", "lock", "*.lck", "portable.txt", ".nomedia"]


@dataclass
class Copy:
    kind: str                       # "overwritten" | "history"
    when: float                     # when it was kept
    size: int
    path: Path = None               # the file (overwritten) or the stored object (history)
    version: dict = None            # history only

    def describe(self) -> str:
        t = datetime.fromtimestamp(self.when).strftime("%Y-%m-%d %H:%M")
        if self.kind == "overwritten":
            return f"Kept when a sync replaced it, {t}"
        return f"Save History, {t}"


@dataclass
class Finding:
    emu: str
    label: str
    folder: Path
    rel: str                        # posix path inside the folder
    mtime: float                    # when the empty file was written
    copies: list = field(default_factory=list)      # newest first

    @property
    def path(self) -> Path:
        return self.folder / Path(*PurePosixPath(self.rel).parts)

    @property
    def best(self):
        return self.copies[0] if self.copies else None


def _not_save(name: str) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatchcase(low, p.lower()) for p in NOT_SAVES)


def empty_files(folder, exclude_dirs=()):
    """[(rel, mtime)] of the empty files in a save folder (links to folders are followed once)."""
    folder = Path(folder)
    out = []
    if not folder.is_dir():
        return out
    skip = {d.lower() for d in exclude_dirs}
    seen = set()
    for dirpath, dirs, files in os.walk(folder, followlinks=True):
        try:
            real = os.path.realpath(dirpath)
        except OSError:
            real = dirpath
        if real in seen:                                   # a link loop
            dirs[:] = []
            continue
        seen.add(real)
        dirs[:] = [d for d in dirs if d.lower() not in skip and not d.startswith(".esm")]
        for f in files:
            if _not_save(f):
                continue
            p = Path(dirpath) / f
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size == 0:
                out.append((p.relative_to(folder).as_posix(), st.st_mtime))
    out.sort()
    return out


def _stamp_time(name: str, fallback: float) -> float:
    try:
        return datetime.strptime(name, STAMP_FORMAT).timestamp()
    except ValueError:
        return fallback


def overwritten_copies(backup_dir, emu: str, label: str, rel: str) -> list:
    """Non-empty copies of one file kept by syncs, newest first."""
    root = Path(backup_dir) / OVERWRITTEN
    out = []
    try:
        stamps = [d for d in root.iterdir() if d.is_dir()]
    except OSError:
        return out
    parts = PurePosixPath(rel).parts
    for d in stamps:
        p = d / emu / label / Path(*parts)
        try:
            st = p.stat()
        except OSError:
            continue
        if p.is_file() and st.st_size > 0:
            out.append(Copy("overwritten", _stamp_time(d.name, st.st_mtime), st.st_size, p))
    out.sort(key=lambda c: c.when, reverse=True)
    return out


def history_copies(history, emu: str, label: str, rel: str) -> list:
    """Non-empty versions of one file in Save History, newest first."""
    if history is None:
        return []
    out = []
    for v in history.versions(sh.location_key(emu, label), rel):
        if int(v.get("size", 0)) > 0 and history.object_path(v["sha"]).is_file():
            out.append(Copy("history", float(v["taken"]), int(v["size"]),
                            history.object_path(v["sha"]), v))
    return out


def scan(locations, backup_dir, history_settings=None, cancelled=lambda: False) -> list:
    """locations: [(emu, label, folder, exclude_dirs)].  Findings with a copy come first."""
    try:
        history = sh.SaveHistory(sh.history_dir(backup_dir), history_settings)
    except Exception:  # noqa - a broken history index only means no history copies
        history = None
    found, seen = [], set()
    for emu, label, folder, exclude in locations:
        if cancelled():
            break
        if not folder:
            continue
        try:
            real = os.path.realpath(folder)
        except OSError:
            real = str(folder)
        if real in seen:                                   # two profiles sharing one folder
            continue
        seen.add(real)
        for rel, mtime in empty_files(folder, exclude):
            f = Finding(emu, label, Path(folder), rel, mtime)
            f.copies = sorted(overwritten_copies(backup_dir, emu, label, rel)
                              + history_copies(history, emu, label, rel),
                              key=lambda c: c.when, reverse=True)
            found.append(f)
    found.sort(key=lambda f: (not f.copies, f.emu.lower(), f.label.lower(), f.rel.lower()))
    return found


def restore(finding: Finding, copy: Copy, backup_dir, now=None) -> Path:
    """Put `copy` back in place of the empty file. The empty file is moved to the safety folder
    first (never deleted). Returns the safety path of the empty file (None if it had gone)."""
    now = time.time() if now is None else now
    target = finding.path
    if not copy.path or not Path(copy.path).is_file():
        raise FileNotFoundError("The kept copy is no longer there.")
    if target.is_file() and target.stat().st_size > 0:
        raise ValueError(f"{finding.rel} isn't empty any more; it was left alone.")
    kept = None
    stamp = datetime.fromtimestamp(now).strftime(STAMP_FORMAT)
    if target.is_file():
        kept = Path(backup_dir) / SAFETY / stamp / finding.emu / finding.label / Path(*PurePosixPath(finding.rel).parts)
        kept.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, kept)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".esm_tmp")
    shutil.copyfile(copy.path, tmp)
    if tmp.stat().st_size != copy.size:
        tmp.unlink()
        raise OSError(f"Copying {finding.rel} didn't complete; nothing was changed.")
    os.utime(tmp, (now, now))                          # the newest copy: the next sync uploads it
    os.replace(tmp, target)
    return kept


def locations_of(app) -> list:
    """Every save folder the app knows on this device: [(emu, label, folder, exclude_dirs)]."""
    out = []
    for emu, prof in app.profiles.items():
        try:
            resolved = app.resolve(emu)
        except Exception:  # noqa
            continue
        for loc in prof.get("locations", []):
            folder = resolved.get(loc["label"])
            if folder and Path(folder).is_dir():
                out.append((emu, loc["label"], Path(folder), tuple(loc.get("exclude_dirs", ()))))
    return out
