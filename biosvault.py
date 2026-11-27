"""
biosvault.py - the user's own BIOS files, kept in one place and synced between devices.

Layout (local copy and cloud copy are identical):

    <vault>/<System name>/<file>                      e.g. Sony - PlayStation 2/scph39001.bin
    <vault>/<System name>/<sub folder>/<file>         a folder set a core needs as-is,
                                                      e.g. Sega - Dreamcast/dc/dc_boot.bin
    <vault>/Unidentified/<file>                       nothing could place it (never installed)

The folder a file sits in IS its system, so there is no index file to merge between
devices; fingerprints are recomputed locally (and cached by size + time).  The cloud
copy lives at the top level of the cloud drive: "<remote>:Emulator BIOS Vault"
(for EmuDeck Cloud, inside the account's own folder).  Syncing uses the same rules
as save syncing: newest file wins, nothing is ever deleted.

No GUI.  No network except the user's own rclone remote.
"""

import io
import json
import re
import os
import shutil
import zipfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from dataclasses import dataclass

import biosdb
import cloudsync as cs
import gamebios

APP_DIR = Path.home() / ".emu_save_manager"
DEFAULT_VAULT = APP_DIR / "bios_vault"
CACHE_FILE = APP_DIR / "bios_vault_cache.json"
CLOUD_FOLDER = "Emulator BIOS Vault"
CACHE_TAG = f"r{biosdb.RULES_VERSION}-{biosdb.SOURCE_VERSION}-g1-l1"     # g: gamebios rules
MAX_FILE = 64 * 1024 * 1024            # no BIOS is this big; keeps disc images etc. out
SKIP_NAMES = {"desktop.ini", "thumbs.db", ".ds_store", "bios list.csv", "readme.txt"}
SKIP_EXTS = (".txt", ".md", ".csv", ".lnk", ".url", ".exe", ".dll", ".7z", ".rar")


class VaultEntry:
    __slots__ = ("path", "rel", "system", "ident", "_sub")

    def __init__(self, path: Path, rel: str, system: str, ident: biosdb.Identity, sub: str = None):
        self.path, self.rel, self.system, self.ident, self._sub = path, rel, system, ident, sub

    @property
    def sub(self) -> str:
        """Path below the system folder ('scph1001.bin' or 'dc/dc_boot.bin')."""
        if self._sub is not None:
            return self._sub
        return self.rel.split("/", 1)[1] if "/" in self.rel else self.rel


# A vault can also be laid out like RetroArch's flat system folder, or have a folder grouping the
# system folders ("Sorted by system/Sony - PlayStation 2/..."). Both are understood.
FLAT_FOLDERS = {"system", "bios", "retroarch system", "retroarch"}
FLAT_SET = "RetroArch system folder"        # unknown files in subfolders of a flat folder (dc/, Machines/)


def _layout(rel: str, known) -> tuple:
    """(kind, system folder or '', path below it) for a file in the vault.
    kind: 'system' (<System>/...), 'flat' (system/...: the system comes from the contents),
    or None (a loose file at the top: not in a system)."""
    parts = rel.split("/")
    if len(parts) < 2:
        return None, "", rel
    top = parts[0]
    if top.lower() in FLAT_FOLDERS and top not in known:
        return "flat", "", "/".join(parts[1:])
    if len(parts) >= 3 and top not in known and " - " not in top:
        return "system", parts[1], "/".join(parts[2:])          # a folder grouping system folders
    return "system", top, "/".join(parts[1:])


def vault_dir(cfg: dict) -> Path:
    return Path(cfg.get("bios_vault", {}).get("folder") or DEFAULT_VAULT)


def cloud_folder(cfg: dict) -> str:
    """The vault's folder on the cloud drive: "Emulator BIOS Vault" unless set (e.g. "Emudeck/BIOS",
    when the vault on the PC is that folder in the OneDrive folder, so every device uses the one copy)."""
    f = str(cfg.get("bios_vault", {}).get("cloud_folder") or "").strip().strip("/\\")
    return f.replace("\\", "/") or CLOUD_FOLDER


