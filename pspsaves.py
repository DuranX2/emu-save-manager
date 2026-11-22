"""
pspsaves.py - PSP saves on PPSSPP's memory stick (PSP/SAVEDATA/<folder>), and backups of them.

Each save is a folder named after the game's ID (ULUS10041DATA00, …) holding PARAM.SFO (the game's
title, the save's title and a detail line), ICON0.PNG (its icon), often PIC1.PNG (a full-screen
background picture) and SND0.AT3, plus the game's own (often encrypted) data files. Folders are
copied as they are, so PPSSPP reads them exactly as before: nothing is decrypted or re-signed.

Backups live in <backup folder>/PSP saves/<folder>. Copies are staged, read back and swapped in
(safecopy.py); replaced, moved-away and deleted saves go to <backup folder>/_psp_safety/.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import saveinfo
import safecopy

SAFETY_NAME = "_psp_safety"
_PNG = b"\x89PNG\r\n\x1a\n"
_ID = re.compile(r"^([A-Z]{4})(\d{5})")
REGIONS = {"US": "America", "UC": "America", "ES": "Europe", "EC": "Europe", "JS": "Japan", "JM": "Japan",
           "AS": "Asia", "KS": "Korea", "NP": ""}


class PSPError(Exception):
    pass


@dataclass
class PSPSave:
    name: str                        # folder name
    path: Path
    kind: str                        # stick | backup
    title: str = ""                  # the game's title
    save_title: str = ""
    detail: str = ""
    icon: bytes = b""                # ICON0.PNG
    background: bytes = b""          # PIC1.PNG
    size: int = 0
    n_files: int = 0
    modified: datetime | None = None
    sfo: dict = field(default_factory=dict)

    @property
    def key(self):
        return self.name.upper()

    @property
    def game_id(self):
        m = _ID.match(self.name.upper())
        return f"{m.group(1)}{m.group(2)}" if m else self.name[:9].upper()

    @property
    def region(self):
        gid = self.game_id
        if gid.startswith("NP"):                      # PSN titles: NPUH, NPEH, NPJH …
            return {"U": "America", "E": "Europe", "J": "Japan", "H": "Asia"}.get(gid[2:3], "")
        return REGIONS.get(gid[2:4], "")

    @property
    def label(self):
        return self.title or self.game_id


def _read_png(p: Path) -> bytes:
    try:
        data = p.read_bytes()[:2_000_000]
    except OSError:
        return b""
    return data if data[:8] == _PNG else b""


def describe(folder: Path, kind: str) -> PSPSave:
    s = PSPSave(folder.name, folder, kind)
    files = {p.name.upper(): p for p in folder.iterdir() if p.is_file()}
    if "PARAM.SFO" in files:
        s.sfo = saveinfo.parse_sfo(files["PARAM.SFO"])
        s.title = s.sfo.get("TITLE", "")
        s.save_title = s.sfo.get("SAVEDATA_TITLE", "")
        s.detail = (s.sfo.get("SAVEDATA_DETAIL", "") or "").strip()
    if "ICON0.PNG" in files:
        s.icon = _read_png(files["ICON0.PNG"])
    if "PIC1.PNG" in files:
        s.background = _read_png(files["PIC1.PNG"])
    s.size, s.n_files, newest = safecopy.folder_stats(folder)
    s.modified = datetime.fromtimestamp(newest) if newest else None
    return s


def list_saves(folder, kind="stick") -> list:
    """Save folders (ones with a PARAM.SFO) in a SAVEDATA or backups folder."""
    out = []
    folder = Path(folder) if folder else None
    if folder is None or not folder.is_dir():
        return out
    for d in sorted(folder.iterdir()):
        if d.is_dir() and not d.name.endswith(".copying") and any(
                p.name.upper() == "PARAM.SFO" for p in d.iterdir() if p.is_file()):
            out.append(describe(d, kind))
    out.sort(key=lambda s: (s.label.lower(), s.name))
    return out


@dataclass
class Report:
    done: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    refused: list = field(default_factory=list)
    cancelled: bool = False
    safety: Path | None = None


def transfer(saves, dest_dir, move: bool, backup_dir, conflict=None) -> Report:
    """Copy (or move) save folders into dest_dir. conflict(save, existing) -> replace/skip/cancel."""
    rep = Report()
    safety = safecopy.Safety(backup_dir, SAFETY_NAME)
    dest_dir = Path(dest_dir)
    for s in saves:
        dest = dest_dir / s.name
        if dest.exists() and dest.resolve() == s.path.resolve():
            rep.refused.append((s, "it's already there"))
            continue
        if dest.is_dir():
            choice = conflict(s, describe(dest, "")) if conflict else "skip"
            if choice == "cancel":
                rep.cancelled = True
                break
            if choice != "replace":
                rep.skipped.append(s)
                continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        safecopy.copy_tree_verified(s.path, dest, safety, f"{s.name}-replaced")
        if move:
            safety.keep(s.path, f"{s.name}-moved")
        rep.done.append(s)
    rep.safety = safety.dir
    safety.prune()
    return rep


def delete(saves, backup_dir) -> Report:
    rep = Report()
    safety = safecopy.Safety(backup_dir, SAFETY_NAME)
    for s in saves:
        safety.keep(s.path, f"{s.name}-deleted")
        rep.done.append(s)
    rep.safety = safety.dir
    safety.prune()
    return rep
