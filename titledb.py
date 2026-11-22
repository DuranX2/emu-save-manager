"""
titledb.py - game names for game IDs, from things already on this machine (no downloads).

Sources, best first:
  1. Lists the emulators ship with:
       Dolphin / PrimeHack  Sys/wiitdb-en.txt   GameCube + Wii IDs (GameTDB's list)
       PCSX2                resources/GameIndex.yaml   PS2 serials
       DuckStation          resources/gamedb.yaml      PS1 serials
  2. Cemu's title_list_cache.xml (Wii U title IDs of every game Cemu has seen).
  3. Your ROM folders (EmuDeck's Emulation/roms/<system>): GameCube/Wii disc headers
     (ISO, GCM, WBFS, CISO, RVZ/WIA), 3DS program IDs (.3ds/.cci/.cxi/.cia, plus the CIA's
     own title), Switch title IDs in file names or inside NSPs, Wii U meta/meta.xml and
     file names, PS3 PARAM.SFO.
  4. Names seen in saves themselves (GameCube comments, Wii banners, PARAM.SFO), remembered
     so that save states named only by ID get a name too.
The ROM scan reads a few hundred bytes per file and is cached by path, size and date in
~/.emu_save_manager/titles.json.
"""
from __future__ import annotations

import json
import os
import re
import struct
import threading
import time
from pathlib import Path

import saveinfo

CACHE_FILE = Path.home() / ".emu_save_manager" / "titles.json"
SCAN_DEPTH = 4
MAX_ROM_FILES = 20000

GCWII_EXT = {".iso", ".gcm", ".wbfs", ".ciso", ".rvz", ".wia", ".nkit", ".tgc"}
N3DS_EXT = {".3ds", ".cci", ".cxi", ".cia", ".app"}
SWITCH_EXT = {".nsp", ".xci", ".nsz", ".xcz"}
ROM_DIRS = {                          # EmuDeck folder names -> what to look for
    "gc": "gcwii", "gamecube": "gcwii", "ngc": "gcwii", "wii": "gcwii", "primehack": "gcwii",
    "3ds": "3ds", "n3ds": "3ds", "switch": "switch", "wiiu": "wiiu", "ps3": "ps3",
}

_RE_SW_ID = re.compile(r"\[(01[0-9A-Fa-f]{14})\]")
_RE_WIIU_ID = re.compile(r"\[?(0005000[0-9A-Fa-f]{9})\]?")
_RE_TAGS = re.compile(r"\s*\[[^\]]*\]")


def clean_name(stem: str) -> str:
    s = _RE_TAGS.sub("", stem)
    s = re.sub(r"\.(nkit|dec)$", "", s, flags=re.I)
    return " ".join(s.split()) or stem


def switch_base(tid: str) -> str:
    """Updates (…800) and the base game share a save folder: use the base title ID."""
    tid = tid.upper()
    return tid[:-3] + "000" if tid.endswith("800") else tid


def unique_id(tid16: str) -> str:
    """3DS title ID -> the 'unique ID' that extdata folders are named after."""
    try:
        return f"{(int(tid16, 16) >> 8) & 0xFFFFF:08X}"
    except ValueError:
        return ""