def cloud_folder_for(vault: Path) -> str:
    """If the vault is inside a OneDrive / Dropbox / Google Drive folder on this machine, the same
    folder's path on the cloud drive ("E:/OneDrive/Emudeck/BIOS" -> "Emudeck/BIOS"); else ''."""
    parts = [x for x in re.split(r"[\\/]+", str(vault)) if x]
    for i, part in enumerate(parts[:-1]):
        low = part.lower()
        if low == "onedrive" or low.startswith("onedrive - ") or low in ("dropbox", "google drive", "my drive"):
            return "/".join(parts[i + 1:])
    return ""


def remote_vault_path(remote: str, base: str, folder: str = CLOUD_FOLDER) -> str:
    """Top level of the cloud drive (or the folder set); for EmuDeck Cloud the account's own folder."""
    folder = folder or CLOUD_FOLDER
    if cs.is_emudeck_cloud(remote):
        b = base.strip("/")
        prefix = b[:-len("Emudeck/saves")].strip("/") if b.lower().endswith("emudeck/saves") else b
        return f"{remote}:{prefix}/{folder}" if prefix else f"{remote}:{folder}"
    return f"{remote}:{folder}"


# ---------------------------------------------------------------------------
# Reading the vault
# ---------------------------------------------------------------------------
def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict):
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass


def scan(vault: Path) -> List[VaultEntry]:
    """Every file in the vault with its identity (cached by size and modification time)."""
    cache, fresh, out = _load_cache(), {}, []
    known = _systems()
    if not vault.is_dir():
        return out
    for root, dirs, files in os.walk(vault):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "_pcsx2_tmp")))
        for f in sorted(files, key=str.lower):
            p = Path(root) / f
            rel = p.relative_to(vault).as_posix()
            kind, system, sub = _layout(rel, known)
            if kind is None or f.lower() in SKIP_NAMES:
                continue                       # loose files at the top are not in a system
            try:
                st = p.stat()
            except OSError:
                continue
            key = f"{CACHE_TAG}|{rel}|{st.st_size}|{int(st.st_mtime)}"
            c = cache.get(key)
            if c:
                ident = biosdb.Identity(**c)
            else:
                try:
                    data = p.read_bytes()
                except OSError:
                    continue
                ident = identify(data, f, system)
                c = {k: getattr(ident, k) for k in ("system", "label", "status", "md5", "size",
                                                      "names", "region")}
                c["names"] = list(c["names"])
                ident = biosdb.Identity(**dict(c, names=tuple(c["names"])))
            fresh[key] = dict(c, names=list(c["names"]))
            if kind == "flat":                 # the system comes from what the file is
                system = ident.system if ident.status != "unknown" else (FLAT_SET if "/" in sub else biosdb.UNKNOWN)
            if ident.status in ("unknown", "unverified") and "/" in sub and not ident.names:
                # a member of a folder set an emulator core needs as-is (dc/, Machines/ ...)
                ident = biosdb.Identity(ident.system, "kept with its folder set",
                                        "in-set", ident.md5, ident.size, ident.names, ident.region)
            out.append(VaultEntry(p, rel, system, ident, sub))
    _save_cache(fresh)
    return out


def identify(data: bytes, name: str, hint_system: str = "") -> biosdb.Identity:
    """libretro's list first; then the sets MAME wants beside the games (Game.com); then a zip
    that holds BIOS files ('packed': Organise unpacks it)."""
    ident = biosdb.identify(data, name, hint_system)
    if ident.status == "unknown":
        g = gamebios.identify(name, data)
        if g:
            return g
        if name.lower().endswith(".zip") and data[:4] == b"PK\x03\x04":
            inside = packed_contents(data)
            if inside:
                labels = sorted({i.label for _n, _r, _d, i in inside})
                more = f" and {len(labels) - 3} more" if len(labels) > 3 else ""
                return biosdb.Identity(inside[0][3].system,
                                       f"A zip holding {len(inside)} BIOS file(s) ({', '.join(labels[:3])}{more}): "
                                       f"Organise unpacks them", "packed", ident.md5, ident.size)
    return ident


