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
import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import biosdb
import cloudsync as cs

APP_DIR = Path.home() / ".emu_save_manager"
DEFAULT_VAULT = APP_DIR / "bios_vault"
CACHE_FILE = APP_DIR / "bios_vault_cache.json"
CLOUD_FOLDER = "Emulator BIOS Vault"
CACHE_TAG = f"r{biosdb.RULES_VERSION}-{biosdb.SOURCE_VERSION}"
MAX_FILE = 64 * 1024 * 1024            # no BIOS is this big; keeps disc images etc. out
SKIP_NAMES = {"desktop.ini", "thumbs.db", ".ds_store", "bios list.csv", "readme.txt"}
SKIP_EXTS = (".txt", ".md", ".csv", ".lnk", ".url", ".exe", ".dll", ".7z", ".rar")


class VaultEntry:
    __slots__ = ("path", "rel", "system", "ident")

    def __init__(self, path: Path, rel: str, system: str, ident: biosdb.Identity):
        self.path, self.rel, self.system, self.ident = path, rel, system, ident

    @property
    def sub(self) -> str:
        """Path below the system folder ('scph1001.bin' or 'dc/dc_boot.bin')."""
        return self.rel.split("/", 1)[1] if "/" in self.rel else self.rel


def vault_dir(cfg: dict) -> Path:
    return Path(cfg.get("bios_vault", {}).get("folder") or DEFAULT_VAULT)


def remote_vault_path(remote: str, base: str) -> str:
    """Top level of the cloud drive; for EmuDeck Cloud the account's own folder."""
    if cs.is_emudeck_cloud(remote):
        b = base.strip("/")
        prefix = b[:-len("Emudeck/saves")].strip("/") if b.lower().endswith("emudeck/saves") else b
        return f"{remote}:{prefix}/{CLOUD_FOLDER}" if prefix else f"{remote}:{CLOUD_FOLDER}"
    return f"{remote}:{CLOUD_FOLDER}"


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
    if not vault.is_dir():
        return out
    for root, dirs, files in os.walk(vault):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "_pcsx2_tmp")))
        for f in sorted(files, key=str.lower):
            p = Path(root) / f
            rel = p.relative_to(vault).as_posix()
            if "/" not in rel or f.lower() in SKIP_NAMES:
                continue                       # loose files at the top are not in a system
            system = rel.split("/", 1)[0]
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
                    ident = biosdb.identify(p.read_bytes(), f, system)
                except OSError:
                    continue
                c = {k: getattr(ident, k) for k in ("system", "label", "status", "md5", "size",
                                                      "names", "region")}
                c["names"] = list(c["names"])
                ident = biosdb.Identity(**dict(c, names=tuple(c["names"])))
            fresh[key] = dict(c, names=list(c["names"]))
            if ident.status == "unknown" and rel.count("/") >= 2:
                # a member of a folder set an emulator core needs as-is (dc/, Machines/ ...)
                ident = biosdb.Identity(ident.system, "kept with its folder set",
                                        "in-set", ident.md5, ident.size, ident.names, ident.region)
            out.append(VaultEntry(p, rel, system, ident))
    _save_cache(fresh)
    return out


def scan_any(folder):
    """Identify every file under any folder (read-only): [(system, status, label, path)]."""
    out = []
    for name, rel, data in _iter_sources([folder]):
        i = biosdb.identify(data, name)
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
    return len(parts) >= 3 and (" - " in parts[0] or parts[0] in _systems())


def _systems():
    return {v[0] for v in biosdb.DB.values()} | {biosdb.PS2, biosdb.XBOX, biosdb.GC}


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
                inner = [i for i in z.infolist() if not i.is_dir() and i.file_size <= MAX_FILE]
                ids = [biosdb.identify(z.read(i), i.filename) for i in inner[:400]]
                # a zip that is itself a BIOS set (Dreamcast arcade boards, Neo Geo) is kept whole
                if not any(x.status in ("verified", "identified") for x in ids):
                    if size <= MAX_FILE:
                        yield p.name, rel, p.read_bytes()
                    return
                for i in inner:
                    n = i.filename.rsplit("/", 1)[-1]
                    if n.lower() in SKIP_NAMES or n.lower().endswith(SKIP_EXTS):
                        continue
                    yield n, i.filename, z.read(i)
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
    known_systems = {v[0] for v in biosdb.DB.values()} | {biosdb.PS2, biosdb.XBOX, biosdb.GC}
    counts = {"added": 0, "duplicate": 0, "skipped": 0}
    for name, rel, data in _iter_sources(paths):
        ident = biosdb.identify(data, name)
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
    """Two-way copy of the vault with '<remote>:Emulator BIOS Vault'.  Newest wins,
    nothing deleted; files a download replaces go to <backup>/bios_vault_overwritten/."""
    c = cfg.get("cloud", {})
    exe = c.get("rclone") or cs.find_rclone()
    remote = c.get("remote", "")
    if not exe or not Path(exe).is_file() or not remote:
        log("Cloud Sync is not set up, so the vault stays on this device only.")
        return False
    vault = vault_dir(cfg)
    vault.mkdir(parents=True, exist_ok=True)
    runner = runner or cs.RcloneRunner(exe, c.get("config", ""), log)
    rpath = remote_vault_path(remote, c.get("base") or cs.DEFAULT_BASE)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup = Path(cfg.get("backup_dir") or APP_DIR / "backups") / "bios_vault_overwritten" / stamp
    log(f"BIOS Vault <-> {rpath}")
    down = cs.sync_pair(runner, "download", vault, rpath, dry, backup)
    if down == "failed":
        return False
    up = cs.sync_pair(runner, "upload", vault, rpath, dry, None)
    return up != "failed"