# ---------------------------------------------------------------------------
# Emulator lists
# ---------------------------------------------------------------------------
def parse_wiitdb(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        k, sep, v = line.partition(" = ")
        k = k.strip()
        if sep and 4 <= len(k) <= 6 and k != "TITLES":
            out[k.upper()] = v.strip()
    return out


_RE_YAML_GAME = re.compile(r'^([A-Z]{4}-\d{5}):\s*\n\s+name:\s*"((?:[^"\\]|\\.)*)"', re.M)


def parse_serial_yaml(text: str) -> dict:
    """PCSX2's GameIndex.yaml / DuckStation's gamedb.yaml: 'SLUS-00067:' then 'name: "…"'."""
    return {m.group(1): m.group(2).replace('\\"', '"') for m in _RE_YAML_GAME.finditer(text)}


def parse_cemu_cache(text: str) -> dict:
    out = {}
    for m in re.finditer(r'<title\s+titleId="([0-9a-fA-F]{16})"[^>]*>(.*?)</title>', text, re.S):
        n = re.search(r"<name>(.*?)</name>", m.group(2), re.S)
        if n and n.group(1).strip():
            name = n.group(1).strip()
            for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'")):
                name = name.replace(a, b)
            out[m.group(1).upper()] = " ".join(name.split())
    return out


# ---------------------------------------------------------------------------
# ROM headers
# ---------------------------------------------------------------------------
def _read_at(f, off, n):
    f.seek(off)
    return f.read(n)


def gcwii_header(path: Path):
    """(ID6, internal title) from a GameCube/Wii disc image, or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(0x8080)
    except OSError:
        return None
    for off in (0, 0x58, 0x200, 0x8000):             # ISO/GCM, RVZ/WIA, WBFS, CISO
        h = head[off:off + 0x60]
        if len(h) < 0x60:
            continue
        wii = h[0x18:0x1C] == b"\x5D\x1C\x9E\xA3"
        gc = h[0x1C:0x20] == b"\xC2\x33\x9F\x3D"
        if (wii or gc) and all(0x20 < c < 0x7F for c in h[:6]):
            return h[:6].decode("ascii").upper(), saveinfo._cstr(h[0x20:0x60], "cp1252")
    return None


def _smdh_title(b: bytes) -> str:
    if b[:4] != b"SMDH":
        return ""
    for lang in (1, 0):                                # English, then Japanese
        e = b[8 + 0x200 * lang:8 + 0x200 * (lang + 1)]
        for part in (e[0x80:0x180], e[:0x80]):         # long title, short title
            try:
                s = part.decode("utf-16-le").split("\0", 1)[0]
            except UnicodeDecodeError:
                continue
            s = " ".join(s.split())
            if s:
                return s
    return ""


def n3ds_header(path: Path):
    """(16-hex program ID, title or '') from a 3DS ROM, or None."""
    try:
        with open(path, "rb") as f:
            h = f.read(0x400)
            ext = path.suffix.lower()
            if h[0x100:0x104] == b"NCSD":
                off = struct.unpack("<I", h[0x120:0x124])[0] * 0x200
                n = _read_at(f, off, 0x200)
            elif h[0x100:0x104] == b"NCCH":
                n = h
            elif ext == ".cia" and len(h) >= 0x20:
                hdr, _t, _v, cert, tik, tmd, meta = struct.unpack("<IHHIIII", h[:0x18])
                csize, = struct.unpack("<Q", h[0x18:0x20])
                al = lambda x: (x + 63) & ~63
                coff = al(al(al(al(hdr) + cert) + tik) + tmd)
                n = _read_at(f, coff, 0x200)
                title = ""
                if meta >= 0x400 + 0x36C0:
                    title = _smdh_title(_read_at(f, al(coff + csize) + 0x400, 0x36C0))
                if n[0x100:0x104] == b"NCCH":
                    return f"{struct.unpack('<Q', n[0x118:0x120])[0]:016X}", title
                return None
            else:
                return None
    except (OSError, struct.error):
        return None
    if n[0x100:0x104] != b"NCCH":
        return None
    return f"{struct.unpack('<Q', n[0x118:0x120])[0]:016X}", ""


def nsp_title_ids(path: Path) -> list:
    """Title IDs from the file names inside an NSP (tickets are named <title id><key gen>.tik)."""
    try:
        with open(path, "rb") as f:
            h = f.read(16)
            if h[:4] != b"PFS0":
                return []
            n, strsize = struct.unpack("<II", h[4:12])
            if n > 4096 or strsize > 1 << 20:
                return []
            names = _read_at(f, 16 + 24 * n, strsize)
    except (OSError, struct.error):
        return []
    out = []
    for nm in names.split(b"\0"):
        m = re.match(rb"^(01[0-9a-fA-F]{14})[0-9a-fA-F]{16}\.(tik|cert)$", nm)
        if m:
            out.append(m.group(1).decode().upper())
    return out


# ---------------------------------------------------------------------------
# Where things are
# ---------------------------------------------------------------------------
def _env_path(var):
    v = os.environ.get(var)
    return Path(v) if v else None


def install_roots(extra=()) -> list:
    """Folders that may hold an emulator's program files."""
    home = Path.home()
    roots = [Path(p) for p in extra if p]
    appdata, local = _env_path("APPDATA"), _env_path("LOCALAPPDATA")
    if appdata:
        roots.append(appdata / "EmuDeck" / "Emulators")
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        p = _env_path(var)
        if p:
            roots += [p / n for n in ("Dolphin", "Dolphin-x64", "PrimeHack", "DuckStation", "PCSX2")]
    if local:
        roots.append(local / "Programs")
    for fp in (Path("/var/lib/flatpak/app"), home / ".local/share/flatpak/app"):
        for app, share in (("org.DolphinEmu.dolphin-emu", "dolphin-emu"),
                           ("io.github.shiiion.primehack", "dolphin-emu"),
                           ("net.pcsx2.PCSX2", "PCSX2"), ("org.duckstation.DuckStation", "duckstation")):
            roots.append(fp / app / "current" / "active" / "files" / "share" / share)
    roots += [Path("/usr/share/dolphin-emu"), Path("/usr/share/PCSX2"), Path("/usr/share/duckstation")]
    return roots


_BUNDLED = {
    "wiitdb": ("wiitdb-en.txt", ("Sys", "sys", "*/Sys", "*/sys", "")),
    "pcsx2": ("GameIndex.yaml", ("resources", "*/resources", "")),
    "duckstation": ("gamedb.yaml", ("resources", "*/resources", "")),
}


def find_bundled(kind: str, roots) -> list:
    name, subs = _BUNDLED[kind]
    found, seen = [], set()
    for r in roots:
        for sub in subs:
            try:
                cands = list(r.glob(f"{sub}/{name}" if sub else name)) if r.is_dir() else []
            except (OSError, ValueError):
                continue
            for c in cands:
                k = str(c.resolve()) if c.exists() else str(c)
                if k not in seen and c.is_file():
                    seen.add(k)
                    found.append(c)
    return found


def cemu_caches(extra=()) -> list:
    home = Path.home()
    cands = [Path(p) / "title_list_cache.xml" for p in extra if p]
    appdata = _env_path("APPDATA")
    if appdata:
        cands += [appdata / "Cemu" / "title_list_cache.xml",
                  appdata / "EmuDeck" / "Emulators" / "cemu" / "title_list_cache.xml"]
    cands += [home / ".local/share/Cemu/title_list_cache.xml", home / ".config/Cemu/title_list_cache.xml",
              home / ".var/app/info.cemu.Cemu/data/Cemu/title_list_cache.xml",
              home / ".var/app/info.cemu.Cemu/config/Cemu/title_list_cache.xml"]
    return [c for c in dict.fromkeys(cands) if c.is_file()]


def rom_roots() -> list:
    try:
        import cloudsync
        return [r / "roms" for r in cloudsync.emudeck_roots() if (r / "roms").is_dir()]
    except Exception:  # noqa
        return []


# ---------------------------------------------------------------------------
# The database
# ---------------------------------------------------------------------------
class TitleDB:
    def __init__(self, cache_file: Path | None = None):
        self.cache_file = Path(cache_file or CACHE_FILE)
        self.tables = {}               # id kind -> {id: title}
        self.learned = {}              # id kind -> {id: title} from saves
        self.roms = {}                 # path -> [size, mtime, kind, id, title]
        self.sources = []              # human-readable list of what was read
        self.loaded = False
        self._lock = threading.Lock()
        self._read_cache()

    # ---- cache -------------------------------------------------------------
    def _read_cache(self):
        try:
            d = json.loads(self.cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.learned = {k: dict(v) for k, v in (d.get("learned") or {}).items() if isinstance(v, dict)}
        self.roms = {k: v for k, v in (d.get("roms") or {}).items() if isinstance(v, list) and len(v) == 5}
        self._index_roms()

    def save(self):
        with self._lock:
            d = {"version": 1, "learned": self.learned, "roms": self.roms}
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.cache_file)
        except OSError:
            pass

    # ---- lookups -----------------------------------------------------------
    def lookup(self, kind: str, gid: str) -> str:
        if not gid:
            return ""
        gid = gid.upper()
        t = self.tables
        if kind in ("gc", "wii"):
            for k in (gid, gid[:4]):
                for table in (t.get("gcwii", {}), t.get("rom-gcwii", {}), self.learned.get("gc", {}),
                              self.learned.get("wii", {})):
                    if k in table:
                        return table[k]
            return ""
        if kind in ("ps1", "ps2"):
            g = gid if "-" in gid else (gid[:4] + "-" + gid[4:] if len(gid) == 9 else gid)
            return t.get(kind, {}).get(g, "") or self.learned.get(kind, {}).get(g, "")
        if kind in ("psp", "ps3"):
            g = gid.replace("-", "")
            return (t.get("rom-ps3", {}).get(g, "") if kind == "ps3" else "") or \
                self.learned.get(kind, {}).get(g, "")
        if kind == "switch":
            return t.get("rom-switch", {}).get(switch_base(gid), "") or self.learned.get("switch", {}).get(gid, "")
        if kind == "3ds":
            return t.get("rom-3ds", {}).get(gid, "") or self.learned.get("3ds", {}).get(gid, "")
        if kind == "3ds-unique":
            return t.get("rom-3ds-unique", {}).get(gid, "")
        if kind == "wiiu":
            return (t.get("cemu", {}).get(gid, "") or t.get("rom-wiiu", {}).get(gid, "")
                    or self.learned.get("wiiu", {}).get(gid, ""))
        return self.learned.get(kind, {}).get(gid, "")

    def learn(self, kind: str, gid: str, title: str) -> bool:
        """Remember a name seen in a save. True if it was new."""
        if not (kind and gid and title):
            return False
        gid = gid.upper().replace("-", "") if kind in ("psp", "ps3") else gid.upper()
        with self._lock:
            table = self.learned.setdefault(kind, {})
            if table.get(gid) == title:
                return False
            table[gid] = title
            return True

    # ---- loading -----------------------------------------------------------
    def load(self, install_extra=(), cemu_extra=(), rom_dirs=None, ps3_game_dirs=(), progress=None):
        """Read every source. Safe to run in a thread; lookups keep working meanwhile."""
        tables, sources = {}, []
        roots = install_roots(install_extra)
        for kind, table in (("wiitdb", "gcwii"), ("pcsx2", "ps2"), ("duckstation", "ps1")):
            for f in find_bundled(kind, roots):
                try:
                    text = f.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                parsed = parse_wiitdb(text) if kind == "wiitdb" else parse_serial_yaml(text)
                if parsed:
                    dest = tables.setdefault(table, {})
                    for k, v in parsed.items():
                        dest.setdefault(k, v)
                        if table == "gcwii":
                            dest.setdefault(k[:4], v)
                    sources.append(f"{f} ({len(parsed)} names)")
                    break
        for f in cemu_caches(cemu_extra):
            try:
                parsed = parse_cemu_cache(f.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            tables.setdefault("cemu", {}).update(parsed)
            sources.append(f"{f} ({len(parsed)} names)")
        self._scan_roms(rom_roots() if rom_dirs is None else [Path(d) for d in rom_dirs],
                        [Path(d) for d in ps3_game_dirs], progress)
        rom_tables = self._rom_tables()
        with self._lock:
            self.tables = {**tables, **rom_tables}
            self.sources = sources + [f"ROM folders: {len(self.roms)} files"]
            self.loaded = True
        self.save()
        return self

    def _scan_roms(self, roots, ps3_game_dirs, progress=None):
        seen = set()
        count = 0
        jobs = []
        for root in roots:
            if not root.is_dir():
                continue
            try:
                subs = list(root.iterdir())
            except OSError:
                continue
            for sub in subs:
                what = ROM_DIRS.get(sub.name.lower())
                if what and sub.is_dir():
                    jobs.append((what, sub))
        jobs += [("ps3", d) for d in ps3_game_dirs if d.is_dir()]
        for what, folder in jobs:
            for p in _walk(folder, SCAN_DEPTH):
                if count >= MAX_ROM_FILES:
                    break
                count += 1
                key = str(p)
                seen.add(key)
                try:
                    st = p.stat()
                except OSError:
                    continue
                old = self.roms.get(key)
                if old and old[0] == st.st_size and abs(old[1] - st.st_mtime) < 1:
                    continue
                rec = _identify_rom(what, p)
                if rec is not None:
                    self.roms[key] = [st.st_size, st.st_mtime, rec[0], rec[1], rec[2]]
                if progress and count % 200 == 0:
                    progress(count)
        scanned_roots = [str(f) for _w, f in jobs]
        for key in list(self.roms):          # forget files that were in a scanned folder but are gone
            if key not in seen and any(key.startswith(r) for r in scanned_roots):
                del self.roms[key]

    def _rom_tables(self):
        out = {}
        for _size, _mt, kind, gid, title in self.roms.values():
            out.setdefault("rom-" + kind, {}).setdefault(gid, title)
            if kind == "gcwii":
                out["rom-gcwii"].setdefault(gid[:4], title)
            if kind == "3ds":
                out.setdefault("rom-3ds-unique", {}).setdefault(unique_id(gid), title)
        return out

    def _index_roms(self):
        self.tables.update(self._rom_tables())


def _walk(folder: Path, depth: int):
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return
    for e in entries:
        try:
            if e.is_file():
                yield Path(e.path)
            elif e.is_dir() and depth > 0 and not e.name.startswith("."):
                yield from _walk(Path(e.path), depth - 1)
        except OSError:
            continue


def _identify_rom(what: str, p: Path):
    """(kind, id, title) or None."""
    ext = p.suffix.lower()
    name = p.name
    if what == "gcwii" and ext in GCWII_EXT:
        h = gcwii_header(p)
        if h:
            return "gcwii", h[0], clean_name(p.stem) if not h[1] else h[1]
    elif what == "3ds" and ext in N3DS_EXT:
        h = n3ds_header(p)
        if h:
            return "3ds", h[0], h[1] or clean_name(p.stem)
    elif what == "switch" and ext in SWITCH_EXT:
        m = _RE_SW_ID.search(name)
        ids = [m.group(1).upper()] if m else (nsp_title_ids(p) if ext in (".nsp", ".nsz") else [])
        if ids:
            return "switch", switch_base(ids[0]), clean_name(p.stem)
    elif what == "wiiu":
        if name.lower() == "meta.xml" and p.parent.name.lower() == "meta":
            try:
                t = p.read_text(encoding="utf-8", errors="replace")[:65536]
            except OSError:
                return None
            tid = re.search(r"<title_id[^>]*>\s*([0-9A-Fa-f]{16})\s*<", t)
            nm = re.search(r"<longname_en[^>]*>(.*?)</longname_en>", t, re.S)
            if tid:
                title = " ".join(nm.group(1).split()) if nm else p.parent.parent.name
                return "wiiu", tid.group(1).upper(), title
        else:
            m = _RE_WIIU_ID.search(name)
            if m and ext in (".wua", ".wux", ".wud", ".rpx", ".iso"):
                return "wiiu", m.group(1).upper(), clean_name(p.stem)
    elif what == "ps3" and name.upper() == "PARAM.SFO":
        sfo = saveinfo.parse_sfo(p)
        tid, title = sfo.get("TITLE_ID", ""), sfo.get("TITLE", "")
        if tid and title and sfo.get("CATEGORY", "") not in ("SD", "GD"):
            return "ps3", tid.upper().replace("-", ""), title
    return None