def packed_contents(data: bytes):
    """[(name, path inside, bytes, identity)] of the BIOS files a zip holds (zips inside it too)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            members = _zip_members(z)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError):
        return []
    out = []
    for n, r, d in members:
        i = biosdb.identify(d, n)
        if i.status in ("verified", "identified"):
            out.append((n, r, d, i))
    return out


def scan_any(folder):
    """Identify every file under any folder (read-only): [(system, status, label, path)]."""
    out = []
    for name, rel, data in _iter_sources([folder]):
        i = identify(data, name)
        out.append((i.system, i.status, i.label, rel))
    return sorted(out, key=lambda r: (r[0] == biosdb.UNKNOWN, r[0], r[3].lower()))


def summary(entries: List[VaultEntry]) -> Dict[str, Dict[str, int]]:
    s: Dict[str, Dict[str, int]] = {}
    for e in entries:
        d = s.setdefault(e.system, {})
        d[e.ident.status] = d.get(e.ident.status, 0) + 1
    return s


# ---------------------------------------------------------------------------
# Adding files
# ---------------------------------------------------------------------------
def _iter_sources(paths):
    """(display name, relative path inside a picked folder or '', bytes) for files, folders
    and zip archives (zips are read in memory, never extracted to disk)."""
    for p in map(Path, paths):
        if p.is_dir():
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in files:
                    fp = Path(root) / f
                    yield from _iter_file(fp, fp.relative_to(p).as_posix())
        elif p.is_file():
            yield from _iter_file(p, p.name)


def _is_set(rel: str) -> bool:
    """<System folder>/<sub folder>/.../<file>: a folder set kept exactly as it is.
    A system folder is named like the vault's ('Sega - Dreamcast', 'blueMSX - MSX ...')."""
    parts = rel.split("/")
    if any(x.lower().endswith(".zip") for x in parts[:-1]):
        return False                            # a file from a zip inside a zip: placed on its own
    return len(parts) >= 3 and (" - " in parts[0] or parts[0] in _systems())


def _systems():
    return {v[0] for v in biosdb.DB.values()} | {biosdb.PS2, biosdb.XBOX, biosdb.GC} | set(gamebios.BY_SYSTEM)


def _iter_file(p: Path, rel: str):
    low = p.name.lower()
    in_set = _is_set(rel)                   # inside a folder set: keep everything it has
    if low in SKIP_NAMES or (low.endswith(SKIP_EXTS) and not in_set):
        return
    try:
        size = p.stat().st_size
    except OSError:
        return
    if low.endswith(".zip") and size < 512 * 1024 * 1024:
        try:
            with zipfile.ZipFile(p) as z:
                inner = _zip_members(z)
                ids = [biosdb.identify(d, n) for n, _r, d in inner[:400]]
                # a zip that is itself a BIOS set (Dreamcast arcade boards, Neo Geo, Game.com) is
                # kept whole
                if not any(x.status in ("verified", "identified") for x in ids):
                    if size <= MAX_FILE:
                        yield p.name, rel, p.read_bytes()
                    return
                for n, inner_rel, data in inner:
                    if n.lower() in SKIP_NAMES or n.lower().endswith(SKIP_EXTS):
                        continue
                    yield n, inner_rel, data
        except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError):
            if size <= MAX_FILE:               # not readable as a zip: keep it as a plain file
                try:
                    yield p.name, rel, p.read_bytes()
                except OSError:
                    pass
        return
    if size <= MAX_FILE:
        try:
            yield p.name, rel, p.read_bytes()
        except OSError:
            return


def _zip_members(z: zipfile.ZipFile, depth: int = 0):
    """[(name, path inside, bytes)]; a zip inside the zip is opened too (TOSEC and No-Intro packs
    ship BIOS files as zips inside a zip), unless it's a set kept whole (nothing in it known)."""
    out = []
    for i in z.infolist():
        if i.is_dir() or i.file_size > MAX_FILE:
            continue
        data = z.read(i)
        n = i.filename.rsplit("/", 1)[-1]
        if depth == 0 and n.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z2:
                    sub = _zip_members(z2, 1)
                if any(biosdb.identify(d, sn).status in ("verified", "identified") for sn, _r, d in sub[:400]):
                    out += [(sn, f"{i.filename}/{sr}", d) for sn, sr, d in sub]
                    continue
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError):
                pass
        out.append((n, i.filename, data))
    return out


