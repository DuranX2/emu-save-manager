"""
safecopy.py - the safe-copy pieces shared by the folder-based save managers (PSP, …).

  * copy_tree_verified(src, dest): copy a folder into a staging folder next to dest, fsync every
    file, read everything back and compare, then swap it into place (the old dest goes to the
    safety folder first). Nothing is half-written in place.
  * Safety: a per-change folder under <backups>/<name>/<date-time>/ that replaced, moved-away and
    deleted things are moved into (never erased outright); prune() keeps the newest 20 changes.
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

SAFETY_KEEP = 20


class CopyError(Exception):
    pass


class Safety:
    def __init__(self, backup_dir, name):
        self.base = Path(backup_dir) / name
        self.dir = None

    def keep(self, path: Path, label: str) -> Path:
        """Move a file or folder into this change's safety folder (under `label`)."""
        if self.dir is None:
            self.dir = self.base / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            self.dir.mkdir(parents=True, exist_ok=True)
        dest = self.dir / label / Path(path).name
        n = 2
        while dest.exists():
            dest = self.dir / f"{label} ({n})" / Path(path).name
            n += 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))
        return dest

    def prune(self):
        if self.base.is_dir():
            for d in sorted(d for d in self.base.iterdir() if d.is_dir())[:-SAFETY_KEEP]:
                shutil.rmtree(d, ignore_errors=True)


def copy_tree_verified(src: Path, dest: Path, safety: Safety | None = None, label: str = "replaced") -> int:
    """Copy folder src to dest safely; returns the number of files copied."""
    src, dest = Path(src), Path(dest)
    stage = dest.with_name(dest.name + ".copying")
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    files = [p for p in src.rglob("*") if p.is_file()]
    try:
        for p in files:
            t = stage / p.relative_to(src)
            t.parent.mkdir(parents=True, exist_ok=True)
            data = p.read_bytes()
            with open(t, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            shutil.copystat(p, t)
        for p in files:
            if (stage / p.relative_to(src)).read_bytes() != p.read_bytes():
                raise CopyError(f"{p.name}: the copy didn't read back correctly")
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    if dest.exists():
        if safety is None:
            raise CopyError(f"{dest} already exists")
        safety.keep(dest, label)
    os.replace(stage, dest)
    return len(files)


def folder_stats(path: Path):
    """(total bytes, file count, newest modification time or 0)."""
    size = count = 0
    newest = 0.0
    for p in Path(path).rglob("*"):
        if p.is_file():
            st = p.stat()
            size += st.st_size
            count += 1
            newest = max(newest, st.st_mtime)
    return size, count, newest