def _target_rel(name: str, rel: str, ident: biosdb.Identity, known_systems) -> str:
    """Where a file goes in the vault: <system>/<name>, keeping a sub folder when the
    source already used the vault layout (e.g. 'Sega - Dreamcast/dc/dc_boot.bin')."""
    parts = rel.split("/")
    if _is_set(rel):
        return rel                                     # a folder set (blueMSX's Machines/..., dc/...)
    for i, part in enumerate(parts[:-1]):              # source laid out by system already
        if part in known_systems or part == ident.system:
            sys_name = ident.system if ident.system != biosdb.UNKNOWN else part
            return "/".join([sys_name] + parts[i + 1:])
    if ident.names and "/" in ident.names[0] and ident.system != biosdb.PS2:
        return f"{ident.system}/{ident.names[0]}"    # e.g. dc/dc_boot.bin
    return f"{ident.system}/{name}"


def add(vault: Path, paths, log=print) -> dict:
    """Copy files into the vault.  Identical files are stored once; a different file with
    a taken name gets ' (2)' etc.  Returns counts."""
    vault.mkdir(parents=True, exist_ok=True)
    have = {e.ident.md5: e.rel for e in scan(vault)}
    known_systems = _systems()
    counts = {"added": 0, "duplicate": 0, "skipped": 0}
    for name, rel, data in _iter_sources(paths):
        ident = identify(data, name)
        in_set = _is_set(rel)
        if ident.status == "unknown" and not in_set and not any(p in known_systems for p in rel.split("/")[:-1]):
            if not name.lower().endswith((".bin", ".rom", ".img", ".bios", ".sms", ".gg", ".pce",
                                          ".min", ".dat", ".sfc", ".tgc", ".zip", ".nvm", ".mec",
                                          ".erom", ".rom1", ".rom2", ".pgf", ".ttf", ".zim")):
                counts["skipped"] += 1
                continue
        target_rel = _target_rel(name, rel, ident, known_systems)
        if target_rel.count("/") >= 2 or ident.status == "side-file":
            # folder-set members and PCSX2 side files: the name/path matters, not just the bytes
            if (vault / target_rel).exists() and biosdb.md5_of((vault / target_rel).read_bytes()) == ident.md5:
                counts["duplicate"] += 1
                continue
        elif ident.md5 in have:
            counts["duplicate"] += 1
            continue
        target = vault / target_rel
        if target.exists():
            stem, ext = os.path.splitext(target.name)
            n = 2
            while target.exists():
                target = target.with_name(f"{stem} ({n}){ext}")
                n += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        have[ident.md5] = target.relative_to(vault).as_posix()
        counts["added"] += 1
        log(f"Added  {have[ident.md5]}  -  {ident.label} [{ident.status}]")
    log(f"{counts['added']} added, {counts['duplicate']} already in the vault, "
        f"{counts['skipped']} not BIOS files.")
    return counts


# ---------------------------------------------------------------------------
# Organise: one tidy <System>/<name> layout
# ---------------------------------------------------------------------------
MOVED_FILE = ".vault_moved.json"       # {old path in the vault: md5}: applied on every device


@dataclass
class Step:
    kind: str           # "copy" (to its tidy place), "unpack" (from a zip), "fix" (clean copy), "dup", "keep"
    src: "VaultEntry"
    dest: str = ""      # tidy path in the vault ("" for dup/keep)
    note: str = ""
    data: bytes = None  # for unpack / fix


def _tidy_name(e) -> str:
    """Where a recognised file belongs: <system>/<the name its emulator looks for>."""
    i = e.ident
    if i.status == "side-file" or e.system in (biosdb.PS2, biosdb.XBOX) or not i.names:
        return f"{i.system}/{e.path.name}"            # PS2 / Xbox: names chosen by the user matter
    n = i.names[0]
    if i.system == biosdb.PS1:
        n = os.path.basename(n)
    return f"{i.system}/{n}"


def organise_plan(vault: Path, entries=None) -> List[Step]:
    """What Organise would do. Recognised files get one place each, <System>/<name>; zips holding
    BIOS files are unpacked; copier headers are removed in a clean copy. Folder sets keep their
    folders; unknown and damaged files stay where they are."""
    entries = scan(vault) if entries is None else entries
    known = _systems()
    at = {e.rel: e for e in entries}
    want: Dict[str, str] = {}                        # tidy path -> md5 already claimed
    for e in entries:
        want.setdefault(e.rel, e.ident.md5)
    steps: List[Step] = []

    def claim(dest, md5) -> str:
        """The tidy path for md5: dest, or dest (2)... if another file has that name."""
        stem, ext = os.path.splitext(dest)
        n = 2
        while dest in want and want[dest] != md5:
            dest = f"{stem} ({n}){ext}"
            n += 1
        new = dest not in want
        want[dest] = md5
        return dest if new else ""

    for e in sorted(entries, key=lambda x: (x.ident.status != "verified", x.rel)):
        st, kind = e.ident.status, _layout(e.rel, known)[0]
        if st in ("unknown", "damaged"):
            steps.append(Step("keep", e, note=e.ident.label))
            continue
        if st == "in-set" or (st == "unverified" and "/" in e.sub and not e.rel.startswith(e.ident.system + "/")):
            if kind == "system" and e.rel.split("/")[0] not in known and " - " not in e.rel.split("/")[0]:
                dest = f"{e.system}/{e.sub}"                # out of a grouping folder
                d = claim(dest, e.ident.md5)
                steps.append(Step("copy", e, d, "folder set") if d else Step("dup", e, note=f"also at {dest}"))
            elif kind == "flat" and any(x is not e and x.sub == e.sub and x.ident.md5 == e.ident.md5
                                        for x in entries):
                steps.append(Step("dup", e, note="the same folder set is elsewhere in the vault"))
            else:
                steps.append(Step("keep", e, note="folder set"))
            continue
        if st == "packed":
            inside = packed_contents(e.path.read_bytes())
            for n, r, d, i in inside:
                tmp = VaultEntry(e.path, f"{i.system}/{n}", i.system, i)
                dest = claim(_tidy_name(tmp), i.md5)
                if dest:
                    steps.append(Step("unpack", e, dest, f"{r} from the zip", d))
            steps.append(Step("dup", e, note=f"unpacked ({len(inside)} file(s))"))
            continue
        if st == "fixable":
            data = e.path.read_bytes()[biosdb.COPIER_HEADER:]
            fixed = biosdb.identify(data, os.path.basename(e.ident.extra.get("fixed_names", [e.path.name])[0]))
            dest = claim(_tidy_name(VaultEntry(e.path, "", fixed.system, fixed)), fixed.md5)
            if dest:
                steps.append(Step("fix", e, dest, "the 512-byte copier header removed", data))
            steps.append(Step("dup", e, note="a clean copy replaces it"))
            continue
        dest = _tidy_name(e)
        if dest == e.rel:
            steps.append(Step("keep", e, note="already tidy"))
            continue
        d = claim(dest, e.ident.md5)
        if d:
            steps.append(Step("copy", e, d))
        else:
            steps.append(Step("dup", e, note=f"already at {dest}" if want.get(dest) == e.ident.md5 else ""))
    return steps


def organise(vault: Path, steps: List[Step], move_originals_to: Path = None, log=print) -> dict:
    """Carry out a plan. Nothing is deleted: copies are added, and the files they came from stay,
    unless move_originals_to is given, in which case those files (and duplicates) are moved there
    and listed in .vault_moved.json, so other devices move their copies away too when they sync."""
    counts = {"copied": 0, "unpacked": 0, "fixed": 0, "moved": 0, "failed": 0}
    done_src = set()
    for st in steps:
        if st.kind not in ("copy", "unpack", "fix"):
            continue
        dest = vault / st.dest
        try:
            data = st.data if st.data is not None else st.src.path.read_bytes()
            if dest.exists():
                if biosdb.md5_of(dest.read_bytes()) != biosdb.md5_of(data):
                    log(f"  {st.dest} already holds a different file - left alone")
                    counts["failed"] += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".part")
            tmp.write_bytes(data)
            if biosdb.md5_of(tmp.read_bytes()) != biosdb.md5_of(data):
                raise OSError("the copy didn't read back the same")
            os.replace(tmp, dest)
            counts[{"copy": "copied", "unpack": "unpacked", "fix": "fixed"}[st.kind]] += 1
            done_src.add(st.src.rel)
            log(f"  {st.src.rel}  ->  {st.dest}" + (f"  ({st.note})" if st.note else ""))
        except OSError as ex:
            counts["failed"] += 1
            log(f"  {st.src.rel}: couldn't copy ({ex})")
    if move_originals_to:
        failed_src = {st.src.rel for st in steps if st.kind in ("copy", "unpack", "fix")} - done_src
        movable = [st.src for st in steps if st.kind in ("copy", "unpack", "fix", "dup")
                   and st.src.rel not in failed_src]
        moved = {}
        for e in {x.rel: x for x in movable}.values():
            if not e.path.exists():
                continue
            dst = Path(move_originals_to) / e.rel
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(e.path), str(dst))
                moved[e.rel] = e.ident.md5
                counts["moved"] += 1
            except OSError as ex:
                log(f"  {e.rel}: couldn't move it ({ex})")
        if moved:
            _note_moved(vault, moved)
            _remove_empty_dirs(vault)
            log(f"  {len(moved)} original(s) moved to {move_originals_to}")
    log(f"Organise: {counts['copied']} copied, {counts['unpacked']} unpacked, {counts['fixed']} cleaned, "
        f"{counts['moved']} original(s) moved out, {counts['failed']} problem(s).")
    return counts


def _note_moved(vault: Path, moved: dict):
    path = vault / MOVED_FILE
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    old.update(moved)
    path.write_text(json.dumps(old, indent=1, sort_keys=True), encoding="utf-8")


def _remove_empty_dirs(vault: Path):
    """Folders Organise emptied (only empty ones: rmdir refuses anything else)."""
    for d in sorted((p for p in vault.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        try:
            d.rmdir()
        except OSError:
            pass


def apply_moved(vault: Path, backup: Path, log=print) -> list:
    """Move away this device's copies of files Organise moved out on another device (only if they
    are still the same file), so a sync doesn't bring them back. Returns the paths moved."""
    try:
        moved = json.loads((vault / MOVED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for rel, md5 in moved.items():
        p = vault / rel
        if p.is_file() and biosdb.md5_of(p.read_bytes()) == md5:
            dst = Path(backup) / rel
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(dst))
                out.append(rel)
            except OSError:
                pass
    if out:
        _remove_empty_dirs(vault)
        log(f"Moved {len(out)} file(s) that were tidied away on another device to {backup}")
    return out


def move_to_system(vault: Path, rel: str, system: str) -> str:
    """Assign a file to a system by moving it into that system's folder."""
    src = vault / rel
    dst = vault / system / src.name
    n = 2
    while dst.exists():
        dst = dst.with_name(f"{src.stem} ({n}){src.suffix}")
        n += 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return dst.relative_to(vault).as_posix()


# ---------------------------------------------------------------------------
# Cloud
# ---------------------------------------------------------------------------
def sync_cloud(cfg: dict, log=print, dry=False, runner=None) -> bool:
    """Two-way copy of the vault with '<remote>:Emulator BIOS Vault' (or the folder set).  Newest wins,
    nothing deleted; files a download replaces go to <backup>/bios_vault_overwritten/."""
    c = cfg.get("cloud", {})
    exe = (runner.exe if runner else cs.usable_rclone(c.get("rclone", ""), log, conf=c.get("config", ""),
                                                           remote=c.get("remote", ""))) if c.get("remote") else ""
    remote = c.get("remote", "")
    if not exe or not Path(exe).is_file() or not remote:
        log("Cloud Sync is not set up, so the vault stays on this device only.")
        return False
    vault = vault_dir(cfg)
    vault.mkdir(parents=True, exist_ok=True)
    runner = runner or cs.RcloneRunner(exe, c.get("config", ""), log)
    rpath = remote_vault_path(remote, c.get("base") or cs.DEFAULT_BASE, cloud_folder(cfg))
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup = Path(cfg.get("backup_dir") or APP_DIR / "backups") / "bios_vault_overwritten" / stamp
    log(f"BIOS Vault <-> {rpath}")
    down = cs.sync_pair(runner, "download", vault, rpath, dry, backup)
    if down == "failed":
        return False
    if not dry:
        gone = apply_moved(vault, Path(cfg.get("backup_dir") or APP_DIR / "backups") / "bios_vault_tidied" / stamp, log)
        if gone:                     # the cloud's copies go to a backup folder there (moved, not deleted)
            fd, lst = tempfile.mkstemp(prefix="esm_moved_", suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(gone) + "\n")
            try:
                runner.run(["move", rpath, f"{rpath.rstrip('/')}-tidied/{stamp}", "--files-from", lst])
            finally:
                os.unlink(lst)
    up = cs.sync_pair(runner, "upload", vault, rpath, dry, None)
    return up != "failed"
